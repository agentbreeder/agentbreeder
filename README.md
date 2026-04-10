<div align="center">

# AgentBreeder

### Stop wrangling agents. Start shipping them.

**One YAML file. Any framework. Any cloud. Governance built in.**

[![PyPI](https://img.shields.io/pypi/v/agentbreeder?color=blue&label=PyPI)](https://pypi.org/project/agentbreeder/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/agentbreeder?color=green&label=Downloads)](https://pypi.org/project/agentbreeder/)
[![Python](https://img.shields.io/pypi/pyversions/agentbreeder?color=blue)](https://pypi.org/project/agentbreeder/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![CI](https://github.com/open-agent-garden/agentbreeder/actions/workflows/ci.yml/badge.svg)](https://github.com/open-agent-garden/agentbreeder/actions/workflows/ci.yml)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

<br/>

[![LangGraph](https://img.shields.io/badge/LangGraph-supported-purple)](https://github.com/langchain-ai/langgraph)
[![OpenAI Agents](https://img.shields.io/badge/OpenAI_Agents-supported-teal)](https://github.com/openai/openai-agents-python)
[![Claude SDK](https://img.shields.io/badge/Claude_SDK-supported-orange)](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/sdk)
[![CrewAI](https://img.shields.io/badge/CrewAI-supported-red)](https://github.com/crewAIInc/crewAI)
[![Google ADK](https://img.shields.io/badge/Google_ADK-supported-4285F4)](https://github.com/google/adk-python)
[![MCP](https://img.shields.io/badge/MCP-native-green)](https://modelcontextprotocol.io/)

<br/>

[Quick Start](#quick-start) · [How It Works](#how-it-works) · [Install](#install) · [Features](#whats-implemented) · [CLI Reference](#cli) · [Docs](#documentation) · [Contributing](#contributing)

</div>

---

Your company has 47 AI agents. Nobody knows what they cost, who approved them, or which ones are still running. Three teams built the same summarizer. The security team hasn't audited any of them.

**AgentBreeder fixes this.**

Write one `agent.yaml`. Run `agentbreeder deploy`. Your agent is live — with RBAC, cost tracking, audit trail, and org-wide discoverability. Automatic. Not optional.

```
╔═══════════════════════════════════════════════════════════════╗
║                   AGENTBREEDER DEPLOY                         ║
╠═══════════════════════════════════════════════════════════════╣
║                                                               ║
║  ✅  YAML parsed & validated                                  ║
║  ✅  RBAC check passed (team: engineering)                    ║
║  ✅  Dependencies resolved (3 tools, 1 prompt)                ║
║  ✅  Container built (langgraph runtime)                      ║
║  ✅  Deployed to GCP Cloud Run                                ║
║  ✅  Health check passed                                      ║
║  ✅  Registered in org registry                               ║
║  ✅  Cost attribution: engineering / $0.12/hr                 ║
║                                                               ║
║  ENDPOINT: https://support-agent-a1b2c3.run.app              ║
║  STATUS:   ✅ LIVE                                            ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
```

---

## The Problem

AI coding tools make it easy to **build** agents. Nobody has made it easy to **ship** them responsibly.

| What happens today | What happens with AgentBreeder |
|---|---|
| Every framework has its own deploy story | One YAML, any framework, any cloud |
| No RBAC — anyone deploys anything | RBAC validated before the first container builds |
| No cost tracking — $40k surprise cloud bills | Cost attributed per team, per agent, per model |
| No audit trail — "who deployed that?" | Every deploy logged with who, what, when, where |
| No discoverability — duplicate agents everywhere | Org-wide registry — search before you build |
| Governance is bolted on after the fact | Governance is a **structural side effect** of deploying |

**Governance is not configuration. It is a side effect of the pipeline. There is no way to skip it.**

---

## How It Works

```yaml
# agent.yaml — this is the entire config
name: customer-support-agent
version: 1.0.0
team: customer-success
owner: alice@company.com

framework: langgraph          # or: openai_agents, claude_sdk, crewai, google_adk, custom

model:
  primary: claude-sonnet-4
  fallback: gpt-4o

tools:
  - ref: tools/zendesk-mcp    # pull from org registry
  - ref: tools/order-lookup

deploy:
  cloud: gcp                  # or: aws, local, kubernetes
  scaling:
    min: 1
    max: 10
```

```bash
pip install agentbreeder
agentbreeder deploy ./agent.yaml
```

That's it. Eight atomic steps — parse, RBAC, resolve deps, build container, provision infra, deploy, health check, register. If any step fails, the entire deploy rolls back.

---

## Three Ways to Build

All three tiers compile to the same internal format. Same deploy pipeline. Same governance. No lock-in.

| Tier | Who | How | Eject to |
|------|-----|-----|----------|
| **No Code** | PMs, analysts, citizen builders | Visual drag-and-drop canvas — pick model, tools, prompts from the registry | Low Code (view YAML) |
| **Low Code** | ML engineers, DevOps | Write `agent.yaml` in any IDE | Full Code (`agentbreeder eject`) |
| **Full Code** | Senior engineers, researchers | Python/TS SDK with full programmatic control | — |

```python
# Full Code SDK — builder pattern
from agenthub import Agent

agent = (
    Agent("support-agent", version="1.0.0", team="eng")
    .with_model(primary="claude-sonnet-4", fallback="gpt-4o")
    .with_tools(["tools/zendesk-mcp", "tools/order-lookup"])
    .with_prompt(system="You are a helpful customer support agent.")
    .with_deploy(cloud="gcp", min_scale=1, max_scale=10)
)
agent.deploy()
```

---

## What's Implemented

### Frameworks

| Framework | Status | Runtime |
|-----------|--------|---------|
| LangGraph | ✅ | `engine/runtimes/langgraph.py` |
| OpenAI Agents SDK | ✅ | `engine/runtimes/openai_agents.py` |
| Claude SDK (Anthropic) | 🔲 Planned | |
| CrewAI | 🔲 Planned | |
| Google ADK | 🔲 Planned | |
| Custom (bring your own) | ✅ | |

### Cloud Targets

| Target | Status | Deployer |
|--------|--------|----------|
| Local (Docker Compose) | ✅ | `engine/deployers/docker_compose.py` |
| GCP Cloud Run | ✅ | `engine/deployers/gcp_cloudrun.py` |
| AWS ECS Fargate | 🔲 Planned | |
| Kubernetes | 🔲 Planned | |

### LLM Providers (6 providers + fallback chains)

| Provider | Status |
|----------|--------|
| Anthropic (Claude) | ✅ |
| OpenAI (GPT-4o, o1, etc.) | ✅ |
| Google (Gemini) | ✅ |
| Ollama (local models) | ✅ |
| LiteLLM gateway (100+ models) | ✅ |
| OpenRouter | ✅ |

### Secrets Backends

| Backend | Status |
|---------|--------|
| Environment variables / `.env` | ✅ |
| AWS Secrets Manager | ✅ |
| GCP Secret Manager | ✅ |
| HashiCorp Vault | ✅ |

### Platform Features (30+ shipped)

| Feature | Status |
|---------|--------|
| Org-wide agent registry | ✅ |
| Visual agent builder (ReactFlow canvas) | ✅ |
| Multi-agent orchestration (6 strategies) | ✅ |
| Visual orchestration canvas | ✅ |
| A2A (Agent-to-Agent) protocol | ✅ |
| MCP server hub + sidecar injection | ✅ |
| Agent evaluation framework | ✅ |
| Cost tracking (per team / agent / model) | ✅ |
| RBAC + team management | ✅ |
| Full audit trail | ✅ |
| Distributed tracing (OpenTelemetry) | ✅ |
| AgentOps fleet dashboard | ✅ |
| Community marketplace + templates | ✅ |
| Git workflow (PR create → review → publish) | ✅ |
| Prompt builder + test panel | ✅ |
| RAG index builder | ✅ |
| Memory configuration | ✅ |
| Tool sandbox execution | ✅ |
| Interactive chat playground | ✅ |
| API versioning (v1 stable, v2 preview) | ✅ |
| Python SDK | ✅ |
| TypeScript SDK | ✅ |
| Tier mobility (`agentbreeder eject`) | ✅ |

---

## Orchestration

Six strategies. Define in YAML or the visual canvas — both compile to the same pipeline.

```yaml
# orchestration.yaml
name: support-pipeline
version: "1.0.0"
team: customer-success
strategy: router       # router | sequential | parallel | hierarchical | supervisor | fan_out_fan_in

agents:
  triage:
    ref: agents/triage-agent
    routes:
      - condition: billing
        target: billing
      - condition: default
        target: general
  billing:
    ref: agents/billing-agent
    fallback: general
  general:
    ref: agents/general-agent

shared_state:
  type: session_context
  backend: redis

deploy:
  target: gcp
```

Or programmatically with the SDK:

```python
from agenthub import Orchestration

pipeline = (
    Orchestration("support-pipeline", strategy="router", team="eng")
    .add_agent("triage",  ref="agents/triage-agent")
    .add_agent("billing", ref="agents/billing-agent")
    .add_agent("general", ref="agents/general-agent")
    .with_route("triage", condition="billing", target="billing")
    .with_route("triage", condition="default", target="general")
    .with_shared_state(state_type="session_context", backend="redis")
)
pipeline.deploy()
```

---

## Install

### PyPI (recommended)

```bash
# Full CLI + API server + engine
pip install agentbreeder

# Lightweight SDK only (for programmatic agent definitions)
pip install agentbreeder-sdk
```

### Homebrew (macOS / Linux)

```bash
brew tap open-agent-garden/agentbreeder
brew install agentbreeder
```

### Docker

```bash
# API server
docker pull agentbreeder/api
docker run -p 8000:8000 agentbreeder/api

# Dashboard
docker pull agentbreeder/dashboard
docker run -p 3001:3001 agentbreeder/dashboard

# CLI (for CI/CD pipelines)
docker pull agentbreeder/cli
docker run agentbreeder/cli deploy agent.yaml --target gcp
```

---

## Quick Start

```bash
pip install agentbreeder

# Scaffold your first agent (interactive wizard — pick framework, cloud, model)
agentbreeder init

# Validate the config
agentbreeder validate agent.yaml

# Deploy locally
agentbreeder deploy agent.yaml --target local
```

**Or run the full platform locally:**

```bash
git clone https://github.com/open-agent-garden/agentbreeder.git
cd agentbreeder

python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Start postgres + redis + API + dashboard
docker compose -f deploy/docker-compose.yml up -d
```

Dashboard: `http://localhost:3001` · API: `http://localhost:8000` · API Docs: `http://localhost:8000/docs`

See [docs/quickstart.md](docs/quickstart.md) for the full guide.

---

## CLI

24 commands. Everything you need from scaffold to teardown.

```bash
agentbreeder init              # Scaffold a new agent project (interactive wizard)
agentbreeder validate          # Validate agent.yaml without deploying
agentbreeder deploy            # Deploy an agent (the core command)
agentbreeder up / down         # Start / stop the full local platform stack
agentbreeder status            # Show deploy status
agentbreeder logs <name>       # Tail agent logs
agentbreeder list              # List agents / tools / models / prompts
agentbreeder describe <name>   # Show detail for a registry entity
agentbreeder search <query>    # Search across the entire registry
agentbreeder chat <name>       # Interactive chat with a deployed agent
agentbreeder eval              # Run evaluations against golden datasets
agentbreeder eject             # Eject from YAML to Full Code SDK
agentbreeder submit            # Create a PR for review
agentbreeder review            # Review / approve / reject a submission
agentbreeder publish           # Merge approved PR and publish to registry
agentbreeder provider          # Manage LLM provider connections
agentbreeder secret            # Manage secrets across backends (env, AWS, GCP, Vault)
agentbreeder scan              # Discover MCP servers and LiteLLM models
agentbreeder template          # Manage agent templates
agentbreeder orchestration     # Multi-agent orchestration commands
agentbreeder teardown          # Remove a deployed agent and clean up resources
```

See [docs/cli-reference.md](docs/cli-reference.md) for full usage and flags.

---

## Architecture

```
Developer                    AgentBreeder Platform                  Cloud
                                     ┌──────────────┐
agent.yaml  ──▶  [ CLI ]  ──▶  │  API Server  │  ──▶  [ Engine ]  ──▶  AWS / GCP / Local
                                     └──────┬───────┘         │
                                            │                 ▼
                                     ┌──────▼───────┐  ┌─────────────────┐
                                     │  PostgreSQL   │  │ Container Build │
                                     │  (Registry)   │  │  + MCP Sidecar  │
                                     └──────┬───────┘  └─────────────────┘
                                            │
                                     ┌──────▼───────┐
                                     │    Redis      │
                                     │   (Queue)     │
                                     └──────────────┘
```

**Deploy pipeline** — 8 atomic steps. If any fails, the entire deploy rolls back:

1. Parse & validate YAML
2. RBAC check (fail fast if unauthorized)
3. Dependency resolution (tools, prompts, models from registry)
4. Container build (framework-specific Dockerfile)
5. Infrastructure provision (Pulumi)
6. Deploy & health check
7. Auto-register in org registry
8. Return endpoint URL

---

## Project Structure

```
agentbreeder/
├── api/                # FastAPI backend — 25 route modules, services, models
├── cli/                # CLI — 24 commands (Typer + Rich)
├── engine/
│   ├── config_parser.py       # YAML parsing + JSON Schema validation
│   ├── builder.py             # 8-step atomic deploy pipeline
│   ├── orchestrator.py        # Multi-agent orchestration engine
│   ├── providers/             # LLM providers (Anthropic, OpenAI, Google, Ollama)
│   ├── runtimes/              # Framework builders (LangGraph, OpenAI Agents)
│   ├── deployers/             # Cloud deployers (Docker Compose, GCP Cloud Run)
│   ├── secrets/               # Secrets backends (env, AWS, GCP, Vault)
│   ├── a2a/                   # Agent-to-Agent protocol
│   └── mcp/                   # MCP server packaging
├── registry/           # Catalog services — agents, tools, models, prompts, templates
├── sdk/python/         # Python SDK (pip install agentbreeder-sdk)
├── connectors/         # LiteLLM, OpenRouter, MCP scanner
├── dashboard/          # React + TypeScript + Tailwind
├── tests/              # 2,378 unit tests + Playwright E2E
└── examples/           # Working examples per framework + orchestration
```

---

## Documentation

| Doc | Description |
|-----|-------------|
| [Quickstart](docs/quickstart.md) | Local setup in under 10 minutes |
| [CLI Reference](docs/cli-reference.md) | All 24 commands with flags and examples |
| [agent.yaml Reference](docs/agent-yaml.md) | Full configuration field reference |
| [orchestration.yaml Reference](docs/orchestration-yaml.md) | Multi-agent pipeline config |
| [Orchestration SDK](docs/orchestration-sdk.md) | Python/TS SDK for complex workflows |
| [API Stability](docs/api-stability.md) | Versioning and deprecation policy |
| [Local Development](docs/local-development.md) | Contributor setup guide |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Technical deep-dive |
| [ROADMAP.md](ROADMAP.md) | Release plan and milestone status |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |

---

## Compared to Alternatives

| | AgentBreeder | DIY Deploy Scripts | LangServe | Vertex AI Agent Builder |
|---|---|---|---|---|
| Framework-agnostic | ✅ 6 frameworks | ❌ Your stack only | ❌ LangChain only | ❌ Google only |
| Multi-cloud | ✅ AWS + GCP + Local | ❌ Manual | ❌ No | ❌ GCP only |
| Governance built-in | ✅ Automatic | ❌ None | ❌ None | ⚠️ Partial |
| RBAC | ✅ | ❌ | ❌ | ✅ |
| Cost tracking | ✅ Per team/agent/model | ❌ | ❌ | ⚠️ Project-level |
| Org registry | ✅ | ❌ | ❌ | ❌ |
| Multi-agent orchestration | ✅ 6 strategies | ❌ | ❌ | ⚠️ Limited |
| MCP native | ✅ | ❌ | ❌ | ❌ |
| Open source | ✅ Apache 2.0 | ✅ | ✅ | ❌ |
| Vendor lock-in | None | N/A | LangChain | Google Cloud |

---

## Contributing

High-impact areas where contributions are especially welcome:

- **AWS ECS deployer** — `engine/deployers/aws_ecs.py` — most requested cloud target
- **Framework runtimes** — CrewAI, Claude SDK, Google ADK in `engine/runtimes/`
- **Agent templates** — starter templates for common use cases
- **Connectors** — Datadog, Grafana, and other observability integrations

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions and guidelines.

---

## Community

- [GitHub Issues](https://github.com/open-agent-garden/agentbreeder/issues) — bugs and feature requests
- [GitHub Discussions](https://github.com/open-agent-garden/agentbreeder/discussions) — questions and show & tell

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

---

<div align="center">

### Built by [Rajit Saha](https://www.linkedin.com/in/rajsaha/)

Tech executive · 20+ years building enterprise data & ML platforms · Udemy, LendingClub, VMware, Yahoo

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Rajit_Saha-blue?logo=linkedin)](https://www.linkedin.com/in/rajsaha/)
[![GitHub](https://img.shields.io/github/followers/rajitsaha?label=Follow&style=social)](https://github.com/rajitsaha)

<br/>

**If AgentBreeder saves you time, [star the repo](https://github.com/open-agent-garden/agentbreeder) and share it with your team.**

</div>
