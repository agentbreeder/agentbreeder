# Agent Garden

**Define Once. Deploy Anywhere. Govern Automatically.**

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

| Feature | v0.1 (Current) | v0.2 | v0.3+ |
|---------|:-:|:-:|:-:|
| Frameworks | LangGraph | +CrewAI, Claude SDK, OpenAI, ADK | Custom |
| Cloud targets | Local / Docker Compose | +AWS ECS/Lambda, GCP Cloud Run | +Azure |
| Registry | Agents, Tools, Models | +Prompts, KBs, Semantic search | Full lineage |
| Dashboard | Read-only browser | +Low-code visual builder | +No-code templates |
| Governance | Auto-registration | +Basic RBAC | Full RBAC, Audit, Cost |
| Cost tracking | - | Basic | Per-team/agent/model |
| A2A Communication | - | - | Agent-to-Agent protocol |

## Supported Stack

### Agent Frameworks

| Framework | Status |
|-----------|--------|
| LangGraph | v0.1 |
| CrewAI | v0.2 |
| Claude SDK (Anthropic) | v0.2 |
| OpenAI Agents SDK | v0.2 |
| Google ADK | v0.2 |
| Custom (any Python/TS) | v0.2 |

### Cloud Targets

| Target | Status |
|--------|--------|
| Local Docker Compose | v0.1 |
| Kubernetes | v0.1 |
| AWS ECS Fargate | v0.2 |
| AWS Lambda | v0.2 |
| GCP Cloud Run | v0.2 |
| GCP GKE | v0.2 |

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
├── api/                    # FastAPI backend
├── cli/                    # CLI (Typer + Rich)
├── sdk/                    # Python & TypeScript SDKs
├── engine/                 # Deploy pipeline, runtimes, deployers
│   ├── runtimes/           # Framework-specific builders
│   └── deployers/          # Cloud-specific deployers
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
| [CLAUDE.md](CLAUDE.md) | AI development guide — project structure, coding standards, API conventions |
| [AGENT.md](AGENT.md) | AI skill library — 20+ reusable skills for building, testing, deploying |
| [ROADMAP.md](ROADMAP.md) | Release plan — v0.1 through v1.0 with milestones and success metrics |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture — deploy pipeline, abstractions, data model |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contributor guide — setup, standards, how to add deployers/runtimes |
| [SECURITY.md](SECURITY.md) | Security policy — reporting vulnerabilities, security considerations |

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
