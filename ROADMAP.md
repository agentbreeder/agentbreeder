# ROADMAP.md — AgentHub Release Plan

> **Guiding principle:** Ship something real that solves one problem perfectly before expanding scope.
> The first release must create a "wow" moment: `agenthub deploy` works end-to-end.

---

## 🗺️ Release Overview

| Release | Name | Target | Theme | Status |
|---------|------|--------|-------|--------|
| **v0.1** | Foundation | Month 3 | CLI deploys LangGraph to K8s + Registry | 🚧 In Progress |
| **v0.2** | Multi-Cloud | Month 6 | AWS + GCP + 5 frameworks + Low Code | 📅 Planned |
| **v0.3** | Governance | Month 9 | RBAC + Cost Intelligence + Audit | 📅 Planned |
| **v0.4** | Marketplace | Month 12 | Community + A2A + Enterprise | 📅 Planned |
| **v1.0** | GA | Month 15 | Production-hardened, SOC2-ready | 📅 Planned |

---

## 🎯 v0.1 — "Foundation" (First Public Release)

> **Release criterion:** A developer can clone the repo, run `docker compose up`, write an `agenthub.yaml` for a LangGraph agent, run `agenthub deploy --target local`, and see their agent running with a registry entry automatically created — in under 15 minutes.

**Release name:** Foundation
**Target date:** Month 3 from project start
**Git tag:** `v0.1.0`

### ✅ Milestone 1: Zero-to-Deploy (Weeks 1–3) — COMPLETE

The absolute core. Nothing else matters until this works perfectly.

#### 1.1 — YAML Config Engine
- [x] `agenthub.yaml` JSON Schema spec (all fields documented)
- [x] Config parser with full validation (`engine/config_parser.py`)
- [x] Helpful validation errors (point to the exact line, suggest fixes)
- [x] Schema versioning support (`spec_version: v1`)
- [x] Unit tests: 100% coverage on parser
- [ ] Example configs for 5 different agent types in `examples/`

#### 1.2 — Python SDK
- [x] `AgentConfig` Pydantic model matching YAML schema
- [ ] `pip install agenthub-sdk` installable package
- [ ] `AgentHubClient` with `deploy()`, `list()`, `describe()` methods
- [ ] SDK connects to the API server (configurable base URL)
- [ ] Authentication via API key
- [ ] Full type hints and docstrings
- [ ] Unit tests: 90%+ coverage

#### 1.3 — LangGraph Runtime Builder
- [x] `engine/runtimes/langgraph.py` implementing `RuntimeBuilder` interface
- [x] Generates a working Dockerfile for LangGraph agents
- [x] Handles Python dependency resolution (reads requirements.txt or pyproject.toml)
- [x] Supports LangGraph v0.2+ API
- [ ] Injects the AgentHub sidecar into the container
- [ ] Working example in `examples/langgraph-agent/`
- [ ] E2E test: build a container from the example and verify it starts

#### 1.4 — Local Docker Compose Deployer
- [x] `engine/deployers/docker_compose.py` (Docker Compose deployer)
- [x] Deploy a LangGraph agent to local Docker
- [x] Agent is accessible at `http://localhost:{port}/agents/{name}/invoke`
- [x] Health check endpoint responds at `/health`
- [x] Logs accessible via deployer `get_logs()`

---

### ✅ Milestone 2: Registry & Discoverability (Weeks 4–5) — COMPLETE

The governance side effect. Every deploy must populate the registry automatically.

#### 2.1 — Core Registry
- [x] PostgreSQL schema for: Agents, Tools, Models, Prompts, KnowledgeBases
- [x] Alembic migrations for all tables
- [x] Registry service classes (CRUD + search)
- [x] Auto-registration after every successful deploy
- [x] `garden list agents` — CLI command
- [x] `garden describe agent [name]` — CLI command
- [x] `garden search [query]` — basic keyword search
- [x] FastAPI REST API: agents CRUD, tools CRUD, cross-entity search

#### 2.2 — MCP Server Auto-Discovery
- [x] `connectors/mcp_scanner/` — scans for local MCP servers
- [x] Reads MCP server schemas via the MCP protocol
- [x] Populates the Tools registry with discovered servers
- [x] Runs as a background task (configurable interval)
- [x] `garden list tools` — shows discovered MCP servers

