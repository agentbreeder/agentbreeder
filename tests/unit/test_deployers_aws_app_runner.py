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


# ---------------------------------------------------------------------------
# _get_boto3_client — lines 121-122
# ---------------------------------------------------------------------------


class TestGetBoto3Client:
    def test_raises_import_error_when_boto3_missing(self) -> None:
        deployer = _make_deployer()
        sys.modules["boto3"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(ImportError, match="pip install agentbreeder\\[aws\\]"):
                deployer._get_boto3_client("apprunner")
        finally:
            del sys.modules["boto3"]

    def test_uses_default_region_when_ar_config_not_set(self) -> None:
        """_get_boto3_client falls back to DEFAULT_REGION when _ar_config is None."""
        from engine.deployers.aws_app_runner import DEFAULT_REGION

        deployer = _make_deployer()
        assert deployer._ar_config is None

        mock_boto3 = MagicMock()
        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            deployer._get_boto3_client("apprunner")

        mock_boto3.client.assert_called_once_with("apprunner", region_name=DEFAULT_REGION)


# ---------------------------------------------------------------------------
# _ensure_ecr_repository — lines 173-174 (generic exception branch)
# ---------------------------------------------------------------------------


class TestEnsureEcrRepository:
    @pytest.mark.asyncio
    async def test_logs_warning_on_unexpected_exception(self) -> None:
        """Non-RepositoryNotFoundException errors are caught and logged — not re-raised."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)

        ecr_mock = MagicMock()
        ecr_mock.exceptions.RepositoryNotFoundException = type(
            "RepositoryNotFoundException", (Exception,), {}
        )
        # Simulate a generic unexpected error (e.g. network timeout)
        ecr_mock.describe_repositories.side_effect = RuntimeError("network timeout")

        with patch.object(deployer, "_get_boto3_client", return_value=ecr_mock):
            # Should NOT raise — must log warning and continue
            await deployer._ensure_ecr_repository("agentbreeder")

        ecr_mock.create_repository.assert_not_called()


# ---------------------------------------------------------------------------
# _push_image — lines 182-228
# ---------------------------------------------------------------------------


class TestPushImage:
    def _make_image(self):
        from pathlib import Path

        img = MagicMock()
        img.tag = "my-agent:1.0.0"
        img.context_dir = Path("/tmp/agent-context")
        return img

    @pytest.mark.asyncio
    async def test_raises_import_error_when_docker_sdk_missing(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)
        deployer._image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        image = self._make_image()
        with patch.dict("sys.modules", {"docker": None}):
            with pytest.raises(ImportError, match="pip install docker"):
                await deployer._push_image(image, deployer._image_uri)

    @pytest.mark.asyncio
    async def test_push_image_tags_and_pushes_to_ecr(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)
        image = self._make_image()
        image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        ecr_mock = MagicMock()
        import base64

        token = base64.b64encode(b"AWS:secret-token").decode()
        ecr_mock.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": token}]
        }

        mock_docker = MagicMock()
        mock_docker_client = MagicMock()
        mock_docker.from_env.return_value = mock_docker_client

        built_image = MagicMock()
        mock_docker_client.images.build.return_value = (built_image, iter([{"stream": "done\n"}]))
        # push returns iterable of status chunks (no errors)
        mock_docker_client.images.push.return_value = iter([{"status": "Pushed"}])

        with (
            patch.object(deployer, "_get_boto3_client", return_value=ecr_mock),
            patch.dict("sys.modules", {"docker": mock_docker}),
        ):
            await deployer._push_image(image, image_uri)

        built_image.tag.assert_called_once_with(image_uri)
        mock_docker_client.images.push.assert_called_once()

    @pytest.mark.asyncio
    async def test_push_image_raises_on_ecr_push_error(self) -> None:
        """An 'error' key in the push output stream raises RuntimeError."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)
        image = self._make_image()
        image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        ecr_mock = MagicMock()
        import base64

        token = base64.b64encode(b"AWS:secret-token").decode()
        ecr_mock.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": token}]
        }

        mock_docker = MagicMock()
        mock_docker_client = MagicMock()
        mock_docker.from_env.return_value = mock_docker_client

        built_image = MagicMock()
        mock_docker_client.images.build.return_value = (built_image, iter([]))
        # Push returns an error chunk
        mock_docker_client.images.push.return_value = iter([{"error": "denied: access denied"}])

        with (
            patch.object(deployer, "_get_boto3_client", return_value=ecr_mock),
            patch.dict("sys.modules", {"docker": mock_docker}),
            pytest.raises(RuntimeError, match="ECR image push failed"),
        ):
            await deployer._push_image(image, image_uri)


