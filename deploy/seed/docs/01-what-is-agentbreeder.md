# What is AgentBreeder?

AgentBreeder is an open-source platform for building, deploying, and governing enterprise AI agents.

**Core tagline:** Define Once. Deploy Anywhere. Govern Automatically.

## The one-sentence pitch

A developer writes one `agent.yaml` file, runs `agentbreeder deploy`, and their agent is live on AWS or GCP — with RBAC and org-wide discoverability automatic and zero extra work.

## What makes it unique

- **Framework-agnostic** — LangGraph, CrewAI, Claude SDK, OpenAI Agents, Google ADK, Custom
- **Multi-cloud first** — AWS ECS Fargate, GCP Cloud Run, Azure Container Apps, Kubernetes, local Docker
- **Governance is a side effect** — not extra configuration
- **Shared org-wide registry** — agents, prompts, tools, MCP servers, models
- **Three builder tiers** — No Code (chat builder in the dashboard), Low Code (YAML), Full Code (Python/TS SDK)
- **Tier mobility** — start No Code, eject to YAML, eject to Full Code — no vendor lock-in

## Who is it for?

| Role | How they use AgentBreeder |
|------|--------------------------|
| ML Engineer | Write agent.yaml, `agentbreeder deploy`, done |
| DevOps | Manage providers, secrets, cloud targets via CLI |
| PM / Analyst | Describe an agent to the dashboard chat builder and get an agent.yaml |
| Security team | RBAC on every deploy; audit events as structured logs |
| Platform team | Self-host the registry, govern all org agents |

## Key concepts

- **agent.yaml** — the single config file that defines an agent (model, tools, prompts, deployment target)
- **Registry** — org-wide catalog of agents, tools, prompts, models, MCP servers
- **Deploy pipeline** — Parse → RBAC check → Dependency resolution → Build → Deploy → Health check → Register
- **Governance** — RBAC checks and registry registration happen automatically on every deploy
