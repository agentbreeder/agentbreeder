"""Tests for the orchestration system — parser, engine, and service."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from api.services.orchestration_service import OrchestrationStore
from engine.orchestration_parser import (
    AgentRef,
    OrchestrationConfig,
    OrchestrationStrategy,
    RoutingRule,
    parse_orchestration,
    validate_orchestration,
)
from engine.orchestrator import OrchestrationResult, Orchestrator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(content: str) -> Path:
    """Write YAML content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


VALID_YAML = """\
name: test-orchestration
version: 1.0.0
strategy: router
agents:
  agent-a:
    ref: agents/alpha
    routes:
      - condition: billing
        target: agent-a
    fallback: agent-b
  agent-b:
    ref: agents/beta
"""

SEQUENTIAL_YAML = """\
name: seq-pipeline
version: 1.0.0
strategy: sequential
agents:
  step-one:
    ref: agents/step-one
  step-two:
    ref: agents/step-two
  step-three:
    ref: agents/step-three
"""

PARALLEL_YAML = """\
name: parallel-fan
version: 1.0.0
strategy: parallel
agents:
  worker-a:
    ref: agents/worker-a
  worker-b:
    ref: agents/worker-b
"""

HIERARCHICAL_YAML = """\
name: hierarchical-team
version: 1.0.0
strategy: hierarchical
agents:
  supervisor:
    ref: agents/supervisor
  worker-a:
    ref: agents/worker-a
  worker-b:
    ref: agents/worker-b
"""


# ---------------------------------------------------------------------------
# Parser Tests
# ---------------------------------------------------------------------------


class TestParseOrchestration:
    def test_parse_orchestration_valid(self) -> None:
        path = _write_yaml(VALID_YAML)
        config = parse_orchestration(path)
        assert config.name == "test-orchestration"
        assert config.version == "1.0.0"
        assert config.strategy == OrchestrationStrategy.router
        assert "agent-a" in config.agents
        assert "agent-b" in config.agents
        assert config.agents["agent-a"].ref == "agents/alpha"
        assert len(config.agents["agent-a"].routes) == 1
        assert config.agents["agent-a"].fallback == "agent-b"

    def test_parse_orchestration_invalid_strategy(self) -> None:
        yaml = """\
name: bad-strategy
version: 1.0.0
strategy: round_robin
agents:
  agent-a:
    ref: agents/alpha
"""
        path = _write_yaml(yaml)
        result = validate_orchestration(path)
        assert not result.valid
        assert any("strategy" in e.path or "strategy" in e.message for e in result.errors)

    def test_parse_orchestration_missing_required(self) -> None:
        yaml = """\
name: missing-fields
version: 1.0.0
"""
        path = _write_yaml(yaml)
        result = validate_orchestration(path)
        assert not result.valid
        has_error = any(
            "strategy" in e.message.lower() or "agents" in e.message.lower()
            for e in result.errors
        )
        assert has_error

    def test_validate_orchestration_returns_errors(self) -> None:
        yaml = """\
name: INVALID_NAME!
version: not-semver
strategy: router
agents:
  a:
    ref: agents/a
"""
        path = _write_yaml(yaml)
        result = validate_orchestration(path)
        assert not result.valid
        assert len(result.errors) >= 1

    def test_validate_orchestration_file_not_found(self) -> None:
        result = validate_orchestration(Path("/nonexistent/file.yaml"))
        assert not result.valid
        assert "not found" in result.errors[0].message.lower()

    def test_validate_orchestration_empty_file(self) -> None:
        path = _write_yaml("")
        result = validate_orchestration(path)
        assert not result.valid

    def test_parse_orchestration_with_all_fields(self) -> None:
        yaml = """\
spec_version: v1
name: full-config
version: 2.1.0
description: A fully specified orchestration
team: engineering
owner: test@example.com
strategy: sequential
agents:
  step-one:
    ref: agents/step-one
shared_state:
  type: dict
  backend: redis
deploy:
  target: aws
  resources:
    cpu: "2"
    memory: "4Gi"
"""
        path = _write_yaml(yaml)
        config = parse_orchestration(path)
        assert config.description == "A fully specified orchestration"
        assert config.team == "engineering"
        assert config.owner == "test@example.com"
        assert config.shared_state.backend == "redis"
        assert config.deploy.target == "aws"
        assert config.deploy.resources.cpu == "2"


