# AgentBreeder

**Define Once. Deploy Anywhere. Govern Automatically.**

AgentBreeder is an open-source platform for building, deploying, and governing enterprise AI agents. Write one `agent.yaml`, run `agentbreeder deploy`, and your agent is live on AWS or GCP — with RBAC, cost tracking, audit trail, and org-wide discoverability automatic.

---

## Why AgentBreeder?

| Challenge | Without AgentBreeder | With AgentBreeder |
|-----------|---------------------|------------------|
| Framework fragmentation | Each team uses a different framework | One deploy pipeline, any framework |
| Cloud sprawl | Agents manually deployed to ad-hoc infra | One command, any cloud |
| Governance gaps | No audit trail, no RBAC, no cost tracking | Governance is a side effect of deploying |
| Discoverability | Agents exist in silos | Shared org-wide registry |
| Builder diversity | Engineers only | No Code → Low Code → Full Code |

---

## Three Builder Tiers

AgentBreeder supports three ways to build agents and orchestrations. All three compile to the same internal format and share the same deploy pipeline.

=== "No Code (Visual UI)"

    Drag-and-drop agent builder. Pick model, tools, prompt, guardrails from the registry. Define multi-agent routing on a ReactFlow canvas.

    **Who:** PMs, analysts, citizen builders.

=== "Low Code (YAML)"

    Write `agent.yaml` or `orchestration.yaml` in any IDE. Schema-aware, human-readable, version-controlled.

    ```yaml
    name: customer-support-agent
    version: 1.0.0
    team: customer-success
    framework: langgraph
    model:
      primary: claude-sonnet-4
    deploy:
      cloud: local          # or: gcp
    ```

    **Who:** ML engineers, DevOps, developers comfortable with config files.

=== "Full Code (Python/TS SDK)"

    Full programmatic control with custom routing, state machines, and dynamic agent spawning.

    ```python
    from agenthub import Pipeline

    research = (
        Pipeline("research-pipeline", team="eng")
        .step("researcher", ref="agents/researcher")
        .step("summarizer", ref="agents/summarizer")
        .step("reviewer",   ref="agents/reviewer")
    )
    research.deploy()
    ```

    **Who:** Senior engineers, researchers, teams that have outgrown YAML.

---

## Key Features

- **Framework-agnostic** — LangGraph, OpenAI Agents, CrewAI, Claude SDK, Google ADK, and Custom frameworks supported
- **Multi-cloud** — GCP Cloud Run and local Docker Compose implemented; AWS ECS and Kubernetes planned
- **Governance as a side effect** — RBAC, cost attribution, audit trail, and registry registration happen automatically on every `agentbreeder deploy`
- **Shared org registry** — agents, prompts, tools/MCP servers, models, knowledge bases all in one place
- **Tier mobility** — start No Code, eject to YAML, eject to SDK — no lock-in at any level
- **Multi-agent orchestration** — 6 strategies (router, sequential, parallel, supervisor, hierarchical, fan-out/fan-in) via YAML or SDK

---

## Quick Start

**Python (CLI + full platform):**

```bash
pip install agentbreeder
agentbreeder init
agentbreeder validate
agentbreeder deploy --target local
agentbreeder chat my-agent
```

**TypeScript / JavaScript (SDK only):**

```bash
npm install @agentbreeder/sdk
```

```typescript
import { Agent } from "@agentbreeder/sdk";

const agent = new Agent("customer-support", { version: "1.0.0", team: "eng" })
  .withModel({ primary: "claude-sonnet-4", fallback: "gpt-4o" })
  .withTool({ ref: "tools/zendesk-mcp" })
  .withDeploy({ cloud: "aws", region: "us-east-1" });

agent.toYaml("agent.yaml");
```

See the [Quickstart guide](quickstart.md) for the full setup.

---

## Supported Stack

| Layer | Implemented | Planned |
|-------|-------------|---------|
| Frameworks | LangGraph, OpenAI Agents, CrewAI, Claude SDK, Google ADK, Custom | — |
| Cloud targets | GCP Cloud Run, Local Docker Compose | AWS ECS Fargate, Kubernetes |
| LLM providers | Anthropic, OpenAI, Google, Ollama, LiteLLM, OpenRouter | — |
| Secrets backends | env, AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault | — |
| SDKs | Python (`agentbreeder-sdk`), TypeScript (`@agentbreeder/sdk`) | — |
| Auth | JWT + OAuth2, RBAC | SSO / SAML |
| Observability | OpenTelemetry, distributed tracing, cost monitoring | — |

---

## Documentation

| Section | Description |
|---------|-------------|
| [Quickstart](quickstart.md) | Get running in under 10 minutes |
| [How-To Guide](how-to.md) | 20+ practical recipes for common workflows |
| [Registry Guide](registry-guide.md) | Create, edit, and register prompts, tools, RAG, memory, and agents |
| [CLI Reference](cli-reference.md) | All `agentbreeder` commands |
| [agent.yaml](agent-yaml.md) | Full agent configuration reference |
| [orchestration.yaml](orchestration-yaml.md) | Multi-agent pipeline configuration |
| [Orchestration SDK](orchestration-sdk.md) | Python/TypeScript SDK for complex workflows |
| [TypeScript SDK](https://www.npmjs.com/package/@agentbreeder/sdk) | `npm install @agentbreeder/sdk` — JS/TS agent definitions |
| [Migration Guides](migrations/OVERVIEW.md) | Migrate from LangGraph, CrewAI, OpenAI Agents, AutoGen |
| [API Stability](api-stability.md) | API versioning and deprecation policy |
