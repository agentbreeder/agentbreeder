"""Example MCP server for AgentBreeder.

A minimal MCP server (stdio transport) that exposes three tools:
- calculate: evaluate a math expression safely
- get_weather: return mock weather data for a city
- search_docs: return mock search results for a query

This demonstrates the standard pattern for building MCP tool servers
that integrate with AgentBreeder's tool registry.

Usage:
    python server.py

The server communicates over stdin/stdout using the MCP protocol.
Agents connect to it by referencing it in their agent.yaml tools section.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Create the MCP server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="example-tools",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Tool 1: calculate — safe math expression evaluator
# ---------------------------------------------------------------------------

# Allowed operators for safe evaluation (no exec/eval)
_SAFE_OPERATORS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval_node(node: ast.AST) -> float:
    """Recursively evaluate an AST node using only safe math operations."""
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        op_func = _SAFE_OPERATORS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        return op_func(left, right)
    if isinstance(node, ast.UnaryOp):
        op_func = _SAFE_OPERATORS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op_func(_safe_eval_node(node.operand))
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


def _safe_eval(expression: str) -> float:
    """Safely evaluate a math expression without using eval().

    Only supports: numbers, +, -, *, /, //, %, ** and parentheses.
    Raises ValueError for anything else (function calls, variables, etc.).
    """
    tree = ast.parse(expression, mode="eval")
    return _safe_eval_node(tree)


@mcp.tool()
def calculate(expression: str) -> str:
    """Evaluate a mathematical expression and return the result.

    Supports basic arithmetic: +, -, *, /, //, %, ** and parentheses.
    Does NOT support variables, function calls, or arbitrary code.

    Args:
        expression: A math expression like "2 + 3 * 4" or "(10 - 2) ** 3".

    Returns:
        The numeric result as a string.
    """
    try:
        result = _safe_eval(expression)
        # Format cleanly: drop .0 for integer results
        if result == int(result):
            return str(int(result))
        return str(result)
    except (ValueError, SyntaxError, ZeroDivisionError) as exc:
        return f"Error: {exc}"


# ---------------------------------------------------------------------------
# Tool 2: get_weather — mock weather data
# ---------------------------------------------------------------------------

# Mock weather database
_MOCK_WEATHER: dict[str, dict[str, Any]] = {
    "san francisco": {
        "city": "San Francisco",
        "temperature_f": 62,
        "condition": "Foggy",
        "humidity": 78,
        "wind_mph": 12,
    },
    "new york": {
        "city": "New York",
        "temperature_f": 45,
        "condition": "Cloudy",
        "humidity": 65,
        "wind_mph": 8,
    },
    "london": {
        "city": "London",
        "temperature_f": 50,
        "condition": "Rainy",
        "humidity": 85,
        "wind_mph": 15,
    },
    "tokyo": {
        "city": "Tokyo",
        "temperature_f": 72,
        "condition": "Sunny",
        "humidity": 55,
        "wind_mph": 5,
    },
}


@mcp.tool()
def get_weather(city: str) -> str:
    """Get current weather data for a city (mock data for demonstration).

    Args:
        city: Name of the city to look up (e.g. "San Francisco", "Tokyo").

    Returns:
        Weather information as formatted text, or an error if city not found.
    """
    weather = _MOCK_WEATHER.get(city.lower())
    if weather is None:
        available = ", ".join(w["city"] for w in _MOCK_WEATHER.values())
        return f"City '{city}' not found. Available cities: {available}"
    return (
        f"Weather for {weather['city']}:\n"
        f"  Temperature: {weather['temperature_f']}°F\n"
        f"  Condition:   {weather['condition']}\n"
        f"  Humidity:    {weather['humidity']}%\n"
        f"  Wind:        {weather['wind_mph']} mph"
    )


# ---------------------------------------------------------------------------
# Tool 3: search_docs — mock document search
# ---------------------------------------------------------------------------

# Mock document corpus
_MOCK_DOCS = [
    {
        "title": "Getting Started with AgentBreeder",
        "snippet": "Install AgentBreeder with pip install agentbreeder-sdk. "
        "Create an agent.yaml file and run agentbreeder deploy.",
        "url": "https://docs.agentbreeder.dev/getting-started",
    },
    {
        "title": "Writing Custom MCP Servers",
        "snippet": "MCP servers expose tools over stdio or HTTP. "
        "Use the mcp Python package to build a server in minutes.",
        "url": "https://docs.agentbreeder.dev/mcp-servers",
    },
    {
        "title": "Deploying to AWS ECS",
        "snippet": "Set deploy.cloud to 'aws' and deploy.runtime to 'ecs-fargate' "
        "in your agent.yaml. AgentBreeder handles the rest.",
        "url": "https://docs.agentbreeder.dev/deploy/aws-ecs",
    },
    {
        "title": "RBAC and Governance",
        "snippet": "Every deployment is governed by role-based access control. "
        "Teams, cost attribution, and audit trails are automatic.",
        "url": "https://docs.agentbreeder.dev/governance",
    },
    {
        "title": "Multi-Agent Orchestration",
        "snippet": "Define orchestration.yaml to wire multiple agents together. "
        "Supports sequential, parallel, and router patterns.",
        "url": "https://docs.agentbreeder.dev/orchestration",
    },
]


@mcp.tool()
def search_docs(query: str) -> str:
    """Search the AgentBreeder documentation (mock results for demonstration).

    Args:
        query: Search query string (e.g. "deploy aws", "mcp server").

    Returns:
        Matching documents as formatted text, up to 3 results.
    """
    query_lower = query.lower()
    # Simple keyword matching against title and snippet
    scored: list[tuple[int, dict[str, str]]] = []
    for doc in _MOCK_DOCS:
        score = 0
        for word in query_lower.split():
            if word in doc["title"].lower():
                score += 2
            if word in doc["snippet"].lower():
                score += 1
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:3]

    if not top:
        return f"No results found for '{query}'."

    lines = [f"Found {len(top)} result(s) for '{query}':\n"]
    for i, (_score, doc) in enumerate(top, 1):
        lines.append(f"{i}. {doc['title']}")
        lines.append(f"   {doc['snippet']}")
        lines.append(f"   {doc['url']}\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point — run the MCP server over stdio
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
