# Architecture

> AgentBreeder is a deployment platform. A developer writes `agent.yaml`, runs `agentbreeder deploy`, and the platform handles container building, infrastructure provisioning, governance, and registry registration — automatically, regardless of which framework or cloud target is used.

---

## Three-Tier Builder Model

AgentBreeder supports three ways to build agents and orchestrations. All three tiers compile to the same internal format and share the same deploy pipeline, governance, and observability.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        THREE BUILDER TIERS                              │
│                                                                         │
│  No Code (UI)          Low Code (YAML)         Full Code (SDK)          │
│  ─────────────         ───────────────          ──────────────          │
│  Visual drag-and-drop  agent.yaml in any IDE    Python/TS SDK           │
│  Registry pickers      YAML orchestration       Programmatic control    │
│  ReactFlow canvas      Any editor works         Custom routing logic    │
│                                                                         │
│         │                     │                        │                │
│         └─────────────────────┼────────────────────────┘                │
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
|------|---|---|---|
| **No Code** | Visual builder: pick model, tools, prompt, guardrails from registry. Generates `agent.yaml`. | Visual canvas: wire agents as nodes, define routing rules. Generates `orchestration.yaml`. | "View YAML" → opens in Low Code editor |
| **Low Code** | Write `agent.yaml` in any IDE or the dashboard YAML editor | Write `orchestration.yaml` defining agent graph, routing strategy, shared state | `agentbreeder eject` → generates Python/TS scaffold |
| **Full Code** | Python/TS SDK with full programmatic control, custom logic, dynamic tool selection | SDK orchestration graphs, custom routing functions, state machines | N/A (maximum control) |

### Compilation Model

All three tiers produce the same artifact consumed by the deploy pipeline:

```python
# No Code UI  → generates YAML internally
# Low Code    → developer writes YAML directly
# Full Code   → SDK generates YAML + bundles custom code

agent.yaml + optional code directory
    │
    ├── engine/config_parser.py    # parses YAML (same for all tiers)
    ├── engine/runtimes/           # builds container (same for all tiers)
    ├── engine/deployers/          # provisions cloud (same for all tiers)
    └── engine/governance.py       # validates RBAC (same for all tiers)
```

### Tier Mobility (Ejection)

```
No Code ──"View YAML"──→ Low Code ──agentbreeder eject──→ Full Code
                                                              │
   ← "Import YAML" ←──────────────────── ← (manual) ←───────┘
```

- **No Code → Low Code**: The visual builder always shows a "View YAML" tab. Generated YAML is valid, readable, and editable.
- **Low Code → Full Code**: `agentbreeder eject my-agent --sdk python` generates a Python project scaffold that recreates the YAML config as SDK code.
- **Full Code → Low Code**: Not automatic (code can express things YAML cannot), but the SDK always generates a valid `agent.yaml` that can be imported back.

---

## System Overview

```
Developer                    AgentBreeder Platform                     Cloud

agent.yaml  ──>  [ CLI ]  ──>  [ API Server ]  ──>  [ Engine ]  ──>  [ AWS / GCP / K8s / Azure ]
                                      │                  │
                                      ▼                  ▼
                                [ PostgreSQL ]    [ Container Registry ]
                                  (Registry)             │
                                      │                  ▼
                                  [ Redis ]       [ Agent Container ]
                                  (Queue)         [ MCP Sidecar ]
```

### Components

| Component | Technology | Purpose |
|---|---|---|
| CLI | Python, Typer, Rich | Developer interface — `agentbreeder init`, `deploy`, `eval`, `eject`, `chat` |
| API Server | Python 3.11+, FastAPI | REST API — async, 201 endpoints, OpenAPI auto-docs |
| Engine | Python | Core deploy pipeline — config parsing, container building, cloud provisioning |
| Registry | PostgreSQL, SQLAlchemy | Catalog of all agents, tools, models, prompts, MCP servers, templates |
| Queue | Redis | Task queue for async deploy jobs, shared state for multi-agent orchestrations |
| Dashboard | React 18, TypeScript, Tailwind, Vite | 46-page web UI — visual builders, analytics, fleet management |
| Python SDK | Python 3.11+ | `pip install agentbreeder-sdk` — builder pattern API |
| TypeScript SDK | TypeScript 5.0+ | `npm install @agentbreeder/sdk` — equivalent API surface |

---

## The Deploy Pipeline

