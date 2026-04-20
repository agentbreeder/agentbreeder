"""
Performance benchmarks for AgentBreeder core paths.

These are pytest-benchmark tests that track performance of the most
critical synchronous operations across releases.

Run:
    pytest benchmarks/ --benchmark-only
    pytest benchmarks/ --benchmark-compare          # compare vs. last run
    pytest benchmarks/ --benchmark-save=v1.3.0      # save named baseline
    pytest benchmarks/ --benchmark-compare=v1.3.0   # compare vs. named baseline

Results are stored in .benchmarks/ (git-tracked baselines, run-generated data ignored).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from agenthub.orchestration import (
    FanOut,
    Orchestration,
    Pipeline,
    Supervisor,
)

from engine.config_parser import parse_config as parse_agent_config
from engine.config_parser import validate_config as validate_agent
from engine.orchestration_parser import parse_orchestration, validate_orchestration

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AGENT_YAML = """
name: bench-agent
version: "1.0.0"
team: benchmarks
owner: bench@example.com
framework: langgraph
model:
  primary: claude-haiku-4-5
  temperature: 0.7
tools:
  - ref: tools/web-search
  - ref: tools/database-query
prompts:
  system: "You are a helpful assistant."
deploy:
  cloud: local
"""

ORCHESTRATION_YAML = """
name: bench-pipeline
version: "1.0.0"
team: benchmarks
strategy: router
agents:
  triage:
    ref: agents/triage
    routes:
      - condition: billing
        target: billing
      - condition: default
        target: general
  billing:
    ref: agents/billing
    fallback: general
  general:
    ref: agents/general
"""


@pytest.fixture
def agent_yaml_path(tmp_path: Path) -> Path:
    p = tmp_path / "agent.yaml"
    p.write_text(AGENT_YAML)
    return p


@pytest.fixture
def orchestration_yaml_path(tmp_path: Path) -> Path:
    p = tmp_path / "orchestration.yaml"
    p.write_text(ORCHESTRATION_YAML)
    return p


# ---------------------------------------------------------------------------
# Config parser benchmarks
# ---------------------------------------------------------------------------


def test_benchmark_agent_yaml_parse(benchmark, agent_yaml_path):
    """Benchmark: parse agent.yaml → AgentConfig (full validation)."""
    result = benchmark(parse_agent_config, agent_yaml_path)
    assert result.name == "bench-agent"


def test_benchmark_agent_yaml_validate(benchmark, agent_yaml_path):
    """Benchmark: validate agent.yaml against JSON Schema."""
    result = benchmark(validate_agent, agent_yaml_path)
    assert result.valid


def test_benchmark_orchestration_yaml_parse(benchmark, orchestration_yaml_path):
    """Benchmark: parse orchestration.yaml → OrchestrationConfig."""
    result = benchmark(parse_orchestration, orchestration_yaml_path)
    assert result.name == "bench-pipeline"


def test_benchmark_orchestration_yaml_validate(benchmark, orchestration_yaml_path):
    """Benchmark: validate orchestration.yaml against JSON Schema."""
    result = benchmark(validate_orchestration, orchestration_yaml_path)
    assert result.valid


# ---------------------------------------------------------------------------
# SDK builder benchmarks
# ---------------------------------------------------------------------------


def test_benchmark_sdk_router_build(benchmark):
    """Benchmark: build a router orchestration via Python SDK."""

    def build():
        return (
            Orchestration("bench-support", strategy="router", team="eng")
            .add_agent("triage", ref="agents/triage")
            .add_agent("billing", ref="agents/billing")
            .add_agent("general", ref="agents/general")
            .with_route("triage", condition="billing", target="billing")
            .with_route("triage", condition="default", target="general")
            .with_shared_state(backend="redis")
        )

    orch = benchmark(build)
    assert len(orch.config.agents) == 3


def test_benchmark_sdk_pipeline_build(benchmark):
    """Benchmark: build a 5-step sequential pipeline via Python SDK."""

    def build():
        return (
            Pipeline("bench-pipeline", team="eng")
            .step("ingest", ref="agents/ingest")
            .step("classify", ref="agents/classify")
            .step("enrich", ref="agents/enrich")
            .step("summarize", ref="agents/summarize")
            .step("review", ref="agents/review")
        )

    pipeline = benchmark(build)
    assert len(pipeline._steps) == 5


def test_benchmark_sdk_fanout_build(benchmark):
    """Benchmark: build a FanOut with 4 workers + merge via Python SDK."""

    def build():
        return (
            FanOut("bench-analysis", team="eng")
            .worker("sentiment", ref="agents/sentiment")
            .worker("topics", ref="agents/topics")
            .worker("summary", ref="agents/summary")
            .worker("entities", ref="agents/entities")
            .merge(ref="agents/aggregator")
            .with_merge_strategy("aggregate")
        )

    fanout = benchmark(build)
    assert fanout.config.supervisor_config is not None


def test_benchmark_sdk_supervisor_build(benchmark):
    """Benchmark: build a Supervisor with 3 workers via Python SDK."""

    def build():
        return (
            Supervisor("bench-workflow", team="eng")
            .with_supervisor_agent("coordinator", ref="agents/coordinator")
            .worker("researcher", ref="agents/researcher")
            .worker("writer", ref="agents/writer")
            .worker("reviewer", ref="agents/reviewer")
            .with_max_iterations(5)
        )

    workflow = benchmark(build)
    assert workflow.config.supervisor_config is not None


def test_benchmark_sdk_yaml_roundtrip(benchmark):
    """Benchmark: build → to_yaml() → from_yaml() round-trip."""
    orch = (
        Orchestration("bench-rt", strategy="router", team="eng")
        .add_agent("triage", ref="agents/triage")
        .add_agent("billing", ref="agents/billing")
        .add_agent("general", ref="agents/general")
        .with_route("triage", condition="billing", target="billing")
    )

    def round_trip():
        yaml_str = orch.to_yaml()
        return Orchestration.from_yaml(yaml_str)

    restored = benchmark(round_trip)
    assert restored.config.name == "bench-rt"


def test_benchmark_sdk_validate(benchmark):
    """Benchmark: validate a complex orchestration config."""
    orch = (
        Orchestration("bench-validate", strategy="router", team="eng")
        .add_agent("triage", ref="agents/triage")
        .add_agent("billing", ref="agents/billing")
        .add_agent("general", ref="agents/general")
        .with_route("triage", condition="billing", target="billing")
        .with_route("triage", condition="default", target="general")
        .with_shared_state(backend="redis")
        .with_deploy(target="cloud-run")
    )

    errors = benchmark(orch.validate)
    assert errors == []


# ---------------------------------------------------------------------------
# YAML library benchmarks (stdlib comparison)
# ---------------------------------------------------------------------------


def test_benchmark_yaml_safe_load(benchmark):
    """Benchmark: raw PyYAML safe_load of agent.yaml."""
    result = benchmark(yaml.safe_load, AGENT_YAML)
    assert result["name"] == "bench-agent"
