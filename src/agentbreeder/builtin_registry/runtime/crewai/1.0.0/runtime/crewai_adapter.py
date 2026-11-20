"""The CrewAI binding of the runtime kit (multi-framework spec §8.5).

- Model: CrewAI's native `LLM(model="openai/<alias>")` (the OpenAI SDK; CrewAI does not use
  LiteLLM) at the kit's loopback proxy, its own retries off. `LLM` takes no HTTP client, so the
  running turn's token goes in through an interceptor (§7.2.2); the API key is a placeholder that
  the token replaces on every request. The proxy owns retries, cost, the step cap and coded
  errors.
- Retries: `max_retries=0` turns the OpenAI SDK's off. CrewAI retries a throttled call itself,
  above the client, up to 3 attempts with 1-8 s backoff, and the proxy answers its refusals
  (including MODEL_STEP_LIMIT) with 429. That loop has no switch; it skips itself when its own
  re-entrancy guard (a context variable) is set, so the adapter sets it for the turn. If CrewAI
  renamed it, the loop would run again and the proxy's stored status would still end it
  (§7.2.7).
- Agent: one CrewAI `Agent` per turn (its backstory carries the customer's notes), run with
  `Agent.kickoff_async(messages)`: the turn's history goes in as structured messages (§7.5), and
  no `Crew` is built, so CrewAI's crew-level task-output store is never used (see
  `crewai_confine.py`).
  Everything that would make a model call the author did not ask for is off: memory, planning,
  reasoning, delegation, code execution, context-window summarising, the tool cache and the
  agent's task retries. So is "better compaction", which writes a large tool result to
  `/tmp/crewai-tool-results-*` and gives the model an extra, undeclared tool to read it back.
  `max_iter` = max_model_calls + 2, a backstop the proxy's cap reaches first.
- System prompt: CrewAI writes its own system message around the backstory, so the proxy's
  main-loop test (the system prompt's hash plus the tool set, §7.2.4) is told the system
  message CrewAI actually sends for this turn's agent (`main_loop_system`).
- Tools: one `BaseTool` per kit function, its `args_schema` a pydantic model generated from the
  declared JSON Schema (`crewai_schema.py`), shown to the model as declared. CrewAI's own argument
  validation is bypassed: the kit validates against the declared schema and writes the audit row
  for a refused call (§7.3). A tool call the agent did not declare is answered to the model by
  CrewAI ("Tool not found"); nothing runs.
- Traces: `openinference-instrumentation-crewai` (AGENT, CHAIN and TOOL spans, no LLM spans; the
  kit relabels its TOOL spans CHAIN, since the kit writes its own). LLM calls are in the proxy's
  `model_called` rows.

CrewAI's telemetry, tracking and trace upload are switched off, and its storage confined, by the
entry point (`server.py`) before CrewAI is imported.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import crewai_confine
import httpx
from crewai import LLM, Agent
from crewai.llms import retry as crewai_retry
from crewai.llms.base_llm import EmptyLLMResponseError
from crewai.llms.hooks.base import BaseInterceptor
from crewai.tools import BaseTool
from crewai.utilities.prompts import SystemPromptResult
from crewai.utilities.string_utils import sanitize_tool_name
from crewai_schema import args_model, given
from kit.context import CURRENT, TOKEN
from kit.turn import CLIENT_TIMEOUT

LOGGERS = ("crewai", "crewai_core", "chromadb", "openai", "openinference", "httpx", "LiteLLM")
# CrewAI warns on every call that it estimates token counts for an unknown model (F0); the
# counts that matter are the gateway's, in the proxy's tally.
NOISY = ("crewai.llms.base_llm",)
# The proxy refuses any other key: the turn token replaces this on every request.
PLACEHOLDER_KEY = "agentbreeder-turn-token"
ROLE_LIMIT = 200
# CrewAI substitutes these in its system prompt after the backstory is in it, so a customer's
# note holding `{input}` would put the customer's message into the system prompt. They are
# written with a full-width brace instead.
PLACEHOLDERS = ("{input}", "{tools}", "{tool_names}", "{goal}", "{role}", "{backstory}")
# CrewAI's rate-limit retry loop does nothing while this is set (see the module docstring).
RETRY_GUARD = getattr(crewai_retry, "_active_llm_rate_limit_retry", None)


class TurnToken(BaseInterceptor[httpx.Request, httpx.Response]):
    """Sends the running turn's token as the API key (spec §7.2.2): CrewAI's `LLM` takes no HTTP
    client, only an interceptor on its transport."""

    @staticmethod
    def _apply(message: httpx.Request) -> httpx.Request:
        token = TOKEN.get()
        if token:
            message.headers["Authorization"] = f"Bearer {token}"
        else:
            message.headers.pop("Authorization", None)
        return message

    def on_outbound(self, message: httpx.Request) -> httpx.Request:
        return self._apply(message)

    def on_inbound(self, message: httpx.Response) -> httpx.Response:
        return message

    async def aon_outbound(self, message: httpx.Request) -> httpx.Request:
        return self._apply(message)

    async def aon_inbound(self, message: httpx.Response) -> httpx.Response:
        return message


def _never_cache(_args: Any = None, _result: Any = None) -> bool:
    return False


class KitTool(BaseTool):
    """A kit function as a CrewAI tool. Every call goes through the kit's wrapper (argument
    check against the declared schema, audit row, TOOL span, the reply's `tools`)."""

    function: Any = None
    loop: Any = None  # the kit's event loop, for calls CrewAI makes from worker threads

    def _validate_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        # The kit validates against the declared JSON Schema and audits a refused call; CrewAI's
        # pydantic pass would refuse it unaudited, and fill unset optional arguments with None.
        return kwargs

    async def _arun(self, **arguments: Any) -> str:
        return await self.function(**given(self.function.parameters, arguments))

    def _run(self, **arguments: Any) -> str:
        # CrewAI runs native tool calls from worker threads that copied the turn's context; the
        # kit's wrapper is async and belongs to the kit's loop. Waiting on that loop from its own
        # thread would deadlock it.
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and running is self.loop:
            raise RuntimeError("a CrewAI tool ran synchronously on the kit's event loop")
        call = self.function(**given(self.function.parameters, arguments))
        future = asyncio.run_coroutine_threadsafe(call, self.loop)
        return future.result()