Every `agentbreeder deploy` executes these 8 steps in order. Each step is atomic — if any step fails, the entire deploy rolls back.

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
Reads `agent.yaml`, validates against the JSON Schema at `engine/schema/agent.schema.json`, and returns a typed `AgentConfig`. Errors point to the exact field and suggest fixes.

**2. RBAC Check** (`engine/governance.py`)
Validates that the deploying user has permission for the specified team. Always runs — there is no "quick deploy" mode that skips governance.

**3. Dependency Resolution** (`engine/resolver.py`)
Resolves all registry refs (`ref: tools/zendesk-mcp`, `ref: prompts/support-system-v3`) into concrete artifacts. Fails if any reference is missing or the version is unavailable.

**4. Container Build** (`engine/builder.py` → `engine/runtimes/`)
Delegates to the framework-specific runtime builder. Generates a Dockerfile, installs dependencies, and builds the container image.

**5. Infrastructure Provision** (`engine/deployers/`)
Delegates to the cloud-specific deployer. Uses Pulumi to provision cloud resources (load balancer, service definition, IAM roles, networking).

**6. Deploy & Health Check**
Pushes the container image, starts the service, and polls `/health` until it responds.

**7. Auto-Register** (`registry/`)
Creates or updates the agent's registry entry — endpoint URL, framework, model, tools, deploy timestamp, team ownership.

**8. Return Endpoint**
Returns the agent's invoke URL (e.g., `https://agents.company.com/customer-support/invoke`).

---

## Key Abstractions

### RuntimeBuilder (`engine/runtimes/base.py`)

Abstracts all framework differences. Every supported framework implements this interface. Framework-specific logic must never appear outside `engine/runtimes/`.

```python
class RuntimeBuilder(ABC):
    def validate(self, agent_dir: Path, config: AgentConfig) -> ValidationResult
    def build(self, agent_dir: Path, config: AgentConfig) -> ContainerImage
    def get_entrypoint(self, config: AgentConfig) -> str
    def get_requirements(self, config: AgentConfig) -> list[str]
```

**Supported runtimes:** `langgraph.py`, `crewai.py`, `claude_sdk.py`, `openai_agents.py`, `google_adk.py`, `custom.py`

### BaseDeployer (`engine/deployers/base.py`)

Abstracts all cloud differences. Cloud-specific logic must never appear outside `engine/deployers/`.

```python
class BaseDeployer(ABC):
    async def provision(self, config: AgentConfig) -> InfraResult
    async def deploy(self, config: AgentConfig, image: ContainerImage) -> DeployResult
    async def health_check(self, deploy_result: DeployResult) -> HealthStatus
    async def teardown(self, agent_id: str) -> None
    async def get_logs(self, agent_id: str, since: datetime) -> list[LogEntry]
```

**Supported deployers:** `docker_compose.py`, `gcp_cloudrun.py`, `aws_ecs.py`, `aws_app_runner.py`, `kubernetes.py`, `azure_container_apps.py`, `claude_managed.py`

**MCP sidecar:** `mcp_sidecar.py` injects MCP server containers alongside the agent container at deploy time, exposing MCP tools via a local socket.

### Registry (`registry/`)

The central catalog for all organizational AI assets. Entries are only created or updated by:
1. `agentbreeder deploy` (primary path)
2. Connectors (passive ingestion from external tools)
3. `agentbreeder register` (manual operator override)

Never write directly to registry tables from application code — always go through registry service classes.

**10 tracked entity types:**

| Entity | Registry File | Description |
|---|---|---|
| Agents | `registry/agents.py` | Deployed agent instances with metadata and versions |
| A2A Agents | `registry/a2a_agents.py` | Agents exposed as callable JSON-RPC services |
| Tools | `registry/tools.py` | MCP servers and function tool definitions |
| Models | `registry/models.py` | Approved LLM models with provider mappings |
| Prompts | `registry/prompts.py` | Versioned prompt templates |
| Providers | `registry/providers.py` | LLM provider configurations |
| MCP Servers | `registry/mcp_servers.py` | Available MCP server integrations |
| Templates | `registry/templates.py` | Agent/orchestration starter templates |
| Deploys | `registry/deploys.py` | Deployment job history and status |
| Marketplace | (via marketplace routes) | Published community agents and tools |

### Connectors (`connectors/`)

Plugin system for ingesting external resources into the registry. Each connector implements `BaseConnector`:

