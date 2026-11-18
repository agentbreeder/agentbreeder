"""Sub-agents as tools (runtime/adk@1.5.0, #1058).

The parent agent calls a sub-agent like a function, `ask_<name>(request)`, and gets its final
text back; the parent always writes the reply. This is our own tool, not ADK's `AgentTool`,
which would copy the parent's whole session state into the child and the child's changes back,
count the child's model calls against the parent's ADK limit, close the child's MCP sessions
after every call and let two child calls of one turn run at once.

What a sub-agent sees is its request and nothing else: a fresh in-memory session per call (no
history from the parent or from an earlier call), whose state holds only the key naming the
turn (turns.py). Its tools act as the turn's customer, set server-side. Each agent is offered
only its own tools; that narrows what a model can attempt and is not a security boundary, since
all tool code runs in this one process.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from dataclasses import dataclass, field

from google.adk.agents import LlmAgent
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from turns import TURN_KEY, TurnUnknown, caused_by

NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
MAX_SUBAGENTS = 5
MAX_REQUEST = 4000  # characters the parent may send a sub-agent in one call
MAX_CALLS_PER_TURN = 3
# The sub-agent's tag on its model requests (and "-" for the parent), so the gateway's spend
# rows can be grouped by sub-agent. The validated name only, never customer text.
TAG = "agentbreeder-subagent:"
PARENT_TAG = TAG + "-"

ALREADY_CONSULTED = (
    "This specialist was already consulted as often as one turn allows. Answer with what you have."
)
COULD_NOT_FINISH = "The specialist could not finish. Answer with what you have."
COULD_NOT_RUN = "The specialist could not run. Answer without it."


def tool_name(name: str) -> str:
    """The parent's tool for a sub-agent, and the sub-agent's ADK agent name."""
    return f"ask_{name}"


def request_tags(name: str | None) -> dict:
    """LiteLlm arguments that tag an agent's model requests for the gateway's spend log: sent
    in the OpenAI request body as `metadata.tags` (the S0 probe, #1058)."""
    return {"extra_body": {"metadata": {"tags": [TAG + name if name else PARENT_TAG]}}}


@dataclass
class Child:
    """One sub-agent, built once at start from agent.config.json."""

    name: str
    alias: str
    max_model_calls: int
    max_calls_per_turn: int
    description: str
    agent: LlmAgent | None = None
    toolsets: list = field(default_factory=list)

    @property
    def tool(self) -> str:
        return tool_name(self.name)


def new_usage(child: Child) -> dict:
    """What one turn records about one sub-agent (the reply's `subagents[]` entry, plus
    `request_sha256` of the request being answered, which only audit rows carry)."""
    return {
        "name": child.name,
        "calls": 0,
        "model_calls": 0,
        "tools": [],
        "usd": 0.0,
        "cost_calls": 0,
        "cost_unknown": 0,
        "served_by": [],
        "request_sha256": None,
    }


def reply_entry(usage: dict) -> dict:
    """A turn's `subagents[]` entry. `cost_usd` is the cost of the calls that had one (None
    when the gateway priced none); `cost_unknown` counts the calls that had none."""
    priced = usage["cost_calls"] - usage["cost_unknown"]
    return {
        "name": usage["name"],
        "calls": usage["calls"],
        "model_calls": usage["model_calls"],
        "tools": list(usage["tools"]),
        "cost_usd": round(usage["usd"], 10) if priced > 0 else None,
        "cost_unknown": usage["cost_unknown"],
        "served_by": list(usage["served_by"]),
    }


class SubAgentTool(BaseTool):
    """`ask_<name>(request)`: runs the sub-agent on the request and returns its final text."""

    def __init__(self, child: Child, runtime):
        super().__init__(name=child.tool, description=child.description)
        self.child = child
        self.runtime = runtime

    def _get_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "description": (
                            "What you need from this specialist, with every detail it needs: "
                            f"it sees only this text. At most {MAX_REQUEST} characters."
                        ),
                    }
                },
                "required": ["request"],
            },
        )

    async def run_async(self, *, args, tool_context: ToolContext):
        turn = self.runtime.turn_for(tool_context)
        unknown = sorted(set(args) - {"request"})
        if unknown:
            raise ValueError(f"unexpected arguments: {', '.join(unknown)}")
        request = args.get("request")
        if not isinstance(request, str) or not request.strip():
            raise ValueError("missing arguments: request")
        if len(request) > MAX_REQUEST:
            raise ValueError(f"request is longer than {MAX_REQUEST} characters")
        # Counted before the first await, so two calls in one model response cannot both
        # pass the check.
        usage = turn["subagents"].setdefault(self.child.name, new_usage(self.child))
        if usage["calls"] >= self.child.max_calls_per_turn:
            raise ValueError(ALREADY_CONSULTED)
        usage["calls"] += 1
        # One sub-agent call at a time per turn, so every model call of a turn is sequential
        # and the per-call cost and route slots stay correct.
        async with turn["subagent_lock"]:
            usage["request_sha256"] = hashlib.sha256(request.encode()).hexdigest()
            try:
                return await self._run(turn, request)
            except Exception as failure:
                if not caused_by(failure, TurnUnknown):
                    raise
                # A callback of the child found no turn: nothing it did was counted or
                # audited, so its work is not used. The parent's model gets a tool error.
                await self.runtime.audit(
                    turn, "system", "subagent_turn_unknown", {"subagent": self.child.name}
                )
                raise ValueError(COULD_NOT_RUN) from None

    async def _run(self, turn: dict, request: str) -> str:
        # A session of its own per call: no history, and no state but the turn's name.
        sessions = InMemorySessionService()
        session = await sessions.create_session(
            app_name=self.runtime.app,
            user_id=turn["customer"],  # the server-side customer, never a model's value
            state={TURN_KEY: turn["id"]},
        )
        runner = Runner(
            app_name=self.runtime.app,
            agent=self.child.agent,
            session_service=sessions,
            plugins=[self.runtime.plugin],
        )
        # The plugin and the child's MCP sessions belong to the runtime: never closed here.
        runner.plugin_manager.set_skip_closing_plugins(True)
        reply = ""
        events = runner.run_async(
            user_id=turn["customer"],
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=request)]),
            # A backstop; the plugin enforces the real caps and forces a final answer.
            run_config=RunConfig(max_llm_calls=self.runtime.turn_cap + 1),
        )
        try:
            async with contextlib.aclosing(events):
                async for event in events:
                    if event.author != "user" and event.content and event.is_final_response():
                        text = "".join(
                            p.text or "" for p in (event.content.parts or []) if not p.thought
                        ).strip()
                        if text:
                            reply = text
        except LlmCallsLimitExceededError:
            pass
        return reply or COULD_NOT_FINISH
