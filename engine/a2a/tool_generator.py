"""Auto-generate callable tools from subagent references.

When an agent.yaml declares subagents, this module generates
call_{name} tool definitions that can be used by the agent framework
to invoke subagents via A2A protocol.
"""

from __future__ import annotations

from typing import Any

from engine.config_parser import SubagentRef


def generate_subagent_tools(
    subagents: list[SubagentRef],
) -> list[dict[str, Any]]:
    """Generate tool definitions for each subagent reference.

    Each subagent gets a call_{name} tool that:
    - Takes 'message' (str) and optional 'context' (dict) as input
    - Returns the subagent's response

    These tool definitions are injected into the agent's tool list
    during the deploy pipeline's dependency resolution step.
    """
    tools: list[dict[str, Any]] = []

    for sub in subagents:
        slug = sub.slug
        desc = sub.description or f"Call the {slug} subagent"
        tool_name = f"call_{slug.replace('-', '_')}"

        tools.append(
            {
                "name": tool_name,
                "type": "function",
                "description": desc,
                "schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message to send to the subagent",
                        },
                        "context": {
                            "type": "object",
                            "description": "Optional context data",
                            "default": {},
                        },
                    },
                    "required": ["message"],
                },
                "_subagent_ref": sub.ref,
            }
        )

    return tools