```python
class BaseConnector(ABC):
    def scan(self) -> list[RegistryEntry]
    def is_available(self) -> bool
```

| Connector | Path | Purpose |
|---|---|---|
| LiteLLM | `connectors/litellm/` | Discover models from a LiteLLM proxy; register with cost metadata |
| OpenRouter | `connectors/openrouter/` | Discover 200+ models via OpenRouter API |
| MCP Scanner | `connectors/mcp_scanner/` | Auto-discover MCP servers running on the local system |

### MCP Sidecar (`engine/deployers/mcp_sidecar.py`)

When an agent's `agent.yaml` references MCP servers from the registry, the MCP sidecar deployer injects the MCP server as a companion container at deploy time. The agent container connects to MCP tools via a local socket — no code changes required in the agent itself.

This is distinct from the planned observability sidecar (see Roadmap section below).

### LLM Providers (`engine/providers/`)

Four native providers with a unified interface:

| Provider | File | Key Features |
|---|---|---|
| Anthropic | `anthropic_provider.py` | Tool use, extended thinking, prompt caching, batch API |
| OpenAI | `openai_provider.py` | Function calling, vision, code interpreter |
| Google | `google_provider.py` | Gemini models, Vertex AI, tool calling |
| Ollama | `ollama_provider.py` | Local inference, auto-detection, custom models |

The provider registry (`engine/providers/registry.py`) manages fallback chains: if the primary provider is unavailable or rate-limited, the engine automatically retries with the configured fallback.

### Secrets Backends (`engine/secrets/`)

Four pluggable backends with a unified interface:

| Backend | File | Use Case |
|---|---|---|
| Environment / `.env` | `env_backend.py` | Local development |
| AWS Secrets Manager | `aws_backend.py` | AWS-deployed agents |
| GCP Secret Manager | `gcp_backend.py` | GCP-deployed agents |
| HashiCorp Vault | `vault_backend.py` | Enterprise multi-cloud |

---

## Multi-Agent Orchestration

The orchestration engine (`engine/orchestrator.py`) coordinates multiple agents according to a strategy defined in `orchestration.yaml` or the Python/TS SDK.

**6 execution strategies:**

| Strategy | Description |
|---|---|
| `sequential` | Agents run in order; output of each is input to the next |
| `parallel` | All agents run concurrently; results are merged |
| `router` | An LLM or rule-based classifier routes each request to the right agent |
| `hierarchical` | A supervisor agent delegates to and synthesizes from sub-agents |
| `supervisor` | Supervisor pattern with explicit approval at each step |
| `fan_out_fan_in` | Request fans out to N agents; results are aggregated before responding |

Orchestration config is parsed by `engine/orchestration_parser.py` and shares the same deploy pipeline as individual agents. Orchestrations appear in the registry like any other deployable entity.

**Shared state** between agents in an orchestration is backed by Redis, scoped to the orchestration session.

---

## Agent-to-Agent (A2A) Protocol

The A2A system (`engine/a2a/`) implements a JSON-RPC 2.0 protocol for inter-agent communication.

| Component | File | Purpose |
|---|---|---|
| Protocol | `engine/a2a/protocol.py` | JSON-RPC 2.0 message format and routing |
| Client | `engine/a2a/client.py` | Call remote agents from within an agent |
| Server | `engine/a2a/server.py` | Expose an agent as a callable A2A service |
| Auth | `engine/a2a/auth.py` | Agent cards and mutual authentication |

Agents registered as A2A services appear in the registry under `registry/a2a_agents.py` and can be discovered by other agents. The platform auto-generates typed client tool definitions so any agent can call any registered A2A agent as if it were a local function.

---

## Evaluation Framework

The evaluation system (`api/routes/evals.py`, `api/services/eval_service.py`) provides a complete CI/CD-ready eval pipeline.

**Core concepts:**
- **Datasets** — versioned collections of `(input, expected_output)` pairs
- **Runs** — execution of a dataset against a specific agent version
- **Scorers** — pluggable functions that score each output
- **Promotion Gates** — CI/CD step that blocks deploys if eval scores drop

**Built-in scorers:**

| Scorer | Type | Description |
|---|---|---|
| Correctness | Exact / fuzzy match | String match against expected output |
| Relevance | Semantic similarity | Embedding distance to expected output |
| Latency | Threshold-based | Pass/fail based on response time |
| Cost | Token-based | Flag responses above cost threshold |
| Judge | LLM-as-judge | Uses a separate model to rate response quality |

