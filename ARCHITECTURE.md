# Architecture

> AgentBreeder is a deployment platform. A developer writes `agent.yaml`, runs `agentbreeder deploy`, and the platform handles container building, infrastructure provisioning, governance, and registry registration.

---

## Three-Tier Builder Model

AgentBreeder supports three ways to build agents and orchestrations. All three tiers compile down to the same internal representation (`agent.yaml` + optional code) and share the same deploy pipeline, governance, and observability.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     THREE BUILDER TIERS                                  │
│                                                                         │
│  No Code (UI)          Low Code (YAML)         Full Code (SDK)          │
│  ─────────────         ───────────────          ──────────────          │
│  Visual drag-and-drop  agent.yaml in any IDE    Python/TS SDK           │
│  Registry pickers      YAML orchestration       Programmatic control    │
│  ReactFlow canvas      Any editor works         Custom routing logic    │
│                                                                         │
│         │                     │                        │                │
│         └─────────────────────┼────────────────────────┘                │
│                               │                                         │
│                               ▼                                         │
│                    ┌──────────────────────┐                              │
│                    │  agent.yaml + code   │  ← Unified internal format  │
│                    └──────────────────────┘                              │
│                               │                                         │
│                               ▼                                         │
│                    ┌──────────────────────┐                              │
│                    │   Deploy Pipeline    │  ← Same for all tiers       │
│                    │   Governance         │                              │
│                    │   Observability      │                              │
│                    │   Registry           │                              │
│                    └──────────────────────┘                              │
└─────────────────────────────────────────────────────────────────────────┘
```

### Tier Details

| Tier | Agent Development | Agent Orchestration | Eject Path |
|------|-------------------|---------------------|------------|
| **No Code** | Visual builder: pick model, tools, prompt, guardrails from registry. Generates `agent.yaml`. | Visual canvas: wire agents as nodes, define routing/handoff rules. Generates `orchestration.yaml`. | "View YAML" → opens in Low Code editor |
| **Low Code** | Write `agent.yaml` in any IDE (Cursor, Claude Code, VS Code, vim) or the dashboard YAML editor | Write `orchestration.yaml` defining agent graph, routing strategy, shared state | "Eject to SDK" → generates Python/TS scaffold |
| **Full Code** | Python/TS SDK: `from agentbreeder import Agent, Tool, Memory` — full programmatic control, custom logic, dynamic tool selection | Python/TS SDK: define orchestration graphs, custom routing functions, state machines, human-in-the-loop breakpoints | N/A (maximum control) |

### Compilation Model

The key architectural insight: all three tiers produce the same artifact that the deploy pipeline consumes.

```python
# No Code UI → generates this YAML internally
# Low Code   → developer writes this YAML directly
# Full Code  → SDK wraps this YAML + bundles custom code

agent.yaml + optional code directory
    │
    ├── engine/config_parser.py    # parses YAML (same for all tiers)
    ├── engine/runtimes/           # builds container (same for all tiers)
    ├── engine/deployers/          # provisions cloud (same for all tiers)
    └── engine/governance.py       # validates RBAC (same for all tiers)
```

**No Code** generates pure YAML — no custom code. The platform provides all logic via built-in components.

**Low Code** is pure YAML — the developer writes it directly. They may include simple inline tool definitions or prompt text.

**Full Code** produces YAML + a code directory. The SDK generates a valid `agent.yaml` from programmatic definitions and bundles the custom code (routing functions, state management, custom tools) alongside it. The runtime builder packages both into the container.

### Tier Mobility (Ejection)

Users can move between tiers without losing work:

```
No Code ──"View YAML"──→ Low Code ──"Eject to SDK"──→ Full Code
                                                         │
   ← "Import YAML" ←──────────────── ← (manual) ←──────┘
```

- **No Code → Low Code**: The visual builder always shows a "View YAML" tab. The generated YAML is valid, readable, and editable. Switching to Low Code is just opening that YAML in an editor.
- **Low Code → Full Code**: A CLI command (`agentbreeder eject my-agent --sdk python`) generates a Python project scaffold that recreates the YAML config as SDK code, ready for extension.
- **Full Code → Low Code**: Not automatic (code can express things YAML can't), but the SDK always generates a valid `agent.yaml` that can be imported back.

---

## System Overview

```
Developer                    AgentBreeder Platform                     Cloud

agent.yaml  ──>  [ CLI ]  ──>  [ API Server ]  ──>  [ Engine ]  ──>  [ AWS / GCP / K8s ]
                                      |                  |
                                      v                  v
                                [ PostgreSQL ]    [ Container Registry ]
                                  (Registry)             |
                                      |                  v
                                  [ Redis ]       [ Agent + Sidecar ]
                                  (Queue)
