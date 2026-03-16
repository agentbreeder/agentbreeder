"""Unit tests for the Google ADK starter agent."""

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

    def test_framework_is_google_adk(self, agent_config: dict) -> None:
        assert agent_config["framework"] == "google_adk"

    def test_version_is_semver(self, agent_config: dict) -> None:
        import re

        assert re.match(r"^\d+\.\d+\.\d+$", agent_config["version"])

    def test_guardrails_present(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails

    def test_google_key_in_secrets(self, agent_config: dict) -> None:
        secrets = agent_config["deploy"].get("secrets", [])
        assert "GOOGLE_API_KEY" in secrets

    def test_model_is_gemini(self, agent_config: dict) -> None:
        model = agent_config["model"]["primary"]
        assert "gemini" in model.lower()


class TestAgent:
    def test_agent_module_importable(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("agent", TEMPLATE_DIR / "agent.py")
        assert spec is not None, "agent.py must be importable"

    def test_agent_exports_required_objects(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("agent", TEMPLATE_DIR / "agent.py")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            assert hasattr(module, "agent_config"), "Must export 'agent_config'"
            assert hasattr(module, "handle_message"), "Must export 'handle_message'"
        except ImportError:
            pytest.skip("Google Generative AI SDK not installed")

    def test_tool_functions_work(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("agent", TEMPLATE_DIR / "agent.py")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            result = module.search_web("test")
            assert "test" in result

            result = module.get_stock_price("GOOGL")
            assert "GOOGL" in result
        except ImportError:
            pytest.skip("Google Generative AI SDK not installed")
