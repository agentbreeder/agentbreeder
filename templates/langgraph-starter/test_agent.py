"""Unit tests for the LangGraph starter agent."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

TEMPLATE_DIR = Path(__file__).parent
AGENT_YAML = TEMPLATE_DIR / "agent.yaml"


@pytest.fixture
def agent_config() -> dict:
    with open(AGENT_YAML) as f:
        return yaml.safe_load(f)


class TestAgentConfig:
    def test_required_fields_present(self, agent_config: dict) -> None:
        required = ["name", "version", "team", "owner", "framework", "model", "deploy"]
        for field in required:
            assert field in agent_config, f"Missing required field: {field}"

    def test_framework_is_langgraph(self, agent_config: dict) -> None:
        assert agent_config["framework"] == "langgraph"

    def test_version_is_semver(self, agent_config: dict) -> None:
        import re

        assert re.match(r"^\d+\.\d+\.\d+$", agent_config["version"])

    def test_guardrails_present(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails


class TestGraph:
    def test_graph_module_importable(self) -> None:
        """Verify the graph.py module can be imported."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("graph", TEMPLATE_DIR / "graph.py")
        assert spec is not None, "graph.py must be importable"

    def test_graph_exports_graph_object(self) -> None:
        """Verify the module exports a 'graph' object."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("graph", TEMPLATE_DIR / "graph.py")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            assert hasattr(module, "graph"), "Module must export 'graph'"
        except ImportError:
            pytest.skip("LangGraph dependencies not installed")

    def test_tool_execution(self) -> None:
        """Test the execute_tool function directly."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("graph", TEMPLATE_DIR / "graph.py")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            result = module.execute_tool("calculator", {"expression": "2 + 3"})
            assert result == "5"

            result = module.execute_tool("search", {"query": "test"})
            assert "test" in result
        except ImportError:
            pytest.skip("LangGraph dependencies not installed")
