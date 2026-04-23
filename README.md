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
  primary: claude-sonnet-4
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
    .with_model(primary="claude-sonnet-4", fallback="gpt-4o")
    .with_tools(["tools/zendesk-mcp", "tools/order-lookup"])
    .with_deploy(cloud="gcp", min_scale=1, max_scale=10)
)
agent.deploy()
```

---

## What's Supported

**Frameworks** — LangGraph · OpenAI Agents · Claude SDK · CrewAI · Google ADK · Custom

**Cloud targets** — AWS (ECS Fargate, App Runner) · GCP Cloud Run · Azure Container Apps · Kubernetes · Local Docker · Claude Managed Agents

**LLM providers** — Anthropic · OpenAI · Google · Ollama · LiteLLM · OpenRouter

**Platform** — RBAC · cost tracking · audit trail · org registry · MCP hub · multi-agent orchestration · RAG · evaluations · A2A protocol · AgentOps fleet dashboard · community marketplace

Full feature matrix and supported versions → [docs/features](https://www.agentbreeder.io/docs/features)

---

## Install

```bash
pip3 install agentbreeder     # Python 3.11+ required
```

Other methods: [Homebrew · Docker · npm · from source →](https://www.agentbreeder.io/docs/how-to#install-agentbreeder)

> **`agentbreeder: command not found`?** pip's script directory may not be on your PATH — [fix it here](https://www.agentbreeder.io/docs/how-to#agentbreeder-command-not-found). On macOS, Homebrew is the easiest install.

---

## Quick Start

```bash
pip3 install agentbreeder

agentbreeder init             # scaffold a new agent (interactive wizard)
agentbreeder validate         # validate agent.yaml before deploying
agentbreeder deploy --target local   # deploy locally with Docker
```

Full quickstart guide → [agentbreeder.io/docs/quickstart](https://www.agentbreeder.io/docs/quickstart) · [How AgentBreeder compares →](https://www.agentbreeder.io/docs/comparisons)

---

## Documentation

| | |
|---|---|
| [Quickstart](https://www.agentbreeder.io/docs/quickstart) | Deploy your first agent in 5 minutes |
| [agent.yaml reference](https://www.agentbreeder.io/docs/agent-yaml) | Every field, every option |
| [CLI reference](https://www.agentbreeder.io/docs/cli) | All 24 commands |
| [How-To guides](https://www.agentbreeder.io/docs/how-to) | Install, deploy, orchestrate, evaluate |
| [SDK reference](https://www.agentbreeder.io/docs/sdk) | Python + TypeScript |

---

[Contributing](CONTRIBUTING.md) · [Issues](https://github.com/agentbreeder/agentbreeder/issues) · [Discussions](https://github.com/agentbreeder/agentbreeder/discussions) · [Apache 2.0](LICENSE)
