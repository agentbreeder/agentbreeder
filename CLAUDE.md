# CLAUDE.md — AgentBreeder AI Development Guide

> This file instructs Claude (and any AI coding assistant) how to work on the AgentBreeder codebase.
> Keep this file updated as the project evolves. It is the single source of truth for AI-assisted development.

---

## 🧠 What is AgentBreeder?

AgentBreeder is an **open-source platform** for building, deploying, and governing enterprise AI agents.

**Core tagline:** Define Once. Deploy Anywhere. Govern Automatically.

**The one-sentence pitch:** A developer writes one `agent.yaml` file, runs `agentbreeder deploy`, and their agent is live on AWS or GCP — with RBAC, cost tracking, audit trail, and org-wide discoverability automatic and zero extra work.

**What makes it unique:**
- Framework-agnostic (LangGraph, CrewAI, Claude SDK, OpenAI Agents, Google ADK, Custom)
- Multi-cloud first (AWS ECS Fargate/App Runner/EKS, GCP Cloud Run/GKE, Azure Container Apps, and Kubernetes as equal first-class targets)
- Governance is a **side effect** of deploying, not extra configuration
- Shared org-wide registry for agents, prompts, tools/MCP servers, models, knowledge bases
- **Three builder tiers** for both agent development AND agent orchestration:
  - **No Code** — Visual drag-and-drop UI, registry pickers, ReactFlow canvas (for PMs, analysts, citizen builders)
  - **Low Code** — YAML config (`agent.yaml`, `orchestration.yaml`) in any IDE or the dashboard editor (for ML engineers, DevOps)
  - **Full Code** — Python/TS SDK with full programmatic control, custom routing, state machines (for senior engineers, researchers)
- All three tiers compile to the same internal format and share the same deploy pipeline, governance, and observability
- **Tier mobility** — start No Code, eject to YAML, eject to Full Code. No vendor lock-in at any level.

---

## 📁 Project Structure

