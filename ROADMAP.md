# ROADMAP.md — Agent Garden

> **Vision:** The enterprise one-stop shop for Agent Marketplace, Agent Evaluation, Agent Observability, Cost Monitoring, and AgentOps.
>
> **Guiding principle:** The dashboard and registry are the product. The CLI and deploy engine are the on-ramp. Every milestone should make the UI more useful and the registry more valuable.

---

## Release Overview

| Release | Name | Theme | Milestones | Status |
|---------|------|-------|------------|--------|
| **v0.1** | Foundation | CLI + Registry + Basic Dashboard | M1–M5 | Done |
| **v0.2** | Registry UI | Rich registry pages, YAML editor, prompt manager | M6–M7 | Next |
| **v0.3** | Builders | All builders + Git workflow + approval + environments + playground + SDK examples | M8–M13 | Planned |
| **v0.4** | Observability | Tracing (Langfuse/MLflow), cost monitoring, RBAC, audit trail, lineage | M14–M17 | Planned |
| **v0.5** | Evaluation | Eval framework, golden datasets, regression detection, CI gates, feedback loop | M18 | Planned |
| **v0.6** | Connectivity | A2A protocol, MCP server hub, multi-agent orchestration | M19–M20 | Planned |
| **v0.7** | Marketplace | Community templates, ratings, one-click deploy | M21–M22 | Planned |
| **v1.0** | GA | Local Docker + Cloud Run, model catalog, SSO, AgentOps | M23–M27 | Planned |

---

## Distribution & Project Structure

### How Users Install Agent Garden

Two packages: a **CLI tool** (what developers install) and a **platform server** (what runs the dashboard + API + registry).

```
┌──────────────────────────────────────────────────────────────────┐
│                     What to install                               │
│                                                                   │
│  Solo developer / getting started:                                │
│    pip install agent-garden        ← CLI + SDK                    │
│    garden init my-project          ← scaffold project             │
│    garden dev                      ← run locally (Ollama)         │
│                                                                   │
│  Team / full platform:                                            │
│    pip install agent-garden        ← CLI + SDK (on each machine)  │
│    docker compose up               ← platform server              │
│    open http://localhost:3000      ← dashboard                    │
│                                                                   │
│  CI/CD / automation:                                              │
│    pip install agent-garden        ← CLI only (no UI needed)      │
│    garden deploy --env production  ← deploy from pipeline         │
└──────────────────────────────────────────────────────────────────┘
```

#### Package Distribution

| Package | Registry | Installs | Contains |
|---------|----------|----------|----------|
| `agent-garden` | PyPI (`pip install agent-garden`) | `garden` CLI command | CLI, SDK, engine, runtimes, config parser, provider abstraction |
| `@agent-garden/dashboard` | npm (`npm install @agent-garden/dashboard`) | Dashboard dev server | React SPA (only needed for dashboard development/customization) |
| `agent-garden` | Docker Hub / GHCR | `docker pull agentgarden/server` | Full platform: API + dashboard + workers (all-in-one image) |
| `agent-garden` | Helm chart | `helm install agent-garden` | Kubernetes deployment (API + dashboard + PostgreSQL + Redis) |

**Typical install paths:**

```bash
# Path 1: Developer — build agents locally
pip install agent-garden
garden init my-agent-project
cd my-agent-project
garden dev                              # starts local sandbox

# Path 2: Team — run the full platform
git clone https://github.com/agent-garden/agent-garden
cd agent-garden
docker compose up -d                    # starts API + dashboard + DB + Redis
pip install agent-garden                # install CLI on each dev machine
open http://localhost:3000              # dashboard

# Path 3: Production — deploy platform to cloud
helm install agent-garden deploy/helm/agent-garden \
  --set database.url=postgresql://... \
  --set redis.url=redis://...
```

#### `pip install agent-garden` — What It Includes

```
agent-garden (PyPI package)
├── garden                  ← CLI binary (entry point)
│   ├── garden init         ← scaffold new project
│   ├── garden dev          ← local dev sandbox
│   ├── garden deploy       ← deploy agent
│   ├── garden provider     ← manage LLM providers
│   ├── garden chat         ← playground chat
│   ├── garden eval         ← run evaluations
│   ├── garden submit       ← submit PR for review
│   └── ...                 ← all other commands
├── agent_garden.sdk        ← Python SDK for programmatic use
│   ├── AgentGardenClient   ← API client
│   ├── AgentConfig         ← Pydantic models
│   └── deploy(), list()... ← SDK methods
├── agent_garden.engine     ← config parser, runtimes, deployers
└── agent_garden.providers  ← LLM provider abstraction
```

```python
# SDK usage (programmatic)
from agent_garden import AgentGardenClient

client = AgentGardenClient(base_url="http://localhost:8000")
agents = client.agents.list()
client.agents.deploy("my-agent", env="staging")
```

---

### Project Directory Structure

Two project types: **single-agent** (most common starting point) and **workspace** (team with multiple agents).

#### Single-Agent Project

Created by `garden init my-agent`:

```
my-agent/
├── agent.yaml              ← agent configuration (THE source of truth)
├── prompt.md               ← system prompt (Markdown, referenced from agent.yaml)
├── agent.py                ← agent code (for full-code agents, optional for YAML-only)
├── tools/                  ← custom tools (optional)
│   ├── search.yaml         ← tool definition (YAML)
│   └── search.py           ← tool implementation (Python)
├── tests/                  ← evaluation datasets + tests
│   ├── eval_golden.jsonl   ← golden test cases
│   └── test_agent.py       ← unit tests
├── requirements.txt        ← Python dependencies
├── .env                    ← API keys (gitignored)
├── .env.example            ← API key placeholders (committed)
├── .gitignore              ← includes .env, __pycache__, .garden/
├── .garden/                ← Agent Garden metadata (gitignored)
│   ├── state.json          ← local state (current branch, sandbox port, etc.)
│   └── layout.json         ← visual builder node positions (UI-only metadata)
└── README.md               ← auto-generated docs for this agent
```

**`agent.yaml` example (single agent):**

```yaml
name: my-agent
version: "1.0.0"
description: "A helpful research assistant"
team: engineering
owner: alice@company.com

model:
  provider: ollama              # local dev — switch to anthropic for production
  name: llama3.2
  temperature: 0.7

prompt:
  system_ref: prompt.md         # reference to prompt.md file

tools:
  - ref: tools/search.yaml     # local tool
  - mcp://filesystem            # MCP server tool

memory:
  type: buffer_window
  max_messages: 20
  backend: redis                # or: postgresql, in_memory

deploy:
  target: local                 # local | cloud-run | ecs-fargate | kubernetes
  resources:
    cpu: "0.5"
    memory: "512Mi"
```

#### Workspace (Multi-Agent Project)

Created by `garden init my-workspace --workspace`, or naturally evolves when a team has multiple agents:

```
my-workspace/
├── garden.yaml                 ← workspace config (lists all agents, shared settings)
├── agents/                     ← one subdirectory per agent
│   ├── research-agent/
│   │   ├── agent.yaml
│   │   ├── prompt.md
│   │   └── agent.py
│   ├── summarizer-agent/
│   │   ├── agent.yaml
│   │   └── prompt.md
│   └── reviewer-agent/
│       ├── agent.yaml
│       └── prompt.md
├── prompts/                    ← shared prompts (reusable across agents)
│   ├── safety-guardrail.yaml
│   │   └── prompt.md
│   └── citation-format.yaml
│       └── prompt.md
├── tools/                      ← shared tools (reusable across agents)
│   ├── web-search/
│   │   ├── tool.yaml
│   │   └── search.py
│   ├── database-query/
│   │   ├── tool.yaml
│   │   └── query.py
│   └── slack-notify/
│       ├── tool.yaml
│       └── notify.py
├── rag/                        ← RAG / vector index configs
│   └── product-docs/
│       ├── rag.yaml            ← index config (source, chunking, embedding model)
│       └── sources/            ← ingestion source configs
│           ├── gdrive.yaml
│           └── database.yaml
├── memory/                     ← memory backend configs
│   └── shared-redis.yaml
├── mcp-servers/                ← custom MCP servers
│   └── internal-api/
│       ├── server.py
│       └── mcp.yaml
├── evals/                      ← evaluation datasets
│   ├── research-agent/
│   │   └── golden.jsonl
│   └── summarizer-agent/
│       └── golden.jsonl
├── deploy/                     ← deployment configs per environment
│   ├── docker-compose.yml      ← local dev (all agents + platform)
│   ├── staging.yaml            ← staging overrides
│   └── production.yaml         ← production overrides
├── .env                        ← API keys (gitignored)
├── .env.example
├── .gitignore
└── README.md
```

**`garden.yaml` (workspace root config):**

```yaml
workspace:
  name: my-team-agents
  version: "1.0.0"

# Shared defaults — inherited by all agents unless overridden
defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4.6
  memory:
    backend: redis
    url: redis://localhost:6379
  deploy:
    target: local

# Agent list — each must have an agent.yaml in its directory
agents:
  - agents/research-agent
  - agents/summarizer-agent
  - agents/reviewer-agent

# Shared resources — available for all agents to reference
shared:
  prompts: prompts/
  tools: tools/
  rag: rag/
  memory: memory/
  mcp_servers: mcp-servers/

# Environment overrides
environments:
  dev:
    defaults:
      model:
        provider: ollama
        name: llama3.2
  staging:
    defaults:
      model:
        provider: anthropic
        name: claude-sonnet-4.6
  production:
    defaults:
      model:
        provider: anthropic
        name: claude-opus-4.6
```

#### Reference System — How Agents Point to Resources

Every field that references another resource uses a **URI-style prefix** to indicate the source. Three sources: local files, the platform registry, and live services (MCP/A2A).

**Reference prefixes:**

| Prefix | Source | Example | Resolves To |
|--------|--------|---------|-------------|
| `./` or `../` | Local file (relative path) | `./tools/search.yaml` | File on disk, relative to agent.yaml |
| `registry://` | Agent Garden registry | `registry://tools/search@v1.2` | Versioned resource from the platform registry |
| `mcp://` | MCP server | `mcp://filesystem/read_file` | Tool from a running MCP server |
| `agent://` | Another agent (A2A) | `agent://summarizer@v2.0` | Agent-to-agent call via A2A protocol |
| (bare string) | Inline / shorthand | `prompt.md` | Local file in same directory (sugar for `./prompt.md`) |

**Registry reference format:**

```
registry://{resource_type}/{name}@{version}

Examples:
  registry://tools/web-search@v1.2        # specific version
  registry://tools/web-search@latest       # latest stable version
  registry://tools/web-search              # same as @latest
  registry://prompts/safety-guardrail@v3.0
  registry://rag/product-docs@v1.0
  registry://memory/shared-redis@v1.0
  registry://agents/summarizer@v2.0        # for subagent references
  registry://models/claude-sonnet-4.6      # model from model registry
  registry://mcp-servers/filesystem@v1.0   # MCP server config from registry
```

**Full `agent.yaml` example showing all reference types:**

