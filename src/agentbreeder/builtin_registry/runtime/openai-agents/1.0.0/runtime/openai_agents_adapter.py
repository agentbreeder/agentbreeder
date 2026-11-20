"""The OpenAI Agents SDK binding of the runtime kit (multi-framework spec §8.1).

- Model: `OpenAIChatCompletionsModel` (Chat Completions; the proxy serves no Responses API) on an
  `AsyncOpenAI` client at the kit's loopback proxy with the kit's turn-aware HTTP client and its
  own retries off (the proxy owns retries, cost, the step cap and coded errors). Its API key is a
  placeholder: the turn token replaces it on every request.
- Agent: one `Agent` built at start-up and cloned per turn with that turn's instructions (the
  system prompt carries the customer's notes); no SDK session (history comes from the kit, as
  input items) and `max_turns` = max_model_calls + 2, a backstop the proxy's cap reaches first.
- Tools: one `FunctionTool` per kit function, with the function's declared JSON Schema and
  `strict_json_schema=False` (strict mode would make every parameter required); every call goes
  through the kit's wrapper (audit row, TOOL span, the reply's `tools`). The agent has no other
  tool: none of the SDK's built-in tools (shell, computer, code interpreter, hosted MCP and the
  rest), no handoffs, no MCP servers, and `RunConfig.sandbox` is never set.
- Traces: `openinference-instrumentation-openai-agents` with `exclusive_processor=True`, which
  replaces the SDK's own exporter to OpenAI. Tracing is never switched off in the SDK
  (`set_tracing_disabled(True)` also stops the OpenInference spans, F0). The kit relabels the
  instrumentor's TOOL spans CHAIN, since the kit writes its own.

The entry point removes every OPENAI_* environment variable before the SDK is imported.
"""

from __future__ import annotations

import json

from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
)
from kit.turn import CLIENT_TIMEOUT
from openai import AsyncOpenAI

LOGGERS = ("agents", "openai.agents", "mcp")
# The proxy refuses any other key: the turn token replaces this on every request.
PLACEHOLDER_KEY = "agentbreeder-turn-token"


def arguments(text: str) -> dict:
    """The model's tool arguments. Arguments that are not a JSON object reach the kit as none, so
    its schema check answers the model (a missing required field) and records an `ok: false`
    row, as for any other bad call."""
    try:
        parsed = json.loads(text or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def as_tool(function) -> FunctionTool:
    """A kit function as an OpenAI Agents tool with its declared JSON Schema."""

    async def invoke(context, text: str) -> str:
        return await function(**arguments(text))

    return FunctionTool(
        name=function.name,
        description=function.description or function.name,
        params_json_schema=function.parameters,
        on_invoke_tool=invoke,
        strict_json_schema=False,
    )


def messages(turn) -> list[dict]:
    """The conversation as input items: earlier user and assistant text, then the message."""
    history = [{"role": item["role"], "content": item["content"]} for item in turn.history]
    return [*history, {"role": "user", "content": turn.message}]


def reply_text(output) -> str:
    """The run's final output. With no `output_type` the SDK ends a run only on a model answer
    with no tool call, so this is that answer's text."""
    return output if isinstance(output, str) else ""


class OpenAIAgentsAdapter:
    framework = "openai-agents"

    def __init__(self):
        self.kit = None
        self.client = None
        self.agent = None
        self.tools: list = []

    def instrument(self, provider) -> None:
        from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

        # Explicit, though it is the instrumentor's default: it is what keeps the SDK's own
        # trace upload off (spec §8.1).
        OpenAIAgentsInstrumentor().instrument(tracer_provider=provider, exclusive_processor=True)

    def build(self, kit) -> None:
        self.kit = kit
        self.client = AsyncOpenAI(
            base_url=kit.base_url,
            api_key=PLACEHOLDER_KEY,
            http_client=kit.http_client(),
            max_retries=0,
            timeout=CLIENT_TIMEOUT,
        )
        self.tools = [as_tool(f) for f in kit.functions]
        self.agent = Agent(
            name=kit.app,
            instructions=kit.system,
            tools=self.tools,
            model=OpenAIChatCompletionsModel(model=kit.alias, openai_client=self.client),
            # As spec §8.1. It changes only streamed requests (the SDK makes none here); the
            # usage the proxy tallies is the gateway's, from every answer.
            model_settings=ModelSettings(include_usage=True),
        )

    async def run_turn(self, turn) -> str:
        tools = (
            self.tools
            if turn.functions == self.kit.functions
            else [as_tool(f) for f in turn.functions]
        )
        agent = self.agent.clone(instructions=turn.system, tools=tools)
        result = await Runner.run(
            agent,
            input=messages(turn),
            max_turns=self.kit.max_steps + 2,
            # A call to a tool the agent lacks is answered to the model, as LangGraph's tool
            # node does, instead of failing the turn; nothing runs.
            run_config=RunConfig(
                workflow_name=self.kit.app, tool_not_found_behavior="return_error_to_model"
            ),
        )
        return reply_text(result.final_output)

    def close(self) -> None:
        # The async client's connections close with the process.
        pass
