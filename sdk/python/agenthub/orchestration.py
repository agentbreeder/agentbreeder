"""Full Code Orchestration SDK for AgentBreeder.

Provides builder-pattern classes for defining multi-agent workflows
programmatically. All classes serialize to valid orchestration.yaml
and share the same deploy pipeline as YAML-defined orchestrations.

Usage::

    from agenthub import Orchestration, Pipeline, FanOut, Supervisor
    from agenthub import KeywordRouter, IntentRouter, RoundRobinRouter

    # Router-based orchestration
    pipeline = (
        Orchestration("support-pipeline", strategy="router", team="eng")
        .add_agent("triage", ref="agents/triage-agent")
        .add_agent("billing", ref="agents/billing-agent")
        .add_agent("general", ref="agents/general-agent")
        .with_route("triage", condition="billing", target="billing")
        .with_route("triage", condition="default", target="general")
        .with_shared_state(backend="redis")
        .with_deploy(target="cloud-run")
    )
    pipeline.deploy()

    # Sequential pipeline
    research = (
        Pipeline("research-pipeline", team="eng")
        .step("researcher", ref="agents/researcher")
        .step("summarizer", ref="agents/summarizer")
        .step("reviewer", ref="agents/reviewer")
    )

    # Parallel fan-out + merge
    analysis = (
        FanOut("multi-analysis", team="eng")
        .worker("sentiment", ref="agents/sentiment")
        .worker("topics", ref="agents/topic-extractor")
        .merge(ref="agents/aggregator")
        .with_merge_strategy("aggregate")
    )

    # Hierarchical supervisor
    workflow = (
        Supervisor("research-workflow", team="eng")
        .with_supervisor_agent("coordinator", ref="agents/coordinator")
        .worker("researcher", ref="agents/researcher")
        .worker("writer", ref="agents/writer")
        .with_max_iterations(5)
    )
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

VALID_STRATEGIES = frozenset(
    {"router", "sequential", "parallel", "hierarchical", "supervisor", "fan_out_fan_in"}
)
VALID_MERGE_STRATEGIES = frozenset({"first_wins", "majority_vote", "aggregate", "custom"})


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RouteRule:
    """A condition-based routing rule for an agent."""

    condition: str
    target: str


@dataclass
class AgentEntry:
    """An agent reference within an orchestration."""

    ref: str
    routes: list[RouteRule] = field(default_factory=list)
    fallback: str | None = None


@dataclass
class SharedStateConfig:
    """Shared state configuration for inter-agent communication."""

    type: str = "session_context"
    backend: str = "redis"


@dataclass
class SupervisorConfig:
    """Configuration for supervisor/fan_out_fan_in orchestration strategies."""

    supervisor_agent: str | None = None
    merge_agent: str | None = None
    max_iterations: int = 3


@dataclass
class OrchestrationDeployConfig:
    """Deployment configuration for an orchestration."""

    target: str = "local"
    resources: dict[str, str] = field(default_factory=dict)


@dataclass
class OrchestrationConfig:
    """Complete configuration for a multi-agent orchestration."""

    name: str
    version: str = "1.0.0"
    description: str = ""
    team: str = "default"
    owner: str = ""
    strategy: str = "router"
    agents: dict[str, AgentEntry] = field(default_factory=dict)
    shared_state: SharedStateConfig = field(default_factory=SharedStateConfig)
    deploy: OrchestrationDeployConfig = field(default_factory=OrchestrationDeployConfig)
    supervisor_config: SupervisorConfig | None = None
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Base Orchestration class
# ---------------------------------------------------------------------------


class Orchestration:
    """Builder for multi-agent orchestration workflows.

    Supports a fluent builder pattern that serializes to valid
    orchestration.yaml, deployable via the standard pipeline::

        pipeline = (
            Orchestration("support", strategy="router", team="eng")
            .add_agent("triage", ref="agents/triage")
            .add_agent("billing", ref="agents/billing")
            .with_route("triage", condition="billing", target="billing")
            .with_shared_state(backend="redis")
        )
        yaml_str = pipeline.to_yaml()
        pipeline.deploy()
    """

    def __init__(self, name: str, strategy: str = "router", **kwargs: Any) -> None:
        self.config = OrchestrationConfig(name=name, strategy=strategy)
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)

    # ------------------------------------------------------------------
    # Builder methods (all return self for chaining)
    # ------------------------------------------------------------------

    def add_agent(
        self,
        name: str,
        ref: str,
        fallback: str | None = None,
    ) -> Orchestration:
        """Add an agent to the orchestration."""
        self.config.agents[name] = AgentEntry(ref=ref, fallback=fallback)
        return self

    def with_route(self, agent_name: str, condition: str, target: str) -> Orchestration:
        """Add a routing rule to an existing agent.

        Args:
            agent_name: Name of the agent to add the route to.
            condition: Keyword or intent that triggers this route.
            target: Name of the agent to route to.
        """
        if agent_name not in self.config.agents:
            raise ValueError(f"Agent '{agent_name}' not found. Call add_agent() first.")
        self.config.agents[agent_name].routes.append(RouteRule(condition=condition, target=target))
        return self

    def with_shared_state(
        self,
        state_type: str = "session_context",
        backend: str = "redis",
    ) -> Orchestration:
        """Configure shared state across agents."""
        self.config.shared_state = SharedStateConfig(type=state_type, backend=backend)
        return self

    def with_supervisor(
        self,
        supervisor_agent: str,
        merge_agent: str | None = None,
        max_iterations: int = 3,
    ) -> Orchestration:
        """Configure a supervisor agent for hierarchical/supervisor strategies."""
        self.config.supervisor_config = SupervisorConfig(
            supervisor_agent=supervisor_agent,
            merge_agent=merge_agent,
            max_iterations=max_iterations,
        )
        return self

    def with_deploy(self, target: str = "local", **resources: str) -> Orchestration:
        """Configure deployment target and optional resource limits."""
        self.config.deploy = OrchestrationDeployConfig(
            target=target,
            resources=dict(resources),
        )
        return self

    def tag(self, *tags: str) -> Orchestration:
        """Add discovery tags."""
        self.config.tags.extend(tags)
        return self

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate configuration. Returns list of error strings (empty = valid)."""
        errors: list[str] = []

        if not self.config.name:
            errors.append("name is required")
        elif not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", self.config.name):
            errors.append(
                "name must be lowercase alphanumeric with hyphens "
                f"(e.g., my-pipeline), got: {self.config.name!r}"
            )

        if not re.match(r"^\d+\.\d+\.\d+$", self.config.version):
            errors.append(f"version must be semver (e.g., 1.0.0), got: {self.config.version!r}")

        if self.config.strategy not in VALID_STRATEGIES:
            errors.append(
                f"strategy must be one of {sorted(VALID_STRATEGIES)}, "
                f"got: {self.config.strategy!r}"
            )

        if not self.config.agents:
            errors.append("at least one agent is required")

        if self.config.strategy in {"supervisor", "hierarchical"}:
            sc = self.config.supervisor_config
            if not sc or not sc.supervisor_agent:
                errors.append(
                    f"strategy '{self.config.strategy}' requires "
                    "with_supervisor(supervisor_agent=...) to be set"
                )

        if self.config.strategy == "fan_out_fan_in":
            sc = self.config.supervisor_config
            if not sc or not sc.merge_agent:
                errors.append(
                    "strategy 'fan_out_fan_in' requires with_supervisor(merge_agent=...) to be set"
                )

        # Validate route targets and fallbacks reference known agents
        for agent_name, entry in self.config.agents.items():
            for route in entry.routes:
                if route.target and route.target not in self.config.agents:
                    errors.append(
                        f"Route target '{route.target}' in agent '{agent_name}' "
                        "is not a known agent"
                    )
            if entry.fallback and entry.fallback not in self.config.agents:
                errors.append(
                    f"Fallback '{entry.fallback}' for agent '{agent_name}' is not a known agent"
                )

        return errors

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_yaml(self) -> str:
        """Serialize to valid orchestration.yaml content."""
        return _orchestration_to_yaml(self)

    @classmethod
    def from_yaml(cls, yaml_content: str) -> Orchestration:
        """Load an Orchestration from YAML string."""
        return _yaml_to_orchestration(yaml_content)

    @classmethod
    def from_file(cls, path: str) -> Orchestration:
        """Load an Orchestration from a YAML file path."""
        content = Path(path).read_text(encoding="utf-8")
        return cls.from_yaml(content)

    def save(self, path: str) -> None:
        """Write orchestration.yaml to disk."""
        Path(path).write_text(self.to_yaml(), encoding="utf-8")
        logger.info("Saved orchestration config to %s", path)

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def deploy(self, target: str | None = None) -> dict[str, Any]:
        """Deploy this orchestration (delegates to engine deploy pipeline).

        Returns a deploy info dict. The real implementation invokes the
        same engine pipeline used by ``agentbreeder orchestration deploy``.
        """
        deploy_target = target or self.config.deploy.target
        logger.info("Deploying orchestration '%s' to '%s'", self.config.name, deploy_target)
        return {
            "orchestration": self.config.name,
            "version": self.config.version,
            "strategy": self.config.strategy,
            "target": deploy_target,
            "status": "pending",
        }

    def __repr__(self) -> str:
        return (
            f"Orchestration(name={self.config.name!r}, "
            f"strategy={self.config.strategy!r}, "
            f"agents={list(self.config.agents)!r})"
        )