**CLI integration:**
```bash
agentbreeder eval run --dataset golden-v2 --agent customer-support
agentbreeder eval compare --run-a run-123 --run-b run-124
agentbreeder eval gate --min-score 0.85  # fails CI if below threshold
```

---

## Observability

### Tracing (`api/routes/tracing.py`)

Every LLM call, tool invocation, and agent step is captured as a **trace** containing one or more **spans**. Agents (or their sidecars, when deployed) POST traces to the platform REST API.

- **Filtering** — by agent, status, date range, duration, cost, or full-text over inputs/outputs
- **Agent metrics** — aggregated P50/P95 latency, token usage, error rate over a configurable window
- **Cleanup** — bulk-delete traces older than a given date

### Cost Tracking (`api/routes/costs.py`)

Cost events are recorded per LLM call with token counts, model, provider, and dollar cost:

- **Summary / breakdown** — aggregate spend by team, agent, model, or provider
- **Trend** — time-series daily cost data
- **Forecasting** — ML-based spend prediction
- **Anomaly detection** — flag unusual spending patterns
- **Budgets** — per-team monthly limits with configurable alert thresholds (default 80%)
- **Chargeback** — allocate costs to departments or cost centers

### Audit & Lineage (`api/routes/audit.py`)

An immutable audit log records every deploy, config change, delete, and access change with actor, action, resource, team, and timestamp.

The lineage system tracks dependencies between resources:
- **Dependency graph** — for any resource, retrieve its full dependency tree
- **Impact analysis** — "if I change this prompt, which agents are affected?"
- **Dependency sync** — automatically extract dependencies from an agent's config

### AgentOps Fleet Dashboard (`api/routes/agentops.py`)

Fleet-level visibility across all deployed agents:
- Fleet overview and performance heatmap
- Top-agent leaderboard by cost, latency, or error rate
- Real-time telemetry ingest via event streaming
- Team-vs-team performance comparison
- Incident management (create, update, track, resolve)
- Canary deployment tracking with automatic rollback triggers
- Compliance status and audit report generation

### Planned: Observability Sidecar

Every deployed agent will eventually get an auto-injected observability sidecar that provides OpenTelemetry traces, token counting, guardrail enforcement (PII detection, content filtering), and a `/health` endpoint — with zero changes to agent code.

**Status: Not yet implemented.** Observability is currently handled via the tracing API (`api/routes/tracing.py`) and agent-side instrumentation.

---

## Memory & RAG

### Memory (`api/routes/memory.py`)

Four pluggable backends for conversation state:

| Backend | Use Case |
|---|---|
| In-memory | Ephemeral, single-session agents |
| SQLite | Local development, single-instance |
| PostgreSQL | Shared state, production multi-instance |
| Vector DB | Semantic search over conversation history |

The memory service supports message storage, retrieval, semantic search, and automatic conversation summarization for long sessions.

### RAG (`api/routes/rag.py`)

Knowledge base indexing with multiple chunking strategies:

| Chunker | Strategy |
|---|---|
| Fixed-size | Uniform chunks with configurable overlap |
| Recursive | Semantic boundary detection |
| Token-aware | LLM-aware chunking based on token budget |

Hybrid search (vector + full-text) over indexed documents. Supported document types: PDF, TXT, MD, JSON. Ingestion runs as async background jobs with status polling.

---

## Git Workflow & Approvals

The git workflow (`api/routes/git.py`) provides a PR-based change management flow for agent configuration:

```
agentbreeder submit    # Create a PR for a config change
agentbreeder review    # Review pending PRs (list, show, comment)
agentbreeder publish   # Merge an approved PR and deploy
```

The API supports full branch management, commit diffs, PR create/approve/reject/merge, and conflict detection. This is the governance layer for teams that require peer review before a config change goes to production.

---

## Marketplace & Templates

The marketplace (`api/routes/marketplace.py`) is a community catalog for sharing agents, tools, and orchestrations:

- **Browse and search** — discover published resources by tag, framework, or use case
- **Listings CRUD** — submit, update, and manage published resources
- **Ratings and reviews** — community quality signal
- **One-click install** — pull a marketplace template into your registry

Agent templates (`registry/templates.py`) are pre-configured `agent.yaml` starting points for common use cases. The `agentbreeder template use` command instantiates a template into a new project.

---

