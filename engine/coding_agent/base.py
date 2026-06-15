"""Coding-agent contracts: events, bounds, the engine protocol, and the
sandbox-scoped tool surface (write/read/list/exec) shared by all engines."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from engine.providers.models import ToolDefinition, ToolFunction
from engine.sandbox.base import Sandbox

AgentEventType = Literal["token", "tool_call", "file_change", "done", "error"]


@dataclass
class AgentEvent:
    """One event emitted by a coding-agent run."""

    type: AgentEventType
    text: str = ""  # token text / done summary
    tool_name: str = ""  # for tool_call
    path: str = ""  # for file_change
    diff: str = ""  # unified diff for file_change
    error: str = ""  # for error


@dataclass
class AgentBounds:
    """Hard bounds on a coding-agent run."""

    max_turns: int = 12
    max_tokens: int = 200_000
    wall_clock_s: float = 180.0


def _tool(name: str, description: str, params: dict[str, Any]) -> ToolDefinition:
    return ToolDefinition(
        type="function",
        function=ToolFunction(name=name, description=description, parameters=params),
    )


CODING_TOOLS: list[ToolDefinition] = [
    _tool(
        "write_file",
        "Create or overwrite a file in the agent project workspace.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative path."},
                "content": {"type": "string", "description": "Full file contents."},
            },
            "required": ["path", "content"],
        },
    ),
    _tool(
        "read_file",
        "Read a file from the workspace.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ),
    _tool(
        "list_files",
        "List files in the workspace (recursively).",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"directory": {"type": "string", "default": "."}},
        },
    ),
    _tool(
        "run_command",
        "Run a shell command in the workspace (e.g. run tests). Bounded by a timeout.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "cmd": {"type": "array", "items": {"type": "string"}},
                "timeout": {"type": "number", "default": 30},
            },
            "required": ["cmd"],
        },
    ),
]

TOOL_NAMES: set[str] = {t.function.name for t in CODING_TOOLS}


class CodingAgentEngine(Protocol):
    """Strategy: which provider+model+system-prompt drives the shared loop."""

    name: str

    def run(
        self,
        instruction: str,
        history: list[dict[str, str]],
        sandbox: Sandbox,
        bounds: AgentBounds | None = None,
    ) -> AsyncIterator[AgentEvent]: ...