```
agentbreeder/
├── api/                        # FastAPI backend server
│   ├── main.py                 # App entry, middleware, routers
│   ├── auth.py                 # Auth dependencies
│   ├── config.py               # Settings (pydantic-settings)
│   ├── database.py             # Async SQLAlchemy setup
│   ├── versioning.py           # API versioning middleware + deprecation headers
│   ├── middleware/              # RBAC middleware
│   ├── routes/                 # REST endpoints
│   │   ├── agents.py           # Agent CRUD
│   │   ├── deploys.py          # Deploy from dashboard
│   │   ├── prompts.py          # Prompts + test panel
│   │   ├── providers.py        # Provider config
│   │   ├── rag.py              # RAG indexes, search
│   │   ├── memory.py           # Memory configs
│   │   ├── git.py              # Git workflow + PR review
│   │   ├── sandbox.py          # Tool sandbox execution
│   │   ├── teams.py            # Team management
│   │   ├── costs.py            # Cost tracking
│   │   ├── audit.py            # Audit log
│   │   ├── tracing.py          # Distributed tracing
│   │   ├── builders.py         # Visual builder endpoints
│   │   ├── orchestrations.py   # Orchestration management
│   │   ├── evals.py            # Agent evaluation
│   │   ├── playground.py       # Chat playground
│   │   ├── registry.py         # Cross-entity registry search
│   │   ├── a2a.py              # Agent-to-agent (A2A) communication endpoints
│   │   ├── agentops.py         # Fleet operations dashboard endpoints
│   │   ├── gateway.py          # Model gateway status + proxy endpoints
│   │   ├── marketplace.py      # Community marketplace browsing + publishing
│   │   ├── mcp_servers.py      # MCP server registry endpoints
│   │   ├── templates.py        # Agent template endpoints
│   │   └── v2/
│   │       └── agents.py       # API v2 agents endpoints
│   ├── services/               # Business logic layer
│   ├── models/                 # SQLAlchemy DB models + Pydantic schemas
│   └── tasks/                  # Background tasks (provider health)
├── cli/                        # CLI tool (built with Typer)
│   ├── main.py                 # Command registration
│   └── commands/
│       ├── init_cmd.py         # agentbreeder init
│       ├── deploy.py           # agentbreeder deploy (the core command)
│       ├── validate.py         # agentbreeder validate
│       ├── search.py           # agentbreeder search
│       ├── list_cmd.py         # agentbreeder list
│       ├── describe.py         # agentbreeder describe
│       ├── scan.py             # agentbreeder scan (MCP/LiteLLM/Ollama/OpenRouter discovery)
│       ├── schedule.py         # agentbreeder schedule (cron-based agent runs)
│       ├── logs.py             # agentbreeder logs
│       ├── status.py           # agentbreeder status
│       ├── teardown.py         # agentbreeder teardown
│       ├── submit.py           # agentbreeder submit (create PR)
│       ├── review.py           # agentbreeder review (PR review)
│       ├── publish.py          # agentbreeder publish (merge PR)
│       ├── chat.py             # agentbreeder chat
│       ├── eval.py             # agentbreeder eval
│       ├── eject.py            # agentbreeder eject (tier mobility)
│       ├── orchestration.py    # agentbreeder orchestration
│       ├── provider.py         # agentbreeder provider (subcommand)
│       ├── secret.py           # agentbreeder secret (manage secrets across backends)
│       └── template.py         # agentbreeder template (manage agent templates)
├── sdk/
│   └── python/                 # pip install agentbreeder-sdk
│       └── agenthub/           # SDK package (agent, deploy, model, tool, memory, mcp)
├── engine/                     # Core deployment pipeline
│   ├── config_parser.py        # YAML parsing + JSON Schema validation
│   ├── resolver.py             # Dependency resolution from registry
│   ├── builder.py              # Container image builder (per framework)
│   ├── governance.py           # RBAC validation at deploy time
│   ├── orchestrator.py         # Multi-agent orchestration engine
│   ├── orchestration_parser.py # Orchestration YAML parser
│   ├── providers/              # LLM provider abstraction
│   │   ├── base.py             # Provider interface
│   │   ├── openai_provider.py  # OpenAI provider
│   │   ├── anthropic_provider.py # Anthropic (Claude) provider
│   │   ├── google_provider.py  # Google (Gemini) provider
│   │   ├── ollama_provider.py  # Ollama (local) provider
│   │   ├── registry.py         # Provider registry + fallback chains
│   │   └── models.py           # Provider data models
│   ├── deployers/
│   │   ├── base.py                  # Abstract deployer interface
│   │   ├── docker_compose.py        # Local Docker Compose deployer
│   │   ├── gcp_cloudrun.py          # GCP Cloud Run deployer
│   │   ├── aws_ecs.py               # AWS ECS Fargate deployer
│   │   ├── aws_app_runner.py        # AWS App Runner deployer
│   │   ├── azure_container_apps.py  # Azure Container Apps deployer
│   │   ├── kubernetes.py            # Kubernetes deployer (EKS/GKE/AKS/self-hosted)
│   │   ├── claude_managed.py        # Claude Managed Agents deployer
│   │   ├── identity.py              # Per-agent cloud IAM identity provisioner
│   │   └── mcp_sidecar.py           # MCP sidecar container injection
│   ├── runtimes/               # Framework-specific container builders
│   │   ├── base.py             # Runtime builder interface
│   │   ├── langgraph.py        # LangGraph runtime
│   │   ├── openai_agents.py    # OpenAI Agents runtime
│   │   ├── crewai.py           # CrewAI runtime
│   │   ├── claude_sdk.py       # Claude SDK (Anthropic) runtime
│   │   ├── google_adk.py       # Google ADK runtime
│   │   ├── custom.py           # Custom (bring your own) runtime
│   │   └── templates/          # Server templates per runtime
│   ├── secrets/                # Pluggable secrets backend system
│   │   ├── base.py             # Secrets backend interface
│   │   ├── env_backend.py      # .env / environment variable backend
│   │   ├── aws_backend.py      # AWS Secrets Manager backend
│   │   ├── gcp_backend.py      # GCP Secret Manager backend
│   │   └── vault_backend.py    # HashiCorp Vault backend
│   ├── a2a/                    # Agent-to-agent (A2A) communication protocol
│   │   ├── protocol.py         # JSON-RPC A2A protocol implementation
│   │   ├── client.py           # A2A client for calling remote agents
│   │   ├── server.py           # A2A server for exposing agents
│   │   └── auth.py             # A2A authentication + agent cards
│   ├── mcp/                    # MCP packaging utilities
│   │   └── packager.py         # Package MCP servers for deployment
│   └── schema/                 # JSON Schemas
│       ├── agent.schema.json
│       ├── orchestration.schema.json
│       ├── prompt.schema.json
│       ├── tool.schema.json
│       ├── rag.schema.json
│       ├── memory.schema.json
│       └── template.schema.json
├── connectors/                 # Integration plugins (pluggable)
│   ├── base.py
│   ├── litellm/                # LiteLLM gateway connector
│   ├── mcp_scanner/            # MCP server scanner
│   ├── openrouter/             # OpenRouter model gateway connector
│   ├── email/
│   │   └── smtp.py             # SMTP email connector (send/send_async)
│   └── news/
│       ├── base.py             # NewsItem dataclass
│       ├── hackernews.py       # HackerNews via Algolia API
│       ├── arxiv.py            # ArXiv Atom feed connector
│       └── rss.py              # Generic RSS/Atom via feedparser (optional)
├── registry/                   # Catalog service
│   ├── agents.py
│   ├── prompts.py
│   ├── tools.py
│   ├── models.py
│   ├── providers.py
│   ├── deploys.py
│   ├── mcp_servers.py
│   ├── a2a_agents.py           # A2A-enabled agent registry
│   └── templates.py            # Agent template registry
├── dashboard/                  # React + TypeScript web UI
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   └── lib/
│   └── package.json
├── sidecar/                    # Track J — Go cross-cutting-concerns sidecar
│   ├── cmd/sidecar/            # main entrypoint
│   ├── internal/
│   │   ├── auth/               # bearer-token middleware
│   │   ├── guardrails/         # PII / content filter rule engine
│   │   ├── a2a/                # JSON-RPC 2.0 A2A client
│   │   ├── mcp/                # MCP HTTP/SSE passthrough
│   │   ├── otelx/              # OTLP/HTTP span exporter
│   │   ├── cost/               # /api/v1/costs + /api/v1/audit emitter
│   │   ├── proxy/              # reverse-proxy w/ guardrail egress
│   │   ├── server/             # chi router assembly
│   │   └── config/             # env + YAML loader
│   ├── Dockerfile              # multi-arch distroless image
│   └── README.md
├── deploy/
│   └── docker-compose.yml      # Local development
├── alembic/                    # Database migrations
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── examples/
    ├── langgraph-agent/
    ├── openai-agents-agent/
    ├── mcp-server/
    ├── orchestration/          # Multi-agent orchestration examples
    ├── sdk-basic/
    └── sdk-advanced/
```

