# Quickstart Guide

Get AgentBreeder running locally in under 10 minutes.

---

## Prerequisites

- Python 3.11+
- Node.js 22+
- Docker & Docker Compose
- Git

---

## 1. Clone & Install

```bash
git clone git@github.com:open-agentbreeder/agentbreeder.git
cd agentbreeder

# Python
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

# Dashboard
cd dashboard && npm install && cd ..
```

## 2. Start the Stack

```bash
docker compose -f deploy/docker-compose.yml up -d
```

This starts PostgreSQL, Redis, the API server, and the dashboard.

| Service   | URL                        |
|-----------|----------------------------|
| Dashboard | http://localhost:3001       |
| API       | http://localhost:8000       |
| API Docs  | http://localhost:8000/docs  |
| Health    | http://localhost:8000/health|

## 3. Login

A default admin account is created on first startup:

| Field    | Value                        |
|----------|------------------------------|
| Email    | `admin@agentbreeder.local`   |
| Password | `plant`                      |
| Name     | Gardner                      |
| Role     | Admin                        |
| Team     | AgentBreeder Platform        |

> **Change the default password** in production. This account is for local development only.

## 4. Create Your First Agent

```bash
agentbreeder init
```

Follow the interactive wizard to scaffold a new agent project. It will create:
- `agent.yaml` — agent configuration
- `agent.py` — working example agent
- `requirements.txt` — Python dependencies
- `.env.example` — environment template

## 5. Validate & Deploy

```bash
# Validate the config
agentbreeder validate

# Deploy locally
agentbreeder deploy --target local

# Deploy to GCP Cloud Run
agentbreeder deploy --target cloud-run --region us-central1
```

### Local models with Ollama

For local development without cloud API keys, AgentBreeder supports [Ollama](https://ollama.com) via the provider abstraction layer. Set your model to an Ollama-hosted model and the engine routes requests locally:

```yaml
model:
  primary: ollama/llama3
  gateway: ollama
```

Start Ollama before deploying:
```bash
ollama serve &
ollama pull llama3
agentbreeder deploy --target local
```

## 6. Verify

```bash
# Check status
agentbreeder status

# View logs
agentbreeder logs <agent-name> --follow

# Browse the dashboard
open http://localhost:3001
```

---

## CLI Commands

| Command            | Description                              |
|--------------------|------------------------------------------|
| `agentbreeder init`      | Scaffold a new agent project             |
| `agentbreeder validate`  | Validate agent.yaml without deploying    |
| `agentbreeder deploy`    | Deploy an agent                          |
| `agentbreeder list`      | List agents/tools/models/prompts         |
| `agentbreeder describe`  | Show detail for a registry entity        |
| `agentbreeder search`    | Search across the registry               |
| `agentbreeder logs`      | Tail logs from a deployed agent          |
| `agentbreeder status`    | Show deploy status                       |
| `agentbreeder teardown`  | Remove a deployed agent                  |
| `agentbreeder scan`      | Discover MCP servers and LiteLLM models  |
| `agentbreeder chat`      | Interactive chat with a deployed agent    |
| `agentbreeder submit`    | Submit a resource for review (create PR)  |
| `agentbreeder review`    | Review, approve, or reject pull requests  |
| `agentbreeder publish`   | Merge approved PR and publish to registry |
| `agentbreeder provider`  | Manage LLM provider connections           |

---

## Next Steps

- Browse the [CLI Reference](cli-reference.md)
- Read the [agent.yaml spec](agent-yaml.md)
- Check the [ROADMAP](../ROADMAP.md) for what's coming
