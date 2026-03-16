"""Unit tests for the code review agent template."""

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

    def test_name_is_slug_friendly(self, agent_config: dict) -> None:
        name = agent_config["name"]
        assert name == name.lower()
        assert " " not in name

    def test_version_is_semver(self, agent_config: dict) -> None:
        import re

        assert re.match(r"^\d+\.\d+\.\d+$", agent_config["version"])

    def test_low_temperature_for_code_review(self, agent_config: dict) -> None:
        temp = agent_config["model"].get("temperature", 1.0)
        assert temp <= 0.3, "Code review needs low temperature for consistency"

    def test_high_max_tokens(self, agent_config: dict) -> None:
        max_tokens = agent_config["model"].get("max_tokens", 0)
        assert max_tokens >= 4096, "Code reviews need sufficient output length"

    def test_guardrails_present(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails

    def test_github_tool_configured(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        refs = [t.get("ref", "") for t in tools]
        assert any("github" in r for r in refs), "Must have GitHub tool"

    def test_security_scan_tool_present(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "security_scan" in names, "Code review agent must have security scanning"

    def test_system_prompt_has_review_checklist(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "security" in prompt.lower()
        assert "performance" in prompt.lower()
        assert "testing" in prompt.lower()

    def test_github_token_in_secrets(self, agent_config: dict) -> None:
        secrets = agent_config["deploy"].get("secrets", [])
        assert "GITHUB_TOKEN" in secrets
