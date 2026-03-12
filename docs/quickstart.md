# Quickstart Guide

Get Agent Garden running locally in under 10 minutes.

---

## Prerequisites

- Python 3.11+
- Node.js 22+
- Docker & Docker Compose
- Git

---

## 1. Clone & Install

```bash
git clone git@github.com:open-agent-garden/agent-garden.git
cd agent-garden

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
| Email    | `admin@agent-garden.local`   |
| Password | `plant`                      |
| Name     | Gardner                      |
| Role     | Admin                        |
| Team     | Agent Garden Platform        |

> **Change the default password** in production. This account is for local development only.

## 4. Create Your First Agent

```bash
garden init
```

Follow the interactive wizard to scaffold a new agent project. It will create:
- `agent.yaml` — agent configuration
- `agent.py` — working example agent
- `requirements.txt` — Python dependencies
- `.env.example` — environment template

## 5. Validate & Deploy

```bash
# Validate the config
garden validate

# Deploy locally
garden deploy --target local

# Deploy to GCP Cloud Run
garden deploy --target cloud-run --region us-central1
```

### Local models with Ollama

For local development without cloud API keys, Agent Garden supports [Ollama](https://ollama.com) via the provider abstraction layer. Set your model to an Ollama-hosted model and the engine routes requests locally:

```yaml
model:
  primary: ollama/llama3
  gateway: ollama
```

Start Ollama before deploying:
```bash
ollama serve &
ollama pull llama3
garden deploy --target local
```

## 6. Verify

```bash
# Check status
garden status

# View logs
garden logs <agent-name> --follow

# Browse the dashboard
open http://localhost:3001
```

---

## CLI Commands

| Command            | Description                              |
|--------------------|------------------------------------------|
| `garden init`      | Scaffold a new agent project             |
| `garden validate`  | Validate agent.yaml without deploying    |
| `garden deploy`    | Deploy an agent                          |
| `garden list`      | List agents/tools/models/prompts         |
| `garden describe`  | Show detail for a registry entity        |
| `garden search`    | Search across the registry               |
| `garden logs`      | Tail logs from a deployed agent          |
| `garden status`    | Show deploy status                       |
| `garden teardown`  | Remove a deployed agent                  |
| `garden scan`      | Discover MCP servers and LiteLLM models  |

---

## Next Steps

- Browse the [CLI Reference](cli-reference.md)
- Read the [agent.yaml spec](agent-yaml.md)
- Check the [ROADMAP](../ROADMAP.md) for what's coming
