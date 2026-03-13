"""MCP sidecar deployer — deploys MCP servers as sidecar containers.

When an agent references MCP servers in its config, this deployer
generates the sidecar container definitions and injects them
into the agent's deployment.
"""

from __future__ import annotations

import logging
from typing import Any

from engine.config_parser import McpServerRef
from engine.mcp.packager import build_image_tag, generate_sidecar_config

logger = logging.getLogger(__name__)


class McpSidecarDeployer:
    """Deploy MCP servers as sidecar containers alongside an agent."""

    def generate_sidecars(
        self,
        mcp_servers: list[McpServerRef],
        agent_name: str,
        registry_prefix: str = "agent-garden",
    ) -> list[dict[str, Any]]:
        """Generate sidecar configurations for all MCP server refs.

        Args:
            mcp_servers: List of MCP server references from agent.yaml.
            agent_name: Name of the parent agent (for labeling).
            registry_prefix: Docker registry prefix for image URIs.

        Returns:
            List of sidecar container configurations.
        """
        sidecars: list[dict[str, Any]] = []

        for i, mcp in enumerate(mcp_servers):
            # Extract server name from ref (e.g., "mcp/zendesk" -> "zendesk")
            server_name = mcp.ref.split("/")[-1]
            version = "latest"
            image_uri = build_image_tag(server_name, version, registry_prefix)

            sidecar = generate_sidecar_config(
                name=server_name,
                image_uri=image_uri,
                transport=mcp.transport,
                port=3000 + i,
            )
            sidecar["labels"] = {
                "agent-garden.agent": agent_name,
                "agent-garden.mcp-ref": mcp.ref,
            }
            sidecars.append(sidecar)
            logger.info(
                "Generated sidecar for MCP server '%s' (agent: %s)",
                server_name,
                agent_name,
            )

        return sidecars

    def inject_into_compose(
        self,
        compose_config: dict[str, Any],
        sidecars: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Inject sidecar services into a docker-compose configuration."""
        services = compose_config.setdefault("services", {})

        for sidecar in sidecars:
            service_name = sidecar["name"]
            services[service_name] = {
                "image": sidecar["image"],
                "environment": sidecar.get("environment", {}),
                "ports": [f"{sidecar['port']}:{sidecar['port']}"],
                "labels": sidecar.get("labels", {}),
                "restart": "unless-stopped",
            }

        return compose_config
