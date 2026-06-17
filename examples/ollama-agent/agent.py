"""Minimal LangGraph agent powered by a local Ollama model.

The graph contains a single LLM node — perfect for verifying that Ollama
is reachable from the AgentBreeder container at
``http://agentbreeder-ollama:11434`` (set automatically by the LangGraph
runtime for ``ollama/*`` models).

Export ``graph`` — the AgentBreeder server wrapper looks for this symbol.
"""

from __future__ import annotations

import os
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph


class AgentState(TypedDict):
    message: str
    response: str


def _build_llm() -> ChatOllama:
    # Model name carries the "ollama/" prefix in agent.yaml; strip it for the
    # ChatOllama client which expects the bare model id.
    raw = os.environ.get("AGENT_MODEL", "ollama/llama3.3")
    model = raw.split("/", 1)[1] if raw.startswith("ollama/") else raw
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    return ChatOllama(model=model, base_url=base_url)


def respond(state: AgentState) -> AgentState:
    """Run the user message through the LLM and store the reply."""
    llm = _build_llm()
    reply = llm.invoke([HumanMessage(content=state.get("message", ""))])
    return {"message": state.get("message", ""), "response": str(reply.content)}


builder = StateGraph(AgentState)
builder.add_node("respond", respond)
builder.set_entry_point("respond")
builder.set_finish_point("respond")

graph = builder.compile()
