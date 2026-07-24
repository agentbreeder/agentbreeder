> Extracted from CLAUDE.md (thin-router refactor). Read on demand — see CLAUDE.md router for when.

## 🛠️ Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend API | Python 3.11+, FastAPI | Async, OpenAPI auto-docs |
| Database | PostgreSQL + SQLAlchemy | Alembic for migrations |
| Cache / Queue | Redis | Task queue + rate limiting |
| CLI | Python, Typer | Rich for terminal output |
| Python SDK | Python 3.11+ | pip install agentbreeder-sdk |
| TypeScript SDK | TypeScript 5.0+ | npm install @agentbreeder/sdk |
| Frontend | React 18, TypeScript, Tailwind CSS | Vite build tool |
| Container Build | Docker | BuildKit for multi-platform |
| IaC | Pulumi (Python) | Cloud resource provisioning |
| Observability | OpenTelemetry | Traces, metrics, logs |
| Auth | JWT + OAuth2 | RBAC built on top |
| Testing | pytest (Python), Vitest (TS), Playwright (E2E) | |

---

## 🏗️ Architecture Principles

### 1. The Deploy Pipeline (Sacred — Do Not Break)
The core deploy flow must always execute in this exact order:
```
Parse & Validate YAML
    → RBAC Check (fail fast if unauthorized)
    → Dependency Resolution (fetch all refs from registry)
    → Container Build (framework-specific Dockerfile)
    → Infrastructure Provision (Pulumi/Terraform)
    → Deploy & Health Check
    → Auto-Register in Registry
    → Return Endpoint URL
```
Every step is atomic. If any step fails, the entire deploy rolls back. Never skip registration.

### 2. Governance is Non-Negotiable
Every `agentbreeder deploy` MUST:
- Validate RBAC before doing anything
- Register the agent in the registry after success
- Attribute cost to the deploying team
- Write an audit log entry

There is no "quick deploy" mode that skips governance. This is intentional.

### 3. The Sidecar Pattern (Track J — shipped)
Every deployed agent that declares `guardrails:`, MCP `tools:`, or `a2a:` gets the AgentBreeder sidecar container auto-injected. The sidecar — a single Go binary at `sidecar/` — provides:
- OpenTelemetry traces for every LLM call, tool use, and agent step
- Token counting and cost attribution (writes to `costs` + `audit_log`)
- Guardrail enforcement (PII detection, content filtering, custom rules)
- A2A JSON-RPC client at `localhost:9090/a2a/<peer>`
- MCP passthrough at `localhost:9090/mcp/<server>`
- Bearer-token validation on inbound traffic (`AGENT_AUTH_TOKEN`)
- `/health` and `/openapi.json` endpoints

> **Source:** `sidecar/` (Go module), `engine/sidecar/` (Python deployer integration), `website/content/docs/sidecar.mdx` (user docs).
> **Bypass:** set `AGENTBREEDER_SIDECAR=disabled` for local dev; agents without `guardrails:` / MCP / A2A do not get a sidecar at all.
> **Image:** `agentbreeder/agentbreeder-sidecar:<version>` (linux/amd64, linux/arm64).

### 4. Framework Agnosticism
The `engine/runtimes/` layer abstracts all framework differences. Every runtime implements:
```python
class RuntimeBuilder(ABC):
    def validate(self, agent_dir: Path, config: AgentConfig) -> ValidationResult
    def build(self, agent_dir: Path, config: AgentConfig) -> ContainerImage
    def get_entrypoint(self, config: AgentConfig) -> str
    def get_requirements(self, config: AgentConfig) -> list[str]
```
Never put framework-specific logic outside of `engine/runtimes/`. Never hard-code framework names.

### 5. The Registry is Always Consistent
Registry entries are created/updated only by:
1. `agentbreeder deploy` (primary path)
2. Connectors (secondary, passive ingestion)
3. Manual `agentbreeder register` (operator override)

Never write directly to registry tables from application code. Always go through `registry/` services.

### 6. Three-Tier Builder Model (No Code / Low Code / Full Code)
AgentBreeder supports three builder tiers for both individual agent development and multi-agent orchestration. All three compile to the same internal representation (`agent.yaml` + optional code) and share the same deploy pipeline.

```
No Code (UI)    ──→ generates agent.yaml      ──→ deploy pipeline
Low Code (YAML) ──→ is agent.yaml             ──→ deploy pipeline
Full Code (SDK) ──→ agent.yaml + custom code  ──→ deploy pipeline
```

