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


def _fast_health_clock():
    """Patch asyncio.sleep + engine.deployers._health.time.monotonic so
    poll_until_ready completes instantly while still honoring the deadline."""
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

        # #117: scaling keys must be snake_case
        assert template["scaling"]["min_instance_count"] == 0
        assert template["scaling"]["max_instance_count"] == 5

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
        # #117: must be snake_case
        assert template["service_account"] == "sa@proj.iam.gserviceaccount.com"

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
        # #117: must be snake_case
        assert template["vpc_access"]["connector"] == "projects/p/locations/r/connectors/c"

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
        # #117: must be snake_case
        assert template["execution_environment"] == "EXECUTION_ENVIRONMENT_GEN1"

    def test_template_has_health_probes(self) -> None:
        config = _make_config()
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")

        container = template["containers"][0]
        # #117: probe keys must be snake_case
        assert "startup_probe" in container
        assert container["startup_probe"]["http_get"]["path"] == "/health"
        assert "liveness_probe" in container
        assert container["liveness_probe"]["http_get"]["path"] == "/health"

    def test_template_container_port_is_snake_case(self) -> None:
        """#117: 'container_port' not 'containerPort'."""
        config = _make_config()
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        container = template["containers"][0]
        ports = container["ports"]
        assert len(ports) == 1
        assert ports[0].get("container_port") == 8080
        assert "containerPort" not in ports[0]

    def test_template_max_instance_request_concurrency_is_snake_case(self) -> None:
        """#117: top-level concurrency field must be snake_case."""
        config = _make_config()
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        assert "max_instance_request_concurrency" in template
        assert "maxInstanceRequestConcurrency" not in template

    def test_template_execution_environment_is_snake_case(self) -> None:
        """#117: execution_environment key must be snake_case."""
        config = _make_config()
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        assert "execution_environment" in template
        assert "executionEnvironment" not in template

    # -- #119: CPU minimum guard ------------------------------------------------

    def test_cpu_below_1_is_clamped_to_1000m(self) -> None:
        """#119: Cloud Run requires >= 1 vCPU when concurrency > 1."""
        config = _make_config(deploy={"resources": {"cpu": "0.5", "memory": "512Mi"}})
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        cpu_limit = template["containers"][0]["resources"]["limits"]["cpu"]
        assert cpu_limit == "1000m"

    def test_cpu_in_millicores_below_1000m_is_clamped(self) -> None:
        """#119: 500m expressed as millicores should also be clamped."""
        config = _make_config(deploy={"resources": {"cpu": "500m", "memory": "512Mi"}})
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        cpu_limit = template["containers"][0]["resources"]["limits"]["cpu"]
        assert cpu_limit == "1000m"

    def test_cpu_at_or_above_1_is_not_clamped(self) -> None:
        """#119: CPU >= 1 should not be modified."""
        config = _make_config(deploy={"resources": {"cpu": "2", "memory": "1Gi"}})
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        cpu_limit = template["containers"][0]["resources"]["limits"]["cpu"]
        assert cpu_limit == "2"

    # -- #120: secret:// env vars -----------------------------------------------

    def test_secret_ref_env_var_uses_secret_key_ref(self) -> None:
        """#120: secret://FOO should become a SecretKeyRef, not a literal value."""
        config = _make_config(
            deploy={
                "env_vars": {
                    "GCP_PROJECT_ID": "my-project-123",
                    "OPENAI_API_KEY": "secret://OPENAI_API_KEY",
                },
            }
        )
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        container = template["containers"][0]

        secret_entries = [e for e in container["env"] if e["name"] == "OPENAI_API_KEY"]
        assert len(secret_entries) == 1
        entry = secret_entries[0]
        assert "value" not in entry
        assert "value_source" in entry
        ref = entry["value_source"]["secret_key_ref"]
        assert ref["secret"].endswith("/secrets/OPENAI_API_KEY")
        assert ref["version"] == "latest"

    def test_plain_env_var_uses_value_not_secret_key_ref(self) -> None:
        """#120: non-secret:// values should stay as plain 'value' fields."""
        config = _make_config(
            deploy={
                "env_vars": {
                    "GCP_PROJECT_ID": "my-project-123",
                    "LOG_LEVEL": "debug",
                },
            }
        )
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        container = template["containers"][0]

        log_entries = [e for e in container["env"] if e["name"] == "LOG_LEVEL"]
        assert len(log_entries) == 1
        assert log_entries[0]["value"] == "debug"
        assert "value_source" not in log_entries[0]