```yaml
name: research-agent
version: "1.0.0"
description: "Research assistant that searches, summarizes, and cites"
team: engineering
owner: alice@company.com

# ── Model ─────────────────────────────────────────────────────────
model:
  # Option A: inline config (simplest)
  provider: anthropic
  name: claude-sonnet-4.6
  temperature: 0.3

  # Option B: reference from Model Registry
  # ref: registry://models/claude-sonnet-4.6

  # Option C: Ollama for local dev
  # provider: ollama
  # name: llama3.2

# ── Prompt ────────────────────────────────────────────────────────
prompt:
  # Local file (same directory)
  system_ref: prompt.md

  # Or: shared prompt from workspace
  # system_ref: ../../prompts/research-system/prompt.md

  # Or: versioned prompt from registry
  # system_ref: registry://prompts/research-system@v3.1

  # Or: inline (for quick prototyping)
  # system: "You are a research assistant. Always cite your sources."

# ── Tools ─────────────────────────────────────────────────────────
tools:
  # Local tool (file in this project)
  - ref: ./tools/custom-formatter.yaml

  # Shared tool from workspace
  - ref: ../../tools/web-search/tool.yaml

  # Tool from registry (versioned)
  - ref: registry://tools/web-search@v1.2
  - ref: registry://tools/database-query@latest

  # Tool from MCP server (live service)
  - ref: mcp://filesystem/read_file
  - ref: mcp://filesystem/write_file
  - ref: mcp://slack/send_message

  # All tools from an MCP server (wildcard)
  - ref: mcp://filesystem/*

# ── RAG / Vector Search ──────────────────────────────────────────
rag:
  # Local RAG config
  - ref: ./rag/local-docs.yaml

  # From registry
  - ref: registry://rag/product-docs@v1.0
  - ref: registry://rag/api-reference@v2.1

# ── Memory ────────────────────────────────────────────────────────
memory:
  # Inline config (simplest)
  type: buffer_window
  max_messages: 20
  backend: redis

  # Or: reference a shared memory config
  # ref: ../../memory/shared-redis.yaml

  # Or: from registry
  # ref: registry://memory/team-redis@v1.0

# ── Subagents (A2A) ──────────────────────────────────────────────
subagents:
  # Another agent in the same workspace
  - ref: ../../agents/summarizer-agent/agent.yaml
    as: summarize                    # creates a tool called "summarize"

  # Agent from registry
  - ref: registry://agents/summarizer@v2.0
    as: summarize

  # Agent by A2A endpoint (already deployed)
  - ref: agent://summarizer@v2.0
    as: summarize

  # Agent by direct URL
  - ref: https://summarizer.myteam.agentgarden.cloud
    as: summarize

# ── MCP Servers ───────────────────────────────────────────────────
mcp_servers:
  # Local MCP server (starts with the agent)
  - ref: ./mcp-servers/internal-api/mcp.yaml

  # MCP server from registry
  - ref: registry://mcp-servers/filesystem@v1.0

  # MCP server by endpoint (already running)
  - endpoint: http://localhost:9100
    name: filesystem

# ── Guardrails ────────────────────────────────────────────────────
guardrails:
  input:
    - pii_detection                          # built-in guardrail
    - ref: registry://guardrails/toxicity@v1.0  # from registry
  output:
    - hallucination_check
    - ref: ./guardrails/custom-filter.yaml   # local guardrail

# ── Deploy ────────────────────────────────────────────────────────
deploy:
  target: local
  resources:
    cpu: "0.5"
    memory: "512Mi"
```

**Reference resolution order** (when deploying/running an agent):

```
1. Parse agent.yaml
2. For each reference:
   a. Local (./  ../)    → read file from disk
   b. Registry (registry://) → fetch from platform registry API
      → if version specified: fetch that exact version
      → if @latest or no version: fetch latest stable version
      → fail if resource not found or version doesn't exist
   c. MCP (mcp://)       → connect to MCP server, verify tool exists
   d. Agent (agent://)   → resolve via A2A discovery, verify agent is deployed
3. Validate all references resolved
4. Lock versions: create a dependency lockfile (.garden/lock.yaml)
   → pinning exact versions of all registry:// references
   → ensures reproducible deploys
5. Proceed with build + deploy
```

**Dependency lockfile (`.garden/lock.yaml`):**

```yaml
# Auto-generated by garden deploy — do not edit
# Records exact versions of all registry references at deploy time
locked_at: "2026-03-11T14:30:00Z"
agent: research-agent@v1.0.0

dependencies:
  - ref: registry://tools/web-search@v1.2
    resolved: v1.2.3
    sha: abc123
  - ref: registry://prompts/research-system@v3.1
    resolved: v3.1.0
    sha: def456
  - ref: registry://rag/product-docs@v1.0
    resolved: v1.0.7
    sha: ghi789
  - ref: registry://agents/summarizer@v2.0
    resolved: v2.0.1
    endpoint: https://summarizer.staging.internal
```

**CLI commands for references:**

```bash
garden deps                               # show all references and their resolved versions
garden deps update                        # update all @latest refs to current latest
garden deps update tools/web-search       # update one reference
garden deps lock                          # regenerate lock file
garden deps check                         # verify all references still resolve (nothing deleted/broken)
```

**Dashboard UI for references:**

```
Agent Builder → any ref field shows a picker:

  ┌─────────────────────────────────────────────────┐
  │  Add Tool Reference                              │
  │                                                  │
  │  ○ Local file    [Browse project files...]       │
  │  ● Registry      [🔍 Search tools in registry ]  │
  │  ○ MCP server    [Select MCP server + tool  ]    │
  │                                                  │
  │  Registry results:                               │
  │  ┌──────────────────────────────────────────┐   │
  │  │ 🔧 web-search         v1.2.3  ★ 4.8    │   │
  │  │    Search the web via Google/Bing        │   │
  │  │    Used by: 12 agents     [Add →]        │   │
  │  │────────────────────────────────────────  │   │
  │  │ 🔧 database-query     v2.0.1  ★ 4.5    │   │
  │  │    Execute SQL queries safely            │   │
  │  │    Used by: 8 agents      [Add →]        │   │
  │  └──────────────────────────────────────────┘   │
  │                                                  │
  │  Version: [v1.2 ▼]  ☐ Pin to exact version      │
  │                      ☑ Use @latest               │
  └─────────────────────────────────────────────────┘
```

#### CLI Commands for Project Structure

```bash
# Create single-agent project
garden init my-agent
garden init my-agent --framework langgraph     # with framework preset
garden init my-agent --framework google-adk    # another framework

# Create workspace
garden init my-workspace --workspace
garden init my-workspace --workspace --agents research,summarizer,reviewer

# Add resources to existing project
garden add agent customer-support              # creates agents/customer-support/
garden add tool web-search                     # creates tools/web-search/
garden add prompt safety-guardrail             # creates prompts/safety-guardrail/
garden add rag product-docs                    # creates rag/product-docs/
garden add memory shared-redis                 # creates memory/shared-redis.yaml
garden add mcp-server internal-api             # creates mcp-servers/internal-api/

# Work with the project
garden dev                                     # start all agents in dev mode
garden dev research-agent                      # start one agent
garden deploy research-agent --env staging     # deploy one agent
garden deploy --all --env staging              # deploy all agents

# Validate everything
garden validate                                # validate all YAML files in workspace
garden validate agents/research-agent          # validate one agent
```

#### What `garden init` Generates

**Single agent (`garden init my-agent`):**

```
$ garden init my-agent

  🌱 Creating new agent project: my-agent

  ? Framework: (use arrows)
    ❯ LangGraph / DeepAgent
      Google ADK
      OpenAI Agents SDK
      CrewAI
      Claude SDK
      YAML-only (no code)

  ? Default model provider:
    ❯ Ollama (local, free — recommended for dev)
      OpenAI
      Anthropic
      Google

  ✅ Project created at ./my-agent/

  Files created:
    agent.yaml        — agent config
    prompt.md         — system prompt
    agent.py          — starter code (LangGraph)
    requirements.txt  — dependencies
    .env.example      — API key placeholders
    .gitignore        — ignores .env, .garden/, __pycache__
    tests/            — test directory with sample eval
    README.md         — getting started guide

  Next steps:
    cd my-agent
    pip install -r requirements.txt
    garden provider add ollama          # or: garden provider add openai
    garden dev                          # start local sandbox
    garden chat                         # chat with your agent
```

**Workspace (`garden init my-workspace --workspace`):**

```
$ garden init my-workspace --workspace

  🌱 Creating new workspace: my-workspace

  ? How many agents to start with: 2

  ? Agent 1 name: research-agent
  ? Agent 1 framework: Google ADK

  ? Agent 2 name: summarizer-agent
  ? Agent 2 framework: YAML-only

  ✅ Workspace created at ./my-workspace/

  Files created:
    garden.yaml                       — workspace config
    agents/research-agent/agent.yaml  — agent 1
    agents/research-agent/prompt.md
    agents/summarizer-agent/agent.yaml — agent 2
    agents/summarizer-agent/prompt.md
    tools/                            — shared tools (empty)
    prompts/                          — shared prompts (empty)
    deploy/docker-compose.yml         — local dev stack
    .env.example
    .gitignore

  Next steps:
    cd my-workspace
    garden provider add ollama
    garden dev                          # starts both agents
    open http://localhost:3000          # dashboard
```

---

## What's Built (v0.1 — COMPLETE)

### Engine & CLI (COMPLETE)

- [x] `agent.yaml` JSON Schema, config parser with validation, schema versioning
- [x] `engine/runtimes/langgraph.py` — LangGraph runtime builder (Dockerfile generation, dependency resolution)
- [x] `engine/deployers/docker_compose.py` — local Docker Compose deployer
- [x] `engine/resolver.py` — dependency resolution from registry
- [x] `engine/builder.py` — container image builder
- [x] `engine/governance.py` — RBAC validation stub at deploy time
- [x] CLI commands: `garden init`, `validate`, `deploy`, `list`, `describe`, `search`, `logs`, `status`, `teardown`, `scan`
- [x] CLI UX: Rich progress bars, colored output, `--json` flag, meaningful errors
- [x] `garden init` interactive wizard (framework + cloud selection, scaffold generation)

### Registry & Connectors (COMPLETE)

- [x] PostgreSQL schema: Agents, Tools, Models, Prompts, KnowledgeBases, Deploys
- [x] Alembic migrations
- [x] Registry service classes (CRUD + cross-entity search)
- [x] Auto-registration after successful deploy
- [x] `connectors/mcp_scanner/` — MCP server auto-discovery
- [x] `connectors/litellm/` — LiteLLM gateway connector (model + cost ingestion)

### API (COMPLETE)

- [x] FastAPI backend with auth (JWT + bcrypt)
- [x] Routes: agents, tools, models, prompts, deploys, registry, auth
- [x] Pydantic request/response models
- [x] CORS, health check

### Dashboard (COMPLETE)

- [x] React 19 + TypeScript 5.9 + Tailwind v4 + Vite 7
- [x] shadcn/ui components, Geist font, dark mode (3-way toggle)
- [x] App shell: sidebar nav, breadcrumbs, command search (Cmd+K)
- [x] Pages: Home, Agents (list + detail), Tools, Models, Prompts, Deploys, Search, Login
- [x] Deploy pipeline visualization (8-step progress)
- [x] Auth: login page, JWT handling, route guards
- [x] Typed API client with TanStack Query
- [x] Playwright E2E tests (40 tests)

### Infrastructure (COMPLETE)

- [x] Dockerfile, docker-compose.yml
- [x] GitHub Actions: lint, test, E2E, Docker build
- [x] PR template, issue templates, Codecov
- [x] Docs: quickstart, YAML reference, CLI reference, contributing guide

---

## v0.2 — "Registry UI" (Next)

> **Goal:** The dashboard becomes the primary way to explore, edit, and manage everything in the registry. Not just read-only browse — full CRUD with a polished UI.

### M6: Rich Registry Pages

#### 6.1 — Agent Registry Enhancements
- [ ] Agent detail page: full YAML viewer with syntax highlighting (Monaco or CodeMirror)
- [ ] Agent detail: environment variables display (masked secrets)
- [ ] Agent detail: dependency graph tab (which tools, models, prompts this agent uses)
- [ ] Agent config diff viewer (compare two versions side-by-side)
- [ ] Agent clone action (duplicate config as starting point for a new agent)
- [ ] Agent YAML inline editor with validation (edit + save from dashboard)
- [ ] Agent status badge: real-time health indicator (green/yellow/red/gray)
- [ ] Agent logs tab: live log streaming from deployed container (SSE)

#### 6.2 — Prompt Registry (Full)
- [ ] Prompt CRUD from dashboard (create, edit, delete)
- [ ] Prompt editor: Markdown editor with live preview panel
- [ ] Prompt versioning: create new version, view version history
- [ ] Prompt diff viewer (compare two versions, highlight changes)
- [ ] Prompt metadata: tags, description, linked agents
- [ ] Prompt test panel: send a test message using the prompt, see LLM response inline
- [ ] Backend: prompt versions table, version CRUD API routes