**Rules:**
- The deploy pipeline does NOT know which tier produced the config. Never add tier-specific logic to the engine.
- No Code always generates valid, human-readable YAML. Never generate YAML that a human couldn't maintain.
- The Full Code SDK generates `agent.yaml` + bundles code — it does NOT bypass the config parser.
- Tier mobility is a first-class feature: No Code → Low Code (view YAML), Low Code → Full Code (`agentbreeder eject`).
- Visual builder layout metadata (node positions, etc.) lives in `.agentbreeder/layout.json`, never in `agent.yaml`.
- Orchestration follows the same pattern: visual canvas → `orchestration.yaml` → SDK orchestration code.

---

## 📛 Product Naming

AgentBreeder ships one product. Its surfaces have stable user-facing names — any new doc, CLI string, UI label, or marketing line MUST follow these rules.

### Surface names (canonical)

| Surface | User-facing name | Notes |
|---------|------------------|-------|
| Web UI | **AgentBreeder Studio** (or just **Studio** in-context) | The React app served at `:3001`. Folder is still `dashboard/`, Docker image is still `agentbreeder-dashboard`, route paths are unchanged — internal identifiers stay. |
| Command-line | **AgentBreeder CLI** (or `agentbreeder ...` when shown as a command) | Binary name and PyPI package are `agentbreeder`. |
| Python SDK | **AgentBreeder SDK** | `pip install agentbreeder-sdk`; import as `from agenthub import ...`. |
| API server | **AgentBreeder API** | OpenAPI title; no Studio prefix. |
| Sidecar | **AgentBreeder Sidecar** | Go binary; never call it "Studio Sidecar". |

### The Studio rule (load-bearing)

> **"Studio" is a top-level surface name only. It is never a suffix on a feature, page, or view.**

- ✅ "AgentBreeder Studio", "Studio › Agents", "Studio › Costs", "the Costs view in Studio".
- ❌ "Cost Studio", "Sessions Studio", "Eval Studio", "Studio Dashboard", "AgentOps Studio".
- Inner pages take **functional nouns**: Agents, Deploys, Costs, Sessions, Evals, Registry, Audit, Fleet, Playground.
- The lowercase word "dashboard" survives only as a generic UX pattern noun ("the overview dashboard inside Costs") — never branded, never capitalized.

### Third-party "dashboard" references stay as-is

`dashboard.stripe.com`, "Grafana dashboards", Google's "Security Dashboard", AWS "CloudWatch Dashboards" — these are other companies' product names. Do not rewrite them when sweeping AgentBreeder docs.

### What NOT to rename when the rule changes

Folder names (`dashboard/`), Docker images (`agentbreeder-dashboard`), Python modules, compose services, route paths (`/dashboard/*` if present), env vars (`DASHBOARD_URL`), CSS classes, codegen output, and any variable/function/class name. The rename is **user-facing strings only** — internal identifiers carry their own history and renaming them breaks deploys, scripts, and downstream consumers.

---

## 📦 Package Distribution Architecture

AgentBreeder is distributed through three channels for maximum reach.

### Two PyPI Packages

| Package | Contents | Install |
|---------|----------|---------|
| `agentbreeder` | CLI + API server + engine + registry + connectors | `pip install agentbreeder` |
| `agentbreeder-sdk` | Lightweight SDK (`from agenthub import Agent, deploy`) | `pip install agentbreeder-sdk` |

`agentbreeder` depends on `agentbreeder-sdk`. The SDK has minimal deps (httpx, pydantic, ruamel.yaml).

**SDK pyproject.toml:** `sdk/python/pyproject.toml`
**CLI pyproject.toml:** root `pyproject.toml`

### Three Docker Hub Images

| Image | Purpose | Dockerfile |
|-------|---------|-----------|
| `agentbreeder/agentbreeder-api` | API server | `Dockerfile` |
| `agentbreeder/agentbreeder-dashboard` | React frontend | `dashboard/Dockerfile` |
| `agentbreeder/agentbreeder-cli` | Lightweight CLI for CI/CD pipelines | `Dockerfile.cli` |

All images tagged with version + `latest`, built for linux/amd64 and linux/arm64.

### Homebrew Tap

```bash
brew tap agentbreeder/agentbreeder
brew install agentbreeder
```

Tap repo: `agentbreeder/homebrew-agentbreeder`. Auto-updated on each release.
Plan to migrate to Homebrew core once the project has sufficient traction.

### Namespace Alignment

| System | Namespace |
|--------|-----------|
| GitHub | `agentbreeder/agentbreeder` |
| PyPI | `agentbreeder`, `agentbreeder-sdk` |
| Docker Hub | `agentbreeder/agentbreeder-api`, `agentbreeder/agentbreeder-dashboard`, `agentbreeder/agentbreeder-cli` |
| Homebrew | `agentbreeder/homebrew-agentbreeder` |

### Release Flow

1. Create GitHub Release with tag `vX.Y.Z`
2. CI automatically publishes to all three channels
3. Uses PyPI trusted publishers (OIDC) — no API tokens
4. See `.github/workflows/release.yml` for details

---
