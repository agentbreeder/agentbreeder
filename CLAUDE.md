# CLAUDE.md — AgentHub AI Development Guide

> This file instructs Claude (and any AI coding assistant) how to work on the AgentHub codebase.
> Keep this file updated as the project evolves. It is the single source of truth for AI-assisted development.

---

## 🧠 What is AgentHub?

AgentHub is an **open-source platform** for building, deploying, and governing enterprise AI agents.

**Core tagline:** Define Once. Deploy Anywhere. Govern Automatically.

**The one-sentence pitch:** A developer writes one `agenthub.yaml` file, runs `agenthub deploy`, and their agent is live on AWS or GCP — with RBAC, cost tracking, audit trail, and org-wide discoverability automatic and zero extra work.

**What makes it unique:**
- Framework-agnostic (LangGraph, CrewAI, Claude SDK, OpenAI Agents, Google ADK, Custom)
- Multi-cloud first (AWS ECS/Lambda/EKS and GCP Cloud Run/GKE as equal first-class targets)
- Governance is a **side effect** of deploying, not extra configuration
- Shared org-wide registry for agents, prompts, tools/MCP servers, models, knowledge bases
- Three builder tiers: Full Code (CLI), Low Code (visual), No Code (templates)

---

## 📁 Project Structure

```
agenthub/
├── api/                        # FastAPI backend server
│   ├── routes/                 # REST endpoints (agents, prompts, tools, models, etc.)
│   ├── services/               # Business logic layer
│   ├── models/                 # SQLAlchemy DB models + Pydantic schemas
│   └── governance/             # RBAC engine, policy evaluation
├── cli/                        # CLI tool (built with Typer)
│   ├── commands/
│   │   ├── init.py             # agenthub init
│   │   ├── deploy.py           # agenthub deploy (the core command)
│   │   ├── search.py           # agenthub search
│   │   ├── list.py             # agenthub list
│   │   └── describe.py         # agenthub describe
│   └── config.py
├── sdk/
│   ├── python/                 # pip install agenthub-sdk
│   └── typescript/             # npm install @agenthub/sdk
├── engine/                     # Core deployment pipeline
│   ├── config_parser.py        # YAML parsing + JSON Schema validation
│   ├── resolver.py             # Dependency resolution from registry
│   ├── builder.py              # Container image builder (per framework)
│   ├── deployers/
│   │   ├── base.py             # Abstract deployer interface
│   │   ├── kubernetes.py       # Generic K8s / Docker Compose
│   │   ├── aws_ecs.py          # AWS ECS Fargate
│   │   ├── aws_lambda.py       # AWS Lambda
│   │   ├── aws_eks.py          # AWS EKS
│   │   ├── gcp_cloudrun.py     # GCP Cloud Run
│   │   ├── gcp_gke.py          # GCP GKE
│   │   └── gcp_functions.py    # GCP Cloud Functions
│   ├── runtimes/               # Framework-specific container builders
│   │   ├── base.py             # Runtime builder interface
│   │   ├── langgraph.py
│   │   ├── crewai.py
│   │   ├── claude_sdk.py
│   │   ├── openai_agents.py
│   │   └── google_adk.py
│   ├── sidecar/                # Observability sidecar (runs alongside every agent)
│   └── governance.py           # RBAC validation at deploy time
├── connectors/                 # Integration plugins (pluggable)
│   ├── base.py
│   ├── litellm/
│   ├── portkey/
│   ├── langsmith/
│   ├── opentelemetry/
│   └── mcp_scanner/
├── registry/                   # Catalog service
│   ├── agents.py
│   ├── prompts.py
│   ├── tools.py
│   ├── models.py
│   └── knowledge_bases.py
├── dashboard/                  # React + TypeScript web UI
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   └── lib/
│   └── package.json
├── templates/                  # No-code agent templates
│   ├── customer-support/
│   ├── document-analyzer/
│   └── data-monitor/
├── deploy/
│   ├── docker-compose.yml      # Local development
│   └── helm/                   # Kubernetes Helm chart
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── examples/
    ├── langgraph-agent/
    ├── crewai-agent/
    ├── claude-sdk-agent/
    ├── openai-agents-agent/
    └── google-adk-agent/
```

---