```

### Components

| Component | Technology | Purpose |
|-----------|-----------|---------|
| CLI | Python, Typer, Rich | Developer interface — `agentbreeder init`, `agentbreeder deploy`, `agentbreeder list` |
| API Server | Python 3.11+, FastAPI | REST API — async, OpenAPI auto-docs |
| Engine | Python | Core deploy pipeline — config parsing, container building, cloud provisioning |
| Registry | PostgreSQL, SQLAlchemy | Catalog of all agents, tools, models, prompts, knowledge bases |
| Queue | Redis | Task queue for async deploy jobs + rate limiting |
| Dashboard | React 18, TypeScript, Tailwind, Vite | Web UI for browsing and managing the registry |
| SDKs | Python + TypeScript | Programmatic access to the platform |

---

## The Deploy Pipeline

This is the core of AgentBreeder. Every `agentbreeder deploy` executes these steps in order. Each step is atomic — if any step fails, the entire deploy rolls back.

```
1. Parse & Validate YAML
       │
2. RBAC Check (fail fast if unauthorized)
       │
3. Dependency Resolution (fetch all refs from registry)
       │
4. Container Build (framework-specific Dockerfile)
       │
5. Infrastructure Provision (Pulumi)
       │
6. Deploy & Health Check
       │
7. Auto-Register in Registry
       │
8. Return Endpoint URL
```

### Step Details

**1. Parse & Validate** (`engine/config_parser.py`)
Reads the `agent.yaml` file, validates against the JSON Schema, and returns a typed `AgentConfig` object. Validation errors point to the exact line and suggest fixes.

**2. RBAC Check** (`engine/governance.py`)
Validates that the deploying user has permission to deploy for the specified team. This always runs — there is no "quick deploy" mode that skips governance.

**3. Dependency Resolution** (`engine/resolver.py`)
Resolves all registry references (`ref: tools/zendesk-mcp`, `ref: prompts/support-system-v3`) into concrete artifacts. Fails if any reference is missing.

**4. Container Build** (`engine/builder.py` → `engine/runtimes/`)
Delegates to the framework-specific runtime builder (e.g., `LangGraphRuntime`). Generates a Dockerfile, injects the observability sidecar, and builds the container image.

**5. Infrastructure Provision** (`engine/deployers/`)
Delegates to the cloud-specific deployer (e.g., `AWSECSDeployer`). Uses Pulumi to provision cloud resources (load balancer, service, IAM roles, etc.).

**6. Deploy & Health Check**
Pushes the container image, starts the service, and polls the `/health` endpoint until it responds.

**7. Auto-Register**
Creates or updates the agent's entry in the registry with full metadata — endpoint URL, framework, model, tools, deploy timestamp, team ownership.

**8. Return Endpoint**
Returns the agent's invoke URL (e.g., `https://agents.company.com/customer-support/invoke`).

---

## Key Abstractions

### RuntimeBuilder (`engine/runtimes/base.py`)

Abstracts all framework differences. Every supported framework implements this interface:

```python
class RuntimeBuilder(ABC):
    def validate(self, agent_dir: Path, config: AgentConfig) -> ValidationResult
    def build(self, agent_dir: Path, config: AgentConfig) -> ContainerImage
    def get_entrypoint(self, config: AgentConfig) -> str
    def get_requirements(self, config: AgentConfig) -> list[str]
```

Framework-specific logic must never appear outside of `engine/runtimes/`. Never hard-code framework names in the engine or API layers.

### BaseDeployer (`engine/deployers/base.py`)

Abstracts all cloud differences. Every cloud target implements:

```python
class BaseDeployer(ABC):
    async def provision(self, config: AgentConfig) -> InfraResult
    async def deploy(self, config: AgentConfig, image: ContainerImage) -> DeployResult
    async def health_check(self, deploy_result: DeployResult) -> HealthStatus
    async def teardown(self, agent_id: str) -> None
    async def get_logs(self, agent_id: str, since: datetime) -> list[LogEntry]
```

Cloud-specific logic must never appear outside of `engine/deployers/`.

### Registry (`registry/`)

The central catalog for all organizational AI assets:

| Entity | Description |
|--------|-------------|
| Agents | Deployed agent instances with metadata |
| Tools | MCP servers and function tools |
| Models | Approved LLM models (via LiteLLM or direct) |
| Prompts | Versioned prompt templates |
| Knowledge Bases | RAG data sources |

Registry entries are only created/updated by:
1. `agentbreeder deploy` (primary path)
2. Connectors (passive ingestion from external tools)
3. `agentbreeder register` (manual operator override)