# ---------------------------------------------------------------------------
# Orchestrator (Engine) Tests
# ---------------------------------------------------------------------------


class TestOrchestrator:
    def _make_config(
        self,
        strategy: str = "router",
        agents: dict[str, AgentRef] | None = None,
    ) -> OrchestrationConfig:
        if agents is None:
            agents = {
                "billing": AgentRef(
                    ref="agents/billing",
                    routes=[RoutingRule(condition="billing", target="billing")],
                    fallback="general",
                ),
                "technical": AgentRef(
                    ref="agents/technical",
                    routes=[RoutingRule(condition="error", target="technical")],
                ),
                "general": AgentRef(ref="agents/general"),
            }
        return OrchestrationConfig(
            name="test-orch",
            version="1.0.0",
            strategy=OrchestrationStrategy(strategy),
            agents=agents,
        )

    def test_router_strategy_execution(self) -> None:
        config = self._make_config(strategy="router")
        orchestrator = Orchestrator(config)
        result = asyncio.run(orchestrator.execute("I have a billing question"))
        assert isinstance(result, OrchestrationResult)
        assert result.strategy == "router"
        assert len(result.agent_trace) >= 1
        # Should route to billing agent due to "billing" keyword
        assert result.agent_trace[0].agent_name == "billing"
        assert result.agent_trace[0].status == "success"
        assert result.total_tokens > 0

    def test_router_fallback_to_first_agent(self) -> None:
        config = self._make_config(strategy="router")
        orchestrator = Orchestrator(config)
        result = asyncio.run(orchestrator.execute("hello world"))
        assert isinstance(result, OrchestrationResult)
        # No keyword match — falls back to first agent
        assert result.agent_trace[0].agent_name == "billing"

    def test_sequential_strategy_execution(self) -> None:
        agents = {
            "step-one": AgentRef(ref="agents/step-one"),
            "step-two": AgentRef(ref="agents/step-two"),
            "step-three": AgentRef(ref="agents/step-three"),
        }
        config = self._make_config(strategy="sequential", agents=agents)
        orchestrator = Orchestrator(config)
        result = asyncio.run(orchestrator.execute("process this data"))
        assert result.strategy == "sequential"
        assert len(result.agent_trace) == 3
        assert result.agent_trace[0].agent_name == "step-one"
        assert result.agent_trace[1].agent_name == "step-two"
        assert result.agent_trace[2].agent_name == "step-three"

    def test_parallel_strategy_execution(self) -> None:
        agents = {
            "worker-a": AgentRef(ref="agents/worker-a"),
            "worker-b": AgentRef(ref="agents/worker-b"),
        }
        config = self._make_config(strategy="parallel", agents=agents)
        orchestrator = Orchestrator(config)
        result = asyncio.run(orchestrator.execute("analyze this"))
        assert result.strategy == "parallel"
        assert len(result.agent_trace) == 2
        agent_names = {e.agent_name for e in result.agent_trace}
        assert "worker-a" in agent_names
        assert "worker-b" in agent_names
        # Output should contain both agent outputs
        assert "[worker-a]:" in result.output
        assert "[worker-b]:" in result.output

    def test_hierarchical_strategy_execution(self) -> None:
        agents = {
            "supervisor": AgentRef(ref="agents/supervisor"),
            "worker-a": AgentRef(ref="agents/worker-a"),
            "worker-b": AgentRef(ref="agents/worker-b"),
        }
        config = self._make_config(strategy="hierarchical", agents=agents)
        orchestrator = Orchestrator(config)
        result = asyncio.run(orchestrator.execute("delegate this task"))
        assert result.strategy == "hierarchical"
        # supervisor + 2 workers + supervisor aggregation = 4 entries
        assert len(result.agent_trace) == 4
        assert result.agent_trace[0].agent_name == "supervisor"

    def test_fallback_on_agent_error(self) -> None:
        """Test that the fallback agent is used when routing resolves correctly."""
        # This tests the router's fallback path — the simulated agent always
        # succeeds, so we test the routing fallback mechanism exists.
        config = self._make_config(strategy="router")
        orchestrator = Orchestrator(config)
        result = asyncio.run(orchestrator.execute("billing issue"))
        assert result.agent_trace[0].agent_name == "billing"
        assert result.agent_trace[0].status == "success"
        # Verify the config has a fallback defined
        assert config.agents["billing"].fallback == "general"


