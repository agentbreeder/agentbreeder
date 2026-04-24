<div align="center">

# AgentBreeder™

### Stop wrangling agents. Start shipping them.

**One YAML file. Any framework. Any cloud. Governance built in.**

[![PyPI](https://img.shields.io/pypi/v/agentbreeder?color=blue&label=PyPI)](https://pypi.org/project/agentbreeder/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/agentbreeder?color=green&label=Downloads)](https://pypi.org/project/agentbreeder/)
[![npm](https://img.shields.io/npm/v/@agentbreeder/sdk?color=red&label=npm)](https://www.npmjs.com/package/@agentbreeder/sdk)
[![Python](https://img.shields.io/pypi/pyversions/agentbreeder?color=blue)](https://pypi.org/project/agentbreeder/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![CI](https://github.com/agentbreeder/agentbreeder/actions/workflows/ci.yml/badge.svg)](https://github.com/agentbreeder/agentbreeder/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-96%25-brightgreen)](https://github.com/agentbreeder/agentbreeder/actions)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

<br/>

[![LangGraph](https://img.shields.io/badge/LangGraph-supported-purple)](https://github.com/langchain-ai/langgraph)
[![OpenAI Agents](https://img.shields.io/badge/OpenAI_Agents-supported-teal)](https://github.com/openai/openai-agents-python)
[![Claude SDK](https://img.shields.io/badge/Claude_SDK-supported-orange)](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/sdk)
[![CrewAI](https://img.shields.io/badge/CrewAI-supported-red)](https://github.com/crewAIInc/crewAI)
[![Google ADK](https://img.shields.io/badge/Google_ADK-supported-4285F4)](https://github.com/google/adk-python)
[![MCP](https://img.shields.io/badge/MCP-native-green)](https://modelcontextprotocol.io/)

<br/>

[Quick Start](#quick-start) · [Install](#install) · [Docs](https://www.agentbreeder.io/docs) · [Contributing](#contributing)

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
  primary: claude-sonnet-4-6
  fallback: gpt-4o

tools:
  - ref: tools/zendesk-mcp    # pull from org registry
  - ref: tools/order-lookup

deploy:
  cloud: gcp                  # or: aws, azure, local, kubernetes
  scaling:
    min: 1
    max: 10
```

```bash
pip3 install agentbreeder
agentbreeder deploy ./agent.yaml
```

Eight atomic steps run in sequence: parse → RBAC → resolve deps → build container → provision infra → deploy → health check → register. If any step fails, the entire deploy rolls back.

---

## Three Ways to Build

All three tiers compile to the same internal format. Same deploy pipeline. Same governance. No lock-in.

| Tier | Who | How | Eject to |
|------|-----|-----|----------|
| **No Code** | PMs, analysts, citizen builders | Visual drag-and-drop canvas — pick model, tools, prompts from the registry | Low Code |
| **Low Code** | ML engineers, DevOps | Write `agent.yaml` in any IDE | Full Code (`agentbreeder eject`) |
| **Full Code** | Senior engineers, researchers | Python/TS SDK with full programmatic control | — |

```python
from agenthub import Agent

agent = (
    Agent("support-agent", version="1.0.0", team="eng")
    .with_model(primary="claude-sonnet-4-6", fallback="gpt-4o")
    .with_tools(["tools/zendesk-mcp", "tools/order-lookup"])
    .with_deploy(cloud="gcp", min_scale=1, max_scale=10)
)
agent.deploy()
```

---

## What's Supported

**Frameworks** — LangGraph · OpenAI Agents · Claude SDK · CrewAI · Google ADK · Custom

**Cloud targets** — AWS (ECS Fargate, App Runner, EKS) · GCP (Cloud Run, GKE) · Azure Container Apps · Kubernetes (EKS/GKE/AKS/self-hosted) · Local Docker · Claude Managed Agents

**LLM providers** — Anthropic · OpenAI · Google · Ollama (local, free) · LiteLLM · OpenRouter

**RAG & memory** — ChromaDB (vector search) · Neo4j (knowledge graph / GraphRAG) · MCP memory server

**MCP & A2A** — MCP server registry · MCP sidecar injection · Agent-to-Agent (A2A) JSON-RPC protocol · multi-level orchestration

**Platform** — RBAC · cost tracking · audit trail · org registry · MCP hub · multi-agent orchestration · RAG · evaluations · A2A protocol · AgentOps fleet dashboard · community marketplace

Full feature matrix and supported versions → [docs/features](https://www.agentbreeder.io/docs/features)

---

## CLI Reference

| Command | What it does |
|---------|-------------|
| `agentbreeder quickstart` | **Full local bootstrap** — Docker, stack, seed data, 5 sample agents, dashboard |
| `agentbreeder setup` | Configure Ollama + cloud API keys (interactive wizard) |
| `agentbreeder seed` | Seed ChromaDB and Neo4j; ingest your own docs with `--docs` |
| `agentbreeder ui` | Start the dashboard + API via Docker (lighter alternative to `quickstart`) |
| `agentbreeder up` / `down` | Start / stop the full local platform stack |
| `agentbreeder init` | Scaffold a new agent project (interactive) |
| `agentbreeder deploy` | Deploy an agent (local, AWS, GCP, Azure, K8s) |
| `agentbreeder validate` | Validate `agent.yaml` without deploying |
| `agentbreeder chat` | Chat with a deployed agent; `--local` uses Ollama directly |
| `agentbreeder logs` | Stream logs from a deployed agent |
| `agentbreeder status` | Show deploy status of all agents |
| `agentbreeder list` | List registered agents, tools, models, prompts |
| `agentbreeder search` | Search the org registry across all entity types |
| `agentbreeder describe` | Show full detail for a registered agent |
| `agentbreeder teardown` | Remove a deployed agent and its cloud resources |
| `agentbreeder eval` | Run LLM-as-judge evaluations against an agent |
| `agentbreeder eject` | Eject from Low Code to Full Code (generates SDK scaffold) |
| `agentbreeder submit` | Open a PR for an agent change (git workflow) |
| `agentbreeder review` | Review a pending agent PR |
| `agentbreeder publish` | Merge an approved agent PR |
| `agentbreeder schedule` | Create cron-based scheduled agent runs |
| `agentbreeder provider` | Manage LLM provider connections and API keys |
| `agentbreeder scan` | Auto-discover Ollama models and MCP servers on your network |
| `agentbreeder secret` | Manage secrets across backends (env, AWS, GCP, Vault) |
| `agentbreeder template` | Browse and apply agent templates from the marketplace |
| `agentbreeder orchestration` | Manage multi-agent orchestrations |
| `agentbreeder compliance` | Generate SOC 2 / HIPAA / GDPR / ISO 27001 evidence reports |
| `agentbreeder --version` | Print the installed version |

Full CLI reference → [agentbreeder.io/docs/cli-reference](https://www.agentbreeder.io/docs/cli-reference)

---

## Install

Pick the method that matches your environment:

| Method | Command |
|--------|---------|
| **pip** (Python 3.11+) | `pip3 install agentbreeder` |
| **Homebrew** (macOS) | `brew tap agentbreeder/agentbreeder && brew install agentbreeder` |
| **Docker** | `docker run rajits/agentbreeder-cli --help` |
| **npm** | `npm install @agentbreeder/sdk` |

After any install, the same commands are available:

```bash
agentbreeder quickstart       # full local platform in one command
agentbreeder setup            # configure Ollama + API keys
agentbreeder seed             # seed ChromaDB and Neo4j knowledge bases
agentbreeder deploy           # deploy an agent (local, AWS, GCP, Azure)
agentbreeder chat my-agent    # chat with a deployed agent
```

> **`agentbreeder: command not found`?** pip's script directory may not be on your PATH — [fix it here](https://www.agentbreeder.io/docs/how-to#agentbreeder-command-not-found). On macOS, Homebrew is the easiest install.

Install from source: [agentbreeder.io/docs/how-to#install-from-source →](https://www.agentbreeder.io/docs/how-to#install-from-source)

---

## Quick Start

### Option A — Full local platform (recommended for first-timers)

```bash
pip3 install agentbreeder
agentbreeder quickstart
```

That single command:
- Detects and guides Docker/Podman install if needed
- Starts the full stack: API · Dashboard · PostgreSQL · Redis · ChromaDB (RAG) · Neo4j (GraphRAG) · MCP servers · LiteLLM gateway
- Seeds a ChromaDB knowledge base and a Neo4j knowledge graph with sample data
- Deploys 5 sample agents (RAG, GraphRAG, MCP search, A2A orchestrator, assistant)
- Opens the visual dashboard at `http://localhost:3001`

Takes ~3 minutes on first run (image pulls). Then:

```bash
agentbreeder chat assistant                   # chat with the assistant agent
agentbreeder chat rag-agent                   # ask questions about AgentBreeder docs
agentbreeder chat graph-agent                 # query the knowledge graph
agentbreeder chat a2a-orchestrator            # let the orchestrator route your question
agentbreeder chat my-agent --local            # chat via Ollama — no API server needed
```

Deploy to cloud from the same setup:

```bash
agentbreeder quickstart --cloud aws           # local + deploy to AWS ECS Fargate
agentbreeder quickstart --cloud gcp           # local + deploy to GCP Cloud Run
agentbreeder quickstart --cloud azure         # local + deploy to Azure Container Apps
```

### Option B — Build your own agent

```bash
pip3 install agentbreeder
agentbreeder setup                # configure Ollama + API keys (interactive wizard)
agentbreeder init                 # scaffold a new agent project
agentbreeder validate             # validate agent.yaml
agentbreeder deploy --target local       # deploy locally with Docker
agentbreeder deploy --target aws         # deploy to AWS ECS Fargate
agentbreeder deploy --target gcp         # deploy to GCP Cloud Run
```

Full quickstart guide → [agentbreeder.io/docs/quickstart](https://www.agentbreeder.io/docs/quickstart) · [How AgentBreeder compares →](https://www.agentbreeder.io/docs/comparisons)

---

## Documentation

| | |
|---|---|
| [Quickstart](https://www.agentbreeder.io/docs/quickstart) | Full local platform in one command |
| [Examples](https://www.agentbreeder.io/docs/examples) | 18 working examples — every framework, cloud, and pattern |
| [agent.yaml reference](https://www.agentbreeder.io/docs/agent-yaml) | Every field, every option |
| [CLI reference](https://www.agentbreeder.io/docs/cli-reference) | All commands and flags |
| [How-To guides](https://www.agentbreeder.io/docs/how-to) | Install, deploy, orchestrate, evaluate |
| [RAG & GraphRAG](https://www.agentbreeder.io/docs/rag) | ChromaDB vector search + Neo4j knowledge graphs |
| [MCP servers](https://www.agentbreeder.io/docs/mcp-servers) | MCP server registry + sidecar injection |
| [A2A protocol](https://www.agentbreeder.io/docs/a2a-protocol) | Agent-to-Agent JSON-RPC communication |
| [Comparisons](https://www.agentbreeder.io/docs/comparisons) | AgentBreeder vs Google, Anthropic, OpenAI, Azure, AWS |
| [SDK reference](https://www.agentbreeder.io/docs/full-code) | Python + TypeScript full-code SDK |

---

[Contributing](CONTRIBUTING.md) · [Issues](https://github.com/agentbreeder/agentbreeder/issues) · [Discussions](https://github.com/agentbreeder/agentbreeder/discussions) · [Apache 2.0](LICENSE)