Never write directly to registry tables from application code.

### Sidecar (`engine/sidecar/`)

Every deployed agent gets a sidecar container injected automatically. The sidecar provides:

- **OpenTelemetry traces** for every LLM call, tool use, and agent step
- **Token counting** and cost attribution
- **Guardrail enforcement** (PII detection, content filtering)
- **Health check endpoint** (`/health`)

The sidecar requires zero changes to agent code. It is injected at the container build step.

### Connectors (`connectors/`)

Plugin system for integrating external tools. Each connector implements `BaseConnector` and ingests data into the registry:

| Connector | Type | Purpose |
|-----------|------|---------|
| LiteLLM | Gateway | Registers available models, captures cost data |
| LangSmith | Observability | Pulls eval scores and traces |
| MCP Scanner | Discovery | Auto-discovers local MCP servers |
| OpenTelemetry | Observability | Receives traces from sidecars |

---

## Data Model

```
Agent ──references──> Tool (many-to-many)
  │                   Model (many-to-one primary, optional fallback)
  │                   Prompt (many-to-many)
  │                   KnowledgeBase (many-to-many)
  │
  └── belongs to ──> Team
```

Storage: PostgreSQL with SQLAlchemy ORM. Migrations via Alembic.

---

## API Layer

FastAPI with async handlers. All responses follow a consistent shape:

```json
{
  "data": { ... },
  "meta": { "page": 1, "total": 42 },
  "errors": []
}
```

Key routes:

```
POST   /api/v1/deploy                 # Trigger deployment
GET    /api/v1/deploy/{job_id}        # Poll deploy status

GET    /api/v1/agents                 # List agents
GET    /api/v1/agents/{id}            # Agent detail
POST   /api/v1/agents                 # Register agent

GET    /api/v1/registry/tools         # List tools/MCP servers
GET    /api/v1/registry/prompts       # List prompt templates
GET    /api/v1/registry/models        # List approved models

# Tracing (M14)
GET    /api/v1/traces                 # List traces (filterable by agent, status, date, cost)
POST   /api/v1/traces                 # Ingest a new trace
GET    /api/v1/traces/{trace_id}      # Get trace with all spans
GET    /api/v1/traces/metrics/{agent} # Aggregated metrics for an agent
POST   /api/v1/traces/{trace_id}/spans  # Add a span to a trace
DELETE /api/v1/traces                 # Bulk delete traces before a date

# Teams & RBAC (M15)
GET    /api/v1/teams                  # List teams
POST   /api/v1/teams                  # Create team
GET    /api/v1/teams/{id}             # Team detail with members
PUT    /api/v1/teams/{id}             # Update team
DELETE /api/v1/teams/{id}             # Delete team
POST   /api/v1/teams/{id}/members     # Add member
PUT    /api/v1/teams/{id}/members/{uid}  # Update member role
DELETE /api/v1/teams/{id}/members/{uid}  # Remove member
GET    /api/v1/teams/{id}/api-keys    # List API keys (hints only)
POST   /api/v1/teams/{id}/api-keys    # Set API key for a provider
DELETE /api/v1/teams/{id}/api-keys/{kid} # Delete API key
POST   /api/v1/teams/{id}/api-keys/{kid}/test  # Test API key

# Cost Tracking (M16)
POST   /api/v1/costs/events           # Record a cost event
GET    /api/v1/costs/summary          # Aggregated cost summary
GET    /api/v1/costs/breakdown        # Cost breakdown by agent/model/team
GET    /api/v1/costs/trend            # Daily cost trend
GET    /api/v1/costs/top-spenders     # Top N agents by cost
POST   /api/v1/costs/compare          # Compare model costs
GET    /api/v1/budgets                # List team budgets
POST   /api/v1/budgets                # Create/update budget
GET    /api/v1/budgets/{team}         # Get team budget
PUT    /api/v1/budgets/{team}         # Update team budget

# Audit & Lineage (M17)
GET    /api/v1/audit                  # List audit events (filterable)
POST   /api/v1/audit                  # Record audit event
GET    /api/v1/audit/resource/{type}/{id}  # Events for a resource
GET    /api/v1/lineage/{type}/{id}    # Dependency graph for a resource
GET    /api/v1/lineage/impact/{type}/{name}  # Impact analysis
POST   /api/v1/lineage/dependencies   # Register a dependency
POST   /api/v1/lineage/sync/{agent}   # Sync agent dependencies from config
```

---

## Observability Layer (v0.4)

AgentBreeder provides built-in observability across three dimensions: tracing, cost tracking, and audit. These services are implemented as FastAPI route modules backed by dedicated service classes.

### Tracing (`api/routes/tracing.py`, `api/services/tracing_service.py`)

