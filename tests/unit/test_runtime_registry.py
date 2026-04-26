"""Tests for the language-based runtime registry."""

from __future__ import annotations

from engine.config_parser import AgentConfig, FrameworkType, LanguageType, RuntimeConfig


def _make_python_config(framework: str = "langgraph") -> AgentConfig:
    return AgentConfig(
        name="test-agent",
        version="1.0.0",
        team="eng",
        owner="test@example.com",
        framework=FrameworkType(framework),
        model={"primary": "gpt-4o"},
        deploy={"cloud": "local"},
    )


def _make_node_config(framework: str = "vercel-ai") -> AgentConfig:
    return AgentConfig(
        name="test-agent",
        version="1.0.0",
        team="eng",
        owner="test@example.com",
        runtime=RuntimeConfig(language=LanguageType.node, framework=framework),
        model={"primary": "gpt-4o"},
        deploy={"cloud": "local"},
    )


class TestGetRuntimeFromConfig:
    def test_python_langgraph_routes_to_langgraph_runtime(self) -> None:
        from engine.runtimes.langgraph import LangGraphRuntime
        from engine.runtimes.registry import get_runtime_from_config

        runtime = get_runtime_from_config(_make_python_config("langgraph"))
        assert isinstance(runtime, LangGraphRuntime)

    def test_python_crewai_routes_to_crewai_runtime(self) -> None:
        from engine.runtimes.crewai import CrewAIRuntime
        from engine.runtimes.registry import get_runtime_from_config

        runtime = get_runtime_from_config(_make_python_config("crewai"))
        assert isinstance(runtime, CrewAIRuntime)

    def test_node_routes_to_node_runtime_family(self) -> None:
        from engine.runtimes.node import NodeRuntimeFamily
        from engine.runtimes.registry import get_runtime_from_config

        runtime = get_runtime_from_config(_make_node_config())
        assert isinstance(runtime, NodeRuntimeFamily)

    def test_truly_unsupported_language_not_in_registry(self) -> None:
        from engine.runtimes.registry import LANGUAGE_REGISTRY

        assert "rust" not in LANGUAGE_REGISTRY
        assert "go" not in LANGUAGE_REGISTRY