#### 6.3 — Tool & Model Registry Enhancements
- [ ] Tool detail page: schema viewer (JSON Schema rendered as a form)
- [ ] Tool detail: usage stats (which agents reference this tool)
- [ ] Tool health indicator (for MCP servers: last ping status, latency)
- [ ] Model detail page: pricing info, context window, capabilities matrix
- [ ] Model comparison view (select 2-3 models, compare side-by-side)
- [ ] Model usage stats (which agents use this model, token counts)

#### 6.4 — Global Registry Features
- [ ] Unified resource creation dialog ("New..." → Agent / Tool / Prompt / Model)
- [ ] Tag management: add/remove tags on any resource, filter by tags everywhere
- [ ] Favorites/bookmarks: star resources, "My Favorites" filter
- [ ] Activity feed: recent changes across all resources (who changed what, when)
- [ ] Bulk actions: select multiple resources, bulk tag / bulk delete
- [ ] Export: download any resource as YAML/JSON

---

### M7: Dashboard Polish & Navigation

#### 7.1 — Navigation & Layout
- [ ] Resizable sidebar (drag to resize, collapse to icons)
- [ ] Breadcrumb navigation with entity type icons
- [ ] Tab persistence (remember which tab was active on agent detail)
- [ ] URL-driven state: all filters, search queries, and tabs reflected in URL
- [ ] Keyboard shortcuts: `n` for new, `/` for search, `g a` for agents, `g t` for tools
- [ ] Empty states: helpful illustrations + CTAs when no data exists

#### 7.2 — Tables & Data Display
- [ ] Sortable columns on all tables (click header to sort)
- [ ] Column visibility toggle (show/hide columns)
- [ ] Pagination with configurable page size (10, 25, 50, 100)
- [ ] Table row expansion (inline detail without navigating away)
- [ ] Data export from any table (CSV, JSON)
- [ ] Relative timestamps with absolute tooltip ("2 hours ago" → hover: "Mar 11, 2026 14:30")

#### 7.3 — Forms & Validation
- [ ] Form validation: inline error messages, field-level validation
- [ ] Unsaved changes warning (prompt before navigating away)
- [ ] Toast notifications for all actions (success, error, info)
- [ ] Confirmation dialogs for destructive actions (delete, teardown)
- [ ] Loading skeletons on all pages (not spinners)

#### 7.4 — Settings: Provider Onboarding & API Key Management

The Settings → Providers page is how users connect LLM providers, manage API keys, and discover models. This must ship before builders (v0.3) since the Agent Builder's model picker depends on having providers configured.

**Provider Onboarding Flow (UI):**

```
Step 1: Pick a provider
┌─────────────────────────────────────────────────────────────────────┐
│  Add Provider                                                       │
│                                                                     │
│  LLM Providers          Gateways              Local                 │
│  ┌──────────┐           ┌──────────┐          ┌──────────┐         │
│  │ OpenAI   │           │ LiteLLM  │          │ Ollama   │         │
│  │          │           │          │          │          │         │
│  └──────────┘           └──────────┘          └──────────┘         │
│  ┌──────────┐           ┌──────────┐                               │
│  │Anthropic │           │OpenRouter│                               │
│  │          │           │          │                               │
│  └──────────┘           └──────────┘                               │
│  ┌──────────┐           ┌──────────┐                               │
│  │ Google   │           │ Portkey  │                               │
│  │          │           │          │                               │
│  └──────────┘           └──────────┘                               │
└─────────────────────────────────────────────────────────────────────┘

Step 2: Configure connection
┌─────────────────────────────────────────────────────────────────────┐
│  Connect to OpenAI                                                  │
│                                                                     │
│  API Key *         [sk-proj-••••••••••••••••••••]  👁 [Paste]      │
│                    ℹ Get your key: platform.openai.com/api-keys     │
│                                                                     │
│  Organization ID   [org-optional]  (optional)                       │
│                                                                     │
│  Base URL          [https://api.openai.com/v1]  (default)          │
│                    ℹ Change this for Azure OpenAI or custom proxy   │
│                                                                     │
│  ┌─────────────────────────────────────────┐                       │
│  │ 🔒 Key stored encrypted. Never logged. │                       │
│  │    Accessible only by your team.        │                       │
│  └─────────────────────────────────────────┘                       │
│                                                                     │
│                            [Test Connection]  [Cancel]  [Save]      │
└─────────────────────────────────────────────────────────────────────┘

Step 3: Test connection → discover models
┌─────────────────────────────────────────────────────────────────────┐
│  ✅ Connection successful!                                          │
│                                                                     │
│  Discovered 8 models:                                               │
│  ┌──────────────────────────────────────────────────────────┐      │
│  │ ☑ gpt-5           $15.00/$60.00 per 1M tokens  128K ctx │      │
│  │ ☑ gpt-5-mini      $1.50/$6.00   per 1M tokens  128K ctx │      │
│  │ ☑ gpt-5-nano      $0.15/$0.60   per 1M tokens  128K ctx │      │
│  │ ☑ gpt-4.1         $2.00/$8.00   per 1M tokens  1M ctx   │      │
│  │ ☐ gpt-4o          $2.50/$10.00  per 1M tokens  128K ctx │      │
│  │ ☑ text-embed-3-sm $0.02         per 1M tokens           │      │
│  │ ☑ text-embed-3-lg $0.13         per 1M tokens           │      │
│  │ ☐ dall-e-3        $0.04/image                            │      │
│  └──────────────────────────────────────────────────────────┘      │
│                                                                     │
│  ☑ Select all    ☐ Include deprecated models                       │
│                                                                     │
│  Selected models will be registered in the Model Registry           │
│  and available in the Agent Builder model picker.                   │
│                                                                     │
│                                     [Register Selected Models]      │
└─────────────────────────────────────────────────────────────────────┘

Step 4: Provider connected
┌─────────────────────────────────────────────────────────────────────┐
│  ✅ OpenAI connected — 6 models registered                         │
│                                                                     │
│  Your team can now select OpenAI models in the Agent Builder.       │
│  Models appear in: Agent Builder → Model Picker → OpenAI tab       │
└─────────────────────────────────────────────────────────────────────┘
```

**Ollama Onboarding Flow (special — auto-detected):**

```
┌─────────────────────────────────────────────────────────────────────┐
│  Connect to Ollama                                                  │
│                                                                     │
│  Ollama URL        [http://localhost:11434]  (auto-detected ✅)     │
│                                                                     │
│  ✅ Ollama is running — found 3 models:                             │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────┐      │
│  │ ☑ llama3.2        3.2B params   2.0 GB   Text           │      │
│  │ ☑ mistral         7B params     4.1 GB   Text           │      │
│  │ ☑ nomic-embed     137M params   274 MB   Embeddings     │      │
│  └──────────────────────────────────────────────────────────┘      │
│                                                                     │
│  Don't have Ollama?  [Install Guide]  [Download Ollama]             │
│                                                                     │
│  Want more models?   [Browse Ollama Library →]                      │
│  Pull a model:       [llama3.1:70b        ]  [Pull ↓]              │
│                      Estimated download: 40 GB, ~15 min             │
│                                                                     │
│                            [Register Models]  [Cancel]              │
└─────────────────────────────────────────────────────────────────────┘
```

**Provider Settings Page (after onboarding):**

```
┌─────────────────────────────────────────────────────────────────────┐
│  Settings → Providers                                [+ Add Provider]│
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ✅ OpenAI                               [Edit] [Disable] [×] │   │
│  │    6 models registered · $124.50 this month · API key: sk-•• │   │
│  │    Last verified: 2 hours ago · Latency: 180ms avg            │   │
│  │    ┌ Models ──────────────────────────────────────────────┐  │   │
│  │    │ gpt-5  gpt-5-mini  gpt-5-nano  gpt-4.1              │  │   │
│  │    │ text-embedding-3-small  text-embedding-3-large       │  │   │
│  │    │                         [+ Discover New Models]      │  │   │
│  │    └──────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ✅ Anthropic                            [Edit] [Disable] [×] │   │
│  │    3 models registered · $89.20 this month · API key: sk-•• │   │
│  │    Last verified: 5 min ago · Latency: 210ms avg             │   │
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ✅ Ollama (Local)                               [Manage] [×] │   │
│  │    5 models available · Free · http://localhost:11434        │   │
│  │    Status: Running · GPU: Apple M2 Max (32GB)               │   │
│  │    [Pull New Model ↓]                                       │   │
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ⚪ Google AI                                     [Connect]   │   │
│  │    Not configured                                            │   │
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                     │
│  ── Gateways ──────────────────────────────────────────────────    │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ⚪ LiteLLM                                      [Connect]   │   │
│  │    Recommended for production. Self-hosted proxy.           │   │
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

**CLI equivalents:**

```bash
garden provider list                              # list configured providers
garden provider add openai                        # interactive: paste API key, test, discover models
garden provider add openai --api-key sk-proj-...  # non-interactive
garden provider add ollama                        # auto-detect local Ollama
garden provider add ollama --base-url http://remote:11434  # remote Ollama
garden provider test openai                       # verify connection + latency
garden provider models openai                     # list models from provider
garden provider disable openai                    # disable without deleting key
garden provider remove openai                     # remove provider + delete key

garden provider add litellm --base-url http://localhost:4000  # connect gateway
garden provider add openrouter --api-key or-...               # connect OpenRouter
```

**API Key Storage Strategy — progressive:**

| Release | Storage Method | How It Works |
|---------|---------------|--------------|
| **v0.2–v0.3** | `.env` file + env vars | Simple. User pastes key → saved to `.env`. CLI reads from env vars. Dashboard reads from server env. |
| **v0.4** | Encrypted in PostgreSQL | Keys encrypted at rest (Fernet). Team-scoped keys. Key rotation. |
| **v1.0** | External secrets managers | AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, Doppler. Keys never touch our DB. |

**v0.2–v0.3: Simple `.env` approach (ship now):**

```
CLI onboarding:
$ garden provider add openai
  Enter your OpenAI API key: sk-proj-•••••••
  ✅ Key verified. 6 models discovered.

  Key saved to: /path/to/project/.env
  Added: OPENAI_API_KEY=sk-proj-...

  ⚠ Add .env to your .gitignore (already there ✅)

UI onboarding:
  Paste API key → Test Connection → key saved to server .env
  Dashboard shows masked key (sk-••••) — never the full key
```

```bash
# .env file (created/updated by garden provider add)
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
OLLAMA_BASE_URL=http://localhost:11434

# Or per-model overrides:
OPENAI_BASE_URL=https://api.openai.com/v1     # default
LITELLM_BASE_URL=http://localhost:4000          # if using gateway
OPENROUTER_API_KEY=or-...                       # if using OpenRouter
```

```bash
# CLI — manage keys
garden provider add openai                        # interactive: prompts for key, saves to .env
garden provider add openai --api-key sk-proj-...  # non-interactive, saves to .env
garden provider add anthropic                     # same flow
garden provider add ollama                        # no key needed, just base_url

