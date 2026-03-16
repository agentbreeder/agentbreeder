"""Unit tests for the multi-agent pipeline template."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

TEMPLATE_DIR = Path(__file__).parent
AGENT_YAML = TEMPLATE_DIR / "agent.yaml"
ORCHESTRATION_YAML = TEMPLATE_DIR / "orchestration.yaml"


@pytest.fixture
def agent_config() -> dict:
    with open(AGENT_YAML) as f:
        return yaml.safe_load(f)


@pytest.fixture
def orchestration_config() -> dict:
    with open(ORCHESTRATION_YAML) as f:
        return yaml.safe_load(f)


class TestAgentConfig:
    def test_required_fields_present(self, agent_config: dict) -> None:
        required = ["name", "version", "team", "owner", "framework", "model", "deploy"]
        for field in required:
            assert field in agent_config, f"Missing required field: {field}"

    def test_version_is_semver(self, agent_config: dict) -> None:
        import re

        assert re.match(r"^\d+\.\d+\.\d+$", agent_config["version"])

    def test_subagents_configured(self, agent_config: dict) -> None:
        subagents = agent_config.get("subagents", [])
        assert len(subagents) >= 3, "Pipeline needs at least 3 specialist subagents"

    def test_quality_check_tool(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "check_quality" in names, "Must have QA check tool"

    def test_classify_intent_tool(self, agent_config: dict) -> None:
        tools = agent_config.get("tools", [])
        names = [t.get("name", "") for t in tools]
        assert "classify_intent" in names, "Must have intent classification tool"

    def test_guardrails_present(self, agent_config: dict) -> None:
        guardrails = agent_config.get("guardrails", [])
        assert "pii_detection" in guardrails

    def test_sufficient_resources(self, agent_config: dict) -> None:
        cpu = agent_config["deploy"]["resources"]["cpu"]
        assert float(cpu) >= 1, "Multi-agent pipeline needs adequate CPU"


class TestOrchestrationConfig:
    def test_required_fields_present(self, orchestration_config: dict) -> None:
        required = ["name", "version", "strategy", "agents"]
        for field in required:
            assert field in orchestration_config, f"Missing required field: {field}"

    def test_strategy_is_supervisor(self, orchestration_config: dict) -> None:
        assert orchestration_config["strategy"] == "supervisor"

    def test_has_triage_agent(self, orchestration_config: dict) -> None:
        agents = orchestration_config["agents"]
        assert "triage" in agents, "Must have triage agent"

    def test_has_qa_agent(self, orchestration_config: dict) -> None:
        agents = orchestration_config["agents"]
        assert "qa-reviewer" in agents, "Must have QA reviewer agent"

    def test_all_specialists_route_to_qa(self, orchestration_config: dict) -> None:
        agents = orchestration_config["agents"]
        specialists = ["billing-specialist", "technical-specialist", "account-specialist"]
        for name in specialists:
            assert name in agents, f"Missing specialist: {name}"
            routes = agents[name].get("routes", [])
            targets = [r["target"] for r in routes]
            assert "qa-reviewer" in targets, f"{name} must route to qa-reviewer"

    def test_supervisor_config(self, orchestration_config: dict) -> None:
        sup = orchestration_config.get("supervisor_config", {})
        assert sup.get("supervisor_agent") == "triage"
        assert sup.get("max_iterations", 0) >= 3

    def test_shared_state_configured(self, orchestration_config: dict) -> None:
        state = orchestration_config.get("shared_state", {})
        assert "backend" in state
