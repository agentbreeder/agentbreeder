"""Agent Garden orchestration engine.

Executes multi-agent orchestration strategies: router, sequential, parallel, hierarchical.
Each agent call is currently simulated — see TODO markers for real agent invocation.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from pydantic import BaseModel, Field

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

    def __init__(self, config: OrchestrationConfig) -> None:
        self.config = config

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
            agent_ref = self.config.agents.get(matched_agent)
            if agent_ref and agent_ref.fallback:
                fallback_entry = await self._call_agent(
                    agent_ref.fallback, input_message
                )
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
                    fallback_entry = await self._call_agent(
                        agent_ref.fallback, current_input
                    )
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

        tasks = [
            self._call_agent(agent_name, input_message)
            for agent_name in self.config.agents
        ]
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
        worker_tasks = [
            self._call_agent(worker, input_message)
            for worker in worker_names
        ]
        worker_entries = list(await asyncio.gather(*worker_tasks))
        trace.extend(worker_entries)

        # Supervisor aggregates worker outputs
        worker_outputs = "\n".join(
            f"[{e.agent_name}]: {e.output}" for e in worker_entries
        )
        aggregation_input = (
            f"Original: {input_message}\n\nWorker results:\n{worker_outputs}"
        )
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
    # Agent Invocation (Simulated)
    # -----------------------------------------------------------------

    async def _call_agent(self, agent_name: str, input_message: str) -> AgentTraceEntry:
        """Simulate calling a single agent.

        TODO: Replace with real agent invocation via the deployed agent's endpoint.
        This will need to:
        1. Look up the agent's endpoint URL from the registry
        2. POST the input_message to the agent's /invoke endpoint
        3. Parse the response and extract output, tokens, latency
        """
        latency_ms = random.randint(100, 500)
        await asyncio.sleep(latency_ms / 1000.0)

        # Simulated response
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