---

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
> **Image:** `rajits/agentbreeder-sidecar:<version>` (linux/amd64, linux/arm64).

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
| `rajits/agentbreeder-api` | API server | `Dockerfile` |
| `rajits/agentbreeder-dashboard` | React frontend | `dashboard/Dockerfile` |
| `rajits/agentbreeder-cli` | Lightweight CLI for CI/CD pipelines | `Dockerfile.cli` |

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
| Docker Hub | `rajits/agentbreeder-api`, `rajits/agentbreeder-dashboard`, `rajits/agentbreeder-cli` |
| Homebrew | `agentbreeder/homebrew-agentbreeder` |

### Release Flow

1. Create GitHub Release with tag `vX.Y.Z`
2. CI automatically publishes to all three channels
3. Uses PyPI trusted publishers (OIDC) — no API tokens
4. See `.github/workflows/release.yml` for details

---

## 💻 Development Commands

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Local dev stack (starts postgres, redis, API, dashboard)
docker compose up -d

# Run API server
uvicorn api.main:app --reload --port 8000

# Run CLI locally
python -m cli.main --help
agentbreeder --help              # after pip install -e .
agentbreeder validate            # validate agent.yaml
agentbreeder deploy --target local  # deploy locally

# Run tests
pytest tests/unit/                    # Unit tests
pytest tests/integration/             # Integration (requires docker compose)
pytest tests/e2e/ --headed            # E2E with Playwright
pytest --cov=. --cov-report=html      # Coverage