# ---------------------------------------------------------------------------
# Specialised subclasses
# ---------------------------------------------------------------------------


class Pipeline(Orchestration):
    """Sequential agent chain: output of each step feeds the next.

    Usage::

        pipeline = (
            Pipeline("research-pipeline", team="eng")
            .step("researcher", ref="agents/researcher")
            .step("summarizer", ref="agents/summarizer")
            .step("reviewer", ref="agents/reviewer")
            .with_deploy(target="cloud-run")
        )
    """

    def __init__(self, name: str, **kwargs: Any) -> None:
        super().__init__(name=name, strategy="sequential", **kwargs)
        self._steps: list[str] = []

    def step(self, name: str, ref: str, fallback: str | None = None) -> Pipeline:
        """Append an agent step to the pipeline."""
        self.add_agent(name=name, ref=ref, fallback=fallback)
        self._steps.append(name)
        return self

    def validate(self) -> list[str]:
        errors = super().validate()
        if len(self._steps) < 2:
            errors.append("Pipeline requires at least 2 steps")
        return errors


class FanOut(Orchestration):
    """Fan-out to parallel agents, then merge results with a merge agent.

    Usage::

        analysis = (
            FanOut("multi-analysis", team="eng")
            .worker("sentiment", ref="agents/sentiment")
            .worker("topics", ref="agents/topic-extractor")
            .worker("summary", ref="agents/summarizer")
            .merge(ref="agents/aggregator")
            .with_merge_strategy("aggregate")
        )
    """

    def __init__(self, name: str, **kwargs: Any) -> None:
        super().__init__(name=name, strategy="fan_out_fan_in", **kwargs)
        self._merge_strategy: str = "aggregate"

    def worker(self, name: str, ref: str) -> FanOut:
        """Add a parallel worker agent."""
        self.add_agent(name=name, ref=ref)
        return self

    def merge(self, ref: str, name: str = "merger") -> FanOut:
        """Set the merge agent that combines all worker outputs."""
        self.add_agent(name=name, ref=ref)
        if not self.config.supervisor_config:
            self.config.supervisor_config = SupervisorConfig()
        self.config.supervisor_config.merge_agent = name
        return self

    def with_merge_strategy(self, strategy: str) -> FanOut:
        """Set the merge strategy: first_wins | majority_vote | aggregate | custom."""
        if strategy not in VALID_MERGE_STRATEGIES:
            raise ValueError(
                f"merge_strategy must be one of {sorted(VALID_MERGE_STRATEGIES)}, "
                f"got: {strategy!r}"
            )
        self._merge_strategy = strategy
        return self

    def validate(self) -> list[str]:
        errors = super().validate()
        if not self.config.supervisor_config or not self.config.supervisor_config.merge_agent:
            errors.append("FanOut requires a merge agent — call .merge(ref=...)")
        return errors


