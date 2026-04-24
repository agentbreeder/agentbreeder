"""Extended tests for engine/deployers/docker_compose.py."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config_parser import AgentConfig, FrameworkType
from engine.deployers.base import DeployResult
from engine.deployers.docker_compose import DockerComposeDeployer
from engine.runtimes.base import ContainerImage


def _mock_build_success() -> MagicMock:
    """Return a CompletedProcess mock representing a successful docker build."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = 0
    m.stdout = "Successfully built abc123\n"
    m.stderr = ""
    return m


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


def _make_not_found():
    """Return a fresh NotFound exception class and instance for docker error mocking."""
    import docker

    return docker.errors.NotFound


class TestDockerComposeDeployerDeploy:
    """Tests for DockerComposeDeployer deploy/teardown/logs.

    All tests patch _docker_client() directly so they work on macOS
    (where _docker_client uses DockerClient(base_url=...) not from_env()).
    """

    @pytest.mark.asyncio
    async def test_deploy_builds_and_runs_container(self) -> None:
        import docker

        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_container = MagicMock()
        mock_container.id = "container123"
        mock_container.status = "running"

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_client.containers.run.return_value = mock_container

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
            patch("subprocess.run", return_value=_mock_build_success()),
        ):
            deployer = DockerComposeDeployer()
            config = _make_config()
            await deployer.provision(config)
            image = _make_image()
            result = await deployer.deploy(config, image)

        assert result.agent_name == "test-agent"
        assert result.status == "running"
        assert result.container_id == "container123"
        mock_client.containers.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_stops_container(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_container = MagicMock()
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
        ):
            deployer = DockerComposeDeployer()
            await deployer.teardown("test-agent")

        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_handles_missing_container(self) -> None:
        import docker

        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
        ):
            deployer = DockerComposeDeployer()
            await deployer.teardown("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_get_logs(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_container = MagicMock()
        mock_container.logs.return_value = b"2024-01-01 Log line 1\n2024-01-01 Log line 2"

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
        ):
            deployer = DockerComposeDeployer()
            logs = await deployer.get_logs("test-agent")

        assert len(logs) == 2
        assert "Log line 1" in logs[0]

    @pytest.mark.asyncio
    async def test_get_logs_container_not_found(self) -> None:
        import docker

        tmp = Path(tempfile.mkdtemp())
        agentbreeder_dir = tmp / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
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

        mock_run_container = MagicMock(id="cid", status="running")
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_client.containers.run.return_value = mock_run_container
        mock_client.networks.get.side_effect = docker.errors.NotFound("no net")
        mock_client.networks.create.return_value = MagicMock()

        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        image = ContainerImage(
            tag="test:1.0.0", dockerfile_content="FROM python", context_dir=tmp_path
        )
        deployer = DockerComposeDeployer()

        with (
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
            patch("subprocess.run", return_value=_mock_build_success()),
            patch.object(deployer, "_pull_ollama_model", new_callable=AsyncMock) as mock_pull,
        ):
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

        mock_run_container = MagicMock(id="cid", status="running")
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_client.containers.run.return_value = mock_run_container
        mock_client.networks.get.side_effect = docker.errors.NotFound("no net")
        mock_client.networks.create.return_value = MagicMock()

        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        image = ContainerImage(
            tag="test:1.0.0", dockerfile_content="FROM python", context_dir=tmp_path
        )
        deployer = DockerComposeDeployer()

        with (
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
            patch("subprocess.run", return_value=_mock_build_success()),
            patch.object(deployer, "_pull_ollama_model", new_callable=AsyncMock),
        ):
            await deployer.deploy(config, image)

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

        mock_run_container = MagicMock(id="cid", status="running")
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_client.containers.run.return_value = mock_run_container

        config = _make_config(model={"primary": "claude-sonnet-4"})
        image = ContainerImage(
            tag="test:1.0.0", dockerfile_content="FROM python", context_dir=tmp_path
        )
        deployer = DockerComposeDeployer()

        with (
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
            patch("subprocess.run", return_value=_mock_build_success()),
        ):
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

        running_sidecar = MagicMock()
        running_sidecar.status = "running"
        running_sidecar.name = OLLAMA_CONTAINER_NAME

        mock_run_container = MagicMock(id="cid", status="running")

        def containers_get_side_effect(name: str) -> MagicMock:
            if name == OLLAMA_CONTAINER_NAME:
                return running_sidecar
            raise docker.errors.NotFound("not found")

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = containers_get_side_effect
        mock_client.containers.run.return_value = mock_run_container
        mock_client.networks.get.return_value = MagicMock()

        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        image = ContainerImage(
            tag="test:1.0.0", dockerfile_content="FROM python", context_dir=tmp_path
        )
        deployer = DockerComposeDeployer()

        with (
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
            patch("subprocess.run", return_value=_mock_build_success()),
            patch.object(deployer, "_pull_ollama_model", new_callable=AsyncMock),
        ):
            await deployer.deploy(config, image)

        ollama_start_calls = [
            c for c in mock_client.containers.run.call_args_list if "ollama/ollama" in str(c)
        ]
        assert len(ollama_start_calls) == 0, "Ollama sidecar was restarted when already running"

    @pytest.mark.asyncio
    async def test_ollama_sidecar_restarts_stopped_container(self, tmp_path: Path) -> None:
        """If sidecar is stopped (not running), it should be started (not run again)."""
        import docker

        from engine.deployers.docker_compose import OLLAMA_CONTAINER_NAME, DockerComposeDeployer
        from engine.runtimes.base import ContainerImage

        stopped_sidecar = MagicMock()
        stopped_sidecar.status = "exited"
        stopped_sidecar.name = OLLAMA_CONTAINER_NAME

        mock_run_container = MagicMock(id="cid", status="running")

        def containers_get_side_effect(name: str) -> MagicMock:
            if name == OLLAMA_CONTAINER_NAME:
                return stopped_sidecar
            raise docker.errors.NotFound("not found")

        mock_client = MagicMock()
        mock_client.containers.get.side_effect = containers_get_side_effect
        mock_client.containers.run.return_value = mock_run_container
        mock_client.networks.get.return_value = MagicMock()

        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        image = ContainerImage(
            tag="test:1.0.0", dockerfile_content="FROM python", context_dir=tmp_path
        )
        deployer = DockerComposeDeployer()

        with (
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
            patch("subprocess.run", return_value=_mock_build_success()),
            patch.object(deployer, "_pull_ollama_model", new_callable=AsyncMock),
        ):
            await deployer.deploy(config, image)

        stopped_sidecar.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_pull_ollama_model_success(self, tmp_path: Path) -> None:
        """_pull_ollama_model should exec ollama pull and log success."""
        from engine.deployers.docker_compose import DockerComposeDeployer

        mock_container = MagicMock()
        mock_container.exec_run.side_effect = [
            (0, b""),  # ollama list succeeds immediately
            (0, b""),  # ollama pull succeeds
        ]

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        deployer = DockerComposeDeployer()
        with patch("engine.deployers.docker_compose._docker_client", return_value=mock_client):
            await deployer._pull_ollama_model(mock_client, "gemma3:27b")

        calls = [str(c) for c in mock_container.exec_run.call_args_list]
        assert any("pull" in c for c in calls)

    @pytest.mark.asyncio
    async def test_pull_ollama_model_failure_logs_warning(self, tmp_path: Path) -> None:
        """_pull_ollama_model should warn (not raise) when pull fails."""
        from engine.deployers.docker_compose import DockerComposeDeployer

        mock_container = MagicMock()
        mock_container.exec_run.side_effect = [
            (0, b""),  # ollama list ok
            (1, b"error: model not found"),  # pull fails
        ]

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        deployer = DockerComposeDeployer()
        # Should not raise
        await deployer._pull_ollama_model(mock_client, "nonexistent:tag")

    @pytest.mark.asyncio
    async def test_teardown_updates_state(self, tmp_path: Path) -> None:
        """teardown() should update the state file to mark the agent as stopped."""
        import json

        agentbreeder_dir = tmp_path / ".agentbreeder"
        state_file = agentbreeder_dir / "state.json"

        mock_container = MagicMock()
        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with (
            patch("engine.deployers.docker_compose.AGENTBREEDER_DIR", agentbreeder_dir),
            patch("engine.deployers.docker_compose.STATE_FILE", state_file),
            patch("engine.deployers.docker_compose._docker_client", return_value=mock_client),
        ):
            deployer = DockerComposeDeployer()
            # Manually set up agent in state
            deployer._state["agents"]["test-agent"] = {"port": 8080, "status": "running"}
            await deployer.teardown("test-agent")

            # Check state was updated
            saved = json.loads(state_file.read_text())
            assert saved["agents"]["test-agent"]["status"] == "stopped"

    @pytest.mark.asyncio
    async def test_get_logs_with_since_filter(self) -> None:
        from datetime import datetime

        from engine.deployers.docker_compose import DockerComposeDeployer

        mock_container = MagicMock()
        mock_container.logs.return_value = b"2024-01-01 line1"

        mock_client = MagicMock()
        mock_client.containers.get.return_value = mock_container

        with patch("engine.deployers.docker_compose._docker_client", return_value=mock_client):
            deployer = DockerComposeDeployer()
            since = datetime(2024, 1, 1)
            await deployer.get_logs("test-agent", since=since)

        call_kwargs = mock_container.logs.call_args.kwargs
        assert "since" in call_kwargs

    def test_docker_client_uses_docker_host_env(self) -> None:
        """When DOCKER_HOST is set, _docker_client() uses docker.from_env()."""
        from engine.deployers.docker_compose import _docker_client

        with (
            patch.dict("os.environ", {"DOCKER_HOST": "tcp://192.168.1.1:2376"}),
            patch("docker.from_env") as mock_from_env,
        ):
            _docker_client()
            mock_from_env.assert_called_once()

    def test_docker_client_falls_back_to_from_env(self) -> None:
        """When no user socket exists and DOCKER_HOST unset, use docker.from_env()."""
        from engine.deployers.docker_compose import _docker_client

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("pathlib.Path.exists", return_value=False),
            patch("docker.from_env") as mock_from_env,
        ):
            _docker_client()
            mock_from_env.assert_called_once()