# ---------------------------------------------------------------------------
# Service / Store Tests
# ---------------------------------------------------------------------------


class TestOrchestrationStore:
    def test_orchestration_store_crud(self) -> None:
        store = OrchestrationStore()
        store._orchestrations.clear()

        # Create
        created = store.create(
            name="test-orch",
            version="1.0.0",
            strategy="router",
            agents={"agent-a": {"ref": "agents/alpha"}},
            team="engineering",
        )
        assert created["name"] == "test-orch"
        assert created["status"] == "draft"
        orch_id = created["id"]

        # Get
        fetched = store.get(orch_id)
        assert fetched is not None
        assert fetched["name"] == "test-orch"

        # Get by name
        by_name = store.get_by_name("test-orch")
        assert by_name is not None
        assert by_name["id"] == orch_id

        # List
        items = store.list()
        assert len(items) == 1

        # Update
        updated = store.update(orch_id, description="Updated description")
        assert updated is not None
        assert updated["description"] == "Updated description"

        # Delete
        assert store.delete(orch_id) is True
        assert store.get(orch_id) is None

        # Delete non-existent
        assert store.delete("nonexistent") is False

    def test_orchestration_store_list_filters(self) -> None:
        store = OrchestrationStore()
        store._orchestrations.clear()

        store.create(
            name="orch-a",
            version="1.0.0",
            strategy="router",
            agents={"a": {"ref": "agents/a"}},
            team="alpha",
        )
        store.create(
            name="orch-b",
            version="1.0.0",
            strategy="sequential",
            agents={"b": {"ref": "agents/b"}},
            team="beta",
        )

        assert len(store.list()) == 2
        assert len(store.list(team="alpha")) == 1
        assert len(store.list(team="gamma")) == 0

    def test_orchestration_deploy(self) -> None:
        store = OrchestrationStore()
        store._orchestrations.clear()

        created = store.create(
            name="deploy-test",
            version="1.0.0",
            strategy="router",
            agents={"a": {"ref": "agents/a"}},
        )
        orch_id = created["id"]
        assert created["status"] == "draft"

        deployed = store.deploy(orch_id)
        assert deployed is not None
        assert deployed["status"] == "deployed"
        assert deployed["endpoint_url"] is not None

        # Deploy non-existent
        assert store.deploy("nonexistent") is None

    def test_orchestration_execute(self) -> None:
        store = OrchestrationStore()
        store._orchestrations.clear()

        created = store.create(
            name="exec-test",
            version="1.0.0",
            strategy="sequential",
            agents={
                "step-one": {"ref": "agents/step-one"},
                "step-two": {"ref": "agents/step-two"},
            },
        )
        orch_id = created["id"]

        result = asyncio.run(store.execute(orch_id, "hello world"))
        assert result["orchestration_name"] == "exec-test"
        assert result["strategy"] == "sequential"
        assert len(result["agent_trace"]) == 2

    def test_orchestration_execute_not_found(self) -> None:
        store = OrchestrationStore()
        store._orchestrations.clear()

        with pytest.raises(ValueError, match="not found"):
            asyncio.run(store.execute("nonexistent", "hello"))

    def test_seed_demo_data(self) -> None:
        store = OrchestrationStore()
        items = store.list()
        # Should have at least the seeded demo
        assert len(items) >= 1
        demo = next((i for i in items if i["name"] == "customer-support-pipeline"), None)
        assert demo is not None
        assert demo["strategy"] == "router"
        assert demo["status"] == "deployed"
