# Google ADK Starter

Google Agent Development Kit (ADK) starter agent with function declarations, Gemini model, and tool calling. Use this as a starting point for building agents with Google's ADK and AgentBreeder.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Python 3.11+ with `google-generativeai` installed
- Google API key or service account

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your Google API key
   ```

2. **Deploy locally:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

3. **Chat with the agent:**
   ```bash
   agentbreeder chat google-adk-starter --message "Search for recent AI developments"
   ```

## Architecture

```
User Message
    |
    v
[Gemini Agent] -- Function calling loop
    |
    +---> search_web (web search)
    +---> get_stock_price (financial data)
    |
    v (repeat until no more function calls)
    |
    v
Final Response
```

### Key Files

- `agent.yaml` -- AgentBreeder configuration
- `agent.py` -- Google ADK agent with function calling (entry point)

## Customization

### Add a new tool

Define a Python function and add it to the tools list:

```python
def my_tool(param: str) -> str:
    """Description of what this tool does."""
    return "result"

agent_config = {
    "tools": [search_web, get_stock_price, my_tool],
}
```

### Deploy to GCP Cloud Run

```yaml
deploy:
  cloud: gcp
  runtime: cloud-run
  region: us-central1
```

### Switch Gemini model

```yaml
model:
  primary: gemini-1.5-pro       # More capable
  # primary: gemini-2.0-flash   # Faster
```