# ---------------------------------------------------------------------------
# _build_service_config — line 242 (access_role_arn branch)
# ---------------------------------------------------------------------------


class TestBuildServiceConfig:
    def test_includes_auth_config_when_access_role_arn_set(self) -> None:
        from engine.deployers.aws_app_runner import (
            _extract_app_runner_config,
        )

        deployer = _make_deployer()
        config = _make_agent_config(
            extra_env={
                "AWS_APP_RUNNER_ACCESS_ROLE_ARN": "arn:aws:iam::123:role/AppRunnerECRAccess"
            }
        )
        deployer._ar_config = _extract_app_runner_config(config)
        image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        svc_config = deployer._build_service_config(config, image_uri)
        image_repo = svc_config["SourceConfiguration"]["ImageRepository"]
        assert "AuthenticationConfiguration" in image_repo
        assert image_repo["AuthenticationConfiguration"]["AccessRoleArn"].startswith("arn:aws:")

    def test_excludes_auth_config_when_access_role_arn_not_set(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer = _make_deployer()
        config = _make_agent_config()
        deployer._ar_config = _extract_app_runner_config(config)
        image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        svc_config = deployer._build_service_config(config, image_uri)
        image_repo = svc_config["SourceConfiguration"]["ImageRepository"]
        assert "AuthenticationConfiguration" not in image_repo

    def test_normalises_cpu_and_memory(self) -> None:
        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer = _make_deployer()
        config = _make_agent_config()
        # Override resources to non-default values
        config.deploy.resources.cpu = "2"
        config.deploy.resources.memory = "4Gi"
        deployer._ar_config = _extract_app_runner_config(config)
        image_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/agentbreeder:1.0.0"

        svc_config = deployer._build_service_config(config, image_uri)
        instance = svc_config["InstanceConfiguration"]
        assert instance["Cpu"] == "2 vCPU"
        assert instance["Memory"] == "4 GB"


# ---------------------------------------------------------------------------
# _wait_for_service_running — lines 323-345
# ---------------------------------------------------------------------------


class TestWaitForServiceRunning:
    @pytest.mark.asyncio
    async def test_returns_url_when_service_is_immediately_running(self) -> None:
        deployer = _make_deployer()
        ar_mock = MagicMock()
        ar_mock.describe_service.return_value = {
            "Service": {
                "Status": "RUNNING",
                "ServiceUrl": "abc123.us-east-1.awsapprunner.com",
            }
        }

        url = await deployer._wait_for_service_running(ar_mock, "my-agent")
        assert url == "https://abc123.us-east-1.awsapprunner.com"

    @pytest.mark.asyncio
    async def test_raises_on_create_failed_status(self) -> None:
        deployer = _make_deployer()
        ar_mock = MagicMock()
        ar_mock.describe_service.return_value = {
            "Service": {"Status": "CREATE_FAILED", "ServiceUrl": ""}
        }

        with pytest.raises(RuntimeError, match="CREATE_FAILED"):
            await deployer._wait_for_service_running(ar_mock, "my-agent")

    @pytest.mark.asyncio
    async def test_raises_on_delete_failed_status(self) -> None:
        deployer = _make_deployer()
        ar_mock = MagicMock()
        ar_mock.describe_service.return_value = {
            "Service": {"Status": "DELETE_FAILED", "ServiceUrl": ""}
        }

        with pytest.raises(RuntimeError, match="DELETE_FAILED"):
            await deployer._wait_for_service_running(ar_mock, "my-agent")

    @pytest.mark.asyncio
    async def test_raises_on_paused_status(self) -> None:
        deployer = _make_deployer()
        ar_mock = MagicMock()
        ar_mock.describe_service.return_value = {"Service": {"Status": "PAUSED", "ServiceUrl": ""}}

        with pytest.raises(RuntimeError, match="PAUSED"):
            await deployer._wait_for_service_running(ar_mock, "my-agent")

    @pytest.mark.asyncio
    async def test_raises_timeout_error_after_max_attempts(self) -> None:
        from engine.deployers.aws_app_runner import WAITER_MAX_ATTEMPTS

        deployer = _make_deployer()
        ar_mock = MagicMock()
        # Keep returning a non-terminal non-running status
        ar_mock.describe_service.return_value = {
            "Service": {"Status": "OPERATION_IN_PROGRESS", "ServiceUrl": ""}
        }

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(TimeoutError, match="Timed out"),
        ):
            await deployer._wait_for_service_running(ar_mock, "my-agent")

        assert ar_mock.describe_service.call_count == WAITER_MAX_ATTEMPTS

    @pytest.mark.asyncio
    async def test_polls_until_running(self) -> None:
        deployer = _make_deployer()
        ar_mock = MagicMock()
        ar_mock.describe_service.side_effect = [
            {"Service": {"Status": "OPERATION_IN_PROGRESS", "ServiceUrl": ""}},
            {"Service": {"Status": "OPERATION_IN_PROGRESS", "ServiceUrl": ""}},
            {"Service": {"Status": "RUNNING", "ServiceUrl": "ready.us-east-1.awsapprunner.com"}},
        ]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            url = await deployer._wait_for_service_running(ar_mock, "my-agent")

        assert url == "https://ready.us-east-1.awsapprunner.com"
        assert ar_mock.describe_service.call_count == 3