garden provider list                              # show providers + masked keys + status
garden provider test openai                       # verify key still works
garden provider remove openai                     # remove key from .env
```

- [ ] `.env` file as the key store for v0.2–v0.3 (simple, works everywhere)
- [ ] `garden provider add` writes keys to `.env` file (creates if missing)
- [ ] `.env` auto-added to `.gitignore` (warn if not)
- [ ] UI provider setup writes to server-side `.env` via API (`POST /api/v1/providers/{name}/configure`)
- [ ] API never returns full keys — only masked suffix (`sk-••••1234`)
- [ ] Keys loaded from env vars at startup — standard `os.environ` / `pydantic-settings`
- [ ] `.env.example` shipped with all provider key placeholders (documented, no real values)

**Implementation items:**

- [ ] Settings page in sidebar navigation (gear icon, bottom of sidebar)
- [ ] Provider registry: `providers` table (name, type, base_url, status, last_verified, model_count) — keys NOT in DB
- [ ] Provider connection test: `POST /api/v1/providers/{name}/test` — verify key works, measure latency
- [ ] Model discovery: `POST /api/v1/providers/{name}/discover` — list available models from provider API
- [ ] Auto-register discovered models into Model Registry with pricing, context window, capabilities
- [ ] Provider health check: background task pings each provider every 5 min, updates status
- [ ] Provider status indicator on settings page: green (healthy), yellow (slow), red (down), gray (disabled)
- [ ] Ollama auto-detection: on platform startup, check localhost:11434, auto-register if found
- [ ] Ollama model pull: `POST /api/v1/providers/ollama/pull` — trigger `ollama pull <model>`, stream progress
- [ ] Provider cost display: month-to-date spend per provider (from cost tracking data)
- [ ] "Get API Key" links: direct links to each provider's API key page (platform.openai.com, console.anthropic.com, etc.)
- [ ] First-run onboarding wizard: if no providers configured, show "Connect your first provider" splash screen
- [ ] CLI: `garden provider` subcommand with add/list/test/models/disable/remove

---

## v0.3 — "Builders" (Planned)

> **Goal:** Every building block — agents, prompts, tools, RAG indexes, memory — has a dedicated builder UI with Git integration, an approval workflow, and registry promotion with versioning. The agent builder composes from all other registries. This is the core product.

### Shared Builder Infrastructure

#### Core Principle: YAML is the Source of Truth

Every resource (agent, prompt, tool, RAG index, memory config) is defined by a **YAML file on disk**. The dashboard UI is a view and editor for that YAML — not a separate data store. This means:

1. **CLI/IDE → UI works:** A developer writes `agent.yaml` in Claude Code, Cursor, VS Code, or vim → pushes to Git → the dashboard UI shows it immediately with full visual editing.
2. **UI → CLI/IDE works:** A user builds an agent in the visual builder → the UI saves a valid `agent.yaml` to Git → the developer can open it in any editor and continue working.
3. **Round-trip fidelity:** Opening a YAML file in the UI and saving it without changes produces an identical file. No reordering keys, no stripping comments, no adding UI-only metadata into the YAML.

```
Developer's editor                Agent Garden Dashboard
(Claude Code, Cursor,       ←→    (Visual Builder, YAML Editor,
 VS Code, vim, etc.)               Prompt Editor, etc.)
        |                                  |
        |          Same YAML file          |
        +------------- on disk -----------+
                       |
                    Git repo
                       |
              Registry (versioned)
```

#### YAML Interoperability (shared across ALL builders)

This applies to every builder: Agent, Prompt, Tool, RAG, Memory.

- [ ] **Single file format per resource type:** `agent.yaml`, `prompt.yaml` + `prompt.md`, `tool.yaml`, `rag.yaml`, `memory.yaml`
- [ ] **JSON Schema for every YAML format:** published schemas that any IDE can use for autocomplete + validation
- [ ] **UI reads from YAML:** when opening a resource in the dashboard, the UI parses the YAML and renders it — never stores a separate representation
- [ ] **UI writes to YAML:** every save in the dashboard writes back to a valid YAML file — the file is the storage, not a database row
- [ ] **Comment preservation:** UI edits must preserve YAML comments (use `ruamel.yaml` round-trip parser, not `pyyaml`)
- [ ] **Key order preservation:** fields stay in the order the user wrote them (no alphabetical re-sorting)
- [ ] **No UI-only state in YAML:** visual builder layout info (node positions, etc.) stored separately in `.garden/` metadata, never in the YAML itself
- [ ] **CLI validation:** `garden validate <file>` validates any YAML file against the schema — same validation the UI uses
- [ ] **File watching:** dashboard detects external file changes (from git pull, editor saves) and reloads automatically
- [ ] **Conflict resolution:** if both UI and external editor change the same file, show a merge dialog (theirs vs. ours vs. manual merge)
- [ ] **API contract:** `GET /api/v1/builders/{type}/{name}/yaml` returns raw YAML, `PUT` accepts raw YAML — the API speaks YAML, not a custom JSON format
- [ ] **Import from anywhere:** drag-and-drop any valid YAML file onto the dashboard to import it into the registry

#### Lifecycle Pattern & Environment Promotion

Every resource goes through three environments. The registry is populated **after merge** (not after PR creation). Production promotion requires an additional explicit step with an eval gate.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        ENVIRONMENT MODEL                                 │
│                                                                          │
│   DEV (sandbox)          STAGING (shared)         PRODUCTION (live)      │
│   ───────────           ────────────────          ──────────────────     │
│   draft/* branch         main branch               release/* tag        │
│   auto-deploy on save    auto-deploy on merge      manual promote       │
│   your sandbox only      shared team environment   customer-facing       │
│   NOT in registry        IN registry (pre-release) IN registry (stable)  │
│   hot-reload enabled     eval runs automatically   eval gate required    │
│                                                                          │
│   CLI: garden dev        CLI: garden deploy         CLI: garden promote  │
│        garden test            --env staging              --env production│
│   UI:  "Run in Sandbox"  UI:  auto on merge        UI: "Promote" button │
│                                                         (blocked until   │
│                                                          eval passes)    │
└──────────────────────────────────────────────────────────────────────────┘
```

**When resources enter the registry:**

| Event | Registry? | Version | Environment |
|-------|-----------|---------|-------------|
| Create draft / save edits | No | — | DEV only (sandbox) |
| Submit PR | No | — | DEV only (reviewers can preview) |
| PR approved & merged to main | **Yes — pre-release** | `v1.2.0-rc.1` | STAGING |
| Eval passes + manual promote | **Yes — stable** | `v1.2.0` | PRODUCTION |
| Rollback | Yes — re-tags previous | `v1.1.0` (re-activated) | PRODUCTION |

**Full lifecycle:**

```
1. CREATE ─→ draft branch, DEV sandbox
2. EDIT   ─→ commits on draft branch, hot-reload in DEV
3. TEST   ─→ playground chat + eval runs in DEV sandbox
4. SUBMIT ─→ PR created, reviewers can preview in DEV
5. REVIEW ─→ approve/reject with YAML diff + eval results
6. MERGE  ─→ main branch, auto-deploy to STAGING, register as pre-release
7. STAGE  ─→ eval runs automatically in STAGING, smoke tests
8. PROMOTE─→ tag as stable release, deploy to PRODUCTION, register as stable
9. MONITOR─→ traces, cost, health in PRODUCTION (feeds back into eval datasets)
```

#### Environment Configuration

Each environment is defined in the platform config:

```yaml
# platform.yaml
environments:
  dev:
    auto_deploy: true          # deploy on every save
    registry: false            # not in registry
    sandbox: true              # isolated per-user Docker containers
    hot_reload: true           # file-watch + restart on change
    budget_limit: $10/day      # cost cap for dev sandbox

  staging:
    auto_deploy: on_merge      # deploy when PR merges to main
    registry: pre-release      # in registry as pre-release (v1.2.0-rc.1)
    eval_required: true        # auto-run eval suite after deploy
    eval_threshold: 0.8        # minimum eval score to stay deployed
    budget_limit: $50/day

  production:
    auto_deploy: false         # manual promote only
    registry: stable           # in registry as stable release
    eval_gate: true            # must pass eval before promote
    approval_required: true    # human approval required
    canary: true               # canary deploy (10% → 50% → 100%)
    budget_limit: $500/day
    alerting: true             # PagerDuty/OpsGenie on errors
```

CLI environment commands:

```bash
garden env list                           # show all environments
garden env status                         # which version is in each env
garden dev agent my-agent                 # start DEV sandbox for an agent
garden deploy agent my-agent --env staging   # deploy to staging (usually auto)
garden promote agent my-agent --env production  # promote to production
garden promote agent my-agent --env production --skip-eval  # emergency hotfix
```

#### Agent Testing Interface

Three levels of testing, available in both CLI and UI:

**Level 1: Playground (Interactive Testing)**

Test an agent by chatting with it directly — available for any resource in any environment.

```
CLI:  garden chat agent my-agent                    # chat in terminal
      garden chat agent my-agent --env staging      # chat with staging version
      garden chat agent my-agent --verbose           # show tool calls, token counts

UI:   Agent detail → "Playground" tab
      → Chat interface with the agent
      → Tool call visualization (expandable cards showing input/output)
      → Token counter + cost estimate per message
      → Model/prompt override: temporarily swap model or prompt to compare behavior
      → "Save as Eval Case" button: save a conversation turn as a golden test case
```

- [ ] Playground chat in dashboard: send messages, see streamed responses
- [ ] Tool call visualization: expandable cards with input/output JSON
- [ ] Token counter + cost estimate per message
- [ ] Model override: temporarily swap the model to compare behavior
- [ ] Prompt override: edit the system prompt for this session only
- [ ] "Save as Eval Case": capture a conversation turn as a golden test case
- [ ] CLI: `garden chat` — interactive terminal chat with any agent
- [ ] CLI: `garden chat --verbose` — show tool calls, token counts, latency

**Level 2: Evaluation (Automated Testing — see v0.5 for full spec)**

Run golden test suites against an agent to measure quality systematically.

```
CLI:  garden eval agent my-agent                    # run default eval suite
      garden eval agent my-agent --dataset edge-cases  # specific dataset
      garden eval agent my-agent --judge claude-sonnet  # specify judge model

UI:   Agent detail → "Evaluations" tab
      → Run eval, see results, compare against baseline
```

- [ ] Auto-eval on merge: when a PR merges, eval suite runs automatically in staging
- [ ] Eval gate for production: promote is blocked until eval score ≥ threshold
- [ ] Regression alerts: if staging eval scores drop vs. previous version, alert the team

**Level 3: Production Feedback (Continuous Learning)**

Collect feedback from production usage to improve agents over time.

```
User interacts with agent
    → Trace captured (input, output, tool calls, latency, cost)
    → Human feedback: thumbs up/down, corrections, ratings
    → Feedback stored in feedback_events table
    → Curate feedback into eval datasets (close the loop)
```

- [ ] Feedback collection API: `POST /api/v1/feedback` — thumbs up/down, text correction, 1-5 rating
- [ ] Feedback widget: embeddable UI component for end-user feedback on agent responses
- [ ] Feedback dashboard: aggregate scores per agent, trend over time, worst-performing responses
- [ ] "Curate to Dataset" button: select good/bad feedback items → add to eval dataset
- [ ] Feedback-to-eval pipeline: auto-generate eval cases from low-rated responses
- [ ] Backend: `feedback_events` table (trace_id, agent_id, rating, correction, user_id, timestamp)

#### Agent Improvement: Prompt Optimization & Fine-Tuning

Most agent improvement is **prompt optimization** (fast, cheap, no training). Fine-tuning and RL are for advanced cases when prompting hits a ceiling.

**Tier 1: Prompt Optimization (primary improvement path)**

```
Feedback + eval results
    → Identify failure patterns (wrong tool choice, bad reasoning, hallucination)
    → Edit prompt in Prompt Builder (or A/B test variants)
    → Re-run eval to measure improvement
    → Promote better prompt version
```

- [ ] Prompt A/B testing: deploy two prompt versions, split traffic, compare metrics
- [ ] Prompt variant management: create variants of the same prompt, tag with experiment IDs
- [ ] Auto-suggest prompt improvements: LLM analyzes failure cases and suggests prompt edits
- [ ] Prompt optimization history: track which prompt changes improved/degraded which metrics

**Tier 2: Few-Shot Example Curation**

```
Good conversations from production (high-rated by users)
    → Curate as few-shot examples
    → Inject into system prompt as examples
    → Re-run eval to validate
```

- [ ] Example curator: select high-rated conversations → format as few-shot examples
- [ ] Auto-inject examples: add curated examples to system prompt at runtime (configurable count)
- [ ] Example effectiveness tracking: measure if adding specific examples improves eval scores

**Tier 3: Fine-Tuning (advanced, optional)**

For teams that have hit the ceiling of prompt engineering and need a custom model.

```
Training dataset (curated from production traces + feedback)
    → Export to fine-tuning format (JSONL: messages + completions)
    → Submit to provider API (OpenAI, Anthropic, Google)
    → Fine-tuned model registered in Model Registry
    → Deploy agent with fine-tuned model
    → Run eval to validate improvement
```

