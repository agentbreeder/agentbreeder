# OpenAI Agents Starter

Minimal OpenAI Agents SDK agent with function tools and structured instructions. Use this as a starting point for building agents with the OpenAI Agents SDK and AgentBreeder.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Python 3.11+ with `openai-agents` installed
- OpenAI API key

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your OpenAI API key
   ```

2. **Deploy locally:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

3. **Chat with the agent:**
   ```bash
   agentbreeder chat openai-agents-starter --message "What's the weather in Tokyo?"
   ```

## Architecture

```
User Message
    |
    v
[OpenAI Agent] -- GPT-4o with function calling
    |
    +---> get_weather (city lookup)
    +---> search_knowledge (knowledge base)
    |
    v
Response
```

### Key Files

- `agent.yaml` -- AgentBreeder configuration
- `agent.py` -- OpenAI Agents SDK agent definition (entry point)

## Customization

### Add a new tool

```python
@function_tool
def my_tool(param: str) -> str:
    """Description of what this tool does."""
    return "result"

agent = Agent(
    name="My Agent",
    tools=[get_weather, search_knowledge, my_tool],  # Add here
)
```

### Add guardrails

```python
from agents import Agent, InputGuardrail

agent = Agent(
    name="My Agent",
    input_guardrails=[InputGuardrail(guardrail_function=my_guardrail)],
)
```

### Switch to a different model

Update `model.primary` in `agent.yaml`:
```yaml
model:
  primary: gpt-4o-mini     # Faster, cheaper
  # primary: o1-preview    # Reasoning model
```