# ---------------------------------------------------------------------------
# health_check — lines 354-369
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_healthy_on_sub_500_response(self) -> None:
        from engine.deployers.base import DeployResult

        deployer = _make_deployer()
        result = DeployResult(
            endpoint_url="https://abc123.us-east-1.awsapprunner.com",
            container_id="arn:aws:apprunner:::service/my-agent/abc",
            status="running",
            agent_name="my-agent",
            version="1.0.0",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            health = await deployer.health_check(result, timeout=5, interval=1)

        assert health.healthy is True
        assert health.checks["http_health"] is True

    @pytest.mark.asyncio
    async def test_returns_unhealthy_after_timeout(self) -> None:
        import httpx as httpx_mod

        from engine.deployers.base import DeployResult

        deployer = _make_deployer()
        result = DeployResult(
            endpoint_url="https://abc123.us-east-1.awsapprunner.com",
            container_id="arn:aws:apprunner:::service/my-agent/abc",
            status="running",
            agent_name="my-agent",
            version="1.0.0",
        )

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx_mod.ConnectError("refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            health = await deployer.health_check(result, timeout=3, interval=1)

        assert health.healthy is False
        assert health.checks["http_health"] is False

    @pytest.mark.asyncio
    async def test_retries_on_timeout_exception(self) -> None:
        import httpx as httpx_mod

        from engine.deployers.base import DeployResult

        deployer = _make_deployer()
        result = DeployResult(
            endpoint_url="https://abc123.us-east-1.awsapprunner.com",
            container_id="arn:aws:apprunner:::service/my-agent/abc",
            status="running",
            agent_name="my-agent",
            version="1.0.0",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.side_effect = [
            httpx_mod.TimeoutException("timeout"),
            mock_response,
        ]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            health = await deployer.health_check(result, timeout=5, interval=1)

        assert health.healthy is True


# ---------------------------------------------------------------------------
# get_url — lines 418-423
# ---------------------------------------------------------------------------


class TestGetUrl:
    @pytest.mark.asyncio
    async def test_get_url_raises_when_not_provisioned(self) -> None:
        deployer = _make_deployer()
        with pytest.raises(RuntimeError, match="Cannot get URL"):
            await deployer.get_url("my-agent")

    @pytest.mark.asyncio
    async def test_get_url_returns_https_url(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)

        ar_mock = MagicMock()
        ar_mock.describe_service.return_value = {
            "Service": {"ServiceUrl": "abc123.us-east-1.awsapprunner.com"}
        }

        with patch.object(deployer, "_get_boto3_client", return_value=ar_mock):
            url = await deployer.get_url("my-agent")

        assert url == "https://abc123.us-east-1.awsapprunner.com"


# ---------------------------------------------------------------------------
# status — lines 427-441
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_raises_when_not_provisioned(self) -> None:
        deployer = _make_deployer()
        with pytest.raises(RuntimeError, match="Cannot get status"):
            await deployer.status("my-agent")

    @pytest.mark.asyncio
    async def test_status_returns_dict_with_fields(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)

        ar_mock = MagicMock()
        ar_mock.describe_service.return_value = {
            "Service": {
                "Status": "RUNNING",
                "ServiceUrl": "abc123.us-east-1.awsapprunner.com",
                "ServiceArn": "arn:aws:apprunner:us-east-1:123:service/my-agent/abc",
            }
        }

        with patch.object(deployer, "_get_boto3_client", return_value=ar_mock):
            result = await deployer.status("my-agent")

        assert result["name"] == "my-agent"
        assert result["status"] == "RUNNING"
        assert result["url"] == "https://abc123.us-east-1.awsapprunner.com"
        assert "arn:aws:apprunner" in result["service_arn"]

    @pytest.mark.asyncio
    async def test_status_returns_not_found_on_exception(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)

        ar_mock = MagicMock()
        ar_mock.describe_service.side_effect = RuntimeError("service not found")

        with patch.object(deployer, "_get_boto3_client", return_value=ar_mock):
            result = await deployer.status("my-agent")

        assert result["name"] == "my-agent"
        assert result["status"] == "not_found"


# ---------------------------------------------------------------------------
# get_logs — lines 413-414 (with-since path already tested; cover error path)
# ---------------------------------------------------------------------------


class TestGetLogsAdditional:
    @pytest.mark.asyncio
    async def test_get_logs_returns_error_message_on_exception(self) -> None:
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)

        logs_mock = MagicMock()
        logs_mock.filter_log_events.side_effect = RuntimeError("permission denied")

        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            logs = await deployer.get_logs("my-agent")

        assert len(logs) == 1
        assert "Error fetching logs" in logs[0]
        assert "permission denied" in logs[0]

    @pytest.mark.asyncio
    async def test_get_logs_passes_since_filter_as_ms_timestamp(self) -> None:
        """When `since` is provided the startTime kwarg should be milliseconds."""
        deployer = _make_deployer()
        config = _make_agent_config()

        from engine.deployers.aws_app_runner import _extract_app_runner_config

        deployer._ar_config = _extract_app_runner_config(config)

        logs_mock = MagicMock()
        logs_mock.filter_log_events.return_value = {"events": []}

        since = datetime(2025, 6, 1, 0, 0, 0)
        with patch.object(deployer, "_get_boto3_client", return_value=logs_mock):
            await deployer.get_logs("my-agent", since=since)

        call_kwargs = logs_mock.filter_log_events.call_args.kwargs
        assert "startTime" in call_kwargs
        # timestamp in milliseconds should be a large positive integer
        assert call_kwargs["startTime"] == int(since.timestamp() * 1000)
