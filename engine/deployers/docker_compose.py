"""Local Docker Compose deployer.

Deploys agents to local Docker using the Docker SDK.
Each agent gets its own container with a unique port.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import httpx

from engine.config_parser import AgentConfig
from engine.deployers.base import BaseDeployer, DeployResult, HealthStatus, InfraResult
from engine.runtimes.base import ContainerImage

logger = logging.getLogger(__name__)


def _docker_client():
    """Return a Docker client, preferring the current user's socket on macOS."""
    import docker

    # If DOCKER_HOST is already set, honour it.
    if os.environ.get("DOCKER_HOST"):
        return docker.from_env()

    # On macOS, Docker Desktop creates a per-user socket that may differ from
    # the system symlink at /var/run/docker.sock (which can point to another
    # user's socket and cause PermissionError).
    user_socket = Path.home() / ".docker" / "run" / "docker.sock"
    if user_socket.exists():
        return docker.DockerClient(base_url=f"unix://{user_socket}")

    return docker.from_env()


AGENTBREEDER_DIR = Path.home() / ".agentbreeder"
STATE_FILE = AGENTBREEDER_DIR / "state.json"
BASE_PORT = 8080
OLLAMA_CONTAINER_NAME = "agentbreeder-ollama"
OLLAMA_NETWORK_NAME = "agentbreeder-net"


