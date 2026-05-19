# LangGraph Example — Hello World Agent

A minimal AgentBreeder agent built with [LangGraph](https://github.com/langchain-ai/langgraph) — a single-node graph that echoes whatever message you send.

## What it does

- Defines a one-node `StateGraph` that returns `"Hello from AgentBreeder! You said: <message>"`
- Exports the compiled graph as `graph` so the AgentBreeder server wrapper can invoke it
- No LLM API key required — the node is pure Python

## Run locally (standalone)

```bash
pip install langgraph langchain-core
python -c "from agent import graph; print(graph.invoke({'message': 'hi'}))"
```

## Deploy with AgentBreeder

```bash
agentbreeder validate
agentbreeder deploy examples/langgraph-agent/ --target local
```

## Call the deployed agent

```bash
curl -X POST http://localhost:8080/invoke \
  -H "Content-Type: application/json" \
  -d '{"input": {"message": "hello"}}'
```

## Project structure

| File | Purpose |
|------|---------|
| `agent.yaml` | AgentBreeder configuration |
| `agent.py` | LangGraph agent definition (exports `graph`) |
| `requirements.txt` | Python dependencies |