def as_tool(function, loop) -> KitTool:
    name = function.name
    if sanitize_tool_name(name) != name:
        # CrewAI would rename it, and the model's calls would not match the declared tool.
        raise RuntimeError(f"tool name {name!r} is not usable by CrewAI as declared")
    return KitTool(
        name=name,
        description=function.description or name,
        args_schema=args_model(name, function.parameters),
        function=function,
        loop=loop,
        cache_function=_never_cache,
    )


def inert(text: str) -> str:
    """Text with CrewAI's prompt placeholders made inert (see PLACEHOLDERS)."""
    for placeholder in PLACEHOLDERS:
        text = text.replace(placeholder, "\uff5b" + placeholder[1:])
    return text


def main_loop_system(agent: Agent) -> str | None:
    """The system message CrewAI sends for this agent's own reasoning steps, built by CrewAI's
    own prompt code, or None when it sends none (then every call counts as hidden, which fails
    safe, spec §7.2.4). The adapter makes every placeholder in the agent's text inert, so the
    executor's later substitutions leave it unchanged."""
    prompt, _, _ = agent._build_execution_prompt(list(agent.tools or []))
    if not isinstance(prompt, SystemPromptResult) or not prompt.system:
        return None
    return prompt.system


# What CrewAI's executor substitutes when it ends with no answer: not an answer to the customer.
NO_OUTPUT = "Agent completed execution but produced no final output."


def reply_text(output) -> str:
    """The agent's final answer, or "" (the kit's fallback reply) for CrewAI's own no-answer
    text."""
    raw = getattr(output, "raw", None)
    if not isinstance(raw, str) or raw.strip() == NO_OUTPUT:
        return ""
    return raw


class CrewAIAdapter:
    framework = "crewai"

    def __init__(self):
        self.kit = None
        self.llm = None
        self.loop = None

    def instrument(self, provider) -> None:
        from openinference.instrumentation.crewai import CrewAIInstrumentor

        CrewAIInstrumentor().instrument(tracer_provider=provider)

    def build(self, kit) -> None:
        self.kit = kit
        for name in NOISY:
            logging.getLogger(name).setLevel(logging.ERROR)
        self.loop = asyncio.get_running_loop()
        self.llm = LLM(
            model=f"openai/{kit.alias}",
            base_url=kit.base_url,
            api_key=PLACEHOLDER_KEY,
            max_retries=0,
            timeout=CLIENT_TIMEOUT,
            interceptor=TurnToken(),
        )
        # Refuse at start-up, not on the first turn, a tool CrewAI cannot carry as declared, and
        # check the CrewAI internals this adapter relies on, so a different CrewAI fails here.
        for function in kit.functions:
            as_tool(function, self.loop)
        self.self_check()

    def self_check(self) -> None:
        if RETRY_GUARD is None:
            raise RuntimeError("CrewAI's rate-limit retry guard is missing")
        probe = Agent(role="r", goal="g", backstory="b", llm=self.llm, use_better_compaction=False,
                      respect_context_window=False, memory=None)  # fmt: skip
        if probe.use_better_compaction is not False or probe.respect_context_window is not False:
            raise RuntimeError("CrewAI's compaction settings are not honoured")
        if not main_loop_system(probe):
            raise RuntimeError("CrewAI's system prompt cannot be computed")

    def agent_for(self, turn) -> Agent:
        config = self.kit.config
        return Agent(
            role=inert(str(config.get("name") or "assistant")[:ROLE_LIMIT]),
            goal=inert(str(config.get("description") or "Help the customer.")[: 4 * ROLE_LIMIT]),
            backstory=inert(turn.system),
            llm=self.llm,
            tools=[as_tool(f, self.loop) for f in turn.functions],
            allow_delegation=False,
            allow_code_execution=False,
            memory=None,
            cache=False,
            respect_context_window=False,
            use_better_compaction=False,
            checkpoint=None,
            reasoning=False,
            planning=False,
            inject_date=False,
            max_iter=self.kit.max_steps + 2,
            max_retry_limit=0,
            verbose=False,
        )

    @staticmethod
    def messages(turn) -> list[dict]:
        return [*({"role": m["role"], "content": m["content"]} for m in turn.history),
                {"role": "user", "content": turn.message}]  # fmt: skip

    async def run_turn(self, turn) -> str:
        agent = self.agent_for(turn)
        # The proxy tells the agent's own steps from hidden calls by the system prompt's hash and
        # the tool set (§7.2.4); CrewAI wraps the prompt in its own, so the proxy is given that.
        state = CURRENT.get().proxy_state
        if state is not None:
            state.system = main_loop_system(agent)
        guard = RETRY_GUARD.set(True) if RETRY_GUARD is not None else None
        try:
            output = await agent.kickoff_async(self.messages(turn))
        except EmptyLLMResponseError:
            # The model answered with nothing, twice (CrewAI asks once more itself): no answer,
            # so the kit's fallback reply, as on LangGraph, not a 500.
            return ""
        finally:
            if guard is not None:
                RETRY_GUARD.reset(guard)
            crewai_confine.sweep()
        return reply_text(output)

    def close(self) -> None:
        pass
