"""Unit tests for the Azure Container Apps deployer.

All Azure SDK and Docker SDK calls are mocked — no real cloud access required.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

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
from engine.deployers.azure_container_apps import (
    DEFAULT_LOCATION,
    AzureConfig,
    AzureContainerAppsDeployer,
    _extract_azure_config,
    _get_acr_image_uri,
)
from engine.deployers.base import DeployResult, InfraResult
from engine.runtimes.base import ContainerImage

# ---------------------------------------------------------------------------
# Helpers
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


def _make_agent_config(
    name: str = "my-agent",
    version: str = "1.0.0",
    env_vars: dict[str, str] | None = None,
    cpu: str = "0.5",
    memory: str = "1Gi",
    min_replicas: int = 0,
    max_replicas: int = 5,
) -> AgentConfig:
    """Build a minimal AgentConfig suitable for Azure deployment."""
    base_env: dict[str, str] = {
        "AZURE_SUBSCRIPTION_ID": "sub-1234",
        "AZURE_RESOURCE_GROUP": "rg-agents",
        "AZURE_CONTAINER_APPS_ENV": "aca-env-prod",
        "AZURE_REGISTRY_SERVER": "myregistry.azurecr.io",
        "AZURE_LOCATION": "eastus",
    }
    if env_vars:
        base_env.update(env_vars)

    return AgentConfig(
        name=name,
        version=version,
        team="engineering",
        owner="dev@example.com",
        framework=FrameworkType.langgraph,
        model=ModelConfig(primary="claude-sonnet-4"),
        deploy=DeployConfig(
            cloud=CloudType.aws,  # CloudType doesn't have azure yet — uses aws as placeholder
            region="eastus",
            env_vars=base_env,
            resources=ResourceConfig(cpu=cpu, memory=memory),
            scaling=ScalingConfig(min=min_replicas, max=max_replicas),
        ),
        access=AccessConfig(),
    )


def _make_container_image(name: str = "my-agent", tag: str = "1.0.0") -> ContainerImage:
    return ContainerImage(
        tag=f"{name}:{tag}",
        context_dir=Path("/tmp/agent-build"),
        dockerfile_content='FROM python:3.11-slim\nCMD ["uvicorn", "main:app"]',
    )


def _make_deploy_result(
    endpoint_url: str = "https://my-agent.eastus.azurecontainerapps.io",
) -> DeployResult:
    return DeployResult(
        endpoint_url=endpoint_url,
        container_id="myregistry.azurecr.io/my-agent:1.0.0",
        status="running",
        agent_name="my-agent",
        version="1.0.0",
    )


# ---------------------------------------------------------------------------
# Tests: _extract_azure_config
# ---------------------------------------------------------------------------


class TestExtractAzureConfig:
    def test_happy_path_extracts_all_fields(self) -> None:
        config = _make_agent_config(
            env_vars={
                "AZURE_REGISTRY_USERNAME": "myuser",
                "AZURE_REGISTRY_PASSWORD": "s3cr3t",
            }
        )
        azure = _extract_azure_config(config)

        assert azure.subscription_id == "sub-1234"
        assert azure.resource_group == "rg-agents"
        assert azure.container_apps_env == "aca-env-prod"
        assert azure.registry_server == "myregistry.azurecr.io"
        assert azure.location == "eastus"
        assert azure.registry_username == "myuser"
        assert azure.registry_password == "s3cr3t"

    def test_defaults_location_from_deploy_region_when_azure_location_absent(self) -> None:
        config = _make_agent_config()
        # Remove AZURE_LOCATION so it falls back to deploy.region
        del config.deploy.env_vars["AZURE_LOCATION"]
        azure = _extract_azure_config(config)
        assert azure.location == "eastus"  # from deploy.region

    def test_defaults_location_to_eastus_when_nothing_set(self) -> None:
        config = _make_agent_config()
        del config.deploy.env_vars["AZURE_LOCATION"]
        config.deploy.region = None
        azure = _extract_azure_config(config)
        assert azure.location == DEFAULT_LOCATION

    def test_registry_credentials_are_optional(self) -> None:
        config = _make_agent_config()
        azure = _extract_azure_config(config)
        assert azure.registry_username is None
        assert azure.registry_password is None

    def test_raises_when_subscription_id_missing(self) -> None:
        config = _make_agent_config()
        del config.deploy.env_vars["AZURE_SUBSCRIPTION_ID"]

        with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID"):
            _extract_azure_config(config)

    def test_raises_when_resource_group_missing(self) -> None:
        config = _make_agent_config()
        del config.deploy.env_vars["AZURE_RESOURCE_GROUP"]

        with pytest.raises(ValueError, match="AZURE_RESOURCE_GROUP"):
            _extract_azure_config(config)

    def test_raises_when_container_apps_env_missing(self) -> None:
        config = _make_agent_config()
        del config.deploy.env_vars["AZURE_CONTAINER_APPS_ENV"]

        with pytest.raises(ValueError, match="AZURE_CONTAINER_APPS_ENV"):
            _extract_azure_config(config)

    def test_raises_when_registry_server_missing(self) -> None:
        config = _make_agent_config()
        del config.deploy.env_vars["AZURE_REGISTRY_SERVER"]

        with pytest.raises(ValueError, match="AZURE_REGISTRY_SERVER"):
            _extract_azure_config(config)


# ---------------------------------------------------------------------------
# Tests: _get_acr_image_uri
# ---------------------------------------------------------------------------


class TestGetAcrImageUri:
    def test_constructs_correct_uri(self) -> None:
        azure = AzureConfig(
            subscription_id="sub-1234",
            resource_group="rg-agents",
            container_apps_env="aca-env-prod",
            registry_server="myregistry.azurecr.io",
        )
        uri = _get_acr_image_uri(azure, "my-agent", "2.3.1")
        assert uri == "myregistry.azurecr.io/my-agent:2.3.1"


# ---------------------------------------------------------------------------
# Tests: AzureContainerAppsDeployer._get_credential
# ---------------------------------------------------------------------------


class TestGetCredential:
    def test_returns_credential_when_sdk_installed(self) -> None:
        deployer = AzureContainerAppsDeployer()
        mock_cred = MagicMock()

        with patch.dict(
            sys.modules,
            {"azure.identity": MagicMock(DefaultAzureCredential=Mock(return_value=mock_cred))},
        ):
            cred = deployer._get_credential()
            assert cred is mock_cred

    def test_raises_import_error_with_pip_hint_when_sdk_missing(self) -> None:
        deployer = AzureContainerAppsDeployer()

        with patch.dict(sys.modules, {"azure.identity": None}):
            with pytest.raises(ImportError, match="pip install agentbreeder\\[azure\\]"):
                deployer._get_credential()


# ---------------------------------------------------------------------------
# Tests: AzureContainerAppsDeployer._get_aca_client
# ---------------------------------------------------------------------------


class TestGetAcaClient:
    def test_raises_import_error_with_pip_hint_when_sdk_missing(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deployer._azure_config = AzureConfig(
            subscription_id="sub-1234",
            resource_group="rg-agents",
            container_apps_env="aca-env",
            registry_server="myregistry.azurecr.io",
        )

        with patch.dict(sys.modules, {"azure.mgmt.appcontainers": None}):
            with pytest.raises(ImportError, match="pip install agentbreeder\\[azure\\]"):
                deployer._get_aca_client()

    def test_raises_runtime_error_when_config_not_initialized(self) -> None:
        deployer = AzureContainerAppsDeployer()
        mock_module = MagicMock()

        with patch.dict(sys.modules, {"azure.mgmt.appcontainers": mock_module}):
            with pytest.raises(RuntimeError, match="Azure config not initialized"):
                deployer._get_aca_client()


# ---------------------------------------------------------------------------
# Tests: provision()
# ---------------------------------------------------------------------------


class TestProvision:
    @pytest.mark.asyncio
    async def test_raises_value_error_when_subscription_id_missing(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config()
        del config.deploy.env_vars["AZURE_SUBSCRIPTION_ID"]

        with pytest.raises(ValueError, match="AZURE_SUBSCRIPTION_ID"):
            await deployer.provision(config)

    @pytest.mark.asyncio
    async def test_raises_value_error_when_resource_group_missing(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config()
        del config.deploy.env_vars["AZURE_RESOURCE_GROUP"]

        with pytest.raises(ValueError, match="AZURE_RESOURCE_GROUP"):
            await deployer.provision(config)

    @pytest.mark.asyncio
    async def test_raises_value_error_when_container_apps_env_missing(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config()
        del config.deploy.env_vars["AZURE_CONTAINER_APPS_ENV"]

        with pytest.raises(ValueError, match="AZURE_CONTAINER_APPS_ENV"):
            await deployer.provision(config)

    @pytest.mark.asyncio
    async def test_raises_value_error_when_registry_server_missing(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config()
        del config.deploy.env_vars["AZURE_REGISTRY_SERVER"]

        with pytest.raises(ValueError, match="AZURE_REGISTRY_SERVER"):
            await deployer.provision(config)

    @pytest.mark.asyncio
    async def test_returns_infra_result_on_success(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config()

        # Mock env validation to succeed silently (SDK not installed path)
        with patch.object(
            deployer,
            "_validate_environment_exists",
            return_value=None,
        ):
            result = await deployer.provision(config)

        assert isinstance(result, InfraResult)
        assert "my-agent" in result.endpoint_url
        assert result.resource_ids["subscription_id"] == "sub-1234"
        assert result.resource_ids["resource_group"] == "rg-agents"
        assert result.resource_ids["container_apps_env"] == "aca-env-prod"
        assert "myregistry.azurecr.io/my-agent:1.0.0" in result.resource_ids["image_uri"]

    @pytest.mark.asyncio
    async def test_validate_environment_raises_when_env_not_found(self) -> None:
        deployer = AzureContainerAppsDeployer()

        mock_aca_client = MagicMock()
        mock_aca_client.managed_environments.get.side_effect = Exception(
            "ResourceNotFound: Environment not found (404)"
        )

        with patch.object(deployer, "_get_aca_client", return_value=mock_aca_client):
            with pytest.raises(ValueError, match="not found"):
                await deployer._validate_environment_exists(
                    deployer._azure_config
                    or AzureConfig(
                        subscription_id="sub-1234",
                        resource_group="rg-agents",
                        container_apps_env="aca-env-prod",
                        registry_server="myregistry.azurecr.io",
                    )
                )

    @pytest.mark.asyncio
    async def test_validate_environment_warns_when_sdk_missing(self) -> None:
        deployer = AzureContainerAppsDeployer()
        azure_cfg = AzureConfig(
            subscription_id="sub-1234",
            resource_group="rg-agents",
            container_apps_env="aca-env-prod",
            registry_server="myregistry.azurecr.io",
        )

        with patch.object(
            deployer,
            "_get_aca_client",
            side_effect=ImportError("pip install agentbreeder[azure]"),
        ):
            # Should NOT raise — just log a warning
            await deployer._validate_environment_exists(azure_cfg)


# ---------------------------------------------------------------------------
# Tests: deploy()
# ---------------------------------------------------------------------------


class TestDeploy:
    @pytest.mark.asyncio
    async def test_push_image_called_with_correct_acr_uri(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config()
        image = _make_container_image()

        pushed_uris: list[str] = []

        async def mock_push_image(img: ContainerImage, uri: str) -> None:
            pushed_uris.append(uri)

        with (
            patch.object(deployer, "_push_image", side_effect=mock_push_image),
            patch.object(
                deployer,
                "_get_managed_environment_id",
                return_value="/subscriptions/sub-1234/resourceGroups/rg-agents/providers/Microsoft.App/managedEnvironments/aca-env-prod",
            ),
            patch.object(
                deployer,
                "_create_or_update_container_app",
                return_value="https://my-agent.eastus.azurecontainerapps.io",
            ),
        ):
            result = await deployer.deploy(config, image)

        assert len(pushed_uris) == 1
        assert pushed_uris[0] == "myregistry.azurecr.io/my-agent:1.0.0"
        assert isinstance(result, DeployResult)
        assert result.agent_name == "my-agent"
        assert result.version == "1.0.0"
        assert result.status == "running"

    @pytest.mark.asyncio
    async def test_begin_create_or_update_called_with_correct_params(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config()

        # Pre-populate azure config (normally done by provision())
        deployer._azure_config = _extract_azure_config(config)
        managed_env_id = (
            "/subscriptions/sub-1234/resourceGroups/rg-agents"
            "/providers/Microsoft.App/managedEnvironments/aca-env-prod"
        )

        mock_result = MagicMock()
        mock_result.properties.configuration.ingress.fqdn = "my-agent.eastus.azurecontainerapps.io"

        mock_poller = MagicMock()
        mock_poller.result.return_value = mock_result

        mock_aca_client = MagicMock()
        mock_aca_client.container_apps.begin_create_or_update.return_value = mock_poller

        with patch.object(deployer, "_get_aca_client", return_value=mock_aca_client):
            endpoint = await deployer._create_or_update_container_app(
                config,
                deployer._azure_config,
                "myregistry.azurecr.io/my-agent:1.0.0",
                managed_env_id,
            )

        mock_aca_client.container_apps.begin_create_or_update.assert_called_once()
        call_kwargs = mock_aca_client.container_apps.begin_create_or_update.call_args

        assert call_kwargs.kwargs["resource_group_name"] == "rg-agents"
        assert call_kwargs.kwargs["container_app_name"] == "my-agent"

        body = call_kwargs.kwargs["container_app_envelope"]
        assert body["location"] == "eastus"
        assert body["properties"]["managedEnvironmentId"] == managed_env_id
        assert body["properties"]["configuration"]["ingress"]["targetPort"] == 8080
        assert body["properties"]["configuration"]["ingress"]["external"] is True

        containers = body["properties"]["template"]["containers"]
        assert len(containers) == 1
        assert containers[0]["image"] == "myregistry.azurecr.io/my-agent:1.0.0"
        assert containers[0]["name"] == "my-agent"

        # Verify env vars include the mandatory agent metadata
        env_names = {e["name"] for e in containers[0]["env"]}
        assert "AGENT_NAME" in env_names
        assert "AGENT_VERSION" in env_names
        assert "AGENT_FRAMEWORK" in env_names

        assert endpoint == "https://my-agent.eastus.azurecontainerapps.io"

    @pytest.mark.asyncio
    async def test_endpoint_falls_back_to_constructed_url_when_fqdn_absent(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config()
        deployer._azure_config = _extract_azure_config(config)
        managed_env_id = (
            "/subscriptions/sub-1234/resourceGroups/rg-agents"
            "/providers/Microsoft.App/managedEnvironments/aca-env-prod"
        )

        mock_result = MagicMock()
        # Simulate fqdn not available
        mock_result.properties.configuration.ingress.fqdn = None

        mock_poller = MagicMock()
        mock_poller.result.return_value = mock_result

        mock_aca_client = MagicMock()
        mock_aca_client.container_apps.begin_create_or_update.return_value = mock_poller

        with patch.object(deployer, "_get_aca_client", return_value=mock_aca_client):
            endpoint = await deployer._create_or_update_container_app(
                config,
                deployer._azure_config,
                "myregistry.azurecr.io/my-agent:1.0.0",
                managed_env_id,
            )

        assert endpoint == "https://my-agent.eastus.azurecontainerapps.io"

    @pytest.mark.asyncio
    async def test_acr_push_includes_auth_when_credentials_provided(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config(
            env_vars={
                "AZURE_REGISTRY_USERNAME": "acruser",
                "AZURE_REGISTRY_PASSWORD": "acrpass",
            }
        )
        deployer._azure_config = _extract_azure_config(config)
        deployer._image_uri = "myregistry.azurecr.io/my-agent:1.0.0"

        image = _make_container_image()

        mock_docker_client = MagicMock()
        mock_built_image = MagicMock()
        mock_docker_client.images.build.return_value = (mock_built_image, [])
        mock_docker_client.images.push.return_value = iter([{"status": "Pushed"}])

        mock_docker = MagicMock()
        mock_docker.from_env.return_value = mock_docker_client

        with patch.dict(sys.modules, {"docker": mock_docker}):
            await deployer._push_image(image, "myregistry.azurecr.io/my-agent:1.0.0")

        push_kwargs = mock_docker_client.images.push.call_args.kwargs
        assert "auth_config" in push_kwargs
        assert push_kwargs["auth_config"]["username"] == "acruser"
        assert push_kwargs["auth_config"]["password"] == "acrpass"

    @pytest.mark.asyncio
    async def test_azure_env_vars_excluded_from_container_env(self) -> None:
        """AZURE_* env vars must not leak into the container environment."""
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config(env_vars={"LOG_LEVEL": "debug", "MY_APP_KEY": "abc123"})
        deployer._azure_config = _extract_azure_config(config)
        managed_env_id = "/subscriptions/.../managedEnvironments/aca-env-prod"

        mock_result = MagicMock()
        mock_result.properties.configuration.ingress.fqdn = "my-agent.eastus.azurecontainerapps.io"
        mock_poller = MagicMock()
        mock_poller.result.return_value = mock_result
        mock_aca_client = MagicMock()
        mock_aca_client.container_apps.begin_create_or_update.return_value = mock_poller

        with patch.object(deployer, "_get_aca_client", return_value=mock_aca_client):
            await deployer._create_or_update_container_app(
                config,
                deployer._azure_config,
                "myregistry.azurecr.io/my-agent:1.0.0",
                managed_env_id,
            )

        body = mock_aca_client.container_apps.begin_create_or_update.call_args.kwargs[
            "container_app_envelope"
        ]
        env_names = {e["name"] for e in body["properties"]["template"]["containers"][0]["env"]}

        # User-defined non-AZURE vars must be present
        assert "LOG_LEVEL" in env_names
        assert "MY_APP_KEY" in env_names

        # AZURE_ infra config vars must NOT be in container env
        azure_env_names = {n for n in env_names if n.startswith("AZURE_")}
        assert len(azure_env_names) == 0, f"Found AZURE_ vars in container env: {azure_env_names}"


# ---------------------------------------------------------------------------
# Tests: teardown()
# ---------------------------------------------------------------------------


class TestTeardown:
    @pytest.mark.asyncio
    async def test_begin_delete_called_with_correct_params(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deployer._azure_config = AzureConfig(
            subscription_id="sub-1234",
            resource_group="rg-agents",
            container_apps_env="aca-env-prod",
            registry_server="myregistry.azurecr.io",
        )

        mock_poller = MagicMock()
        mock_poller.result.return_value = None

        mock_aca_client = MagicMock()
        mock_aca_client.container_apps.begin_delete.return_value = mock_poller

        with patch.object(deployer, "_get_aca_client", return_value=mock_aca_client):
            await deployer.teardown("my-agent")

        mock_aca_client.container_apps.begin_delete.assert_called_once_with(
            resource_group_name="rg-agents",
            container_app_name="my-agent",
        )
        mock_poller.result.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_config_not_initialized(self) -> None:
        deployer = AzureContainerAppsDeployer()  # _azure_config is None

        with pytest.raises(RuntimeError, match="Cannot teardown without Azure config"):
            await deployer.teardown("my-agent")

    @pytest.mark.asyncio
    async def test_reraises_import_error_with_pip_hint(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deployer._azure_config = AzureConfig(
            subscription_id="sub-1234",
            resource_group="rg-agents",
            container_apps_env="aca-env-prod",
            registry_server="myregistry.azurecr.io",
        )

        with patch.object(
            deployer,
            "_get_aca_client",
            side_effect=ImportError("pip install agentbreeder[azure]"),
        ):
            with pytest.raises(ImportError, match="pip install agentbreeder\\[azure\\]"):
                await deployer.teardown("my-agent")

    @pytest.mark.asyncio
    async def test_reraises_unexpected_exceptions(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deployer._azure_config = AzureConfig(
            subscription_id="sub-1234",
            resource_group="rg-agents",
            container_apps_env="aca-env-prod",
            registry_server="myregistry.azurecr.io",
        )

        mock_aca_client = MagicMock()
        mock_aca_client.container_apps.begin_delete.side_effect = RuntimeError(
            "Connection timeout"
        )

        with patch.object(deployer, "_get_aca_client", return_value=mock_aca_client):
            with pytest.raises(RuntimeError, match="Connection timeout"):
                await deployer.teardown("my-agent")


# ---------------------------------------------------------------------------
# Tests: health_check()
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_healthy_when_endpoint_responds_200(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deploy_result = _make_deploy_result()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("engine.deployers.azure_container_apps.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            status = await deployer.health_check(deploy_result, timeout=10, interval=5)

        assert status.healthy is True
        assert status.checks["reachable"] is True
        assert status.checks["healthy"] is True

    @pytest.mark.asyncio
    async def test_returns_unhealthy_when_endpoint_returns_non_200(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deploy_result = _make_deploy_result()

        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("engine.deployers.azure_container_apps.httpx.AsyncClient") as mock_cls,
            _fast_health_clock(),
        ):
            mock_cls.return_value = mock_client
            # timeout=10, interval=5 → 2 attempts, all returning 503
            status = await deployer.health_check(deploy_result, timeout=10, interval=5)

        assert status.healthy is False
        assert status.checks["reachable"] is True
        assert status.checks["healthy"] is False

    @pytest.mark.asyncio
    async def test_returns_unhealthy_after_timeout_when_connect_fails(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deploy_result = _make_deploy_result()

        import httpx as httpx_module

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx_module.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("engine.deployers.azure_container_apps.httpx.AsyncClient") as mock_cls,
            _fast_health_clock(),
        ):
            mock_cls.return_value = mock_client
            status = await deployer.health_check(deploy_result, timeout=10, interval=5)

        assert status.healthy is False
        assert status.checks["reachable"] is False
        assert status.checks["healthy"] is False

    @pytest.mark.asyncio
    async def test_polls_correct_health_url(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deploy_result = _make_deploy_result(
            endpoint_url="https://my-agent.eastus.azurecontainerapps.io"
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("engine.deployers.azure_container_apps.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = mock_client
            await deployer.health_check(deploy_result, timeout=5, interval=5)

        mock_client.get.assert_called_once_with(
            "https://my-agent.eastus.azurecontainerapps.io/health"
        )


# ---------------------------------------------------------------------------
# Tests: get_logs()
# ---------------------------------------------------------------------------


class TestGetLogs:
    @pytest.mark.asyncio
    async def test_returns_fallback_message_when_azure_monitor_not_installed(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deployer._azure_config = AzureConfig(
            subscription_id="sub-1234",
            resource_group="rg-agents",
            container_apps_env="aca-env-prod",
            registry_server="myregistry.azurecr.io",
        )

        with patch.dict(sys.modules, {"azure.monitor.query": None}):
            logs = await deployer.get_logs("my-agent")

        assert len(logs) == 1
        assert "Azure Monitor Logs query not configured" in logs[0]
        assert "pip install agentbreeder[azure]" in logs[0]

    @pytest.mark.asyncio
    async def test_returns_fallback_message_when_config_not_initialized(self) -> None:
        deployer = AzureContainerAppsDeployer()  # _azure_config is None

        logs = await deployer.get_logs("my-agent")

        assert len(logs) == 1
        assert "Azure config not initialized" in logs[0]

    @pytest.mark.asyncio
    async def test_returns_logs_on_success(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deployer._azure_config = AzureConfig(
            subscription_id="sub-1234",
            resource_group="rg-agents",
            container_apps_env="aca-env-prod",
            registry_server="myregistry.azurecr.io",
        )

        mock_row_1 = ["2026-04-10T10:00:00Z", "Agent started successfully"]
        mock_row_2 = ["2026-04-10T10:00:01Z", "Listening on port 8080"]

        mock_table = MagicMock()
        mock_table.rows = [mock_row_1, mock_row_2]

        mock_response = MagicMock()
        mock_response.status = "Success"  # LogsQueryStatus.SUCCESS
        mock_response.tables = [mock_table]

        mock_logs_client = MagicMock()
        mock_logs_client.query_resource.return_value = mock_response

        mock_logs_query_status = MagicMock()
        mock_logs_query_status.SUCCESS = "Success"

        mock_azure_monitor = MagicMock()
        mock_azure_monitor.LogsQueryClient.return_value = mock_logs_client
        mock_azure_monitor.LogsQueryStatus = mock_logs_query_status

        mock_identity = MagicMock()

        with patch.dict(
            sys.modules,
            {
                "azure.monitor.query": mock_azure_monitor,
                "azure.identity": mock_identity,
            },
        ):
            logs = await deployer.get_logs("my-agent")

        assert len(logs) == 2
        assert "Agent started successfully" in logs[0]
        assert "Listening on port 8080" in logs[1]

    @pytest.mark.asyncio
    async def test_filters_by_since_timestamp_when_provided(self) -> None:
        deployer = AzureContainerAppsDeployer()
        deployer._azure_config = AzureConfig(
            subscription_id="sub-1234",
            resource_group="rg-agents",
            container_apps_env="aca-env-prod",
            registry_server="myregistry.azurecr.io",
        )

        since = datetime(2026, 4, 10, 9, 0, 0)

        mock_table = MagicMock()
        mock_table.rows = []

        mock_response = MagicMock()
        mock_response.status = "Success"
        mock_response.tables = [mock_table]

        mock_logs_client = MagicMock()
        mock_logs_client.query_resource.return_value = mock_response

        mock_logs_query_status = MagicMock()
        mock_logs_query_status.SUCCESS = "Success"

        mock_azure_monitor = MagicMock()
        mock_azure_monitor.LogsQueryClient.return_value = mock_logs_client
        mock_azure_monitor.LogsQueryStatus = mock_logs_query_status

        mock_identity = MagicMock()

        with patch.dict(
            sys.modules,
            {
                "azure.monitor.query": mock_azure_monitor,
                "azure.identity": mock_identity,
            },
        ):
            await deployer.get_logs("my-agent", since=since)

        # Verify the query was called and included the since timestamp
        call_args = mock_logs_client.query_resource.call_args
        query_str = call_args.args[1] if call_args.args else call_args.kwargs.get("query", "")
        assert "2026-04-10T09:00:00Z" in query_str


# ---------------------------------------------------------------------------
# Tests: _build_container_app_body
# ---------------------------------------------------------------------------


class TestBuildContainerAppBody:
    def test_scaling_config_applied_correctly(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config(min_replicas=1, max_replicas=8)
        azure = _extract_azure_config(config)
        managed_env_id = "/subscriptions/.../managedEnvironments/aca-env-prod"

        body = deployer._build_container_app_body(
            config, azure, "myregistry.azurecr.io/my-agent:1.0.0", managed_env_id
        )

        scale = body["properties"]["template"]["scale"]
        assert scale["minReplicas"] == 1
        assert scale["maxReplicas"] == 8

    def test_registry_credentials_added_to_configuration_when_present(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config(
            env_vars={
                "AZURE_REGISTRY_USERNAME": "acruser",
                "AZURE_REGISTRY_PASSWORD": "acrpass",
            }
        )
        azure = _extract_azure_config(config)
        managed_env_id = "/subscriptions/.../managedEnvironments/aca-env-prod"

        body = deployer._build_container_app_body(
            config, azure, "myregistry.azurecr.io/my-agent:1.0.0", managed_env_id
        )

        registries = body["properties"]["configuration"]["registries"]
        secrets = body["properties"]["configuration"]["secrets"]

        assert len(registries) == 1
        assert registries[0]["server"] == "myregistry.azurecr.io"
        assert registries[0]["username"] == "acruser"

        assert len(secrets) == 1
        assert secrets[0]["name"] == "acr-password"
        assert secrets[0]["value"] == "acrpass"

    def test_no_registry_credentials_when_not_provided(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config()
        azure = _extract_azure_config(config)
        managed_env_id = "/subscriptions/.../managedEnvironments/aca-env-prod"

        body = deployer._build_container_app_body(
            config, azure, "myregistry.azurecr.io/my-agent:1.0.0", managed_env_id
        )

        assert body["properties"]["configuration"]["registries"] == []
        assert body["properties"]["configuration"]["secrets"] == []

    def test_cpu_and_memory_resources_applied(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config(cpu="1", memory="2Gi")
        azure = _extract_azure_config(config)
        managed_env_id = "/subscriptions/.../managedEnvironments/aca-env-prod"

        body = deployer._build_container_app_body(
            config, azure, "myregistry.azurecr.io/my-agent:1.0.0", managed_env_id
        )

        resources = body["properties"]["template"]["containers"][0]["resources"]
        assert resources["cpu"] == 1.0
        assert resources["memory"] == "2Gi"

    def test_tags_include_agent_metadata(self) -> None:
        deployer = AzureContainerAppsDeployer()
        config = _make_agent_config()
        azure = _extract_azure_config(config)
        managed_env_id = "/subscriptions/.../managedEnvironments/aca-env-prod"

        body = deployer._build_container_app_body(
            config, azure, "myregistry.azurecr.io/my-agent:1.0.0", managed_env_id
        )

        tags = body["tags"]
        assert tags["managed-by"] == "agentbreeder"
        assert tags["agent-name"] == "my-agent"
        assert tags["team"] == "engineering"
