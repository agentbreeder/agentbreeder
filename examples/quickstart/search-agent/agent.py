"""Search Agent — LangGraph implementation.

Performs web searches via DuckDuckGo (no API key required).
Deployed by: agentbreeder deploy --target local
Chat with:   agentbreeder chat search-agent
"""

from __future__ import annotations

import json
import os
from typing import Annotated, TypedDict

import httpx
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

# ── Tool: web_search ────────────────────────────────────────────────────────


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for current information using DuckDuckGo.

    Args:
        query: The search query.
        max_results: Maximum number of results to return (default 5).

    Returns:
        JSON string with search results including title, URL, and snippet.
    """
    try:
        from duckduckgo_search import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    }
                )

        return json.dumps({"query": query, "total": len(results), "results": results}, indent=2)

    except ImportError:
        return json.dumps(
            {"error": "duckduckgo-search package not installed. Run: pip install duckduckgo-search"}
        )
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ── LangGraph state + graph ─────────────────────────────────────────────────


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


tools = [web_search]
tool_node = ToolNode(tools)


def _build_llm():
    """Build the LLM, preferring Ollama → Anthropic → OpenAI."""
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model_name = os.environ.get("AGENT_MODEL", "llama3.2")

    # Try Ollama first (free, local)
    try:
        resp = httpx.get(f"{ollama_url}/", timeout=3.0)
        if resp.status_code == 200:
            from langchain_ollama import ChatOllama

            return ChatOllama(model=model_name, base_url=ollama_url).bind_tools(tools)
    except Exception:
        pass

    # Try Anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model="claude-haiku-4-20250414").bind_tools(tools)

    # Try OpenAI
    if os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model="gpt-4o-mini").bind_tools(tools)

    # Fall back to LiteLLM gateway
    litellm_url = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=f"ollama/{model_name}",
        base_url=f"{litellm_url}/v1",
        api_key=os.environ.get("LITELLM_MASTER_KEY", "sk-agentbreeder-quickstart"),
    ).bind_tools(tools)


SYSTEM_PROMPT = """You are a research assistant with access to web search.

Use the web_search tool to find current information before answering questions.
Always:
1. Search for relevant information first
2. Base your answer on the search results
3. Cite sources (use the 'url' field from results)
4. If search returns no results, say so clearly

You are especially good at:
- Finding recent news and events
- Researching topics and summarizing findings
- Answering factual questions with up-to-date information
"""


def call_model(state: AgentState) -> AgentState:
    llm = _build_llm()
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response]}


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


# Build and export the graph
builder = StateGraph(AgentState)
builder.add_node("agent", call_model)
builder.add_node("tools", tool_node)
builder.set_entry_point("agent")
builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
builder.add_edge("tools", "agent")

graph = builder.compile()