#### 2.3 — LiteLLM Gateway Connector
- [x] `connectors/litellm/` — connects to a local LiteLLM instance
- [x] Registers available models in the Models registry
- [x] Passively captures cost data from LiteLLM logs
- [x] Config: `LITELLM_BASE_URL` environment variable

---

### ✅ Milestone 3: The CLI Experience (Week 6) — COMPLETE

The CLI is the product for v0.1. It must be excellent.

#### 3.1 — CLI Commands (Full Set)
- [x] `garden init` — scaffold a new agent project (interactive wizard)
- [x] `garden validate` — validate an agent.yaml without deploying
- [x] `garden deploy` — full deploy pipeline with rich progress output
- [x] `garden list` — list agents/tools/models/prompts (subcommands)
- [x] `garden describe` — show full detail for any registry entity
- [x] `garden search` — search across all registry entities
- [x] `garden logs` — tail logs from a deployed agent (--follow, --since, --lines)
- [x] `garden status` — show deploy status (single agent detail or all agents summary)
- [x] `garden teardown` — remove a deployed agent (--force, confirmation prompt)
- [x] `garden scan` — discover MCP servers and LiteLLM models

**CLI UX delivered:**
- [x] Rich progress bars for deploy
- [x] Colored output (green=success, red=error, yellow=warning)
- [x] `--json` flag on every command
- [x] `--help` with examples on every command
- [x] Meaningful error messages with suggestions

#### 3.2 — `garden init` — The Magic Onboarding Command
- [x] Interactive wizard: "What framework? (LangGraph / CrewAI / Claude SDK / OpenAI / ADK / Custom)"
- [x] "What cloud? (Local / AWS / GCP / Kubernetes)"
- [x] Creates a complete project scaffold:
  - `agent.yaml` (pre-filled based on answers)
  - `agent.py` (working example agent for the chosen framework)
  - `requirements.txt`
  - `.env.example`
  - `README.md` (instructions for this specific project)
- [x] Runs `garden validate` automatically after creation
- [x] Prints next steps with exact commands to run
- [x] Input validation (agent name, email) with reprompt
- [x] Git email auto-detection for owner default

---

### 🚧 Milestone 4: Basic Dashboard (Week 7–8) — IN PROGRESS

A read-only dashboard to browse the registry. Not feature-complete — just good enough to show value.

#### ✅ 4.1 — Dashboard Foundation — COMPLETE
- [x] React 19 + TypeScript 5.9 + Tailwind v4 setup (Vite 7)
- [x] shadcn/ui component library (button, badge, input, table, dialog, separator, dropdown-menu, avatar, tooltip, tabs, card)
- [x] Geist font (Vercel aesthetic), oklch color space, dark mode CSS variables
- [x] Path aliases (@/\*), Vite proxy to API (localhost:8000)
- [x] React Router, TanStack Query, Lucide icons
- [x] shadcn/ui + Magic UI MCP servers configured for dev tooling
- [x] App shell: sidebar navigation, breadcrumbs, command search (⌘K)
- [x] Home overview page: stats cards (agents, tools, running), recent agents list
- [x] Agent list page: searchable, filterable by team/status/framework, status indicators
- [x] Agent detail page: overview tab (config, metadata, endpoint copy), deploy history + logs tabs (placeholder)
- [x] Tool registry page: grid cards with type/source filters, expandable endpoint details
- [x] Global search page with cross-entity results (agents + tools)
- [x] Dark mode toggle (3-way: dark/light/system with localStorage persistence)
- [x] Responsive design (desktop-first, mobile-functional)
- [x] Typed API client (`src/lib/api.ts`) with React Query integration
- [x] Placeholder pages for Models, Prompts, Deploys (ready for 4.2)
- [x] Playwright E2E tests: 16 tests covering shell, agents, tools pages

**Design reference:** Linear.app / Vercel dashboard aesthetic. Professional, not consumer.

