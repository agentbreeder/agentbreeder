"""Unit tests for the research agent template."""

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

    def test_high_max_tokens_for_reports(self, agent_config: dict) -> None:
        max_tokens = agent_config["model"].get("max_tokens", 0)
        assert max_tokens >= 4096, "Research reports need high token limits"

    def test_guardrails_present(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails
        assert "hallucination_check" in guardrails, "Research agent must have hallucination check"

    def test_web_search_tool_configured(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        refs = [t.get("ref", "") for t in tools]
        assert any("search" in r for r in refs), "Must have web search tool"

    def test_report_generation_tool(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "generate_report" in names

    def test_prompt_requires_citations(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "source" in prompt.lower()
        assert "cite" in prompt.lower() or "citation" in prompt.lower()

    def test_sufficient_memory_for_research(self, agent_config: dict) -> None:
        memory = agent_config["deploy"]["resources"]["memory"]
        # Parse memory value (e.g., "4Gi" -> 4)
        mem_val = int(memory.replace("Gi", ""))
        assert mem_val >= 2, "Research agent needs at least 2Gi for document processing"
