"""Claude / Codex coding-agent strategies over the shared provider loop.

Each engine is a thin strategy: it owns the model id + system prompt and hands
an injected provider to run_coding_loop(). The provider is constructed by the
API layer from the BYO key in the secrets backend (never here).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from engine.coding_agent.base import AgentBounds, AgentEvent
from engine.coding_agent.loop import run_coding_loop
from engine.sandbox.base import Sandbox

_CODING_SYSTEM_PROMPT = """\
You are AgentBreeder's coding agent. You are ejecting a validated agent.yaml into
real code. Write agent.py, any tools/ modules, and tests into the workspace using
the provided tools. Keep the code framework-correct for the chosen framework, runnable,
and covered by at least one test. Use write_file to create files, run_command to run
tests, and stop when the project is complete. Never print secrets."""


class _BaseEngine:
    name: str = ""
    model: str = ""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    def run(
        self,
        instruction: str,
        history: list[dict[str, str]],
        sandbox: Sandbox,
        bounds: AgentBounds | None = None,
    ) -> AsyncIterator[AgentEvent]:
        return run_coding_loop(
            provider=self._provider,
            model=self.model,
            system_prompt=_CODING_SYSTEM_PROMPT,
            instruction=instruction,
            history=history,
            sandbox=sandbox,
            bounds=bounds,
        )


class ClaudeAgentEngine(_BaseEngine):
    name = "claude"
    model = "claude-sonnet-4-6"


class CodexEngine(_BaseEngine):
    name = "codex"
    model = "gpt-4o"


def engine_for(name: str, *, provider: Any) -> _BaseEngine:
    if name == "claude":
        return ClaudeAgentEngine(provider)
    if name == "codex":
        return CodexEngine(provider)
    raise ValueError(f"unknown coding engine: {name!r} (expected 'claude' or 'codex')")
