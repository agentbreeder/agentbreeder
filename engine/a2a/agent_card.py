"""Auto-generate A2A Agent Card from AgentConfig."""

from __future__ import annotations

from typing import Any

from engine.config_parser import AgentConfig


def generate_agent_card(
    config: AgentConfig,
    endpoint_url: str,
) -> dict[str, Any]:
    """Generate an A2A Agent Card from an AgentConfig.

    The Agent Card is served at /.well-known/agent.json and describes
    the agent's capabilities for discovery by other agents.
    """
    skills = []
    for tool in config.tools:
        tool_name = tool.name or (tool.ref.split("/")[-1] if tool.ref else "unknown")
        skills.append(
            {
                "id": tool_name,
                "name": tool_name,
                "description": tool.description or f"Tool: {tool_name}",
                "input_modes": ["text"],
                "output_modes": ["text"],
            }
        )

    # If no tools, add a default "chat" skill
    if not skills:
        skills.append(
            {
                "id": "chat",
                "name": "chat",
                "description": config.description or f"Chat with {config.name}",
                "input_modes": ["text"],
                "output_modes": ["text"],
            }
        )

    return {
        "name": config.name,
        "description": config.description,
        "url": endpoint_url,
        "version": config.version,
        "capabilities": ["streaming", "tasks"],
        "skills": skills,
        "authentication": {"schemes": ["bearer"]},
        "default_input_modes": ["text"],
        "default_output_modes": ["text"],
    }
