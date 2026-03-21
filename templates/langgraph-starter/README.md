# LangGraph Starter

Minimal LangGraph agent with tool-calling nodes, conditional routing, and typed state. Use this as a starting point for building LangGraph-based agents with AgentBreeder.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Python 3.11+ with `langgraph` and `langchain-core` installed
- Anthropic API key or OpenAI API key

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

2. **Run locally:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

3. **Test the graph:**
   ```bash
   agentbreeder chat langgraph-starter-agent --message "Search for quantum computing breakthroughs"
   ```

## Architecture

```
[agent] -- Decides action (respond or call tool)
    |
    +---> has tool calls? --yes--> [tools] -- Execute tool calls
    |                                  |
    +---> no tool calls ---> END       +---> back to [agent]
```

### Key Files

- `agent.yaml` -- AgentBreeder configuration
- `graph.py` -- LangGraph graph definition (entry point)

### Graph Structure

- **agent node** -- Main decision node; produces responses or tool calls
- **tool node** -- Executes tool calls and returns results
- **conditional edge** -- Routes to tools or end based on tool calls

## Customization

### Add a new tool

1. Add the tool schema to the `TOOLS` dict in `graph.py`
2. Add the execution logic to `execute_tool()`
3. Register it in `agent.yaml` under `tools:`

### Integrate a real LLM

Replace the pattern-matching logic in `agent_node()` with an LLM call:

```python
from langchain_anthropic import ChatAnthropic

llm = ChatAnthropic(model="claude-sonnet-4").bind_tools(list(TOOLS.values()))

def agent_node(state: AgentState) -> AgentState:
    response = llm.invoke(state["messages"])
    return {"messages": [response]}
```

### Eject to full code

```bash
agentbreeder eject --format python
```

This generates a standalone Python project with no AgentBreeder dependencies.
