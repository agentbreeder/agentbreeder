"""Unit tests for engine/deployers/gcp_cloudrun.py.

All GCP API calls are mocked — no real cloud resources are touched.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config_parser import AgentConfig, CloudType, FrameworkType, Visibility
from engine.deployers.base import DeployResult
from engine.deployers.gcp_cloudrun import (
    DEFAULT_REGION,
    CloudRunConfig,
    GCPCloudRunDeployer,
    _build_service_template,
    _extract_cloudrun_config,
    _get_artifact_registry_image_uri,
)
from engine.runtimes.base import ContainerImage

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Build a minimal AgentConfig for GCP Cloud Run tests."""
    defaults: dict = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "platform",
        "owner": "alice@example.com",
        "framework": FrameworkType.langgraph,
        "model": {"primary": "claude-sonnet-4"},
        "deploy": {
            "cloud": "gcp",
            "region": "us-central1",
            "env_vars": {
                "GCP_PROJECT_ID": "my-project-123",
            },
            "scaling": {"min": 0, "max": 5},
            "resources": {"cpu": "2", "memory": "1Gi"},
        },
    }
    # Deep merge deploy overrides
    if "deploy" in overrides:
        defaults["deploy"].update(overrides.pop("deploy"))
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_image() -> ContainerImage:
    d = Path(tempfile.mkdtemp())
    (d / "Dockerfile").write_text("FROM python:3.11-slim")
    return ContainerImage(
        tag="agentbreeder/test-agent:1.0.0",
        dockerfile_content="FROM python:3.11-slim",
        context_dir=d,
    )


# ---------------------------------------------------------------------------
# _extract_cloudrun_config
# ---------------------------------------------------------------------------


class TestExtractCloudRunConfig:
    def test_extracts_project_id(self) -> None:
        config = _make_config()
        gcp = _extract_cloudrun_config(config)
        assert gcp.project_id == "my-project-123"

    def test_extracts_region(self) -> None:
        config = _make_config()
        gcp = _extract_cloudrun_config(config)
        assert gcp.region == "us-central1"

    def test_defaults_region_when_not_set(self) -> None:
        config = _make_config(deploy={"region": None})
        # region=None in deploy should fall back to DEFAULT_REGION
        gcp = _extract_cloudrun_config(config)
        assert gcp.region == DEFAULT_REGION

    def test_raises_without_project_id(self) -> None:
        config = _make_config(deploy={"env_vars": {}})
        with pytest.raises(ValueError, match="GCP project ID is required"):
            _extract_cloudrun_config(config)

    def test_accepts_google_cloud_project_env_var(self) -> None:
        config = _make_config(deploy={"env_vars": {"GOOGLE_CLOUD_PROJECT": "alt-project"}})
        gcp = _extract_cloudrun_config(config)
        assert gcp.project_id == "alt-project"

    def test_extracts_optional_settings(self) -> None:
        config = _make_config(
            deploy={
                "env_vars": {
                    "GCP_PROJECT_ID": "my-project-123",
                    "GCP_SERVICE_ACCOUNT": "sa@my-project.iam.gserviceaccount.com",
                    "GCP_ARTIFACT_REGISTRY_REPO": "custom-repo",
                    "GCP_VPC_CONNECTOR": "projects/p/locations/r/connectors/c",
                    "GCP_INGRESS": "internal",
                    "GCP_CPU_THROTTLING": "false",
                    "GCP_STARTUP_CPU_BOOST": "false",
                    "GCP_EXECUTION_ENVIRONMENT": "gen1",
                    "GCP_TIMEOUT_SECONDS": "600",
                    "GCP_CONCURRENCY": "100",
                },
            }
        )
        gcp = _extract_cloudrun_config(config)
        assert gcp.service_account == "sa@my-project.iam.gserviceaccount.com"
        assert gcp.artifact_registry_repo == "custom-repo"
        assert gcp.vpc_connector == "projects/p/locations/r/connectors/c"
        assert gcp.ingress == "internal"
        assert gcp.cpu_throttling is False
        assert gcp.startup_cpu_boost is False
        assert gcp.execution_environment == "gen1"
        assert gcp.timeout_seconds == 600
        assert gcp.concurrency == 100


# ---------------------------------------------------------------------------
# _get_artifact_registry_image_uri
# ---------------------------------------------------------------------------


class TestGetArtifactRegistryImageUri:
    def test_default_repo_name(self) -> None:
        gcp = CloudRunConfig(project_id="proj", region="us-east1")
        uri = _get_artifact_registry_image_uri(gcp, "my-agent", "2.1.0")
        assert uri == "us-east1-docker.pkg.dev/proj/agentbreeder/my-agent:2.1.0"

    def test_custom_repo_name(self) -> None:
        gcp = CloudRunConfig(
            project_id="proj", region="europe-west1", artifact_registry_repo="custom"
        )
        uri = _get_artifact_registry_image_uri(gcp, "agent-x", "0.1.0")
        assert uri == "europe-west1-docker.pkg.dev/proj/custom/agent-x:0.1.0"


