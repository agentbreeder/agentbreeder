"""Agent Garden orchestration engine.

Executes multi-agent orchestration strategies: router, sequential, parallel,
hierarchical, supervisor, and fan_out_fan_in.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from pydantic import BaseModel, Field

from engine.a2a.client import AgentInvocationClient
from engine.orchestration_parser import OrchestrationConfig

logger = logging.getLogger(__name__)


class AgentTraceEntry(BaseModel):
    """Record of a single agent invocation within an orchestration run."""

    agent_name: str
    input: str
    output: str
    latency_ms: int
    tokens: int
    status: str  # "success" | "error" | "fallback"


class OrchestrationResult(BaseModel):
    """Result of executing an orchestration."""

    orchestration_name: str
    strategy: str
    input_message: str
    output: str
    agent_trace: list[AgentTraceEntry] = Field(default_factory=list)
    total_latency_ms: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0


class Orchestrator:
    """Execute multi-agent orchestration strategies."""

    def __init__(
        self,
        config: OrchestrationConfig,
        agent_endpoints: dict[str, str] | None = None,
        auth_token: str | None = None,
    ) -> None:
        self.config = config
        self._endpoints = agent_endpoints or {}
        self._client = AgentInvocationClient(auth_token=auth_token) if self._endpoints else None

    async def execute(
        self, input_message: str, context: dict[str, Any] | None = None
    ) -> OrchestrationResult:
        """Dispatch to the appropriate strategy handler."""
        ctx = context or {}
        strategy = self.config.strategy

        logger.info(
            "Executing orchestration",
            extra={
                "orchestration": self.config.name,
                "strategy": strategy,
            },
        )

        if strategy == "router":
            return await self._execute_router(input_message, ctx)
        elif strategy == "sequential":
            return await self._execute_sequential(input_message, ctx)
        elif strategy == "parallel":
            return await self._execute_parallel(input_message, ctx)
        elif strategy == "hierarchical":
            return await self._execute_hierarchical(input_message, ctx)
        elif strategy == "supervisor":
            return await self._execute_supervisor(input_message, ctx)
        elif strategy == "fan_out_fan_in":
            return await self._execute_fan_out_fan_in(input_message, ctx)
        else:
            msg = f"Unknown strategy: {strategy}"
            raise ValueError(msg)

    # -----------------------------------------------------------------
    # Strategy Implementations
    # -----------------------------------------------------------------

    async def _execute_router(
        self, input_message: str, context: dict[str, Any]
    ) -> OrchestrationResult:
        """Match routing conditions against input, route to matching agent.

        Uses simple keyword matching for conditions.
        Falls back to first agent if no match.
        """
        start = time.monotonic()
        trace: list[AgentTraceEntry] = []
        matched_agent: str | None = None

        # Check each agent's routing rules
        for _agent_name, agent_ref in self.config.agents.items():
            for rule in agent_ref.routes:
                # Simple keyword matching: condition is a keyword to look for
                if rule.condition.lower() in input_message.lower():
                    matched_agent = rule.target
                    break
            if matched_agent:
                break

        # Fall back to first agent if no match
        if not matched_agent:
            matched_agent = next(iter(self.config.agents))

        entry = await self._call_agent(matched_agent, input_message)
        trace.append(entry)

        # Handle fallback on error
        if entry.status == "error":
            agent_ref_or_none = self.config.agents.get(matched_agent)
            if agent_ref_or_none and agent_ref_or_none.fallback:
                fallback_entry = await self._call_agent(agent_ref_or_none.fallback, input_message)
                fallback_entry.status = "fallback"
                trace.append(fallback_entry)
                entry = fallback_entry

        total_ms = int((time.monotonic() - start) * 1000)
        return OrchestrationResult(
            orchestration_name=self.config.name,
            strategy="router",
            input_message=input_message,
            output=trace[-1].output,
            agent_trace=trace,
            total_latency_ms=total_ms,
            total_tokens=sum(t.tokens for t in trace),
            total_cost=sum(t.tokens for t in trace) * 0.00001,
        )

    async def _execute_sequential(
        self, input_message: str, context: dict[str, Any]
    ) -> OrchestrationResult:
        """Chain agents: output of agent N becomes input of agent N+1."""
        start = time.monotonic()
        trace: list[AgentTraceEntry] = []
        current_input = input_message

        for agent_name in self.config.agents:
            entry = await self._call_agent(agent_name, current_input)
            trace.append(entry)

            if entry.status == "error":
                agent_ref = self.config.agents.get(agent_name)
                if agent_ref and agent_ref.fallback:
                    fallback_entry = await self._call_agent(agent_ref.fallback, current_input)
                    fallback_entry.status = "fallback"
                    trace.append(fallback_entry)
                    current_input = fallback_entry.output
                else:
                    break
            else:
                current_input = entry.output

        total_ms = int((time.monotonic() - start) * 1000)
        return OrchestrationResult(
            orchestration_name=self.config.name,
            strategy="sequential",
            input_message=input_message,
            output=trace[-1].output if trace else "",
            agent_trace=trace,
            total_latency_ms=total_ms,
            total_tokens=sum(t.tokens for t in trace),
            total_cost=sum(t.tokens for t in trace) * 0.00001,
        )

    async def _execute_parallel(
        self, input_message: str, context: dict[str, Any]
    ) -> OrchestrationResult:
        """Fan-out to all agents concurrently, merge results."""
        start = time.monotonic()

        tasks = [self._call_agent(agent_name, input_message) for agent_name in self.config.agents]
        trace = list(await asyncio.gather(*tasks))

        # Merge outputs — concatenate with agent labels
        merged_parts: list[str] = []
        for entry in trace:
            merged_parts.append(f"[{entry.agent_name}]: {entry.output}")
        merged_output = "\n\n".join(merged_parts)

        total_ms = int((time.monotonic() - start) * 1000)
        return OrchestrationResult(
            orchestration_name=self.config.name,
            strategy="parallel",
            input_message=input_message,
            output=merged_output,
            agent_trace=trace,
            total_latency_ms=total_ms,
            total_tokens=sum(t.tokens for t in trace),
            total_cost=sum(t.tokens for t in trace) * 0.00001,
        )

    async def _execute_hierarchical(
        self, input_message: str, context: dict[str, Any]
    ) -> OrchestrationResult:
        """First agent is supervisor, delegates to workers, then aggregates."""
        start = time.monotonic()
        trace: list[AgentTraceEntry] = []

        agent_names = list(self.config.agents.keys())
        if not agent_names:
            return OrchestrationResult(
                orchestration_name=self.config.name,
                strategy="hierarchical",
                input_message=input_message,
                output="",
                total_latency_ms=0,
            )

        supervisor_name = agent_names[0]
        worker_names = agent_names[1:]

        # Supervisor analyzes the input
        supervisor_entry = await self._call_agent(supervisor_name, input_message)
        trace.append(supervisor_entry)

        # Supervisor delegates to workers
        worker_tasks = [self._call_agent(worker, input_message) for worker in worker_names]
        worker_entries = list(await asyncio.gather(*worker_tasks))
        trace.extend(worker_entries)

        # Supervisor aggregates worker outputs
        worker_outputs = "\n".join(f"[{e.agent_name}]: {e.output}" for e in worker_entries)
        aggregation_input = f"Original: {input_message}\n\nWorker results:\n{worker_outputs}"
        aggregation_entry = await self._call_agent(supervisor_name, aggregation_input)
        aggregation_entry.agent_name = f"{supervisor_name} (aggregation)"
        trace.append(aggregation_entry)

        total_ms = int((time.monotonic() - start) * 1000)
        return OrchestrationResult(
            orchestration_name=self.config.name,
            strategy="hierarchical",
            input_message=input_message,
            output=aggregation_entry.output,
            agent_trace=trace,
            total_latency_ms=total_ms,
            total_tokens=sum(t.tokens for t in trace),
            total_cost=sum(t.tokens for t in trace) * 0.00001,
        )

    # -----------------------------------------------------------------
    # Agent Invocation
    # -----------------------------------------------------------------

    async def _call_agent(self, agent_name: str, input_message: str) -> AgentTraceEntry:
        """Call a deployed agent via HTTP, falling back to simulation."""
        endpoint = self._endpoints.get(agent_name)

        if endpoint and self._client:
            result = await self._client.invoke(endpoint, input_message)
            return AgentTraceEntry(
                agent_name=agent_name,
                input=input_message,
                output=result.output,
                latency_ms=result.latency_ms,
                tokens=result.tokens,
                status=result.status,
            )

        # Fallback: simulated response (no endpoint configured)
        latency_ms = random.randint(100, 500)
        await asyncio.sleep(latency_ms / 1000.0)
        tokens = random.randint(50, 200)
        output = f"Response from {agent_name}: Processed input '{input_message[:80]}'"

        return AgentTraceEntry(
            agent_name=agent_name,
            input=input_message,
            output=output,
            latency_ms=latency_ms,
            tokens=tokens,
            status="success",
        )

    # -----------------------------------------------------------------
    # New Strategies: supervisor + fan_out_fan_in (Phase 4)
    # -----------------------------------------------------------------

    async def _execute_supervisor(
        self, input_message: str, context: dict[str, Any]
    ) -> OrchestrationResult:
        """Supervisor agent decides which workers to invoke and synthesizes results.

        The supervisor_config in the orchestration determines which agent
        is the supervisor. The supervisor is called first with the input,
        then workers are invoked based on supervisor output, and the
        supervisor aggregates the final result.
        """
        start = time.monotonic()
        trace: list[AgentTraceEntry] = []

        agent_names = list(self.config.agents.keys())
        if not agent_names:
            return OrchestrationResult(
                orchestration_name=self.config.name,
                strategy="supervisor",
                input_message=input_message,
                output="",
                total_latency_ms=0,
            )

        # First agent is the supervisor by default
        supervisor_config = getattr(self.config, "supervisor_config", None) or {}
        supervisor_name = supervisor_config.get("supervisor_agent", agent_names[0])
        worker_names = [n for n in agent_names if n != supervisor_name]

        # Step 1: Supervisor plans
        plan_input = (
            f"You are a supervisor. Analyze this request and decide which workers "
            f"to delegate to.\nAvailable workers: {', '.join(worker_names)}\n\n"
            f"Request: {input_message}"
        )
        plan_entry = await self._call_agent(supervisor_name, plan_input)
        trace.append(plan_entry)

        # Step 2: Invoke workers (all in parallel for now)
        worker_tasks = [self._call_agent(w, input_message) for w in worker_names]
        worker_entries = list(await asyncio.gather(*worker_tasks))
        trace.extend(worker_entries)

        # Step 3: Supervisor synthesizes
        worker_outputs = "\n".join(f"[{e.agent_name}]: {e.output}" for e in worker_entries)
        synthesis_input = (
            f"Original request: {input_message}\n\n"
            f"Worker results:\n{worker_outputs}\n\n"
            f"Synthesize the final response."
        )
        synthesis_entry = await self._call_agent(supervisor_name, synthesis_input)
        synthesis_entry.agent_name = f"{supervisor_name} (synthesis)"
        trace.append(synthesis_entry)

        total_ms = int((time.monotonic() - start) * 1000)
        return OrchestrationResult(
            orchestration_name=self.config.name,
            strategy="supervisor",
            input_message=input_message,
            output=synthesis_entry.output,
            agent_trace=trace,
            total_latency_ms=total_ms,
            total_tokens=sum(t.tokens for t in trace),
            total_cost=sum(t.tokens for t in trace) * 0.00001,
        )

    async def _execute_fan_out_fan_in(
        self, input_message: str, context: dict[str, Any]
    ) -> OrchestrationResult:
        """Fan-out to all agents, then fan-in with a designated merger agent.

        Like parallel, but uses the last agent (or configured merge_agent)
        to combine results instead of simple concatenation.
        """
        start = time.monotonic()
        trace: list[AgentTraceEntry] = []

        agent_names = list(self.config.agents.keys())
        if not agent_names:
            return OrchestrationResult(
                orchestration_name=self.config.name,
                strategy="fan_out_fan_in",
                input_message=input_message,
                output="",
                total_latency_ms=0,
            )

        supervisor_config = getattr(self.config, "supervisor_config", None) or {}
        merge_agent = supervisor_config.get("merge_agent", agent_names[-1])
        fan_out_agents = [n for n in agent_names if n != merge_agent]

        # Fan-out: invoke all non-merge agents in parallel
        fan_tasks = [self._call_agent(name, input_message) for name in fan_out_agents]
        fan_entries = list(await asyncio.gather(*fan_tasks))
        trace.extend(fan_entries)

        # Fan-in: merge agent combines results
        fan_outputs = "\n".join(f"[{e.agent_name}]: {e.output}" for e in fan_entries)
        merge_input = (
            f"Original request: {input_message}\n\n"
            f"Results from agents:\n{fan_outputs}\n\n"
            f"Combine these results into a single coherent response."
        )
        merge_entry = await self._call_agent(merge_agent, merge_input)
        merge_entry.agent_name = f"{merge_agent} (merge)"
        trace.append(merge_entry)

        total_ms = int((time.monotonic() - start) * 1000)
        return OrchestrationResult(
            orchestration_name=self.config.name,
            strategy="fan_out_fan_in",
            input_message=input_message,
            output=merge_entry.output,
            agent_trace=trace,
            total_latency_ms=total_ms,
            total_tokens=sum(t.tokens for t in trace),
            total_cost=sum(t.tokens for t in trace) * 0.00001,
        )
