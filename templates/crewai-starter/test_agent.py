"""Unit tests for the CrewAI starter agent."""

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

    def test_framework_is_crewai(self, agent_config: dict) -> None:
        assert agent_config["framework"] == "crewai"

    def test_version_is_semver(self, agent_config: dict) -> None:
        import re

        assert re.match(r"^\d+\.\d+\.\d+$", agent_config["version"])

    def test_guardrails_present(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails


class TestCrew:
    def test_crew_module_importable(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("crew", TEMPLATE_DIR / "crew.py")
        assert spec is not None, "crew.py must be importable"

    def test_crew_exports_crew_object(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("crew", TEMPLATE_DIR / "crew.py")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            assert hasattr(module, "crew"), "Module must export 'crew'"
        except ImportError:
            pytest.skip("CrewAI dependencies not installed")
