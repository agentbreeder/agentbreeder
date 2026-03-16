"""Unit tests for the email triage agent template."""

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

    def test_version_is_semver(self, agent_config: dict) -> None:
        import re

        assert re.match(r"^\d+\.\d+\.\d+$", agent_config["version"])

    def test_low_temperature(self, agent_config: dict) -> None:
        temp = agent_config["model"].get("temperature", 1.0)
        assert temp <= 0.3, "Classification needs low temperature"

    def test_guardrails_present(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails

    def test_classification_tool(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "classify_email" in names

    def test_routing_tool(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "route_email" in names

    def test_priority_tool(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "score_priority" in names

    def test_email_secrets_configured(self, agent_config: dict) -> None:
        secrets = agent_config["deploy"].get("secrets", [])
        assert "IMAP_HOST" in secrets or "IMAP_USER" in secrets

    def test_prompt_defines_categories(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        categories = ["support", "sales", "billing", "spam"]
        for cat in categories:
            assert cat in prompt.lower(), f"Missing category: {cat}"

    def test_prompt_defines_priority_levels(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "critical" in prompt.lower()
        assert "1-5" in prompt or "1 " in prompt
