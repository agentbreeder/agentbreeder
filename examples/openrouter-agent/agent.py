"""Minimal LangGraph agent routing through OpenRouter via LiteLLM.

OpenRouter exposes 300+ models behind a single OpenAI-compatible API. The
``openrouter/...`` prefix in ``agent.yaml`` tells the LangGraph runtime to
add the LiteLLM SDK to the image; this node uses ``ChatOpenAI`` with a
custom base URL to hit OpenRouter directly without the proxy.

Set ``OPENROUTER_API_KEY`` in your environment / deploy secrets.
"""

from __future__ import annotations

import os
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph


class AgentState(TypedDict):
    message: str
    response: str


def _build_llm() -> ChatOpenAI:
    raw = os.environ.get("AGENT_MODEL", "openrouter/deepseek/deepseek-r1")
    # ChatOpenAI expects "<vendor>/<model>" — strip only the leading "openrouter/" hop.
    model = raw.split("/", 1)[1] if raw.startswith("openrouter/") else raw
    return ChatOpenAI(
        model=model,
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        base_url="https://openrouter.ai/api/v1",
        temperature=float(os.environ.get("AGENT_TEMPERATURE", "0.7")),
    )


def respond(state: AgentState) -> AgentState:
    llm = _build_llm()
    reply = llm.invoke([HumanMessage(content=state.get("message", ""))])
    return {"message": state.get("message", ""), "response": str(reply.content)}


builder = StateGraph(AgentState)
builder.add_node("respond", respond)
builder.set_entry_point("respond")
builder.set_finish_point("respond")

graph = builder.compile()
