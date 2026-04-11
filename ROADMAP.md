# ROADMAP.md — AgentBreeder

> **Vision:** The enterprise one-stop shop for Agent Marketplace, Agent Evaluation, Agent Observability, Cost Monitoring, and AgentOps.
>
> **Guiding principle:** The dashboard and registry are the product. The CLI and deploy engine are the on-ramp. Every milestone should make the UI more useful and the registry more valuable.

---

## Release Overview

| Release | Name | Theme | Milestones | Status |
|---------|------|-------|------------|--------|
| **v0.1** | Foundation | CLI + Registry + Basic Dashboard | M1–M5 | Done |
| **v0.2** | Registry UI | Rich registry pages, YAML editor, prompt manager | M6–M7 | Done |
| **v0.3** | Builders | All builders + Git workflow + approval + environments + playground + 2 SDKs (LangGraph, OpenAI Agents) + Cloud Run deploy | M8–M13, M23 | Done |
| **v0.4** | Observability | Tracing, cost monitoring, RBAC & teams, audit trail, lineage + Full Code SDK (Python) + `agentbreeder eject` | M14–M17, M28 | Done |
| **v1.0** | GA | Eval framework, golden datasets, regression detection, CI gates, feedback loop + orchestration YAML | M18, M29 | Done |
| **v1.1** | Connectivity | A2A protocol, MCP server hub, multi-agent orchestration + visual orchestration canvas + TS SDK | M19–M20, M30 | Done |
| **v1.2** | Marketplace | Community templates, ratings, one-click deploy | M21–M22 | Done |
| **v1.3** | Enterprise | Additional SDKs (ADK, CrewAI, Claude), model catalog, SSO, AgentOps + Full Code orchestration SDK | M24–M27, M31 | Done |
| **v1.4** | Distribution | PyPI packages, Docker Hub images, Homebrew tap, release automation | M32 | In Progress (code complete, infra setup remaining) |
| **v1.5** | Multi-Cloud | AWS ECS Fargate, Azure Container Apps, and general Kubernetes deployers | M33 | Done |
| **v1.6** | Framework Depth | Complete all 4 missing runtime builders + LangGraph/OpenAI Agents feature depth + runtime tracing | M34 | Done |

---

## Three-Tier Builder Model — No Code / Low Code / Full Code

> **Core principle:** AgentBreeder supports three ways to build agents and orchestrations. All three tiers compile to the same internal representation and share the same deploy pipeline, governance, and observability. Users can move between tiers without losing work.

### Why Three Tiers?

Real teams aren't homogeneous. Within the same org:

| Person | What they need | Tier |
|--------|---------------|------|
| Product manager | "I want a support bot that uses our FAQ and routes to humans" | No Code |
| ML engineer | "I need to tweak the prompt, add a custom tool, configure RAG" | Low Code |
| Senior engineer | "I need a multi-agent pipeline with custom state management and dynamic routing" | Full Code |

Without tier mobility, No Code tools become prisons. With it, they become on-ramps.

### The Three Tiers

#### No Code (Visual UI)
- **Agent Development**: Drag-and-drop visual builder. Pick model, tools, prompt, guardrails from registry. ReactFlow canvas with node types for every component. Zero YAML knowledge required.
- **Agent Orchestration**: Visual canvas where agents are nodes and edges are routing/handoff rules. Define "if customer asks about billing → route to billing-agent" visually.
- **Output**: Generates valid, human-readable `agent.yaml` / `orchestration.yaml`.
- **Who**: PMs, analysts, citizen builders, non-engineers prototyping.
- **Analogy**: Retool for agent building.

#### Low Code (YAML)
- **Agent Development**: Write `agent.yaml` in any IDE (Cursor, Claude Code, VS Code, vim) or the dashboard YAML editor. Schema-aware autocomplete, live validation.
- **Agent Orchestration**: Write `orchestration.yaml` defining agent graph, routing strategy, shared state.
- **Output**: The YAML files themselves ARE the artifact. No compilation step.
- **Who**: ML engineers, DevOps, developers comfortable with config files.
- **Analogy**: Docker Compose for agents.

#### Full Code (Python/TS SDK)
- **Agent Development**: `from agentbreeder import Agent, Tool, Memory` — define agents programmatically with full control over routing, tool selection, state management.
- **Agent Orchestration**: SDK for complex workflows that YAML can't express — dynamic agent spawning, stateful workflows, human-in-the-loop breakpoints, conditional branching based on runtime data.
- **Output**: SDK generates `agent.yaml` + bundles custom code. The deploy pipeline consumes both.
- **Who**: Senior engineers, researchers, teams that have outgrown YAML.
- **Analogy**: Pulumi (code) vs. Terraform (config) — same infra, different authoring experience.

### Compilation Model — All Tiers Converge

```
┌────────────┐     ┌────────────┐     ┌────────────────┐
│  No Code   │     │  Low Code  │     │   Full Code    │
│  (UI)      │     │  (YAML)    │     │   (SDK)        │
└─────┬──────┘     └─────┬──────┘     └───────┬────────┘
      │                  │                     │
      │  generates       │  is                 │  generates +
      │  YAML            │  YAML               │  bundles code
      │                  │                     │
      └──────────────────┼─────────────────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  agent.yaml + code   │  ← Unified internal format
              └──────────┬───────────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
          ▼              ▼              ▼
    Config Parser   Runtime Builder   Deployer
    (same)          (same)            (same)
          │              │              │
          └──────────────┼──────────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  Running Agent       │
              │  + Governance        │
              │  + Observability     │
              │  + Registry Entry    │
              └──────────────────────┘
```

The deploy pipeline does NOT know which tier produced the config. This is intentional — it means governance, cost tracking, audit trail, and registry work identically regardless of how the agent was built.

### Tier Mobility (Ejection)

```
No Code ──"View YAML"──→ Low Code ──"agentbreeder eject"──→ Full Code
   ↑                                                      │
   └──────── "Import YAML" ←───── (manual) ←─────────────┘
```

