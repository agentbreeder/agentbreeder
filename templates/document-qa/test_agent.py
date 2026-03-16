"""Unit tests for the document QA agent template."""

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
        assert temp <= 0.3, "Document QA needs low temperature for factual accuracy"

    def test_guardrails_include_hallucination_check(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails
        assert "hallucination_check" in guardrails, "Document QA must have hallucination check"

    def test_search_tool_configured(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "search_documents" in names

    def test_knowledge_bases_configured(self, agent_config: dict) -> None:
        kbs = agent_config.get("knowledge_bases", [])
        assert len(kbs) >= 1, "Document QA must have at least one knowledge base"

    def test_prompt_requires_citations(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "cite" in prompt.lower() or "source" in prompt.lower()

    def test_prompt_forbids_fabrication(self, agent_config: dict) -> None:
        prompt = agent_config["prompts"]["system"]
        assert "never" in prompt.lower()
        assert "fabricat" in prompt.lower()

    def test_sufficient_memory(self, agent_config: dict) -> None:
        memory = agent_config["deploy"]["resources"]["memory"]
        mem_val = int(memory.replace("Gi", ""))
        assert mem_val >= 2, "Document QA needs memory for embedding operations"
