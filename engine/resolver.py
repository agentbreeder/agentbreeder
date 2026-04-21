"""Dependency resolver.

Resolves registry references (ref: tools/zendesk-mcp) into concrete artifacts.
For v0.1 this is a stub — references are passed through unchanged.
Subagent refs are resolved into auto-generated tool definitions.
"""

from __future__ import annotations

import logging
import os

from engine.a2a.tool_generator import generate_subagent_tools
from engine.config_parser import AgentConfig, ToolRef

logger = logging.getLogger(__name__)


class ResolutionError(Exception):
    """Raised when a registry reference cannot be resolved."""


def resolve_dependencies(config: AgentConfig) -> AgentConfig:
    """Resolve all registry references in the config.

    - Tool and knowledge base refs are passed through (stub for v0.1).
    - Subagent refs are resolved into auto-generated call_{name} tools.
    - MCP server refs are passed through for sidecar deployment.
    """
    refs = []
    for tool in config.tools:
        if tool.ref:
            refs.append(tool.ref)
    for kb in config.knowledge_bases:
        refs.append(kb.ref)

    # Resolve subagent refs into auto-generated tools
    if config.subagents:
        subagent_tools = generate_subagent_tools(config.subagents)
        for tool_def in subagent_tools:
            config.tools.append(
                ToolRef(
                    name=tool_def["name"],
                    type=tool_def["type"],
                    description=tool_def["description"],
                    schema=tool_def["schema"],
                )
            )
            refs.append(f"subagent:{tool_def.get('_subagent_ref', '')}")
        logger.info(
            "Generated %d subagent tools: %s",
            len(subagent_tools),
            [t["name"] for t in subagent_tools],
        )

    # MCP server refs (pass through for sidecar deployment)
    for mcp in config.mcp_servers:
        refs.append(mcp.ref)

    # If NEO4J_URL is set in environment, inject it for any deployment with knowledge bases
    if config.knowledge_bases:
        neo4j_url = os.environ.get("NEO4J_URL")
        if neo4j_url and "NEO4J_URL" not in (config.deploy.env_vars or {}):
            if config.deploy.env_vars is None:
                config.deploy.env_vars = {}
            config.deploy.env_vars["NEO4J_URL"] = neo4j_url
            logger.debug("Injected NEO4J_URL for agent with knowledge bases")

    if refs:
        logger.debug("Dependency resolution — refs: %s", refs)

    return config