# Frontend
cd dashboard && npm install && npm run dev

# Linting + formatting
ruff check . && ruff format .         # Python
mypy .                                 # Python type checking
cd dashboard && npm run lint           # TypeScript
cd dashboard && npm run typecheck      # TypeScript type checking

# Database migrations
alembic upgrade head                  # Apply migrations
alembic revision --autogenerate -m "description"  # Create migration

# Build CLI package
pip install build && python -m build
```

---

## 📝 The `agent.yaml` Specification

This is the canonical YAML config. AI assistants must understand every field.

```yaml
# Identity
name: customer-support-agent          # Required. Slug-friendly name.
version: 1.0.0                        # Required. SemVer.
description: "Handles tier-1 support" # Optional but encouraged.
team: customer-success                # Required. Must match a team in registry.
owner: alice@company.com              # Required. Email of responsible engineer.
tags: [support, zendesk, production]  # Optional. Used for discovery.

# Model Configuration
model:
  primary: claude-sonnet-4            # Required. Registry ref or provider/model-id.
  fallback: gpt-4o                    # Optional. Used if primary unavailable.
  gateway: litellm                    # Optional. Defaults to org gateway setting.
  temperature: 0.7                    # Optional. Model parameter.
  max_tokens: 4096                    # Optional. Model parameter.

# Framework
framework: langgraph                  # Required. One of: langgraph | crewai | claude_sdk
                                      #   | openai_agents | google_adk | custom

# Tools & MCP Servers
tools:
  - ref: tools/zendesk-mcp            # Registry reference (recommended)
  - ref: tools/order-lookup
  - name: search                      # Inline definition (for simple tools)
    type: function
    description: "Search knowledge base"
    schema: { ... }                   # OpenAPI-compatible schema

# Knowledge Bases
knowledge_bases:
  - ref: kb/product-docs              # Registry reference
  - ref: kb/return-policy

# Prompts
prompts:
  system: prompts/support-system-v3   # Registry reference (versioned)
  # Or inline:
  # system: "You are a helpful customer support agent..."

# Guardrails
guardrails:
  - pii_detection                     # Built-in: strips PII from outputs
  - hallucination_check               # Built-in: flags low-confidence responses
  - content_filter                    # Built-in: blocks harmful content
  # Custom guardrail:
  # - name: custom_check
  #   endpoint: https://guardrails.company.com/check

# Deployment Configuration
deploy:
  cloud: aws                          # Required. One of: aws | gcp | azure | kubernetes | local | claude-managed
  runtime: ecs-fargate                # Optional. Defaults per cloud:
                                      #   aws → ecs-fargate  (also: app-runner)
                                      #   gcp → cloud-run
                                      #   azure → container-apps
                                      #   kubernetes → deployment
                                      #   local → docker-compose
                                      #   claude-managed → (no container; Anthropic manages runtime)
  region: us-east-1                   # Optional. Cloud-specific.
  scaling:
    min: 1
    max: 10
    target_cpu: 70                    # Percentage for autoscaling trigger
  resources:
    cpu: "1"                          # vCPU units
    memory: "2Gi"                     # Memory
  env_vars:                           # Non-secret environment variables
    LOG_LEVEL: info
    ENVIRONMENT: production
  secrets:                            # Secret references (from AWS Secrets Manager / GCP Secret Manager)
    - ZENDESK_API_KEY
    - OPENAI_API_KEY

# Access Control (optional — defaults to team's policy)
access:
  visibility: team                    # One of: public | team | private
  allowed_callers:                    # Optional. Restrict who can call this agent.
    - team:engineering
    - team:customer-success
  require_approval: false             # If true, deploys require admin approval

# Framework-Specific Configuration (optional — only read by the matching runtime)

# Claude SDK — adaptive thinking + prompt caching
claude_sdk:
  thinking:
    type: adaptive                    # "adaptive" (default) | "enabled"
    effort: high                      # "low" | "medium" | "high"
  prompt_caching: true                # Cache system prompt (requires ≥8 192 chars for Sonnet)