- [ ] Training dataset builder: curate traces + feedback into fine-tuning datasets
- [ ] Export formats: OpenAI JSONL, Anthropic messages, Google Vertex format
- [ ] Fine-tune job trigger: `POST /api/v1/fine-tune/jobs` — submit to provider API
- [ ] Fine-tune job tracking: status, cost, estimated completion time
- [ ] Auto-register model: on completion, register fine-tuned model in Model Registry
- [ ] Side-by-side eval: compare fine-tuned model vs. base model on same eval suite
- [ ] CLI: `garden fine-tune agent my-agent --provider openai --dataset my-dataset`

**Tier 4: RLHF / Reinforcement Learning (future, post v1.0)**

Use human feedback to systematically improve agent behavior through reward modeling.

```
Production traces + human ratings (thumbs up/down, corrections)
    → Train reward model (what makes a "good" response for this agent)
    → Use reward model to rank response candidates
    → PPO/DPO training to optimize the agent's model
    → Or: use reward model to select best prompt variant automatically
```

- [ ] Reward model training: train a classifier on human feedback (good/bad responses)
- [ ] Preference pairs: collect A/B comparisons from users ("response A or B is better?")
- [ ] DPO dataset export: export preference pairs in DPO format for external training
- [ ] Reward-guided prompt selection: use reward model to auto-select best prompt variant
- [ ] This is post-v1.0 — requires significant production data volume to be effective

#### Git Workflow — CLI and UI Parity

The same Git workflow works identically from the CLI and the dashboard. Every operation available in the UI has a CLI equivalent. The Git repo is the single source of truth — the platform database only stores metadata indexes and approval state.

**Branch Model:**

```
main                            ← published resources (production)
  └── draft/{user}/{resource}   ← work-in-progress (one branch per resource per user)
```

**Full Workflow (CLI commands + UI equivalents):**

```
Step 1: CREATE — start a new resource
─────────────────────────────────────────────────────────
CLI:  garden create agent my-agent
      → Creates agents/my-agent/agent.yaml from template
      → Creates Git branch: draft/{user}/agents/my-agent
      → First commit: "Create agent my-agent"

UI:   Click "New Agent" → fill in builder → Save
      → Same branch + commit created via API
─────────────────────────────────────────────────────────

Step 2: EDIT — iterate on the resource
─────────────────────────────────────────────────────────
CLI:  # Edit in any editor (Claude Code, Cursor, vim...)
      vim agents/my-agent/agent.yaml
      garden save agent my-agent                # or: git add + git commit
      garden save agent my-agent -m "add tools" # custom commit message

UI:   Edit in YAML editor / Visual builder / Prompt editor → Save
      → Each save = Git commit on the draft branch
      → Commit message auto-generated or user-provided

Both: Multiple saves = multiple commits on the same branch
      Full commit history visible in both CLI and UI
─────────────────────────────────────────────────────────

Step 3: DIFF — review what changed
─────────────────────────────────────────────────────────
CLI:  garden diff agent my-agent                # diff vs. main
      garden diff agent my-agent --version v1.2 # diff vs. specific version
      garden log agent my-agent                 # commit history

UI:   "Changes" tab in builder → Monaco diff editor (side-by-side)
      "History" tab → commit log with expandable diffs
─────────────────────────────────────────────────────────

Step 4: SUBMIT — request review (creates a PR)
─────────────────────────────────────────────────────────
CLI:  garden submit agent my-agent
      garden submit agent my-agent -m "Ready for review: added Zendesk tool"
      → Creates a Pull Request (internal PR, or GitHub/GitLab PR if external repo)
      → PR contains: YAML diff, commit history, auto-generated summary
      → Status changes: draft → submitted

UI:   Click "Submit for Review" → add description → Submit
      → Same PR created via API
      → Badge changes to "Pending Review"
─────────────────────────────────────────────────────────

Step 5: REVIEW — approve, reject, or comment
─────────────────────────────────────────────────────────
CLI:  garden review list                        # see pending reviews
      garden review show agent my-agent         # see PR diff + comments
      garden review approve agent my-agent      # approve
      garden review reject agent my-agent -m "needs X" # reject with comment
      garden review comment agent my-agent -m "looks good, minor nit on line 12"

UI:   Approvals page → click PR → Review UI:
        - Side-by-side YAML diff with line-level comments
        - Approve / Request Changes / Reject buttons
        - Comment thread per PR
        - See which agents/tools/prompts are affected (impact analysis)
─────────────────────────────────────────────────────────

Step 6: MERGE — publish to registry
─────────────────────────────────────────────────────────
CLI:  (automatic on approval, or manual:)
      garden publish agent my-agent             # merge + tag + publish
      garden publish agent my-agent --version 2.0.0  # explicit version bump

UI:   (automatic on approval)
      → Branch merged to main
      → Git tag created: agents/my-agent/v1.0.0
      → Resource published to Registry with semver
      → Badge changes to "Published v1.0.0"

Auto-versioning:
  - Patch bump (1.0.0 → 1.0.1): default for minor edits
  - Minor bump (1.0.0 → 1.1.0): new tools/capabilities added
  - Major bump (1.0.0 → 2.0.0): breaking changes (user specifies)
─────────────────────────────────────────────────────────

Step 7: ROLLBACK — revert to a previous version
─────────────────────────────────────────────────────────
CLI:  garden rollback agent my-agent --version v1.0.0
      → Creates a new commit that reverts to v1.0.0 state
      → Goes through the same submit → review → merge flow
      → (or --force to skip review for emergencies)

UI:   Agent detail → Version History → click "Rollback to this version"
      → Same flow: creates revert commit, optionally submits for review
─────────────────────────────────────────────────────────
```

**Additional CLI commands for Git workflow:**

```bash
garden status                     # show all resources with uncommitted changes
garden status agent my-agent      # show status of one resource (branch, commits ahead, review status)
garden branches                   # list all draft branches
garden branches --mine            # list only your draft branches
garden discard agent my-agent     # delete draft branch, discard changes
garden sync                       # pull latest from remote Git repo
garden clone <repo-url>           # clone external Git repo into platform
```

#### Git Integration — Backend Implementation
- [ ] Git backend: each resource type stored in directories (agents/, prompts/, tools/, rag/, memory/)
- [ ] Branch management: auto-create `draft/{user}/{type}/{name}` branches on create/edit
- [ ] Commit on save: every save (CLI or UI) = Git commit with author, timestamp, message
- [ ] Diff engine: compute YAML-aware diffs (highlight changed fields, not just text lines)
- [ ] PR system: internal PR model (or bridge to GitHub/GitLab PRs for external repos)
- [ ] PR metadata: title, description, YAML diff, commit list, impact analysis (affected dependents)
- [ ] Merge engine: fast-forward merge to main on approval, conflict detection if main changed
- [ ] Tag on publish: Git tag with semver (e.g., `agents/my-agent/v1.0.0`)
- [ ] Auto-versioning: detect change scope (patch/minor/major) based on diff analysis
- [ ] File watching: detect changes made by external editors, update dashboard in real-time
- [ ] Git clone/pull: sync resources from external Git repos (GitHub, GitLab, Bitbucket)
- [ ] Git push: push approved resources to external repos (optional, configurable)
- [ ] Backend: `resource_versions` table (resource_type, resource_id, version, git_sha, status, author, approved_by)
- [ ] Backend: `pull_requests` table (id, resource_type, resource_id, branch, status, submitter, reviewer, title, description, comments_json)

#### Approval Workflow
- [ ] Status state machine: `draft` → `submitted` → `in_review` → `approved` / `changes_requested` / `rejected` → `published`
- [ ] Submit for review: CLI (`garden submit`) and UI ("Submit for Review" button)
- [ ] Approvals page in dashboard: pending reviews queue, grouped by resource type, filterable by team
- [ ] Review UI: side-by-side YAML diff, line-level comments, approve/reject/request-changes buttons
- [ ] Comment threads: threaded discussion per PR (persisted in `pull_requests.comments_json`)
- [ ] Re-submit after changes: address feedback, re-submit — reviewer sees new diff against previous review
- [ ] On approval: auto-merge to main, auto-tag, auto-publish to registry
- [ ] Notifications: email/webhook on submit, comment, approve, reject, publish
- [ ] Review policies: configurable per team (e.g., "require 2 approvals for agents", "auto-approve prompt edits")
- [ ] Backend: `approval_requests` table (resource_type, resource_id, version, status, submitter, reviewer, comment)

---

### M8: Prompt Builder

#### 8.1 — Prompt Editor
- [ ] Markdown editor (Monaco) with live preview panel (side-by-side)
- [ ] Template variable support: `{{variable}}` syntax with variable list panel
- [ ] Variable defaults and descriptions (editable in sidebar)
- [ ] Prompt metadata: name, description, tags, linked agents
- [ ] Character/token counter (estimate tokens for selected model)
- [ ] "Test Prompt" panel: select a model from Model Registry, send test message, see response inline
- [ ] Test with variables: fill in template variables, see rendered prompt, send to LLM

#### 8.2 — Prompt Versioning & Registry
- [ ] Version history: list all versions with author, date, diff summary
- [ ] Promote to registry: on approval, prompt appears in Prompt Registry with version
- [ ] Compare versions: side-by-side diff of any two versions
- [ ] Linked agents view: which agents reference this prompt (auto-detected from agent.yaml)
- [ ] Import: paste existing prompt text, upload .md file
- [ ] Export: download as .md, copy to clipboard

---

### M9: Tool Builder

#### 9.1 — Tool Editor
- [ ] Tool definition editor: name, description, input schema (JSON Schema), output schema
- [ ] Schema builder UI: form-based JSON Schema editor (add fields, set types, required flags)
- [ ] Or raw JSON Schema editor (Monaco) with validation
- [ ] Tool implementation: code editor for the tool function (Python)
- [ ] Dependencies: specify pip packages required by the tool
- [ ] Tool metadata: tags, category, author

#### 9.2 — Tool Execution Sandbox
- [ ] Sandbox environment: Docker-based isolated execution (no network access by default)
- [ ] "Run Tool" panel: provide sample input JSON, execute tool, see output
- [ ] Execution log: stdout/stderr captured and displayed
- [ ] Execution history: previous runs with input/output preserved
- [ ] Timeout configuration: max execution time per tool (default 30s)
- [ ] Network toggle: allow/deny network access per tool in sandbox
- [ ] Backend: `POST /api/v1/tools/sandbox/execute` — runs tool in ephemeral Docker container

#### 9.3 — Tool Versioning & Registry
- [ ] Git-backed versioning (same pattern as prompts)
- [ ] Promote to Tool Registry on approval
- [ ] Tool health check: periodic sandbox execution with sample input to verify tool still works
- [ ] Usage stats: which agents reference this tool

#### 9.4 — MCP Server Management
- [ ] MCP server registry: register servers with name, endpoint, transport (stdio/SSE/streamable-HTTP)
- [ ] MCP server CRUD API: `POST /api/v1/mcp-servers`, `GET`, `PUT`, `DELETE`
- [ ] MCP server health monitoring: periodic ping, track uptime and latency
- [ ] MCP server auto-discovery: enhance existing `connectors/mcp_scanner/`
- [ ] MCP Servers dashboard page: list servers with status indicators (green/red/gray)
- [ ] MCP Server detail: tools list (auto-fetched via `tools/list`), schema viewer, health chart
- [ ] "Register MCP Server" dialog: enter endpoint, auto-detect transport, fetch tool list
- [ ] Backend: `mcp_servers` table (name, endpoint, transport, status, last_ping, tool_count)

#### 9.5 — Example MCP Server
- [ ] `examples/mcp-server/` — minimal MCP server (Python, stdio transport) with 2-3 example tools
- [ ] MCP server SDK helper: `from agent_garden.mcp import serve` one-liner to expose tools as MCP
- [ ] Docker Compose includes the example MCP server alongside the platform

---

### M10: RAG Builder (Vector DB Index Management)

