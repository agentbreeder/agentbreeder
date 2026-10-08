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

Your company has 47 AI agents. Nobody knows who can deploy them, where they run, or which ones are still alive. Three teams built the same summarizer.

**AgentBreeder fixes this.**

Write one `agent.yaml`. Run `agentbreeder deploy`. Your agent is live — RBAC-checked and registered for org-wide discoverability. Automatic. Not optional.

---

## The Problem

AI coding tools make it easy to **build** agents. Nobody has made it easy to **ship** them responsibly.

| What happens today | What happens with AgentBreeder |
|---|---|
| Every framework has its own deploy story | One YAML, any framework, any cloud |
| No RBAC — anyone deploys anything | RBAC validated before the first container builds |
| No discoverability — duplicate agents everywhere | Org-wide registry — search before you build |
| Governance is bolted on after the fact | Governance is a **structural side effect** of deploying |

**Governance is not configuration. It is a side effect of the pipeline. There is no way to skip it.**

---

## How It Works

Eight steps run in sequence:
```
parse → RBAC check → resolve deps → build container → provision infra → deploy → health check → register
```
Any failing step stops the deploy; a failed health check tears the new deployment down.
---

## Three Ways to Build

All three tiers produce the same `agent.yaml`. Same deploy pipeline. Same governance. No lock-in.

| Tier | Who | How | Eject to |
|------|-----|-----|----------|
| **No Code** | PMs, analysts, citizen builders | Describe the agent in the Studio chat builder — it writes the `agent.yaml` | Low Code |
| **Low Code** | ML engineers, DevOps | Write `agent.yaml` in any IDE | Full Code (`agentbreeder eject`) |
| **Full Code** | Senior engineers, researchers | Python/TS SDK with full programmatic control | — |


---
## Use with Claude Code

Install the `/agent-build` architect as a Claude Code plugin:

```bash
claude plugin marketplace add agentbreeder/agentbreeder
claude plugin install agent-build@agentbreeder
```

Then run `/agent-build` in Claude Code to scaffold an agent (it recommends framework, model, memory, RAG, and deploy target, then generates `agent.yaml` + code — retrieval and eval scaffolding is plain code in your project).

---
## Documentation

**User docs** (guides, references, examples) — [agentbreeder.io/docs](https://www.agentbreeder.io/docs)

| | |
|---|---|
| [How-To guides](https://www.agentbreeder.io/docs/how-to) | Install, configure, deploy |
| [Quickstart](https://www.agentbreeder.io/docs/quickstart) | Full local platform in one command |
| [Self-hosting](https://www.agentbreeder.io/docs/self-hosting) | Run the platform on your own Kubernetes via Helm (`deploy/helm/agentbreeder`) |
| [CLI reference](https://www.agentbreeder.io/docs/cli-reference) | All commands and flags |
| [SDK reference](https://www.agentbreeder.io/docs/full-code) | Python + TypeScript full-code SDK |


**For contributors** — internal engineering references in this repo:

| | |
|---|---|
| [Contributing](CONTRIBUTING.md) | How to contribute — setup, standards, PR process |
| [Architecture](ARCHITECTURE.md) | Platform architecture — deploy pipeline, abstractions, data model |
| [Design](docs/design/) | Feature design docs — RBAC, LiteLLM gateway, polyglot agents |



[Changelog](CHANGELOG.md) · [Roadmap](ROADMAP.md) · [Issues](https://github.com/agentbreeder/agentbreeder/issues) · [Discussions](https://github.com/agentbreeder/agentbreeder/discussions) · [Discord](https://discord.gg/QT9j3Uj4s5) · [Apache 2.0](LICENSE) · [Trademark](TRADEMARK.md) · [Code of conduct](CODE_OF_CONDUCT.md) · [CLA](CLA.md) · [Security](SECURITY.md) · [Governance](GOVERNANCE.md)