class Supervisor(Orchestration):
    """Hierarchical orchestration: a supervisor agent delegates to workers.

    Usage::

        workflow = (
            Supervisor("research-workflow", team="eng")
            .with_supervisor_agent("coordinator", ref="agents/coordinator")
            .worker("researcher", ref="agents/researcher")
            .worker("writer", ref="agents/writer")
            .worker("reviewer", ref="agents/reviewer")
            .with_max_iterations(5)
        )
    """

    def __init__(self, name: str, **kwargs: Any) -> None:
        super().__init__(name=name, strategy="supervisor", **kwargs)

    def with_supervisor_agent(self, name: str, ref: str) -> Supervisor:
        """Set the supervisor agent (plans and delegates to workers)."""
        self.add_agent(name=name, ref=ref)
        if not self.config.supervisor_config:
            self.config.supervisor_config = SupervisorConfig()
        self.config.supervisor_config.supervisor_agent = name
        return self

    def worker(self, name: str, ref: str, fallback: str | None = None) -> Supervisor:
        """Add a worker agent under the supervisor."""
        self.add_agent(name=name, ref=ref, fallback=fallback)
        return self

    def with_max_iterations(self, max_iterations: int) -> Supervisor:
        """Set the maximum supervisor/worker iteration count."""
        if not self.config.supervisor_config:
            self.config.supervisor_config = SupervisorConfig()
        self.config.supervisor_config.max_iterations = max_iterations
        return self

    def validate(self) -> list[str]:
        errors = super().validate()
        sc = self.config.supervisor_config
        if not sc or not sc.supervisor_agent:
            errors.append("Supervisor requires with_supervisor_agent() to be called")
        return errors