#### 10.1 — Vector Index Registry
- [ ] Vector index as a registry resource: name, description, embedding model, chunk strategy, source
- [ ] Supported vector backends: pgvector (built-in), Qdrant, Pinecone, Weaviate (pluggable)
- [ ] Index metadata: document count, embedding dimensions, last updated, size
- [ ] Backend: `vector_indexes` table (name, backend, embedding_model, doc_count, status, config_json)

#### 10.2 — Data Ingestion
- [ ] Upload files: drag-and-drop PDF, TXT, MD, CSV, JSON files → auto-chunk → embed → index
- [ ] Database source: connect to PostgreSQL/MySQL, select table/query, ingest rows as documents
- [ ] Google Drive source: OAuth2 connect, browse folders, select files/folders to ingest
- [ ] Google Sheets source: OAuth2 connect, select spreadsheet, map columns to document fields
- [ ] Web URL source: provide URL(s), crawl pages, extract text, chunk, embed
- [ ] Chunking strategies: fixed-size, sentence-based, paragraph-based, recursive (configurable)
- [ ] Embedding model selection: pick from Model Registry (OpenAI ada, Cohere, local models)
- [ ] Ingestion progress: real-time progress bar (documents processed / total)
- [ ] Scheduled re-ingestion: cron-based refresh from connected sources
- [ ] Backend: `ingestion_jobs` table (index_id, source_type, status, doc_count, error_log)

#### 10.3 — Vector Search UI
- [ ] Search panel: type a query, select an index, see ranked results with similarity scores
- [ ] Result display: document chunk, metadata, similarity score, source file/row
- [ ] Filter results by: metadata fields, date range, source
- [ ] Search settings: top_k, similarity threshold, reranking toggle
- [ ] Compare indexes: run same query against two indexes, see results side-by-side
- [ ] Search API: `POST /api/v1/rag/search` — programmatic access for agents

#### 10.4 — RAG Versioning & Registry
- [ ] Git-backed index configuration versioning
- [ ] Promote to RAG Registry on approval
- [ ] Index snapshots: point-in-time backup/restore
- [ ] Linked agents view: which agents reference this index

---

### M11: Memory Builder

#### 11.1 — Memory Backend Registry
- [ ] Memory as a registry resource: name, backend type, configuration, scope
- [ ] Supported backends:
  - **Redis**: conversation buffer, TTL-based expiry, fast read/write
  - **PostgreSQL**: persistent conversation history, full-text search
  - **In-Memory**: ephemeral, for testing and development only
- [ ] Backend configuration UI: connection string, TTL, max messages, namespace pattern
- [ ] Memory metadata: backend type, message count, storage size, linked agents
- [ ] Backend: `memory_configs` table (name, backend_type, config_json, status)

#### 11.2 — Memory Management UI
- [ ] Memory instances page: list all configured memory backends with stats
- [ ] Memory detail: browse stored conversations (namespaced by agent + user)
- [ ] Conversation viewer: message-by-message display with timestamps
- [ ] Search memory: full-text search across stored conversations
- [ ] Delete conversations: bulk delete by agent, user, or date range
- [ ] Memory usage dashboard: storage size over time, message count by agent

#### 11.3 — Memory Types
- [ ] Buffer Window: sliding window of last N messages (configurable N)
- [ ] Buffer: full conversation history (no truncation)
- [ ] Summary: periodic LLM-generated summaries of long conversations
- [ ] Entity: extract and track entities mentioned across conversations
- [ ] Semantic: vector-indexed memory for similarity-based recall
- [ ] Each type configurable per agent in agent.yaml `memory:` block

#### 11.4 — Memory Versioning & Registry
- [ ] Git-backed configuration versioning
- [ ] Promote to Memory Registry on approval
- [ ] Linked agents view: which agents use this memory config

---

### M12: Agent Builder (Composes from All Registries)

The agent builder is the capstone — it pulls from every other registry to assemble a complete agent.

#### 12.1 — YAML Builder Mode
- [ ] Full YAML editor (Monaco) with JSON Schema validation
- [ ] Schema-aware autocomplete (field names, enum values, registry refs)
- [ ] Insert snippets: model config, tool reference, guardrail block, deploy block
- [ ] Live validation: red markers on invalid lines, error panel below
- [ ] "Deploy" button: trigger deploy pipeline directly from editor
- [ ] "Save Draft" button: save agent config to registry without deploying

#### 12.2 — Visual Builder Mode
- [ ] ReactFlow canvas with drag-and-drop nodes
- [ ] Node types: Agent, Model, Tool, MCP Server, Prompt, Memory, RAG Index, Guardrail
- [ ] Node palette sidebar: drag nodes from palette onto canvas
- [ ] Edge connections: wire components to the agent node
- [ ] Property panel: click a node to edit its properties in a side panel
- [ ] Generates valid `agent.yaml` from the graph
- [ ] "Deploy from Canvas" button

#### 12.3 — Registry Pickers (pull from all registries)
- [ ] **LLM Provider picker**: browse providers (Anthropic, OpenAI, Google, etc.)
- [ ] **Model picker**: browse Model Registry, filter by provider/capability/price, select model
- [ ] **Tool picker**: browse Tool Registry, see schema preview, add to agent
- [ ] **MCP Server picker**: browse MCP servers, select individual tools from each server
- [ ] **Prompt picker**: browse Prompt Registry, preview content, select version
- [ ] **Memory picker**: browse Memory Registry, select backend + type
- [ ] **RAG Index picker**: browse RAG Registry, preview index stats, select for agent
- [ ] Each picker: search, filter, preview, "Add to Agent" button
- [ ] Selected components appear as nodes in visual mode / YAML refs in code mode

#### 12.4 — Deploy from Dashboard
- [ ] Deploy dialog: select target (Local Docker, Kubernetes)
- [ ] Pre-deploy validation: check all registry refs resolve, MCP servers reachable, model available
- [ ] Deploy progress: 8-step pipeline visualization, real-time
- [ ] Deploy log streaming in a bottom panel
- [ ] Rollback button on failed deploys
- [ ] Teardown button on agent detail page

#### 12.5 — Agent Versioning & Registry
- [ ] Git-backed versioning (same pattern as all builders)
- [ ] Promote to Agent Registry on approval
- [ ] Dependency lock: snapshot exact versions of all referenced resources at deploy time
- [ ] Dependency update check: "newer versions available" indicator on agent detail

---

### M13: Framework Examples & Runtime Builders (1 per SDK)

Each example is a complete, working agent with `agent.yaml`, source code, `requirements.txt`, and a README.

#### Supported SDKs (v1.0)

| SDK | Example | Runtime Builder | Priority |
|-----|---------|----------------|----------|
| **Google ADK** | `examples/google-adk-agent/` | `engine/runtimes/google_adk.py` | P0 |
| **LangGraph / DeepAgent** | `examples/langgraph-agent/` (exists) | `engine/runtimes/langgraph.py` (exists) | P0 |
| **OpenAI Agents SDK** | `examples/openai-agents-agent/` | `engine/runtimes/openai_agents.py` | P0 |
| **CrewAI** | `examples/crewai-agent/` | `engine/runtimes/crewai.py` | P0 |
| **Claude SDK** | `examples/claude-sdk-agent/` | `engine/runtimes/claude_sdk.py` | P0 |

#### Later SDKs (post v1.0)

| SDK | Notes |
|-----|-------|
| Pydantic AI | When adoption grows |
| Mastra (TypeScript) | Requires TS runtime builder |
| Strands | AWS-native agent framework |
| AutoGen | Microsoft multi-agent |
| Custom | Any Python agent with HTTP interface |

#### 13.1 — Examples (1 per SDK)
- [ ] `examples/langgraph-agent/` — LangGraph / DeepAgent (polish existing)
- [ ] `examples/google-adk-agent/` — Google Agent Development Kit
- [ ] `examples/openai-agents-agent/` — OpenAI Agents SDK
- [ ] `examples/crewai-agent/` — CrewAI multi-agent crew
- [ ] `examples/claude-sdk-agent/` — Anthropic Claude SDK (tool use)

#### 13.2 — Runtime Builders
- [ ] `engine/runtimes/google_adk.py` — Google ADK runtime builder
- [ ] `engine/runtimes/openai_agents.py` — OpenAI Agents runtime builder
- [ ] `engine/runtimes/crewai.py` — CrewAI runtime builder
- [ ] `engine/runtimes/claude_sdk.py` — Claude SDK runtime builder
- [ ] Each runtime: Dockerfile generation, dependency resolution, entrypoint config
- [ ] Integration test per runtime: build container, start, verify `/health` responds

---

## v0.4 — "Observability" (Planned)

> **Goal:** Full visibility into what agents are doing, how much they cost, and who has access. Tracing, cost tracking, RBAC, and audit — all surfaced in the dashboard.

### M14: Agent Tracing & Observability

Integrate with Langfuse (primary) and support MLflow as an alternative backend. Every LLM call, tool invocation, and agent step gets traced automatically.

#### 14.1 — Tracing Backend Integration
- [ ] Langfuse Python SDK integration (`langfuse` package) as the default tracing backend
- [ ] MLflow Tracing support as an alternative (`mlflow.tracing`) — configurable via env var
- [ ] OpenTelemetry export: emit OTel-compatible spans for each agent invocation
- [ ] Auto-instrumentation: patch LangGraph, CrewAI, OpenAI, Anthropic SDK calls automatically
- [ ] Trace context propagation: pass trace IDs through tool calls and MCP requests
- [ ] Config: `TRACING_BACKEND=langfuse|mlflow|otlp|none`, connection URL, API keys
- [ ] Backend: `traces` table for local trace metadata (trace_id, agent, duration, token_count, cost, status)

#### 14.2 — Tracing Dashboard
- [ ] Traces page: list of recent traces with agent name, duration, token count, cost, status
- [ ] Trace detail page: waterfall/timeline view of spans (LLM calls, tool calls, agent steps)
- [ ] Span detail: input/output content, model used, token counts, latency
- [ ] Filter traces by: agent, status (success/error), date range, min duration, min cost
- [ ] Trace search: full-text search over inputs/outputs
- [ ] Link from agent detail page → "View Traces" (pre-filtered)
- [ ] Embedded Langfuse iframe option (for users running Langfuse, deep-link into its UI)

#### 14.3 — Agent Monitoring
- [ ] Agent health dashboard: uptime, request count, error rate per agent
- [ ] Latency charts: P50, P95, P99 over time per agent
- [ ] Token usage charts: daily token consumption per agent, per model
- [ ] Error log: recent errors with stack traces, grouped by error type
- [ ] Alerting rules: email/webhook notifications on error rate spike or latency threshold
- [ ] Backend: `agent_metrics` table (agent_id, timestamp, request_count, error_count, p50_ms, p95_ms, tokens_in, tokens_out)

### M15: RBAC & Teams

- [ ] Team management UI: create teams, add/remove members
- [ ] Role definitions: Viewer, Deployer, Admin
- [ ] Resource ownership: every agent/tool/prompt belongs to a team
- [ ] Permission checks on all CRUD operations (API + UI)
- [ ] Team switcher in sidebar (filter everything by team)
- [ ] Invite flow: invite users by email
- [ ] Team-scoped API keys: each team can set their own provider keys (overrides platform-level)
- [ ] API key encryption: keys stored encrypted in PostgreSQL (Fernet, keyed from `SECRET_KEY`)
- [ ] Key rotation: update a key → test new key → swap (zero downtime)
- [ ] Backend: teams table, team_memberships table, role-based middleware, encrypted_keys table

### M16: Cost Tracking

- [ ] Cost event ingestion: capture token counts + dollar costs per LLM call (from traces)
- [ ] Cost dashboard page: charts for daily/weekly/monthly spend
- [ ] Cost breakdown: by agent, by model, by team
- [ ] Budget management: set budget per team, alert at 80%/100%
- [ ] Cost comparison: side-by-side model cost analysis
- [ ] Cost per request: show cost inline on each trace
- [ ] Backend: cost_events table, budget table, aggregation queries
- [ ] LiteLLM connector enhanced: ingest cost data from LiteLLM proxy logs into cost_events

### M17: Audit & Lineage

