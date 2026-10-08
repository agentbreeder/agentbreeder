"""Tests for engine/resolver.py — dependency resolution."""

from __future__ import annotations

from engine.config_parser import AgentConfig, FrameworkType
from engine.resolver import resolve_dependencies


def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "test",
        "owner": "test@example.com",
        "framework": FrameworkType.langgraph,
        "model": {"primary": "gpt-4o"},
        "deploy": {"cloud": "local"},
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


class TestResolveDependencies:
    def test_stub_returns_config_unchanged(self) -> None:
        """v0.1 resolver stub should return config unchanged."""
        config = _make_config()
        resolved = resolve_dependencies(config)
        assert resolved.name == config.name
        assert resolved.version == config.version

    def test_with_tool_refs(self) -> None:
        config = _make_config(tools=[{"ref": "tools/zendesk"}, {"ref": "tools/search"}])
        resolved = resolve_dependencies(config)
        assert len(resolved.tools) == 2
        assert resolved.tools[0].ref == "tools/zendesk"

    def test_no_refs_passes(self) -> None:
        config = _make_config()
        resolved = resolve_dependencies(config)
        assert resolved.tools == []
