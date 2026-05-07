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
Full CLI reference → [agentbreeder.io/docs/cli-reference](https://www.agentbreeder.io/docs/cli-reference)

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


[Contributing](CONTRIBUTING.md) · [Issues](https://github.com/agentbreeder/agentbreeder/issues) · [Discussions](https://github.com/agentbreeder/agentbreeder/discussions) · [Discord](https://discord.gg/QT9j3Uj4s5) · [Apache 2.0](LICENSE) · [Trademark](TRADEMARK.md)