## 🛠️ Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend API | Python 3.11+, FastAPI | Async, OpenAPI auto-docs |
| Database | PostgreSQL + SQLAlchemy | Alembic for migrations |
| Cache / Queue | Redis | Task queue + rate limiting |
| CLI | Python, Typer | Rich for terminal output |
| Python SDK | Python 3.11+ | pip install agenthub-sdk |
| TypeScript SDK | TypeScript 5.0+ | npm install @agenthub/sdk |
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
Every `agenthub deploy` MUST:
- Validate RBAC before doing anything
- Register the agent in the registry after success
- Attribute cost to the deploying team
- Write an audit log entry

There is no "quick deploy" mode that skips governance. This is intentional.

### 3. The Sidecar Pattern
Every deployed agent gets a sidecar container injected automatically. The sidecar provides:
- OpenTelemetry traces for every LLM call, tool use, and agent step
- Token counting and cost attribution
- Guardrail enforcement (PII detection, content filtering)
- Health check endpoint

The sidecar must never require changes to agent code. It is injected at the container build step.

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
1. `agenthub deploy` (primary path)
2. Connectors (secondary, passive ingestion)
3. Manual `agenthub register` (operator override)

Never write directly to registry tables from application code. Always go through `registry/` services.

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
agenthub --help   # after pip install -e .

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

## 📝 The `agenthub.yaml` Specification

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
  cloud: aws                          # Required. One of: aws | gcp | kubernetes | local
  runtime: ecs-fargate                # Optional. Defaults per cloud:
                                      #   aws → ecs-fargate
                                      #   gcp → cloud-run
                                      #   kubernetes → deployment
                                      #   local → docker-compose
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
```

---

## 🔌 MCP Servers in Use

AgentHub uses MCP servers for development tooling. These are configured in `.mcp.json` at the repo root.

### Active MCP Servers

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/agenthub"],
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
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/agenthub"],
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
7. **Never break the `agenthub deploy` happy path** — it is the product; protect it like an API contract
8. **Never merge without tests** — CI blocks PRs with < 80% coverage on changed files

---

## 🎯 When Adding a New Feature

1. **Check the ROADMAP.md** — is this feature planned? Which milestone?
2. **Check AGENT.md** — which AI skills/agents can help build it?
3. **Use `sequential-thinking` MCP** — plan before coding for anything > 100 lines
4. **Write the test first** — TDD is strongly preferred for engine and API code
5. **Update the JSON Schema** — if you changed `agenthub.yaml` fields
6. **Update the docs** — if you changed a public API or CLI command
7. **Add an example** — if you added a new framework or deployer, add it to `examples/`

---

## 🌐 API Conventions

```
GET    /api/v1/agents                 # List agents (paginated, filterable)
GET    /api/v1/agents/{id}            # Get agent detail
POST   /api/v1/agents                 # Create/register agent
PUT    /api/v1/agents/{id}            # Update agent
DELETE /api/v1/agents/{id}            # Soft-delete (archive)

POST   /api/v1/deploy                 # Trigger a deployment
GET    /api/v1/deploy/{job_id}        # Poll deploy status
DELETE /api/v1/deploy/{job_id}        # Cancel in-progress deploy

GET    /api/v1/registry/tools         # List tools/MCP servers
GET    /api/v1/registry/prompts       # List prompt templates
GET    /api/v1/registry/models        # List approved models
GET    /api/v1/registry/knowledge-bases

GET    /api/v1/governance/costs       # Cost data (filterable by team/agent/model/date)
GET    /api/v1/governance/audit       # Audit trail
GET    /api/v1/governance/lineage/{agent_id}
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
DATABASE_URL=postgresql://user:pass@localhost/agenthub
REDIS_URL=redis://localhost:6379
SECRET_KEY=<random-256-bit-key>
AGENTHUB_ENV=development

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
- [ ] Does this change the `agenthub.yaml` schema? If yes, update JSON Schema + docs.
- [ ] Does this change the registry schema? If yes, write a migration.
- [ ] Does this add a new deployer or runtime? If yes, add it to the supported stack matrix in README.
- [ ] Does this change a CLI command? If yes, update `agenthub --help` output in docs.

---

*Last updated: March 2026 — AgentHub v0.1*