class DockerComposeDeployer(BaseDeployer):
    """Deploys agents as local Docker containers."""

    def __init__(self) -> None:
        AGENTBREEDER_DIR.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
        return {"agents": {}, "next_port": BASE_PORT}

    def _save_state(self) -> None:
        STATE_FILE.write_text(json.dumps(self._state, indent=2, default=str))

    def _allocate_port(self) -> int:
        port = self._state.get("next_port", BASE_PORT)
        self._state["next_port"] = port + 1
        self._save_state()
        return port

    async def _ensure_network(self, client: object) -> None:
        """Create agentbreeder-net bridge network if it doesn't already exist."""
        import docker

        try:
            client.networks.get(OLLAMA_NETWORK_NAME)  # type: ignore[attr-defined]
        except docker.errors.NotFound:
            client.networks.create(OLLAMA_NETWORK_NAME, driver="bridge")  # type: ignore[attr-defined]
            logger.info("Created Docker network: %s", OLLAMA_NETWORK_NAME)

    async def _ensure_ollama_sidecar(self, client: object) -> None:
        """Start the Ollama sidecar container if it is not already running."""
        import docker

        try:
            container = client.containers.get(OLLAMA_CONTAINER_NAME)  # type: ignore[attr-defined]
            if container.status == "running":
                logger.info("Ollama sidecar already running: %s", OLLAMA_CONTAINER_NAME)
                return
            logger.info("Restarting stopped Ollama container: %s", OLLAMA_CONTAINER_NAME)
            container.start()
        except docker.errors.NotFound:
            logger.info("Starting Ollama sidecar container...")
            client.containers.run(  # type: ignore[attr-defined]
                "ollama/ollama",
                name=OLLAMA_CONTAINER_NAME,
                ports={"11434/tcp": 11434},
                volumes={"ollama_data": {"bind": "/root/.ollama", "mode": "rw"}},
                network=OLLAMA_NETWORK_NAME,
                detach=True,
                remove=False,
            )
            logger.info("Ollama sidecar started: %s", OLLAMA_CONTAINER_NAME)

    async def _pull_ollama_model(self, client: object, model_tag: str) -> None:
        """Pull the Ollama model inside the sidecar via docker exec."""
        logger.info("Pulling Ollama model: %s (this may take several minutes)", model_tag)
        container = client.containers.get(OLLAMA_CONTAINER_NAME)  # type: ignore[attr-defined]
        for _ in range(30):
            exit_code, _ = container.exec_run(["ollama", "list"])
            if exit_code == 0:
                break
            await asyncio.sleep(1)
        exit_code, output = container.exec_run(["ollama", "pull", model_tag], stream=False)
        if exit_code != 0:
            logger.warning(
                "ollama pull %s exited %d: %s",
                model_tag,
                exit_code,
                output.decode(errors="replace"),
            )
        else:
            logger.info("Pulled Ollama model: %s", model_tag)

    async def provision(self, config: AgentConfig) -> InfraResult:
        port = self._allocate_port()
        endpoint_url = f"http://localhost:{port}"

        self._state.setdefault("agents", {})[config.name] = {
            "port": port,
            "endpoint_url": endpoint_url,
            "status": "provisioned",
            "deployed_at": datetime.now().isoformat(),
        }
        self._save_state()

        return InfraResult(
            endpoint_url=endpoint_url,
            resource_ids={"port": str(port)},
        )

    async def deploy(self, config: AgentConfig, image: ContainerImage | None) -> DeployResult:
        """Build the Docker image and run the container."""
        try:
            import docker
        except ImportError as e:
            msg = "Docker SDK not installed. Run: pip install docker"
            raise RuntimeError(msg) from e

        client = _docker_client()
        assert image is not None, "ContainerImage required for Docker Compose deployer"
        agent_state = self._state.get("agents", {}).get(config.name, {})
        port = agent_state.get("port", self._allocate_port())

        # Build via the CLI so BuildKit output is handled correctly.
        # The Python Docker SDK misparses BuildKit log format and fails to
        # resolve the image ID after a successful build on modern Docker Desktop.
        import subprocess as _sp

        logger.info("Building Docker image: %s", image.tag)
        result = _sp.run(
            ["docker", "build", "-t", image.tag, str(image.context_dir)],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            logger.debug("  %s", line)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker build failed (exit {result.returncode}):\n{result.stderr}"
            )

        # Stop existing container if any
        container_name = f"agentbreeder-{config.name}"
        try:
            existing = client.containers.get(container_name)
            logger.info("Stopping existing container: %s", container_name)
            existing.stop(timeout=10)
            existing.remove()
        except docker.errors.NotFound:
            pass

        # Build container environment — inject platform defaults first so
        # user env_vars can override them if needed.
        import os as _os

        container_env: dict[str, str] = {
            "AGENT_NAME": config.name,
            "AGENT_VERSION": config.version,
            "AGENT_FRAMEWORK": config.framework.value,
            "AGENT_MODEL": config.model.primary,
        }
        if otel := _os.getenv("OPENTELEMETRY_ENDPOINT"):
            container_env["OPENTELEMETRY_ENDPOINT"] = otel
        container_env.update(config.deploy.env_vars)

        # For ollama/ models: start Ollama sidecar and pull model weights before the agent
        if config.model.primary.startswith("ollama/"):
            await self._ensure_network(client)
            await self._ensure_ollama_sidecar(client)
            model_tag = config.model.primary.split("/", 1)[1]  # e.g. "gemma3:27b"
            await self._pull_ollama_model(client, model_tag)
            container_env["OLLAMA_BASE_URL"] = f"http://{OLLAMA_CONTAINER_NAME}:11434"

        # Run the container
        logger.info("Starting container: %s on port %d", container_name, port)
        run_kwargs: dict = {
            "name": container_name,
            "ports": {"8080/tcp": port},
            "environment": container_env,
            "detach": True,
            "remove": False,
        }
        if config.model.primary.startswith("ollama/"):
            run_kwargs["network"] = OLLAMA_NETWORK_NAME
        container = client.containers.run(image.tag, **run_kwargs)

        # Give the container a moment to start, then check it didn't exit immediately.
        await asyncio.sleep(3)
        container.reload()
        if container.status != "running":
            logs = container.logs().decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Container exited immediately (status: {container.status}).\n\n"
                f"Container logs:\n{logs or '(no output)'}\n\n"
                f"Tip: if the error mentions a missing API key, add it to your .env file and redeploy."
            )

        endpoint_url = f"http://localhost:{port}"
        self._state["agents"][config.name] = {
            "port": port,
            "endpoint_url": endpoint_url,
            "container_id": container.id,
            "container_name": container_name,
            "image_tag": image.tag,
            "status": "running",
            "deployed_at": datetime.now().isoformat(),
        }
        self._save_state()

        return DeployResult(
            endpoint_url=endpoint_url,
            container_id=container.id,
            status="running",
            agent_name=config.name,
            version=config.version,
        )

    async def health_check(
        self, deploy_result: DeployResult, timeout: int = 60, interval: int = 2
    ) -> HealthStatus:
        """Poll the health endpoint until the agent is ready."""
        url = f"{deploy_result.endpoint_url}/health"
        checks: dict[str, bool] = {"reachable": False, "healthy": False}

        for attempt in range(timeout // interval):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=5.0)
                    checks["reachable"] = True
                    if response.status_code == 200:
                        checks["healthy"] = True
                        logger.info(
                            "Health check passed (attempt %d/%d)",
                            attempt + 1,
                            timeout // interval,
                        )
                        return HealthStatus(healthy=True, checks=checks)
            except (httpx.ConnectError, httpx.ReadTimeout, OSError, ExceptionGroup):
                pass

            logger.debug(
                "Health check attempt %d/%d — waiting...", attempt + 1, timeout // interval
            )
            await asyncio.sleep(interval)

        logger.warning("Health check failed after %d seconds", timeout)
        return HealthStatus(healthy=False, checks=checks)

    async def teardown(self, agent_name: str) -> None:
        """Stop and remove the agent container."""
        try:
            import docker
        except ImportError as e:
            msg = "Docker SDK not installed. Run: pip install docker"
            raise RuntimeError(msg) from e

        client = _docker_client()
        container_name = f"agentbreeder-{agent_name}"

        try:
            container = client.containers.get(container_name)
            container.stop(timeout=10)
            container.remove()
            logger.info("Removed container: %s", container_name)
        except docker.errors.NotFound:
            logger.warning("Container not found: %s", container_name)

        # Update state
        agents = self._state.get("agents", {})
        if agent_name in agents:
            agents[agent_name]["status"] = "stopped"
            self._save_state()

    async def get_logs(self, agent_name: str, since: datetime | None = None) -> list[str]:
        """Get logs from the agent container."""
        try:
            import docker
        except ImportError as e:
            msg = "Docker SDK not installed. Run: pip install docker"
            raise RuntimeError(msg) from e

        client = _docker_client()
        container_name = f"agentbreeder-{agent_name}"

        try:
            container = client.containers.get(container_name)
            kwargs: dict = {"timestamps": True}
            if since:
                kwargs["since"] = since
            logs = container.logs(**kwargs).decode("utf-8")
            return logs.strip().splitlines()
        except docker.errors.NotFound:
            return [f"Container '{container_name}' not found"]
