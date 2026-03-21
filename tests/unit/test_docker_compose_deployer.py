"""Extended tests for engine/deployers/docker_compose.py."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config_parser import AgentConfig, FrameworkType
from engine.deployers.base import DeployResult
from engine.deployers.docker_compose import DockerComposeDeployer
from engine.runtimes.base import ContainerImage


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


def _make_image() -> ContainerImage:
    d = Path(tempfile.mkdtemp())
    (d / "Dockerfile").write_text("FROM python:3.11-slim")
    return ContainerImage(
        tag="agentbreeder/test-agent:1.0.0",
        dockerfile_content="FROM python:3.11-slim",
        context_dir=d,
    )


class TestDockerComposeDeployerState:
    def test_state_persists(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
        ):
            deployer = DockerComposeDeployer()
            port = deployer._allocate_port()
            assert port == 8080

            # State should be saved
            assert state_file.exists()
            state = json.loads(state_file.read_text())
            assert state["next_port"] == 8081

    def test_state_loads_existing(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        agentbreeder_dir.mkdir()
        state_file = agentbreeder_dir / "state.json"
        state_file.write_text(json.dumps({"agents": {}, "next_port": 9000}))

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
        ):
            deployer = DockerComposeDeployer()
            port = deployer._allocate_port()
            assert port == 9000


class TestDockerComposeDeployerDeploy:
    @pytest.mark.asyncio
    async def test_deploy_builds_and_runs_container(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_container = MagicMock()
        mock_container.id = "container123"

        mock_client = MagicMock()
        mock_client.images.build.return_value = (MagicMock(), [{"stream": "Building..."}])
        mock_client.containers.get.side_effect = Exception("NotFound")
        mock_client.containers.run.return_value = mock_container

        mock_docker = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_docker.errors = MagicMock()
        mock_docker.errors.NotFound = type("NotFound", (Exception,), {})
        mock_client.containers.get.side_effect = mock_docker.errors.NotFound()

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch.dict("sys.modules", {"docker": mock_docker}),
        ):
            deployer = DockerComposeDeployer()
            # Pre-provision
            config = _make_config()
            await deployer.provision(config)

            # Deploy
            image = _make_image()
            result = await deployer.deploy(config, image)

        assert result.agent_name == "test-agent"
        assert result.status == "running"
        assert result.container_id == "container123"
        mock_client.images.build.assert_called_once()
        mock_client.containers.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_stops_container(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_container = MagicMock()
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        mock_docker = MagicMock()
        mock_docker.from_env.return_value = mock_client
        mock_docker.errors = MagicMock()
        mock_docker.errors.NotFound = type("NotFound", (Exception,), {})

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch.dict("sys.modules", {"docker": mock_docker}),
        ):
            deployer = DockerComposeDeployer()
            await deployer.teardown("test-agent")

        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_handles_missing_container(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_docker = MagicMock()
        not_found = type("NotFound", (Exception,), {})
        mock_docker.errors.NotFound = not_found
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = not_found()
        mock_docker.from_env.return_value = mock_client

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch.dict("sys.modules", {"docker": mock_docker}),
        ):
            deployer = DockerComposeDeployer()
            # Should not raise
            await deployer.teardown("nonexistent")

    @pytest.mark.asyncio
    async def test_get_logs(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_container = MagicMock()
        mock_container.logs.return_value = b"2024-01-01 Log line 1\n2024-01-01 Log line 2"

        mock_docker = MagicMock()
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container
        mock_docker.from_env.return_value = mock_client
        mock_docker.errors = MagicMock()
        mock_docker.errors.NotFound = type("NotFound", (Exception,), {})

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch.dict("sys.modules", {"docker": mock_docker}),
        ):
            deployer = DockerComposeDeployer()
            logs = await deployer.get_logs("test-agent")

        assert len(logs) == 2
        assert "Log line 1" in logs[0]

    @pytest.mark.asyncio
    async def test_get_logs_container_not_found(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_docker = MagicMock()
        not_found = type("NotFound", (Exception,), {})
        mock_docker.errors.NotFound = not_found
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = not_found()
        mock_docker.from_env.return_value = mock_client

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch.dict("sys.modules", {"docker": mock_docker}),
        ):
            deployer = DockerComposeDeployer()
            logs = await deployer.get_logs("nonexistent")

        assert len(logs) == 1
        assert "not found" in logs[0].lower()

    @pytest.mark.asyncio
    async def test_health_check_succeeds(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
        ):
            deployer = DockerComposeDeployer()

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client_instance = AsyncMock()
        mock_client_instance.get.return_value = mock_response
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        result = DeployResult(
            endpoint_url="http://localhost:8080",
            container_id="abc123",
            status="running",
            agent_name="test",
            version="1.0.0",
        )

        with patch("httpx.AsyncClient", return_value=mock_client_instance):
            health = await deployer.health_check(result, timeout=4, interval=1)

        assert health.healthy is True
        assert health.checks["reachable"] is True