- [ ] Audit log: immutable record of all actions (deploy, config change, delete, access change)
- [ ] Audit log UI: filterable table with actor, action, resource, timestamp
- [ ] Lineage graph: visual dependency graph (agent → tools, MCP servers, models, prompts)
- [ ] Lineage UI: ReactFlow graph showing relationships between resources
- [ ] Impact analysis: "if I change this prompt, which agents are affected?"
- [ ] Change notifications: subscribe to changes on resources you depend on

---

## v0.5 — "Evaluation" (Planned)

> **Goal:** Teams can measure agent quality systematically — golden test sets, automated scoring, regression detection, and CI/CD gates. Ship agents with confidence.

### M18: Evaluation Framework

#### 18.1 — Evaluation Dataset Management
- [ ] Evaluation dataset as a registry resource: name, agent, format, version
- [ ] Dataset format: JSON Lines with `input`, `expected_output`, `expected_tool_calls`, `tags`, `metadata`
- [ ] Dataset editor UI: create/edit/tag/version datasets from the dashboard
- [ ] Import datasets: upload JSONL file, paste JSON, or capture from live conversations
- [ ] Export datasets: download as JSONL, CSV
- [ ] Dataset splitting: auto-split into train/test/validation sets
- [ ] Backend: `eval_datasets` table (name, agent_id, version, row_count, format)
- [ ] Backend: `eval_dataset_rows` table (dataset_id, input, expected_output, tags)

#### 18.2 — Evaluation Runner
- [ ] CLI: `garden eval <agent-name> --dataset <dataset>` — run offline evaluation
- [ ] API: `POST /api/v1/eval/run` — trigger evaluation run programmatically
- [ ] Run against any deployed agent or local agent (Docker)
- [ ] Parallel execution: run N test cases concurrently (configurable)
- [ ] Progress tracking: real-time progress bar in UI and CLI
- [ ] Backend: `eval_runs` table (id, agent_id, dataset_id, status, started_at, completed_at, summary_json)
- [ ] Backend: `eval_results` table (run_id, row_id, actual_output, scores_json, latency_ms, token_count)

#### 18.3 — Scoring & Metrics
- [ ] Built-in metrics: correctness, relevance, groundedness, tool accuracy, latency, cost
- [ ] Judge model integration: use a configurable LLM (e.g., Claude, GPT-4) to auto-grade responses
- [ ] Judge prompt templates: customizable grading rubrics per metric
- [ ] Custom metric plugins: user-defined Python scoring functions
- [ ] Aggregate scores: per-run summary (mean, median, P95 per metric)
- [ ] Score breakdown: by tag, by test case category
- [ ] Confidence intervals on scores

#### 18.4 — Evaluation Dashboard
- [ ] Eval Runs page: list all runs with agent, dataset, date, overall score, status
- [ ] Run detail page: per-test-case results table with input, expected, actual, scores
- [ ] Score trends chart: plot scores over time per agent (detect regressions)
- [ ] Regression detection: compare current run against baseline, alert on score drop > threshold
- [ ] Run comparison: select two runs, see side-by-side score diffs
- [ ] Link from agent detail → "Evaluations" tab (pre-filtered)

#### 18.5 — CI/CD Integration
- [ ] GitHub Action: `agent-garden/eval-action` — run evals on PR, post results as PR comment
- [ ] Quality gate: block merge if scores below configurable threshold
- [ ] Eval badge: embed score badge in README (like coverage badges)
- [ ] Scheduled evals: cron-based regression runs (daily/weekly)
- [ ] Promotion gate: require passing eval before registry promotion (ties into approval workflow)

---

## v0.6 — "Connectivity" (Planned)

> **Goal:** Agents can discover and call each other via A2A protocol. MCP servers become a managed hub. The platform enables multi-agent workflows.

### M19: Agent-to-Agent (A2A) Protocol

Implement the Google A2A specification for agent interoperability. Any deployed agent can advertise its capabilities and be invoked by other agents.

#### 19.1 — A2A Server
- [ ] A2A JSON-RPC 2.0 server endpoint on every deployed agent (`/.well-known/agent.json`)
- [ ] Agent Card generation: auto-generate capability advertisement from `agent.yaml` (name, description, skills, input/output schemas)
- [ ] A2A discovery endpoint: `GET /api/v1/a2a/agents` — list all A2A-capable agents
- [ ] A2A invoke: `POST /api/v1/a2a/invoke` — call an agent by name, get structured response
- [ ] A2A streaming: SSE support for long-running agent responses
- [ ] A2A authentication: JWT-based inter-agent auth (agents get service tokens)
- [ ] Backend: `a2a_agents` table (agent_id, agent_card_json, endpoint, status)

#### 19.2 — A2A in Agent Config
- [ ] `agent.yaml` `subagents:` field — declare agents this agent can call
- [ ] Auto-generated `call_{agent_name}` tools from subagent declarations
- [ ] Subagent resolution: validate that referenced agents exist in registry and are deployed
- [ ] Subagent call tracing: A2A calls appear as spans in the trace waterfall
- [ ] Example: `examples/a2a-orchestrator/` — supervisor agent that delegates to two sub-agents

#### 19.3 — A2A Dashboard
- [ ] A2A Agents page: list all agents with A2A enabled, show Agent Cards
- [ ] A2A topology graph: ReactFlow visualization of which agents call which
- [ ] A2A call log: recent inter-agent calls with latency, status, input/output preview
- [ ] Agent Card editor: customize the auto-generated Agent Card from dashboard
- [ ] A2A test panel: invoke any A2A agent from the dashboard, see response inline

#### 19.4 — Multi-Agent Orchestration Patterns
- [ ] Supervisor pattern: one agent routes requests to specialized sub-agents
- [ ] Fan-out/fan-in: send request to N agents in parallel, aggregate responses
- [ ] Chain pattern: sequential pipeline of agents (output of one → input of next)
- [ ] Orchestration YAML schema: define multi-agent workflows in `agent.yaml`
- [ ] Example: `examples/a2a-supervisor/` — supervisor + 2 workers (research + summarize)

### M20: MCP Server Hub

Elevate MCP from "tool connector" to a managed server hub with lifecycle management.

- [ ] MCP server packaging: define MCP servers as registry resources (name, version, transport, tools)
- [ ] MCP server deploy: deploy MCP servers alongside agents (Docker sidecar or standalone)
- [ ] MCP server versioning: track versions, support rollback
- [ ] MCP server sharing: teams can publish MCP servers for other teams to use
- [ ] MCP compose: `agent.yaml` `mcp_servers:` field auto-starts required MCP servers at deploy time
- [ ] MCP server metrics: request count, latency, error rate per server per tool
- [ ] MCP server UI enhancements: "Try Tool" panel (invoke a tool with sample input, see output)

---

## v0.7 — "Marketplace" (Planned)

> **Goal:** Users can discover, share, and one-click deploy agent templates from a community marketplace.

### M21: Template System

- [ ] Template schema: parameterized agent configs with user-fillable variables
- [ ] Template creation UI: convert any agent config into a template
- [ ] Template gallery page: grid of cards with preview, description, framework badge
- [ ] "Use Template" flow: fill in parameters → generates agent.yaml → deploy
- [ ] Built-in templates: Customer Support Bot, Data Analyzer, Code Reviewer, Research Assistant
- [ ] Template versioning and deprecation

### M22: Marketplace

- [ ] Marketplace page: browse templates by category, framework, rating
- [ ] Search + filters: keyword search, framework filter, tag filter
- [ ] Template detail page: README preview, config preview, usage stats
- [ ] Ratings & reviews: star rating + text review per template
- [ ] "One-click deploy" from marketplace listing
- [ ] Publish flow: submit template for listing (admin approval)

---

## v1.0 — "General Availability" (Planned)

> **Goal:** The enterprise one-stop shop for AgentOps. Local Docker full-stack + Google Cloud Run for production. Full model catalog. SSO. Operational maturity.

### M23: Deployment Targets

#### v1.0 Deployers (ship with GA)
- [ ] **Local Docker** (full stack) — `docker compose up` runs everything: API, dashboard, PostgreSQL, Redis, agents, MCP servers
- [ ] **Google Cloud Run** — primary cloud target: auto-scaling, scale-to-zero, Artifact Registry, Cloud Load Balancing, Workload Identity
- [ ] Deploy target selector in dashboard UI
- [ ] Cloud console deep links on deploy status page

#### Deployer Priority (post v1.0, in order)

| Priority | Target | Notes |
|----------|--------|-------|
| **P1** | Databricks Apps / Lakebase | Deploy agents as Databricks Apps; Lakebase for state/vector storage |
| **P1** | AI Gateway (LiteLLM/Portkey) | Centralized model routing, cost tracking, fallback chains |
| **P2** | AWS ECS Fargate | Task def, ECS service, ALB, IAM, auto-scaling |
| **P2** | AWS Lambda | Lightweight/event-driven agents, API Gateway |
| **P3** | AWS EKS | Helm chart, IRSA, HPA + KEDA |
| **P3** | Google GKE | GKE Autopilot, Workload Identity |
| **P4** | Oracle OKE | Oracle Kubernetes Engine |

### M24: Model Gateway & Model Support

#### Model Gateway Strategy

The platform uses a **layered gateway approach**. Users choose their routing tier in `agent.yaml`. The platform doesn't force a single gateway — it supports direct calls, self-hosted proxies, and SaaS gateways.

```yaml
# agent.yaml — gateway configuration examples
model:
  provider: anthropic
  name: claude-sonnet-4.6
  gateway: none            # Tier 0: direct SDK call (default)
  # gateway: litellm       # Tier 1: self-hosted LiteLLM proxy
  # gateway: openrouter    # Tier 2: OpenRouter SaaS
  # gateway: portkey       # Tier 3: Portkey (advanced routing + caching)

# Local development with Ollama — no API keys needed
model:
  provider: ollama
  name: llama3.2           # or: mistral, codellama, gemma2, phi3, etc.
  base_url: http://localhost:11434  # default Ollama endpoint
```

**Gateway Tiers:**

| Tier | Gateway | Type | When to Use | Priority |
|------|---------|------|-------------|----------|
| **0** | Direct SDKs + Ollama | No gateway | Dev/test, single-provider, local-first | v0.3 (default) |
| **1** | LiteLLM | OSS, self-hosted | Production — unified API, cost tracking, fallbacks, 100+ providers | v0.4 (recommended) |
| **2** | OpenRouter | SaaS | Teams that don't want to manage infra, quick multi-provider access | v1.0 |
| **3** | Portkey | OSS + SaaS | Advanced — semantic caching, virtual keys, load balancing, guardrails | Post v1.0 |

**Tier 0: Direct Provider SDKs + Ollama (v0.3 — ships with builders)**
- [ ] Direct calls to OpenAI, Anthropic, Google SDKs — no proxy, no extra service
- [ ] **Ollama integration**: connect to local Ollama instance for zero-cost local dev/test
- [ ] Ollama auto-detection: if Ollama is running on localhost:11434, auto-register available models in Model Registry
- [ ] Ollama model pull from UI: "Download Model" button → triggers `ollama pull <model>` behind the scenes
- [ ] Ollama models in Agent Builder: appear in model picker alongside cloud models, tagged as "Local"
- [ ] Provider abstraction layer: `engine/providers/` with unified `generate()` interface (Ollama uses OpenAI-compatible API)
- [ ] Provider config: API keys via env vars or secrets manager (not needed for Ollama)
- [ ] Fallback chains: if primary model fails, try fallback model (e.g., Ollama → cloud API)
- [ ] This is the default — zero config, works out of the box

**Ollama for local development workflow:**

```
1. Install Ollama (ollama.com)
2. ollama pull llama3.2                     # download a model
3. garden dev agent my-agent                # start dev sandbox
   → Agent runs locally against Ollama
   → No API keys, no cost, no internet needed
   → Iterate on prompts + tools with instant feedback

4. When ready for production:
   → Change model.provider to anthropic/openai/google
   → garden submit agent my-agent           # submit for review
```

