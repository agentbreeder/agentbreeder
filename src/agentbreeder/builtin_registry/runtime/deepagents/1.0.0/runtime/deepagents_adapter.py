"""The Deep Agents binding of the runtime kit (multi-framework spec §8.3).

- Model: `ChatOpenAI` at the kit's loopback proxy with the kit's turn-aware HTTP clients, its own
  retries off, as runtime/langgraph (the proxy owns retries, cost, the step cap and coded
  errors). Its API key is a placeholder: the turn token replaces it on every request.
- Agent: `deepagents.create_deep_agent(model, tools=..., backend=StateBackend())`, built once at
  start-up, with no checkpointer, store, skills, memory files, interrupts or extra sub-agents.
  The state backend is passed explicitly: its file tools act on the turn's graph state only, and
  it has no `execute` tool. The built-in tools the model is offered beside the agent's own
  (`ls`, `read_file`, `write_file`, `edit_file`, `delete`, `glob`, `grep`, `task`) are the
  manifest's `reserved_tool_names`.
- The built-in `task` sub-agent is kept (D10). It uses the same model object, so its calls go
  through the same proxy with the same turn token and count against the turn's step cap; its
  calls to the agent's tools go through the kit's wrapper like any other (audit row, TOOL span,
  the reply's `tools`), with the customer the server set.
- System prompt: the turn's (the agent's prompt plus the customer's notes) goes first, then any
  section Deep Agents' middleware added, as `create_deep_agent(system_prompt=...)` would place
  them. With 0.7.32, this set-up and a ChatOpenAI model it adds none: its guidance is in its
  tools' descriptions. It is set per turn by a middleware placed after Deep Agents' own, since
  the notes are per customer.
- Main-loop calls (spec §7.2.4): that middleware also tells the proxy the main loop's final
  system prompt and tool set for the turn, so the proxy's test (prompt hash plus exact tool set)
  matches the agent's own calls and nothing else. The sub-agent has its own prompt and no `task`
  tool, so its calls stay hidden calls.
- The main graph's `recursion_limit` = 2 x max_model_calls + 4, a backstop the proxy's cap
  reaches first (Deep Agents' own default is 9,999). The `task` sub-agent keeps that default (Deep
  Agents invokes it with its own config); the proxy's cap bounds it, since each step is a model
  call.
- Traces: `openinference-instrumentation-langchain`, as runtime/langgraph (the kit relabels the
  instrumentor's TOOL spans CHAIN, since the kit writes its own).

LangSmith's environment variables are removed by the entry point before LangChain is imported.
"""

from __future__ import annotations

import contextvars

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from kit.context import CURRENT
from kit.proxy import content_text
from kit.turn import CLIENT_TIMEOUT
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_openai import ChatOpenAI

LOGGERS = (
    "deepagents",
    "langchain",
    "langgraph",
    "langchain_core",
    "langchain_openai",
    "langsmith",
)
# The proxy refuses any other key: the turn token replaces this on every request.
PLACEHOLDER_KEY = "agentbreeder-turn-token"
# Deep Agents 0.7.32's built-in tools with the state backend (F0; spec §8.3). The manifest's
# `reserved_tool_names` lists them, so no agent tool can take one of these names.
BUILTIN_TOOLS = frozenset({"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep"})
TASK_TOOL = "task"

# The running turn's system prompt, for the middleware below.
TURN_SYSTEM: contextvars.ContextVar[str] = contextvars.ContextVar("turn_system")


def as_tool(function) -> StructuredTool:
    """A kit function as a LangChain tool with its declared JSON Schema."""

    async def run(**arguments):
        return await function(**arguments)

    return StructuredTool.from_function(
        coroutine=run,
        name=function.name,
        description=function.description or function.name,
        args_schema=function.parameters,
    )


def reply_text(message) -> str:
    """The final message's visible text. A message that still asks for a tool, or whose tool
    call LangChain could not parse (`invalid_tool_calls`, so no tool ran), is not an answer
    (spec §7.2.4): it gives "", and the kit's fallback reply."""
    if not isinstance(message, AIMessage):
        return ""
    if message.tool_calls or message.invalid_tool_calls:
        return ""
    return str(message.text or "")


def tool_name(tool) -> str | None:
    """A tool's name as it goes on the wire (a LangChain tool, or an OpenAI-shaped dict)."""
    if isinstance(tool, BaseTool):
        return tool.name
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return function["name"]
        if isinstance(tool.get("name"), str):
            return tool["name"]
    return None


def turn_system_message(system: str, framework: SystemMessage | None) -> SystemMessage:
    """The turn's prompt first, then Deep Agents' own sections (what `create_deep_agent`
    does with a fixed `system_prompt`), as one text."""
    sections = content_text(framework.content) if framework is not None else ""
    return SystemMessage(f"{system}\n\n{sections}" if sections else system)


class TurnPrompt(AgentMiddleware):
    """The main agent's middleware after Deep Agents' own: the turn's system prompt, and the
    main loop's shape for the proxy's main-loop test. Sub-agents do not have it."""

    name = "AgentBreederTurnPrompt"

    def _request(self, request):
        system = turn_system_message(TURN_SYSTEM.get(), request.system_message)
        request = request.override(system_message=system)
        context = CURRENT.get(None)
        if context is not None and context.proxy_state is not None:
            state = context.proxy_state
            state.system = content_text(system.content)
            state.toolset = frozenset(
                name for name in (tool_name(t) for t in request.tools) if name is not None
            )
        return request

    def wrap_model_call(self, request, handler):
        return handler(self._request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._request(request))


class DeepAgentsAdapter:
    framework = "deepagents"

    def __init__(self):
        self.kit = None
        self.model = None
        self.graph = None
        self.clients: list = []

    def instrument(self, provider) -> None:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().instrument(tracer_provider=provider)

    def build(self, kit) -> None:
        self.kit = kit
        self.clients = [kit.http_client(), kit.sync_http_client()]
        self.model = ChatOpenAI(
            model=kit.alias,
            base_url=kit.base_url,
            api_key=PLACEHOLDER_KEY,
            http_async_client=self.clients[0],
            http_client=self.clients[1],
            max_retries=0,
            timeout=CLIENT_TIMEOUT,
        )
        self.graph = self.graph_for(kit.functions)

    def graph_for(self, functions):
        return create_deep_agent(
            model=self.model,
            tools=[as_tool(f) for f in functions],
            backend=StateBackend(),
            middleware=[TurnPrompt()],
        )

    def messages(self, turn) -> list:
        history = [
            HumanMessage(item["content"]) if item["role"] == "user" else AIMessage(item["content"])
            for item in turn.history
        ]
        return [*history, HumanMessage(turn.message)]

    async def run_turn(self, turn) -> str:
        graph = (
            self.graph if turn.functions == self.kit.functions else self.graph_for(turn.functions)
        )
        token = TURN_SYSTEM.set(turn.system)
        try:
            result = await graph.ainvoke(
                {"messages": self.messages(turn)},
                config={"recursion_limit": 2 * self.kit.max_steps + 4},
            )
        finally:
            TURN_SYSTEM.reset(token)
        messages = result.get("messages") or []
        return reply_text(messages[-1]) if messages else ""

    def close(self) -> None:
        # The async client's connections close with the process; the sync one is closed here.
        for client in self.clients[1:]:
            client.close()