# ---------------------------------------------------------------------------
# Router classes
# ---------------------------------------------------------------------------


class Router(ABC):
    """Base class for custom routing logic in Full Code orchestrations.

    Subclass this to implement routing that YAML conditions can't express::

        class VIPRouter(Router):
            async def route(self, message: str, context: dict) -> str:
                if context.get("user_tier") == "enterprise":
                    return "priority-agent"
                return "standard-agent"

        orch = Orchestration("support", strategy="router")
        orch._custom_router = VIPRouter()
    """

    @abstractmethod
    async def route(self, message: str, context: dict[str, Any]) -> str:
        """Return the name of the agent to route this message to."""
        ...


class KeywordRouter(Router):
    """Routes messages to agents based on keyword presence.

    Usage::

        router = KeywordRouter(
            rules={
                "billing": "billing-agent",
                "refund": "billing-agent",
                "broken": "tech-support",
            },
            default="general-agent",
        )
    """

    def __init__(
        self,
        rules: dict[str, str],
        default: str,
        case_sensitive: bool = False,
    ) -> None:
        self.rules = rules
        self.default = default
        self.case_sensitive = case_sensitive

    async def route(self, message: str, context: dict[str, Any]) -> str:
        text = message if self.case_sensitive else message.lower()
        for keyword, target in self.rules.items():
            k = keyword if self.case_sensitive else keyword.lower()
            if k in text:
                return target
        return self.default


class IntentRouter(Router):
    """Routes based on a pre-classified intent in the context dict.

    Expects ``context["intent"]`` to contain the intent label::

        router = IntentRouter(
            intents={
                "billing_inquiry": "billing-agent",
                "technical_support": "tech-agent",
                "general_question": "general-agent",
            },
            default="general-agent",
        )
    """

    def __init__(self, intents: dict[str, str], default: str) -> None:
        self.intents = intents
        self.default = default

    async def route(self, message: str, context: dict[str, Any]) -> str:
        intent = context.get("intent", "")
        return self.intents.get(str(intent), self.default)


class RoundRobinRouter(Router):
    """Distributes messages to agents in round-robin order.

    Usage::

        router = RoundRobinRouter(agents=["agent-a", "agent-b", "agent-c"])
    """

    def __init__(self, agents: list[str]) -> None:
        self.agents = agents
        self._idx: int = 0

    async def route(self, message: str, context: dict[str, Any]) -> str:
        target = self.agents[self._idx % len(self.agents)]
        self._idx += 1
        return target


class ClassifierRouter(Router):
    """Base for model-based classification routing.

    Override ``classify()`` to implement LLM or ML-based intent detection::

        class SupportClassifier(ClassifierRouter):
            async def classify(self, message: str) -> str:
                # Call your model or classifier API
                response = await my_model.predict(message)
                return response.label

            async def route(self, message, context):
                intent = await self.classify(message)
                # Add custom business rules on top of classification
                if intent == "billing" and context.get("user_tier") == "enterprise":
                    return "priority-billing"
                return self.label_to_agent.get(intent, self.default)
    """

    def __init__(self, label_to_agent: dict[str, str], default: str) -> None:
        self.label_to_agent = label_to_agent
        self.default = default

    async def classify(self, message: str) -> str:
        """Override to implement model-based classification. Default: return default label."""
        return self.default

    async def route(self, message: str, context: dict[str, Any]) -> str:
        label = await self.classify(message)
        return self.label_to_agent.get(label, self.default)


# ---------------------------------------------------------------------------
# YAML serialization
# ---------------------------------------------------------------------------


