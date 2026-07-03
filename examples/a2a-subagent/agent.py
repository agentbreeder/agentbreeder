"""LangGraph "coordinator" agent that delegates to subagents over A2A.

Loaded by the AgentBreeder server wrapper as ``graph``. The wrapper exposes
the JSON-RPC A2A endpoint at ``/a2a`` automatically when subagents are
declared in ``agent.yaml`` — this file only needs to wire the local graph
logic.

The graph is intentionally a single passthrough node so the example focuses
on the A2A *registration* path, not the routing logic. In a real agent the
node would call ``agenthub.a2a.call("summarizer", ...)`` etc.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import StateGraph


class AgentState(TypedDict):
    message: str
    response: str


def coordinate(state: AgentState) -> AgentState:
    message = state.get("message", "")
    return {
        "message": message,
        "response": (
            "Coordinator received: "
            f"{message!r}. In a real agent this node would delegate to the "
            "summarizer / verifier subagents declared in agent.yaml via the "
            "A2A protocol (see examples/orchestration for a fully-wired demo)."
        ),
    }


builder = StateGraph(AgentState)
builder.add_node("coordinate", coordinate)
builder.set_entry_point("coordinate")
builder.set_finish_point("coordinate")

graph = builder.compile()
