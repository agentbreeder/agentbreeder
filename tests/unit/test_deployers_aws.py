"""Unit tests for the AWS ECS Fargate deployer.

All AWS API calls are mocked via unittest.mock.patch so no real AWS
credentials or infrastructure are required.
"""

from __future__ import annotations

import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config_parser import (
    AccessConfig,
    AgentConfig,
    CloudType,
    DeployConfig,
    FrameworkType,
    ModelConfig,
    ResourceConfig,
    ScalingConfig,
)
from engine.deployers.base import DeployResult


def _fast_health_clock():
    """Patch asyncio.sleep + engine.deployers._health.time.monotonic so the
    poll_until_ready loop in deployer health checks completes instantly while
    still observing the configured timeout deadline.

    Each mocked sleep advances a fake clock by the requested duration, so the
    helper's deadline-based loop terminates after the same number of iterations
    as the wall-clock implementation would.
    """
    from contextlib import ExitStack

    clock = {"t": 0.0}

    async def _fake_sleep(seconds: float) -> None:
        clock["t"] += float(seconds)

    def _fake_monotonic() -> float:
        return clock["t"]

    stack = ExitStack()
    stack.enter_context(patch("asyncio.sleep", side_effect=_fake_sleep))
    stack.enter_context(patch("engine.deployers._health.time.monotonic", _fake_monotonic))
    return stack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_config(
    *,
    name: str = "my-agent",
    version: str = "1.0.0",
    extra_env: dict[str, str] | None = None,
) -> AgentConfig:
    """Build a minimal AgentConfig wired for ECS Fargate."""
    env_vars: dict[str, str] = {
        "AWS_ACCOUNT_ID": "123456789012",
        "AWS_REGION": "us-east-1",
        "AWS_ECS_CLUSTER": "agentbreeder-cluster",
        "AWS_EXECUTION_ROLE_ARN": "arn:aws:iam::123456789012:role/ecsTaskExecutionRole",
        "AWS_VPC_SUBNETS": "subnet-aaa,subnet-bbb",
        "AWS_SECURITY_GROUPS": "sg-111",
        "LOG_LEVEL": "info",
    }
    if extra_env:
        env_vars.update(extra_env)

    return AgentConfig(
        name=name,
        version=version,
        description="Test agent",
        team="engineering",
        owner="alice@example.com",
        framework=FrameworkType.langgraph,
        model=ModelConfig(primary="claude-sonnet-4"),
        deploy=DeployConfig(
            cloud=CloudType.aws,
            runtime="ecs-fargate",
            region="us-east-1",
            scaling=ScalingConfig(min=1, max=3),
            resources=ResourceConfig(cpu="512", memory="1024"),
            env_vars=env_vars,
        ),
        access=AccessConfig(),
    )


def _make_deployer() -> AWSECSDeployer:  # noqa: F821
    from engine.deployers.aws_ecs import AWSECSDeployer

    return AWSECSDeployer()


def _mock_boto3_client(service_mocks: dict[str, MagicMock]) -> MagicMock:
    """Return a factory function that yields the correct mock per service name."""

    def factory(service: str, **kwargs: object) -> MagicMock:
        return service_mocks.get(service, MagicMock())

    return factory


# ---------------------------------------------------------------------------
# _extract_ecs_config
# ---------------------------------------------------------------------------


class TestExtractECSConfig:
    def test_extracts_required_fields(self) -> None:
        from engine.deployers.aws_ecs import _extract_ecs_config

        config = _make_agent_config()
        aws = _extract_ecs_config(config)

        assert aws.account_id == "123456789012"
        assert aws.region == "us-east-1"
        assert aws.ecs_cluster == "agentbreeder-cluster"
        assert aws.execution_role_arn == "arn:aws:iam::123456789012:role/ecsTaskExecutionRole"
        assert aws.vpc_subnets == ["subnet-aaa", "subnet-bbb"]
        assert aws.security_groups == ["sg-111"]
        assert aws.task_role_arn is None

    def test_optional_task_role_arn(self) -> None:
        from engine.deployers.aws_ecs import _extract_ecs_config

        config = _make_agent_config(
            extra_env={"AWS_TASK_ROLE_ARN": "arn:aws:iam::123456789012:role/taskRole"}
        )
        aws = _extract_ecs_config(config)
        assert aws.task_role_arn == "arn:aws:iam::123456789012:role/taskRole"

    def test_raises_when_account_id_missing(self) -> None:
        from engine.deployers.aws_ecs import _extract_ecs_config

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_ACCOUNT_ID"]

        with pytest.raises(ValueError, match="AWS_ACCOUNT_ID"):
            _extract_ecs_config(config)

    def test_raises_when_cluster_missing(self) -> None:
        from engine.deployers.aws_ecs import _extract_ecs_config

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_ECS_CLUSTER"]

        with pytest.raises(ValueError, match="AWS_ECS_CLUSTER"):
            _extract_ecs_config(config)

    def test_raises_when_execution_role_missing(self) -> None:
        from engine.deployers.aws_ecs import _extract_ecs_config

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_EXECUTION_ROLE_ARN"]

        with pytest.raises(ValueError, match="AWS_EXECUTION_ROLE_ARN"):
            _extract_ecs_config(config)

    def test_raises_when_subnets_missing(self) -> None:
        from engine.deployers.aws_ecs import _extract_ecs_config

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_VPC_SUBNETS"]

        with pytest.raises(ValueError, match="AWS_VPC_SUBNETS"):
            _extract_ecs_config(config)

    def test_raises_when_security_groups_missing(self) -> None:
        from engine.deployers.aws_ecs import _extract_ecs_config

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_SECURITY_GROUPS"]

        with pytest.raises(ValueError, match="AWS_SECURITY_GROUPS"):
            _extract_ecs_config(config)

    def test_region_falls_back_to_deploy_region(self) -> None:
        from engine.deployers.aws_ecs import _extract_ecs_config

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_REGION"]
        config.deploy.region = "eu-west-1"

        aws = _extract_ecs_config(config)
        assert aws.region == "eu-west-1"

    def test_region_default(self) -> None:
        from engine.deployers.aws_ecs import DEFAULT_REGION, _extract_ecs_config

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_REGION"]
        config.deploy.region = None

        aws = _extract_ecs_config(config)
        assert aws.region == DEFAULT_REGION


