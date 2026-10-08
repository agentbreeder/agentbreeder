# Remove unfinished and simulated features

**Date:** 2026-10-08
**Status:** Implemented on `claude/elegant-allen-eiwkyu`

## Problem

Several product surfaces looked finished but were not:

- **Fake results.** Some code returned invented values: simulated prompt-test output, placeholder MCP tools, a hard-coded provider/model price catalog, a fake `sk-sp-` API key on failure, "DEMO DATA" compliance reports, quickstart "deploys" that deployed nothing, and a CLI remote deploy that reported success without deploying.
- **In-memory or never-populated storage.** Some features kept state in process-local stores that are lost on restart, or read database tables that nothing ever writes (`cost_events`, `audit_events`, `traces`/`spans`):
  - evals
  - audit/lineage
  - costs/budgets
  - tracing
  - orchestrations
  - git/PR review
- **Unwired UI.** Some UI was never connected to a working backend:
  - the deploy wizard calls a `deployer_for` that does not exist;
  - the visual builder's registry pickers are fake;
  - incident action buttons do nothing.

Docs, the README and the marketing site advertised all of these as working.

## Goal

The repo, docs and site describe only what works end to end. Every feature above was removed rather than labelled, per the maintainer's decision.

## Removed

### CLI

- `agentbreeder eval`, `submit`, `review`, `publish`, `orchestration`, `schedule` and `compliance`.
- `agentbreeder registry rag …`.
- `agentbreeder list deploys` and `list orchestrations`.
- `agentbreeder deploy --remote` and remote mode. Deploys always run in-process. `--local` is still accepted as a hidden no-op for backwards compatibility.
- `agentbreeder quickstart` no longer pretends to deploy. It registers the sample agents and prints the `agentbreeder deploy … --target local` command.
- `agentbreeder provider add` no longer fakes a connection test. It stores the provider as `configured`.
- `agentbreeder provider test` no longer simulates a connection test for providers outside the catalog; it now exits with an error.

### `agent.yaml`

- The `knowledge_bases` and `subagents` fields.
- The `a2a` tool type.

The JSON Schema now rejects these fields.

### API

| Area | Removed |
|---|---|
| `/api/v1/evals`, `/audit`, `/costs` (budgets), `/tracing`, `/orchestrations`, `/git` (branches, PRs, review), `/rag`, `/deploys`, `/compliance` | whole routers |
| `/api/v1/prompts/{id}/test` | simulated LLM output |
| `/api/v1/deployments` | `POST /`, `GET /{job_id}`, `/stream` and `/destroy-partial` (deploy-job pipeline). `cloud-requirements` and `validate-infra` remain. |
| `/api/v1/builder/sessions/{id}/deploy` | dashboard deploy |
| `/api/v1/providers` | `/health-check`, `/detect-ollama`, `/{id}/test` and `/{id}/discover` |
| `/api/v1/gateway/costs/comparison` | static price table |
| `/api/v1/agentops` | `/events`, `/teams`, `/costs`, `/top-agents` and incident actions |
| `/api/v1/playground/eval-case` | eval store |

### Dashboard

- **Removed pages:**
  - Deploys and Deploy Wizard
  - RAG Builder
  - Approvals/PR review
  - Traces, Costs, Budgets, Audit, Lineage and Activity
  - all Eval pages
  - Orchestrations and the Orchestration Builder
  - the ReactFlow visual agent builder
- **Removed controls:** the dashboard Deploy button.
- **No-code tier:** now the chat builder at `/agents/new`. It produces `agent.yaml`; deploy goes through the CLI.

### SDKs

- **Python:** `Agent.deploy()`, `with_subagent`, knowledge bases, the orchestration classes and the RAG clients.
- **TypeScript:** `deploy`, `withSubagent`, knowledge bases and orchestration.

### Engine and sidecar

- The orchestration engine (`orchestration.yaml`).
- The A2A JSON-RPC server inside agents.
- RAG/GraphRAG/pgvector knowledge-base injection.
- The sidecar `/cost` endpoint and cost emission.
- The sidecar TS client methods for rag/cost/trace/a2a/config.

## Kept (works end to end)

- **Deploy:** `agentbreeder deploy` runs parse → RBAC → resolve → build → provision → deploy → health check → register. A failed health check tears down the new deployment.
- **Registry** for agents, prompts, tools, MCP servers, models and providers, plus model lifecycle sync.
- **A2A:** the registry and invoke API (`/api/v1/a2a/agents`, `/api/v1/a2a/invoke`) and the sidecar A2A forwarder.
- **Memory:** Postgres and Redis backends, with managed provisioning when `memory` is declared without `backend_url`.
- **Platform services:**
  - secrets backends
  - RBAC, teams and API keys
  - the LiteLLM gateway (status, models, providers, spend, logs, teams — read live from LiteLLM)
  - playground chat
  - the tool sandbox
- **HITL approvals queue** (`/api/v1/approvals`, Redis-backed), where agents pause for human sign-off on a tool call. It is not a deploy approval gate.
- **AgentOps:** fleet and heatmap, incident CRUD, and compliance scan/status/report.
- **Other surfaces:** marketplace, templates, `eject`, and the chat builder.
- **Observability hooks:**
  - Audit events are structured `audit_event` log lines. There is no audit API or UI.
  - Agents can export OpenTelemetry spans to an OTLP collector. There is no platform trace store or viewer.

## Deliberately untouched

- **Database tables and Alembic migrations** for the removed features. Dropping tables is a separate, reversible-by-migration change.
- **Dated blog posts**, which are historical.
- **The Cloud marketing page**, which describes the separate managed product.
- **Design docs** under `docs/superpowers/`.

## Acceptance criteria

- No route, command, page, SDK method or schema field listed above remains.
- The Python unit and integration suites pass, except `test_builder_extended::test_deploy_fails_on_registration`, which also fails on `main`.
- The dashboard passes `tsc`, eslint and vitest, and Playwright has no new failures compared with `main`.
- The Go sidecar passes `go build`, `go vet` and `go test`. The TS SDK passes `tsc` and its tests.
- The README, `docs/` and website docs no longer advertise removed features.

## Cross-repo impact

`agentbreeder-cloud` must stop calling the removed API routes:

- `/deploys`, `/evals`, `/costs`, `/audit`, `/tracing`, `/orchestrations`, `/git` and `/rag`
- the `/deployments` job endpoints
- builder-session deploy

It must also stop accepting `knowledge_bases` and `subagents` in `agent.yaml`. That needs a companion PR.