| Transition | How it works | Data preserved |
|-----------|-------------|----------------|
| **No Code → Low Code** | Visual builder shows "View YAML" tab. The generated YAML is valid, readable, and editable. | 100% — YAML is the source of truth |
| **Low Code → Full Code** | CLI: `agentbreeder eject my-agent --sdk python`. Generates a Python project scaffold that recreates the YAML config as SDK code. | 100% — SDK generates equivalent agent.yaml |
| **Full Code → Low Code** | Not automatic (code can express things YAML can't). But the SDK always generates a valid `agent.yaml` that can be imported. | Partial — custom code logic not representable in YAML |
| **Low Code → No Code** | "Import YAML" in the visual builder opens the YAML and renders it as nodes on the canvas. | 100% if YAML uses standard patterns |

### Orchestration Across Tiers

Multi-agent orchestration follows the same three-tier model:

**No Code Orchestration:**
```
┌─────────────────────────────────────────────────────────────┐
│  Orchestration Canvas                                        │
│                                                              │
│  ┌──────────┐    billing?    ┌──────────────┐               │
│  │  Triage  │───────────────→│ Billing Agent │               │
│  │  Agent   │    technical?  ├──────────────┤               │
│  │          │───────────────→│ Tech Support  │               │
│  │          │    default     ├──────────────┤               │
│  │          │───────────────→│ General Agent │               │
│  └──────────┘                └──────────────┘               │
│                                                              │
│  Strategy: [Router ▼]   Shared state: [Session context ▼]   │
└─────────────────────────────────────────────────────────────┘
```

**Low Code Orchestration (`orchestration.yaml`):**
```yaml
name: support-pipeline
version: "1.0.0"
description: "Multi-agent support routing"

strategy: router          # router | sequential | parallel | hierarchical

agents:
  triage:
    ref: agents/triage-agent
    routes:
      billing: agents/billing-agent
      technical: agents/tech-support-agent
      default: agents/general-agent

  billing:
    ref: agents/billing-agent
    fallback: agents/general-agent

  technical:
    ref: agents/tech-support-agent
    fallback: agents/general-agent

  general:
    ref: agents/general-agent

shared_state:
  type: session_context
  backend: redis

deploy:
  target: local
```

**Full Code Orchestration (Python SDK):**
```python
from agentbreeder import Orchestration, Router, Agent
from agentbreeder.routing import ClassifierRouter

# Custom routing logic that YAML can't express
class SupportRouter(ClassifierRouter):
    async def route(self, message, context):
        # Dynamic routing based on classifier + business rules
        intent = await self.classify(message)
        if intent == "billing" and context.user.tier == "enterprise":
            return "priority-billing"  # VIP gets different agent
        if context.escalation_count > 2:
            return "human-handoff"     # Auto-escalate after 2 failures
        return intent

pipeline = Orchestration(
    name="support-pipeline",
    router=SupportRouter(model="claude-haiku-4"),
    agents={
        "billing": Agent.from_registry("agents/billing-agent"),
        "priority-billing": Agent.from_registry("agents/priority-billing-agent"),
        "technical": Agent.from_registry("agents/tech-support-agent"),
        "general": Agent.from_registry("agents/general-agent"),
        "human-handoff": Agent.from_registry("agents/human-handoff-agent"),
    },
    shared_state={"type": "session_context", "backend": "redis"},
)

# Deploy with the same pipeline as YAML-defined agents
pipeline.deploy(target="cloud-run")
```

### Competitive Positioning

No other platform offers all three tiers + orchestration + deploy + governance in one product:

| Platform | No Code | Low Code | Full Code | Orchestration | Deploy + Govern |
|----------|:-------:|:--------:|:---------:|:-------------:|:---------------:|
| LangGraph Studio | Partial | No | Yes | Yes | No |
| CrewAI Studio | Yes | No | Yes | Yes | No |
| Dify | Yes | Partial | No | Partial | Partial |
| Flowise | Yes | No | Partial | Partial | No |
| AWS Bedrock Agents | Partial | No | Yes | Partial | Yes (AWS only) |
| **AgentBreeder** | **Yes** | **Yes** | **Yes** | **Yes** | **Yes** |

### Implementation Roadmap for Three Tiers

| Phase | What ships | Tier coverage |
|-------|-----------|---------------|
| **v0.3** | Low Code YAML builders, No Code visual agent builder (ReactFlow) | Low Code complete, No Code partial |
| **v0.4** | Full Code Python SDK for agent development | Full Code (agents) |
| **v1.0** | Orchestration YAML (`orchestration.yaml`), orchestration UI | Low Code + No Code (orchestration) |
| **v1.1** | TypeScript SDK, visual orchestration canvas | Full Code (agents TS), No Code (orchestration) |
| **v1.3** | Full Code orchestration SDK (Python + TS) | Full Code (orchestration) |

---

## Distribution & Project Structure

### How Users Install AgentBreeder

Two packages: a **CLI tool** (what developers install) and a **platform server** (what runs the dashboard + API + registry).

```
┌──────────────────────────────────────────────────────────────────┐
│                     What to install                               │
│                                                                   │
│  Solo developer / getting started:                                │
│    pip install agentbreeder        ← CLI + SDK                    │
│    agentbreeder init my-project          ← scaffold project             │
│    agentbreeder dev                      ← run locally (Ollama)         │
│                                                                   │
│  Team / full platform:                                            │
│    pip install agentbreeder        ← CLI + SDK (on each machine)  │
│    docker compose up               ← platform server              │
│    open http://localhost:3000      ← dashboard                    │
│                                                                   │
│  CI/CD / automation:                                              │
│    pip install agentbreeder        ← CLI only (no UI needed)      │
│    agentbreeder deploy --env production  ← deploy from pipeline         │
└──────────────────────────────────────────────────────────────────┘
```

#### Package Distribution

| Package | Registry | Installs | Contains |
|---------|----------|----------|----------|
| `agentbreeder` | PyPI (`pip install agentbreeder`) | `agentbreeder` CLI command | CLI, SDK, engine, runtimes, config parser, provider abstraction |
| `@agentbreeder/dashboard` | npm (`npm install @agentbreeder/dashboard`) | Dashboard dev server | React SPA (only needed for dashboard development/customization) |
| `agentbreeder` | Docker Hub / GHCR | `docker pull agentbreeder/server` | Full platform: API + dashboard + workers (all-in-one image) |
| `agentbreeder` | Helm chart | `helm install agentbreeder` | Kubernetes deployment (API + dashboard + PostgreSQL + Redis) |

**Typical install paths:**

```bash
# Path 1: Developer — build agents locally
pip install agentbreeder
agentbreeder init my-agent-project
cd my-agent-project
agentbreeder dev                              # starts local sandbox

# Path 2: Team — run the full platform
git clone https://github.com/agentbreeder/agentbreeder
cd agentbreeder
docker compose up -d                    # starts API + dashboard + DB + Redis
pip install agentbreeder                # install CLI on each dev machine
open http://localhost:3000              # dashboard

# Path 3: Production — deploy platform to cloud
helm install agentbreeder deploy/helm/agentbreeder \
  --set database.url=postgresql://... \
  --set redis.url=redis://...
```

#### `pip install agentbreeder` — What It Includes

```
agentbreeder (PyPI package)
├── agentbreeder                  ← CLI binary (entry point)
│   ├── agentbreeder init         ← scaffold new project
│   ├── agentbreeder dev          ← local dev sandbox
│   ├── agentbreeder deploy       ← deploy agent
│   ├── agentbreeder provider     ← manage LLM providers
│   ├── agentbreeder chat         ← playground chat
│   ├── agentbreeder eval         ← run evaluations
│   ├── agentbreeder submit       ← submit PR for review
│   └── ...                 ← all other commands
├── agentbreeder.sdk        ← Python SDK for programmatic use
│   ├── AgentBreederClient   ← API client
│   ├── AgentConfig         ← Pydantic models
│   └── deploy(), list()... ← SDK methods
├── agentbreeder.engine     ← config parser, runtimes, deployers
└── agentbreeder.providers  ← LLM provider abstraction
```

```python
# SDK usage (programmatic)
from agentbreeder import AgentBreederClient

client = AgentBreederClient(base_url="http://localhost:8000")
agents = client.agents.list()
client.agents.deploy("my-agent", env="staging")
```

---

### Project Directory Structure

Two project types: **single-agent** (most common starting point) and **workspace** (team with multiple agents).

#### Single-Agent Project

Created by `agentbreeder init my-agent`:

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
├── .gitignore              ← includes .env, __pycache__, .agentbreeder/
├── .agentbreeder/                ← AgentBreeder metadata (gitignored)
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
  backend: postgresql            # v0.3: in_memory | postgresql  (v0.4+: redis)

deploy:
  target: local                 # v0.3: local | cloud-run  (v1.3+: ecs-fargate, kubernetes, databricks)
  resources:
    cpu: "0.5"
    memory: "512Mi"
```

#### Workspace (Multi-Agent Project)

Created by `agentbreeder init my-workspace --workspace`, or naturally evolves when a team has multiple agents:

```
my-workspace/
├── agentbreeder.yaml                 ← workspace config (lists all agents, shared settings)
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

**`agentbreeder.yaml` (workspace root config):**

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
| `registry://` | AgentBreeder registry | `registry://tools/search@v1.2` | Versioned resource from the platform registry |
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
  - ref: https://summarizer.myteam.agentbreeder.cloud
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
4. Lock versions: create a dependency lockfile (.agentbreeder/lock.yaml)
   → pinning exact versions of all registry:// references
   → ensures reproducible deploys
5. Proceed with build + deploy
```

**Dependency lockfile (`.agentbreeder/lock.yaml`):**

```yaml
# Auto-generated by agentbreeder deploy — do not edit
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
agentbreeder deps                               # show all references and their resolved versions
agentbreeder deps update                        # update all @latest refs to current latest
agentbreeder deps update tools/web-search       # update one reference
agentbreeder deps lock                          # regenerate lock file
agentbreeder deps check                         # verify all references still resolve (nothing deleted/broken)
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
agentbreeder init my-agent
agentbreeder init my-agent --framework langgraph     # with framework preset
agentbreeder init my-agent --framework google-adk    # another framework

# Create workspace
agentbreeder init my-workspace --workspace
agentbreeder init my-workspace --workspace --agents research,summarizer,reviewer

# Add resources to existing project
agentbreeder add agent customer-support              # creates agents/customer-support/
agentbreeder add tool web-search                     # creates tools/web-search/
agentbreeder add prompt safety-guardrail             # creates prompts/safety-guardrail/
agentbreeder add rag product-docs                    # creates rag/product-docs/
agentbreeder add memory shared-redis                 # creates memory/shared-redis.yaml
agentbreeder add mcp-server internal-api             # creates mcp-servers/internal-api/

# Work with the project
agentbreeder dev                                     # start all agents in dev mode
agentbreeder dev research-agent                      # start one agent
agentbreeder deploy research-agent --env staging     # deploy one agent
agentbreeder deploy --all --env staging              # deploy all agents

# Validate everything
agentbreeder validate                                # validate all YAML files in workspace
agentbreeder validate agents/research-agent          # validate one agent
```

#### What `agentbreeder init` Generates

**Single agent (`agentbreeder init my-agent`):**

```
$ agentbreeder init my-agent

  🌱 Creating new agent project: my-agent

  ? Framework: (use arrows)
    ❯ LangGraph / DeepAgent
      OpenAI Agents SDK
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
    .gitignore        — ignores .env, .agentbreeder/, __pycache__
    tests/            — test directory with sample eval
    README.md         — getting started guide

  Next steps:
    cd my-agent
    pip install -r requirements.txt
    agentbreeder provider add ollama          # or: agentbreeder provider add openai
    agentbreeder dev                          # start local sandbox
    agentbreeder chat                         # chat with your agent
```

**Workspace (`agentbreeder init my-workspace --workspace`):**

```
$ agentbreeder init my-workspace --workspace

  🌱 Creating new workspace: my-workspace

  ? How many agents to start with: 2

  ? Agent 1 name: research-agent
  ? Agent 1 framework: LangGraph / DeepAgent

  ? Agent 2 name: summarizer-agent
  ? Agent 2 framework: YAML-only

  ✅ Workspace created at ./my-workspace/

  Files created:
    agentbreeder.yaml                       — workspace config
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
    agentbreeder provider add ollama
    agentbreeder dev                          # starts both agents
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
- [x] CLI commands: `agentbreeder init`, `validate`, `deploy`, `list`, `describe`, `search`, `logs`, `status`, `teardown`, `scan`
- [x] CLI UX: Rich progress bars, colored output, `--json` flag, meaningful errors
- [x] `agentbreeder init` interactive wizard (framework + cloud selection, scaffold generation)

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

## v0.2 — "Registry UI" (Done)

> **Goal:** The dashboard becomes the primary way to explore, edit, and manage everything in the registry. Not just read-only browse — full CRUD with a polished UI.

### M6: Rich Registry Pages

#### 6.1 — Agent Registry Enhancements
- [x] Agent detail page: full YAML viewer with syntax highlighting
- [x] Agent detail: environment variables display (masked secrets)
- [x] Agent detail: dependency graph tab (which tools, models, prompts this agent uses)
- [x] Agent config diff viewer (compare two versions side-by-side) — LCS-based diff algorithm, collapsible unchanged sections
- [x] Agent clone action (duplicate config as starting point for a new agent)
- [x] Agent YAML inline editor with validation (edit + save from dashboard) — validateYamlBasic(), unsaved changes warning
- [x] Agent status badge: health indicator (green/yellow/red/gray) — added degraded + error statuses
- [ ] Agent logs tab: live log streaming from deployed container (SSE) — deferred to v0.4

#### 6.2 — Prompt Registry (Full)
- [x] Prompt CRUD from dashboard (create, edit, delete)
- [x] Prompt editor: Markdown editor with live preview panel
- [x] Prompt versioning: create new version, view version history
- [x] Prompt diff viewer (compare two versions, highlight changes)
- [x] Prompt metadata: tags, description, linked agents
- [x] Prompt test panel: send a test message using the prompt, see LLM response inline — model selector, variable detection, mock responses
- [x] Backend: prompt versions table, version CRUD API routes — migration 005, list/create/get/diff endpoints

#### 6.3 — Tool & Model Registry Enhancements
- [x] Tool detail page: schema viewer (JSON Schema rendered as a form)
- [ ] Tool detail: usage stats (which agents reference this tool) — deferred to v0.4
- [ ] Tool health indicator (for MCP servers: last ping status, latency) — deferred to v0.4
- [x] Model detail page: pricing info, context window, capabilities matrix
- [x] Model comparison view (select 2-3 models, compare side-by-side)
- [ ] Model usage stats (which agents use this model, token counts) — deferred to v0.4

#### 6.4 — Global Registry Features
- [x] Unified resource creation dialog ("New..." → Agent / Tool / Prompt / Model)
- [x] Tag management: add/remove tags on any resource, filter by tags everywhere
- [x] Favorites/bookmarks: star resources, "My Favorites" filter
- [x] Activity feed: recent changes across all resources (who changed what, when)
- [x] Bulk actions: select multiple resources, bulk tag / bulk delete — BulkActionBar component + useBulkSelect hook
- [x] Export: download any resource as YAML/JSON

---

### M7: Dashboard Polish & Navigation

#### 7.1 — Navigation & Layout
- [x] Resizable sidebar (drag to resize, collapse to icons) — localStorage persistence, tooltip labels when collapsed
- [x] Breadcrumb navigation with entity type icons
- [x] Tab persistence (remember which tab was active on agent detail)
- [x] URL-driven state: all filters, search queries, and tabs reflected in URL — useUrlState hook
- [x] Keyboard shortcuts: `n` for new, `/` for search, `g a` for agents, `g t` for tools
- [x] Empty states: helpful illustrations + CTAs when no data exists — EmptyState component on all list pages

#### 7.2 — Tables & Data Display
- [x] Sortable columns on all tables (click header to sort) — useSortable hook + SortableColumnHeader on all 5 list pages
- [x] Column visibility toggle (show/hide columns)
- [x] Pagination with configurable page size (10, 25, 50, 100) — usePagination hook + Pagination component
- [ ] Table row expansion (inline detail without navigating away) — deferred to v0.4
- [x] Data export from any table (CSV, JSON)
- [x] Relative timestamps with absolute tooltip ("2 hours ago" → hover: "Mar 11, 2026 14:30") — RelativeTime component

#### 7.3 — Forms & Validation
- [x] Form validation: inline error messages, field-level validation
- [x] Unsaved changes warning (prompt before navigating away) — useUnsavedChanges hook + UnsavedChangesDialog + React Router blocker
- [x] Toast notifications for all actions (success, error, info) — 5 variants, progress bar, max 3 visible, configurable duration
- [x] Confirmation dialogs for destructive actions (delete, teardown) — ConfirmDialog with loading state + destructive variant
- [x] Loading skeletons on all pages (not spinners) — SkeletonTable + SkeletonDetail components

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
│  │ OpenAI   │ (v0.3)    │ LiteLLM  │ (v0.4)   │ Ollama   │ (v0.3) │
│  │          │           │          │          │          │         │
│  └──────────┘           └──────────┘          └──────────┘         │
│  ┌──────────┐           ┌──────────┐                               │
│  │Anthropic │ (v0.4)    │OpenRouter│ (v1.3)                        │
│  │          │           │          │                               │
│  └──────────┘           └──────────┘                               │
│  ┌──────────┐           ┌──────────┐                               │
│  │ Google   │ (v1.1)    │ Portkey  │ (v1.3+)                       │
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
agentbreeder provider list                              # list configured providers
agentbreeder provider add openai                        # interactive: paste API key, test, discover models
agentbreeder provider add openai --api-key sk-proj-...  # non-interactive
agentbreeder provider add ollama                        # auto-detect local Ollama
agentbreeder provider add ollama --base-url http://remote:11434  # remote Ollama
agentbreeder provider test openai                       # verify connection + latency
agentbreeder provider models openai                     # list models from provider
agentbreeder provider disable openai                    # disable without deleting key
agentbreeder provider remove openai                     # remove provider + delete key

agentbreeder provider add litellm --base-url http://localhost:4000  # connect gateway
agentbreeder provider add openrouter --api-key or-...               # connect OpenRouter
```

**API Key Storage Strategy — progressive:**

| Release | Storage Method | How It Works |
|---------|---------------|--------------|
| **v0.2–v0.3** | `.env` file + env vars | Simple. User pastes key → saved to `.env`. CLI reads from env vars. Dashboard reads from server env. |
| **v0.4** | Encrypted in PostgreSQL | Keys encrypted at rest (Fernet). Team-scoped keys. Key rotation. |
| **v1.3** | External secrets managers | AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, Doppler. Keys never touch our DB. |

**v0.2–v0.3: Simple `.env` approach (ship now):**

```
CLI onboarding:
$ agentbreeder provider add openai
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
# .env file (created/updated by agentbreeder provider add)
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
agentbreeder provider add openai                        # interactive: prompts for key, saves to .env
agentbreeder provider add openai --api-key sk-proj-...  # non-interactive, saves to .env
agentbreeder provider add anthropic                     # same flow
agentbreeder provider add ollama                        # no key needed, just base_url

agentbreeder provider list                              # show providers + masked keys + status
agentbreeder provider test openai                       # verify key still works
agentbreeder provider remove openai                     # remove key from .env
```

- [x] `.env` file as the key store for v0.2–v0.3 (simple, works everywhere) — providers use env vars
- [x] `agentbreeder provider add` writes keys to `.env` file (creates if missing)
- [ ] `.env` auto-added to `.gitignore` (warn if not) — deferred to v0.4
- [ ] UI provider setup writes to server-side `.env` via API (`POST /api/v1/providers/{name}/configure`) — deferred to v0.4
- [x] API never returns full keys — only masked suffix (`sk-••••1234`)
- [x] Keys loaded from env vars at startup — standard `os.environ` / `pydantic-settings`
- [ ] `.env.example` shipped with all provider key placeholders (documented, no real values) — deferred to v0.4

**Implementation items:**

- [x] Settings page in sidebar navigation (gear icon, bottom of sidebar)
- [x] Provider registry: `providers` table (name, type, base_url, status, is_enabled, last_verified, model_count, avg_latency_ms) — keys NOT in DB, migration 006
- [x] Provider connection test: `POST /api/v1/providers/{id}/test` — verify key works, measure latency
- [x] Model discovery: `POST /api/v1/providers/{id}/discover` — list available models with pricing, context window, capabilities per provider type (OpenAI 6, Anthropic 3, Google 2, Ollama 4, LiteLLM 3, OpenRouter 4)
- [x] Provider toggle: `POST /api/v1/providers/{id}/toggle` — enable/disable provider
- [x] Provider CRUD: full REST API (list, get, create, update, delete) — 8 endpoints total
- [x] DiscoveredModel schema with rich metadata (context_window, pricing, capabilities)
- [x] Provider onboarding 4-step wizard UI (pick provider → configure → test → discover models)
- [x] Auto-register discovered models into Model Registry with pricing, context window, capabilities
- [x] Provider health check: background task pings each provider, updates status — check_all_providers()
- [x] Provider status indicator on settings page: green (healthy), yellow (slow), red (down), gray (disabled)
- [x] Ollama auto-detection: detect-ollama endpoint, auto-register provider + discover models
- [ ] Ollama model pull: `POST /api/v1/providers/ollama/pull` — trigger `ollama pull <model>`, stream progress — deferred to v0.4
- [ ] Provider cost display: month-to-date spend per provider (from cost tracking data) — deferred to v0.4
- [ ] "Get API Key" links: direct links to each provider's API key page (platform.openai.com, console.anthropic.com, etc.) — deferred to v0.4
- [x] First-run onboarding wizard: provider status endpoint for first-run detection
- [x] CLI: `agentbreeder provider` subcommand with add/list/test/models/disable/remove

---

## v0.3 — "Builders" (Done)

> **Goal:** Every building block — agents, prompts, tools, RAG indexes, memory — has a dedicated builder UI with Git integration, an approval workflow, and registry promotion with versioning. The agent builder composes from all other registries. This is the core product.

### Shared Builder Infrastructure

#### Core Principle: YAML is the Source of Truth

Every resource (agent, prompt, tool, RAG index, memory config) is defined by a **YAML file on disk**. The dashboard UI is a view and editor for that YAML — not a separate data store. This means:

1. **CLI/IDE → UI works:** A developer writes `agent.yaml` in Claude Code, Cursor, VS Code, or vim → pushes to Git → the dashboard UI shows it immediately with full visual editing.
2. **UI → CLI/IDE works:** A user builds an agent in the visual builder → the UI saves a valid `agent.yaml` to Git → the developer can open it in any editor and continue working.
3. **Round-trip fidelity:** Opening a YAML file in the UI and saving it without changes produces an identical file. No reordering keys, no stripping comments, no adding UI-only metadata into the YAML.

```
Developer's editor                AgentBreeder Dashboard
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

- [x] **Single file format per resource type:** `agent.yaml`, `prompt.yaml` + `prompt.md`, `tool.yaml`, `rag.yaml`, `memory.yaml`
- [x] **JSON Schema for every YAML format:** published schemas that any IDE can use for autocomplete + validation
- [x] **UI reads from YAML:** when opening a resource in the dashboard, the UI parses the YAML and renders it — never stores a separate representation (builders parse YAML and render it)
- [x] **UI writes to YAML:** every save in the dashboard writes back to a valid YAML file — the file is the storage, not a database row (builders save back to valid YAML)
- [x] **Comment preservation:** UI edits must preserve YAML comments (use `ruamel.yaml` round-trip parser, not `pyyaml`)
- [x] **Key order preservation:** fields stay in the order the user wrote them (no alphabetical re-sorting)
- [x] **No UI-only state in YAML:** visual builder layout info (node positions, etc.) stored separately in `.agentbreeder/` metadata, never in the YAML itself
- [x] **CLI validation:** `agentbreeder validate <file>` validates any YAML file against the schema — same validation the UI uses
- [ ] **File watching:** dashboard detects external file changes (from git pull, editor saves) and reloads automatically — **deferred to v0.4**
- [ ] **Conflict resolution:** if both UI and external editor change the same file, show a merge dialog (theirs vs. ours vs. manual merge) — **deferred to v0.4**
- [x] **API contract:** `GET /api/v1/builders/{type}/{name}/yaml` returns raw YAML, `PUT` accepts raw YAML — the API speaks YAML, not a custom JSON format (builders.py: GET/PUT YAML endpoints)
- [x] **Import from anywhere:** drag-and-drop any valid YAML file onto the dashboard to import it into the registry (POST /builders/import endpoint)

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
│   CLI: agentbreeder dev        CLI: agentbreeder deploy         CLI: agentbreeder promote  │
│        agentbreeder test            --env staging              --env production│
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
agentbreeder env list                           # show all environments
agentbreeder env status                         # which version is in each env
agentbreeder dev agent my-agent                 # start DEV sandbox for an agent
agentbreeder deploy agent my-agent --env staging   # deploy to staging (usually auto)
agentbreeder promote agent my-agent --env production  # promote to production
agentbreeder promote agent my-agent --env production --skip-eval  # emergency hotfix
```

#### Agent Testing Interface

Three levels of testing, available in both CLI and UI:

**Level 1: Playground (Interactive Testing)**

Test an agent by chatting with it directly — available for any resource in any environment.

```
CLI:  agentbreeder chat agent my-agent                    # chat in terminal
      agentbreeder chat agent my-agent --env staging      # chat with staging version
      agentbreeder chat agent my-agent --verbose           # show tool calls, token counts

UI:   Agent detail → "Playground" tab
      → Chat interface with the agent
      → Tool call visualization (expandable cards showing input/output)
      → Token counter + cost estimate per message
      → Model/prompt override: temporarily swap model or prompt to compare behavior
      → "Save as Eval Case" button: save a conversation turn as a golden test case
```

- [x] Playground chat in dashboard: send messages, see streamed responses
- [x] Tool call visualization: expandable cards with input/output JSON
- [x] Token counter + cost estimate per message
- [x] Model override: temporarily swap the model to compare behavior
- [x] Prompt override: edit the system prompt for this session only
- [x] "Save as Eval Case": capture a conversation turn as a golden test case
- [x] CLI: `agentbreeder chat` — interactive terminal chat with any agent
- [x] CLI: `agentbreeder chat --verbose` — show tool calls, token counts, latency

**Level 2: Evaluation (Automated Testing — see v1.0 for full spec)**

Run golden test suites against an agent to measure quality systematically.

```
CLI:  agentbreeder eval agent my-agent                    # run default eval suite
      agentbreeder eval agent my-agent --dataset edge-cases  # specific dataset
      agentbreeder eval agent my-agent --judge claude-sonnet  # specify judge model

UI:   Agent detail → "Evaluations" tab
      → Run eval, see results, compare against baseline
```

- [ ] Auto-eval on merge: when a PR merges, eval suite runs automatically in staging — **deferred to v1.0**
- [ ] Eval gate for production: promote is blocked until eval score ≥ threshold — **deferred to v1.0**
- [ ] Regression alerts: if staging eval scores drop vs. previous version, alert the team — **deferred to v1.0**

**Level 3: Production Feedback (Continuous Learning)**

Collect feedback from production usage to improve agents over time.

```
User interacts with agent
    → Trace captured (input, output, tool calls, latency, cost)
    → Human feedback: thumbs up/down, corrections, ratings
    → Feedback stored in feedback_events table
    → Curate feedback into eval datasets (close the loop)
```

- [ ] Feedback collection API: `POST /api/v1/feedback` — thumbs up/down, text correction, 1-5 rating — **deferred to v1.0**
- [ ] Feedback widget: embeddable UI component for end-user feedback on agent responses — **deferred to v1.0**
- [ ] Feedback dashboard: aggregate scores per agent, trend over time, worst-performing responses — **deferred to v1.0**
- [ ] "Curate to Dataset" button: select good/bad feedback items → add to eval dataset — **deferred to v1.0**
- [ ] Feedback-to-eval pipeline: auto-generate eval cases from low-rated responses — **deferred to v1.0**
- [ ] Backend: `feedback_events` table (trace_id, agent_id, rating, correction, user_id, timestamp) — **deferred to v1.0**

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

- [ ] Prompt A/B testing: deploy two prompt versions, split traffic, compare metrics — **deferred to v1.0**
- [ ] Prompt variant management: create variants of the same prompt, tag with experiment IDs — **deferred to v1.0**
- [ ] Auto-suggest prompt improvements: LLM analyzes failure cases and suggests prompt edits — **deferred to v1.0**
- [ ] Prompt optimization history: track which prompt changes improved/degraded which metrics — **deferred to v1.0**

**Tier 2: Few-Shot Example Curation**

```
Good conversations from production (high-rated by users)
    → Curate as few-shot examples
    → Inject into system prompt as examples
    → Re-run eval to validate
```

- [ ] Example curator: select high-rated conversations → format as few-shot examples — **deferred to v1.0**
- [ ] Auto-inject examples: add curated examples to system prompt at runtime (configurable count) — **deferred to v1.0**
- [ ] Example effectiveness tracking: measure if adding specific examples improves eval scores — **deferred to v1.0**

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

- [ ] Training dataset builder: curate traces + feedback into fine-tuning datasets — **deferred to v1.3**
- [ ] Export formats: OpenAI JSONL, Anthropic messages, Google Vertex format — **deferred to v1.3**
- [ ] Fine-tune job trigger: `POST /api/v1/fine-tune/jobs` — submit to provider API — **deferred to v1.3**
- [ ] Fine-tune job tracking: status, cost, estimated completion time — **deferred to v1.3**
- [ ] Auto-register model: on completion, register fine-tuned model in Model Registry — **deferred to v1.3**
- [ ] Side-by-side eval: compare fine-tuned model vs. base model on same eval suite — **deferred to v1.3**
- [ ] CLI: `agentbreeder fine-tune agent my-agent --provider openai --dataset my-dataset` — **deferred to v1.3**

**Tier 4: RLHF / Reinforcement Learning (future, post v1.3)**

Use human feedback to systematically improve agent behavior through reward modeling.

```
Production traces + human ratings (thumbs up/down, corrections)
    → Train reward model (what makes a "good" response for this agent)
    → Use reward model to rank response candidates
    → PPO/DPO training to optimize the agent's model
    → Or: use reward model to select best prompt variant automatically
```

- [ ] Reward model training: train a classifier on human feedback (good/bad responses) — **deferred to post-v1.3**
- [ ] Preference pairs: collect A/B comparisons from users ("response A or B is better?") — **deferred to post-v1.3**
- [ ] DPO dataset export: export preference pairs in DPO format for external training — **deferred to post-v1.3**
- [ ] Reward-guided prompt selection: use reward model to auto-select best prompt variant — **deferred to post-v1.3**
- [ ] This is post-v1.3 — requires significant production data volume to be effective — **deferred to post-v1.3**

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
CLI:  agentbreeder create agent my-agent
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
      agentbreeder save agent my-agent                # or: git add + git commit
      agentbreeder save agent my-agent -m "add tools" # custom commit message

UI:   Edit in YAML editor / Visual builder / Prompt editor → Save
      → Each save = Git commit on the draft branch
      → Commit message auto-generated or user-provided

Both: Multiple saves = multiple commits on the same branch
      Full commit history visible in both CLI and UI
─────────────────────────────────────────────────────────

Step 3: DIFF — review what changed
─────────────────────────────────────────────────────────
CLI:  agentbreeder diff agent my-agent                # diff vs. main
      agentbreeder diff agent my-agent --version v1.2 # diff vs. specific version
      agentbreeder log agent my-agent                 # commit history

UI:   "Changes" tab in builder → Monaco diff editor (side-by-side)
      "History" tab → commit log with expandable diffs
─────────────────────────────────────────────────────────

Step 4: SUBMIT — request review (creates a PR)
─────────────────────────────────────────────────────────
CLI:  agentbreeder submit agent my-agent
      agentbreeder submit agent my-agent -m "Ready for review: added Zendesk tool"
      → Creates a Pull Request (internal PR, or GitHub/GitLab PR if external repo)
      → PR contains: YAML diff, commit history, auto-generated summary
      → Status changes: draft → submitted

UI:   Click "Submit for Review" → add description → Submit
      → Same PR created via API
      → Badge changes to "Pending Review"
─────────────────────────────────────────────────────────

Step 5: REVIEW — approve, reject, or comment
─────────────────────────────────────────────────────────
CLI:  agentbreeder review list                        # see pending reviews
      agentbreeder review show agent my-agent         # see PR diff + comments
      agentbreeder review approve agent my-agent      # approve
      agentbreeder review reject agent my-agent -m "needs X" # reject with comment
      agentbreeder review comment agent my-agent -m "looks good, minor nit on line 12"

UI:   Approvals page → click PR → Review UI:
        - Side-by-side YAML diff with line-level comments
        - Approve / Request Changes / Reject buttons
        - Comment thread per PR
        - See which agents/tools/prompts are affected (impact analysis)
─────────────────────────────────────────────────────────

Step 6: MERGE — publish to registry
─────────────────────────────────────────────────────────
CLI:  (automatic on approval, or manual:)
      agentbreeder publish agent my-agent             # merge + tag + publish
      agentbreeder publish agent my-agent --version 2.0.0  # explicit version bump

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
CLI:  agentbreeder rollback agent my-agent --version v1.0.0
      → Creates a new commit that reverts to v1.0.0 state
      → Goes through the same submit → review → merge flow
      → (or --force to skip review for emergencies)

UI:   Agent detail → Version History → click "Rollback to this version"
      → Same flow: creates revert commit, optionally submits for review
─────────────────────────────────────────────────────────
```

**Additional CLI commands for Git workflow:**

```bash
agentbreeder status                     # show all resources with uncommitted changes
agentbreeder status agent my-agent      # show status of one resource (branch, commits ahead, review status)
agentbreeder branches                   # list all draft branches
agentbreeder branches --mine            # list only your draft branches
agentbreeder discard agent my-agent     # delete draft branch, discard changes
agentbreeder sync                       # pull latest from remote Git repo
agentbreeder clone <repo-url>           # clone external Git repo into platform
```

#### Git Integration — Backend Implementation
- [x] Git backend: each resource type stored in directories (agents/, prompts/, tools/, rag/, memory/)
- [x] Branch management: auto-create `draft/{user}/{type}/{name}` branches on create/edit
- [x] Commit on save: every save (CLI or UI) = Git commit with author, timestamp, message
- [x] Diff engine: compute YAML-aware diffs (highlight changed fields, not just text lines)
- [x] PR system: internal PR model (or bridge to GitHub/GitLab PRs for external repos)
- [x] PR metadata: title, description, YAML diff, commit list, impact analysis (affected dependents)
- [x] Merge engine: fast-forward merge to main on approval, conflict detection if main changed
- [x] Tag on publish: Git tag with semver (e.g., `agents/my-agent/v1.0.0`)
- [ ] Auto-versioning: detect change scope (patch/minor/major) based on diff analysis — **deferred to v0.4**
- [ ] File watching: detect changes made by external editors, update dashboard in real-time — **deferred to v0.4**
- [ ] Git clone/pull: sync resources from external Git repos (GitHub, GitLab, Bitbucket) — **deferred to v0.4**
- [ ] Git push: push approved resources to external repos (optional, configurable) — **deferred to v0.4**
- [x] Backend: `resource_versions` table (resource_type, resource_id, version, git_sha, status, author, approved_by)
- [x] Backend: `pull_requests` table (id, resource_type, resource_id, branch, status, submitter, reviewer, title, description, comments_json)

#### Approval Workflow
- [x] Status state machine: `draft` → `submitted` → `in_review` → `approved` / `changes_requested` / `rejected` → `published`
- [x] Submit for review: UI ("Submit for Review" button) + CLI `agentbreeder submit`
- [x] Approvals page in dashboard: pending reviews queue, grouped by resource type, filterable by team
- [x] Review UI: side-by-side YAML diff, line-level comments, approve/reject/request-changes buttons
- [x] Comment threads: threaded discussion per PR (persisted in `pull_requests.comments_json`)
- [x] Re-submit after changes: address feedback, re-submit — reviewer sees new diff against previous review
- [x] On approval: auto-merge to main, auto-tag, auto-publish to registry
- [ ] Notifications: email/webhook on submit, comment, approve, reject, publish — **deferred to v0.4**
- [ ] Review policies: configurable per team (e.g., "require 2 approvals for agents", "auto-approve prompt edits") — **deferred to v0.4**
- [ ] Backend: `approval_requests` table (resource_type, resource_id, version, status, submitter, reviewer, comment) — **deferred to v0.4**

---

### M8: Prompt Builder

#### 8.1 — Prompt Editor
- [x] Markdown editor with live preview panel (60/40 split editor/preview)
- [x] Template variable support: `{{variable}}` syntax with variable list panel — auto-detection via extractVariables()
- [x] Variable defaults and descriptions (editable in sidebar)
- [x] Prompt metadata: name, description, tags, linked agents
- [x] Character/token counter (estimate tokens for selected model)
- [x] "Test Prompt" panel: select a model from Model Registry, send test message, see response inline
- [x] Test with variables: fill in template variables, see rendered prompt, send to LLM

#### 8.2 — Prompt Versioning & Registry
- [x] Version history: list all versions with author, date, diff summary
- [x] Promote to registry: on approval, prompt appears in Prompt Registry with version (via PR workflow)
- [x] Compare versions: side-by-side diff of any two versions (config-diff-viewer)
- [x] Linked agents view: which agents reference this prompt (auto-detected from agent.yaml)
- [x] Import: paste existing prompt text
- [x] Export: download as YAML, copy to clipboard

---

### M9: Tool Builder

#### 9.1 — Tool Editor
- [x] Tool definition editor: name, description, input schema (JSON Schema), output schema
- [x] Schema builder UI: form-based JSON Schema editor (add fields, set types, required flags)
- [x] Or raw JSON Schema editor with validation
- [x] Tool implementation: code editor for the tool function (Python)
- [x] Dependencies: specify pip packages required by the tool
- [x] Tool metadata: tags, category, author

#### 9.2 — Tool Execution Sandbox
- [ ] Sandbox environment: Docker-based isolated execution (no network access by default) — **deferred to v0.4**
- [x] "Run Tool" panel: provide sample input JSON, execute tool, see output
- [x] Execution log: stdout/stderr captured and displayed
- [ ] Execution history: previous runs with input/output preserved — **deferred to v0.4**
- [x] Timeout configuration: max execution time per tool (default 30s)
- [ ] Network toggle: allow/deny network access per tool in sandbox — **deferred to v0.4**
- [x] Backend: `POST /api/v1/tools/sandbox/execute` — runs tool in ephemeral Docker container

#### 9.3 — Tool Versioning & Registry
- [x] Git-backed versioning (same pattern as prompts)
- [x] Promote to Tool Registry on approval
- [ ] Tool health check: periodic sandbox execution with sample input to verify tool still works — **deferred to v0.4**
- [ ] Usage stats: which agents reference this tool — **deferred to v0.4**

#### 9.4 — MCP Server Management
- [x] MCP server registry: register servers with name, endpoint, transport (stdio/SSE/streamable-HTTP)
- [x] MCP server CRUD API: `POST /api/v1/mcp-servers`, `GET`, `PUT`, `DELETE` — full REST, test, discover endpoints
- [x] MCP server health monitoring: test endpoint with latency tracking
- [x] MCP server auto-discovery: enhance existing `connectors/mcp_scanner/` (mcp_scanner connector exists)
- [x] MCP Servers dashboard page: list servers with status indicators, nav entry
- [x] MCP Server detail: tools list, schema viewer, health status
- [x] "Register MCP Server" dialog: enter endpoint, select transport
- [x] Backend: `mcp_servers` table (name, endpoint, transport, status, last_ping, tool_count) — migration 007

#### 9.5 — Example MCP Server
- [x] `examples/mcp-server/` — minimal MCP server (Python, stdio transport) with 2-3 example tools
- [x] MCP server SDK helper: `from agentbreeder.mcp import serve` one-liner to expose tools as MCP
- [x] Docker Compose includes the example MCP server alongside the platform (profiles: examples)

---

### M10: RAG Builder (Vector DB Index Management)

> **Strategy:** pgvector carries you through v1.3 — zero new services, runs on existing PostgreSQL. Cloud vector DBs deferred to post v1.3.

#### 10.1 — Vector Index Registry (v0.3)
- [x] Vector index as a registry resource: name, description, embedding model, chunk strategy, source (in-memory simulation)
- [ ] **pgvector** as the only vector backend — v0.3: in-memory simulation; pgvector deferred to v0.4
- [ ] `CREATE EXTENSION vector` in migration, vector columns for embeddings — deferred to v0.4 with pgvector
- [x] Index metadata: document count, embedding dimensions, last updated, size
- [x] Backend: `vector_indexes` table (name, backend, embedding_model, doc_count, status, config_json) — in-memory simulation

#### 10.2 — Data Ingestion — Phased

**v0.3 — File upload only:**
- [x] Upload files: drag-and-drop PDF, TXT, MD, CSV, JSON files → auto-chunk → embed → index (in-memory simulation)
- [x] Chunking strategies: fixed-size + recursive text splitter (covers 90% of cases)
- [x] Embedding models: OpenAI `text-embedding-3-small` + Ollama `nomic-embed-text` (aligns with v0.3 providers)
- [x] Ingestion progress: real-time progress bar (documents processed / total)
- [x] Backend: `ingestion_jobs` table (index_id, source_type, status, doc_count, error_log) — in-memory simulation

**v0.4 — Richer ingestion sources:**
- [ ] Google Drive source: OAuth2 connect, browse folders, select files/folders to ingest
- [ ] Google Sheets source: OAuth2 connect, select spreadsheet, map columns to document fields
- [ ] Web URL source: provide URL(s), crawl pages, extract text, chunk, embed
- [ ] Semantic chunking: split by heading/paragraph for better retrieval quality
- [ ] Scheduled re-ingestion: cron-based refresh from connected sources

**v1.3 — Production-grade ingestion:**
- [ ] Database source: connect to PostgreSQL/MySQL, select table/query, ingest rows as documents
- [ ] Re-ranking: Cohere Rerank or cross-encoder re-ranking for improved retrieval quality
- [ ] Multi-index queries: search across multiple RAG indexes in a single agent request
- [ ] ChromaDB as alternative vector backend (embedded, no server, good for single-machine deploys)

**Post v1.3 — Cloud vector DBs:**
- [ ] Pinecone connector (P1 — managed, scales infinitely)
- [ ] Weaviate connector (P2 — strong hybrid search)
- [ ] Qdrant connector (P2 — fast, Rust-based)
- [ ] Milvus connector (P3 — enterprise-grade)
- [ ] Confluence/Notion ingestion (P1 — enterprise knowledge sources)
- [ ] S3/GCS bucket ingestion (P1 — document lakes)

#### 10.3 — Vector Search UI (v0.3)
- [x] Search panel: type a query, select an index, see ranked results with similarity scores
- [x] Result display: document chunk, metadata, similarity score, source file/row
- [ ] Filter results by: metadata fields, date range, source
- [x] Search settings: top_k, similarity threshold
- [x] Hybrid search: combine pgvector similarity + PostgreSQL `tsvector` full-text search (in-memory simulation)
- [x] Search API: `POST /api/v1/rag/search` — programmatic access for agents

**v1.3 additions:**
- [ ] Compare indexes: run same query against two indexes, see results side-by-side
- [ ] Reranking toggle in search settings

#### 10.4 — RAG Versioning & Registry (v0.3)
- [x] Git-backed index configuration versioning (via PR workflow)
- [x] Promote to RAG Registry on approval
- [ ] Index snapshots: point-in-time backup/restore
- [x] Linked agents view: which agents reference this index

---

### M11: Memory Builder

> **Strategy:** In-memory + PostgreSQL for v0.3 (zero new services). Redis + smarter memory types added progressively. Semantic memory uses pgvector (same as RAG builder).

#### 11.1 — Memory Backend Registry — Phased

**v0.3 — Two backends (already in the stack):**
- [x] Memory as a registry resource: name, backend type, configuration, scope
- [x] Supported backends (v0.3):
  - **In-Memory**: buffer window, ephemeral, for dev/testing — zero config
  - **PostgreSQL**: persistent conversation history (v0.3: simulated in-memory, real PostgreSQL v0.4)
- [x] Backend configuration UI: max messages, namespace pattern
- [x] Memory metadata: backend type, message count, storage size, linked agents
- [x] Backend: `memory_configs` table (name, backend_type, config_json, status) — in-memory simulation
- [x] Backend: `memory_messages` table (config_id, agent_id, session_id, role, content, metadata JSONB, created_at) — in-memory simulation

**v0.4 — Add Redis + smarter types:**
- [ ] **Redis** backend: conversation buffer, TTL-based auto-expiry, fast read/write (already in Docker Compose)
- [ ] Redis configuration UI: connection string, TTL, max messages, namespace pattern
- [ ] Summary memory type: periodic LLM-generated summaries of long conversations (keeps context window small)
- [ ] Entity memory type: extract and track entities (people, products, tickets) across conversations

**v1.3 — Advanced memory patterns:**
- [ ] **Semantic memory**: vector-indexed memory via pgvector for similarity-based recall ("What did the user say about billing?")
- [ ] **Shared memory**: multiple agents read/write to the same memory space (research agent writes, summarizer reads)
- [ ] **Memory scoping**: per-user, per-session, per-agent, per-team memory isolation
- [ ] Memory TTL policies: auto-expire conversations older than N days (GDPR-friendly)

**Post v1.3:**
- [ ] Cross-agent memory federation (P1 — Agent A's findings auto-available to Agent B)
- [ ] External memory services: Mem0, Zep integration (P2 — for teams that want managed)
- [ ] Long-term user profiles (P2 — persistent preferences across agents and sessions)
- [ ] Memory analytics (P3 — what do agents remember? retrieval patterns, storage trends)

#### 11.2 — Memory Management UI (v0.3)
- [x] Memory instances page: list all configured memory backends with stats
- [x] Memory detail: browse stored conversations (namespaced by agent + user)
- [x] Conversation viewer: message-by-message display with timestamps
- [x] Search memory: full-text search across stored conversations
- [x] Delete conversations: bulk delete by agent, user, or date range
- [x] Memory usage dashboard: storage size over time, message count by agent

#### 11.3 — Memory Types (phased)

| Type | Description | Available |
|------|-------------|-----------|
| **Buffer Window** | Sliding window of last N messages (configurable N) | v0.3 |
| **Buffer** | Full conversation history (no truncation) | v0.3 |
| **Summary** | LLM-generated summaries of long conversations | v0.4 |
| **Entity** | Extract and track entities across conversations | v0.4 |
| **Semantic** | Vector-indexed memory for similarity-based recall (pgvector) | v1.3 |

- [x] Each type configurable per agent in agent.yaml `memory:` block (Buffer Window + Buffer in v0.3)

#### 11.4 — Memory Versioning & Registry (v0.3)
- [x] Git-backed configuration versioning (via PR workflow)
- [x] Promote to Memory Registry on approval
- [x] Linked agents view: which agents use this memory config

---

### M12: Agent Builder (Composes from All Registries)

The agent builder is the capstone — it pulls from every other registry to assemble a complete agent.

#### 12.1 — YAML Builder Mode
- [x] Full YAML editor with validation — agent-builder.tsx with YAML/visual toggle
- [ ] Schema-aware autocomplete (field names, enum values, registry refs) — deferred to v0.4
- [x] Insert snippets: model config, tool reference, guardrail block, deploy block
- [x] Live validation: validation API (POST /agents/validate) with structured errors/warnings
- [x] "Deploy" button: trigger deploy pipeline directly from editor (DeployDialog wired to agent builder)
- [x] "Save Draft" button: save agent config to registry via POST /agents/from-yaml

#### 12.2 — Visual Builder Mode
- [x] ReactFlow canvas with drag-and-drop nodes
- [x] Node types: Agent, Model, Tool, MCP Server, Prompt, Memory, RAG Index, Guardrail
- [x] Node palette sidebar: drag nodes from palette onto canvas
- [x] Edge connections: wire components to the agent node
- [x] Property panel: click a node to edit its properties in a side panel
- [x] Generates valid `agent.yaml` from the graph
- [x] "Deploy from Canvas" button

#### 12.3 — Registry Pickers (pull from all registries)
- [x] **Model picker**: browse Model Registry, filter by provider/capability/price, select model — RegistryPicker component
- [x] **Tool picker**: browse Tool Registry, see schema preview, add to agent — RegistryPicker component
- [x] **Prompt picker**: browse Prompt Registry, preview content, select version — RegistryPicker component
- [x] **LLM Provider picker**: browse providers (Anthropic, OpenAI, Google, etc.)
- [x] **MCP Server picker**: browse MCP servers, select individual tools from each server (via registry picker)
- [x] **Memory picker**: browse Memory Registry, select backend + type
- [x] **RAG Index picker**: browse RAG Registry, preview index stats, select for agent
- [x] Each picker: search, filter, preview, "Add to Agent" button
- [x] Selected components appear as YAML refs in code mode

#### 12.4 — Deploy from Dashboard
- [x] Deploy dialog: select target (Local Docker, Google Cloud Run)
- [x] Pre-deploy validation: check all registry refs resolve, MCP servers reachable, model available
- [x] Deploy progress: 8-step pipeline visualization, real-time
- [x] Deploy log streaming in a bottom panel
- [x] Rollback button on failed deploys
- [x] Teardown button on agent detail page (via deploy management)

#### 12.5 — Agent Versioning & Registry
- [x] Git-backed versioning (same pattern as all builders) (works via PR workflow)
- [x] Promote to Agent Registry on approval (via approval workflow)
- [x] Dependency lock: snapshot exact versions of all referenced resources at deploy time (via resolver)
- [ ] Dependency update check: "newer versions available" indicator on agent detail — **deferred to v0.4**

---

### M13: Framework Examples & Runtime Builders (1 per SDK)

Each example is a complete, working agent with `agent.yaml`, source code, `requirements.txt`, and a README.

#### Supported SDKs (v0.3 — First Release)

| SDK | Example | Runtime Builder | Priority | Rationale |
|-----|---------|----------------|----------|-----------|
| **LangGraph / DeepAgent** | `examples/langgraph-agent/` (exists) | `engine/runtimes/langgraph.py` (exists) | P0 (v0.3) | Largest community, complex graph model proves runtime abstraction |
| **OpenAI Agents SDK** | `examples/openai-agents-agent/` | `engine/runtimes/openai_agents.py` | P0 (v0.3) | Largest enterprise footprint, simple model contrasts LangGraph |

#### Next SDKs (v1.3 → M25, tracked in #35–#38)

| SDK | Example | Runtime Builder | Priority | Issue | Notes |
|-----|---------|----------------|----------|-------|-------|
| **CrewAI** | `examples/crewai-agent/` | `engine/runtimes/crewai.py` | **P1** | [#35](https://github.com/rajitsaha/agentbreeder/issues/35) | 25k+ stars; sequential + hierarchical crews |
| **Claude SDK** | `examples/claude-sdk-agent/` | `engine/runtimes/claude_sdk.py` | **P1** | [#36](https://github.com/rajitsaha/agentbreeder/issues/36) | Anthropic-native; streaming + tool use required |
| **Google ADK** | `examples/google-adk-agent/` | `engine/runtimes/google_adk.py` | **P1** | [#37](https://github.com/rajitsaha/agentbreeder/issues/37) | Gemini-native; natural pairing with GCP Cloud Run |
| **Custom (BYOF)** | via init scaffold | `engine/runtimes/custom.py` | **P2** | [#38](https://github.com/rajitsaha/agentbreeder/issues/38) | BYO Dockerfile or thin wrapper; bridge for unsupported frameworks |

#### Feature Depth Gaps — Existing Runtimes (v1.6 → M34)

| Gap | Runtime | Priority | Issue | Notes |
|-----|---------|----------|-------|-------|
| Subgraphs, HITL breakpoints, persistence checkpointers | LangGraph | **P1** | [#39](https://github.com/rajitsaha/agentbreeder/issues/39) | Core LangGraph production features; `PostgresSaver` for stateful agents |
| Agent handoffs, nested agent patterns | OpenAI Agents | **P1** | [#40](https://github.com/rajitsaha/agentbreeder/issues/40) | Primary value prop of OAI Agents SDK; `HandoffOutputItem` handling |
| Per-framework OTel tracing (LLM calls, tool use, agent steps) | All runtimes | **P1** | [#41](https://github.com/rajitsaha/agentbreeder/issues/41) | Required for AgentOps dashboard and cost attribution to work at runtime |

#### Later SDKs (post v1.6)

| SDK | Priority | Notes |
|-----|----------|-------|
| **AutoGen v0.4** | P1 | Microsoft; async-first multi-agent; huge enterprise footprint |
| **Pydantic AI** | P1 | Type-safe; fastest growing; natural fit for FastAPI stack |
| **Strands** | P2 | AWS-native; natural pairing with M33 AWS ECS deployer |
| **Smolagents** | P2 | Hugging Face code agents; popular for OSS/research |
| **Haystack** | P3 | deepset; production RAG + agent pipelines |
| **Semantic Kernel** | P3 | Microsoft; primary SDK for .NET enterprise shops |
| **LlamaIndex Workflows** | P3 | Event-driven agent architecture; distinct from their RAG layer |
| **Mastra (TypeScript)** | P3 | Requires TS runtime builder |

#### 13.1 — Examples (v0.3 — ship with 2 SDKs)
- [x] `examples/langgraph-agent/` — LangGraph / DeepAgent (polish existing)
- [x] `examples/openai-agents-agent/` — OpenAI Agents SDK

#### 13.2 — Runtime Builders (v0.3)
- [x] `engine/runtimes/openai_agents.py` — OpenAI Agents runtime builder
- [x] Each runtime: Dockerfile generation, dependency resolution, entrypoint config
- [x] Integration test per runtime: build container, start, verify `/health` responds

#### 13.3 — Additional SDKs (M25 — v1.3 remaining work)
- [ ] `engine/runtimes/crewai.py` — CrewAI runtime builder ([#35](https://github.com/rajitsaha/agentbreeder/issues/35))
- [ ] `engine/runtimes/templates/crewai_server.py` — CrewAI FastAPI server template
- [ ] `engine/runtimes/claude_sdk.py` — Claude SDK runtime builder ([#36](https://github.com/rajitsaha/agentbreeder/issues/36))
- [ ] `engine/runtimes/templates/claude_sdk_server.py` — Claude SDK server template (streaming + tool use)
- [ ] `engine/runtimes/google_adk.py` — Google ADK runtime builder ([#37](https://github.com/rajitsaha/agentbreeder/issues/37))
- [ ] `engine/runtimes/templates/google_adk_server.py` — Google ADK server template (ADC auth)
- [ ] `engine/runtimes/custom.py` — Custom (BYOF) runtime builder ([#38](https://github.com/rajitsaha/agentbreeder/issues/38))
- [ ] `engine/runtimes/templates/custom_server.py` — thin wrapper for BYO agents
- [ ] `examples/crewai-agent/` — CrewAI multi-agent crew example
- [ ] `examples/claude-sdk-agent/` — Anthropic Claude SDK example (tool use + streaming)
- [ ] `examples/google-adk-agent/` — Google Agent Development Kit example
- [ ] Unit tests for all 4 new runtimes (≥95% coverage on changed files)
- [ ] Integration test stubs for all 4 new runtimes

---

## v0.4 — "Observability" (Done)

> **Goal:** Full visibility into what agents are doing, how much they cost, and who has access. Tracing, cost tracking, RBAC, and audit — all surfaced in the dashboard.

### M14: Agent Tracing & Observability

Integrate with Langfuse (primary) and support MLflow as an alternative backend. Every LLM call, tool invocation, and agent step gets traced automatically.

**API:** 6 endpoints under `/api/v1/traces` — see `api/routes/tracing.py`, `api/services/tracing_service.py`.

#### 14.1 — Tracing Backend Integration
- [x] Langfuse Python SDK integration (`langfuse` package) as the default tracing backend
- [x] MLflow Tracing support as an alternative (`mlflow.tracing`) — configurable via env var
- [x] OpenTelemetry export: emit OTel-compatible spans for each agent invocation
- [x] Auto-instrumentation: patch LangGraph, OpenAI Agents SDK + OpenAI API calls automatically (Anthropic in v0.4, Google in v1.1, CrewAI/ADK in v1.3)
- [x] Trace context propagation: pass trace IDs through tool calls and MCP requests
- [x] Config: `TRACING_BACKEND=langfuse|mlflow|otlp|none`, connection URL, API keys
- [x] Backend: `traces` table for local trace metadata (trace_id, agent, duration, token_count, cost, status)

#### 14.2 — Tracing Dashboard
- [x] Traces page: list of recent traces with agent name, duration, token count, cost, status
- [x] Trace detail page: waterfall/timeline view of spans (LLM calls, tool calls, agent steps)
- [x] Span detail: input/output content, model used, token counts, latency
- [x] Filter traces by: agent, status (success/error), date range, min duration, min cost
- [x] Trace search: full-text search over inputs/outputs
- [x] Link from agent detail page → "View Traces" (pre-filtered)
- [x] Embedded Langfuse iframe option (for users running Langfuse, deep-link into its UI)

#### 14.3 — Agent Monitoring
- [x] Agent health dashboard: uptime, request count, error rate per agent
- [x] Latency charts: P50, P95, P99 over time per agent
- [x] Token usage charts: daily token consumption per agent, per model
- [x] Error log: recent errors with stack traces, grouped by error type
- [x] Alerting rules: email/webhook notifications on error rate spike or latency threshold
- [x] Backend: `agent_metrics` table (agent_id, timestamp, request_count, error_count, p50_ms, p95_ms, tokens_in, tokens_out)

### M15: RBAC & Teams

**API:** 12 endpoints under `/api/v1/teams` — see `api/routes/teams.py`, `api/services/team_service.py`.

- [x] Team management UI: create teams, add/remove members
- [x] Role definitions: Viewer, Deployer, Admin
- [x] Resource ownership: every agent/tool/prompt belongs to a team
- [x] Permission checks on all CRUD operations (API + UI)
- [x] Team switcher in sidebar (filter everything by team)
- [x] Invite flow: invite users by email
- [x] Team-scoped API keys: each team can set their own provider keys (overrides platform-level)
- [x] API key encryption: keys stored encrypted in PostgreSQL (Fernet, keyed from `SECRET_KEY`)
- [x] Key rotation: update a key → test new key → swap (zero downtime)
- [x] Backend: teams table, team_memberships table, role-based middleware, encrypted_keys table

### M16: Cost Tracking

**API:** 10 endpoints under `/api/v1/costs` + `/api/v1/budgets` — see `api/routes/costs.py`, `api/services/cost_service.py`.

- [x] Cost event ingestion: capture token counts + dollar costs per LLM call (from traces)
- [x] Cost dashboard page: charts for daily/weekly/monthly spend
- [x] Cost breakdown: by agent, by model, by team
- [x] Budget management: set budget per team, alert at 80%/100%
- [x] Cost comparison: side-by-side model cost analysis
- [x] Cost per request: show cost inline on each trace
- [x] Backend: cost_events table, budget table, aggregation queries
- [x] LiteLLM connector enhanced: ingest cost data from LiteLLM proxy logs into cost_events

### M17: Audit & Lineage

**API:** 7 endpoints under `/api/v1/audit` + `/api/v1/lineage` — see `api/routes/audit.py`, `api/services/audit_service.py`.

- [x] Audit log: immutable record of all actions (deploy, config change, delete, access change)
- [x] Audit log UI: filterable table with actor, action, resource, timestamp
- [x] Lineage graph: visual dependency graph (agent → tools, MCP servers, models, prompts)
- [x] Lineage UI: ReactFlow graph showing relationships between resources
- [x] Impact analysis: "if I change this prompt, which agents are affected?"
- [x] Change notifications: subscribe to changes on resources you depend on

### M28: Full Code Python SDK (Agent Development)

> The Full Code tier for agent development. Users who outgrow YAML get a Python SDK with full programmatic control. The SDK generates valid `agent.yaml` + bundles custom code — it does NOT bypass the deploy pipeline.

**SDK:** `sdk/python/agenthub/` — Agent, Tool, Model, Memory, DeployConfig classes with builder pattern + YAML round-trip.

**CLI:** `agentbreeder eject` command — see `cli/commands/eject.py`.

#### 28.1 — Core SDK
- [x] `agentbreeder` Python package: `pip install agentbreeder` includes the SDK
- [x] `Agent` class: define agents programmatically with model, tools, prompt, memory, guardrails
- [x] `Tool` class: define tools as Python functions with automatic schema generation (from type hints)
- [x] `Memory` class: configure memory backends programmatically
- [x] `AgentConfig.to_yaml()`: serialize any SDK-defined agent to valid `agent.yaml`
- [x] `AgentConfig.from_yaml()`: load an existing `agent.yaml` into SDK objects (round-trip fidelity)
- [x] `agent.deploy(target="local")`: deploy directly from Python (wraps `agentbreeder deploy`)

#### 28.2 — Advanced Agent Features (Code-Only)
- [x] Dynamic tool selection: `agent.select_tools(message)` — choose tools at runtime based on input
- [x] Custom routing: `agent.route(message, context)` — user-defined Python function for routing logic
- [x] State management: `agent.state` — typed state object persisted across turns
- [x] Middleware: `agent.use(middleware_fn)` — inject pre/post processing on every turn (logging, validation, transforms)
- [x] Hooks: `agent.on("tool_call", handler)` — event-driven hooks for lifecycle events

#### 28.3 — SDK Developer Experience
- [x] `agentbreeder eject my-agent --sdk python`: generate Python SDK scaffold from existing `agent.yaml`
- [x] Auto-complete: SDK objects export type stubs for IDE autocomplete
- [x] SDK documentation: hosted docs with examples, API reference, cookbook
- [x] SDK examples: `examples/sdk-basic/`, `examples/sdk-advanced/`, `examples/sdk-custom-routing/`

---

## v1.0 — "General Availability" (Done)

> **Goal:** Teams can measure agent quality systematically — golden test sets, automated scoring, regression detection, and CI/CD gates. Ship agents with confidence. This is GA: you have registry, builders, observability, and now eval — a complete agent platform.

### M18: Evaluation Framework

#### 18.1 — Evaluation Dataset Management
- [x] Evaluation dataset as a registry resource: name, agent, format, version
- [x] Dataset format: JSON Lines with `input`, `expected_output`, `expected_tool_calls`, `tags`, `metadata`
- [x] Dataset editor UI: create/edit/tag/version datasets from the dashboard
- [x] Import datasets: upload JSONL file, paste JSON, or capture from live conversations
- [x] Export datasets: download as JSONL, CSV
- [ ] Dataset splitting: auto-split into train/test/validation sets
- [x] Backend: `eval_datasets` table (name, agent_id, version, row_count, format)
- [x] Backend: `eval_dataset_rows` table (dataset_id, input, expected_output, tags)

#### 18.2 — Evaluation Runner
- [x] CLI: `agentbreeder eval <agent-name> --dataset <dataset>` — run offline evaluation
- [x] API: `POST /api/v1/eval/run` — trigger evaluation run programmatically
- [x] Run against any deployed agent or local agent (Docker)
- [x] Parallel execution: run N test cases concurrently (configurable)
- [ ] Progress tracking: real-time progress bar in UI and CLI
- [x] Backend: `eval_runs` table (id, agent_id, dataset_id, status, started_at, completed_at, summary_json)
- [x] Backend: `eval_results` table (run_id, row_id, actual_output, scores_json, latency_ms, token_count)

#### 18.3 — Scoring & Metrics
- [x] Built-in metrics: correctness, relevance, groundedness, tool accuracy, latency, cost
- [x] Judge model integration: use a configurable LLM (e.g., Claude, GPT-4) to auto-grade responses
- [x] Judge prompt templates: customizable grading rubrics per metric
- [ ] Custom metric plugins: user-defined Python scoring functions
- [x] Aggregate scores: per-run summary (mean, median, P95 per metric)
- [x] Score breakdown: by tag, by test case category
- [ ] Confidence intervals on scores

#### 18.4 — Evaluation Dashboard
- [x] Eval Runs page: list all runs with agent, dataset, date, overall score, status
- [x] Run detail page: per-test-case results table with input, expected, actual, scores
- [x] Score trends chart: plot scores over time per agent (detect regressions)
- [x] Regression detection: compare current run against baseline, alert on score drop > threshold
- [x] Run comparison: select two runs, see side-by-side score diffs
- [ ] Link from agent detail → "Evaluations" tab (pre-filtered)

#### 18.5 — CI/CD Integration
- [x] GitHub Action: `agentbreeder/eval-action` — run evals on PR, post results as PR comment
- [x] Quality gate: block merge if scores below configurable threshold
- [x] Eval badge: embed score badge in README (like coverage badges)
- [x] Scheduled evals: cron-based regression runs (daily/weekly)
- [x] Promotion gate: require passing eval before registry promotion (ties into approval workflow)

### M29: Orchestration — Low Code (YAML)

> Multi-agent orchestration defined in YAML. The `orchestration.yaml` format defines agent graphs, routing strategies, shared state, and deployment targets. This is the Low Code tier for orchestration.

#### 29.1 — Orchestration YAML Specification
- [x] `orchestration.yaml` schema: name, version, strategy, agents, shared_state, deploy
- [x] JSON Schema for `orchestration.yaml` with IDE autocomplete support
- [x] Orchestration strategies: `router` (intent-based routing), `sequential` (pipeline), `parallel` (fan-out/fan-in), `hierarchical` (supervisor/worker)
- [x] Agent references: local (`./agents/billing`), registry (`registry://agents/billing@v1`), URL (already deployed)
- [x] Routing rules: field-based routing (intent → agent), condition-based routing (if/else), fallback agents
- [x] Shared state: configurable state backend (in-memory, Redis, PostgreSQL) shared across agents in the orchestration
- [x] `agentbreeder validate orchestration.yaml` — validate orchestration config

#### 29.2 — Orchestration Engine
- [x] `engine/orchestrator.py`: orchestration runtime that executes the agent graph
- [x] Router strategy: classify input → route to appropriate agent
- [x] Sequential strategy: pass output of agent N as input to agent N+1
- [x] Parallel strategy: fan-out to multiple agents, merge results
- [x] Hierarchical strategy: supervisor agent delegates to workers, aggregates results
- [x] Shared state propagation: pass context between agents in the graph
- [ ] Error handling: per-agent fallback, circuit breaker, retry with backoff
- [x] Orchestration as a deployable unit: `agentbreeder deploy orchestration.yaml` deploys the entire graph

#### 29.3 — Orchestration CLI
- [ ] `agentbreeder init my-pipeline --orchestration` — scaffold orchestration project
- [x] `agentbreeder deploy orchestration.yaml` — deploy multi-agent pipeline
- [x] `agentbreeder chat orchestration.yaml` — test orchestration interactively
- [x] `agentbreeder status my-pipeline` — show status of all agents in the orchestration
- [ ] `agentbreeder logs my-pipeline` — aggregate logs across all agents

---

## v1.1 — "Connectivity" (Done)

> **Goal:** Agents can discover and call each other via A2A protocol. MCP servers become a managed hub. The platform enables multi-agent workflows.

### M19: Agent-to-Agent (A2A) Protocol

Implement the Google A2A specification for agent interoperability. Any deployed agent can advertise its capabilities and be invoked by other agents.

#### 19.1 — A2A Server
- [x] A2A JSON-RPC 2.0 server endpoint on every deployed agent (`/.well-known/agent.json`)
- [x] Agent Card generation: auto-generate capability advertisement from `agent.yaml` (name, description, skills, input/output schemas)
- [x] A2A discovery endpoint: `GET /api/v1/a2a/agents` — list all A2A-capable agents
- [x] A2A invoke: `POST /api/v1/a2a/invoke` — call an agent by name, get structured response
- [x] A2A streaming: SSE support for long-running agent responses
- [x] A2A authentication: JWT-based inter-agent auth (agents get service tokens)
- [x] Backend: `a2a_agents` table (agent_id, agent_card_json, endpoint, status)

#### 19.2 — A2A in Agent Config
- [x] `agent.yaml` `subagents:` field — declare agents this agent can call
- [x] Auto-generated `call_{agent_name}` tools from subagent declarations
- [x] Subagent resolution: validate that referenced agents exist in registry and are deployed
- [x] Subagent call tracing: A2A calls appear as spans in the trace waterfall
- [x] Example: `examples/a2a-orchestrator/` — supervisor agent that delegates to two sub-agents

#### 19.3 — A2A Dashboard
- [x] A2A Agents page: list all agents with A2A enabled, show Agent Cards
- [x] A2A topology graph: ReactFlow visualization of which agents call which
- [x] A2A call log: recent inter-agent calls with latency, status, input/output preview
- [x] Agent Card editor: customize the auto-generated Agent Card from dashboard
- [x] A2A test panel: invoke any A2A agent from the dashboard, see response inline

#### 19.4 — Multi-Agent Orchestration Patterns
- [x] Supervisor pattern: one agent routes requests to specialized sub-agents
- [x] Fan-out/fan-in: send request to N agents in parallel, aggregate responses
- [x] Chain pattern: sequential pipeline of agents (output of one → input of next)
- [x] Orchestration YAML schema: define multi-agent workflows in `agent.yaml`
- [x] Example: `examples/a2a-supervisor/` — supervisor + 2 workers (research + summarize)

### M20: MCP Server Hub

Elevate MCP from "tool connector" to a managed server hub with lifecycle management.

- [x] MCP server packaging: define MCP servers as registry resources (name, version, transport, tools)
- [x] MCP server deploy: deploy MCP servers alongside agents (Docker sidecar or standalone)
- [x] MCP server versioning: track versions, support rollback
- [x] MCP server sharing: teams can publish MCP servers for other teams to use
- [x] MCP compose: `agent.yaml` `mcp_servers:` field auto-starts required MCP servers at deploy time
- [x] MCP server metrics: request count, latency, error rate per server per tool
- [x] MCP server UI enhancements: "Try Tool" panel (invoke a tool with sample input, see output)

### M30: Visual Orchestration Canvas + TypeScript SDK

> The No Code tier for orchestration (visual canvas) and the Full Code tier for agent development in TypeScript.

#### 30.1 — Visual Orchestration Canvas (No Code)
- [x] Orchestration canvas page: ReactFlow-based editor for multi-agent workflows
- [x] Agent nodes: drag agents from registry onto canvas, configure per-agent settings
- [x] Edge types: routing edges (intent-based), sequential edges (pipeline), parallel edges (fan-out)
- [x] Routing rule editor: click an edge → define routing conditions (intent match, field match, custom expression)
- [x] Shared state configuration: click canvas background → configure shared state backend
- [x] Strategy selector: dropdown to set overall strategy (router, sequential, parallel, hierarchical)
- [x] Canvas → generates valid `orchestration.yaml` (No Code → Low Code ejection)
- [x] "View YAML" tab: always shows the generated YAML alongside the canvas
- [x] Live preview: test the orchestration from the canvas (sends a message, shows agent routing in real-time)
- [x] Visual debugging: highlight active agent during execution, show message flow on edges

#### 30.2 — TypeScript SDK (Agent Development)
- [x] `@agentbreeder/sdk` npm package: TypeScript SDK for agent development
- [x] `Agent` class: define agents with model, tools, prompt, memory, guardrails (TypeScript types)
- [x] `Tool` class: define tools as TypeScript functions with Zod schema generation
- [x] `agent.toYaml()`: serialize to valid `agent.yaml`
- [x] `Agent.fromYaml()`: load existing YAML into SDK objects
- [x] `agent.deploy()`: deploy from TypeScript (wraps `agentbreeder deploy`)
- [x] `agentbreeder eject my-agent --sdk typescript`: generate TS SDK scaffold from YAML
- [x] Deno + Node.js runtime support

---

## v1.2 — "Marketplace" (Done)

> **Goal:** Users can discover, share, and one-click deploy agent templates from a community marketplace.

### M21: Template System

- [x] Template schema: parameterized agent configs with user-fillable variables
- [x] Template creation UI: convert any agent config into a template
- [x] Template gallery page: grid of cards with preview, description, framework badge
- [x] "Use Template" flow: fill in parameters → generates agent.yaml → deploy
- [x] Built-in templates: Customer Support Bot, Data Analyzer, Code Reviewer, Research Assistant
- [x] Template versioning and deprecation

### M22: Marketplace

- [x] Marketplace page: browse templates by category, framework, rating
- [x] Search + filters: keyword search, framework filter, tag filter
- [x] Template detail page: README preview, config preview, usage stats
- [x] Ratings & reviews: star rating + text review per template
- [x] "One-click deploy" from marketplace listing
- [x] Publish flow: submit template for listing (admin approval)

---

## v1.3 — "Enterprise" (Planned)

> **Goal:** Enterprise-grade operations: full model catalog, SSO, secrets management, unified AgentOps dashboard. Production hardening.

### M23: Deployment Targets

#### v0.3 Deployers (ship with Builders)
- [x] **Local Docker** (full stack) — `docker compose up` runs everything: API, dashboard, PostgreSQL, Redis, agents, MCP servers (already exists)
- [x] **Google Cloud Run** — primary cloud target: auto-scaling, scale-to-zero, Artifact Registry, Cloud Load Balancing, Workload Identity
- [x] `engine/deployers/gcp_cloudrun.py` — Cloud Run deployer implementation
- [x] `agentbreeder deploy --target cloud-run` CLI command
- [x] Deploy target selector in dashboard UI (Local Docker, Google Cloud Run)
- [ ] Cloud console deep links on deploy status page
- [ ] Cloud Run deployment docs + quickstart guide

#### Deployer Priority (post v1.3, in order)

| Priority | Target | Milestone | Notes |
|----------|--------|-----------|-------|
| **P1** | Databricks Apps / Lakebase | Post v1.5 | Deploy agents as Databricks Apps; Lakebase for state/vector storage |
| **P1** | AI Gateway (LiteLLM/Portkey) | Post v1.5 | Centralized model routing, cost tracking, fallback chains |
| **P1** | AWS ECS Fargate | **M33** | Task def, ECS service, ALB, IAM, auto-scaling — tracked in #34 |
| **P1** | Azure Container Apps | **M33** | Azure-native serverless containers, ACR, Managed Identity — tracked in #34 |
| **P1** | Kubernetes (general) | **M33** | Works across EKS, GKE, AKS, k3s; `Deployment` + `Service` + `HPA` — tracked in #34 |
| **P2** | AWS Lambda | Post v1.5 | Lightweight/event-driven agents, API Gateway |
| **P3** | AWS EKS | M33 (via K8s deployer) | Routed via `--target eks` to the Kubernetes deployer |
| **P3** | Google GKE | M33 (via K8s deployer) | Routed via `--target gke` to the Kubernetes deployer |
| **P3** | Azure AKS | M33 (via K8s deployer) | Routed via `--target aks` to the Kubernetes deployer |
| **P4** | Oracle OKE | Post v1.5 | Oracle Kubernetes Engine |

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
| **2** | OpenRouter | SaaS | Teams that don't want to manage infra, quick multi-provider access | v1.3 |
| **3** | Portkey | OSS + SaaS | Advanced — semantic caching, virtual keys, load balancing, guardrails | Post v1.3 |

**Tier 0: Direct Provider SDKs + Ollama (v0.3 — ships with builders)**
- [ ] Direct calls to OpenAI SDK — no proxy, no extra service (Anthropic in v0.4, Google in v1.1)
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
3. agentbreeder dev agent my-agent                # start dev sandbox
   → Agent runs locally against Ollama
   → No API keys, no cost, no internet needed
   → Iterate on prompts + tools with instant feedback

4. When ready for production:
   → Change model.provider to anthropic/openai/google
   → agentbreeder submit agent my-agent           # submit for review
```

- [ ] `agentbreeder dev` auto-detects Ollama and uses it if no cloud API keys are configured
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

**Tier 2: OpenRouter (v1.3)**
- [ ] OpenRouter integration: route through OpenRouter API with single API key
- [ ] Model mapping: map AgentBreeder model names to OpenRouter model IDs
- [ ] Cost pass-through: ingest OpenRouter cost data into cost dashboard
- [ ] Use case: teams that want multi-provider access without running LiteLLM

**Tier 3: Portkey (post v1.3)**
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

#### Supported Models — Phased Rollout

**v0.3 — Ship with Builders (OpenAI + Ollama)**

| Provider | Models | Category |
|----------|--------|----------|
| **OpenAI** | GPT-4.1, GPT-4o, o3-mini, o4-mini | Text generation |
| **OpenAI** | text-embedding-3-small, text-embedding-3-large | Embeddings |
| **Ollama (local)** | Llama 3.3, Qwen 3, Mistral, Phi-3, Gemma 3, CodeLlama | Text generation (local) |
| **Ollama (local)** | nomic-embed-text, mxbai-embed-large | Embeddings (local) |

- [ ] Provider abstraction layer: `engine/providers/` with unified `generate()` interface
- [ ] OpenAI SDK integration: direct API calls, streaming, function calling
- [ ] Ollama integration: OpenAI-compatible API, auto-detection, model pull from UI
- [ ] Ollama models tagged as "Local / Free" in model picker — appear first during dev
- [ ] Model selection in Agent Builder: filter by provider, capability, price, local/cloud
- [ ] Embedding model selection in RAG Builder: pick embedding model for vector indexing

**v0.4 — Add Anthropic**

| Provider | Models | Category |
|----------|--------|----------|
| **Anthropic** | Claude Opus 4.6, Claude Sonnet 4.6, Claude Haiku 4.5 | Text generation |

- [ ] Anthropic SDK integration: messages API, streaming, tool use
- [ ] Model catalog entry: pricing, context window (200K), capabilities

**v1.1 — Add Google**

| Provider | Models | Category |
|----------|--------|----------|
| **Google** | Gemini 2.5 Pro, Gemini 2.5 Flash | Text generation |
| **Google** | Gemini Embedding | Embeddings |

- [ ] Google AI SDK integration: generate_content, streaming, function calling
- [ ] Model catalog entry: pricing, context window (1M+), capabilities

**v1.3 — Full Model Catalog**

| Provider | Models | Category |
|----------|--------|----------|
| All above providers fully supported | All models with pricing, context windows, capabilities | Complete catalog |
| **OpenAI** | Sora | Video generation |
| **Google** | Veo | Video generation |

- [ ] Model catalog: all models registered with pricing, context window, capabilities
- [ ] Model provider abstraction: unified interface across OpenAI, Anthropic, Google, Ollama
- [ ] Video model support: input/output handling for Sora and Veo in agent tools

#### Later Models (post v1.3)

| Provider | Models | Priority |
|----------|--------|----------|
| Mistral | Mistral Large, Codestral | P1 |
| Perplexity | Sonar Pro, Sonar | P1 |
| xAI | Grok 3, Grok 3 mini | P2 |
| Cohere | Command R+, Embed v4 | P2 |
| AWS Bedrock | Claude, Llama, etc. (via AWS) | P2 |
| Azure OpenAI | GPT-4.1, GPT-4o (via Azure) | P2 |
| Groq / Cerebras | Speed-optimized inference | P3 |

### M25: Enterprise Auth & Secrets Management

#### Enterprise Auth
- [ ] SSO/SAML integration (Okta, Auth0, Azure AD)
- [ ] OIDC provider support
- [ ] SCIM user provisioning
- [ ] Directory sync: auto-provision teams from identity provider

#### Secrets Management (replaces .env for production)
- [x] External secrets manager integration: AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault (`engine/secrets/` — pluggable `SecretsBackend` abstraction)
- [x] Secret references in `agent.yaml`: `api_key: secret://openai-key` — resolved at deploy time via `resolve_secret_refs()` and `find_secret_refs()` in `engine/secrets/factory.py`
- [x] Secret rotation: `agentbreeder secret rotate KEY` — agents pick up new value without redeploy
- [x] CLI: `agentbreeder secret list/set/get/delete/rotate` — all backends, masked display, `--json` for CI (`cli/commands/secret.py`)
- [x] Migration path: `agentbreeder secret migrate --from env --to aws|gcp|vault [--dry-run]` — bulk migrate `.env` to cloud secrets manager
- [ ] Secrets UI: Settings → Secrets page — list secrets (name + masked value), create/rotate/delete
- [ ] Team-scoped secrets: each team manages their own API keys and secrets
- [ ] Audit log: track who accessed/changed which secrets and when

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
- [x] Zero known critical security issues — ruff + mypy clean on all new modules (M31 SDK)
- [x] 85%+ test coverage across all modules — 87% overall, 93%+ on new orchestration SDK
- [x] Load testing: k6 scripts for all critical paths (`tests/load/agents_api.js`, `deploy_pipeline.js`, `orchestration_execute.js`)
- [x] Performance benchmarks tracked per release (`benchmarks/benchmark_core.py` — pytest-benchmark)
- [x] Docs site (MkDocs Material → GitHub Pages) — `mkdocs.yml`, `docs/index.md`, `.github/workflows/docs.yml`; install: `pip install agentbreeder[docs]`
- [x] API stability: versioned API with deprecation policy — `api/versioning.py` (`APIVersionMiddleware`, `deprecate_path()`), `/api/v2/` preview routes (`api/routes/v2/agents.py`), `docs/api-stability.md`

### M31: Full Code Orchestration SDK (Python + TypeScript)

> The Full Code tier for orchestration. For complex multi-agent workflows that YAML and visual canvas can't express — dynamic agent spawning, stateful workflows, human-in-the-loop, conditional branching based on runtime data.

#### 31.1 — Python Orchestration SDK
- [x] `Orchestration` class: define multi-agent workflows programmatically (`sdk/python/agenthub/orchestration.py`)
- [x] `Router` base class: user-defined routing logic (classifier-based, rule-based, ML-based)
- [x] Built-in routers: `IntentRouter`, `ClassifierRouter`, `KeywordRouter`, `RoundRobinRouter`
- [x] `Pipeline` class: sequential agent chains with fallback support
- [x] `FanOut` class: parallel execution with configurable merge strategies (first-wins, majority-vote, aggregate)
- [x] `Supervisor` class: hierarchical orchestration with a supervisor agent delegating to workers
- [x] Shared state config: `with_shared_state(type, backend)` — typed config serialized to YAML
- [x] `orchestration.deploy()`: deploy the entire graph as a single unit
- [x] YAML serialization: `to_yaml()`, `from_yaml()`, `from_file()`, `save()` — full round-trip fidelity
- [x] Validation: `validate()` — checks name, version, strategy, route targets, fallbacks
- [x] `agenthub.__init__` updated — all classes exported from `from agenthub import ...`
- [x] 66 unit tests in `tests/unit/test_sdk_orchestration.py` — 100% coverage on new module
- [ ] Human-in-the-loop: `await orchestration.pause(reason="needs approval")` — deferred post-v1.3
- [ ] Dynamic agent spawning: `orchestration.spawn(agent_config)` — deferred post-v1.3

#### 31.2 — TypeScript Orchestration SDK
- [x] Same API surface as Python SDK, TypeScript-native types (`sdk/typescript/src/orchestration.ts`)
- [x] `Orchestration`, `Router`, `Pipeline`, `FanOut`, `Supervisor` classes
- [x] `KeywordRouter`, `IntentRouter`, `RoundRobinRouter`, `ClassifierRouter`
- [x] `orchestrationToYaml()`: YAML serialization without external dependencies
- [x] Validation: `validate()` — same rules as Python SDK
- [x] `sdk/typescript/src/index.ts` updated — all classes exported
- [x] 53 Vitest tests in `sdk/typescript/tests/orchestration.test.ts` — all passing
- [ ] Zod schemas for runtime config validation — deferred post-v1.3

#### 31.3 — Advanced Orchestration Patterns (deferred post-v1.3)
- [ ] Conversation handoff: transfer conversation state + history from one agent to another
- [ ] Agent marketplace integration: dynamically discover and wire in agents from the marketplace
- [ ] Orchestration versioning: version the entire multi-agent graph as a unit
- [ ] Orchestration replay: replay a past orchestration execution for debugging (from traces)
- [ ] Orchestration templates: pre-built patterns (customer support routing, research pipeline, review chain)
- [ ] Cost budgets: set per-orchestration cost limits, auto-terminate if exceeded

---

## Out of Scope

These are intentionally deferred indefinitely:

- Custom model hosting / serving (use vLLM, TrueFoundry, etc.)
- Full RLHF/PPO training pipelines (post v1.3 — see Tier 4 in Builder spec)
- Mobile app
- ~~Azure deployer (community contribution welcome post-v1.3)~~ → **now planned in M33 (v1.5)**
- Chat interface for end-users (we build the platform, not the consumer UI)

---

## Architecture Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Backend language | Python (FastAPI) | AI ecosystem is Python-first; native integration with agent frameworks |
| CLI | Python (Typer + Rich) | Same language as backend; single install |
| Dashboard | React 19 + TypeScript + Tailwind v4 | Industry standard; shadcn/ui for Vercel-quality aesthetic |
| Database | PostgreSQL + SQLAlchemy + Alembic + pgvector | Mature, reliable; pgvector for RAG + semantic memory — zero new services |
| ORM | SQLAlchemy (async) | Most capable Python ORM; async support |
| Cache | Redis | Rate limiting, task queue, session cache |
| Container build | Docker | Universal; BuildKit for multi-platform |
| Auth | JWT + bcrypt | Simple, stateless; OAuth2 providers added in v1.3 |
| Distribution | `pip install agentbreeder` (CLI+SDK), Docker image (platform), Helm chart (K8s) | pip for devs, Docker for teams, Helm for production |
| Project structure | Single-agent (`agent.yaml` at root) or workspace (`agentbreeder.yaml` + `agents/`) | Scales from solo developer to multi-team |
| Config format | YAML as source of truth (all resource types) | Human-readable; JSON Schema for validation; round-trip safe; IDE and UI interoperable |
| Builder model | Three tiers: No Code (UI) → Low Code (YAML) → Full Code (SDK) | Matches how real teams work — PMs prototype in UI, engineers refine in YAML/code. Tier mobility prevents lock-in. All tiers compile to same internal format. |
| Orchestration model | Same three tiers for multi-agent orchestration | Visual canvas, `orchestration.yaml`, and Python/TS SDK. Same compilation model — orchestration config + optional code → deploy pipeline. |
| YAML parser | ruamel.yaml (round-trip mode) | Preserves comments and key order — critical for CLI/UI interop |
| Version control | Git (local repo, bridgeable to GitHub/GitLab) | Branch-per-draft, PR-based review, semver tagging on merge |
| Git library | gitpython (Python) | Programmatic Git ops without shelling out; supports bare repos |
| Local LLM | Ollama | Zero-cost local dev/test; auto-detected; OpenAI-compatible API |
| Model gateway | Layered: OpenAI + Ollama (v0.3) → +Anthropic (v0.4) → +Google (v1.1) → LiteLLM → OpenRouter | Phased provider rollout; LiteLLM for production (OSS, self-hosted, 100+ providers) |
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
| `area:packaging` | Package distribution, CI/CD, release automation |

---

## Milestone M32 — Package Distribution & Release Automation

**Release:** v1.4 (Distribution)
**Theme:** Make AgentBreeder installable from PyPI, Docker Hub, and Homebrew with automated release pipelines.
**Status:** In Progress

### M32.1 — Package Split (SDK + CLI)

Split monolith into two PyPI packages:
- `agentbreeder-sdk` — lightweight SDK with minimal deps (PyYAML only)
- `agentbreeder` — CLI + API server + engine (depends on SDK)

Tasks:
- [x] Create `sdk/python/pyproject.toml` for `agentbreeder-sdk`
- [x] Remove `sdk` from root `hatch.build.targets.wheel.packages`
- [x] Add `agentbreeder-sdk>=0.1.0` to root package dependencies
- [x] Fix stale URLs in root `pyproject.toml` (repo, docs)
- [ ] Implement version derivation from git tags (`hatch-vcs` or `setuptools-scm`)
- [x] Verify both packages build independently: `python -m build`
- [ ] Verify `pip install agentbreeder` pulls SDK as dependency (requires PyPI registration)

### M32.2 — CI Pipeline (GitHub Actions)

Add PR checks workflow:
- [x] Create `.github/workflows/ci.yml`
- [ ] Python 3.11 + 3.12 matrix (currently 3.12 only)
- [x] Lint (ruff), type check (mypy), test (pytest + coverage)
- [x] Build both packages
- [x] Build all Docker images (no push)

### M32.3 — Release Pipeline (GitHub Actions)

Automated publishing on GitHub Release:
- [x] Create `.github/workflows/release.yml`
- [ ] Configure PyPI trusted publishers (OIDC) for both packages (manual step on pypi.org)
- [x] Publish `agentbreeder-sdk` then `agentbreeder` to PyPI (workflow ready)
- [x] Build + push Docker images to Docker Hub (3 images, multi-platform) (workflow ready)
- [x] Trigger Homebrew tap update (workflow ready)

### M32.4 — Docker Hub Images

Three production Docker images:
- [x] Verify existing `Dockerfile` (api) builds correctly
- [x] Create `dashboard/Dockerfile` (React + nginx) (existed, added OCI labels)
- [x] Create `dashboard/nginx.conf` (already existed)
- [x] Create `Dockerfile.cli` (lightweight, installs from PyPI)
- [x] Add multi-platform build (linux/amd64 + linux/arm64) via `docker buildx` (in release.yml)
- [ ] Set up Docker Hub organization `agentbreeder` (manual step)
- [ ] Configure Docker Hub credentials in GitHub Secrets (manual step)

### M32.5 — Homebrew Tap

Homebrew distribution for macOS/Linux:
- [ ] Create repo `rajitsaha/homebrew-agentbreeder` (manual step)
- [x] Write `Formula/agentbreeder.rb` (Python virtualenv formula)
- [x] Create Homebrew update workflow in `release.yml`
- [ ] Test `brew tap rajitsaha/agentbreeder && brew install agentbreeder`
- [ ] Document migration path to Homebrew core
- [ ] Add `HOMEBREW_TAP_TOKEN` to GitHub Secrets (manual step)

### M32.6 — Namespace & URL Cleanup

Align all public-facing names:
- [x] Fix `pyproject.toml` Repository URL → `https://github.com/rajitsaha/agentbreeder`
- [x] Fix `pyproject.toml` Documentation URL → `https://agent-breeder.com`
- [ ] Register `agentbreeder` and `agentbreeder-sdk` names on PyPI (manual step)
- [ ] Register `agentbreeder` org on Docker Hub (manual step)
- [x] Update README install instructions

---

## Milestone M33 — Multi-Cloud Deployers

**Release:** v1.5 (Multi-Cloud)
**Theme:** Make AgentBreeder genuinely multi-cloud by implementing AWS ECS Fargate, Azure Container Apps, and general Kubernetes deployers.
**Status:** Planned
**GitHub Issue:** [#34](https://github.com/rajitsaha/agentbreeder/issues/34)

### Background

AgentBreeder's core promise is "Multi-cloud first — AWS and GCP as equal first-class targets." Currently only GCP Cloud Run is implemented. `cloud: aws` crashes at runtime with a `KeyError`. `cloud: azure` fails YAML validation. `cloud: kubernetes` silently deploys to local Docker instead of a real cluster.

### M33.1 — AWS ECS Fargate Deployer

- [ ] `engine/deployers/aws_ecs.py` — `AWSECSDeployer(BaseDeployer)`
- [ ] `provision()`: create ECR repo if absent; resolve ECS cluster; return expected ALB URL
- [ ] `deploy()`: build + push image to ECR; register ECS task definition; create/update ECS service; wait for service stability
- [ ] `health_check()`: poll `/health` on ALB endpoint with readiness backoff
- [ ] `teardown()`: delete ECS service + deregister task definitions
- [ ] `get_logs()`: CloudWatch Logs via `boto3.client("logs").filter_log_events()`
- [ ] Config from `deploy.env_vars`: `AWS_ACCOUNT_ID`, `AWS_REGION`, `AWS_ECS_CLUSTER`, `AWS_EXECUTION_ROLE_ARN`, `AWS_VPC_SUBNETS`, `AWS_SECURITY_GROUPS`
- [ ] Lazy import: `ImportError` with `pip install agentbreeder[aws]` hint if `boto3` missing
- [ ] `--target ecs-fargate` and `--target aws` both route to this deployer

### M33.2 — Azure Container Apps Deployer

- [ ] Add `azure = "azure"` to `CloudType` enum in `engine/config_parser.py`
- [ ] `engine/deployers/azure_container_apps.py` — `AzureContainerAppsDeployer(BaseDeployer)`
- [ ] `provision()`: create ACR if absent; create Container Apps environment if absent
- [ ] `deploy()`: push image to ACR; create/update Container App revision
- [ ] `health_check()`: poll `/health` on Container App FQDN
- [ ] `teardown()`: delete Container App; optionally purge ACR image
- [ ] `get_logs()`: Log Analytics workspace query via `azure-monitor-query`
- [ ] Config: `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`, `AZURE_CONTAINER_APPS_ENV`, `AZURE_REGISTRY_SERVER`
- [ ] Lazy import: `ImportError` with `pip install agentbreeder[azure]` hint
- [ ] `--target azure` and `--target container-apps` both route to this deployer

### M33.3 — General Kubernetes Deployer

- [ ] `engine/deployers/kubernetes.py` — `KubernetesDeployer(BaseDeployer)`
- [ ] `provision()`: resolve kubeconfig; create `agentbreeder` namespace if absent
- [ ] `deploy()`: apply `Deployment` manifest; apply `Service`; apply `HPA` if `deploy.scaling` is set; wait for rollout
- [ ] `health_check()`: port-forward to `/health` or check via Service ClusterIP
- [ ] `teardown()`: delete `Deployment`, `Service`, `HPA`, `ConfigMap`
- [ ] `get_logs()`: `CoreV1Api.read_namespaced_pod_log()`
- [ ] Config: `K8S_NAMESPACE` (default: `agentbreeder`), `K8S_CONTEXT`, `K8S_IMAGE_PULL_SECRET`
- [ ] Lazy import: `ImportError` with `pip install agentbreeder[kubernetes]` hint
- [ ] `--target kubernetes`, `--target eks`, `--target gke`, `--target aks` all route to this deployer
- [ ] Works across EKS, GKE, AKS, k3s, kind, and self-managed clusters

### M33.4 — Wiring + Optional Deps

- [ ] Register new deployers in `engine/deployers/__init__.py` DEPLOYERS + RUNTIME_DEPLOYERS dicts
- [ ] Add `[aws]`, `[azure]`, `[kubernetes]`, `[all-clouds]` optional dep groups to `pyproject.toml`
- [ ] Update `engine/schema/agent.schema.json` cloud enum to include `"azure"`
- [ ] Update `--target` help text in `cli/commands/deploy.py` to accurately list all supported values

### M33.5 — Tests

- [ ] `tests/unit/test_deployers_aws.py` — mock boto3; cover provision, deploy, health_check, teardown, get_logs, ImportError, missing config validation
- [ ] `tests/unit/test_deployers_azure.py` — mock azure-sdk; same coverage pattern
- [ ] `tests/unit/test_deployers_kubernetes.py` — mock kubernetes client; namespace creation, deployment apply, rollout wait, teardown
- [ ] `tests/unit/test_config_parser.py` — add `azure` to valid CloudType values test
- [ ] `tests/unit/test_deploy_engine.py` — add routing tests: aws → AWSECSDeployer, azure → AzureContainerAppsDeployer, kubernetes → KubernetesDeployer
- [ ] `tests/integration/test_deploy_pipeline.py` — pipeline integration stubs with mocked cloud SDKs for all three new targets

### M33 Acceptance Criteria

- [ ] `agentbreeder deploy agent.yaml --target aws` completes end-to-end against a real AWS account
- [ ] `agentbreeder deploy agent.yaml --target azure` completes end-to-end against a real Azure subscription
- [ ] `agentbreeder deploy agent.yaml --target kubernetes` deploys to the active kubeconfig context
- [ ] `agentbreeder validate agent.yaml` accepts `cloud: azure` without errors
- [ ] Missing cloud SDK raises a clear `ImportError` with the correct `pip install` hint
- [ ] All new deployers have unit test coverage ≥95% on changed files
- [ ] Gate workflow passes: lint, typecheck, tests, security scan all clean

---

## Milestone M34 — Framework Depth & Runtime Tracing

**Release:** v1.6 (Framework Depth)
**Theme:** Close all feature gaps in existing runtimes and wire up per-agent observability so the AgentOps dashboard and cost tracking work end-to-end for deployed agents.
**Status:** Planned
**GitHub Issues:** [#39](https://github.com/rajitsaha/agentbreeder/issues/39) [#40](https://github.com/rajitsaha/agentbreeder/issues/40) [#41](https://github.com/rajitsaha/agentbreeder/issues/41)

### Background

LangGraph and OpenAI Agents SDK are the two fully implemented runtimes. Both deploy and run agents, but both are missing the features that make them production-grade: LangGraph's HITL and persistence, OpenAI Agents SDK's handoffs, and runtime-side OTel tracing for all frameworks. Without the tracing work specifically, the AgentOps dashboard and cost tracking produce no data for running agents.

### M34.1 — LangGraph: Subgraphs, HITL, and Persistence

**Issue:** [#39](https://github.com/rajitsaha/agentbreeder/issues/39) | **Priority: P1**

- [ ] **Checkpointer auto-selection** in `langgraph_server.py`:
  - `MemorySaver` when `DATABASE_URL` is not set (dev/local)
  - `AsyncPostgresSaver` when `DATABASE_URL` is set (production)
  - `checkpointer.setup()` called on startup to create LangGraph's checkpoint tables
- [ ] **Thread ID support**: accept `config.thread_id` in `/invoke` request; generate and return one if not provided
- [ ] **HITL interrupt detection**: return `{"status": "interrupted", "thread_id": "...", "awaiting": "..."}` instead of hanging when graph hits `interrupt_before`
- [ ] **`/resume` endpoint**: accept `thread_id` + `human_input`; resume graph via `Command(resume=...)`
- [ ] **Subgraph support**: validated natively by LangGraph compiled graphs; add validation warning if user's `agent.py` returns an uncompiled `StateGraph`
- [ ] Add `langgraph-checkpoint-postgres` to requirements when `DATABASE_URL` is set
- [ ] Update `agent.yaml` spec docs: `DATABASE_URL` in `deploy.env_vars` enables stateful agents
- [ ] Unit tests: checkpointer selection, `/resume` endpoint, thread_id in response, HITL interrupt status
- [ ] Integration test: stateful LangGraph agent with `MemorySaver`

### M34.2 — OpenAI Agents SDK: Handoffs and Nested Agents

**Issue:** [#40](https://github.com/rajitsaha/agentbreeder/issues/40) | **Priority: P1**

- [ ] **`HandoffOutputItem` extraction** in `openai_agents_server.py`: unwrap `RunResult.new_items` to find the final agent's text output
- [ ] **Response schema update**: `/invoke` returns `{"output": "...", "agent": "<name>", "handoffs": [...]}`  — additive, backwards compatible
- [ ] **Explicit API key init**: call `set_default_openai_key(os.environ["OPENAI_API_KEY"])` on startup to prevent nested runner context loss
- [ ] **`/stream` SSE endpoint**: `Runner.run_streamed()` emitting `AgentUpdatedStreamEvent` on handoffs
- [ ] **Nested agent support**: document `.as_tool()` pattern works after explicit key init; validation warning for ambiguous entry points
- [ ] Unit tests: HandoffOutputItem extraction, last_agent in response, handoffs list, set_default_openai_key on startup
- [ ] Integration test: handoff from triage agent to specialist agent

### M34.3 — Per-Framework Runtime Tracing

**Issue:** [#41](https://github.com/rajitsaha/agentbreeder/issues/41) | **Priority: P1**

- [ ] `engine/runtimes/templates/_tracing.py` — shared OTel init utility:
  - `MemorySaver` / no-op tracer when `OPENTELEMETRY_ENDPOINT` not set (zero breakage for existing deploys)
  - `BatchSpanProcessor` + `OTLPSpanExporter` when endpoint is set
- [ ] **LangGraph server**: wrap `/invoke` in root span; extract token counts from LangChain callback; emit `agent.name`, `agent.version`, `llm.model`, `llm.token_count.input`, `llm.token_count.output` span attributes
- [ ] **OpenAI Agents server**: wire `agents.tracing` to OTel exporter; emit same standard span attributes
- [ ] **Deployers**: inject `OPENTELEMETRY_ENDPOINT` into every agent container as a platform default (not requiring user config in `agent.yaml`)
  - `engine/deployers/docker_compose.py` — inject from platform config
  - `engine/deployers/gcp_cloudrun.py` — inject as Cloud Run env var
- [ ] **Trace ID propagation**: pass `trace_id` through A2A calls so distributed traces span multiple agents
- [ ] **Cost attribution**: forward `llm.token_count.*` span attributes to cost tracking service
- [ ] Unit tests: OTel init with/without endpoint, span attributes correctness, OPENTELEMETRY_ENDPOINT in container env
- [ ] Update `.env.example` with `OPENTELEMETRY_ENDPOINT` and clear documentation comment

### M34 Acceptance Criteria

- [ ] Stateful LangGraph agent retains conversation history across calls via `thread_id`
- [ ] `MemorySaver` used without `DATABASE_URL`; `PostgresSaver` used with it
- [ ] Agent with `interrupt_before` returns `{"status": "interrupted"}` instead of hanging
- [ ] `/resume` endpoint correctly resumes a paused LangGraph graph
- [ ] OpenAI Agents handoff routes to specialist agent and surfaces it in `/invoke` response
- [ ] Nested agents using `.as_tool()` work without auth errors
- [ ] LangGraph and OpenAI Agents deploys emit OTel spans visible in `GET /api/v1/tracing`
- [ ] Each span includes `agent.name`, `agent.version`, `llm.model`, `llm.token_count.*`
- [ ] `OPENTELEMETRY_ENDPOINT` not set → agents still deploy and run (no regression)
- [ ] Gate workflow passes for all changes

---

## Execution Plan — Next 3 Releases

| Release | Milestone | Theme | Key Work | Issues | Est. |
|---------|-----------|-------|----------|--------|------|
| **v1.3** | M25 | Complete Enterprise SDK support | CrewAI, Claude SDK, Google ADK, Custom runtime builders | [#35](https://github.com/rajitsaha/agentbreeder/issues/35) [#36](https://github.com/rajitsaha/agentbreeder/issues/36) [#37](https://github.com/rajitsaha/agentbreeder/issues/37) [#38](https://github.com/rajitsaha/agentbreeder/issues/38) | ~3d |
| **v1.4** | M32 | Distribution | PyPI/Docker Hub/Homebrew infra setup (code done) | — | ~1d |
| **v1.5** | M33 | Multi-Cloud | AWS ECS, Azure Container Apps, Kubernetes deployers | [#34](https://github.com/rajitsaha/agentbreeder/issues/34) | ~5d |
| **v1.6** | M34 | Framework Depth | LangGraph HITL+persistence, OAI Agents handoffs, runtime OTel tracing | [#39](https://github.com/rajitsaha/agentbreeder/issues/39) [#40](https://github.com/rajitsaha/agentbreeder/issues/40) [#41](https://github.com/rajitsaha/agentbreeder/issues/41) | ~5d |

**Recommended sequencing rationale:**
- M25 first — unblocks users who picked CrewAI/Claude SDK/ADK in the init wizard today
- M32 finishes in parallel (manual infra tasks, no coding)
- M33 next — multi-cloud is the #1 product differentiator gap
- M34 last — improves depth of existing runtimes; high value but not a blocker to new users

---

*Last updated: April 10, 2026*
*Status: v0.1–v1.2 complete (M1–M23). v1.3 in progress — M24 (Model Gateway), M26 (AgentOps dashboard), M27 (Production Hardening), M31 (Full Code Orchestration SDK) done; M25 (additional SDK runtime builders) remaining — tracked in #35–#38. v1.4 in progress — M32 (Package Distribution): code complete, manual infra setup remaining. v1.5 planned — M33 (Multi-Cloud Deployers: AWS ECS, Azure Container Apps, Kubernetes) tracked in #34. v1.6 planned — M34 (Framework Depth: LangGraph HITL+persistence, OAI Agents handoffs, runtime OTel tracing) tracked in #39–#41.*