#### 4.2 — Dashboard: Deploy Status & Registry Pages
- [x] Real-time deploy status (polling with 10s auto-refresh)
- [x] Deploy progress visualization (8-step pipeline with icons: Parse→Build→Provision→Deploy→Health→Register→Done)
- [x] Deploy history for each agent (agent detail tab, expandable rows)
- [x] Error display with actionable messages (inline error banners on failed steps)
- [x] Models registry page (table with provider badges, source icons, filters)
- [x] Prompts registry page (card grid grouped by name, expandable version history with content preview)
- [x] Deploys page (global deploy list with status/target filters, expandable pipeline view)
- [x] Backend: registry services + API routes for models, prompts, deploy jobs
- [x] Cross-entity search extended to include models and prompts
- [x] Playwright E2E tests: 40 tests covering all pages + auth
- [x] Authentication (login page, JWT handling, route guards) — M4.3
- [ ] Link to cloud console for deployed resource — deferred to M4.4

**Done when:** A non-engineer can open the dashboard and understand what agents are deployed and their status.

---

### 🚧 Milestone 5: v0.1 Release Readiness (Week 9–10) — IN PROGRESS

Everything needed to ship publicly.

#### 5.1 — Documentation
- [x] `README.md` — complete with badges, quick start, architecture diagram
- [x] `docs/quickstart.md` — 10-minute tutorial from zero to deployed agent
- [x] `docs/agenthub-yaml.md` — complete field reference
- [x] `docs/cli-reference.md` — every command documented
- [x] `docs/local-development.md` — contributor setup guide
- [x] `CONTRIBUTING.md` — how to contribute (deployers, runtimes, connectors, templates)
- [ ] `docs/examples/` — 3 complete end-to-end tutorials

#### 5.2 — CI/CD & Quality Gates
- [x] GitHub Actions: lint (ruff, mypy) on every PR
- [x] GitHub Actions: unit tests on every PR (must pass)
- [x] GitHub Actions: E2E tests (Playwright)
- [x] GitHub Actions: Docker build verification
- [x] PR template with checklist
- [ ] Branch protection: require review + CI pass
- [x] Code coverage badge (target: 80%+) + Codecov integration

#### 5.3 — Community Infrastructure
- [ ] GitHub Discussions enabled
- [x] Issue templates: Bug Report, Feature Request, New Deployer, New Framework
- [ ] Discord server with channels: #general, #help, #contribute, #releases, #show-and-tell
- [ ] Weekly office hours (async, Discord)
- [x] `SECURITY.md` — responsible disclosure policy

#### 5.4 — v0.1 Launch Assets
- [ ] Hacker News launch post (draft)
- [ ] Twitter/X launch thread (draft)
- [ ] Demo video (3–5 min): `agenthub init` → `agenthub deploy` → dashboard walkthrough
- [ ] GitHub social preview image
- [ ] DockerHub published images

### v0.1 Release Checklist
- [ ] All unit tests passing (100%)
- [ ] All integration tests passing
- [ ] `agenthub init → deploy` works in < 15 min on a fresh machine
- [ ] Docker images published to DockerHub
- [ ] Helm chart published
- [ ] Docs site live (GitHub Pages or Mintlify)
- [ ] Release notes written
- [ ] Discord server live
- [ ] Demo video recorded and published

---

## 🚀 v0.2 — "Multi-Cloud" (Month 6)

> **Release criterion:** A developer can deploy any of 5 supported frameworks to AWS ECS Fargate or GCP Cloud Run with one command, and see their agent in the dashboard.

### New in v0.2

#### Cloud Deployers
- [ ] **AWS ECS Fargate** — primary AWS target
  - Task definition, ECS service, ALB, IAM roles, VPC networking
  - Auto-scaling (CPU-based + request-based)
  - Secrets Manager integration for env secrets
- [ ] **AWS Lambda** — for lightweight/event-driven agents
  - Lambda function + API Gateway
  - DynamoDB for state persistence
  - EventBridge triggers
- [ ] **AWS EKS** — for existing K8s users
  - Helm chart generation
  - Workload Identity (IRSA)
  - HPA + KEDA for scaling
- [ ] **GCP Cloud Run** — primary GCP target
  - Auto-scaling (including scale-to-zero)
  - Cloud Load Balancing
  - Workload Identity
  - Artifact Registry integration
- [ ] **GCP GKE** — for existing GKE users
  - GKE Autopilot support
  - Workload Identity
  - Cloud Operations integration
- [ ] **GCP Cloud Functions** — for lightweight/event-driven agents

#### Framework Runtimes (Complete the Set)
- [ ] **CrewAI** runtime builder + example
- [ ] **Claude SDK** (Anthropic) runtime builder + example
- [ ] **OpenAI Agents SDK** runtime builder + example
- [ ] **Google ADK** runtime builder + example
- [ ] **Custom** runtime (any Python/TS agent with SDK wrapper)

