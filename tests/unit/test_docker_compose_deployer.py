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

    @pytest.mark.asyncio
    async def test_deploy_starts_ollama_sidecar_for_ollama_model(self, tmp_path: Path) -> None:
        """When model is ollama/*, deploy() must start the Ollama sidecar container."""
        import docker

        from engine.deployers.docker_compose import DockerComposeDeployer
        from engine.runtimes.base import ContainerImage

        mock_client = MagicMock()
        mock_client.images.build.return_value = (MagicMock(), [])
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_client.containers.run.return_value = MagicMock(id="cid")
        mock_client.networks.get.side_effect = docker.errors.NotFound("no net")
        mock_client.networks.create.return_value = MagicMock()

        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        image = ContainerImage(
            tag="test:1.0.0", dockerfile_content="FROM python", context_dir=tmp_path
        )
        deployer = DockerComposeDeployer()

        with patch("docker.from_env", return_value=mock_client):
            with patch.object(deployer, "_pull_ollama_model", new_callable=AsyncMock) as mock_pull:
                await deployer.deploy(config, image)

        sidecar_run_calls = [
            c for c in mock_client.containers.run.call_args_list if "ollama/ollama" in str(c)
        ]
        assert len(sidecar_run_calls) >= 1, "Ollama sidecar was not started"
        mock_pull.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_injects_ollama_base_url_in_container_env(self, tmp_path: Path) -> None:
        """For ollama/ models, OLLAMA_BASE_URL must be in the agent container env."""
        import docker

        from engine.deployers.docker_compose import OLLAMA_CONTAINER_NAME, DockerComposeDeployer
        from engine.runtimes.base import ContainerImage

        mock_client = MagicMock()
        mock_client.images.build.return_value = (MagicMock(), [])
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_client.containers.run.return_value = MagicMock(id="cid")
        mock_client.networks.get.side_effect = docker.errors.NotFound("no net")
        mock_client.networks.create.return_value = MagicMock()

        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        image = ContainerImage(
            tag="test:1.0.0", dockerfile_content="FROM python", context_dir=tmp_path
        )
        deployer = DockerComposeDeployer()

        with patch("docker.from_env", return_value=mock_client):
            with patch.object(deployer, "_pull_ollama_model", new_callable=AsyncMock):
                await deployer.deploy(config, image)

        # Find agent container run call (not sidecar)
        agent_run_calls = [
            c for c in mock_client.containers.run.call_args_list if "ollama/ollama" not in str(c)
        ]
        assert agent_run_calls, "Agent container was not started"
        env_arg = agent_run_calls[0].kwargs.get("environment", {})
        assert "OLLAMA_BASE_URL" in env_arg
        assert OLLAMA_CONTAINER_NAME in env_arg["OLLAMA_BASE_URL"]

    @pytest.mark.asyncio
    async def test_deploy_skips_ollama_sidecar_for_non_ollama_model(self, tmp_path: Path) -> None:
        """For non-ollama models, deploy() must NOT start the Ollama sidecar."""
        import docker

        from engine.deployers.docker_compose import DockerComposeDeployer
        from engine.runtimes.base import ContainerImage

        mock_client = MagicMock()
        mock_client.images.build.return_value = (MagicMock(), [])
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_client.containers.run.return_value = MagicMock(id="cid")

        config = _make_config(model={"primary": "claude-sonnet-4"})
        image = ContainerImage(
            tag="test:1.0.0", dockerfile_content="FROM python", context_dir=tmp_path
        )
        deployer = DockerComposeDeployer()

        with patch("docker.from_env", return_value=mock_client):
            await deployer.deploy(config, image)

        ollama_runs = [
            c for c in mock_client.containers.run.call_args_list if "ollama/ollama" in str(c)
        ]
        assert len(ollama_runs) == 0, "Ollama sidecar was incorrectly started"

    @pytest.mark.asyncio
    async def test_deploy_does_not_restart_running_ollama_sidecar(self, tmp_path: Path) -> None:
        """If Ollama sidecar is already running, deploy() must not start it again."""
        import docker

        from engine.deployers.docker_compose import OLLAMA_CONTAINER_NAME, DockerComposeDeployer
        from engine.runtimes.base import ContainerImage

        # Sidecar container already exists and is running
        running_sidecar = MagicMock()
        running_sidecar.status = "running"
        running_sidecar.name = OLLAMA_CONTAINER_NAME

        def containers_get_side_effect(name: str) -> MagicMock:
            if name == OLLAMA_CONTAINER_NAME:
                return running_sidecar
            raise docker.errors.NotFound("not found")

        mock_client = MagicMock()
        mock_client.images.build.return_value = (MagicMock(), [])
        mock_client.containers.get.side_effect = containers_get_side_effect
        mock_client.containers.run.return_value = MagicMock(id="cid")
        mock_client.networks.get.return_value = MagicMock()  # network already exists

        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        image = ContainerImage(
            tag="test:1.0.0", dockerfile_content="FROM python", context_dir=tmp_path
        )
        deployer = DockerComposeDeployer()

        with patch("docker.from_env", return_value=mock_client):
            with patch.object(deployer, "_pull_ollama_model", new_callable=AsyncMock):
                await deployer.deploy(config, image)

        # Only one containers.run call — for the agent, not a new Ollama sidecar
        ollama_start_calls = [
            c for c in mock_client.containers.run.call_args_list if "ollama/ollama" in str(c)
        ]
        assert len(ollama_start_calls) == 0, "Ollama sidecar was restarted when already running"
