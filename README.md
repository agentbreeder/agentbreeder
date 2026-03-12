# Agent Garden

**Define Once. Deploy Anywhere. Govern Automatically.**

[![CI](https://github.com/open-agent-garden/agent-garden/actions/workflows/ci.yml/badge.svg)](https://github.com/open-agent-garden/agent-garden/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/open-agent-garden/agent-garden/graph/badge.svg)](https://codecov.io/gh/open-agent-garden/agent-garden)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

Every team picks a different AI agent framework. Nobody knows what's already deployed. Nobody tracks the cost. Agent Garden fixes that — one open-source platform to build, deploy, govern, and discover all your AI agents.

---

## The Problem

- **Framework sprawl** — teams use LangGraph, CrewAI, Claude SDK, OpenAI Agents, Google ADK... each with its own deployment story
- **No discoverability** — five teams build five Salesforce tools because nobody knows the others exist
- **No cost visibility** — AI spend is a black box across teams, agents, and models
- **Governance is bolted on** — RBAC, audit trails, and compliance are always an afterthought

## How Agent Garden Works

```
garden init  →  Write agent.yaml  →  garden deploy  →  Agent is live
```

Define your agent in a single YAML file:

```yaml
name: customer-support-agent
version: 1.0.0
team: customer-success
owner: alice@company.com

framework: langgraph

model:
  primary: claude-sonnet-4
  fallback: gpt-4o

tools:
  - ref: tools/zendesk-mcp
  - ref: tools/order-lookup

deploy:
  cloud: aws
  runtime: ecs-fargate
  scaling:
    min: 1
    max: 10
```

Run `garden deploy` and your agent is live on AWS or GCP — with RBAC, cost tracking, audit trail, and org-wide discoverability automatic and zero extra work.

---

## Features

| Feature | v0.1 | v0.2 | v0.3 (Current) |
|---------|:-:|:-:|:-:|
| Frameworks | LangGraph | +CrewAI, Claude SDK, ADK, Custom | +OpenAI Agents runtime |
| Cloud targets | Local / Docker Compose | +Kubernetes | +GCP Cloud Run |
| Registry | Agents, Tools, Models | +Prompts, KBs, Semantic search | +RAG indexes, Memory configs |
| Dashboard | Read-only browser | +Low-code builders (Prompt, Tool, MCP) | +Visual Agent Builder, Deploy dialog, RAG/Memory builders, Approvals |
| Providers | - | LiteLLM gateway | +Provider abstraction (OpenAI, Ollama, fallback chains) |
| Governance | Auto-registration | +Basic RBAC | +Approval workflows, PR review, environment promotion |
| Git workflow | - | - | Git backend, PR creation + review |
| Cost tracking | - | Basic | Per-team/agent/model |

### What's New in v0.3

- **Provider abstraction layer** -- OpenAI + Ollama providers with automatic fallback chains (`engine/providers/`)
- **GCP Cloud Run deployer** -- `garden deploy --target cloud-run` for serverless container deployment
- **OpenAI Agents runtime** -- first-class support for the OpenAI Agents SDK (`engine/runtimes/openai_agents.py`)
- **Deploy from Dashboard** -- 8-step deploy pipeline dialog, no CLI required
- **Visual Agent Builder** -- ReactFlow canvas with 8 node types, drag-and-drop agent composition
- **RAG Builder** -- create RAG indexes, ingest files, hybrid search via the dashboard
- **Memory Builder** -- configure conversation memory, storage backends
- **Approval Workflow** -- PR review UI, environment promotion, approval gates
- **Test Prompt panel** -- test prompts against models with variables directly in the dashboard
- **Tool sandbox execution** -- safely execute tools in an isolated sandbox environment
- **Git workflow backend** -- git operations and PR lifecycle management from the API

## Supported Stack

### Agent Frameworks

| Framework | Status | Runtime |
|-----------|--------|---------|
| LangGraph | v0.1 | `engine/runtimes/langgraph.py` |
| CrewAI | v0.2 | Planned |
| Claude SDK (Anthropic) | v0.2 | Planned |
| OpenAI Agents SDK | v0.3 | `engine/runtimes/openai_agents.py` |
| Google ADK | v0.2 | Planned |
| Custom (any Python/TS) | v0.2 | Planned |

### Cloud Targets

| Target | Status | Deployer |
|--------|--------|----------|
| Local Docker Compose | v0.1 | `engine/deployers/docker_compose.py` |
| Kubernetes | v0.1 | Planned |
| GCP Cloud Run | v0.3 | `engine/deployers/gcp_cloudrun.py` |
| AWS ECS Fargate | Planned | — |
| AWS Lambda | Planned | — |
| GCP GKE | Planned | — |

### Model Providers

| Provider | Status |
|----------|--------|
| OpenAI | v0.3 (`engine/providers/openai_provider.py`) |
| Ollama (local) | v0.3 (`engine/providers/ollama_provider.py`) |
| LiteLLM gateway | v0.2 |
| Fallback chains | v0.3 |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Git

### Install & Run

```bash
# Clone the repo
git clone https://github.com/open-agent-garden/agent-garden.git
cd agent-garden

# Set up Python environment
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Start the local stack (postgres, redis, API, dashboard)
docker compose up -d

# Create your first agent
garden init

# Deploy locally
garden deploy ./agent.yaml --target local
```

Your agent is now running at `http://localhost:8080/agents/{name}/invoke` with a registry entry automatically created.

---

## Architecture

```
Developer                    Agent Garden Platform                     Cloud

agent.yaml  ──>  [ CLI ]  ──>  [ API Server ]  ──>  [ Engine ]  ──>  [ AWS / GCP / K8s ]
                                      |                  |
                                      v                  v
                                [ PostgreSQL ]    [ Container Registry ]
                                  (Registry)             |
                                      |                  v
                                  [ Redis ]       [ Agent + Sidecar ]
                                  (Queue)
```

**The deploy pipeline** (every step is atomic — if any fails, the entire deploy rolls back):

1. Parse & validate YAML
2. RBAC check (fail fast if unauthorized)
3. Dependency resolution (fetch refs from registry)
4. Container build (framework-specific Dockerfile)
5. Infrastructure provision (Pulumi)
6. Deploy & health check
7. Auto-register in registry
8. Return endpoint URL

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full technical deep-dive.

---

## Project Structure

```
agent-garden/
├── api/                    # FastAPI backend (routes for agents, deploys, prompts, RAG, memory, git, sandbox)
├── cli/                    # CLI (Typer + Rich)
├── sdk/                    # Python & TypeScript SDKs
├── engine/                 # Deploy pipeline
│   ├── providers/          # Provider abstraction (OpenAI, Ollama, fallback chains)
│   ├── runtimes/           # Framework-specific builders (LangGraph, OpenAI Agents)
│   └── deployers/          # Cloud-specific deployers (Docker Compose, GCP Cloud Run)
├── connectors/             # Integration plugins (LiteLLM, LangSmith, etc.)
├── registry/               # Catalog service (agents, tools, models, prompts)
├── dashboard/              # React + TypeScript + Tailwind UI
├── templates/              # No-code agent templates (Seeds)
├── deploy/                 # Docker Compose + Helm charts
├── tests/                  # Unit, integration, E2E tests
└── examples/               # Working agent examples per framework
```

See [CLAUDE.md](CLAUDE.md) for the fully annotated project structure and coding standards.

---

## Documentation

| Doc | Description |
|-----|-------------|
| [Quick Start](docs/quickstart.md) | 10-minute guide from zero to deployed agent |
| [CLI Reference](docs/cli-reference.md) | Every command documented with examples |
| [agent.yaml Reference](docs/agent-yaml.md) | Complete field reference for the config file |
| [Local Development](docs/local-development.md) | Contributor setup guide |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture — deploy pipeline, abstractions, data model |
| [ROADMAP.md](ROADMAP.md) | Release plan — v0.1 through v1.0 with milestones and success metrics |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contributor guide — standards, how to add deployers/runtimes |
| [SECURITY.md](SECURITY.md) | Security policy — reporting vulnerabilities |

---

## Contributing

We welcome contributions! Agent Garden has a naturally pluggable architecture — every new deployer, runtime, connector, and template is a self-contained contribution.

**High-impact contribution areas:**
- Add a cloud deployer (Azure, Oracle Cloud, Render, Fly.io)
- Add a framework runtime (Semantic Kernel, AutoGen, etc.)
- Add a connector (Datadog, Grafana, etc.)
- Create agent templates (Seeds)
- Improve documentation

See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

---

## Community

- [GitHub Discussions](https://github.com/open-agent-garden/agent-garden/discussions) — questions, ideas, show & tell
- [GitHub Issues](https://github.com/open-agent-garden/agent-garden/issues) — bug reports and feature requests

---

## License

Agent Garden is open source under the [Apache License 2.0](LICENSE).

---

**Built with AI-assisted development from Day 1.** See [AGENT.md](AGENT.md) for how we use AI skills to build Agent Garden.