#### TypeScript SDK
- [ ] `npm install @agenthub/sdk`
- [ ] Full parity with Python SDK
- [ ] TypeScript types for all schemas
- [ ] Node.js and Deno support

#### Low-Code Visual Builder (Beta)
- [ ] Drag-and-drop canvas in dashboard
- [ ] Component library from registry (models, tools, prompts, KBs)
- [ ] Multi-agent flow wiring
- [ ] Generates `agenthub.yaml` from canvas
- [ ] Deploy directly from the visual builder

#### Registry Enhancements
- [ ] Prompt registry with versioning
  - Version history with diffs
  - Eval scores per version
  - A/B variant support
- [ ] Tool/MCP registry with health monitoring
  - Uptime tracking
  - Schema version history
  - Usage statistics
- [ ] Semantic search across all registry entities (pgvector)

---

## 🛡️ v0.3 — "Governance" (Month 9)

> **Release criterion:** A platform engineer can configure team-level RBAC policies, see per-team AI spend, review the complete audit trail, and get impact alerts when a shared prompt changes.

### New in v0.3

#### RBAC Engine (Full)
- [ ] Policy-as-code (YAML policy files, version-controlled)
- [ ] Team → Resource access matrix
- [ ] Role definitions: Viewer, Contributor, Deployer, Admin
- [ ] Approval workflows for production deploys
- [ ] Access request + grant flow
- [ ] Cross-team resource sharing with explicit grants

#### Cost Intelligence
- [ ] Per-agent daily/weekly/monthly cost breakdown
- [ ] Per-team cost dashboards with budget alerts
- [ ] Per-model cost comparison (side-by-side)
- [ ] Cost optimization recommendations ("switch to Claude Haiku to save 60%")
- [ ] Budget alerts (email + Slack webhook)
- [ ] Cost anomaly detection

#### Lineage & Impact Analysis
- [ ] Lineage graph visualization (which agents use which tools/prompts/models)
- [ ] Forward impact analysis ("if I update prompt v3 → v4, these 12 agents are affected")
- [ ] Dependency graph for every registry entity
- [ ] Change notification system (subscribe to changes on assets you depend on)

#### Audit Trail
- [ ] Immutable audit log for all actions (deployments, config changes, access changes)
- [ ] SIEM export (webhook + syslog format)
- [ ] Compliance reports (who deployed what, when, with what permissions)
- [ ] Retention policies (configurable, default 2 years)

#### No-Code Template Builder
- [ ] Template creation UI (parameterize existing agents as templates)
- [ ] Built-in templates: Customer Support, Document Analyzer, Data Monitor, Code Review
- [ ] Template marketplace (community submissions)
- [ ] One-click deploy from template

#### Evaluation Pipeline Integration
- [ ] Connect to Langsmith/Braintrust for eval scores
- [ ] Eval scores visible in registry and prompt history
- [ ] Regression detection on prompt updates
- [ ] Promotion gate: require eval pass before production deploy

---

## 🌐 v0.4 — "Marketplace" (Month 12)

> **Release criterion:** Community contributors have published 20+ agent templates, A2A communication works between deployed agents, and enterprise SSO is available.

### New in v0.4

#### Community Marketplace
- [ ] Community agent template submissions (PR-based workflow)
- [ ] Template ratings and reviews
- [ ] Verified publisher badges
- [ ] Template versioning and deprecation
- [ ] "One-click deploy" from marketplace listing

#### Agent-to-Agent (A2A) Communication
- [ ] A2A protocol implementation (agent discovery + invocation)
- [ ] Agent cards (capability advertisements per Google A2A spec)
- [ ] Secure inter-agent authentication (JWT)
- [ ] Async A2A (job-based) and sync A2A (streaming)
- [ ] A2A call graph in the lineage viewer

#### Advanced Observability
- [ ] Anomaly detection (unusual token usage, latency spikes, error rate increases)
- [ ] Custom alerting rules
- [ ] Distributed tracing dashboard (Jaeger integration)
- [ ] SLA monitoring and breach alerts
- [ ] Agent performance benchmarking

#### Duplication Detection
- [ ] Semantic similarity search across agent configs
- [ ] "Similar agents exist" warning at deploy time
- [ ] Merge suggestion workflow (combine two similar agents)
- [ ] Deduplication report for platform teams

