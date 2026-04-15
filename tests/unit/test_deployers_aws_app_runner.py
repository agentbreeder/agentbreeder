"""Unit tests for the AWS App Runner deployer.

All AWS API calls are mocked via unittest.mock.patch — no real AWS
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_config(
    *,
    name: str = "my-agent",
    version: str = "1.0.0",
    extra_env: dict[str, str] | None = None,
) -> AgentConfig:
    """Build a minimal AgentConfig wired for App Runner."""
    env_vars: dict[str, str] = {
        "AWS_ACCOUNT_ID": "123456789012",
        "AWS_REGION": "us-east-1",
        "AWS_ECR_REPO": "agentbreeder",
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
            runtime="app-runner",
            region="us-east-1",
            scaling=ScalingConfig(min=1, max=5),
            resources=ResourceConfig(cpu="1", memory="2Gi"),
            env_vars=env_vars,
        ),
        access=AccessConfig(),
    )


def _make_deployer():
    from engine.deployers.aws_app_runner import AWSAppRunnerDeployer

    return AWSAppRunnerDeployer()


# ---------------------------------------------------------------------------
# _extract_app_runner_config
# ---------------------------------------------------------------------------


class TestExtractAppRunnerConfig:
    def test_extracts_required_fields(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config

        config = _make_agent_config()
        ar = _extract_app_runner_config(config)
        assert ar.account_id == "123456789012"
        assert ar.region == "us-east-1"
        assert ar.ecr_repo == "agentbreeder"

    def test_raises_when_account_id_missing(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_ACCOUNT_ID"]
        with pytest.raises(ValueError, match="AWS_ACCOUNT_ID"):
            _extract_app_runner_config(config)

    def test_region_falls_back_to_deploy_region(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_REGION"]
        config.deploy.region = "eu-west-1"
        ar = _extract_app_runner_config(config)
        assert ar.region == "eu-west-1"

    def test_region_falls_back_to_default_when_neither_set(self) -> None:
        from engine.deployers.aws_app_runner import (
            DEFAULT_REGION,
            _extract_app_runner_config,
        )

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_REGION"]
        config.deploy.region = None
        ar = _extract_app_runner_config(config)
        assert ar.region == DEFAULT_REGION

    def test_ecr_repo_defaults_to_agent_name(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config

        config = _make_agent_config()
        del config.deploy.env_vars["AWS_ECR_REPO"]
        ar = _extract_app_runner_config(config)
        assert ar.ecr_repo == "my-agent"

    def test_optional_access_role_arn(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config

        config = _make_agent_config(
            extra_env={
                "AWS_APP_RUNNER_ACCESS_ROLE_ARN": "arn:aws:iam::123:role/AppRunnerECRAccess"
            }
        )
        ar = _extract_app_runner_config(config)
        assert ar.access_role_arn == "arn:aws:iam::123:role/AppRunnerECRAccess"

    def test_access_role_arn_is_none_by_default(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config

        config = _make_agent_config()
        ar = _extract_app_runner_config(config)
        assert ar.access_role_arn is None


# ---------------------------------------------------------------------------
# _build_env_vars
# ---------------------------------------------------------------------------


class TestBuildEnvVars:
    def test_includes_agent_metadata(self) -> None:
        from engine.deployers.aws_app_runner import _build_env_vars

        config = _make_agent_config()
        env_list = _build_env_vars(config)
        env_names = {e["Name"] for e in env_list}
        assert "AGENT_NAME" in env_names
        assert "AGENT_VERSION" in env_names
        assert "AGENT_FRAMEWORK" in env_names

    def test_passes_through_non_aws_env_vars(self) -> None:
        from engine.deployers.aws_app_runner import _build_env_vars

        config = _make_agent_config()
        env_list = _build_env_vars(config)
        env_names = {e["Name"] for e in env_list}
        assert "LOG_LEVEL" in env_names

    def test_excludes_aws_prefixed_keys(self) -> None:
        from engine.deployers.aws_app_runner import _build_env_vars

        config = _make_agent_config()
        env_list = _build_env_vars(config)
        env_names = {e["Name"] for e in env_list}
        assert "AWS_ACCOUNT_ID" not in env_names
        assert "AWS_REGION" not in env_names
        assert "AWS_ECR_REPO" not in env_names


# ---------------------------------------------------------------------------
# provision
# ---------------------------------------------------------------------------


class TestProvision:
    @pytest.mark.asyncio
    async def test_provision_returns_expected_url(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        ecr_mock = MagicMock()
        ecr_mock.exceptions.RepositoryNotFoundException = type(
            "RepositoryNotFoundException", (Exception,), {}
        )
        ecr_mock.describe_repositories.return_value = {"repositories": [{}]}

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            result = await deployer.provision(config)

        assert "my-agent" in result.endpoint_url
        assert result.resource_ids["account_id"] == "123456789012"
        assert "image_uri" in result.resource_ids

    @pytest.mark.asyncio
    async def test_provision_creates_ecr_repo_when_absent(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        ecr_mock = MagicMock()
        ecr_mock.exceptions.RepositoryNotFoundException = type(
            "RepositoryNotFoundException", (Exception,), {}
        )
        ecr_mock.describe_repositories.side_effect = (
            ecr_mock.exceptions.RepositoryNotFoundException
        )

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            await deployer.provision(config)

        ecr_mock.create_repository.assert_called_once()

    @pytest.mark.asyncio
    async def test_provision_skips_ecr_creation_when_repo_exists(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        ecr_mock = MagicMock()
        ecr_mock.exceptions.RepositoryNotFoundException = type(
            "RepositoryNotFoundException", (Exception,), {}
        )
        ecr_mock.describe_repositories.return_value = {"repositories": [{}]}

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            await deployer.provision(config)

        ecr_mock.create_repository.assert_not_called()

    @pytest.mark.asyncio
    async def test_provision_raises_import_error_without_boto3(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        original = sys.modules.get("boto3")
        sys.modules["boto3"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(ImportError, match="pip install"):
                await deployer.provision(config)
        finally:
            if original is None:
                del sys.modules["boto3"]
            else:
                sys.modules["boto3"] = original

    @pytest.mark.asyncio
    async def test_provision_stores_image_uri(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        ecr_mock = MagicMock()
        ecr_mock.exceptions.RepositoryNotFoundException = type(
            "RepositoryNotFoundException", (Exception,), {}
        )
        ecr_mock.describe_repositories.return_value = {"repositories": [{}]}

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            await deployer.provision(config)

        assert deployer._image_uri is not None
        assert "123456789012.dkr.ecr.us-east-1" in deployer._image_uri
        assert "agentbreeder" in deployer._image_uri


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


class TestDeploy:
    def _make_image(self) -> MagicMock:
        from pathlib import Path

        img = MagicMock()
        img.tag = "my-agent:1.0.0"
        img.context_dir = Path("/tmp/agent-context")
        return img

    @pytest.mark.asyncio
    async def test_deploy_creates_new_service(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        image = self._make_image()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)
        deployer._image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        ar_mock = MagicMock()
        ar_mock.exceptions = MagicMock()
        ar_mock.exceptions.ResourceNotFoundException = type(
            "ResourceNotFoundException", (Exception,), {}
        )
        ar_mock.describe_service.side_effect = ar_mock.exceptions.ResourceNotFoundException
        ar_mock.create_service.return_value = {
            "Service": {
                "ServiceArn": "arn:aws:apprunner:us-east-1:123:service/my-agent/abc",
                "ServiceUrl": "abc123.us-east-1.awsapprunner.com",
                "Status": "RUNNING",
            }
        }

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock),
            patch.object(deployer, "_get_boto3_client", return_value=ar_mock),
            patch.object(
                deployer,
                "_wait_for_service_running",
                new_callable=AsyncMock,
                return_value="https://abc123.us-east-1.awsapprunner.com",
            ),
        ):
            result = await deployer.deploy(config, image)

        ar_mock.create_service.assert_called_once()
        call_kwargs = ar_mock.create_service.call_args.kwargs
        assert call_kwargs["ServiceName"] == "my-agent"
        assert result.status == "running"
        assert "awsapprunner.com" in result.endpoint_url

    @pytest.mark.asyncio
    async def test_deploy_updates_existing_service(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        image = self._make_image()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)
        deployer._image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        ar_mock = MagicMock()
        ar_mock.describe_service.return_value = {
            "Service": {
                "ServiceArn": "arn:aws:apprunner:us-east-1:123:service/my-agent/abc",
                "ServiceUrl": "abc123.us-east-1.awsapprunner.com",
                "Status": "RUNNING",
            }
        }
        ar_mock.update_service.return_value = {
            "Service": {"ServiceArn": "arn:aws:apprunner:us-east-1:123:service/my-agent/abc"}
        }

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock),
            patch.object(deployer, "_get_boto3_client", return_value=ar_mock),
            patch.object(
                deployer,
                "_wait_for_service_running",
                new_callable=AsyncMock,
                return_value="https://abc123.us-east-1.awsapprunner.com",
            ),
        ):
            await deployer.deploy(config, image)

        ar_mock.update_service.assert_called_once()
        ar_mock.create_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_deploy_raises_if_provision_not_called(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        image = self._make_image()

        with pytest.raises(AssertionError):
            await deployer.deploy(config, image)

    @pytest.mark.asyncio
    async def test_deploy_result_has_correct_fields(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()
        image = self._make_image()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)
        deployer._image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        ar_mock = MagicMock()
        ar_mock.exceptions = MagicMock()
        ar_mock.exceptions.ResourceNotFoundException = type(
            "ResourceNotFoundException", (Exception,), {}
        )
        ar_mock.describe_service.side_effect = ar_mock.exceptions.ResourceNotFoundException
        ar_mock.create_service.return_value = {
            "Service": {
                "ServiceArn": "arn:aws:apprunner:us-east-1:123:service/my-agent/abc",
                "ServiceUrl": "abc123.us-east-1.awsapprunner.com",
                "Status": "RUNNING",
            }
        }

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock),
            patch.object(deployer, "_get_boto3_client", return_value=ar_mock),
            patch.object(
                deployer,
                "_wait_for_service_running",
                new_callable=AsyncMock,
                return_value="https://abc123.us-east-1.awsapprunner.com",
            ),
        ):
            result = await deployer.deploy(config, image)

        assert result.agent_name == "my-agent"
        assert result.version == "1.0.0"
        assert result.status == "running"


# ---------------------------------------------------------------------------
# teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    @pytest.mark.asyncio
    async def test_teardown_deletes_service(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)

        ar_mock = MagicMock()
        ar_mock.describe_service.return_value = {
            "Service": {"ServiceArn": "arn:aws:apprunner:us-east-1:123:service/my-agent/abc"}
        }
        ar_mock.delete_service.return_value = {}

        with patch.object(deployer, "_get_boto3_client", return_value=ar_mock):
            await deployer.teardown("my-agent")

        ar_mock.delete_service.assert_called_once()
        call_kwargs = ar_mock.delete_service.call_args.kwargs
        assert "ServiceArn" in call_kwargs

    @pytest.mark.asyncio
    async def test_teardown_raises_without_ar_config(self) -> None:
        deployer = _make_deployer()
        with pytest.raises(RuntimeError, match="App Runner config"):
            await deployer.teardown("orphan-agent")


# ---------------------------------------------------------------------------
# get_logs
# ---------------------------------------------------------------------------


class TestGetLogs:
    @pytest.mark.asyncio
    async def test_get_logs_calls_cloudwatch(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)

        logs_mock = MagicMock()
        logs_mock.filter_log_events.return_value = {
            "events": [{"timestamp": 1700000000000, "message": "App Runner started"}]
        }

        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            logs = await deployer.get_logs("my-agent")

        logs_mock.filter_log_events.assert_called_once()
        assert any("App Runner started" in log for log in logs)

    @pytest.mark.asyncio
    async def test_get_logs_returns_placeholder_when_not_provisioned(self) -> None:
        deployer = _make_deployer()
        logs = await deployer.get_logs("unprovisioned-agent")
        assert len(logs) == 1
        assert "Cannot get logs" in logs[0]

    @pytest.mark.asyncio
    async def test_get_logs_returns_empty_message_when_no_events(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)

        logs_mock = MagicMock()
        logs_mock.filter_log_events.return_value = {"events": []}

        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            logs = await deployer.get_logs("my-agent")

        assert len(logs) == 1
        assert "No logs found" in logs[0]

    @pytest.mark.asyncio
    async def test_get_logs_passes_since_filter(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)

        logs_mock = MagicMock()
        logs_mock.filter_log_events.return_value = {"events": []}

        since = datetime(2024, 1, 1)
        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            await deployer.get_logs("my-agent", since=since)

        call_kwargs = logs_mock.filter_log_events.call_args.kwargs
        assert "startTime" in call_kwargs
        assert call_kwargs["startTime"] == int(since.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Deployer registry
# ---------------------------------------------------------------------------


class TestDeployerRegistry:
    def test_app_runner_runtime_routes_to_app_runner_deployer(self) -> None:
        from engine.deployers import get_deployer
        from engine.deployers.aws_app_runner import AWSAppRunnerDeployer

        deployer = get_deployer(CloudType.aws, runtime="app-runner")
        assert isinstance(deployer, AWSAppRunnerDeployer)

    def test_apprunner_alias_routes_to_app_runner_deployer(self) -> None:
        from engine.deployers import get_deployer
        from engine.deployers.aws_app_runner import AWSAppRunnerDeployer

        deployer = get_deployer(CloudType.aws, runtime="apprunner")
        assert isinstance(deployer, AWSAppRunnerDeployer)

    def test_ecs_fargate_still_routes_to_ecs_deployer(self) -> None:
        from engine.deployers import get_deployer
        from engine.deployers.aws_ecs import AWSECSDeployer

        deployer = get_deployer(CloudType.aws, runtime="ecs-fargate")
        assert isinstance(deployer, AWSECSDeployer)

    def test_default_aws_cloud_routes_to_ecs_deployer(self) -> None:
        from engine.deployers import get_deployer
        from engine.deployers.aws_ecs import AWSECSDeployer

        deployer = get_deployer(CloudType.aws)
        assert isinstance(deployer, AWSECSDeployer)