- [ ] `garden dev` auto-detects Ollama and uses it if no cloud API keys are configured
- [ ] Docker Compose includes optional Ollama service (GPU pass-through if available)
- [ ] Ollama model suggestions: recommend small models for dev (llama3.2, phi3) vs. large for eval (llama3.1:70b)

**Tier 1: LiteLLM Proxy (v0.4 — ships with observability)**
- [ ] LiteLLM proxy runs as a Docker service in the platform stack (port 4000)
- [ ] Unified OpenAI-compatible API across all providers
- [ ] Built-in cost tracking: per-request token counts + dollar costs → feeds into cost dashboard
- [ ] Built-in rate limiting: per-user, per-model, per-team limits
- [ ] Model fallback chains: configure primary → fallback → fallback in LiteLLM config
- [ ] Load balancing: round-robin or least-latency across multiple API keys / deployments
- [ ] LiteLLM config auto-generated from Model Registry (registered models → litellm config.yaml)
- [ ] Enhance existing `connectors/litellm/` connector for deeper integration
- [ ] Dashboard: LiteLLM status page (connected models, request count, error rate)
- [ ] Docker Compose: add LiteLLM service alongside platform services

**Tier 2: OpenRouter (v1.0)**
- [ ] OpenRouter integration: route through OpenRouter API with single API key
- [ ] Model mapping: map Agent Garden model names to OpenRouter model IDs
- [ ] Cost pass-through: ingest OpenRouter cost data into cost dashboard
- [ ] Use case: teams that want multi-provider access without running LiteLLM

**Tier 3: Portkey (post v1.0)**
- [ ] Portkey gateway integration (self-hosted or cloud)
- [ ] Semantic caching: cache responses for similar prompts (saves cost on repeated queries)
- [ ] Virtual keys: abstract API keys behind Portkey virtual keys (rotate without agent restart)
- [ ] Advanced load balancing: weighted routing, geo-routing, cost-optimized routing
- [ ] Guardrails: Portkey's built-in content filtering + our guardrail engine

**Gateway Dashboard UI:**
- [ ] Gateway settings page: configure which gateway tier is active
- [ ] Gateway status: connected/disconnected, request throughput, error rate
- [ ] Model routing visualization: which models route through which gateway
- [ ] Cost comparison: show cost difference between direct vs. gateway routing
- [ ] Gateway logs: recent requests with model, latency, cost, status

#### Supported Models (v1.0)

| Provider | Models | Category | Available From |
|----------|--------|----------|----------------|
| **Ollama (local)** | Llama 3.2, Mistral, Phi-3, Gemma 2, CodeLlama, etc. | Text generation (local) | v0.3 |
| **Ollama (local)** | nomic-embed-text, mxbai-embed-large | Embeddings (local) | v0.3 |
| **OpenAI** | GPT-5, GPT-5 mini, GPT-5 nano | Text generation | v1.0 |
| **Anthropic** | Claude Opus 4.6, Claude Sonnet 4.6, Claude Haiku 4.5 | Text generation | v1.0 |
| **Google** | Gemini 3 Pro, Gemini 3 Flash, Gemini 3 Nano | Text generation | v1.0 |
| **OpenAI** | text-embedding-3-small, text-embedding-3-large | Embeddings | v1.0 |
| **Google** | Gemini Nano Embedding | Embeddings | v1.0 |
| **OpenAI** | Sora | Video generation | v1.0 |
| **Google** | Veo | Video generation | v1.0 |

- [ ] Model catalog: all v1.0 models registered with pricing, context window, capabilities
- [ ] Model provider abstraction: unified interface across OpenAI, Anthropic, Google, Ollama
- [ ] Ollama models tagged as "Local / Free" in model picker — appear first during dev
- [ ] Model selection in Agent Builder: filter by provider, capability, price, local/cloud
- [ ] Embedding model selection in RAG Builder: pick embedding model for vector indexing (Ollama embeddings for local dev)
- [ ] Video model support: input/output handling for Sora and Veo in agent tools

#### Later Models (post v1.0)

| Provider | Models | Priority |
|----------|--------|----------|
| Perplexity | Sonar Pro, Sonar | P1 |
| xAI | Grok 3, Grok 3 mini | P1 |
| Open Source | Llama 4, Mistral Large, DeepSeek V3 (via LiteLLM/OpenRouter) | P2 |
| Cohere | Command R+, Embed v4 | P3 |

### M25: Enterprise Auth & Secrets Management

#### Enterprise Auth
- [ ] SSO/SAML integration (Okta, Auth0, Azure AD)
- [ ] OIDC provider support
- [ ] SCIM user provisioning
- [ ] Directory sync: auto-provision teams from identity provider

#### Secrets Management (replaces .env for production)
- [ ] External secrets manager integration: AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, Doppler
- [ ] Secrets config in `platform.yaml`: `secrets_backend: aws_secrets_manager | gcp_secret_manager | vault | doppler | env`
- [ ] API keys stored in secrets manager — never in `.env` or database in production
- [ ] Secret references in `agent.yaml`: `api_key: secret://openai-key` (resolved at deploy time)
- [ ] Secrets UI: Settings → Secrets page — list secrets (name + masked value), create/rotate/delete
- [ ] Secret rotation: update a secret → all agents using it pick up the new value without redeploy
- [ ] Team-scoped secrets: each team manages their own API keys and secrets
- [ ] Audit log: track who accessed/changed which secrets and when
- [ ] CLI: `garden secret list`, `garden secret set openai-key`, `garden secret rotate openai-key`
- [ ] Migration path: `garden secrets migrate --from env --to aws` — migrate `.env` keys to secrets manager

### M26: AgentOps — Unified Operations Dashboard

The "single pane of glass" for running agents in production. Consolidates observability, cost, health, and governance into one view.

#### 26.1 — Control Plane Dashboard
- [ ] Fleet overview: all agents across all teams in one view (status, health, cost, last deploy)
- [ ] Fleet health heatmap: grid of agents colored by health status
- [ ] Top-N widgets: most expensive agents, most errors, highest latency, most invocations
- [ ] Real-time event stream: live feed of deploys, errors, alerts, approvals across all agents
- [ ] Team comparison: side-by-side team metrics (agent count, spend, error rate)

#### 26.2 — Incident Management
- [ ] Incident detection: auto-create incidents on sustained error rate spikes or health check failures
- [ ] Incident timeline: chronological view of events leading to and during an incident
- [ ] Incident actions: restart agent, rollback to previous version, scale up, disable
- [ ] Incident status: open → investigating → mitigated → resolved
- [ ] Post-incident report: auto-generated summary (timeline, root cause, resolution, impact)
- [ ] On-call integration: PagerDuty / OpsGenie webhook for incident notifications

#### 26.3 — Canary Deployments & Rollbacks
- [ ] Canary deploy: route N% of traffic to new version, monitor metrics
- [ ] Auto-rollback: if error rate or latency exceeds threshold during canary, auto-revert
- [ ] Blue/green deploy: instant cutover with instant rollback capability
- [ ] Deploy approval gates: require manual approval + passing eval before production promote
- [ ] Deploy freeze windows: block deploys during configured time periods

#### 26.4 — Cost Intelligence
- [ ] Cost forecasting: project next month's spend based on trends
- [ ] Cost anomaly detection: alert on unexpected spend spikes
- [ ] Cost optimization suggestions: "switch model X to Y to save Z%", "reduce max_tokens"
- [ ] Chargeback reports: per-team cost allocation (exportable PDF/CSV)
- [ ] Budget enforcement: hard limits that block requests when budget exhausted

#### 26.5 — Compliance & Reporting
- [ ] SOC2 evidence collection: auto-generate audit evidence (access logs, change logs, approval records)
- [ ] Compliance dashboard: at-a-glance compliance status per regulation
- [ ] Scheduled reports: weekly/monthly email summaries (cost, health, usage, incidents)
- [ ] Data retention policies: configurable TTL on traces, logs, conversations
- [ ] Export everything: all data exportable as CSV/JSON for external compliance tools

### M27: Production Hardening
- [ ] Zero known critical security issues
- [ ] 85%+ test coverage across all modules
- [ ] Load testing: k6 scripts for all critical paths
- [ ] Performance benchmarks tracked per release
- [ ] Docs site (GitHub Pages or Mintlify)
- [ ] API stability: versioned API with deprecation policy

---

## Out of Scope

These are intentionally deferred indefinitely:

- Custom model hosting / serving (use vLLM, TrueFoundry, etc.)
- Full RLHF/PPO training pipelines (post v1.0 — see Tier 4 in Builder spec)
- Mobile app
- Azure deployer (community contribution welcome post-v1.0)
- Chat interface for end-users (we build the platform, not the consumer UI)

---

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Backend language | Python (FastAPI) | AI ecosystem is Python-first; native integration with agent frameworks |
| CLI | Python (Typer + Rich) | Same language as backend; single install |
| Dashboard | React 19 + TypeScript + Tailwind v4 | Industry standard; shadcn/ui for Vercel-quality aesthetic |
| Database | PostgreSQL + SQLAlchemy + Alembic | Mature, reliable; pgvector for future semantic search |
| ORM | SQLAlchemy (async) | Most capable Python ORM; async support |
| Cache | Redis | Rate limiting, task queue, session cache |
| Container build | Docker | Universal; BuildKit for multi-platform |
| Auth | JWT + bcrypt | Simple, stateless; OAuth2 providers added in v1.0 |
| Distribution | `pip install agent-garden` (CLI+SDK), Docker image (platform), Helm chart (K8s) | pip for devs, Docker for teams, Helm for production |
| Project structure | Single-agent (`agent.yaml` at root) or workspace (`garden.yaml` + `agents/`) | Scales from solo developer to multi-team |
| Config format | YAML as source of truth (all resource types) | Human-readable; JSON Schema for validation; round-trip safe; IDE and UI interoperable |
| YAML parser | ruamel.yaml (round-trip mode) | Preserves comments and key order — critical for CLI/UI interop |
| Version control | Git (local repo, bridgeable to GitHub/GitLab) | Branch-per-draft, PR-based review, semver tagging on merge |
| Git library | gitpython (Python) | Programmatic Git ops without shelling out; supports bare repos |
| Local LLM | Ollama | Zero-cost local dev/test; auto-detected; OpenAI-compatible API |
| Model gateway | Layered: Direct SDKs + Ollama → LiteLLM → OpenRouter → Portkey | No forced gateway; LiteLLM for production (OSS, self-hosted, 100+ providers) |
| Tracing | Langfuse (primary), MLflow (alt), OTel export | Open-source, self-hostable, purpose-built for LLM observability |
| Evaluation | Built-in eval framework + LLM-as-judge | No vendor lock-in; pluggable scoring |
| Agent improvement | Prompt optimization (primary) → few-shot curation → fine-tuning (advanced) | Most gains come from prompts, not model training |
| Environments | dev (sandbox) → staging (on merge) → production (on promote) | Registry populated after merge, not after PR |
| Agent protocol | A2A (Google spec) + MCP (Anthropic spec) | Industry-standard interop; no proprietary protocols |
| Design system | shadcn/ui + Lucide icons | Vercel/Linear aesthetic; composable, not a monolithic library |

---

## Issue Labels

| Label | Meaning |
|-------|---------|
| `area:dashboard` | Frontend / UI work |
| `area:registry` | Registry service + API |
| `area:engine` | Deploy engine, runtimes, builders |
| `area:cli` | CLI commands |
| `area:governance` | RBAC, audit, cost tracking |
| `area:observability` | Tracing, monitoring, alerting |
| `area:eval` | Evaluation framework, datasets, scoring |
| `area:agentops` | Fleet management, incidents, canary deploys |
| `area:mcp` | MCP server integration |
| `area:a2a` | Agent-to-Agent protocol |
| `area:docs` | Documentation |
| `type:feature` | New feature |
| `type:bug` | Bug fix |
| `type:polish` | UX improvement, design refinement |
| `good first issue` | Approachable for new contributors |
| `help wanted` | Community contribution welcome |

---

*Last updated: March 11, 2026*
*Status: v0.1 complete (M1–M5). Starting v0.2 (Registry UI).*
