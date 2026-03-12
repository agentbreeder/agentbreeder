"""A research assistant agent built with the OpenAI Agents SDK.

Demonstrates:
- Creating an Agent with a system prompt
- Defining tools using @function_tool decorators
- Proper OpenAI Agents SDK patterns (Agent, Runner, function_tool)

Requires OPENAI_API_KEY environment variable to be set.
"""

from __future__ import annotations

import math

from agents import Agent, Runner, function_tool


@function_tool
def web_search(query: str) -> str:
    """Search the web for information on a given query.

    Args:
        query: The search query string.

    Returns:
        Search results as a formatted string.
    """
    # Placeholder — in production, integrate with a real search API
    return (
        f"Search results for '{query}':\n"
        f"1. Wikipedia article on {query}\n"
        f"2. Recent news about {query}\n"
        f"3. Academic papers related to {query}"
    )


@function_tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression safely.

    Args:
        expression: A mathematical expression to evaluate (e.g., '2 + 3 * 4').

    Returns:
        The result of the expression as a string.
    """
    # Allow only safe math operations
    allowed_names = {
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
        result = eval(expression, {"__builtins__": {}}, allowed_names)  # noqa: S307
        return str(result)
    except Exception as e:
        return f"Error evaluating '{expression}': {e}"


# Build the agent — exported as 'agent' for the Agent Garden server wrapper
agent = Agent(
    name="Research Assistant",
    instructions=(
        "You are a helpful research assistant. You can search the web for information "
        "and perform calculations. When asked a question, use your tools to find accurate "
        "information and provide well-structured answers. Always cite your sources when "
        "using search results."
    ),
    tools=[web_search, calculator],
)


async def main() -> None:
    """Run the agent interactively for local testing."""
    result = await Runner.run(agent, "What is the square root of 144 plus 25?")
    print(f"Agent response: {result.final_output}")  # noqa: T201


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
