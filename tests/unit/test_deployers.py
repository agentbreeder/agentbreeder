"""Tests for engine/deployers/ — deployer registry and Docker Compose deployer."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.builder import DeployEngine
from engine.config_parser import AgentConfig, CloudType, FrameworkType
from engine.deployers import get_deployer
from engine.deployers.base import DeployResult, HealthStatus, InfraResult
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

    def test_runtime_deployers_take_precedence_over_cloud(self) -> None:
        """RUNTIME_DEPLOYERS overrides DEPLOYERS.

        When deploy.cloud=aws (default ECS Fargate) and deploy.runtime=app-runner,
        the runtime alias wins and the AWS App Runner deployer is returned.
        """
        from engine.deployers.aws_app_runner import AWSAppRunnerDeployer
        from engine.deployers.aws_ecs import AWSECSDeployer

        # Default for cloud=aws is ECS Fargate
        default_deployer = get_deployer(CloudType.aws)
        assert isinstance(default_deployer, AWSECSDeployer)

        # But runtime=app-runner overrides it
        runtime_deployer = get_deployer(CloudType.aws, runtime="app-runner")
        assert isinstance(runtime_deployer, AWSAppRunnerDeployer)

        # Same idea: cloud=gcp default is Cloud Run, but an alias still wins
        # (cloud-run is also the GCP default — assert the runtime path is taken)
        from engine.deployers.gcp_cloudrun import GCPCloudRunDeployer

        runtime_gcp = get_deployer(CloudType.gcp, runtime="cloud-run")
        assert isinstance(runtime_gcp, GCPCloudRunDeployer)

    def test_runtime_precedence_is_case_insensitive(self) -> None:
        """Runtime lookup normalises case and whitespace before precedence resolution."""
        from engine.deployers.aws_app_runner import AWSAppRunnerDeployer

        deployer = get_deployer(CloudType.aws, runtime="  APP-RUNNER  ")
        assert isinstance(deployer, AWSAppRunnerDeployer)


def _make_agent_dir_for_engine() -> Path:
    """Create a temp agent directory with valid files for DeployEngine tests."""
    d = Path(tempfile.mkdtemp())
    (d / "agent.yaml").write_text("""\
name: test-agent
version: 1.0.0
team: test
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
""")
    (d / "agent.py").write_text("graph = None")
    (d / "requirements.txt").write_text("langgraph>=0.2.0")
    return d


class TestDeployEnginePartialFailureRollback:
    """D7: The engine must teardown when a deployer's health check fails after deploy."""

    @pytest.mark.asyncio
    async def test_deploy_engine_teardown_on_health_check_failure(self) -> None:
        """Engine must call deployer.teardown() when health_check returns healthy=False.

        Cross-deployer guarantee: regardless of cloud target, if deploy() succeeds
        but health_check() reports unhealthy, the engine must invoke teardown()
        before propagating the DeployError so partial infrastructure is cleaned up.
        """
        agent_dir = _make_agent_dir_for_engine()

        mock_deployer = MagicMock()
        mock_deployer.provision = AsyncMock(
            return_value=InfraResult(endpoint_url="http://localhost:8080", resource_ids={})
        )
        mock_deployer.deploy = AsyncMock(
            return_value=DeployResult(
                endpoint_url="http://localhost:8080",
                container_id="abc123",
                status="running",
                agent_name="test-agent",
                version="1.0.0",
            )
        )
        mock_deployer.health_check = AsyncMock(
            return_value=HealthStatus(healthy=False, checks={"reachable": False})
        )
        mock_deployer.teardown = AsyncMock()

        with patch("engine.builder.get_deployer", return_value=mock_deployer):
            engine = DeployEngine()
            with pytest.raises(Exception, match="Health check failed"):
                await engine.deploy(agent_dir / "agent.yaml")

        # Engine must have called teardown before raising
        mock_deployer.teardown.assert_called_once_with("test-agent")
        # And deploy/health_check must have happened in order
        mock_deployer.deploy.assert_awaited_once()
        mock_deployer.health_check.assert_awaited_once()


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
