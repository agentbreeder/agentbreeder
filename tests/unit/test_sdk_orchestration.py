"""Unit tests for the Full Code Orchestration SDK (M31).

Tests cover:
- Orchestration builder (base class)
- Pipeline, FanOut, Supervisor subclasses
- Router classes (Keyword, Intent, RoundRobin, Classifier)
- Validation
- YAML serialization and round-trip
- deploy() return value
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from sdk.python.agenthub.orchestration import (
    AgentEntry,
    ClassifierRouter,
    FanOut,
    IntentRouter,
    KeywordRouter,
    Orchestration,
    OrchestrationConfig,
    Pipeline,
    RouteRule,
    Router,
    RoundRobinRouter,
    SharedStateConfig,
    Supervisor,
    SupervisorConfig,
    _orchestration_to_yaml,
    _yaml_to_orchestration,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_router_orch() -> Orchestration:
    return (
        Orchestration("support-pipeline", strategy="router", team="eng")
        .add_agent("triage", ref="agents/triage-agent")
        .add_agent("billing", ref="agents/billing-agent")
        .add_agent("general", ref="agents/general-agent")
        .with_route("triage", condition="billing", target="billing")
        .with_route("triage", condition="default", target="general")
    )


# ---------------------------------------------------------------------------
# TestOrchestrationBuilder
# ---------------------------------------------------------------------------


class TestOrchestrationBuilder:
    def test_basic_construction(self):
        orch = Orchestration("my-pipeline", strategy="router", team="eng")
        assert orch.config.name == "my-pipeline"
        assert orch.config.strategy == "router"
        assert orch.config.team == "eng"
        assert orch.config.version == "1.0.0"

    def test_add_agent(self):
        orch = Orchestration("test-orch").add_agent("agent-a", ref="agents/a")
        assert "agent-a" in orch.config.agents
        assert orch.config.agents["agent-a"].ref == "agents/a"

    def test_add_agent_with_fallback(self):
        orch = (
            Orchestration("test-orch")
            .add_agent("primary", ref="agents/primary")
            .add_agent("fallback-agent", ref="agents/fallback")
            .add_agent("main", ref="agents/main", fallback="fallback-agent")
        )
        assert orch.config.agents["main"].fallback == "fallback-agent"

    def test_with_route(self):
        orch = make_router_orch()
        routes = orch.config.agents["triage"].routes
        assert len(routes) == 2
        assert routes[0].condition == "billing"
        assert routes[0].target == "billing"

    def test_with_route_unknown_agent_raises(self):
        orch = Orchestration("test-orch")
        with pytest.raises(ValueError, match="not found"):
            orch.with_route("nonexistent", condition="x", target="y")

    def test_with_shared_state(self):
        orch = Orchestration("test-orch").add_agent("a", ref="agents/a")
        orch.with_shared_state(state_type="session_context", backend="redis")
        assert orch.config.shared_state.type == "session_context"
        assert orch.config.shared_state.backend == "redis"

    def test_with_supervisor(self):
        orch = (
            Orchestration("test-orch", strategy="supervisor")
            .add_agent("coord", ref="agents/coord")
            .with_supervisor(supervisor_agent="coord", max_iterations=5)
        )
        sc = orch.config.supervisor_config
        assert sc is not None
        assert sc.supervisor_agent == "coord"
        assert sc.max_iterations == 5

    def test_with_deploy(self):
        orch = Orchestration("test-orch").add_agent("a", ref="agents/a")
        orch.with_deploy(target="cloud-run", cpu="1", memory="2Gi")
        assert orch.config.deploy.target == "cloud-run"
        assert orch.config.deploy.resources["cpu"] == "1"

    def test_tag(self):
        orch = (
            Orchestration("test-orch")
            .add_agent("a", ref="agents/a")
            .tag("production", "support")
        )
        assert "production" in orch.config.tags
        assert "support" in orch.config.tags

    def test_chaining_returns_self(self):
        orch = Orchestration("chain-test")
        result = orch.add_agent("a", ref="agents/a")
        assert result is orch

    def test_repr(self):
        orch = Orchestration("my-orch", strategy="router")
        r = repr(orch)
        assert "my-orch" in r
        assert "router" in r

    def test_deploy_returns_dict(self):
        orch = make_router_orch()
        result = orch.deploy()
        assert result["orchestration"] == "support-pipeline"
        assert result["status"] == "pending"

    def test_deploy_target_override(self):
        orch = make_router_orch()
        result = orch.deploy(target="cloud-run")
        assert result["target"] == "cloud-run"


# ---------------------------------------------------------------------------
# TestOrchestrationValidation
# ---------------------------------------------------------------------------


class TestOrchestrationValidation:
    def test_valid_config_no_errors(self):
        orch = make_router_orch()
        assert orch.validate() == []

    def test_missing_name(self):
        orch = Orchestration("ab")  # "ab" is valid
        orch.config.name = ""
        orch.add_agent("a", ref="agents/a")
        errors = orch.validate()
        assert any("name is required" in e for e in errors)

    def test_invalid_name_uppercase(self):
        orch = Orchestration("ab")
        orch.config.name = "MyPipeline"
        orch.add_agent("a", ref="agents/a")
        errors = orch.validate()
        assert any("lowercase" in e for e in errors)

    def test_invalid_version(self):
        orch = make_router_orch()
        orch.config.version = "not-semver"
        errors = orch.validate()
        assert any("semver" in e for e in errors)

    def test_invalid_strategy(self):
        orch = make_router_orch()
        orch.config.strategy = "magic"
        errors = orch.validate()
        assert any("strategy" in e for e in errors)

    def test_no_agents(self):
        orch = Orchestration("empty-orch")
        errors = orch.validate()
        assert any("at least one agent" in e for e in errors)

    def test_supervisor_strategy_without_supervisor_config(self):
        orch = (
            Orchestration("sup-orch", strategy="supervisor")
            .add_agent("a", ref="agents/a")
        )
        errors = orch.validate()
        assert any("supervisor" in e for e in errors)

    def test_fan_out_without_merge_agent(self):
        orch = (
            Orchestration("fan-orch", strategy="fan_out_fan_in")
            .add_agent("worker-a", ref="agents/a")
            .add_agent("worker-b", ref="agents/b")
        )
        errors = orch.validate()
        assert any("fan_out" in e or "merge_agent" in e for e in errors)

    def test_route_target_not_known_agent(self):
        orch = (
            Orchestration("route-test", strategy="router")
            .add_agent("triage", ref="agents/triage")
        )
        orch.config.agents["triage"].routes.append(
            RouteRule(condition="billing", target="nonexistent")
        )
        errors = orch.validate()
        assert any("nonexistent" in e for e in errors)

    def test_fallback_not_known_agent(self):
        orch = (
            Orchestration("fallback-test", strategy="router")
            .add_agent("primary", ref="agents/primary")
        )
        orch.config.agents["primary"].fallback = "ghost-agent"
        errors = orch.validate()
        assert any("ghost-agent" in e for e in errors)


# ---------------------------------------------------------------------------
# TestPipeline
# ---------------------------------------------------------------------------


class TestPipeline:
    def test_basic_pipeline(self):
        pipeline = (
            Pipeline("research-pipeline", team="eng")
            .step("researcher", ref="agents/researcher")
            .step("summarizer", ref="agents/summarizer")
            .step("reviewer", ref="agents/reviewer")
        )
        assert pipeline.config.strategy == "sequential"
        assert len(pipeline.config.agents) == 3
        assert pipeline._steps == ["researcher", "summarizer", "reviewer"]

    def test_pipeline_validates_minimum_steps(self):
        pipeline = Pipeline("short-pipe").step("only-one", ref="agents/a")
        errors = pipeline.validate()
        assert any("2 steps" in e for e in errors)

    def test_pipeline_valid_two_steps(self):
        pipeline = (
            Pipeline("two-step")
            .step("first", ref="agents/first")
            .step("second", ref="agents/second")
        )
        assert pipeline.validate() == []

    def test_pipeline_with_fallback_step(self):
        pipeline = (
            Pipeline("with-fallback")
            .step("first", ref="agents/first")
            .step("second", ref="agents/second", fallback="first")
        )
        assert pipeline.config.agents["second"].fallback == "first"

    def test_pipeline_is_orchestration_subclass(self):
        assert issubclass(Pipeline, Orchestration)

    def test_pipeline_yaml_round_trip(self):
        pipeline = (
            Pipeline("rt-pipeline", team="eng")
            .step("a", ref="agents/a")
            .step("b", ref="agents/b")
        )
        yaml_str = pipeline.to_yaml()
        restored = Orchestration.from_yaml(yaml_str)
        assert isinstance(restored, Pipeline)
        assert restored.config.strategy == "sequential"
        assert len(restored.config.agents) == 2


# ---------------------------------------------------------------------------
# TestFanOut
# ---------------------------------------------------------------------------


class TestFanOut:
    def test_basic_fanout(self):
        analysis = (
            FanOut("multi-analysis", team="eng")
            .worker("sentiment", ref="agents/sentiment")
            .worker("topics", ref="agents/topics")
            .worker("summary", ref="agents/summarizer")
            .merge(ref="agents/aggregator")
        )
        assert analysis.config.strategy == "fan_out_fan_in"
        assert "sentiment" in analysis.config.agents
        assert "merger" in analysis.config.agents
        sc = analysis.config.supervisor_config
        assert sc is not None
        assert sc.merge_agent == "merger"

    def test_fanout_custom_merger_name(self):
        analysis = (
            FanOut("analysis")
            .worker("w1", ref="agents/w1")
            .merge(ref="agents/agg", name="aggregator")
        )
        assert analysis.config.supervisor_config.merge_agent == "aggregator"

    def test_fanout_merge_strategy(self):
        analysis = (
            FanOut("analysis")
            .worker("w1", ref="agents/w1")
            .worker("w2", ref="agents/w2")
            .merge(ref="agents/merger")
            .with_merge_strategy("majority_vote")
        )
        assert analysis._merge_strategy == "majority_vote"

    def test_fanout_invalid_merge_strategy(self):
        with pytest.raises(ValueError, match="merge_strategy"):
            FanOut("analysis").with_merge_strategy("magic")

    def test_fanout_validates_missing_merger(self):
        analysis = FanOut("no-merger").worker("w1", ref="agents/w1")
        errors = analysis.validate()
        assert any("merge agent" in e for e in errors)

    def test_fanout_valid(self):
        analysis = (
            FanOut("valid-fanout")
            .worker("w1", ref="agents/w1")
            .worker("w2", ref="agents/w2")
            .merge(ref="agents/merger")
        )
        assert analysis.validate() == []

    def test_fanout_is_orchestration_subclass(self):
        assert issubclass(FanOut, Orchestration)


# ---------------------------------------------------------------------------
# TestSupervisor
# ---------------------------------------------------------------------------


class TestSupervisor:
    def test_basic_supervisor(self):
        workflow = (
            Supervisor("research-workflow", team="eng")
            .with_supervisor_agent("coordinator", ref="agents/coordinator")
            .worker("researcher", ref="agents/researcher")
            .worker("writer", ref="agents/writer")
            .with_max_iterations(5)
        )
        assert workflow.config.strategy == "supervisor"
        assert len(workflow.config.agents) == 3
        sc = workflow.config.supervisor_config
        assert sc is not None
        assert sc.supervisor_agent == "coordinator"
        assert sc.max_iterations == 5

    def test_supervisor_validates_missing_supervisor_agent(self):
        workflow = (
            Supervisor("no-supervisor")
            .worker("w1", ref="agents/w1")
        )
        errors = workflow.validate()
        assert any("with_supervisor_agent" in e for e in errors)

    def test_supervisor_valid(self):
        workflow = (
            Supervisor("valid-supervisor")
            .with_supervisor_agent("coord", ref="agents/coord")
            .worker("w1", ref="agents/w1")
        )
        assert workflow.validate() == []

    def test_supervisor_worker_with_fallback(self):
        workflow = (
            Supervisor("fallback-workflow")
            .with_supervisor_agent("coord", ref="agents/coord")
            .worker("primary", ref="agents/primary")
            .worker("backup", ref="agents/backup")
        )
        # Add fallback manually (after both are registered)
        workflow.config.agents["primary"].fallback = "backup"
        assert workflow.validate() == []

    def test_supervisor_is_orchestration_subclass(self):
        assert issubclass(Supervisor, Orchestration)


# ---------------------------------------------------------------------------
# TestKeywordRouter
# ---------------------------------------------------------------------------


class TestKeywordRouter:
    def test_matches_keyword(self):
        router = KeywordRouter(
            rules={"billing": "billing-agent", "broken": "tech-support"},
            default="general",
        )
        result = asyncio.run(router.route("I have a billing question", {}))
        assert result == "billing-agent"

    def test_falls_back_to_default(self):
        router = KeywordRouter(rules={"billing": "billing-agent"}, default="general")
        result = asyncio.run(router.route("Hello world", {}))
        assert result == "general"

    def test_case_insensitive_by_default(self):
        router = KeywordRouter(rules={"BILLING": "billing-agent"}, default="general")
        result = asyncio.run(router.route("billing issue", {}))
        assert result == "billing-agent"

    def test_case_sensitive_mode(self):
        router = KeywordRouter(
            rules={"Billing": "billing-agent"}, default="general", case_sensitive=True
        )
        # lowercase "billing" should NOT match "Billing" in case-sensitive mode
        result = asyncio.run(router.route("billing issue", {}))
        assert result == "general"

    def test_router_is_abstract_subclass(self):
        assert issubclass(KeywordRouter, Router)


# ---------------------------------------------------------------------------
# TestIntentRouter
# ---------------------------------------------------------------------------


class TestIntentRouter:
    def test_routes_on_intent(self):
        router = IntentRouter(
            intents={"billing_inquiry": "billing", "tech_support": "tech"},
            default="general",
        )
        result = asyncio.run(router.route("...", {"intent": "billing_inquiry"}))
        assert result == "billing"

    def test_fallback_on_unknown_intent(self):
        router = IntentRouter(intents={"x": "agent-x"}, default="general")
        result = asyncio.run(router.route("...", {"intent": "unknown"}))
        assert result == "general"

    def test_fallback_on_missing_intent(self):
        router = IntentRouter(intents={"x": "agent-x"}, default="general")
        result = asyncio.run(router.route("...", {}))
        assert result == "general"


# ---------------------------------------------------------------------------
# TestRoundRobinRouter
# ---------------------------------------------------------------------------


class TestRoundRobinRouter:
    def test_distributes_in_order(self):
        router = RoundRobinRouter(agents=["a", "b", "c"])

        async def run():
            return [await router.route("msg", {}) for _ in range(6)]

        results = asyncio.run(run())
        assert results == ["a", "b", "c", "a", "b", "c"]

    def test_single_agent(self):
        router = RoundRobinRouter(agents=["only"])

        async def run():
            return [await router.route("msg", {}) for _ in range(3)]

        results = asyncio.run(run())
        assert all(r == "only" for r in results)


# ---------------------------------------------------------------------------
# TestClassifierRouter
# ---------------------------------------------------------------------------


class TestClassifierRouter:
    def test_routes_via_classify(self):
        class FixedClassifier(ClassifierRouter):
            async def classify(self, message: str) -> str:
                return "billing"

        router = FixedClassifier(
            label_to_agent={"billing": "billing-agent"},
            default="general",
        )
        result = asyncio.run(router.route("any message", {}))
        assert result == "billing-agent"

    def test_default_on_unrecognised_label(self):
        class UnknownClassifier(ClassifierRouter):
            async def classify(self, message: str) -> str:
                return "mystery"

        router = UnknownClassifier(label_to_agent={}, default="general")
        result = asyncio.run(router.route("msg", {})
        )
        assert result == "general"

    def test_base_classify_returns_default(self):
        # ClassifierRouter.classify() returns self.default by default
        class PassthroughClassifier(ClassifierRouter):
            async def classify(self, message: str) -> str:
                return await super().classify(message)

        router = PassthroughClassifier(label_to_agent={}, default="fallback")
        result = asyncio.run(router.route("msg", {}))
        assert result == "fallback"


# ---------------------------------------------------------------------------
# TestYamlSerialization
# ---------------------------------------------------------------------------


class TestYamlSerialization:
    def test_router_round_trip(self):
        orch = make_router_orch()
        yaml_str = orch.to_yaml()
        restored = Orchestration.from_yaml(yaml_str)
        assert restored.config.name == "support-pipeline"
        assert restored.config.strategy == "router"
        assert set(restored.config.agents) == {"triage", "billing", "general"}
        routes = restored.config.agents["triage"].routes
        assert len(routes) == 2

    def test_yaml_contains_required_fields(self):
        orch = make_router_orch()
        yaml_str = orch.to_yaml()
        assert "name: support-pipeline" in yaml_str
        assert "strategy: router" in yaml_str
        assert "agents:" in yaml_str

    def test_supervisor_config_round_trip(self):
        orch = (
            Orchestration("sup-test", strategy="supervisor")
            .add_agent("coord", ref="agents/coord")
            .add_agent("worker", ref="agents/worker")
            .with_supervisor(supervisor_agent="coord", max_iterations=7)
        )
        yaml_str = orch.to_yaml()
        restored = Orchestration.from_yaml(yaml_str)
        sc = restored.config.supervisor_config
        assert sc is not None
        assert sc.supervisor_agent == "coord"
        assert sc.max_iterations == 7

    def test_shared_state_round_trip(self):
        orch = (
            Orchestration("state-test")
            .add_agent("a", ref="agents/a")
            .with_shared_state(state_type="session_context", backend="redis")
        )
        yaml_str = orch.to_yaml()
        restored = Orchestration.from_yaml(yaml_str)
        ss = restored.config.shared_state
        assert ss.type == "session_context"
        assert ss.backend == "redis"

    def test_deploy_config_round_trip(self):
        orch = (
            Orchestration("deploy-test")
            .add_agent("a", ref="agents/a")
            .with_deploy(target="cloud-run", cpu="1", memory="2Gi")
        )
        yaml_str = orch.to_yaml()
        restored = Orchestration.from_yaml(yaml_str)
        assert restored.config.deploy.target == "cloud-run"
        assert restored.config.deploy.resources["cpu"] == "1"

    def test_pipeline_round_trip_produces_pipeline_instance(self):
        pipeline = (
            Pipeline("seq-rt", team="eng")
            .step("step-a", ref="agents/a")
            .step("step-b", ref="agents/b")
        )
        yaml_str = pipeline.to_yaml()
        assert "strategy: sequential" in yaml_str
        restored = Orchestration.from_yaml(yaml_str)
        assert isinstance(restored, Pipeline)

    def test_fanout_round_trip_produces_fanout_instance(self):
        fanout = (
            FanOut("fo-rt")
            .worker("w1", ref="agents/w1")
            .worker("w2", ref="agents/w2")
            .merge(ref="agents/merger")
        )
        yaml_str = fanout.to_yaml()
        assert "strategy: fan_out_fan_in" in yaml_str
        restored = Orchestration.from_yaml(yaml_str)
        assert isinstance(restored, FanOut)

    def test_supervisor_round_trip_produces_supervisor_instance(self):
        workflow = (
            Supervisor("sup-rt")
            .with_supervisor_agent("coord", ref="agents/coord")
            .worker("w1", ref="agents/w1")
        )
        yaml_str = workflow.to_yaml()
        assert "strategy: supervisor" in yaml_str
        restored = Orchestration.from_yaml(yaml_str)
        assert isinstance(restored, Supervisor)

    def test_from_yaml_invalid_raises(self):
        with pytest.raises(ValueError, match="mapping"):
            Orchestration.from_yaml("- just a list")

    def test_tags_round_trip(self):
        orch = (
            Orchestration("tagged-orch")
            .add_agent("a", ref="agents/a")
            .tag("production", "support")
        )
        yaml_str = orch.to_yaml()
        restored = Orchestration.from_yaml(yaml_str)
        assert "production" in restored.config.tags
        assert "support" in restored.config.tags

    def test_fallback_round_trip(self):
        orch = (
            Orchestration("fallback-orch", strategy="router")
            .add_agent("primary", ref="agents/primary")
            .add_agent("backup", ref="agents/backup")
            .add_agent("main", ref="agents/main", fallback="backup")
        )
        yaml_str = orch.to_yaml()
        restored = Orchestration.from_yaml(yaml_str)
        assert restored.config.agents["main"].fallback == "backup"


# ---------------------------------------------------------------------------
# TestOrchestrationFromFile (integration-style)
# ---------------------------------------------------------------------------


class TestOrchestrationFromFile:
    def test_save_and_load(self, tmp_path):
        orch = make_router_orch()
        path = str(tmp_path / "orchestration.yaml")
        orch.save(path)

        loaded = Orchestration.from_file(path)
        assert loaded.config.name == orch.config.name
        assert loaded.config.strategy == orch.config.strategy
        assert set(loaded.config.agents) == set(orch.config.agents)
