"""Unit tests for NodeRuntimeFamily."""

from __future__ import annotations

import json
from pathlib import Path

from engine.config_parser import AgentConfig, RuntimeConfig
from engine.runtimes.node import NodeRuntimeFamily, _substitute_placeholders


def _make_node_config(framework: str = "vercel-ai") -> AgentConfig:
    return AgentConfig(
        name="test-agent",
        version="1.0.0",
        team="eng",
        owner="test@test.com",
        runtime=RuntimeConfig(language="node", framework=framework),
        model={"primary": "gpt-4o"},
        deploy={"cloud": "local"},
    )


class TestNodeRuntimeFamily:
    def test_build_returns_container_image_for_vercel_ai(self, tmp_path: Path) -> None:
        (tmp_path / "agent.ts").write_text(
            "export const model = null; export const systemPrompt = ''; export const tools = {}"
        )
        config = _make_node_config("vercel-ai")
        runtime = NodeRuntimeFamily()
        image = runtime.build(tmp_path, config)
        assert image.tag == "agentbreeder/test-agent:1.0.0"
        assert (image.context_dir / "server.ts").exists()
        assert (image.context_dir / "Dockerfile").exists()
        assert (image.context_dir / "package.json").exists()

    def test_build_includes_framework_deps_in_package_json(self, tmp_path: Path) -> None:
        (tmp_path / "agent.ts").write_text("export const handler = async (input: string) => input")
        config = _make_node_config("vercel-ai")
        runtime = NodeRuntimeFamily()
        image = runtime.build(tmp_path, config)
        pkg = json.loads((image.context_dir / "package.json").read_text())
        assert (image.context_dir / "aps-client.ts").exists()
        assert "ai" in pkg["dependencies"]

    def test_unknown_framework_falls_back_to_custom(self, tmp_path: Path) -> None:
        (tmp_path / "agent.ts").write_text("export const handler = async (input: string) => input")
        config = _make_node_config("some-unknown-framework")
        runtime = NodeRuntimeFamily()
        image = runtime.build(tmp_path, config)
        # Should succeed (no exception) and produce a server.ts from custom_node_server.ts
        server_ts = (image.context_dir / "server.ts").read_text()
        assert "handler" in server_ts  # custom template imports handler

    def test_placeholder_substitution(self) -> None:
        template = "Agent: {{AGENT_NAME}} v{{AGENT_VERSION}} ({{AGENT_FRAMEWORK}}) on :{{PORT}}"
        config = _make_node_config("vercel-ai")
        result = _substitute_placeholders(template, config, port="3000")
        assert result == "Agent: test-agent v1.0.0 (vercel-ai) on :3000"

    def test_validate_fails_when_agent_ts_missing(self, tmp_path: Path) -> None:
        config = _make_node_config("vercel-ai")
        runtime = NodeRuntimeFamily()
        result = runtime.validate(tmp_path, config)
        assert not result.valid
        assert any("agent.ts" in e for e in result.errors)

    def test_validate_passes_when_agent_ts_exists(self, tmp_path: Path) -> None:
        (tmp_path / "agent.ts").write_text("export const handler = async (input: string) => input")
        config = _make_node_config("vercel-ai")
        runtime = NodeRuntimeFamily()
        result = runtime.validate(tmp_path, config)
        assert result.valid

    def test_mcp_ts_template_selected_for_mcp_ts_framework(self, tmp_path: Path) -> None:
        (tmp_path / "tools.ts").write_text(
            "export async function search({ query }: { query: string }) { return query }"
        )
        config = _make_node_config("mcp-ts")
        runtime = NodeRuntimeFamily()
        # Validate uses 'agent.ts' by default — but MCP servers use 'tools.ts'.
        # For this test just check build works without agent.ts
        image = runtime.build(tmp_path, config)
        server_ts = (image.context_dir / "server.ts").read_text()
        assert "tools/list" in server_ts or "tools/call" in server_ts

    def test_node_registered_in_language_registry(self) -> None:
        from engine.runtimes.registry import LANGUAGE_REGISTRY

        assert "node" in LANGUAGE_REGISTRY


class TestLanguageRegistry:
    def test_get_runtime_from_config_routes_node_to_node_family(self) -> None:
        from engine.runtimes.registry import get_runtime_from_config

        config = _make_node_config("vercel-ai")
        runtime = get_runtime_from_config(config)
        assert isinstance(runtime, NodeRuntimeFamily)