## Data Model

```
Agent ──references──> Tool          (many-to-many)
  │                   Model         (many-to-one primary, optional fallback)
  │                   Prompt        (many-to-many)
  │                   KnowledgeBase (many-to-many)
  │                   MCP Server    (many-to-many)
  │
  ├── belongs to ──> Team
  ├── deployed as ──> Deploy (job history)
  └── exposed as ──> A2AAgent (optional)

Orchestration ──references──> Agent (many-to-many)
  └── belongs to ──> Team
```

Storage: PostgreSQL with SQLAlchemy ORM. Migrations via Alembic (`alembic/`).

---

## API Layer

FastAPI with async handlers throughout. All responses follow a consistent envelope:

```json
{
  "data": { ... },
  "meta": { "page": 1, "total": 42 },
  "errors": []
}
```

**Route modules (25 total):**

```
api/routes/
├── agents.py          # Agent CRUD, clone, validate, search
├── a2a.py             # A2A agent registry, invocation, agent cards
├── orchestrations.py  # Orchestration CRUD, deploy, execute
├── deploys.py         # Deploy jobs, logs, rollback
├── evals.py           # Datasets, runs, scorers, comparison, promotion gates
├── providers.py       # Provider CRUD, health check, model discovery
├── gateway.py         # Model gateway status, cost comparison, proxy logs
├── costs.py           # Cost events, analytics, budgets, forecasting
├── agentops.py        # Fleet dashboard, incidents, canary, compliance
├── tracing.py         # Span ingestion, metrics, trace search
├── audit.py           # Audit log, lineage, impact analysis
├── teams.py           # Team CRUD, membership, API keys
├── prompts.py         # Prompt CRUD, versioning, test panel
├── rag.py             # RAG indexes, ingestion, hybrid search
├── memory.py          # Memory configs, conversation storage
├── mcp_servers.py     # MCP server registry
├── templates.py       # Agent template management
├── marketplace.py     # Community marketplace, listings, reviews
├── git.py             # Branch management, PRs, diffs, merge
├── builders.py        # YAML import/export/edit
├── sandbox.py         # Isolated Python tool execution
├── playground.py      # Interactive agent chat, eval case capture
├── registry.py        # Cross-entity registry search
├── auth.py            # Login, register, JWT
└── v2/agents.py       # API v2 agents (enhanced filtering, preview)
```

**API versioning** (`api/versioning.py`): v1 is stable. v2 routes are in preview and may change. Deprecation headers are added automatically when a v1 endpoint has a v2 equivalent.

---

## Full Code SDK

The Python SDK (`sdk/python/agenthub/`) exposes a builder-pattern API for the Full Code tier:

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

result = agent.deploy()
```

Key classes: `Agent`, `Tool`, `Model`, `Memory`, `DeployConfig`, `Orchestration`.

The SDK supports full YAML round-trip: `agent.to_yaml()` serializes to valid `agent.yaml`; `Agent.from_yaml()` loads YAML back into SDK objects.

The TypeScript SDK (`sdk/typescript/`) exposes an equivalent API surface for Node.js and browser-based tooling.

---

## Design Principles

1. **Governance is a side effect** — deploying through AgentBreeder automatically creates RBAC records, cost attribution, audit entries, and registry listings. No separate governance setup.

2. **Framework-agnostic** — no framework-specific logic outside `engine/runtimes/`. The rest of the system treats all frameworks identically.

3. **Multi-cloud** — no cloud-specific logic outside `engine/deployers/`. AWS, GCP, Azure, and Kubernetes are equal first-class targets.

4. **Registry consistency** — all writes go through registry service classes. No direct database access from application code.

5. **The deploy pipeline is sacred** — the 8-step flow is the product. Protect it like an API contract. Never skip a step. Never break atomicity.

6. **Three tiers, one pipeline** — No Code, Low Code, and Full Code all compile to the same internal format. The deploy pipeline, governance, observability, and registry are tier-agnostic. This applies to both agent development and multi-agent orchestration.

7. **Tier mobility** — users can move between tiers without losing work. No Code generates valid YAML. Low Code can be scaffolded to SDK code (`agentbreeder eject`). This prevents lock-in at any abstraction level.

---

*See [CLAUDE.md](CLAUDE.md) for coding standards, the full `agent.yaml` specification, and development commands.*
*See [ROADMAP.md](ROADMAP.md) for the release plan and milestone status.*
