"""Claude SDK starter agent with tool use.

Demonstrates:
- Anthropic client setup
- Tool definitions with JSON schema
- Tool use loop (send message -> execute tools -> continue)
- Agent Garden export pattern

Export the agent config as `agent_config` and the handler as `handle_message`
— Agent Garden's server wrapper looks for these.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

# --- Tool definitions ---

TOOLS = [
    {
        "name": "get_time",
        "description": "Get the current time in a specified timezone",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name (e.g., 'America/New_York',"
                    " 'Europe/London')",
                },
            },
            "required": ["timezone"],
        },
    },
    {
        "name": "lookup_info",
        "description": "Look up information from the knowledge base",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
            },
            "required": ["query"],
        },
    },
]


def execute_tool(name: str, input_data: dict) -> str:
    """Execute a tool by name with the given input."""
    if name == "get_time":
        tz_name = input_data.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
            now = datetime.now(tz)
            return f"Current time in {tz_name}: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        except Exception:
            return f"Unknown timezone: {tz_name}. Use IANA format (e.g., America/New_York)"

    if name == "lookup_info":
        query = input_data.get("query", "")
        # Placeholder — integrate with your RAG pipeline in production
        return (
            f"Knowledge base results for '{query}':\n"
            f"1. Relevant information about {query}\n"
            f"2. Additional context for {query}"
        )

    return f"Unknown tool: {name}"


# --- Agent configuration ---

agent_config = {
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 2048,
    "system": (
        "You are a helpful assistant built with the Claude SDK. Use your tools when "
        "appropriate. Be thoughtful, accurate, and concise in your responses."
    ),
    "tools": TOOLS,
}


async def handle_message(message: str, history: list[dict] | None = None) -> str:
    """Handle a user message with tool use loop.

    Args:
        message: The user's message.
        history: Optional conversation history.

    Returns:
        The agent's final response text.
    """
    client = anthropic.AsyncAnthropic()

    messages = list(history or [])
    messages.append({"role": "user", "content": message})

    # Tool use loop — keep calling the API until no more tool calls
    while True:
        response = await client.messages.create(
            model=agent_config["model"],
            max_tokens=agent_config["max_tokens"],
            system=agent_config["system"],
            tools=agent_config["tools"],
            messages=messages,
        )

        # Check if the model wants to use tools
        if response.stop_reason == "tool_use":
            # Add assistant response to history
            messages.append({"role": "assistant", "content": response.content})

            # Execute each tool call
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})
        else:
            # No more tool calls — extract final text response
            text_parts = [block.text for block in response.content if block.type == "text"]
            return "\n".join(text_parts)


async def main() -> None:
    """Run the agent interactively for local testing."""
    response = await handle_message("What time is it in Tokyo?")
    print(f"Agent response: {response}")  # noqa: T201


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
