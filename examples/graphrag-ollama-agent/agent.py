"""GraphRAG demo agent — answers questions against a Neo4j knowledge base
using a locally-hosted Ollama model.

The companion ``ingest.py`` populates the knowledge base; this file wires a
single-node LangGraph that retrieves matching graph context and runs an
Ollama LLM over it. Designed to run fully offline once the model is pulled.

Export ``graph`` — the AgentBreeder server wrapper looks for this symbol.
"""

from __future__ import annotations

import os
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph


class AgentState(TypedDict):
    message: str
    response: str


def _build_llm() -> ChatOllama:
    raw = os.environ.get("AGENT_MODEL", "ollama/qwen2.5:7b")
    model = raw.split("/", 1)[1] if raw.startswith("ollama/") else raw
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    return ChatOllama(model=model, base_url=base_url)


def answer(state: AgentState) -> AgentState:
    """Run a single LLM call. In a fully-wired GraphRAG pipeline this node
    would first call ``engine.rag.graph_query`` to fetch sub-graph context and
    inject it into the system prompt — kept minimal here so the example
    container boots without a running Neo4j instance.
    """
    llm = _build_llm()
    reply = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You answer questions about AgentBreeder using GraphRAG "
                    "context retrieved from the kb/agentbreeder-docs knowledge "
                    "base. If you don't know an answer, say so plainly."
                )
            ),
            HumanMessage(content=state.get("message", "")),
        ]
    )
    return {"message": state.get("message", ""), "response": str(reply.content)}


builder = StateGraph(AgentState)
builder.add_node("answer", answer)
builder.set_entry_point("answer")
builder.set_finish_point("answer")

graph = builder.compile()
