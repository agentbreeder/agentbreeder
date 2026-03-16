"""Unit tests for the Slack bot agent template."""

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

    def test_guardrails_present(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails
        assert "content_filter" in guardrails

    def test_slack_tool_configured(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        refs = [t.get("ref", "") for t in tools]
        assert any("slack" in r for r in refs), "Must have Slack tool"

    def test_slack_secrets_configured(self, agent_config: dict) -> None:
        secrets = agent_config["deploy"].get("secrets", [])
        assert "SLACK_BOT_TOKEN" in secrets
        assert "SLACK_SIGNING_SECRET" in secrets

    def test_moderate_temperature(self, agent_config: dict) -> None:
        temp = agent_config["model"].get("temperature", 1.0)
        assert 0.3 <= temp <= 0.7, "Slack bot needs balanced temperature"

    def test_concise_max_tokens(self, agent_config: dict) -> None:
        max_tokens = agent_config["model"].get("max_tokens", 0)
        assert max_tokens <= 2048, "Slack messages should be concise"

    def test_prompt_mentions_threading(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "thread" in prompt.lower(), "Slack bot must handle threads"

    def test_prompt_mentions_slack_formatting(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "bold" in prompt.lower() or "formatting" in prompt.lower()