Every LLM call, tool invocation, and agent step is captured as a **trace** containing one or more **spans**. The tracing store supports:

- **Trace ingestion** — sidecars and SDKs POST traces and spans via the REST API.
- **Filtering and search** — list traces by agent, status, date range, duration, or cost; full-text search over inputs/outputs.
- **Agent metrics** — aggregated summaries (request count, error rate, P50/P95 latency, token usage) over a configurable time window.
- **Bulk cleanup** — delete traces older than a given date to manage storage.

The tracing backend is pluggable: Langfuse (default), MLflow, or raw OpenTelemetry export. Configured via `TRACING_BACKEND` env var.

### Cost Tracking (`api/routes/costs.py`, `api/services/cost_service.py`)

Cost events are recorded per LLM call with token counts, model name, provider, and dollar cost. The cost service provides:

- **Summary and breakdown** — aggregate spend by team, agent, or model over any time window.
- **Daily trend** — time-series cost data for charting.
- **Top spenders** — ranked list of agents by cost.
- **Model comparison** — estimate cost differences between two models for a given token volume.
- **Budgets** — per-team monthly spending limits with configurable alert thresholds (default 80%).

### Audit & Lineage (`api/routes/audit.py`, `api/services/audit_service.py`)

An immutable audit log records every significant action (deploy, config change, delete, access change) with actor, action, resource, team, and timestamp. The lineage system tracks resource dependencies:

- **Dependency graph** — for any resource (agent, tool, prompt, model), retrieve its full dependency tree as nodes and edges.
- **Impact analysis** — answer "if I change this prompt/tool, which agents are affected?" before making changes.
- **Dependency sync** — automatically extract and register dependencies from an agent's config snapshot.

---

## RBAC & Teams (v0.4)

Team management is implemented in `api/routes/teams.py` and `api/services/team_service.py`. Every agent, tool, prompt, and model belongs to a team. Key capabilities:

- **Team CRUD** — create, update, delete teams with display name and description.
- **Membership** — add/remove members, assign roles (Viewer, Deployer, Admin).
- **Team-scoped API keys** — each team manages its own provider keys (Anthropic, OpenAI, etc.), stored encrypted. Keys can be tested before activation.
- **Permission checks** — all CRUD operations validate the caller's team role before proceeding.

---

## Full Code SDK (v0.4)

The Python SDK (`sdk/python/agenthub/`) provides the Full Code tier for agent development. It exposes a builder-pattern API:

```python
from agenthub import Agent, Tool, Model, Memory

agent = (
    Agent("my-agent", version="1.0.0", team="engineering")
    .with_model(primary="claude-sonnet-4", fallback="gpt-4o")
    .with_tool(Tool.from_ref("tools/zendesk-mcp"))
    .with_memory(backend="redis")
    .with_guardrail("pii_detection")
    .with_deploy(cloud="aws", runtime="ecs-fargate")
)
```

Key classes: `Agent`, `Tool`, `Model`, `Memory`, `DeployConfig`. The SDK supports full YAML round-trip: `agent.to_yaml()` serializes to valid `agent.yaml`, and `Agent.from_yaml()` loads YAML back into SDK objects.

The `agentbreeder eject` CLI command (`cli/commands/eject.py`) generates an SDK scaffold from any existing `agent.yaml`, enabling tier mobility from Low Code to Full Code without losing configuration.

---

## Design Principles

1. **Governance is a side effect** — deploying through AgentBreeder automatically creates RBAC records, cost attribution, audit entries, and registry listings. There is no separate governance setup.

2. **Framework-agnostic** — no framework-specific logic outside `engine/runtimes/`. The rest of the system treats all frameworks identically.

3. **Multi-cloud** — no cloud-specific logic outside `engine/deployers/`. AWS and GCP are equal first-class citizens.

4. **Registry consistency** — all writes go through registry service classes. No direct database access from application code.

5. **The deploy pipeline is sacred** — the 8-step flow is the product. Protect it like an API contract. Never skip a step. Never break atomicity.

6. **Three tiers, one pipeline** — No Code (UI), Low Code (YAML), and Full Code (SDK) all compile to the same internal format (`agent.yaml` + optional code). The deploy pipeline, governance, observability, and registry are tier-agnostic. This applies to both agent development and multi-agent orchestration.

7. **Tier mobility** — users can move between tiers without losing work. No Code generates valid YAML (eject to Low Code). Low Code can be scaffolded into SDK code (`agentbreeder eject`). This prevents vendor lock-in at any abstraction level.

---

*See [CLAUDE.md](CLAUDE.md) for complete coding standards, the full `agent.yaml` specification, and development commands.*