#### Enterprise Tier (Commercial)
- [ ] SSO/SAML integration (Okta, Auth0, Azure AD)
- [ ] SCIM user provisioning
- [ ] Compliance reports (SOC2 evidence, GDPR data lineage)
- [ ] Multi-tenant isolation
- [ ] Private marketplace (org-internal templates)
- [ ] SLA-backed premium support

---

## 🏆 v1.0 — "General Availability" (Month 15)

> **Release criterion:** Production-hardened, SOC2 Type II in progress, 1,000+ GitHub stars, 10+ enterprise design partners, 99.9% uptime SLA for managed cloud.

### v1.0 Requirements
- [ ] Zero known critical or high-severity security issues
- [ ] 85%+ test coverage across codebase
- [ ] < 15 minute end-to-end deploy time (P95) for AWS and GCP
- [ ] Documentation covers 100% of public API and CLI commands
- [ ] SOC2 Type II audit started
- [ ] Public status page (status.agenthub.dev)
- [ ] Managed cloud offering (agenthub.cloud) available
- [ ] 10+ reference customers using in production
- [ ] Azure deployer available (community-contributed)

---

## 📊 Success Metrics by Release

| Metric | v0.1 | v0.2 | v0.3 | v0.4 | v1.0 |
|--------|------|------|------|------|------|
| GitHub Stars | 500 | 2,000 | 5,000 | 10,000 | 15,000 |
| Contributors | 5 | 20 | 50 | 100 | 200 |
| Production deployments | 50 | 500 | 2,000 | 5,000 | 10,000 |
| Supported frameworks | 1 (LangGraph) | 5 | 5 | 6+ | 8+ |
| Supported clouds | 1 (Local/K8s) | 3 (K8s+AWS+GCP) | 3 | 4+ | 5+ |
| Discord members | 100 | 500 | 1,500 | 3,000 | 5,000 |

---

## 🚧 What is OUT OF SCOPE (intentionally deferred)

The following are explicitly NOT in scope until v1.0 or later, to keep focus:

- ❌ Azure deployer (v0.3+ — community contribution welcome)
- ❌ Oracle Cloud, Render, Fly.io deployers (community)
- ❌ Training or fine-tuning pipelines (not AgentHub's scope)
- ❌ RAG pipeline builder (use Langflow/Dify for this)
- ❌ Custom model hosting (use TrueFoundry for this)
- ❌ Chat interface or copilot UX (we build the platform, not the UI for end users)
- ❌ Mobile app
- ❌ On-premise air-gapped deployment (enterprise tier v0.4+)

---

## 📋 Issue Labels

| Label | Meaning |
|-------|---------|
| `milestone:v0.1` | Required for first release |
| `milestone:v0.2` | Planned for v0.2 |
| `good first issue` | Approachable for new contributors |
| `help wanted` | We need community help |
| `deployer:aws` | AWS deployer work |
| `deployer:gcp` | GCP deployer work |
| `runtime:langgraph` | LangGraph runtime |
| `runtime:crewai` | CrewAI runtime |
| `area:cli` | CLI work |
| `area:dashboard` | Frontend work |
| `area:registry` | Registry/catalog work |
| `area:governance` | RBAC/audit/cost work |
| `area:docs` | Documentation |
| `type:bug` | Bug fix |
| `type:feature` | New feature |
| `type:perf` | Performance improvement |
| `priority:p0` | Blocking the current milestone |
| `priority:p1` | Important, not blocking |
| `priority:p2` | Nice to have |

---

## 🤝 How to Contribute to the Roadmap

1. Check `milestone:v0.1` issues first — these are the highest priority
2. Look for `good first issue` — these are scoped and approachable
3. Want to add a deployer or runtime? See `CONTRIBUTING.md` and `AGENT.md` for the skill guide
4. Have an idea not on the roadmap? Open a GitHub Discussion before filing an issue
5. Disagree with a priority? Comment on the issue — we value community input

---

*Last updated: March 9, 2026 — M1-M3 complete, M4.1-M4.3 complete (Dashboard + Deploy Status + Registry Pages + Authentication)*
*Roadmap is directional. Dates are targets, not commitments.*
*Follow releases on GitHub: github.com/agenthub-oss/agenthub/releases*
