"""Unit tests for the data analyst agent template."""

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

    def test_very_low_temperature(self, agent_config: dict) -> None:
        temp = agent_config["model"].get("temperature", 1.0)
        assert temp <= 0.2, "Data analysis needs very low temperature for accuracy"

    def test_guardrails_present(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails

    def test_sql_tool_configured(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "execute_sql" in names, "Must have SQL execution tool"

    def test_chart_tool_configured(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "generate_chart" in names, "Must have chart generation tool"

    def test_read_only_enforcement(self, agent_config: dict) -> None:
        env_vars = agent_config["deploy"].get("env_vars", {})
        assert env_vars.get("DB_READ_ONLY") == "true", "Database access must be read-only"

    def test_prompt_enforces_select_only(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "SELECT" in prompt
        assert "never" in prompt.lower()
        assert "DELETE" in prompt

    def test_database_url_in_secrets(self, agent_config: dict) -> None:
        secrets = agent_config["deploy"].get("secrets", [])
        assert "DATABASE_URL" in secrets