# ---------------------------------------------------------------------------
# Track J — sidecar ingress routing (#203)
# ---------------------------------------------------------------------------


class TestSidecarIngressRouting:
    """When the sidecar is injected it must front external traffic: Cloud Run
    routes inbound to the sidecar (:8080), which proxies to the agent (:8081).
    The agent must never be the ingress container.
    """

    def _injected_template(self) -> dict:
        config = _make_config(guardrails=["pii_detection"])
        gcp = _extract_cloudrun_config(config)
        return _build_service_template(config, gcp, "img:1.0.0")

    def _agent_container(self, template: dict) -> dict:
        return next(c for c in template["containers"] if c.get("name") != "agentbreeder-sidecar")

    def _sidecar_container(self, template: dict) -> dict:
        return next(c for c in template["containers"] if c.get("name") == "agentbreeder-sidecar")

    def test_sidecar_is_the_ingress_container(self) -> None:
        template = self._injected_template()
        assert len(template["containers"]) == 2
        sidecar = self._sidecar_container(template)
        agent = self._agent_container(template)
        # Exactly one container declares the ingress port — it must be the sidecar.
        assert sidecar["ports"] == [{"container_port": 8080}]
        assert "ports" not in agent

    def test_agent_listens_on_8081_with_matching_probes(self) -> None:
        template = self._injected_template()
        agent = self._agent_container(template)
        env = {e["name"]: e.get("value") for e in agent["env"] if "value" in e}
        assert env["PORT"] == "8081"
        assert agent["startup_probe"]["http_get"]["port"] == 8081
        assert agent["liveness_probe"]["http_get"]["port"] == 8081

    def test_sidecar_proxies_to_agent_internal_port(self) -> None:
        template = self._injected_template()
        sidecar = self._sidecar_container(template)
        env = {e["name"]: e.get("value") for e in sidecar["env"]}
        assert env["AGENTBREEDER_SIDECAR_AGENT_URL"] == "http://localhost:8081"
        assert env["AGENTBREEDER_SIDECAR_INBOUND_ADDR"] == ":8080"

    def test_no_sidecar_keeps_agent_as_ingress_on_8080(self) -> None:
        config = _make_config()  # no guardrails → no sidecar
        gcp = _extract_cloudrun_config(config)
        template = _build_service_template(config, gcp, "img:1.0.0")
        assert len(template["containers"]) == 1
        agent = template["containers"][0]
        assert agent["ports"] == [{"container_port": 8080}]
        # Cloud Run injects PORT for the ingress container — we must not override it.
        assert all(e["name"] != "PORT" for e in agent["env"])


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
            _fast_health_clock(),
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
            _fast_health_clock(),
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
        # docker_client() may return docker.from_env() or docker.DockerClient(...)
        mock_docker.from_env.return_value = mock_client
        mock_docker.DockerClient.return_value = mock_client

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
        # docker_client() may return docker.from_env() or docker.DockerClient(...)
        mock_docker.from_env.return_value = mock_client
        mock_docker.DockerClient.return_value = mock_client

        with (
            patch.dict("sys.modules", {"docker": mock_docker}),
            pytest.raises(RuntimeError, match="Image push failed"),
        ):
            await deployer._push_image(image, image_uri)
