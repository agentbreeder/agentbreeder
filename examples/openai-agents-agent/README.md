# OpenAI Agents SDK Example — Research Assistant

A research assistant agent built with the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python), deployed via Agent Garden.

## What it does

- Uses two tools: `web_search` (stub) and `calculator` (safe math eval)
- Demonstrates `@function_tool` decorators, `Agent` creation, and `Runner.run()`
- Wraps as a FastAPI service automatically when deployed

## Prerequisites

```bash
export OPENAI_API_KEY="sk-..."
```

## Run locally (standalone)

```bash
pip install openai-agents openai
python agent.py
```

## Deploy with Agent Garden

```bash
garden deploy examples/openai-agents-agent/
```

## Project structure

| File | Purpose |
|------|---------|
| `agent.yaml` | Agent Garden configuration |
| `agent.py` | Agent definition with tools |
| `requirements.txt` | Python dependencies |
