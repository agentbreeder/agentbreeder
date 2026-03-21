# Claude SDK Starter

Claude SDK agent with tool use and an async message handler. Use this as a starting point for building agents directly with the Anthropic Python SDK and AgentBreeder.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Python 3.11+ with `anthropic` installed
- Anthropic API key

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your Anthropic API key
   ```

2. **Deploy locally:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

3. **Chat with the agent:**
   ```bash
   agentbreeder chat claude-sdk-starter --message "What time is it in London?"
   ```

## Architecture

```
User Message
    |
    v
[Claude SDK Agent] -- Tool use loop
    |
    +---> get_time (timezone lookup)
    +---> lookup_info (knowledge base)
    |
    v (repeat until no more tool calls)
    |
    v
Final Response
```

### Key Files

- `agent.yaml` -- AgentBreeder configuration
- `agent.py` -- Claude SDK agent with tool use loop (entry point)

### Tool Use Loop

The agent implements the standard Claude tool use pattern:
1. Send message to Claude with tool definitions
2. If Claude wants to use a tool, execute it and send results back
3. Repeat until Claude returns a text response (no more tool calls)

## Customization

### Add a new tool

1. Add the tool definition to the `TOOLS` list in `agent.py`
2. Add execution logic to `execute_tool()`
3. Register in `agent.yaml` under `tools:`

### Enable streaming

```python
async with client.messages.stream(
    model=agent_config["model"],
    messages=messages,
    tools=agent_config["tools"],
) as stream:
    async for text in stream.text_stream:
        print(text, end="", flush=True)
```

### Use extended thinking

```python
response = await client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=16000,
    thinking={"type": "enabled", "budget_tokens": 10000},
    messages=messages,
)
```
