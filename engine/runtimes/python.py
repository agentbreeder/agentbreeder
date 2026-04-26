"""Python runtime family — dispatches to per-framework runtime builders."""

from __future__ import annotations

from engine.config_parser import FrameworkType
from engine.runtimes.base import RuntimeBuilder
from engine.runtimes.claude_sdk import ClaudeSDKRuntime
from engine.runtimes.crewai import CrewAIRuntime
from engine.runtimes.custom import CustomRuntime
from engine.runtimes.google_adk import GoogleADKRuntime
from engine.runtimes.langgraph import LangGraphRuntime
from engine.runtimes.openai_agents import OpenAIAgentsRuntime

_BUILDERS: dict[FrameworkType, type[RuntimeBuilder]] = {
    FrameworkType.langgraph: LangGraphRuntime,
    FrameworkType.crewai: CrewAIRuntime,
    FrameworkType.claude_sdk: ClaudeSDKRuntime,
    FrameworkType.openai_agents: OpenAIAgentsRuntime,
    FrameworkType.google_adk: GoogleADKRuntime,
    FrameworkType.custom: CustomRuntime,
}


class PythonRuntimeFamily:
    """Factory for Python framework runtime builders."""

    @classmethod
    def from_framework(cls, framework: FrameworkType | None) -> RuntimeBuilder:
        if framework is None:
            raise ValueError("framework must be set for Python agents")
        builder_cls = _BUILDERS.get(framework)
        if builder_cls is None:
            raise KeyError(f"Unsupported Python framework: {framework!r}")
        return builder_cls()