# ---------------------------------------------------------------------------
# _build_service_template
# ---------------------------------------------------------------------------


class TestBuildServiceTemplate:
    def test_template_has_container(self) -> None:
        config = _make_config()
        gcp = _extract_cloudrun_config(config)
        image_uri = "us-central1-docker.pkg.dev/proj/repo/img:1.0.0"
        template = _build_service_template(config, gcp, image_uri)

        assert len(template["containers"]) == 1
        container = template["containers"][0]
        assert container["image"] == image_uri
        assert container["resources"]["limits"]["cpu"] == "2"
        assert container["resources"]["limits"]["memory"] == "1Gi"

    def test_template_has_scaling(self) -> None:
        config = _make_config()
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")

        assert template["scaling"]["minInstanceCount"] == 0
        assert template["scaling"]["maxInstanceCount"] == 5

    def test_template_env_vars_exclude_gcp_prefixed(self) -> None:
        config = _make_config(
            deploy={
                "env_vars": {
                    "GCP_PROJECT_ID": "proj",
                    "LOG_LEVEL": "debug",
                    "CUSTOM_VAR": "value",
                },
            }
        )
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")

        container = template["containers"][0]
        env_names = {e["name"] for e in container["env"]}
        assert "LOG_LEVEL" in env_names
        assert "CUSTOM_VAR" in env_names
        assert "GCP_PROJECT_ID" not in env_names
        # Built-in vars are always present
        assert "AGENT_NAME" in env_names
        assert "AGENT_VERSION" in env_names

    def test_template_has_service_account(self) -> None:
        config = _make_config(
            deploy={
                "env_vars": {
                    "GCP_PROJECT_ID": "proj",
                    "GCP_SERVICE_ACCOUNT": "sa@proj.iam.gserviceaccount.com",
                },
            }
        )
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        assert template["serviceAccount"] == "sa@proj.iam.gserviceaccount.com"

    def test_template_has_vpc_connector(self) -> None:
        config = _make_config(
            deploy={
                "env_vars": {
                    "GCP_PROJECT_ID": "proj",
                    "GCP_VPC_CONNECTOR": "projects/p/locations/r/connectors/c",
                },
            }
        )
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        assert template["vpcAccess"]["connector"] == "projects/p/locations/r/connectors/c"

    def test_template_gen1_execution_environment(self) -> None:
        config = _make_config(
            deploy={
                "env_vars": {
                    "GCP_PROJECT_ID": "proj",
                    "GCP_EXECUTION_ENVIRONMENT": "gen1",
                },
            }
        )
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        assert template["executionEnvironment"] == "EXECUTION_ENVIRONMENT_GEN1"

    def test_template_has_health_probes(self) -> None:
        config = _make_config()
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")

        container = template["containers"][0]
        assert "startupProbe" in container
        assert container["startupProbe"]["httpGet"]["path"] == "/health"
        assert "livenessProbe" in container
        assert container["livenessProbe"]["httpGet"]["path"] == "/health"


# ---------------------------------------------------------------------------
# GCPCloudRunDeployer.provision
# ---------------------------------------------------------------------------


class TestProvision:
    @pytest.mark.asyncio
    async def test_provision_returns_infra_result(self) -> None:
        deployer = GCPCloudRunDeployer()
        config = _make_config()

        with patch.object(deployer, "_ensure_artifact_registry_repo", new_callable=AsyncMock):
            result = await deployer.provision(config)

        assert "my-project-123" in result.resource_ids["project_id"]
        assert "us-central1" in result.resource_ids["region"]
        assert result.resource_ids["image_uri"].startswith("us-central1-docker.pkg.dev/")
        assert "test-agent" in result.endpoint_url

    @pytest.mark.asyncio
    async def test_provision_raises_without_project_id(self) -> None:
        deployer = GCPCloudRunDeployer()
        config = _make_config(deploy={"env_vars": {}})

        with pytest.raises(ValueError, match="GCP project ID is required"):
            await deployer.provision(config)


# ---------------------------------------------------------------------------
# GCPCloudRunDeployer.deploy
# ---------------------------------------------------------------------------