# ---------------------------------------------------------------------------
# _get_boto3_client — ImportError when boto3 is absent
# ---------------------------------------------------------------------------


class TestGetBoto3Client:
    def test_raises_import_error_with_pip_hint_when_boto3_missing(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        # Initialise _aws_config so the client getter has a region
        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        # Temporarily hide boto3 from the import system
        original = sys.modules.get("boto3")
        sys.modules["boto3"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(ImportError) as exc_info:
                deployer._get_boto3_client("ecs")
            assert "pip install agentbreeder[aws]" in str(exc_info.value)
        finally:
            if original is None:
                del sys.modules["boto3"]
            else:
                sys.modules["boto3"] = original


# ---------------------------------------------------------------------------
# provision()
# ---------------------------------------------------------------------------


class TestProvision:
    @pytest.mark.asyncio
    async def test_provision_creates_ecr_repo_when_absent(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        ecr_mock = MagicMock()
        # Simulate repo does not exist → RepositoryNotFoundException
        ecr_mock.exceptions.RepositoryNotFoundException = type(
            "RepositoryNotFoundException", (Exception,), {}
        )
        ecr_mock.describe_repositories.side_effect = (
            ecr_mock.exceptions.RepositoryNotFoundException
        )
        ecr_mock.create_repository.return_value = {}

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            result = await deployer.provision(config)

        ecr_mock.create_repository.assert_called_once()
        call_kwargs = ecr_mock.create_repository.call_args.kwargs
        assert call_kwargs["repositoryName"] == "my-agent"

        assert "my-agent" in result.endpoint_url
        assert result.resource_ids["ecs_cluster"] == "agentbreeder-cluster"
        assert result.resource_ids["account_id"] == "123456789012"

    @pytest.mark.asyncio
    async def test_provision_skips_creation_when_repo_exists(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        ecr_mock = MagicMock()
        ecr_mock.exceptions.RepositoryNotFoundException = type(
            "RepositoryNotFoundException", (Exception,), {}
        )
        ecr_mock.describe_repositories.return_value = {
            "repositories": [{"repositoryName": "my-agent"}]
        }

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            await deployer.provision(config)

        ecr_mock.create_repository.assert_not_called()

    @pytest.mark.asyncio
    async def test_provision_raises_value_error_on_missing_account_id(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        del config.deploy.env_vars["AWS_ACCOUNT_ID"]

        with pytest.raises(ValueError, match="AWS_ACCOUNT_ID"):
            await deployer.provision(config)

    @pytest.mark.asyncio
    async def test_provision_raises_import_error_without_boto3(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        original = sys.modules.get("boto3")
        sys.modules["boto3"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(ImportError) as exc_info:
                await deployer.provision(config)
            assert "pip install agentbreeder[aws]" in str(exc_info.value)
        finally:
            if original is None:
                del sys.modules["boto3"]
            else:
                sys.modules["boto3"] = original

    @pytest.mark.asyncio
    async def test_provision_returns_ecr_image_uri_in_resource_ids(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        ecr_mock = MagicMock()
        ecr_mock.exceptions.RepositoryNotFoundException = type(
            "RepositoryNotFoundException", (Exception,), {}
        )
        ecr_mock.describe_repositories.return_value = {"repositories": [{}]}

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            result = await deployer.provision(config)

        expected_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
        assert result.resource_ids["image_uri"] == expected_uri


# ---------------------------------------------------------------------------
# deploy()
# ---------------------------------------------------------------------------


class TestDeploy:
    def _make_image(self) -> MagicMock:
        from pathlib import Path

        img = MagicMock()
        img.tag = "my-agent:1.0.0"
        img.context_dir = Path("/tmp/agent-context")
        return img

    @pytest.mark.asyncio
    async def test_deploy_registers_task_definition_and_creates_service(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        image = self._make_image()

        # Pre-populate AWS config so we skip re-extraction
        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)
        deployer._image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"

        ecs_mock = MagicMock()
        ecs_mock.register_task_definition.return_value = {
            "taskDefinition": {
                "taskDefinitionArn": (
                    "arn:aws:ecs:us-east-1:123456789012:task-definition/my-agent:1"
                )
            }
        }
        ecs_mock.describe_services.return_value = {"services": []}
        ecs_mock.create_service.return_value = {"service": {"serviceName": "my-agent"}}
        waiter_mock = MagicMock()
        ecs_mock.get_waiter.return_value = waiter_mock

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock) as push_mock,
            patch.object(deployer, "_get_boto3_client", return_value=ecs_mock),
        ):
            result = await deployer.deploy(config, image)

        push_mock.assert_awaited_once()

        # Task definition registered
        ecs_mock.register_task_definition.assert_called_once()
        td_kwargs = ecs_mock.register_task_definition.call_args.kwargs
        assert td_kwargs["family"] == "my-agent"
        assert td_kwargs["networkMode"] == "awsvpc"
        assert td_kwargs["executionRoleArn"] == (
            "arn:aws:iam::123456789012:role/ecsTaskExecutionRole"
        )

        # Service created (not updated)
        ecs_mock.create_service.assert_called_once()
        svc_kwargs = ecs_mock.create_service.call_args.kwargs
        assert svc_kwargs["serviceName"] == "my-agent"
        assert svc_kwargs["desiredCount"] == 1
        assert svc_kwargs["launchType"] == "FARGATE"

        # Waiter invoked
        ecs_mock.get_waiter.assert_called_once_with("services_stable")
        waiter_mock.wait.assert_called_once()

        assert result.status == "running"
        assert result.agent_name == "my-agent"

    @pytest.mark.asyncio
    async def test_deploy_is_idempotent_when_service_is_healthy(self) -> None:
        """W4-35: a healthy existing ECS service short-circuits deploy()."""
        deployer = _make_deployer()
        config = _make_agent_config()
        image = self._make_image()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)
        deployer._image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"

        ecs_mock = MagicMock()
        # Service is healthy: ACTIVE + running == desired
        ecs_mock.describe_services.return_value = {
            "services": [
                {
                    "serviceName": "my-agent",
                    "serviceArn": "arn:aws:ecs:us-east-1:123:service/cluster/my-agent",
                    "status": "ACTIVE",
                    "runningCount": 1,
                    "desiredCount": 1,
                }
            ]
        }

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock) as push_mock,
            patch.object(deployer, "_get_boto3_client", return_value=ecs_mock),
        ):
            result = await deployer.deploy(config, image)

        # No image push, no register_task_definition, no update_service —
        # idempotent return.
        push_mock.assert_not_called()
        ecs_mock.register_task_definition.assert_not_called()
        ecs_mock.update_service.assert_not_called()
        ecs_mock.create_service.assert_not_called()
        assert result.status == "running"
        assert result.agent_name == "my-agent"

    @pytest.mark.asyncio
    async def test_deploy_cleans_stale_service_then_creates(self) -> None:
        """W4-35: an unhealthy existing ECS service is torn down before redeploy."""
        deployer = _make_deployer()
        config = _make_agent_config()
        image = self._make_image()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)
        deployer._image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"

        ecs_mock = MagicMock()
        # First describe_services call returns an UNHEALTHY service
        # (status ACTIVE but running < desired). Subsequent calls (after
        # teardown) return no services so deploy can recreate.
        ecs_mock.describe_services.side_effect = [
            {
                "services": [
                    {
                        "serviceName": "my-agent",
                        "serviceArn": "arn:aws:ecs:us-east-1:123:service/cluster/my-agent",
                        "status": "ACTIVE",
                        "runningCount": 0,
                        "desiredCount": 1,
                    }
                ]
            },
            {"services": []},
        ]
        ecs_mock.register_task_definition.return_value = {
            "taskDefinition": {
                "taskDefinitionArn": (
                    "arn:aws:ecs:us-east-1:123456789012:task-definition/my-agent:2"
                )
            }
        }
        ecs_mock.update_service.return_value = {}
        ecs_mock.delete_service.return_value = {}
        paginator_mock = MagicMock()
        paginator_mock.paginate.return_value = [{"taskDefinitionArns": []}]
        ecs_mock.get_paginator.return_value = paginator_mock
        waiter_mock = MagicMock()
        ecs_mock.get_waiter.return_value = waiter_mock

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock),
            patch.object(deployer, "_get_boto3_client", return_value=ecs_mock),
        ):
            await deployer.deploy(config, image)

        # Teardown scaled to zero, deleted; then create_service was called for
        # the fresh deploy.
        ecs_mock.update_service.assert_called_once()  # scale-to-zero
        ecs_mock.delete_service.assert_called_once()
        ecs_mock.create_service.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_log_configuration_uses_cloudwatch(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        image = self._make_image()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)
        deployer._image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"

        ecs_mock = MagicMock()
        task_arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/my-agent:1"
        ecs_mock.register_task_definition.return_value = {
            "taskDefinition": {"taskDefinitionArn": task_arn}
        }
        ecs_mock.describe_services.return_value = {"services": []}
        ecs_mock.create_service.return_value = {}
        ecs_mock.get_waiter.return_value = MagicMock()

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock),
            patch.object(deployer, "_get_boto3_client", return_value=ecs_mock),
        ):
            await deployer.deploy(config, image)

        td_kwargs = ecs_mock.register_task_definition.call_args.kwargs
        container_def = td_kwargs["containerDefinitions"][0]
        log_config = container_def["logConfiguration"]
        assert log_config["logDriver"] == "awslogs"
        assert log_config["options"]["awslogs-group"] == "/agentbreeder/my-agent"