# CrewAI — no extra config needed; AGENT_MODEL/AGENT_TEMPERATURE are auto-injected
# crewai: {}

# Google ADK — session and memory backends
google_adk:
  session_backend: memory             # "memory" | "database" | "vertex_ai"
  session_db_url: ""                  # Required if session_backend is "database"
  memory_service: memory              # "memory" | "vertex_ai_bank" | "vertex_ai_rag"
  artifact_service: memory            # "memory" | "gcs"
  gcs_bucket: ""                      # Required if artifact_service is "gcs"

# Claude Managed Agents — only read when deploy.cloud == "claude-managed"
# No container is built. Anthropic manages the runtime.
claude_managed:
  environment:
    networking: unrestricted          # "unrestricted" | "restricted"
  tools:
    - type: agent_toolset_20260401    # Full built-in toolset (default)
```

---

## 🔌 MCP Servers in Use

AgentBreeder uses MCP servers for development tooling. These are configured in `.mcp.json` at the repo root.

### Active MCP Servers

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/agentbreeder"],
      "description": "Read/write project files directly"
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "<token>" },
      "description": "Create issues, PRs, search code"
    },
    "postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://agentbreeder:agentbreeder@localhost:5432/agentbreeder"],
      "description": "Query registry database directly during development"
    },
    "docker": {
      "command": "npx",
      "args": ["-y", "mcp-server-docker"],
      "description": "Manage local Docker containers and images"
    },
    "fetch": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-fetch"],
      "description": "Fetch external URLs (docs, APIs)"
    },
    "sequential-thinking": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
      "description": "Use for multi-step planning before implementing complex features"
    },
    "memory": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"],
      "description": "Persist context across sessions (architecture decisions, etc.)"
    },
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp"],
      "description": "E2E test the dashboard UI and CLI output"
    }
  }
}
```

### How to Use MCP in Development

When working on a feature, use MCPs in this order:
1. `sequential-thinking` — plan the implementation approach before coding
2. `filesystem` — read existing code before modifying
3. `postgres` — validate schema before writing migration
4. `github` — create issues or PRs after implementation
5. `playwright` — verify UI changes work end-to-end

---

## ✅ Coding Standards

### Python

```python
# Always use type hints
def deploy_agent(config: AgentConfig, env: str = "production") -> DeployResult:
    ...

# Always use Pydantic for data validation
class AgentConfig(BaseModel):
    name: str
    version: str
    team: str
    framework: FrameworkType
    model: ModelConfig
    deploy: DeployConfig

# Never use print() — use the logger
import logging
logger = logging.getLogger(__name__)
logger.info("Deploying agent", extra={"agent": config.name, "env": env})

# Always handle errors explicitly — never bare except
try:
    result = deployer.deploy(config)
except DeploymentError as e:
    logger.error("Deployment failed", extra={"error": str(e)})
    raise

# Async for all I/O
async def register_agent(agent: Agent) -> RegistryEntry:
    async with db.session() as session:
        ...
```

### TypeScript / React

```typescript
// Always type everything — no `any`
interface AgentCardProps {
  agent: Agent;
  onSelect: (id: string) => void;
}

// Use React Query for all API calls
const { data: agents, isLoading } = useQuery({
  queryKey: ['agents', teamId],
  queryFn: () => api.agents.list({ teamId }),
});

// Use Tailwind — no inline styles
// ✅
<div className="flex items-center gap-3 rounded-lg bg-white border border-gray-200 p-4">
// ❌
<div style={{ display: 'flex', padding: 16 }}>

// Always handle loading and error states
if (isLoading) return <Skeleton />;
if (error) return <ErrorBanner message={error.message} />;
```

### Tests

Every new feature requires:
- Unit test for the core logic (`tests/unit/`)
- Integration test for API endpoints (`tests/integration/`)
- E2E test if it touches the dashboard (`tests/e2e/`)

```python
# Unit test example — mock all external dependencies
async def test_deploy_validates_rbac_before_building():
    config = make_agent_config(team="engineering")
    rbac = MockRBAC(deny_team="engineering")
    engine = DeployEngine(rbac=rbac, builder=MockBuilder())

    with pytest.raises(RBACDeniedError):
        await engine.deploy(config, user="alice")

    # Builder should never have been called
    assert not MockBuilder.build_called
```

