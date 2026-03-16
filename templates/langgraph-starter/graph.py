"""LangGraph starter agent with tool-calling node.

Demonstrates:
- Typed state with TypedDict
- Tool node for function calling
- Conditional routing based on tool calls
- Agent Garden server wrapper export pattern

Export the compiled graph as `graph` — Agent Garden's server wrapper looks for this.
"""

from __future__ import annotations

import math
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """State shared across all nodes in the graph."""

    messages: Annotated[list[BaseMessage], add_messages]


# --- Tool definitions ---

TOOLS = {
    "search": {
        "description": "Search for information on a topic",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
            },
            "required": ["query"],
        },
    },
    "calculator": {
        "description": "Evaluate a mathematical expression",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression to evaluate"},
            },
            "required": ["expression"],
        },
    },
}


def execute_tool(name: str, args: dict) -> str:
    """Execute a tool by name with the given arguments."""
    if name == "search":
        query = args.get("query", "")
        # Placeholder — integrate with a real search API in production
        return f"Search results for '{query}': [Result 1] [Result 2] [Result 3]"

    if name == "calculator":
        expression = args.get("expression", "")
        allowed = {
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "pow": pow,
            "sqrt": math.sqrt,
            "pi": math.pi,
            "e": math.e,
        }
        try:
            result = eval(expression, {"__builtins__": {}}, allowed)  # noqa: S307
            return str(result)
        except Exception as e:
            return f"Error: {e}"

    return f"Unknown tool: {name}"


# --- Graph nodes ---


def agent_node(state: AgentState) -> AgentState:
    """Main agent node — processes messages and decides next action.

    In production, this calls the LLM with tool definitions.
    This starter uses a simple pattern-matching approach for demonstration.
    Replace with your LLM integration.
    """
    messages = state["messages"]
    last_message = messages[-1] if messages else None

    if last_message is None:
        return {"messages": [AIMessage(content="Hello! How can I help you today?")]}

    content = last_message.content if hasattr(last_message, "content") else str(last_message)

    # Simple demonstration logic — replace with LLM tool-calling in production
    if "calculate" in content.lower() or "math" in content.lower():
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "calc_1",
                            "name": "calculator",
                            "args": {"expression": content.split(":")[-1].strip()},
                        }
                    ],
                )
            ]
        }

    if "search" in content.lower() or "find" in content.lower():
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "search_1", "name": "search", "args": {"query": content}}],
                )
            ]
        }

    return {
        "messages": [
            AIMessage(
                content=(
                    f"I received your message: '{content}'. I can help you search for"
                    " information or perform calculations. Try asking me to 'search for X'"
                    " or 'calculate: 2+2'."
                ),
            )
        ]
    }


def tool_node(state: AgentState) -> AgentState:
    """Execute tool calls from the agent and return results."""
    messages = state["messages"]
    last_message = messages[-1]

    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        return {"messages": []}

    tool_messages = []
    for tool_call in last_message.tool_calls:
        result = execute_tool(tool_call["name"], tool_call["args"])
        tool_messages.append(
            ToolMessage(
                content=result,
                tool_call_id=tool_call["id"],
            )
        )

    return {"messages": tool_messages}


def should_use_tools(state: AgentState) -> str:
    """Route to tool_node if the last message has tool calls, otherwise end."""
    messages = state["messages"]
    if not messages:
        return "end"
    last_message = messages[-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"
    return "end"


# --- Build the graph ---

builder = StateGraph(AgentState)
builder.add_node("agent", agent_node)
builder.add_node("tools", tool_node)

builder.set_entry_point("agent")
builder.add_conditional_edges("agent", should_use_tools, {"tools": "tools", "end": "__end__"})
builder.add_edge("tools", "agent")

# Export as 'graph' — the Agent Garden server wrapper looks for this
graph = builder.compile()
