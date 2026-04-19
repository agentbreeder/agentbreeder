"""Tests for engine/deployers/ — deployer registry and Docker Compose deployer."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from engine.config_parser import AgentConfig, CloudType, FrameworkType
from engine.deployers import get_deployer
from engine.deployers.base import DeployResult
from engine.deployers.docker_compose import DockerComposeDeployer


def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "test",
        "owner": "test@example.com",
        "framework": FrameworkType.langgraph,
        "model": {"primary": "gpt-4o"},
        "deploy": {"cloud": "local"},
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


class TestGetDeployer:
    def test_get_local_deployer(self) -> None:
        deployer = get_deployer(CloudType.local)
        assert isinstance(deployer, DockerComposeDeployer)

    def test_get_kubernetes_deployer(self) -> None:
        from engine.deployers.kubernetes import KubernetesDeployer

        deployer = get_deployer(CloudType.kubernetes)
        assert isinstance(deployer, KubernetesDeployer)

    def test_get_aws_deployer(self) -> None:
        from engine.deployers.aws_ecs import AWSECSDeployer

        deployer = get_deployer(CloudType.aws)
        assert isinstance(deployer, AWSECSDeployer)

    def test_get_azure_deployer(self) -> None:
        from engine.deployers.azure_container_apps import AzureContainerAppsDeployer

        deployer = get_deployer(CloudType.azure)
        assert isinstance(deployer, AzureContainerAppsDeployer)

    def test_runtime_alias_ecs_fargate(self) -> None:
        from engine.deployers.aws_ecs import AWSECSDeployer

        deployer = get_deployer(CloudType.aws, runtime="ecs-fargate")
        assert isinstance(deployer, AWSECSDeployer)

    def test_runtime_alias_eks(self) -> None:
        from engine.deployers.kubernetes import KubernetesDeployer

        deployer = get_deployer(CloudType.kubernetes, runtime="eks")
        assert isinstance(deployer, KubernetesDeployer)

    def test_runtime_alias_app_runner(self) -> None:
        from engine.deployers.aws_app_runner import AWSAppRunnerDeployer

        deployer = get_deployer(CloudType.aws, runtime="app-runner")
        assert isinstance(deployer, AWSAppRunnerDeployer)

    def test_runtime_alias_cloud_run(self) -> None:
        from engine.deployers.gcp_cloudrun import GCPCloudRunDeployer

        deployer = get_deployer(CloudType.gcp, runtime="cloud-run")
        assert isinstance(deployer, GCPCloudRunDeployer)

    def test_unknown_runtime_falls_back_to_cloud_default(self) -> None:
        from engine.deployers.docker_compose import DockerComposeDeployer

        deployer = get_deployer(CloudType.local, runtime="nonexistent-runtime")
        assert isinstance(deployer, DockerComposeDeployer)

    def test_unsupported_cloud_raises_key_error(self) -> None:
        from unittest.mock import MagicMock

        from engine.config_parser import CloudType

        fake_cloud = MagicMock(spec=CloudType)
        fake_cloud.value = "martian-cloud"
        with pytest.raises(KeyError, match="not yet supported"):
            get_deployer(fake_cloud)


class TestDockerComposeDeployer:
    @pytest.fixture
    def deployer(self, tmp_path) -> DockerComposeDeployer:
        """Create a deployer with a temp state directory."""
        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", tmp_path / ".agentbreeder"),
            patch(
                "engine.deployers.docker_compose.STATE_FILE",
                tmp_path / ".agentbreeder" / "state.json",
            ),
        ):
            d = DockerComposeDeployer()
            return d

    @pytest.mark.asyncio
    async def test_provision_allocates_port(self, deployer) -> None:
        config = _make_config()
        result = await deployer.provision(config)
        assert result.endpoint_url.startswith("http://localhost:")
        assert "port" in result.resource_ids

    @pytest.mark.asyncio
    async def test_provision_increments_port(self, deployer) -> None:
        config1 = _make_config(name="agent-one")
        config2 = _make_config(name="agent-two")
        result1 = await deployer.provision(config1)
        result2 = await deployer.provision(config2)
        port1 = int(result1.resource_ids["port"])
        port2 = int(result2.resource_ids["port"])
        assert port2 == port1 + 1

    @pytest.mark.asyncio
    async def test_health_check_timeout(self, deployer) -> None:
        """Health check should fail after timeout when agent is not reachable."""
        result = DeployResult(
            endpoint_url="http://localhost:99999",
            container_id="fake",
            status="running",
            agent_name="test",
            version="1.0.0",
        )
        health = await deployer.health_check(result, timeout=2, interval=1)
        assert health.healthy is False
        assert health.checks.get("reachable") is False