# ---------------------------------------------------------------------------
# teardown()
# ---------------------------------------------------------------------------


class TestTeardown:
    @pytest.mark.asyncio
    async def test_teardown_scales_to_zero_then_deletes_service(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.update_service.return_value = {}
        ecs_mock.delete_service.return_value = {}
        paginator_mock = MagicMock()
        paginator_mock.paginate.return_value = [{"taskDefinitionArns": []}]
        ecs_mock.get_paginator.return_value = paginator_mock

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            await deployer.teardown("my-agent")

        # update_service called with desiredCount=0
        ecs_mock.update_service.assert_called_once()
        update_kwargs = ecs_mock.update_service.call_args.kwargs
        assert update_kwargs["desiredCount"] == 0
        assert update_kwargs["service"] == "my-agent"

        # delete_service called
        ecs_mock.delete_service.assert_called_once()
        delete_kwargs = ecs_mock.delete_service.call_args.kwargs
        assert delete_kwargs["service"] == "my-agent"
        assert delete_kwargs["force"] is True

    @pytest.mark.asyncio
    async def test_teardown_deregisters_task_definition_revisions(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.update_service.return_value = {}
        ecs_mock.delete_service.return_value = {}

        task_arns = [
            "arn:aws:ecs:us-east-1:123456789012:task-definition/my-agent:1",
            "arn:aws:ecs:us-east-1:123456789012:task-definition/my-agent:2",
        ]
        paginator_mock = MagicMock()
        paginator_mock.paginate.return_value = [{"taskDefinitionArns": task_arns}]
        ecs_mock.get_paginator.return_value = paginator_mock
        ecs_mock.deregister_task_definition.return_value = {}

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            await deployer.teardown("my-agent")

        assert ecs_mock.deregister_task_definition.call_count == 2

    @pytest.mark.asyncio
    async def test_teardown_raises_runtime_error_without_aws_config(self) -> None:
        deployer = _make_deployer()

        with pytest.raises(RuntimeError, match="AWS config"):
            await deployer.teardown("orphan-agent")


# ---------------------------------------------------------------------------
# get_logs()
# ---------------------------------------------------------------------------


class TestGetLogs:
    @pytest.mark.asyncio
    async def test_get_logs_calls_filter_log_events_with_correct_group(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        logs_mock = MagicMock()
        ts_ms = int(datetime(2026, 1, 1, 0, 0, 0).timestamp() * 1000)
        logs_mock.filter_log_events.return_value = {
            "events": [
                {"timestamp": ts_ms, "message": "Agent started", "eventId": "1"},
                {"timestamp": ts_ms + 1000, "message": "Processed request", "eventId": "2"},
            ]
        }

        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            logs = await deployer.get_logs("my-agent")

        logs_mock.filter_log_events.assert_called_once()
        call_kwargs = logs_mock.filter_log_events.call_args.kwargs
        assert call_kwargs["logGroupName"] == "/agentbreeder/my-agent"

        assert len(logs) == 2
        assert "Agent started" in logs[0]
        assert "Processed request" in logs[1]

    @pytest.mark.asyncio
    async def test_get_logs_passes_start_time_when_since_provided(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        logs_mock = MagicMock()
        logs_mock.filter_log_events.return_value = {"events": []}

        since = datetime(2026, 3, 1, 12, 0, 0)

        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            await deployer.get_logs("my-agent", since=since)

        call_kwargs = logs_mock.filter_log_events.call_args.kwargs
        expected_ts = int(since.timestamp() * 1000)
        assert call_kwargs["startTime"] == expected_ts

    @pytest.mark.asyncio
    async def test_get_logs_returns_placeholder_when_no_events(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        logs_mock = MagicMock()
        logs_mock.filter_log_events.return_value = {"events": []}

        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            logs = await deployer.get_logs("my-agent")

        assert len(logs) == 1
        assert "No logs found" in logs[0]

    @pytest.mark.asyncio
    async def test_get_logs_returns_error_message_on_exception(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        logs_mock = MagicMock()
        logs_mock.filter_log_events.side_effect = Exception("CloudWatch unavailable")

        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            logs = await deployer.get_logs("my-agent")

        assert len(logs) == 1
        assert "Error fetching logs" in logs[0]


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_returns_healthy_on_200(self) -> None:
        deployer = _make_deployer()

        deploy_result = DeployResult(
            endpoint_url="https://my-agent.us-east-1.ecs.local",
            container_id="123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0",
            status="running",
            agent_name="my-agent",
            version="1.0.0",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            status = await deployer.health_check(deploy_result, timeout=10, interval=5)

        assert status.healthy is True
        assert status.checks["healthy"] is True
        assert status.checks["reachable"] is True

    @pytest.mark.asyncio
    async def test_health_check_returns_unhealthy_after_timeout(self) -> None:
        import httpx as real_httpx

        deployer = _make_deployer()

        deploy_result = DeployResult(
            endpoint_url="https://my-agent.us-east-1.ecs.local",
            container_id="123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0",
            status="running",
            agent_name="my-agent",
            version="1.0.0",
        )

        with (
            patch("httpx.AsyncClient") as mock_client_cls,
            _fast_health_clock(),
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=real_httpx.ConnectError("refused"))
            mock_client_cls.return_value = mock_client

            status = await deployer.health_check(deploy_result, timeout=10, interval=5)

        assert status.healthy is False
        assert status.checks["healthy"] is False

    @pytest.mark.asyncio
    async def test_health_check_retries_on_non_200(self) -> None:
        deployer = _make_deployer()

        deploy_result = DeployResult(
            endpoint_url="https://my-agent.us-east-1.ecs.local",
            container_id="123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0",
            status="running",
            agent_name="my-agent",
            version="1.0.0",
        )

        # First call returns 503, second returns 200
        response_503 = MagicMock()
        response_503.status_code = 503
        response_200 = MagicMock()
        response_200.status_code = 200

        call_count = 0

        async def side_effect(url: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return response_503
            return response_200

        with (
            patch("httpx.AsyncClient") as mock_client_cls,
            _fast_health_clock(),
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=side_effect)
            mock_client_cls.return_value = mock_client

            status = await deployer.health_check(deploy_result, timeout=20, interval=5)

        assert status.healthy is True
        assert call_count == 2


# ---------------------------------------------------------------------------
# get_url()
# ---------------------------------------------------------------------------


class TestGetUrl:
    @pytest.mark.asyncio
    async def test_get_url_returns_ecs_local_placeholder(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        url = await deployer.get_url("my-agent")
        assert "my-agent" in url
        assert "us-east-1" in url

    @pytest.mark.asyncio
    async def test_get_url_raises_without_aws_config(self) -> None:
        deployer = _make_deployer()

        with pytest.raises(RuntimeError, match="AWS config"):
            await deployer.get_url("my-agent")


# ---------------------------------------------------------------------------
# _build_container_definition
# ---------------------------------------------------------------------------


class TestBuildContainerDefinition:
    def test_env_vars_exclude_aws_prefixed_keys(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        container_def = deployer._build_container_definition(
            config, "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
        )

        env_names = {e["name"] for e in container_def["environment"]}

        # Framework vars are present
        assert "AGENT_NAME" in env_names
        assert "AGENT_VERSION" in env_names
        assert "AGENT_FRAMEWORK" in env_names

        # User env vars without AWS_ prefix are included
        assert "LOG_LEVEL" in env_names

        # AWS_ infra vars are excluded
        assert "AWS_ACCOUNT_ID" not in env_names
        assert "AWS_ECS_CLUSTER" not in env_names
        assert "AWS_EXECUTION_ROLE_ARN" not in env_names

    def test_log_configuration_contains_correct_group(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        container_def = deployer._build_container_definition(
            config, "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
        )

        log_opts = container_def["logConfiguration"]["options"]
        assert log_opts["awslogs-group"] == "/agentbreeder/my-agent"
        assert log_opts["awslogs-region"] == "us-east-1"


# ---------------------------------------------------------------------------
# _get_boto3_client — success path (boto3 present, uses configured region)
# ---------------------------------------------------------------------------


class TestGetBoto3ClientSuccess:
    def test_returns_client_with_configured_region(self) -> None:
        """When boto3 is present and AWS config is set, client uses the configured region."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        mock_boto3 = MagicMock()
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            result = deployer._get_boto3_client("ecs")

        mock_boto3.client.assert_called_once_with("ecs", region_name="us-east-1")
        assert result is mock_client

    def test_uses_default_region_when_aws_config_absent(self) -> None:
        """When _aws_config is None, client falls back to DEFAULT_REGION."""
        from engine.deployers.aws_ecs import DEFAULT_REGION

        deployer = _make_deployer()
        assert deployer._aws_config is None

        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = MagicMock()

        with patch.dict(sys.modules, {"boto3": mock_boto3}):
            deployer._get_boto3_client("s3")

        mock_boto3.client.assert_called_once_with("s3", region_name=DEFAULT_REGION)


# ---------------------------------------------------------------------------
# _ensure_ecr_repository — fallback / generic exception path
# ---------------------------------------------------------------------------


class TestEnsureECRRepository:
    @pytest.mark.asyncio
    async def test_creates_repo_on_repository_not_found(self) -> None:
        """RepositoryNotFoundException triggers repo creation."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecr_mock = MagicMock()

        class RepositoryNotFoundError(Exception):
            pass

        ecr_mock.exceptions.RepositoryNotFoundException = RepositoryNotFoundError
        ecr_mock.describe_repositories.side_effect = RepositoryNotFoundError("not found")
        ecr_mock.create_repository.return_value = {}

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            await deployer._ensure_ecr_repository("my-agent")

        ecr_mock.create_repository.assert_called_once()
        call_kwargs = ecr_mock.create_repository.call_args.kwargs
        assert call_kwargs["repositoryName"] == "my-agent"

    @pytest.mark.asyncio
    async def test_fallback_path_for_repositorynotfound_in_exc_type(self) -> None:
        """Generic exception whose type name contains 'RepositoryNotFound' triggers fallback."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecr_mock = MagicMock()

        # Make RepositoryNotFoundException a *different* class so the first
        # except branch is not matched, falling through to the generic handler.
        class FallbackBaseError(Exception):
            pass

        class RepositoryNotFoundAliasError(FallbackBaseError):
            pass

        class FakeExceptions:
            RepositoryNotFoundException = FallbackBaseError  # doesn't match

        ecr_mock.exceptions = FakeExceptions()

        class RepositoryNotFoundError(Exception):
            pass

        # Name contains "RepositoryNotFound" → fallback branch
        RepositoryNotFoundError.__name__ = "RepositoryNotFoundError"
        ecr_mock.describe_repositories.side_effect = RepositoryNotFoundError("nope")
        ecr_mock.create_repository.return_value = {}

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            await deployer._ensure_ecr_repository("my-agent")

        ecr_mock.create_repository.assert_called_once_with(repositoryName="my-agent")

    @pytest.mark.asyncio
    async def test_generic_exception_logs_warning_and_continues(self) -> None:
        """An unrecognised exception is logged as a warning and does not raise."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecr_mock = MagicMock()

        class UnrelatedBaseError(Exception):
            pass

        class FakeExceptions:
            RepositoryNotFoundException = UnrelatedBaseError

        ecr_mock.exceptions = FakeExceptions()

        class WeirdError(Exception):
            pass

        ecr_mock.describe_repositories.side_effect = WeirdError("unexpected")

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            # Should NOT raise
            await deployer._ensure_ecr_repository("my-agent")

        ecr_mock.create_repository.assert_not_called()


# ---------------------------------------------------------------------------
# _push_image — full Docker push flow
# ---------------------------------------------------------------------------


class TestPushImage:
    @pytest.mark.asyncio
    async def test_push_image_raises_import_error_without_docker(self) -> None:
        """ImportError with hint is raised when the docker SDK is missing."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config
        from engine.runtimes.base import ContainerImage

        deployer._aws_config = _extract_ecs_config(config)
        image = MagicMock(spec=ContainerImage)
        image.tag = "my-agent:1.0.0"
        image.context_dir = MagicMock()

        with patch.dict(sys.modules, {"docker": None}):
            with pytest.raises(ImportError, match="Docker SDK"):
                await deployer._push_image(image, "123.dkr.ecr.us-east-1.amazonaws.com/x:1")

    @pytest.mark.asyncio
    async def test_push_image_raises_on_ecr_push_error(self) -> None:
        """RuntimeError is raised when the push output contains an 'error' key."""
        from pathlib import Path

        from engine.deployers.aws_ecs import _extract_ecs_config
        from engine.runtimes.base import ContainerImage

        deployer = _make_deployer()
        config = _make_agent_config()
        deployer._aws_config = _extract_ecs_config(config)

        image = MagicMock(spec=ContainerImage)
        image.tag = "my-agent:1.0.0"
        image.context_dir = Path("/tmp/ctx")

        # Set up docker mock
        mock_docker = MagicMock()
        built_image = MagicMock()
        mock_docker.from_env.return_value.images.build.return_value = (built_image, [])
        # Push stream yields an error chunk
        mock_docker.from_env.return_value.images.push.return_value = iter(
            [{"error": "denied: access forbidden"}]
        )

        ecr_mock = MagicMock()
        ecr_mock.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": "dXNlcjpwYXNz"}]  # user:pass
        }

        import base64

        token = base64.b64encode(b"user:pass").decode()
        ecr_mock.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": token}]
        }

        with (
            patch.dict(sys.modules, {"docker": mock_docker}),
            patch.object(deployer, "_get_boto3_client", return_value=ecr_mock),
        ):
            with pytest.raises(RuntimeError, match="ECR image push failed"):
                await deployer._push_image(
                    image, "123.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
                )

    @pytest.mark.asyncio
    async def test_push_image_success_path(self) -> None:
        """Successful push completes without raising."""
        import base64
        from pathlib import Path

        from engine.deployers.aws_ecs import _extract_ecs_config
        from engine.runtimes.base import ContainerImage

        deployer = _make_deployer()
        config = _make_agent_config()
        deployer._aws_config = _extract_ecs_config(config)

        image = MagicMock(spec=ContainerImage)
        image.tag = "my-agent:1.0.0"
        image.context_dir = Path("/tmp/ctx")

        token = base64.b64encode(b"AWS:secret-token").decode()
        ecr_mock = MagicMock()
        ecr_mock.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": token}]
        }

        mock_docker = MagicMock()
        built_image = MagicMock()
        # Build logs include a 'stream' chunk
        mock_docker.from_env.return_value.images.build.return_value = (
            built_image,
            [{"stream": "Step 1/3"}],
        )
        # Push stream has only a status chunk (no error)
        mock_docker.from_env.return_value.images.push.return_value = iter(
            [{"status": "Pushing"}, {"status": "Pushed"}]
        )

        with (
            patch.dict(sys.modules, {"docker": mock_docker}),
            patch.object(deployer, "_get_boto3_client", return_value=ecr_mock),
        ):
            await deployer._push_image(image, "123.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0")

        built_image.tag.assert_called_once_with(
            "123.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
        )


# ---------------------------------------------------------------------------
# _register_task_definition — task_role_arn branch
# ---------------------------------------------------------------------------


class TestRegisterTaskDefinition:
    @pytest.mark.asyncio
    async def test_task_role_arn_included_when_set(self) -> None:
        """taskRoleArn kwarg is added when AWS_TASK_ROLE_ARN is configured."""
        deployer = _make_deployer()
        config = _make_agent_config(
            extra_env={"AWS_TASK_ROLE_ARN": "arn:aws:iam::123456789012:role/myTaskRole"}
        )

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.register_task_definition.return_value = {
            "taskDefinition": {
                "taskDefinitionArn": "arn:aws:ecs:us-east-1:123:task-definition/my-agent:1"
            }
        }

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            arn = await deployer._register_task_definition(
                config, "123.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
            )

        assert arn.endswith(":1")
        call_kwargs = ecs_mock.register_task_definition.call_args.kwargs
        assert call_kwargs["taskRoleArn"] == "arn:aws:iam::123456789012:role/myTaskRole"


# ---------------------------------------------------------------------------
# #400: ECS multi-container sidecar wiring
# ---------------------------------------------------------------------------


class TestRegisterTaskDefinitionSidecar:
    """When guardrails/tools/A2A are declared, the task def must carry both the
    agent (essential, internal :8081) and the sidecar (non-essential, ingress
    :8080) so inbound traffic terminates at the sidecar before the agent."""

    @pytest.mark.asyncio
    async def test_task_def_has_agent_and_sidecar_containers(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        config.guardrails = ["pii_detection"]

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.register_task_definition.return_value = {
            "taskDefinition": {
                "taskDefinitionArn": "arn:aws:ecs:us-east-1:123:task-definition/my-agent:1"
            }
        }

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            await deployer._register_task_definition(
                config, "123.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
            )

        defs = ecs_mock.register_task_definition.call_args.kwargs["containerDefinitions"]
        by_name = {c["name"]: c for c in defs}
        assert "agentbreeder-sidecar" in by_name
        agent = by_name[config.name]
        sidecar = by_name["agentbreeder-sidecar"]

        # Agent: essential, listens on the internal port, not the ingress port.
        assert agent["essential"] is True
        assert agent["portMappings"][0]["containerPort"] == 8081
        agent_env = {e["name"]: e["value"] for e in agent["environment"]}
        assert agent_env["PORT"] == "8081"

        # Sidecar: non-essential ingress on 8080, proxying to the agent on 8081.
        assert sidecar["essential"] is False
        assert sidecar["portMappings"][0]["containerPort"] == 8080
        sidecar_env = {e["name"]: e["value"] for e in sidecar["environment"]}
        assert sidecar_env["AGENTBREEDER_SIDECAR_AGENT_URL"] == "http://localhost:8081"

    @pytest.mark.asyncio
    async def test_task_def_single_container_without_governance(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()  # no guardrails → no sidecar

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.register_task_definition.return_value = {
            "taskDefinition": {
                "taskDefinitionArn": "arn:aws:ecs:us-east-1:123:task-definition/my-agent:1"
            }
        }

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            await deployer._register_task_definition(
                config, "123.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
            )

        defs = ecs_mock.register_task_definition.call_args.kwargs["containerDefinitions"]
        assert [c["name"] for c in defs] == [config.name]
        assert defs[0]["portMappings"][0]["containerPort"] == 8080


# ---------------------------------------------------------------------------
# teardown() — scale-to-zero exception is swallowed, delete failure raises
# ---------------------------------------------------------------------------


class TestTeardownEdgeCases:
    @pytest.mark.asyncio
    async def test_teardown_retries_scale_to_zero_on_transient_failure(self) -> None:
        """W4-36: scale-to-zero retries with backoff and proceeds on eventual success."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        # First two attempts fail, third succeeds.
        ecs_mock.update_service.side_effect = [
            Exception("transient throttling"),
            Exception("transient throttling"),
            {},
        ]
        ecs_mock.delete_service.return_value = {}
        paginator_mock = MagicMock()
        paginator_mock.paginate.return_value = [{"taskDefinitionArns": []}]
        ecs_mock.get_paginator.return_value = paginator_mock

        with (
            patch("api.retry.asyncio.sleep", new_callable=AsyncMock),
            patch.object(deployer, "_get_boto3_client", return_value=ecs_mock),
        ):
            await deployer.teardown("my-agent")

        # update_service called 3 times due to retry, delete_service then called.
        assert ecs_mock.update_service.call_count == 3
        ecs_mock.delete_service.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_raises_when_scale_to_zero_exhausts_retries(self) -> None:
        """W4-36: if all 3 retries fail, teardown fails loudly instead of swallowing."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from api.retry import RetryExhaustedError
        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.update_service.side_effect = Exception("service not found")
        ecs_mock.delete_service.return_value = {}

        with (
            patch("api.retry.asyncio.sleep", new_callable=AsyncMock),
            patch.object(deployer, "_get_boto3_client", return_value=ecs_mock),
        ):
            with pytest.raises(RetryExhaustedError):
                await deployer.teardown("my-agent")

        assert ecs_mock.update_service.call_count == 3
        ecs_mock.delete_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_teardown_raises_when_delete_service_fails(self) -> None:
        """If delete_service raises, teardown re-raises the exception."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.update_service.return_value = {}
        ecs_mock.delete_service.side_effect = RuntimeError("cluster missing")

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            with pytest.raises(RuntimeError, match="cluster missing"):
                await deployer.teardown("my-agent")

    @pytest.mark.asyncio
    async def test_teardown_swallows_deregister_exception(self) -> None:
        """If paginator/deregister raises, teardown logs a warning and does not re-raise."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.update_service.return_value = {}
        ecs_mock.delete_service.return_value = {}
        ecs_mock.get_paginator.side_effect = Exception("paginator unavailable")

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            # Should complete without raising
            await deployer.teardown("my-agent")


# ---------------------------------------------------------------------------
# get_logs() — ResourceNotFoundException and NoSuchLogGroup paths
# ---------------------------------------------------------------------------


class TestGetLogsEdgeCases:
    @pytest.mark.asyncio
    async def test_get_logs_returns_placeholder_on_resource_not_found(self) -> None:
        """ResourceNotFoundException returns a 'does not exist yet' message."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        logs_mock = MagicMock()

        class ResourceNotFoundError(Exception):
            pass

        ResourceNotFoundError.__name__ = "ResourceNotFoundException"
        logs_mock.filter_log_events.side_effect = ResourceNotFoundError("no group")

        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            result = await deployer.get_logs("my-agent")

        assert len(result) == 1
        assert "does not exist yet" in result[0]

    @pytest.mark.asyncio
    async def test_get_logs_returns_error_message_for_generic_exception(self) -> None:
        """Any other exception returns an 'Error fetching logs' message."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        logs_mock = MagicMock()
        logs_mock.filter_log_events.side_effect = ConnectionError("network timeout")

        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            result = await deployer.get_logs("my-agent")

        assert len(result) == 1
        assert "Error fetching logs" in result[0]


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_raises_without_aws_config(self) -> None:
        deployer = _make_deployer()
        with pytest.raises(RuntimeError, match="not initialized"):
            await deployer.status("my-agent")

    @pytest.mark.asyncio
    async def test_status_returns_not_found_when_no_services(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.describe_services.return_value = {"services": []}

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            result = await deployer.status("my-agent")

        assert result == {"name": "my-agent", "status": "not_found"}

    @pytest.mark.asyncio
    async def test_status_returns_service_fields(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.describe_services.return_value = {
            "services": [
                {
                    "status": "ACTIVE",
                    "runningCount": 2,
                    "desiredCount": 2,
                    "pendingCount": 0,
                    "taskDefinition": "arn:aws:ecs:us-east-1:123:task-definition/my-agent:3",
                    "clusterArn": "arn:aws:ecs:us-east-1:123:cluster/agentbreeder-cluster",
                }
            ]
        }

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            result = await deployer.status("my-agent")

        assert result["status"] == "ACTIVE"
        assert result["running_count"] == 2
        assert result["desired_count"] == 2
        assert result["name"] == "my-agent"


# ---------------------------------------------------------------------------
# deploy() — no prior provision (aws_config / image_uri are None)
# ---------------------------------------------------------------------------


class TestLookupExisting:
    """W4-35: idempotency lookup."""

    @pytest.mark.asyncio
    async def test_lookup_returns_none_when_aws_config_absent(self) -> None:
        deployer = _make_deployer()
        result = await deployer._lookup_existing("my-agent")
        assert result is None

    @pytest.mark.asyncio
    async def test_lookup_returns_none_when_no_active_services(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)
        ecs_mock = MagicMock()
        ecs_mock.describe_services.return_value = {"services": []}

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            assert await deployer._lookup_existing("my-agent") is None

    @pytest.mark.asyncio
    async def test_lookup_returns_healthy_for_active_service_at_desired(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)
        ecs_mock = MagicMock()
        ecs_mock.describe_services.return_value = {
            "services": [
                {
                    "serviceName": "my-agent",
                    "serviceArn": "arn:aws:ecs:us-east-1:123:service/c/my-agent",
                    "status": "ACTIVE",
                    "runningCount": 2,
                    "desiredCount": 2,
                }
            ]
        }

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            existing = await deployer._lookup_existing("my-agent")
        assert existing is not None
        assert existing.status == "healthy"
        assert "my-agent" in (existing.url or "")

    @pytest.mark.asyncio
    async def test_lookup_returns_unhealthy_for_active_service_below_desired(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)
        ecs_mock = MagicMock()
        ecs_mock.describe_services.return_value = {
            "services": [
                {
                    "serviceName": "my-agent",
                    "status": "ACTIVE",
                    "runningCount": 0,
                    "desiredCount": 1,
                }
            ]
        }

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            existing = await deployer._lookup_existing("my-agent")
        assert existing is not None
        assert existing.status == "unhealthy"

    @pytest.mark.asyncio
    async def test_lookup_swallows_describe_exception(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)
        ecs_mock = MagicMock()
        ecs_mock.describe_services.side_effect = RuntimeError("api down")

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            assert await deployer._lookup_existing("my-agent") is None


class TestDeploySidecarPreValidation:
    """W4-37: pre-validate sidecar config before any cloud API call."""

    @pytest.mark.asyncio
    async def test_deploy_raises_on_invalid_sidecar_before_cloud_call(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        # Pydantic models allow attribute assignment on instances; attach an
        # invalid sidecar block so validate_sidecar_config rejects at submit.
        object.__setattr__(config.deploy, "sidecar", {"enabled": "yes"})
        image = MagicMock()
        image.tag = "my-agent:1.0.0"
        image.context_dir = MagicMock()

        ecs_mock = MagicMock()

        from engine.sidecar import SidecarConfigError

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock) as push_mock,
            patch.object(deployer, "_get_boto3_client", return_value=ecs_mock),
        ):
            with pytest.raises(SidecarConfigError):
                await deployer.deploy(config, image)

        # No cloud call happened.
        push_mock.assert_not_called()
        ecs_mock.describe_services.assert_not_called()
        ecs_mock.register_task_definition.assert_not_called()


class TestDeployNoPriorProvision:
    @pytest.mark.asyncio
    async def test_deploy_extracts_config_when_aws_config_none(self) -> None:
        """deploy() self-initialises aws_config and image_uri when not pre-set."""
        deployer = _make_deployer()
        config = _make_agent_config()
        image = MagicMock()
        image.tag = "my-agent:1.0.0"
        image.context_dir = MagicMock()

        assert deployer._aws_config is None
        assert deployer._image_uri is None

        ecs_mock = MagicMock()
        ecs_mock.register_task_definition.return_value = {
            "taskDefinition": {
                "taskDefinitionArn": "arn:aws:ecs:us-east-1:123:task-definition/my-agent:1"
            }
        }
        ecs_mock.describe_services.return_value = {"services": []}
        ecs_mock.create_service.return_value = {}
        ecs_mock.get_waiter.return_value = MagicMock()

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock),
            patch.object(deployer, "_get_boto3_client", return_value=ecs_mock),
        ):
            result = await deployer.deploy(config, image)

        assert deployer._aws_config is not None
        assert deployer._image_uri is not None
        assert result.status == "running"


# ---------------------------------------------------------------------------
# Resource unit conversion + log-group pre-creation (deployer hardening)
# ---------------------------------------------------------------------------


class TestResourceUnitConversion:
    """The documented agent.yaml uses vCPU/Gi notation (cpu:"1", memory:"2Gi").
    Fargate's RegisterTaskDefinition needs CPU units (1 vCPU = 1024) and MiB.
    The deployer must convert, while still accepting raw-unit notation.
    """

    @pytest.mark.parametrize(
        "cpu_in,mem_in,cpu_out,mem_out",
        [
            ("1", "2Gi", "1024", "2048"),  # documented vCPU/Gi notation
            ("512", "1024", "512", "1024"),  # raw Fargate units pass through
            ("0.5", "1Gi", "512", "1024"),  # fractional vCPU
            ("2", "4Gi", "2048", "4096"),
            ("256", "512Mi", "256", "512"),  # raw units + Mi suffix
        ],
    )
    @pytest.mark.asyncio
    async def test_register_task_definition_normalizes_resources(
        self, cpu_in: str, mem_in: str, cpu_out: str, mem_out: str
    ) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        config.deploy.resources.cpu = cpu_in
        config.deploy.resources.memory = mem_in

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.register_task_definition.return_value = {
            "taskDefinition": {
                "taskDefinitionArn": "arn:aws:ecs:us-east-1:123:task-definition/my-agent:1"
            }
        }

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            await deployer._register_task_definition(
                config, "123.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
            )

        kwargs = ecs_mock.register_task_definition.call_args.kwargs
        # Task-level cpu/memory are strings in MiB / CPU units
        assert kwargs["cpu"] == cpu_out
        assert kwargs["memory"] == mem_out
        # Container-level values are ints and must match
        container = kwargs["containerDefinitions"][0]
        assert container["cpu"] == int(cpu_out)
        assert container["memory"] == int(mem_out)

    def test_build_container_definition_normalizes_resources(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        config.deploy.resources.cpu = "1"
        config.deploy.resources.memory = "2Gi"

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        container_def = deployer._build_container_definition(
            config, "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
        )

        assert container_def["cpu"] == 1024
        assert container_def["memory"] == 2048


class TestLogGroupPrecreation:
    """A BYO execution role with only AmazonECSTaskExecutionRolePolicy lacks
    logs:CreateLogGroup, so the deployer must pre-create the group itself and
    stop asking the awslogs driver to create it.
    """

    @pytest.mark.asyncio
    async def test_ensure_log_group_creates_named_group(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        logs_mock = MagicMock()
        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            await deployer._ensure_log_group(config)

        logs_mock.create_log_group.assert_called_once()
        assert (
            logs_mock.create_log_group.call_args.kwargs["logGroupName"] == "/agentbreeder/my-agent"
        )

    @pytest.mark.asyncio
    async def test_ensure_log_group_tolerates_already_exists(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        class ResourceAlreadyExistsError(Exception):
            pass

        logs_mock = MagicMock()
        logs_mock.create_log_group.side_effect = ResourceAlreadyExistsError(
            "log group already exists"
        )
        # Should not raise.
        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            await deployer._ensure_log_group(config)

    def test_container_definition_does_not_auto_create_log_group(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        container_def = deployer._build_container_definition(
            config, "123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:1.0.0"
        )

        log_opts = container_def["logConfiguration"]["options"]
        # We pre-create the group, so the task must NOT attempt CreateLogGroup.
        # AWS rejects awslogs-create-group="false", so the option is omitted.
        assert "awslogs-create-group" not in log_opts


class TestResolveTaskEndpoint:
    """Resolve the running task's public IP into a reachable http endpoint."""

    @pytest.mark.asyncio
    async def test_resolves_public_ip_endpoint(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.list_tasks.return_value = {"taskArns": ["arn:task/abc"]}
        ecs_mock.describe_tasks.return_value = {
            "tasks": [
                {
                    "attachments": [
                        {"details": [{"name": "networkInterfaceId", "value": "eni-123"}]}
                    ]
                }
            ]
        }
        ec2_mock = MagicMock()
        ec2_mock.describe_network_interfaces.return_value = {
            "NetworkInterfaces": [{"Association": {"PublicIp": "203.0.113.7"}}]
        }

        with patch.object(
            deployer,
            "_get_boto3_client",
            side_effect=_mock_boto3_client({"ecs": ecs_mock, "ec2": ec2_mock}),
        ):
            url = await deployer._resolve_task_endpoint(config)

        assert url == "http://203.0.113.7:8080"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_running_task(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        from engine.deployers.aws_ecs import _extract_ecs_config

        deployer._aws_config = _extract_ecs_config(config)

        ecs_mock = MagicMock()
        ecs_mock.list_tasks.return_value = {"taskArns": []}

        with patch.object(deployer, "_get_boto3_client", return_value=ecs_mock):
            url = await deployer._resolve_task_endpoint(config)

        assert url is None
