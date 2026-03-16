"""Google ADK starter agent with function declarations.

Demonstrates:
- Google Generative AI client setup
- Function declarations for tool use
- Tool call handling loop
- Agent Garden export pattern

Export the agent config as `agent_config` and handler as `handle_message`
— Agent Garden's server wrapper looks for these.
"""

from __future__ import annotations

import google.generativeai as genai

# --- Tool definitions ---


def search_web(query: str) -> str:
    """Search the web for information.

    Args:
        query: The search query string.

    Returns:
        Search results as a formatted string.
    """
    # Placeholder — integrate with Google Custom Search or similar in production
    return (
        f"Search results for '{query}':\n"
        f"1. Key finding about {query}\n"
        f"2. Recent news about {query}\n"
        f"3. Expert analysis of {query}"
    )


def get_stock_price(ticker: str) -> str:
    """Get the current stock price for a ticker symbol.

    Args:
        ticker: Stock ticker symbol (e.g., 'GOOGL', 'AAPL').

    Returns:
        Stock price information.
    """
    # Placeholder — integrate with a financial data API in production
    return (
        f"Stock: {ticker.upper()}\n"
        f"Price: $175.23\n"
        f"Change: +2.15 (+1.24%)\n"
        f"Volume: 12.3M\n"
        f"Note: Data may be delayed up to 15 minutes."
    )


# Tool registry for function calling
TOOL_FUNCTIONS = {
    "search_web": search_web,
    "get_stock_price": get_stock_price,
}

# --- Agent configuration ---

agent_config = {
    "model": "gemini-2.0-flash",
    "system_instruction": (
        "You are a helpful assistant built with Google's Agent Development Kit. "
        "Use your tools to provide accurate, real-time information. Be concise and helpful. "
        "When providing financial data, always include a disclaimer that it may be delayed."
    ),
    "tools": [search_web, get_stock_price],
}


async def handle_message(message: str, history: list | None = None) -> str:
    """Handle a user message with function calling.

    Args:
        message: The user's message.
        history: Optional conversation history.

    Returns:
        The agent's final response text.
    """
    model = genai.GenerativeModel(
        model_name=agent_config["model"],
        system_instruction=agent_config["system_instruction"],
        tools=agent_config["tools"],
    )

    chat = model.start_chat(history=history or [])
    response = chat.send_message(message)

    # Handle function calls
    while response.candidates[0].content.parts:
        function_calls = [
            part
            for part in response.candidates[0].content.parts
            if hasattr(part, "function_call") and part.function_call.name
        ]

        if not function_calls:
            break

        # Execute function calls
        responses = {}
        for fc in function_calls:
            func = TOOL_FUNCTIONS.get(fc.function_call.name)
            if func:
                args = dict(fc.function_call.args)
                result = func(**args)
                responses[fc.function_call.name] = result

        if not responses:
            break

        # Send function results back
        response = chat.send_message(
            genai.protos.Content(
                parts=[
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=name,
                            response={"result": result},
                        )
                    )
                    for name, result in responses.items()
                ]
            )
        )

    # Extract text response
    return response.text


async def main() -> None:
    """Run the agent interactively for local testing."""
    response = await handle_message("What's the stock price of GOOGL?")
    print(f"Agent response: {response}")  # noqa: T201


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