---

## 🚫 Common Mistakes to Avoid

1. **Never skip RBAC validation** — every deploy MUST check permissions, even in tests (mock it, don't skip it)
2. **Never write to the registry directly** — always use registry service classes
3. **Never hard-code cloud provider names** — use the deployer abstraction
4. **Never put framework-specific logic in `engine/builder.py`** — it belongs in `engine/runtimes/`
5. **Never commit secrets or credentials** — use `.env` and Secrets Manager references
6. **Never use synchronous I/O in async FastAPI handlers** — always `await` or use `run_in_executor`
7. **Never break the `agentbreeder deploy` happy path** — it is the product; protect it like an API contract
8. **Never merge without tests** — CI blocks PRs with < 80% coverage on changed files

---

## 🔄 Cross-Repo Sync (Standing Instruction)

AgentBreeder ships across **three repositories** that must stay in sync at all times:

| Repo | Path | Role |
|------|------|------|
| `agentbreeder` (this repo) | `/Users/rajit/personal-github/agentbreeder` | OSS CLI + engine + connectors — source of truth |
| `agentbreeder-cloud` | `/Users/rajit/personal-github/agentbreeder-cloud` | SaaS managed platform — deploys on top of agentbreeder packages |
| `website` | `website/` subdirectory | agentbreeder.io marketing + docs site |

**Rules — before closing any PR:**
- **Schema change** (`agent.yaml`, API shape, CLI flags): grep `agentbreeder-cloud` for affected fields and update them
- **New connector or feature**: check if `agentbreeder-cloud` needs to expose it
- **Version bump**: update `website/components/footer.tsx` version badge, update features/docs pages
- **Breaking change**: `agentbreeder-cloud` must get a companion PR before or alongside

> The cloud project uses `agentbreeder` packages as its deploy infrastructure.
> A silent break in OSS will silently break Cloud. The website going stale misrepresents the product.

---

## 🎯 When Adding a New Feature

1. **Check the ROADMAP.md** — is this feature planned? Which milestone?
2. **Check AGENT.md** — which AI skills/agents can help build it?
3. **Use `sequential-thinking` MCP** — plan before coding for anything > 100 lines
4. **Write the test first** — TDD is strongly preferred for engine and API code
5. **Update the JSON Schema** — if you changed `agent.yaml` fields
6. **Update the docs** — if you changed a public API or CLI command
7. **Add an example** — if you added a new framework or deployer, add it to `examples/`
8. **Scaffold with `/agent-build`** — when starting a new agent project, run `/agent-build` in Claude Code. The Advisory Path generates IDE config files (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.antigravity.md`) tailored to the chosen framework, model, and deployment target. These files give Claude and Cursor context-aware guidance for the specific agent being built.

---

## 🌐 API Conventions

```
# Registry
GET    /api/v1/agents                 # List agents (paginated, filterable)
GET    /api/v1/agents/{id}            # Get agent detail
POST   /api/v1/agents                 # Create/register agent
PUT    /api/v1/agents/{id}            # Update agent
DELETE /api/v1/agents/{id}            # Soft-delete (archive)
GET    /api/v1/registry/search        # Cross-entity registry search

# Deploy
POST   /api/v1/deploys               # Trigger a deployment
GET    /api/v1/deploys/{job_id}       # Poll deploy status

# Builders (visual agent/tool/prompt builders)
POST   /api/v1/builders/...          # Visual builder endpoints

# Providers
GET    /api/v1/providers              # List configured providers
POST   /api/v1/providers              # Add provider

# RAG & Memory
GET/POST /api/v1/rag/*                # RAG indexes, file ingestion, search
GET/POST /api/v1/memory/*             # Memory configs, conversation storage

# Git Workflow
GET/POST /api/v1/git/*                # Git operations, PR review workflow

# Governance
GET    /api/v1/teams                  # Team management
GET    /api/v1/costs                  # Cost data (filterable by team/agent/model)
GET    /api/v1/audit                  # Audit trail
GET    /api/v1/tracing                # Distributed tracing

# Tools
POST   /api/v1/tools/sandbox/execute  # Tool sandbox execution
GET    /api/v1/prompts/test           # Test prompt with model
GET    /api/v1/playground             # Chat playground
GET    /api/v1/evals                  # Agent evaluation

# Agent-to-Agent (A2A)
GET/POST /api/v1/a2a/*               # A2A protocol, agent cards, inter-agent calls

# Fleet Operations
GET    /api/v1/agentops               # Fleet dashboard, multi-agent monitoring

# Model Gateway
GET    /api/v1/gateway                # Gateway status, provider health, model proxy

# Marketplace
GET    /api/v1/marketplace            # Browse community templates + agents
POST   /api/v1/marketplace/publish    # Publish template to marketplace

# MCP Servers
GET/POST /api/v1/mcp_servers/*        # MCP server registry CRUD

# Templates
GET/POST /api/v1/templates/*          # Agent template management

# API v2 (versioned endpoints — see api/versioning.py)
GET    /api/v2/agents                 # v2 agents endpoint (enhanced filtering)
```

All responses follow:
```json
{
  "data": { ... },
  "meta": { "page": 1, "total": 42 },
  "errors": []
}
```

---

## 📦 Environment Variables

```bash
# Required
DATABASE_URL=postgresql+asyncpg://agentbreeder:agentbreeder@localhost:5432/agentbreeder
REDIS_URL=redis://localhost:6379
SECRET_KEY=<random-256-bit-key>
AGENTBREEDER_ENV=development

# Optional — Cloud credentials (set per environment)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-1
GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
GOOGLE_CLOUD_PROJECT=

# Optional — Integrations
LITELLM_BASE_URL=http://localhost:4000
LANGSMITH_API_KEY=
OPENTELEMETRY_ENDPOINT=http://localhost:4317

# Optional — Auth
JWT_SECRET_KEY=
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
```

---

## 🤝 For Contributors

When reviewing AI-generated code, always verify:
- [ ] Does this change touch the deploy pipeline? If yes, run full integration tests.
- [ ] Does this change the `agent.yaml` schema? If yes, update JSON Schema + docs.
- [ ] Does this change the registry schema? If yes, write a migration.
- [ ] Does this add a new deployer or runtime? If yes, add it to the supported stack matrix in README.
- [ ] Does this change a CLI command? If yes, update the corresponding page in `website/content/docs/` **in the same commit**. See the doc sync table below.

### 📄 Docs Sync — What to Update

Every time a feature or CLI command changes, update the matching doc page(s) before closing the PR.

| What changed | Doc pages to update |
|---|---|
| New CLI command | `website/content/docs/cli-reference.mdx` (add full entry with flags + examples) |
| Changed CLI command flags | `website/content/docs/cli-reference.mdx` (update the relevant section) |
| New quickstart / bootstrap flow | `website/content/docs/quickstart.mdx` |
| New install method | `website/content/docs/quickstart.mdx` → Install table; `website/content/docs/how-to.mdx` → Install section |
| New or changed RAG / ChromaDB behavior | `website/content/docs/rag.mdx` |
| New or changed GraphRAG / Neo4j behavior | `website/content/docs/graphrag.mdx` |
| New or changed MCP server support | `website/content/docs/mcp-servers.mdx` |
| New or changed A2A protocol behavior | `website/content/docs/a2a-protocol.mdx` |
| New `agent.yaml` field | `website/content/docs/agent-yaml.mdx` |
| New LLM provider | `website/content/docs/how-to.mdx` (local models section) |
| New framework support | `website/content/docs/how-to.mdx` + README supported stack matrix |
| New cloud deploy target | `website/content/docs/how-to.mdx` + README supported stack matrix |
| New evaluation feature | `website/content/docs/evaluations.mdx` |
| README Install section | Keep in sync with `website/content/docs/quickstart.mdx` → Install table |

**Rule:** The doc update goes in the **same commit** as the code change — never as a follow-up. If the doc page does not exist yet, create it under `website/content/docs/`.

**The website auto-deploys on push to `main`.** There is no separate deploy step — merging the PR publishes the docs immediately.

---

*Last updated: April 2026 — AgentBreeder v2.0*
