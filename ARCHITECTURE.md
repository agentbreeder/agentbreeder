# Architecture

> Agent Garden is a deployment platform. A developer writes `agent.yaml`, runs `garden deploy`, and the platform handles container building, infrastructure provisioning, governance, and registry registration.

---

## System Overview

```
Developer                    Agent Garden Platform                     Cloud

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
| CLI | Python, Typer, Rich | Developer interface — `garden init`, `garden deploy`, `garden list` |
| API Server | Python 3.11+, FastAPI | REST API — async, OpenAPI auto-docs |
| Engine | Python | Core deploy pipeline — config parsing, container building, cloud provisioning |
| Registry | PostgreSQL, SQLAlchemy | Catalog of all agents, tools, models, prompts, knowledge bases |
| Queue | Redis | Task queue for async deploy jobs + rate limiting |
| Dashboard | React 18, TypeScript, Tailwind, Vite | Web UI for browsing and managing the registry |
| SDKs | Python + TypeScript | Programmatic access to the platform |

---

## The Deploy Pipeline

This is the core of Agent Garden. Every `garden deploy` executes these steps in order. Each step is atomic — if any step fails, the entire deploy rolls back.

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
1. `garden deploy` (primary path)
2. Connectors (passive ingestion from external tools)
3. `garden register` (manual operator override)

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

GET    /api/v1/governance/costs       # Cost data
GET    /api/v1/governance/audit       # Audit trail
```

---

## Design Principles

1. **Governance is a side effect** — deploying through Agent Garden automatically creates RBAC records, cost attribution, audit entries, and registry listings. There is no separate governance setup.

2. **Framework-agnostic** — no framework-specific logic outside `engine/runtimes/`. The rest of the system treats all frameworks identically.

3. **Multi-cloud** — no cloud-specific logic outside `engine/deployers/`. AWS and GCP are equal first-class citizens.

4. **Registry consistency** — all writes go through registry service classes. No direct database access from application code.

5. **The deploy pipeline is sacred** — the 8-step flow is the product. Protect it like an API contract. Never skip a step. Never break atomicity.

---

*See [CLAUDE.md](CLAUDE.md) for complete coding standards, the full `agent.yaml` specification, and development commands.*
