"""Local Docker Compose deployer.

Deploys agents to local Docker using the Docker SDK.
Each agent gets its own container with a unique port.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from engine.config_parser import AgentConfig
from engine.deployers.base import BaseDeployer, DeployResult, HealthStatus, InfraResult
from engine.runtimes.base import ContainerImage

logger = logging.getLogger(__name__)

GARDEN_DIR = Path.home() / ".garden"
STATE_FILE = GARDEN_DIR / "state.json"
BASE_PORT = 8080


class DockerComposeDeployer(BaseDeployer):
    """Deploys agents as local Docker containers."""

    def __init__(self) -> None:
        GARDEN_DIR.mkdir(parents=True, exist_ok=True)
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

    async def deploy(self, config: AgentConfig, image: ContainerImage) -> DeployResult:
        """Build the Docker image and run the container."""
        try:
            import docker
        except ImportError as e:
            msg = "Docker SDK not installed. Run: pip install docker"
            raise RuntimeError(msg) from e

        client = docker.from_env()
        agent_state = self._state.get("agents", {}).get(config.name, {})
        port = agent_state.get("port", self._allocate_port())

        # Build the image
        logger.info("Building Docker image: %s", image.tag)
        built_image, build_logs = client.images.build(
            path=str(image.context_dir),
            tag=image.tag,
            rm=True,
        )
        for chunk in build_logs:
            if "stream" in chunk:
                line = chunk["stream"].strip()
                if line:
                    logger.debug("  %s", line)

        # Stop existing container if any
        container_name = f"garden-{config.name}"
        try:
            existing = client.containers.get(container_name)
            logger.info("Stopping existing container: %s", container_name)
            existing.stop(timeout=10)
            existing.remove()
        except docker.errors.NotFound:
            pass

        # Run the container
        logger.info("Starting container: %s on port %d", container_name, port)
        container = client.containers.run(
            image.tag,
            name=container_name,
            ports={"8080/tcp": port},
            environment={
                "AGENT_NAME": config.name,
                "AGENT_VERSION": config.version,
                **config.deploy.env_vars,
            },
            detach=True,
            remove=False,
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

        client = docker.from_env()
        container_name = f"garden-{agent_name}"

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

        client = docker.from_env()
        container_name = f"garden-{agent_name}"

        try:
            container = client.containers.get(container_name)
            kwargs: dict = {"timestamps": True}
            if since:
                kwargs["since"] = since
            logs = container.logs(**kwargs).decode("utf-8")
            return logs.strip().splitlines()
        except docker.errors.NotFound:
            return [f"Container '{container_name}' not found"]