class TestDeploy:
    @pytest.mark.asyncio
    async def test_deploy_pushes_image_and_creates_service(self) -> None:
        deployer = GCPCloudRunDeployer()
        config = _make_config()
        image = _make_image()

        # Pre-provision
        with patch.object(deployer, "_ensure_artifact_registry_repo", new_callable=AsyncMock):
            await deployer.provision(config)

        # Mock image push and service creation
        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock) as mock_push,
            patch.object(
                deployer,
                "_create_or_update_service",
                new_callable=AsyncMock,
                return_value="https://test-agent-abc123-uc.a.run.app",
            ) as mock_create,
            patch.object(deployer, "_allow_unauthenticated", new_callable=AsyncMock),
        ):
            result = await deployer.deploy(config, image)

        assert result.agent_name == "test-agent"
        assert result.version == "1.0.0"
        assert result.status == "running"
        assert "run.app" in result.endpoint_url
        mock_push.assert_called_once()
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_sets_public_access_for_public_visibility(self) -> None:
        deployer = GCPCloudRunDeployer()
        config = _make_config()
        config.access.visibility = Visibility.public
        image = _make_image()

        with patch.object(deployer, "_ensure_artifact_registry_repo", new_callable=AsyncMock):
            await deployer.provision(config)

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock),
            patch.object(
                deployer,
                "_create_or_update_service",
                new_callable=AsyncMock,
                return_value="https://test-agent.a.run.app",
            ),
            patch.object(deployer, "_allow_unauthenticated", new_callable=AsyncMock) as mock_iam,
        ):
            await deployer.deploy(config, image)

        mock_iam.assert_called_once_with("test-agent", deployer._gcp_config)

    @pytest.mark.asyncio
    async def test_deploy_skips_public_access_for_team_visibility(self) -> None:
        deployer = GCPCloudRunDeployer()
        config = _make_config()
        # Default visibility is "team"
        image = _make_image()

        with patch.object(deployer, "_ensure_artifact_registry_repo", new_callable=AsyncMock):
            await deployer.provision(config)

        with (
            patch.object(deployer, "_push_image", new_callable=AsyncMock),
            patch.object(
                deployer,
                "_create_or_update_service",
                new_callable=AsyncMock,
                return_value="https://test-agent.a.run.app",
            ),
            patch.object(deployer, "_allow_unauthenticated", new_callable=AsyncMock) as mock_iam,
        ):
            await deployer.deploy(config, image)

        mock_iam.assert_not_called()


