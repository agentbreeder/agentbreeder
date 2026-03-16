"""Unit tests for the sales qualification agent template."""

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

    def test_crm_tools_configured(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "search_crm" in names
        assert "update_lead" in names
        assert "score_lead" in names

    def test_outreach_tool_configured(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "draft_outreach" in names

    def test_crm_secrets_configured(self, agent_config: dict) -> None:
        secrets = agent_config["deploy"].get("secrets", [])
        assert "CRM_API_KEY" in secrets

    def test_auto_outreach_disabled_by_default(self, agent_config: dict) -> None:
        env_vars = agent_config["deploy"].get("env_vars", {})
        assert env_vars.get("AUTO_OUTREACH_ENABLED") == "false", (
            "Auto outreach should be disabled by default for safety"
        )

    def test_prompt_has_scoring_framework(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "BANT" in prompt or "budget" in prompt.lower()
        assert "0-100" in prompt or "score" in prompt.lower()

    def test_prompt_has_outreach_guidelines(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "personalize" in prompt.lower() or "personalized" in prompt.lower()
