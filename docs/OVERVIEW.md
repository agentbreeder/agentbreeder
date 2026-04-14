# AgentBreeder — Executive Overview

> **Define Once. Deploy Anywhere. Govern Automatically.**

---

## What Is AgentBreeder?

AgentBreeder is an open-source enterprise platform for building, deploying, and governing AI agents across every major framework and cloud provider.

A developer writes a single `agent.yaml` configuration file, runs `agentbreeder deploy`, and their agent is live — on AWS, GCP, or locally — with automatic access controls, cost tracking, audit logging, and org-wide discoverability. No extra configuration. No governance bolted on later.

---

## Why It's Needed

Modern organizations are accumulating AI agent chaos faster than they can manage it:

- **Framework sprawl** — teams independently adopt LangGraph, CrewAI, OpenAI Agents, Claude SDK, and Google ADK. Each has its own deployment story, its own ops burden.
- **Invisible duplication** — team A builds a web-search tool; team B builds the same one six months later. Nobody knows it already existed.
- **Untracked spend** — LLM costs accumulate across dozens of teams with zero attribution or budget accountability.
- **Governance as an afterthought** — RBAC, audit trails, and compliance are added post-hoc, poorly, or not at all.
- **Skill-gap bottlenecks** — PMs want to prototype agents but can't code; engineers are gatekeeping what should be self-service.

AgentBreeder collapses all of this into a single, opinionated platform where governance is never optional — it's a structural side effect of deploying.

---

## How It Works

Every agent — regardless of how it was built — runs through a mandatory **8-step atomic deploy pipeline**:

```
1. Parse & validate the agent config
2. RBAC check (fail fast if unauthorized)
3. Dependency resolution (fetch tools, prompts, models from the org registry)
4. Container build (framework-specific, automatic)
5. Infrastructure provision (Pulumi-managed, cloud-agnostic)
6. Deploy + health check
7. Auto-register in the org registry (name, endpoint, owner, team, cost attribution)
8. Return endpoint URL
```

If any step fails, the entire deploy rolls back. There is no "quick deploy" mode that bypasses governance.

### Three Ways to Build — One Pipeline

| Tier | Who It's For | How |
|---|---|---|
| **No Code** | PMs, analysts, business users | Visual drag-and-drop UI; generates `agent.yaml` |
| **Low Code** | ML engineers, DevOps | Write `agent.yaml` directly in any editor |
| **Full Code** | Senior engineers, researchers | Python/TypeScript SDK with builder pattern |

All three converge to the same internal format and run through the same pipeline. Tier mobility is built in: export YAML from the UI, or eject from YAML to full code — no lock-in at any level.

---

## How It Helps an Organization

| Challenge | What AgentBreeder Provides |
|---|---|
| Multiple teams, multiple frameworks | One platform, one deploy workflow, full framework compatibility |
| No visibility into what agents exist | Org-wide registry: searchable catalog of every agent, tool, model, and prompt |
| No cost visibility | Every LLM call attributed to a team, a deploy, and an owner |
| Compliance and audit requirements | Immutable audit log on every deploy and invocation |
| Risky agent proliferation | RBAC gates every deploy to authorized users and teams |
| Need to coordinate between agents | Built-in orchestration: supervisor, sequential, parallel, fan-out patterns |

### The Governance Guarantee

When any agent is deployed through AgentBreeder:

- The deploying user's **permissions are verified** against team RBAC
- The agent's **dependencies are resolved** from the registry — no phantom tool references
- An **immutable audit record** is created (who deployed what, when, with what config)
- The agent is **auto-registered** in the org catalog with cost attribution configured
- A **dependency lineage graph** is maintained (agent → tools, prompts, models)

This happens automatically on every deploy. Engineering teams do not need to implement it themselves.

---

## The Merger & Acquisition Scenario

A merger creates an immediate, high-stakes AI governance crisis. Two organizations typically arrive with:

- **Different frameworks** (one on LangGraph, the other on CrewAI)
- **Duplicate agents and tools** (two customer-support bots, two data analysis tools, two internal knowledge search agents)
- **Incompatible infrastructure** (one org on AWS, the other on GCP)
- **No shared cost baseline** — finance cannot reconcile AI spend across the combined entity
- **Unknown risk surface** — nobody has a complete list of what agents are running, on what data, with what model access

Resolving this manually — inventorying two sprawling agent estates, stitching together governance by hand, renegotiating model contracts — typically takes months and introduces significant compliance risk during the integration period.

**AgentBreeder resolves this systematically:**

1. **Ingest both catalogs** — connectors scan existing deployments and populate a unified registry. Leadership has a complete picture of both orgs' AI assets within hours.
2. **Identify duplication** — the registry surfaces identical or overlapping agents and tools. Engineering leads can retire redundant assets with confidence.
3. **Remap governance** — RBAC is remapped to the new org structure. Access controls are enforced from day one of the merged entity, not six months later.
4. **Abstract infrastructure differences** — agents from either cloud are redeployed to a unified target without rewriting application code. Framework differences are irrelevant.
5. **Unify cost tracking** — finance gets a single dashboard with LLM spend attributed by team across both legacy organizations, supporting immediate budget consolidation.

The result: a governed, deduplicated, cost-visible AI estate from the first week of integration — not the first quarter.

---

## Technology

| Layer | Stack |
|---|---|
| **Backend** | Python 3.11+, FastAPI, PostgreSQL, Redis |
| **Frontend** | React 18, TypeScript, Tailwind CSS |
| **Infrastructure** | Docker, Pulumi |
| **Supported Frameworks** | LangGraph, OpenAI Agents SDK (CrewAI, Claude SDK, Google ADK planned) |
| **Cloud Targets** | GCP Cloud Run, local Docker Compose (AWS ECS, Kubernetes planned) |
| **LLM Providers** | OpenAI, Anthropic, Google, Ollama, OpenRouter, LiteLLM |
| **Observability** | OpenTelemetry, distributed tracing, cost monitoring |

---

## Further Reading

- [Quickstart Guide](./quickstart.md) — up and running in 10 minutes
- [Architecture](https://github.com/rajitsaha/agentbreeder/blob/main/ARCHITECTURE.md) — deep-dive into the deploy pipeline and system design
- [agent.yaml Reference](./agent-yaml.md) — complete configuration specification
- [CLI Reference](./cli-reference.md) — all commands documented
- [Migration Guides](./migrations/OVERVIEW.md) — move from LangGraph, CrewAI, OpenAI Agents, AutoGen, or custom Python
- [Roadmap](https://github.com/rajitsaha/agentbreeder/blob/main/ROADMAP.md) — 30-milestone release plan