def _orchestration_to_yaml(orch: Orchestration) -> str:
    """Serialize an Orchestration instance to valid orchestration.yaml content."""
    d: dict[str, Any] = {}

    # Identity block
    d["name"] = orch.config.name
    d["version"] = orch.config.version
    if orch.config.description:
        d["description"] = orch.config.description
    if orch.config.team != "default":
        d["team"] = orch.config.team
    if orch.config.owner:
        d["owner"] = orch.config.owner
    if orch.config.tags:
        d["tags"] = orch.config.tags

    # Strategy
    d["strategy"] = orch.config.strategy

    # Agents
    agents_out: dict[str, Any] = {}
    for agent_name, entry in orch.config.agents.items():
        a: dict[str, Any] = {"ref": entry.ref}
        if entry.routes:
            a["routes"] = [{"condition": r.condition, "target": r.target} for r in entry.routes]
        if entry.fallback:
            a["fallback"] = entry.fallback
        agents_out[agent_name] = a
    d["agents"] = agents_out

    # Shared state (only emit if non-default)
    ss = orch.config.shared_state
    if ss.type != "session_context" or ss.backend != "redis":
        d["shared_state"] = {"type": ss.type, "backend": ss.backend}

    # Supervisor config
    if orch.config.supervisor_config:
        sc = orch.config.supervisor_config
        sc_out: dict[str, Any] = {}
        if sc.supervisor_agent:
            sc_out["supervisor_agent"] = sc.supervisor_agent
        if sc.merge_agent:
            sc_out["merge_agent"] = sc.merge_agent
        if sc.max_iterations != 3:
            sc_out["max_iterations"] = sc.max_iterations
        if sc_out:
            d["supervisor_config"] = sc_out

    # Deploy (only emit if non-default)
    dep = orch.config.deploy
    if dep.target != "local" or dep.resources:
        dep_out: dict[str, Any] = {"target": dep.target}
        if dep.resources:
            dep_out["resources"] = dep.resources
        d["deploy"] = dep_out

    return yaml.dump(d, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _yaml_to_orchestration(yaml_str: str) -> Orchestration:
    """Parse orchestration.yaml content into the appropriate Orchestration subclass."""
    data = yaml.safe_load(yaml_str)
    if not isinstance(data, dict):
        raise ValueError("Invalid YAML: expected a mapping at the top level")

    strategy = data.get("strategy", "router")

    # Instantiate the right subclass without calling __init__
    if strategy == "sequential":
        orch: Orchestration = object.__new__(Pipeline)
        orch._steps = []  # type: ignore[attr-defined]
    elif strategy == "fan_out_fan_in":
        orch = object.__new__(FanOut)
        orch._merge_strategy = "aggregate"
    elif strategy == "supervisor":
        orch = object.__new__(Supervisor)
    else:
        orch = object.__new__(Orchestration)

    # Build config
    orch.config = OrchestrationConfig(
        name=data.get("name", ""),
        version=data.get("version", "1.0.0"),
        description=data.get("description", ""),
        team=data.get("team", "default"),
        owner=data.get("owner", ""),
        strategy=strategy,
        tags=data.get("tags", []),
    )

    # Agents
    for agent_name, agent_data in (data.get("agents") or {}).items():
        if not isinstance(agent_data, dict):
            continue
        routes = [
            RouteRule(condition=r["condition"], target=r["target"])
            for r in agent_data.get("routes", [])
            if isinstance(r, dict)
        ]
        orch.config.agents[agent_name] = AgentEntry(
            ref=agent_data.get("ref", ""),
            routes=routes,
            fallback=agent_data.get("fallback"),
        )

    # Shared state
    if "shared_state" in data:
        ss = data["shared_state"]
        orch.config.shared_state = SharedStateConfig(
            type=ss.get("type", "session_context"),
            backend=ss.get("backend", "redis"),
        )

    # Supervisor config
    if "supervisor_config" in data:
        sc = data["supervisor_config"]
        orch.config.supervisor_config = SupervisorConfig(
            supervisor_agent=sc.get("supervisor_agent"),
            merge_agent=sc.get("merge_agent"),
            max_iterations=sc.get("max_iterations", 3),
        )

    # Deploy
    if "deploy" in data:
        dep = data["deploy"]
        orch.config.deploy = OrchestrationDeployConfig(
            target=dep.get("target", "local"),
            resources=dep.get("resources") or {},
        )

    # Populate Pipeline steps from ordered agents
    if isinstance(orch, Pipeline):
        orch._steps = list(orch.config.agents.keys())

    return orch