# ---------------------------------------------------------------------------
# GCPCloudRunDeployer.health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_passes(self) -> None:
        deployer = GCPCloudRunDeployer()
        result = DeployResult(
            endpoint_url="https://test-agent.a.run.app",
            container_id="img:1.0.0",
            status="running",
            agent_name="test-agent",
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
        assert health.checks["reachable"] is True
        assert health.checks["healthy"] is True

    @pytest.mark.asyncio
    async def test_health_check_fails_after_timeout(self) -> None:
        deployer = GCPCloudRunDeployer()
        result = DeployResult(
            endpoint_url="https://test-agent.a.run.app",
            container_id="img:1.0.0",
            status="running",
            agent_name="test-agent",
            version="1.0.0",
        )

        import httpx as httpx_mod

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx_mod.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            health = await deployer.health_check(result, timeout=3, interval=1)

        assert health.healthy is False
        assert health.checks["reachable"] is False

    @pytest.mark.asyncio
    async def test_health_check_retries_on_non_200(self) -> None:
        deployer = GCPCloudRunDeployer()
        result = DeployResult(
            endpoint_url="https://test-agent.a.run.app",
            container_id="img:1.0.0",
            status="running",
            agent_name="test-agent",
            version="1.0.0",
        )

        mock_response_503 = MagicMock()
        mock_response_503.status_code = 503
        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.side_effect = [mock_response_503, mock_response_200]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            health = await deployer.health_check(result, timeout=4, interval=1)

        assert health.healthy is True


# ---------------------------------------------------------------------------
# GCPCloudRunDeployer.teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    @pytest.mark.asyncio
    async def test_teardown_deletes_service(self) -> None:
        deployer = GCPCloudRunDeployer()
        config = _make_config()

        with patch.object(deployer, "_ensure_artifact_registry_repo", new_callable=AsyncMock):
            await deployer.provision(config)

        mock_operation = AsyncMock()
        mock_operation.result = AsyncMock(return_value=None)

        mock_run_client = AsyncMock()
        mock_run_client.delete_service = AsyncMock(return_value=mock_operation)

        with patch.object(deployer, "_get_run_client", return_value=mock_run_client):
            await deployer.teardown("test-agent")

        mock_run_client.delete_service.assert_called_once()
        call_args = mock_run_client.delete_service.call_args
        request = call_args.kwargs.get("request")
        assert "test-agent" in request.name

    @pytest.mark.asyncio
    async def test_teardown_raises_without_gcp_config(self) -> None:
        deployer = GCPCloudRunDeployer()
        with pytest.raises(RuntimeError, match="Cannot teardown without GCP config"):
            await deployer.teardown("test-agent")

    @pytest.mark.asyncio
    async def test_teardown_propagates_api_errors(self) -> None:
        deployer = GCPCloudRunDeployer()
        config = _make_config()

        with patch.object(deployer, "_ensure_artifact_registry_repo", new_callable=AsyncMock):
            await deployer.provision(config)

        mock_run_client = AsyncMock()
        mock_run_client.delete_service = AsyncMock(side_effect=Exception("Service not found"))

        with (
            patch.object(deployer, "_get_run_client", return_value=mock_run_client),
            pytest.raises(Exception, match="Service not found"),
        ):
            await deployer.teardown("test-agent")


# ---------------------------------------------------------------------------
# GCPCloudRunDeployer.get_logs
# ---------------------------------------------------------------------------


class TestGetLogs:
    @pytest.mark.asyncio
    async def test_get_logs_returns_message_when_no_config(self) -> None:
        deployer = GCPCloudRunDeployer()
        logs = await deployer.get_logs("test-agent")
        assert len(logs) == 1
        assert "not initialized" in logs[0].lower()

    @pytest.mark.asyncio
    async def test_get_logs_returns_install_message_when_sdk_missing(self) -> None:
        deployer = GCPCloudRunDeployer()
        config = _make_config()

        with patch.object(deployer, "_ensure_artifact_registry_repo", new_callable=AsyncMock):
            await deployer.provision(config)

        with patch.dict("sys.modules", {"google.cloud": None, "google.cloud.logging": None}):
            # Force ImportError by patching the import
            has_attr = hasattr(__builtins__, "__import__")
            original_import = __builtins__.__import__ if has_attr else __import__

            def mock_import(name, *args, **kwargs):
                if "google.cloud.logging" in name or name == "google.cloud.logging":
                    raise ImportError("No module named 'google.cloud.logging'")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                logs = await deployer.get_logs("test-agent")

        assert len(logs) == 1
        assert "not installed" in logs[0].lower() or "error" in logs[0].lower()


# ---------------------------------------------------------------------------
# Deployer registry integration
# ---------------------------------------------------------------------------


class TestDeployerRegistry:
    def test_gcp_cloud_type_returns_cloudrun_deployer(self) -> None:
        from engine.deployers import get_deployer

        deployer = get_deployer(CloudType.gcp)
        assert isinstance(deployer, GCPCloudRunDeployer)

    def test_cloud_run_runtime_returns_cloudrun_deployer(self) -> None:
        from engine.deployers import get_deployer

        deployer = get_deployer(CloudType.gcp, runtime="cloud-run")
        assert isinstance(deployer, GCPCloudRunDeployer)

    def test_cloudrun_runtime_alias(self) -> None:
        from engine.deployers import get_deployer

        deployer = get_deployer(CloudType.gcp, runtime="cloudrun")
        assert isinstance(deployer, GCPCloudRunDeployer)


# ---------------------------------------------------------------------------
# Push image
# ---------------------------------------------------------------------------


class TestPushImage:
    @pytest.mark.asyncio
    async def test_push_image_builds_tags_and_pushes(self) -> None:
        deployer = GCPCloudRunDeployer()
        image = _make_image()
        image_uri = "us-central1-docker.pkg.dev/proj/repo/test-agent:1.0.0"

        mock_built_image = MagicMock()
        mock_client = MagicMock()
        mock_client.images.build.return_value = (mock_built_image, [{"stream": "Building..."}])
        mock_client.images.push.return_value = [{"status": "Pushed"}]

        mock_docker = MagicMock()
        mock_docker.from_env.return_value = mock_client

        with patch.dict("sys.modules", {"docker": mock_docker}):
            await deployer._push_image(image, image_uri)

        mock_client.images.build.assert_called_once()
        mock_built_image.tag.assert_called_once_with(image_uri)
        mock_client.images.push.assert_called_once_with(image_uri, stream=True, decode=True)

    @pytest.mark.asyncio
    async def test_push_image_raises_on_push_error(self) -> None:
        deployer = GCPCloudRunDeployer()
        image = _make_image()
        image_uri = "us-central1-docker.pkg.dev/proj/repo/test-agent:1.0.0"

        mock_built_image = MagicMock()
        mock_client = MagicMock()
        mock_client.images.build.return_value = (mock_built_image, [])
        mock_client.images.push.return_value = [{"error": "access denied"}]

        mock_docker = MagicMock()
        mock_docker.from_env.return_value = mock_client

        with (
            patch.dict("sys.modules", {"docker": mock_docker}),
            pytest.raises(RuntimeError, match="Image push failed"),
        ):
            await deployer._push_image(image, image_uri)
