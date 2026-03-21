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
      cloud: aws
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

- **Framework-agnostic** — LangGraph, CrewAI, Claude SDK, OpenAI Agents, Google ADK, Custom
- **Multi-cloud first** — AWS ECS/Fargate/EKS and GCP Cloud Run as equal first-class targets
- **Governance as a side effect** — RBAC, cost attribution, audit trail, and registry registration happen automatically on every `agentbreeder deploy`
- **Shared org registry** — agents, prompts, tools/MCP servers, models, knowledge bases all in one place
- **Tier mobility** — start No Code, eject to YAML, eject to SDK — no lock-in at any level
- **Multi-agent orchestration** — 6 strategies (router, sequential, parallel, supervisor, hierarchical, fan-out/fan-in) via YAML or SDK

---

## Quick Start

```bash
# Install
pip install agentbreeder

# Create an agent
agentbreeder init

# Validate the config
agentbreeder validate

# Deploy locally
agentbreeder deploy --target local

# Chat with your agent
agentbreeder chat my-agent
```

See the [Quickstart guide](quickstart.md) for the full setup.

---

## Supported Stack

| Layer | Options |
|-------|---------|
| Frameworks | LangGraph, CrewAI, Claude SDK, OpenAI Agents, Google ADK, Custom |
| Cloud targets | AWS ECS Fargate, GCP Cloud Run, Kubernetes, Local Docker |
| LLM providers | Anthropic, OpenAI, Google, Ollama, LiteLLM, OpenRouter |
| Shared state backends | Redis, PostgreSQL, In-Memory |
| Auth | JWT + OAuth2, RBAC |
| Observability | OpenTelemetry, distributed tracing, cost monitoring |

---

## Documentation

| Section | Description |
|---------|-------------|
| [Quickstart](quickstart.md) | Get running in under 10 minutes |
| [CLI Reference](cli-reference.md) | All `agentbreeder` commands |
| [agent.yaml](agent-yaml.md) | Full agent configuration reference |
| [orchestration.yaml](orchestration-yaml.md) | Multi-agent pipeline configuration |
| [Orchestration SDK](orchestration-sdk.md) | Python/TypeScript SDK for complex workflows |
| [API Stability](api-stability.md) | API versioning and deprecation policy |
