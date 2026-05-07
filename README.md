<div align="center">

# AgentBreeder™ — v2.0

### The only agent platform that doesn't pick a winner.

**Build with anyone's framework. Deploy to anyone's cloud. Govern automatically.**
One YAML, one command — Apache 2.0, no vendor lock-in.

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

---

## What's new in v2.0

> v2 turns AgentBreeder from a CLI + engine into a full **platform substrate**. Six tracks ship together; the deploy pipeline contract is unchanged.

| Track | Ships | Docs |
|---|---|---|
| **F — 9-provider catalog** | Generic OpenAI-compatible provider + presets for Nvidia · OpenRouter · Moonshot/Kimi · Groq · Together · Fireworks · DeepInfra · Cerebras · Hyperbolic. New `agentbreeder provider list/add/test/publish`. | [providers](https://www.agentbreeder.io/docs/providers) |
| **G — Model lifecycle** | Auto-discover models from each provider's `/models` endpoint; daily diff; status badges (`active`/`deprecated`/`retired`); `agentbreeder model sync`. | [providers](https://www.agentbreeder.io/docs/providers) |
| **H — Gateways as first-class** | LiteLLM + OpenRouter promoted into the catalog; `<gateway>/<provider>/<model>` syntax; workspace-level gateway config. | [gateways](https://www.agentbreeder.io/docs/gateways) |
| **I — Polyglot SDKs** | Stable HTTP runtime contract v1; `language:` field in `agent.yaml`; thin SDK targets for Go, Kotlin, Rust, .NET. | [runtime contract](https://www.agentbreeder.io/docs/runtime-contract) · [polyglot agents](https://www.agentbreeder.io/docs/polyglot-agents) |
| **J — Sidecar** | Single Go binary auto-injected next to every agent; handles tracing, cost attribution, guardrails, A2A, MCP, bearer-token auth. | [sidecar](https://www.agentbreeder.io/docs/sidecar) |
| **K — Workspace secrets** | OS keychain default; `agentbreeder secret set/list/rotate/sync`; auto-mirror to AWS Secrets Manager / GCP Secret Manager at deploy. | [secrets](https://www.agentbreeder.io/docs/secrets) |

Backward-compatible: every v1 `agent.yaml` continues to work unchanged. v1 providers (openai/anthropic/google/ollama) keep their hand-written classes — the catalog is purely additive.

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

Eight atomic steps run in sequence: parse → RBAC check → *(approval gate if required)* → resolve deps → build container → provision infra → deploy → health check → register. If any step fails, the entire deploy rolls back.

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

**Agent languages** — Python · TypeScript/Node.js · Go · Kotlin/Java · Rust · .NET *(via runtime contract v1, Track I)*

**Python frameworks** — LangGraph · OpenAI Agents · Claude SDK · CrewAI · Google ADK · Custom

**TypeScript frameworks** — Vercel AI SDK · Mastra · LangChain.js · OpenAI Agents TS · DeepAgent · Custom

**Cloud targets** — AWS (ECS Fargate, App Runner, EKS) · GCP (Cloud Run, GKE) · Azure Container Apps · Kubernetes (EKS/GKE/AKS/self-hosted) · Local Docker · Claude Managed Agents

**LLM providers — direct** — Anthropic · OpenAI · Google · Ollama (local, free)

**LLM providers — OpenAI-compatible catalog (v2)** — Nvidia NIM · Moonshot/Kimi · Groq · Together · Fireworks · DeepInfra · Cerebras · Hyperbolic *(plus your own user-local entries)*

**LLM gateways** — LiteLLM (self-hosted proxy) · OpenRouter (200+ models) — see [`gateways`](https://www.agentbreeder.io/docs/gateways)

**Secrets backends** — OS keychain *(default)* · `.env` · AWS Secrets Manager · GCP Secret Manager · HashiCorp Vault — auto-mirrored to the cloud at deploy

**RAG & memory** — ChromaDB (vector search) · Neo4j (knowledge graph / GraphRAG) · MCP memory server

**MCP & A2A** — MCP server registry · MCP sidecar injection · Agent-to-Agent (A2A) JSON-RPC protocol · multi-level orchestration

**Platform** — RBAC · cost tracking · audit trail · org registry · MCP hub · multi-agent orchestration · RAG · evaluations · A2A protocol · AgentOps fleet dashboard · community marketplace · v2 platform sidecar

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
| `agentbreeder provider` | List/add/test/publish LLM providers — including the v2 OpenAI-compatible catalog (Nvidia, Groq, Together, …) |
| `agentbreeder scan` | Auto-discover Ollama models and MCP servers on your network |
| `agentbreeder secret` | Workspace-bound secrets (keychain default) with auto-mirror to AWS / GCP / Vault at deploy |
| `agentbreeder template` | Browse and apply agent templates from the marketplace |
| `agentbreeder orchestration` | Manage multi-agent orchestrations |
| `agentbreeder compliance` | Generate SOC 2 / HIPAA / GDPR / ISO 27001 evidence reports |
| `agentbreeder registry prompt push|list|try` | Push, list, and **render** prompts via a real LLM |
| `agentbreeder registry tool push|list|run` | Push (auto-detects Python vs TS), list, and **execute** tools |
| `agentbreeder registry agent push|list|invoke` | Push, list, and **chat** with deployed agents |
| `agentbreeder --version` | Print the installed version |

Full CLI reference → [agentbreeder.io/docs/cli-reference](https://www.agentbreeder.io/docs/cli-reference)

---

## Install

Requires Python 3.11+:

```bash
pip3 install agentbreeder
```

> `brew install` and `npx` support are coming soon.

After install, the same commands are available:

```bash
agentbreeder quickstart       # full local platform in one command
agentbreeder setup            # configure Ollama + API keys
agentbreeder seed             # seed ChromaDB and Neo4j knowledge bases
agentbreeder deploy           # deploy an agent (local, AWS, GCP, Azure)
agentbreeder chat my-agent    # chat with a deployed agent
```

> **`agentbreeder: command not found`?** pip's script directory may not be on your PATH — [fix it here](https://www.agentbreeder.io/docs/how-to#agentbreeder-command-not-found).

> **Working from a clone?** Use `agentbreeder quickstart --dev` to build the API + Dashboard images from your local source instead of pulling `rajits/agentbreeder-{api,dashboard}:latest` from Docker Hub. This is the way to test in-flight changes or work around a stale cached image.

---

## Quick Start

### Option A — Full local platform (recommended for first-timers)

```bash
pip3 install agentbreeder
agentbreeder quickstart
```

After it boots, every prompt, tool, and agent lives in the registry — accessible
from CLI, the API, or the dashboard:

```bash
# Login + export the JWT (CLI commands need it)
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@agentbreeder.local","password":"…"}' \
  | jq -r '.data.access_token')
export AGENTBREEDER_API_TOKEN=$TOKEN

# Browse and execute
agentbreeder registry prompt list
agentbreeder registry prompt try gemini-assistant-system --input "Greet me"

agentbreeder registry tool list
agentbreeder registry tool run web-search --args '{"query":"What is RAG?"}'

agentbreeder registry agent list
agentbreeder registry agent invoke gemini-assistant \
  --input "What time is it?" \
  --endpoint http://localhost:8080 --token $AGENT_AUTH_TOKEN
```

The dashboard at **http://localhost:3001** has the same affordances under
`/prompts`, `/tools`, and `/agents` — including a **Try it** tab on every tool,
a **Test** tab on every prompt that calls a real LLM, and an **Invoke** tab on
every agent that chats with the deployed runtime.



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

# v2: pick a provider from the catalog and stash the key in your workspace backend
agentbreeder provider list                # see all 9 OpenAI-compatible presets + legacy providers
agentbreeder secret set NVIDIA_API_KEY    # prompted securely; stored in OS keychain by default

agentbreeder init                         # scaffold a new agent project
agentbreeder validate                     # validate agent.yaml
agentbreeder deploy --target local        # deploy locally with Docker
agentbreeder deploy --target aws          # deploy to AWS ECS Fargate (secrets auto-mirrored)
agentbreeder deploy --target gcp          # deploy to GCP Cloud Run   (secrets auto-mirrored)
```

Full quickstart guide → [agentbreeder.io/docs/quickstart](https://www.agentbreeder.io/docs/quickstart) · [How AgentBreeder compares →](https://www.agentbreeder.io/docs/comparisons)

---

## Viewing deployed agents

After deploying, start the UI stack to see your agents in the dashboard (requires Docker):

```bash
agentbreeder ui
```

Then open **http://localhost:3001** and log in (default: `admin@agentbreeder.local` / `plant`). Deployed agents appear automatically in the **Agents** tab.

> **Docker networking note:** Agent containers reach the API at `http://host.docker.internal:8000` (macOS/Windows with Docker Desktop) or `http://172.17.0.1:8000` (Linux). Use `localhost:8000` only from your host terminal.

---

## Deploying to production

The reference `microlearning-ebook-agent` is **deployed and serving** at
`https://microlearning-ebook-agent-sizukgalta-uc.a.run.app`. The deploy script
at `microlearning-ebook-agent/scripts/deploy_gcp.sh` automates the full flow:

1. Enable GCP APIs (Cloud Run, Artifact Registry, Cloud Build, Secret Manager)
2. Create the image repository
3. Push secrets (`GOOGLE_API_KEY`, `TAVILY_API_KEY`, `AGENT_AUTH_TOKEN`) to Secret Manager
4. Build + push the container via Cloud Build (~3 min)
5. Deploy to Cloud Run with min=0 (scale-to-zero), max=5

```bash
cd microlearning-ebook-agent
bash scripts/deploy_gcp.sh
# → Deployed: https://<service>-<hash>-uc.a.run.app
```

Auth, config, and verification details — [agentbreeder.io/docs/deployment](https://www.agentbreeder.io/docs/deployment)

The same pattern (`agentbreeder deploy`) works for AWS ECS Fargate, App Runner, Azure Container Apps, and Kubernetes — set `deploy.cloud:` in `agent.yaml`.

---

## Documentation

**User docs** (guides, references, examples) — [agentbreeder.io/docs](https://www.agentbreeder.io/docs)

| | |
|---|---|
| [Quickstart](https://www.agentbreeder.io/docs/quickstart) | Full local platform in one command |
| [Examples](https://www.agentbreeder.io/docs/examples) | 18 working examples — every framework, cloud, and pattern |
| [agent.yaml reference](https://www.agentbreeder.io/docs/agent-yaml) | Every field, every option |
| [CLI reference](https://www.agentbreeder.io/docs/cli-reference) | All commands and flags |
| [How-To guides](https://www.agentbreeder.io/docs/how-to) | Install, deploy, orchestrate, evaluate |
| [Model Gateway](https://www.agentbreeder.io/docs/gateway) | LiteLLM proxy — routing, budgets, guardrails, caching |
| [RAG & GraphRAG](https://www.agentbreeder.io/docs/rag) | ChromaDB vector search + Neo4j knowledge graphs |
| [MCP servers](https://www.agentbreeder.io/docs/mcp-servers) | MCP server registry + sidecar injection |
| [A2A protocol](https://www.agentbreeder.io/docs/a2a-protocol) | Agent-to-Agent JSON-RPC communication |
| [Comparisons](https://www.agentbreeder.io/docs/comparisons) | AgentBreeder vs Google, Anthropic, OpenAI, Azure, AWS |
| [SDK reference](https://www.agentbreeder.io/docs/full-code) | Python + TypeScript full-code SDK |

**For contributors** — internal engineering references in this repo:

| | |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Platform architecture — deploy pipeline, abstractions, data model |
| [docs/design/](docs/design/) | Feature design docs — RBAC, LiteLLM gateway, polyglot agents |
| [ROADMAP.md](ROADMAP.md) | Release plan and milestone status |
| [CHANGELOG.md](CHANGELOG.md) | Version history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute — setup, standards, PR process |
| [GOVERNANCE.md](GOVERNANCE.md) | Project governance, decision-making, maintainer ladder |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community standards (Contributor Covenant 2.1) |
| [CLA.md](CLA.md) | Contributor License Agreement (Apache ICLA / CCLA) |
| [TRADEMARK.md](TRADEMARK.md) | AgentBreeder™ trademark policy and permitted uses |
| [SECURITY.md](SECURITY.md) | Security policy and vulnerability reporting |

---

## Want us to run it?

AgentBreeder is fully self-hostable — that's the point. But operating it in
production means running Postgres, ClickHouse, Redis, the control plane,
sidecars, autoscaling, and on-call across five clouds.

If you'd rather skip that, **[AgentBreeder Cloud](https://www.agentbreeder.io/cloud)**
is the same Apache 2.0 platform, run by us, with multi-tenant SSO, cross-cloud
cost rollups, compliance attestations, and a 99.95% SLA.

Either way, the runtime is the same code. Switch at any time. **No data
lock-in. No feature lock-out.**

[Try Cloud →](https://www.agentbreeder.io/cloud) &nbsp;·&nbsp; [Self-host docs →](https://www.agentbreeder.io/docs/quickstart) &nbsp;·&nbsp; [Join Discord →](https://discord.gg/QT9j3Uj4s5)

---

[Contributing](CONTRIBUTING.md) · [Issues](https://github.com/agentbreeder/agentbreeder/issues) · [Discussions](https://github.com/agentbreeder/agentbreeder/discussions) · [Discord](https://discord.gg/QT9j3Uj4s5) · [Apache 2.0](LICENSE) · [Trademark](TRADEMARK.md)
