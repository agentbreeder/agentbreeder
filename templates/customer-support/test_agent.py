"""Unit tests for the customer support agent template.

Validates configuration, guardrails, and escalation logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

TEMPLATE_DIR = Path(__file__).parent
AGENT_YAML = TEMPLATE_DIR / "agent.yaml"


@pytest.fixture
def agent_config() -> dict:
    """Load and return the agent.yaml configuration."""
    with open(AGENT_YAML) as f:
        return yaml.safe_load(f)


class TestAgentConfig:
    """Validate the agent.yaml is well-formed and production-ready."""

    def test_required_fields_present(self, agent_config: dict) -> None:
        required = ["name", "version", "team", "owner", "framework", "model", "deploy"]
        for field in required:
            assert field in agent_config, f"Missing required field: {field}"

    def test_name_is_slug_friendly(self, agent_config: dict) -> None:
        name = agent_config["name"]
        assert name == name.lower(), "Name must be lowercase"
        assert " " not in name, "Name must not contain spaces"
        assert name.replace("-", "").isalnum(), "Name must be alphanumeric with hyphens"

    def test_version_is_semver(self, agent_config: dict) -> None:
        import re

        assert re.match(r"^\d+\.\d+\.\d+$", agent_config["version"])

    def test_model_has_primary_and_fallback(self, agent_config: dict) -> None:
        model = agent_config["model"]
        assert "primary" in model, "Model must have a primary"
        assert "fallback" in model, "Production agents should have a fallback model"

    def test_guardrails_include_pii_detection(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails, "PII detection guardrail is required"

    def test_deploy_has_secrets(self, agent_config: dict) -> None:
        secrets = agent_config["deploy"].get("secrets", [])
        assert len(secrets) > 0, "Agent must declare its required secrets"
        assert "ZENDESK_API_KEY" in secrets

    def test_tools_configured(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        assert len(tools) >= 2, "Support agent needs at least Zendesk and escalation tools"

    def test_knowledge_bases_configured(self, agent_config: dict) -> None:
        kbs = agent_config.get("knowledge_bases", [])
        assert len(kbs) >= 1, "Support agent needs at least one knowledge base"

    def test_system_prompt_defined(self, agent_config: dict) -> None:
        prompts = agent_config.get("prompts", {})
        assert "system" in prompts, "System prompt must be defined"
        assert len(prompts["system"]) > 100, "System prompt should be substantive"

    def test_access_control_configured(self, agent_config: dict) -> None:
        access = agent_config.get("access", {})
        assert access.get("visibility") in ("team", "private"), (
            "Support agent should not be publicly accessible"
        )


class TestEscalationLogic:
    """Test escalation trigger behavior."""

    def test_escalation_tool_exists(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        tool_names = [t.get("name") for t in tools if "name" in t]
        assert "escalate_to_human" in tool_names, "Must have escalation tool"

    def test_prompt_mentions_escalation(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "escalat" in prompt.lower(), "System prompt must mention escalation policy"
