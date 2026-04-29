# Architecture

> AgentBreeder is a deployment platform. A developer writes `agent.yaml`, runs `agentbreeder deploy`, and the platform handles container building, infrastructure provisioning, governance, and registry registration — automatically, regardless of framework, language, or cloud target.

---

## Contents

1. [Three-Tier Builder Model](#three-tier-builder-model)
2. [System Overview](#system-overview)
3. [The Deploy Pipeline](#the-deploy-pipeline)
4. [Polyglot Agent Runtime](#polyglot-agent-runtime)
5. [AgentBreeder Platform Sidecar (APS)](#agentbreeder-platform-sidecar-aps)
6. [Model Gateway & LiteLLM](#model-gateway--litellm)
7. [Key Abstractions](#key-abstractions)
8. [Multi-Agent Orchestration](#multi-agent-orchestration)
9. [Agent-to-Agent (A2A) Protocol](#agent-to-agent-a2a-protocol)
10. [Observability](#observability)
11. [Memory & RAG](#memory--rag)
12. [Evaluation Framework](#evaluation-framework)
13. [Governance](#governance)
14. [Data Model](#data-model)
15. [API Layer](#api-layer)
16. [Full Code SDK](#full-code-sdk)
17. [Design Principles](#design-principles)
18. [Authentication Model](#authentication-model)

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
│                    ┌──────────────────────┐                             │
│                    │  agent.yaml + code   │  ← Unified internal format  │
│                    └──────────────────────┘                             │
│                               │                                         │
│                               ▼                                         │
│                    ┌──────────────────────┐                             │
│                    │   Deploy Pipeline    │  ← Same for all tiers       │
│                    │   Governance         │                             │
│                    │   Observability      │                             │
│                    │   Registry           │                             │
│                    └──────────────────────┘                             │
└─────────────────────────────────────────────────────────────────────────┘
```

| Tier | Agent Development | Orchestration | Eject Path |
|---|---|---|---|
| **No Code** | Visual builder: pick model, tools, prompt, guardrails. Generates `agent.yaml`. | Visual canvas: wire agents as nodes. Generates `orchestration.yaml`. | "View YAML" → Low Code editor |
| **Low Code** | Write `agent.yaml` in any IDE or dashboard | Write `orchestration.yaml` | `agentbreeder eject` → Python/TS scaffold |
| **Full Code** | Python/TS SDK with programmatic control | SDK orchestration graphs, custom routing | N/A |

**Tier mobility:**
```
No Code ──"View YAML"──→ Low Code ──agentbreeder eject──→ Full Code
```

---

## System Overview

```
Developer                    AgentBreeder Platform                        Cloud

agent.yaml ──→ [ CLI ] ──→ [ API Server ] ──→ [ Engine ] ──→ [ AWS / GCP / K8s / Azure ]
                                  │                │
                                  ▼                ▼
                            [ PostgreSQL ]   [ Container Registry ]
                              (Registry)            │
                                  │                 ▼
                              [ Redis ]      ┌──────────────────────┐
                              (Queue)        │   Agent Container    │
                                             │   APS Sidecar        │ ← cross-cutting concerns
                                             │   MCP Sidecar(s)     │ ← tool servers
                                             └──────────────────────┘
                                                        │
                                                        ▼
                                             [ LiteLLM Proxy :4000 ]
                                                        │
                                             [ Provider APIs ]
                                          (Anthropic / OpenAI / Google / ...)
```

| Component | Technology | Purpose |
|---|---|---|
| CLI | Python, Typer, Rich | `agentbreeder init`, `deploy`, `eval`, `eject`, `chat` |
| API Server | Python 3.11+, FastAPI | 201 REST endpoints, OpenAPI auto-docs |
| Engine | Python | Deploy pipeline — config parsing, container building, cloud provisioning |
| Registry | PostgreSQL, SQLAlchemy | Catalog: agents, tools, models, prompts, MCP servers, templates |
| Queue | Redis | Async deploy jobs, shared state for multi-agent orchestrations |
| Dashboard | React 18, TypeScript, Tailwind, Vite | 46-page web UI — builders, analytics, fleet |
| Python SDK | Python 3.11+ | `pip install agentbreeder-sdk` |
| TypeScript SDK | TypeScript 5.0+ | `npm install @agentbreeder/sdk` |
| LiteLLM Proxy | LiteLLM (self-hosted) | Model gateway — routing, fallbacks, budget enforcement, guardrails |

---

## The Deploy Pipeline

Every `agentbreeder deploy` executes these 8 steps atomically. If any step fails, the entire deploy rolls back.

```
1. Parse & Validate YAML         engine/config_parser.py
        │
2. RBAC Check                    engine/governance.py         (fail fast — never skip)
        │
2.5 Approval Gate                engine/governance.py         (if require_approval: true —
        │                          check asset_approval_requests for status=approved;
        │                          admin bypasses; blocks before any cloud resource is touched)
3. Dependency Resolution         engine/resolver.py           (all registry refs → artifacts)
        │
4. Container Build               engine/builder.py
        │                          └── engine/runtimes/<language>/
        │                                └── RuntimeBuilder.build()
5. Infrastructure Provision      engine/deployers/<cloud>.py
        │                          └── BaseDeployer.provision()
6. Deploy & Health Check         BaseDeployer.deploy()
        │                          └── inject_aps_sidecar()   (APS sidecar + LiteLLM key +
        │                          │                           APS token — both stored in
        │                          │                           litellm_key_refs)
        │                          └── inject_mcp_sidecars()  (tool servers)
7. Auto-Register in Registry     registry/agents.py
        │
8. Return Endpoint URL
```

**Step 6 detail — sidecar injection:**
At deploy time, every agent receives two companion containers injected automatically:

1. **APS sidecar** — provides RAG, memory, tools, A2A, tracing, and cost over a local HTTP API. Holds the per-agent LiteLLM virtual key. The agent connects to it via `APS_URL` + `APS_TOKEN`.
2. **MCP sidecar(s)** — one container per MCP server referenced in `tools:`. The agent connects to them via local socket.

The agent container holds no raw LLM credentials — only `APS_URL` and `APS_TOKEN`.

---

## Polyglot Agent Runtime

AgentBreeder supports agents written in Python, TypeScript/Node.js, Rust, and Go. All languages deploy through the same pipeline and receive the same governance, observability, and registry features.

### `runtime:` block in `agent.yaml`

```yaml
# Polyglot agent
runtime:
  language: node          # python | node | rust | go
  framework: vercel-ai    # open string — validated against plugin registry
  version: "20"           # runtime version
  entrypoint: agent.ts    # optional; defaults per language

# Python agents — unchanged, 100% backward compatible
framework: langgraph
```

`language` is a **closed enum** — it drives the build system (base image, compiler, package manager).
`framework` is an **open string** validated against the plugin registry — new frameworks don't require a schema PR.

### Language Registry (`engine/runtimes/registry.py`)

```python
LANGUAGE_REGISTRY: dict[str, type[LanguageRuntimeFamily]] = {
    "python": PythonRuntimeFamily,   # existing builders reorganised
    "node":   NodeRuntimeFamily,     # Phase 1
    "rust":   RustRuntimeFamily,     # Phase 2
    "go":     GoRuntimeFamily,       # Phase 2
}
```

Adding a new language = one file + one dict entry. Adding a new framework = one template class + one dict entry within the language family. Zero changes to the deploy pipeline, deployers, or schema.

### Supported Frameworks

**Python (existing):**
`langgraph`, `crewai`, `claude_sdk`, `openai_agents`, `google_adk`, `custom`

**Node.js / TypeScript (Phase 1):**

| `framework:` | SDK | Notes |
|---|---|---|
| `vercel-ai` | Vercel AI SDK | Streaming-first |
| `mastra` | Mastra | Workflow-heavy, HITL |
| `langchain-js` | LangChain.js | Teams migrating from Python |
| `openai-agents-ts` | OpenAI Agents SDK (TS) | Multi-agent handoffs |
| `deepagent` | DeepAgent.ts | Reasoning-intensive |
| `custom` | Any | Fallback |

**Rust (Phase 2):** `rig`, `custom`
**Go (Phase 2):** `langchaingo`, `custom`

### Developer Experience

```bash
# TypeScript agent
agentbreeder init --language node --framework vercel-ai --name my-agent

# Rust agent
agentbreeder init --language rust --framework rig --name my-agent

# MCP server in any language
agentbreeder init --type mcp-server --language node --name my-tools

# Same deploy command for all
agentbreeder deploy
```

The developer writes one file (`agent.ts`, `main.rs`, `main.go`). The platform owns the server wrapper, the sidecar, the protocol wiring, and the Dockerfile.

---

## AgentBreeder Platform Sidecar (APS)

The APS is a companion container injected alongside every deployed agent. It exposes all cross-cutting concerns — RAG, memory, tools, A2A, tracing, and LiteLLM gateway access — over a simple local HTTP API. This eliminates the need to implement these concerns in every language.

```
┌────────────────────────────────────────────┐
│           Deployed Agent Pod               │
│                                            │
│  ┌─────────────────┐  ┌─────────────────┐  │
│  │  Agent Container│  │   APS Sidecar   │  │
│  │  (any language) │  │                 │  │
│  │                 │◄─┤ POST /tools/execute
│  │  APS_URL        │  │ GET  /rag/search │  │
│  │  APS_TOKEN      │  │ GET  /memory/load│  │
│  │                 │  │ POST /memory/save│  │
│  │                 │  │ POST /a2a/call   │  │
│  │                 │  │ POST /trace/span │  │
│  │                 │  │ GET  /config     │  │
│  └─────────────────┘  │ GET  /health     │  │
│                       └────────┬────────┘  │
│                                │           │
│                    LITELLM_API_KEY (scoped) │
│                    LITELLM_BASE_URL         │
└────────────────────────────────────────────┘
                                 │
                                 ▼
                      LiteLLM Proxy (:4000)
```

### APS HTTP API

| Endpoint | Method | Purpose |
|---|---|---|
| `/config` | GET | Agent config + `litellm_base_url` + `litellm_api_key` |
| `/tools/execute` | POST | Execute a registered tool by name |
| `/rag/search` | GET | Semantic search over knowledge bases |
| `/memory/load` | GET | Load conversation history for a thread |
| `/memory/save` | POST | Persist conversation messages |
| `/a2a/call` | POST | Call a remote agent by name |
| `/trace/span` | POST | Record an observability span |
| `/health` | GET | Sidecar liveness check |

Auth: `Authorization: Bearer $APS_TOKEN` — shared secret between agent and APS, injected at deploy time.

### `/config` Response

```json
{
  "agent_name": "customer-support-agent",
  "model": "claude-sonnet-4",
  "kb_index_ids": ["kb/product-docs"],
  "tools": [{"name": "zendesk-lookup", "description": "..."}],
  "litellm_base_url": "http://litellm:4000",
  "litellm_api_key": "sk-agent-customer-support-agent"
}
```

Every framework template initialises its LLM client from `/config`:

```typescript
// vercel_ai_server.ts
const cfg = await aps.config()
const openai = new OpenAI({ baseURL: cfg.litellm_base_url, apiKey: cfg.litellm_api_key })
```

```rust
// rig_server.rs
let cfg = aps.config().await?;
let client = openai::Client::with_base_url(&cfg.litellm_base_url, &cfg.litellm_api_key);
```

### APS Implementations

| Agent language | Sidecar image | Python in stack? |
|---|---|---|
| `python` | `agentbreeder-aps-py` | Yes |
| `node` | `agentbreeder-aps-node` | No |
| `rust` / `go` | `agentbreeder-aps` (Go binary, ~15 MB) | No |

All three implementations satisfy the same HTTP contract — shared contract tests run against all of them.

### Key Isolation

The APS sidecar holds the LiteLLM virtual key (`sk-agent-<name>`). The agent container receives only `APS_URL` and `APS_TOKEN` — never raw LLM credentials. This means:
- Rotating a LiteLLM key requires updating only the APS sidecar; agent stays up with zero downtime
- Compromising the agent container does not expose the LiteLLM key

---

## Model Gateway & LiteLLM

AgentBreeder uses LiteLLM as its model gateway — a self-hosted proxy that sits between agents and LLM providers. The gateway is the enforcement point for budget, guardrails, caching, and observability.

### Two LiteLLM Modes

**Mode 1 — LiteLLM Proxy (gateway mode)**
Set `model.gateway: litellm` in `agent.yaml`. All LLM calls route through the proxy at `:4000`. The agent's APS sidecar holds the per-agent virtual key.

```yaml
model:
  primary: claude-sonnet-4
  fallback: gpt-4o
  gateway: litellm
```

**Mode 2 — LiteLLM Python SDK (direct mode)**
When `model.primary` uses a provider prefix (`openai/`, `anthropic/`, `bedrock/`, etc.) without `model.gateway`, the Python runtime injects `litellm>=1.40.0` into the agent's `requirements.txt` and the agent calls `litellm.completion()` in-process.

```yaml
model:
  primary: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
  # no gateway field → SDK mode
```

The two modes are mutually exclusive. When `model.gateway == "litellm"`, the SDK is not injected (guard in `engine/runtimes/base.py::_should_add_litellm_sdk()`).

### Architecture

```
agent.yaml (model.gateway: litellm)
        │
        ▼
AgentBreeder engine
  ├── RBAC check
  ├── Mints per-agent virtual key (sk-agent-<name>)
  └── Injects into APS sidecar (not agent container)
        │
        ▼  (agent calls APS /config → gets litellm_base_url + litellm_api_key)
        │
        ▼
LiteLLM proxy (:4000)
  ├── Validates virtual key
  ├── Enforces team budget (max_budget, budget_duration)
  ├── Runs PII guardrail (Presidio)
  ├── Checks Redis cache
  ├── Routes with fallback (primary → fallback on error)
  ├── Emits OTEL span → AgentBreeder tracing API
  └── Records spend → AgentBreeder cost dashboard
        │
        ▼
Provider (Anthropic / OpenAI / Google / Ollama / OpenRouter / ...)
```

### Features Used

| Feature | Tier | Notes |
|---|---|---|
| Provider routing (100+ providers) | OSS | `litellm_config.yaml` |
| Virtual keys (per-agent `sk-` tokens) | OSS | `api/services/litellm_key_service.py` |
| Per-team budgets + reset cycles | OSS | Registered on team creation |
| Redis exact-match caching | OSS | TTL 600s default |
| Fallback chains + retries | OSS | `model.fallback` in `agent.yaml` |
| Load balancing (least-busy) | OSS | `litellm_config.yaml` |
| Health-driven routing | OSS | Proactive reroute on degradation |
| Presidio PII guardrail | OSS | `mode: pre_call`, redacts output |
| Lakera prompt injection detection | OSS | `mode: pre_call` |
| OTEL callback → tracing API | OSS | `x-litellm-call-id` bridges to audit log |
| Prometheus `/metrics` | OSS | Fleet dashboard, spend per team |
| Tag-based routing (team pools) | OSS | Phase 4 |
| Semantic caching (Redis vectors) | OSS | Phase 4 |

**What AgentBreeder keeps (does NOT delegate to LiteLLM):**

| Concern | Reason |
|---|---|
| RBAC | AgentBreeder's RBAC knows about agents, deploys, and teams; LiteLLM's is key/team only |
| Audit trail | `api/routes/audit.py` links entries to `agent_id` + `deploy_id` |
| Secret management | `engine/secrets/` supports AWS KMS, GCP Secret Manager, Vault |
| Prompt registry | `registry/prompts.py` is the source of truth |

### Virtual Keys

Every agent gets a scoped virtual key automatically minted at deploy time (`api/services/litellm_key_service.py::get_or_create_agent_key()`). The key:
- Is injected into the **APS sidecar** (not the agent container)
- Is scoped to the agent's allowed models
- Is attributed to the agent's team for cost tracking
- Can be revoked from the dashboard without redeploying the agent

### Cost Tracking

- Agents calling LiteLLM via the proxy → cost tracked automatically per virtual key
- Agents calling local Ollama → APS estimates cost from model pricing tables and posts to LiteLLM `/global/spend`
- No client-side cost recording endpoint — it would cause double-counting

---

## Key Abstractions

### RuntimeBuilder (`engine/runtimes/base.py`)

```python
class RuntimeBuilder(ABC):
    def validate(self, agent_dir: Path, config: AgentConfig) -> ValidationResult
    def build(self, agent_dir: Path, config: AgentConfig) -> ContainerImage
    def get_entrypoint(self, config: AgentConfig) -> str
    def get_requirements(self, config: AgentConfig) -> list[str]
```

**Python runtimes:** `langgraph`, `crewai`, `claude_sdk`, `openai_agents`, `google_adk`, `custom`
**Node runtimes (Phase 1):** `vercel_ai`, `mastra`, `langchain_js`, `openai_agents_ts`, `deepagent`, `custom_node`
**Rust runtimes (Phase 2):** `rig`, `custom_rust`
**Go runtimes (Phase 2):** `langchaingo`, `custom_go`

**LiteLLM SDK injection guard:**
```python
def _should_add_litellm_sdk(config: AgentConfig) -> bool:
    """Only inject litellm SDK when NOT using the proxy gateway."""
    return _is_litellm_model(config.model.primary) and config.model.gateway != "litellm"
```

### BaseDeployer (`engine/deployers/base.py`)

```python
class BaseDeployer(ABC):
    async def provision(self, config: AgentConfig) -> InfraResult
    async def deploy(self, config: AgentConfig, image: ContainerImage) -> DeployResult
    async def inject_aps_sidecar(
        self, config: AgentConfig, litellm_api_key: str, aps_token: str
    ) -> dict
    async def health_check(self, deploy_result: DeployResult) -> HealthStatus
    async def teardown(self, agent_id: str) -> None
    async def get_logs(self, agent_id: str, since: datetime) -> list[LogEntry]
```

`inject_aps_sidecar()` is the unified method for both APS container injection (#129) and LiteLLM key delivery (#131). The agent container receives only `APS_URL` + `APS_TOKEN`.

**Deployers:** `docker_compose`, `gcp_cloudrun`, `aws_ecs`, `aws_app_runner`, `kubernetes`, `azure_container_apps`, `claude_managed`

### LLM Providers (`engine/providers/`)

Native provider implementations used when `model.gateway` is not set:

| Provider | File | Notes |
|---|---|---|
| Anthropic | `anthropic_provider.py` | Tool use, thinking, prompt caching |
| OpenAI | `openai_provider.py` | Function calling, vision |
| Google | `google_provider.py` | Gemini, Vertex AI |
| Ollama | `ollama_provider.py` | Local inference |
| LiteLLM proxy | `litellm_provider.py` | Used when `model.gateway: litellm` |

`engine/providers/registry.py` manages provider selection and fallback chains. When `model.gateway == "litellm"`, `create_provider()` returns a `LiteLLMProvider` regardless of the `model.primary` prefix.

### Registry (`registry/`)

Central catalog for all organizational AI assets. Entries are only created or updated by:
1. `agentbreeder deploy` (primary path)
2. Connectors (passive ingestion)
3. `agentbreeder registry [prompt|tool|agent] push` (operator override)

**Tracked entity types:** Agents, A2A Agents, Tools, Models, Prompts, Providers, MCP Servers, Templates, Deploys, Marketplace listings.

#### Registry-ref pattern (prompts + tools)

Agents reference prompts and tools by name from `agent.yaml` instead of inlining
them in code:

```yaml
prompts:
  system: prompts/<agent-name>-system     # resolved by engine.prompt_resolver
tools:
  - ref: tools/<kebab-tool-name>          # resolved by engine.tool_resolver
```

Both resolvers follow a **file-first** chain: local file in the project →
DB-backed registry API at `${AGENTBREEDER_REGISTRY_URL}/api/v1/registry/...` →
inline literal fallback. Local files win so per-agent overrides work without
touching the registry; the API is the source of truth across agents and across
deploys.

| Resolver | Python | TypeScript |
|---|---|---|
| Prompts | `engine.prompt_resolver.resolve_prompt()` | `resolvePrompt()` in `_shared_loader.ts` |
| Tools   | `engine.tool_resolver.resolve_tool()` | `resolveTool()` in `_shared_loader.ts` |

**Tool dispatch** (set by the `endpoint` field on the tool record, populated
automatically by `agentbreeder registry tool push`):

| Endpoint prefix | Dispatcher |
|---|---|
| `engine.tools.standard.<name>` | In-process Python import — for first-party stdlib tools |
| `python:<abs_path>`             | Python subprocess — local `.py` tool file |
| `node:<abs_path>`               | Node subprocess via `npx tsx` — TypeScript / JS tool file |
| `http(s)://...`                 | HTTP POST with JSON body — remote / MCP-style tool |

**Try-it / Run / Invoke endpoints** — every registered entity is executable
from CLI + UI:

| Endpoint | What it does |
|---|---|
| `POST /api/v1/registry/prompts/{id}/render` | Send the prompt + a user message to a real LLM, return the response |
| `POST /api/v1/registry/tools/{id}/execute` | Dispatch the tool by endpoint prefix, return `{output, stdout, stderr, exit_code, duration_ms, error}` |
| `POST /api/v1/agents/{id}/invoke` | Server-side proxy that forwards to the agent's deployed `/invoke` with bearer auth — solves CORS, keeps secrets server-side |

### Connectors (`connectors/`)

```python
class BaseConnector(ABC):
    async def scan(self) -> list[RegistryEntry]
    async def is_available(self) -> bool
```

| Connector | Purpose |
|---|---|
| `connectors/litellm/` | Discover models from LiteLLM proxy; model cost metadata |
| `connectors/openrouter/` | Discover 200+ models via OpenRouter |
| `connectors/mcp_scanner/` | Auto-discover local MCP servers |

### Secrets Backends (`engine/secrets/`)

| Backend | File | Use Case |
|---|---|---|
| Environment / `.env` | `env_backend.py` | Local development |
| AWS Secrets Manager | `aws_backend.py` | AWS deployments |
| GCP Secret Manager | `gcp_backend.py` | GCP deployments |
| HashiCorp Vault | `vault_backend.py` | Enterprise multi-cloud |

---

## Multi-Agent Orchestration

The orchestration engine (`engine/orchestrator.py`) coordinates multiple agents per a strategy defined in `orchestration.yaml` or the SDK.

**6 execution strategies:** `sequential`, `parallel`, `router`, `hierarchical`, `supervisor`, `fan_out_fan_in`

Shared state between agents in an orchestration is backed by Redis, scoped to the orchestration session. Orchestrations appear in the registry and share the same deploy pipeline as individual agents.

---

## Agent-to-Agent (A2A) Protocol

JSON-RPC 2.0 inter-agent communication (`engine/a2a/`).

| Component | Purpose |
|---|---|
| `protocol.py` | JSON-RPC 2.0 message format and routing |
| `client.py` | Call remote agents from within an agent |
| `server.py` | Expose an agent as a callable A2A service |
| `auth.py` | Agent cards and mutual authentication |

From any language, A2A calls route through the APS sidecar's `POST /a2a/call` endpoint — no language-specific A2A SDK required.

---

## Observability

### Tracing (`api/routes/tracing.py`)

Every LLM call, tool invocation, and agent step is captured as a trace containing spans. The APS sidecar's `POST /trace/span` endpoint provides language-agnostic trace ingestion.

LiteLLM OTEL spans are correlated with AgentBreeder audit log entries via the `x-litellm-call-id` header — every inference call is linked to the `agent_id` and `deploy_id` that made it.

### Cost Tracking (`api/routes/costs.py`)

Spend data is sourced from LiteLLM's `/global/spend` endpoint when the gateway is active:
- Per agent, team, model, and provider
- Daily trend, ML-based forecasting, anomaly detection
- Per-team monthly budgets with alert thresholds (default 80%)
- Chargeback / cost allocation to departments

### Audit Trail (`api/routes/audit.py`)

Immutable log of every deploy, config change, access change, and key lifecycle event — with actor, action, resource, team, and timestamp. Supports dependency lineage and impact analysis ("if I change this prompt, which agents are affected?").

### AgentOps Fleet Dashboard (`api/routes/agentops.py`)

Fleet-wide visibility: heatmap, top-agent leaderboard, real-time telemetry, canary tracking, incident management, compliance status.

---

## Memory & RAG

### Memory (`api/routes/memory.py`)

Four pluggable backends: in-memory, SQLite, PostgreSQL, Vector DB. Accessible from any language via APS `GET /memory/load` and `POST /memory/save`.

### RAG (`api/routes/rag.py`)

Knowledge base indexing with fixed-size, recursive, and token-aware chunkers. Hybrid vector + full-text search. Accessible from any language via APS `GET /rag/search`. Supported backends: ChromaDB (default), Neo4j (Graph RAG), pgvector.

---

## Evaluation Framework

| Concept | Description |
|---|---|
| Datasets | Versioned `(input, expected_output)` collections |
| Runs | Dataset execution against a specific agent version |
| Scorers | Correctness, Relevance, Latency, Cost, LLM-as-judge |
| Promotion Gates | CI/CD gate: blocks deploy if eval scores drop below threshold |

```bash
agentbreeder eval run --dataset golden-v2 --agent customer-support
agentbreeder eval gate --min-score 0.85
```

---

## Governance

### Identity Model

```
Org (AgentBreeder instance)
 └── Teams  (engineering, data-science, ops)
      ├── Users              email + password OR SSO identity
      ├── Service Principals for CI/CD and machine-to-machine calls
      └── Groups             named sets of users (e.g. "ml-leads")
```

Every principal that joins a team is automatically issued a scoped LiteLLM virtual key. Every agent deployment is attributed to the deploying principal and team.

### Platform Roles

| Role | Deploy | Approve | Manage Teams | Billing |
|---|:---:|:---:|:---:|:---:|
| `admin` | yes | yes | yes | yes |
| `deployer` | yes | no | no | no |
| `contributor` | submit only | no | no | no |
| `viewer` | no | no | no | no |

Enforced by `api/middleware/rbac.py::require_role()` and `api/services/rbac_service.py::check_permission()`. All 27 route files require authentication — no unauthenticated routes except `/health`, `/auth/login`, `/auth/register`, and SSO callbacks.

### Per-Asset ACL

Eight asset types have fine-grained ACLs stored in `resource_permissions`: `agent`, `prompt`, `tool`, `memory`, `rag`, `knowledge_base`, `model`, `mcp_server`.

| Principal | read | use | write | deploy | publish | admin |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Owner (creator) | yes | yes | yes | requires approval | yes | yes |
| Owner's team | yes | yes | no | no | no | no |
| Other teams | yes | no | no | no | no | no |
| Unauthenticated | no | no | no | no | no | no |

ACL is enforced at the API route layer (`check_permission()`) and at the APS sidecar layer (before any tool execution or RAG search).

### Approval Workflow

```
contributor submits agent
       │
       ▼
asset_approval_requests (status: pending)  →  admin group notified
       │
       ├── Admin approves  →  status: approved  →  deploy may proceed
       └── Admin rejects   →  status: rejected  →  contributor sees reason
```

The deploy gate in `engine/governance.py::check_deploy_approved()` checks `asset_approval_requests` between Step 2 (RBAC) and Step 5 (Provision). If `agent.yaml::access.require_approval: true` and no approved request exists, the deploy fails before any cloud resources are provisioned — meaning no LiteLLM key is ever minted for an unapproved agent.

Admin users bypass the gate. Every decision is written to the immutable audit log.

### Credential Types

Four types of scoped credentials, all stored in `litellm_key_refs` (`scope_type` enum):

| Scope | Issued to | Minted when | Revoked when |
|---|---|---|---|
| `user` | Human team member | Joins a team | Leaves team |
| `service_principal` | CI/CD bot, automation | SP created | SP deleted/deactivated |
| `agent` | Deployed agent container (via APS) | Agent deployed | Agent torn down |
| `aps_sidecar` | APS sidecar (internal) | Agent deployed | Agent torn down |

The `aps_sidecar` token is a JWT-signed credential carrying `{agent_name, team_id, deploy_id}`. It is injected into both the agent container (`APS_TOKEN`) and the APS sidecar for mutual verification. The agent container never holds raw LiteLLM credentials — only `APS_URL` and `APS_TOKEN`.

### Budget Attribution Chain

Every LiteLLM key carries a `team_id` that links to a registered LiteLLM team (`teams.litellm_team_id`). Spend from all four key types rolls up to the same team budget:

```
teams.litellm_team_id  ←  registered via POST /team/new on team creation
        │
        ├── user keys         (auto-minted on team membership insert)
        ├── service principal keys (minted on SP creation)
        └── agent keys        (minted at deploy, held by APS sidecar)
```

Per-team monthly budget caps are enforced at the LiteLLM proxy — once the budget is exhausted, all keys under that team's LiteLLM team ID are rate-limited until the budget resets.

### Database Tables (RBAC)

| Table | Purpose |
|---|---|
| `team_memberships` | User ↔ team associations with role |
| `resource_permissions` | Fine-grained ACL per asset (type, id, principal, actions) |
| `asset_approval_requests` | Persisted approval queue (status: pending/approved/rejected) |
| `service_principals` | Non-human identities for CI/CD |
| `principal_groups` | Named sets of users for bulk ACL assignment |
| `litellm_key_refs` | All scoped virtual keys (user, SP, agent, APS) |

### Enterprise SSO (Phase 4 — planned)

Pluggable auth providers: `local | okta | azure_ad | aws_iam | google | saml`. OIDC/OAuth2 with PKCE, SAML 2.0 SP, AWS IAM OIDC workload identity, Azure MSAL + Graph groups. Groups claim maps to AgentBreeder teams automatically. Tracked in follow-up issue.

### Git Workflow (`api/routes/git.py`)

PR-based change management for agent configuration:
```bash
agentbreeder submit   # create PR for config change
agentbreeder review   # review pending PRs
agentbreeder publish  # merge approved PR and deploy
```

### Marketplace (`api/routes/marketplace.py`)

Community catalog for sharing agents, tools, and orchestrations. Browse, install, rate, and publish. One-click install pulls a marketplace template into the registry.

---

## Data Model

```
Org
 └── Team ──────────────────────────────────────────────────┐
      │  litellm_team_id (→ LiteLLM budget group)           │
      ├── TeamMembership ──> User                            │
      ├── ServicePrincipal                                   │
      └── PrincipalGroup                                     │
                                                             │
Agent ──references──> Tool           (many-to-many)         │
  │                   Model          (many-to-one)           │
  │                   Prompt         (many-to-many)          │
  │                   KnowledgeBase  (many-to-many)          │
  │                   MCP Server     (many-to-many)          │
  │                                                          │
  ├── belongs to ──────────────────────────────────────────> Team
  ├── deployed as ──> Deploy (job history)
  ├── approval via ──> AssetApprovalRequest (status: pending/approved/rejected)
  ├── ACL via ──────> ResourcePermission (per-asset, per-principal)
  ├── key (agent) ──> LiteLLMKeyRef (scope_type=agent, held by APS sidecar)
  ├── key (APS) ───> LiteLLMKeyRef (scope_type=aps_sidecar, agent↔APS auth)
  └── exposed as ──> A2AAgent (optional)

User ──> LiteLLMKeyRef (scope_type=user, auto-minted on team join)
ServicePrincipal ──> LiteLLMKeyRef (scope_type=service_principal)

Orchestration ──references──> Agent (many-to-many)
  └── belongs to ──> Team
```

Storage: PostgreSQL with SQLAlchemy ORM. Migrations via Alembic (`alembic/versions/`).

---

## API Layer

FastAPI with async handlers throughout. Consistent response envelope:

```json
{
  "data": { ... },
  "meta": { "page": 1, "total": 42 },
  "errors": []
}
```

25 route modules across: agents, A2A, orchestrations, deploys, evals, providers, gateway, costs, agentops, tracing, audit, teams, prompts, RAG, memory, MCP servers, templates, marketplace, git, builders, sandbox, playground, registry, auth, v2/agents.

API versioning (`api/versioning.py`): v1 is stable. v2 routes are preview. Deprecation headers added automatically when a v1 endpoint has a v2 equivalent.

---

## Full Code SDK

Builder-pattern API for the Full Code tier:

```python
from agenthub import Agent, Tool, Model, Memory

agent = (
    Agent("my-agent", version="1.0.0", team="engineering")
    .with_model(primary="claude-sonnet-4", fallback="gpt-4o", gateway="litellm")
    .with_tool(Tool.from_ref("tools/zendesk-mcp"))
    .with_memory(backend="redis")
    .with_guardrail("pii_detection")
    .with_deploy(cloud="aws", runtime="ecs-fargate")
)
result = agent.deploy()
```

Full YAML round-trip: `agent.to_yaml()` → valid `agent.yaml`; `Agent.from_yaml()` → SDK objects.

---

## Design Principles

1. **Governance is a side effect** — deploying through AgentBreeder automatically creates RBAC records, cost attribution, audit entries, and registry listings. No separate governance setup.

2. **Framework-agnostic** — no framework-specific logic outside `engine/runtimes/`. The rest of the system treats all frameworks identically.

3. **Language-agnostic** — no language-specific logic outside `engine/runtimes/<language>/`. The APS sidecar delivers feature parity to every language without re-implementation.

4. **Multi-cloud** — no cloud-specific logic outside `engine/deployers/`. AWS, GCP, Azure, and Kubernetes are equal first-class targets.

5. **The deploy pipeline is sacred** — the 8-step flow is the product. Protect it like an API contract. Never skip a step. Never break atomicity.

6. **Registry consistency** — all registry writes go through registry service classes. No direct table access from application code.

7. **Gateway owns routing; AgentBreeder owns governance** — LiteLLM handles provider translation, fallbacks, retries, caching. AgentBreeder handles RBAC, audit, secrets, and prompt registry. Neither owns the other's domain.

8. **Credentials never touch agent code** — the agent container receives only `APS_URL` and `APS_TOKEN`. All LLM credentials, virtual keys, and secrets live in the APS sidecar or Secrets Manager. Rotating a key requires no agent redeployment.

9. **Three tiers, one pipeline** — No Code, Low Code, and Full Code all compile to the same internal format. Tier-specific logic never appears in the engine, deployers, or registry.

10. **Tier mobility** — users move between tiers without losing work. No Code generates valid YAML. Low Code ejects to Full Code. No lock-in at any abstraction level.

---

## Authentication Model

AgentBreeder enforces auth at two distinct layers:

### Layer 1 — Management API (`/api/v1/*`)

JWT-based, gated at the route level via FastAPI dependencies. **247 of 247
routes** require authentication; only `auth/login` and `auth/register` are open
by design (needed to bootstrap a session).

```python
# Default: any authenticated user
async def list_agents(
    user: User = Depends(get_current_user),
    ...
)

# Stricter: role-based gate
async def register_tool(
    user: User = Depends(require_role("deployer")),
    ...
)
```

Roles: `viewer` < `deployer` < `admin`. Mutating the registry (push prompt /
tool / agent, run tool, deploy) requires `deployer` or higher.

### Layer 2 — Agent Runtime (`/invoke`, `/stream`, `/resume`, `/mcp`)

Bearer-token auth via the `AGENT_AUTH_TOKEN` env var on the deployed agent
container. Every framework runtime template (6 Python servers, 9 Node servers)
implements the same contract:

| Endpoint | Authenticated |
|---|---|
| `GET /health` | No (Cloud Run / k8s liveness probes) |
| `GET /.well-known/agent.json` (Node only) | No (A2A discovery) |
| `POST /invoke`, `POST /stream`, `POST /resume`, `POST /mcp` | Yes (`Authorization: Bearer <AGENT_AUTH_TOKEN>`) |

When the env var is unset/empty, auth is disabled — for local development
ergonomics. Production deploys always set it (via Secret Manager, AWS Secrets
Manager, Vault, etc.) and list it in `agent.yaml`'s `deploy.secrets` so the
deploy pipeline mounts it automatically.

### Why two layers

The management API and the agent runtime are independent failure domains:

- **Management API** owns the registry — accessed by humans (dashboard) and
  CI (CLI commands). Its threat model is internal abuse + RBAC.
- **Agent runtime** owns inference — accessed by end-user clients or peer
  services. Its threat model is public exposure + token compromise.

Mixing them would force the dashboard to issue runtime tokens (insecure) or
force end users to obtain platform JWTs (wrong audience). The proxy endpoint
`POST /api/v1/agents/{id}/invoke` bridges them: takes a JWT in, attaches the
bearer for the agent runtime out, and never exposes the bearer to the browser.

---

*See [CLAUDE.md](CLAUDE.md) for coding standards, the full `agent.yaml` spec, and development commands.*
*See [ROADMAP.md](ROADMAP.md) for the release plan and milestone status.*
*See [docs/design/](docs/design/) for feature-level design documents (RBAC, LiteLLM gateway, polyglot agents).*
