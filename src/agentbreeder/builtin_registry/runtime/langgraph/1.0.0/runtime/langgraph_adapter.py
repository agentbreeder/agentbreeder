"""The LangGraph binding of the runtime kit (multi-framework spec §8.2).

- Model: `ChatOpenAI` at the kit's loopback proxy with the kit's turn-aware HTTP clients, its own
  retries off (the proxy owns retries, cost, the step cap and coded errors). Its API key is a
  placeholder: the turn token replaces it on every request.
- Agent: `langchain.agents.create_agent(model, tools=...)`, built once at start-up, with no
  checkpointer (history comes from the kit, as messages) and `recursion_limit` =
  2 x max_model_calls + 4, a backstop the proxy's cap reaches first.
- System prompt: a SystemMessage per turn, since it carries the customer's notes.
- Tools: one `StructuredTool` per kit function, with the function's declared JSON Schema; every
  call goes through the kit's wrapper (audit row, TOOL span, the reply's `tools`). The agent has
  no other tool.
- Traces: `openinference-instrumentation-langchain` (LLM, TOOL and CHAIN spans; the kit
  relabels its TOOL spans CHAIN, since the kit writes its own).

LangSmith's environment variables are removed by the entry point before LangChain is imported.
"""

from __future__ import annotations

from kit.turn import CLIENT_TIMEOUT
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

LOGGERS = ("langchain", "langgraph", "langchain_core", "langchain_openai", "langsmith")
# The proxy refuses any other key: the turn token replaces this on every request.
PLACEHOLDER_KEY = "agentbreeder-turn-token"


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
    call LangChain could not parse (`invalid_tool_calls`, so no tool ran), is not an answer:
    "let me look that up" would read as a successful turn (spec §7.2.4). It gives "", and the
    kit's fallback reply."""
    if not isinstance(message, AIMessage):
        return ""
    if message.tool_calls or message.invalid_tool_calls:
        return ""
    return str(message.text or "")


class LangGraphAdapter:
    framework = "langgraph"

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
        return create_agent(self.model, tools=[as_tool(f) for f in functions])

    def messages(self, turn) -> list:
        history = [
            HumanMessage(item["content"]) if item["role"] == "user" else AIMessage(item["content"])
            for item in turn.history
        ]
        return [SystemMessage(turn.system), *history, HumanMessage(turn.message)]

    async def run_turn(self, turn) -> str:
        graph = (
            self.graph if turn.functions == self.kit.functions else self.graph_for(turn.functions)
        )
        result = await graph.ainvoke(
            {"messages": self.messages(turn)},
            config={"recursion_limit": 2 * self.kit.max_steps + 4},
        )
        messages = result.get("messages") or []
        return reply_text(messages[-1]) if messages else ""

    def close(self) -> None:
        # The async client's connections close with the process; the sync one is closed here.
        for client in self.clients[1:]:
            client.close()
