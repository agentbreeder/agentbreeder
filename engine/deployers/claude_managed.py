"""Claude Managed Agents deployer.

Instead of building and pushing a container image, this deployer:
1. Creates an Anthropic Managed Agent via POST /v1/agents
2. Creates an Anthropic Environment via POST /v1/environments
3. Returns anthropic://agents/{agent_id}?env={env_id} as the endpoint URL

No container build step occurs — engine/builder.py skips the build phase
when config.deploy.cloud == "claude-managed".

Sessions are created on demand by `agentbreeder chat` when it detects the
anthropic:// URL scheme.

Beta header: anthropic-beta: managed-agents-2026-04-01
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from engine.config_parser import AgentConfig
from engine.deployers.base import BaseDeployer, DeployResult, HealthStatus, InfraResult
from engine.runtimes.base import ContainerImage

logger = logging.getLogger(__name__)

BETA_HEADER = "managed-agents-2026-04-01"


def _get_anthropic_client() -> Any:
    """Get the Anthropic client with managed-agents beta capabilities.

    Raises ImportError with install instructions if the SDK is missing.
    """
    try:
        from anthropic import Anthropic
    except ImportError as e:
        msg = "anthropic SDK is not installed. Run: pip install anthropic"
        raise ImportError(msg) from e

    return Anthropic()


def _build_anthropic_endpoint(agent_id: str, environment_id: str) -> str:
    """Build the anthropic:// endpoint URL from agent and environment IDs."""
    return f"anthropic://agents/{agent_id}?env={environment_id}"


class ClaudeManagedDeployer(BaseDeployer):
    """Deploys agents to Anthropic Claude Managed Agents infrastructure.

    No container image is built or pushed. The agent.yaml system prompt,
    model, and tools are mapped directly to the Anthropic Agent API.
    """

    def __init__(self) -> None:
        self._agent_id: str | None = None
        self._environment_id: str | None = None

    def _resolve_system_prompt(self, config: AgentConfig) -> str:
        """Get the system prompt string from the prompts config.

        Supports inline strings only. Registry ref resolution is handled
        by engine/resolver.py before the deployer is called.
        """
        if config.prompts and config.prompts.system:
            return str(config.prompts.system)
        return f"You are {config.name}, an AI agent. {config.description or ''}"

    async def provision(self, config: AgentConfig) -> InfraResult:
        """Create the Anthropic Agent and Environment definitions.

        This is the entire "deployment" for Claude Managed Agents —
        no infrastructure provisioning occurs beyond these two API calls.
        """
        try:
            client = _get_anthropic_client()
        except ImportError as err:
            raise ImportError(
                "anthropic SDK is not installed. Run: pip install anthropic"
            ) from err

        cm_config = config.claude_managed
        tools = (
            [{"type": t.type} for t in cm_config.tools]
            if cm_config
            else [{"type": "agent_toolset_20260401"}]
        )
        networking_type = cm_config.environment.networking if cm_config else "unrestricted"
        system_prompt = self._resolve_system_prompt(config)

        logger.info(
            "Creating Anthropic Managed Agent '%s' with model '%s'",
            config.name,
            config.model.primary,
        )

        agent = await client.beta.agents.create(
            name=config.name,
            model=config.model.primary,
            system=system_prompt,
            tools=tools,
        )
        self._agent_id = agent.id
        logger.info("Created Anthropic Agent: %s (version %s)", agent.id, agent.version)

        environment = await client.beta.environments.create(
            name=f"{config.name}-env",
            config={
                "type": "cloud",
                "networking": {"type": networking_type},
            },
        )
        self._environment_id = environment.id
        logger.info("Created Anthropic Environment: %s", environment.id)

        endpoint_url = _build_anthropic_endpoint(self._agent_id, self._environment_id)

        return InfraResult(
            endpoint_url=endpoint_url,
            resource_ids={
                "agent_id": self._agent_id,
                "environment_id": self._environment_id,
                "agent_version": str(agent.version),
            },
        )

    async def deploy(self, config: AgentConfig, image: ContainerImage) -> DeployResult:
        """Return the anthropic:// endpoint. Container image is ignored.

        provision() must be called first to create the agent and environment.
        """
        if self._agent_id is None or self._environment_id is None:
            msg = (
                "Cannot deploy: provision() must be called first to create the "
                "Anthropic Agent and Environment."
            )
            raise RuntimeError(msg)

        endpoint_url = _build_anthropic_endpoint(self._agent_id, self._environment_id)

        logger.info("Claude Managed Agent deployed: %s → %s", config.name, endpoint_url)

        return DeployResult(
            endpoint_url=endpoint_url,
            container_id=self._agent_id,
            status="running",
            agent_name=config.name,
            version=config.version,
        )

    async def health_check(
        self,
        deploy_result: DeployResult,
        timeout: int = 30,
        interval: int = 5,
    ) -> HealthStatus:
        """Claude Managed Agents are fully managed by Anthropic.

        Health is assumed — if the agent was created successfully, it is available.
        """
        return HealthStatus(
            healthy=True,
            checks={"managed_by_anthropic": True},
        )

    async def teardown(self, agent_name: str) -> None:
        """Delete the Anthropic Agent and Environment definitions."""
        client = _get_anthropic_client()

        if self._agent_id:
            logger.info("Deleting Anthropic Agent: %s", self._agent_id)
            await client.beta.agents.delete(self._agent_id)
            logger.info("Deleted Anthropic Agent: %s", self._agent_id)

        if self._environment_id:
            logger.info("Deleting Anthropic Environment: %s", self._environment_id)
            await client.beta.environments.delete(self._environment_id)
            logger.info("Deleted Anthropic Environment: %s", self._environment_id)

    async def get_logs(self, agent_name: str, since: datetime | None = None) -> list[str]:
        """Logs for Claude Managed Agents live inside individual sessions.

        Use `agentbreeder chat` to start a session and view its event stream.
        There is no persistent log group — Anthropic manages session history
        server-side, accessible via the sessions API.
        """
        agent_id = self._agent_id or "(unknown)"
        return [
            f"Logs for Claude Managed Agent '{agent_name}' (ID: {agent_id}) are "
            "available per-session via the Anthropic sessions API. "
            f"Use `agentbreeder chat {agent_name}` to start a session and stream events."
        ]

    async def get_url(self, agent_name: str) -> str:
        """Return the anthropic:// endpoint for this agent."""
        if self._agent_id is None or self._environment_id is None:
            msg = "Cannot get URL: agent not yet provisioned."
            raise RuntimeError(msg)
        return _build_anthropic_endpoint(self._agent_id, self._environment_id)

    async def status(self, agent_name: str) -> dict[str, Any]:
        """Get status of the Anthropic Agent definition."""
        if self._agent_id is None:
            return {"name": agent_name, "status": "not_provisioned"}

        try:
            client = _get_anthropic_client()
            agent = await client.beta.agents.retrieve(self._agent_id)
            return {
                "name": agent_name,
                "agent_id": self._agent_id,
                "environment_id": self._environment_id,
                "status": "running",
                "model": getattr(agent, "model", "unknown"),
                "version": getattr(agent, "version", "unknown"),
            }
        except Exception as exc:
            return {"name": agent_name, "status": "error", "error": str(exc)}
