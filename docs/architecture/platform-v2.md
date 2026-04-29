# AgentBreeder Platform v2 — Architecture Spec

> **Status:** Draft (locked) — 2026-04-28
> **Replaces no existing doc.** Unifies the v2 platform substrate.
> **Drives:** Epic issue #TBD + child issues #TBD-A..F

---

## TL;DR

AgentBreeder v1 shipped the deploy pipeline, registry, dashboard, and Python+TS runtimes. **v2 turns the platform into a substrate**: anything above the substrate (frameworks, clouds, languages, providers) plugs in without engine changes.

Eight tracks. Five already have specs/issues (referenced below). Three are new and locked down by this doc.

| # | Track | Source of truth | Owner | Status |
|---|---|---|---|---|
| **A** | Workspace primitive (init, login, multi-cloud config) | Issue #146 | (assigned) | Spec'd |
| **B** | `/agent-build` as full lifecycle entrypoint | Issue #147 (blocks on A) | (assigned) | Spec'd |
| **C** | Polyglot Phase 1: TypeScript runtime | `docs/design/polyglot-agents.md` | (in flight, PR #136 merged) | In flight |
| **D** | LiteLLM gateway integration | `docs/design/litellm-integration.md` | — | Partial (connector exists) |
| **E** | RBAC + Auth (247/247 routes gated) | `docs/design/rbac-auth.md` | — | Shipped (v1.9) |
| **F** | **Generic OpenAI-Compatible Provider + Catalog** | This doc §1 | TBD | **New — locked here** |
| **G** | **Model Lifecycle (auto-discover, status, retire)** | This doc §2 | TBD | **New — locked here** |
| **H** | **Gateway-as-First-Class (LiteLLM + OpenRouter)** | This doc §3 | TBD | **New — locked here** |
| **I** | **Polyglot Phase 2/3: Go, Kotlin, Rust, .NET + Runtime Contract** | This doc §4 | TBD | **New — locked here** |
| **J** | **Sidecar (cross-cutting concerns layer)** | This doc §5 | TBD | **New — locked here** |
| **K** | **Secrets-per-workspace + auto-mirror to cloud** | This doc §6 | TBD | **New — locked here** |

**Not changing:** the deploy pipeline (CLAUDE.md §"Deploy Pipeline (Sacred)") stays exactly as-is. Every v2 track plugs into existing extension points.

---

## §1 Track F — Generic OpenAI-Compatible Provider + Catalog

### Problem
`engine/providers/` has 5 hand-written providers (openai, anthropic, google, ollama, litellm). Adding Nvidia NIM, Moonshot/Kimi, Groq, Together, Fireworks, DeepInfra, Cerebras, Hyperbolic each requires a copy-paste class. The reality: **all of them are OpenAI-compatible** — just different `base_url` + `api_key`.

### Spec

**One new provider class** + **one catalog YAML**.

```
engine/providers/
├── openai_compatible.py      # NEW — generic class parameterized by base_url, api_key_env, default_headers
├── catalog.yaml              # NEW — checked-in registry of preset providers
└── (existing classes unchanged)
```

**`engine/providers/catalog.yaml`** (initial set):

```yaml
version: 1
providers:
  nvidia:
    type: openai_compatible
    base_url: https://integrate.api.nvidia.com/v1
    api_key_env: NVIDIA_API_KEY
    docs: https://build.nvidia.com/models
    discovery: openai-models-list      # uses GET /models
  openrouter:
    type: openai_compatible
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    default_headers:
      HTTP-Referer: https://agentbreeder.io
      X-Title: AgentBreeder
    docs: https://openrouter.ai/models
    discovery: openai-models-list
  moonshot:
    type: openai_compatible
    base_url: https://api.moonshot.cn/v1
    api_key_env: MOONSHOT_API_KEY
    notable_models: [moonshot-v1-128k, kimi-k2-instruct]
  groq:
    type: openai_compatible
    base_url: https://api.groq.com/openai/v1
    api_key_env: GROQ_API_KEY
  together:
    type: openai_compatible
    base_url: https://api.together.xyz/v1
    api_key_env: TOGETHER_API_KEY
  fireworks:
    type: openai_compatible
    base_url: https://api.fireworks.ai/inference/v1
    api_key_env: FIREWORKS_API_KEY
  deepinfra:
    type: openai_compatible
    base_url: https://api.deepinfra.com/v1/openai
    api_key_env: DEEPINFRA_API_KEY
  cerebras:
    type: openai_compatible
    base_url: https://api.cerebras.ai/v1
    api_key_env: CEREBRAS_API_KEY
  hyperbolic:
    type: openai_compatible
    base_url: https://api.hyperbolic.xyz/v1
    api_key_env: HYPERBOLIC_API_KEY
```

### Onboarding paths

| Path | Mechanism | Use case |
|---|---|---|
| **A. Built-in preset** | Entry in `catalog.yaml` (above) | Common providers everyone benefits from |
| **B. User-local** | `agentbreeder provider add <name> --base-url X --api-key-env Y --type openai_compatible` → writes to `~/.agentbreeder/providers.local.yaml` (or workspace-scoped) | Private/internal providers, self-hosted vLLM |
| **C. Promote local → preset** | `agentbreeder provider publish <name>` opens a PR to upstream `catalog.yaml` | Community contribution |
| **D. Truly novel API shape** | New `engine/providers/<name>.py` PR | Rare — most "new" providers are OpenAI-compatible today |

### CLI surface
```
agentbreeder provider list                          # presets + user-local
agentbreeder provider add <name> --base-url URL --api-key-env ENV --type openai_compatible
agentbreeder provider remove <name>
agentbreeder provider test <name>                   # GET /models, validate auth
agentbreeder provider publish <name>                # PR to upstream catalog
```

### Acceptance criteria
- [ ] `engine/providers/openai_compatible.py` (~150 LoC) implements the `Provider` interface
- [ ] `engine/providers/catalog.yaml` ships 9 presets above
- [ ] `agentbreeder provider {list,add,remove,test,publish}` work
- [ ] Existing `agent.yaml` `model.primary: nvidia/meta-llama-3.1-405b-instruct` resolves correctly
- [ ] Dashboard `/models` page lists presets with "Configure" button per provider
- [ ] Docs: `website/content/docs/providers.mdx` covers all 4 onboarding paths

---

## §2 Track G — Model Lifecycle (auto-discover, status, retire)

### Problem
Provider model lists change weekly (Llama 4 ships, Gemini 2.5 deprecates 2.0). Hand-curating `catalog.yaml` model entries doesn't scale and goes stale.

### Spec

**Hybrid: auto-discovery + curated overlay.**

| Field | Source |
|---|---|
| `id`, `context_window`, `pricing`, `created_at` | Auto from provider's `/models` endpoint |
| `status: active \| beta \| deprecated \| retired` | Auto-derived (vanishes from API → mark `deprecated_at`; absent for 30d → `retired`) |
| `tested_with: [langgraph, crewai, claude_sdk, ...]` | Curated (smoke matrix in CI) |
| `tier: flagship \| balanced \| fast \| local` | Curated |
| `notes`, `recommended_for` | Curated |

### Mechanism
- New module `engine/providers/discovery.py` — per-provider `/models` fetcher (most are OpenAI's `GET /models` shape; Anthropic + Google have their own — tiny adapters)
- New CLI: `agentbreeder model sync` (manual)
- New cron in cloud workspaces: daily auto-sync, diff against registry, emit `model.added` / `model.deprecated` / `model.retired` audit events
- New table `models` in registry DB (or `models.discovered_at`/`last_seen_at` columns on existing model entries)
- Deprecated models: still usable, dashboard shows banner. Retired: rejected at deploy with migration suggestion (lookup nearest active alternative).

### CLI surface
```
agentbreeder model list [--provider X] [--status active|deprecated|retired]
agentbreeder model sync [--provider X]              # manual refresh
agentbreeder model show <id>                         # full metadata
agentbreeder model deprecate <id> --replacement Y    # admin override
```

### Acceptance criteria
- [ ] `engine/providers/discovery.py` with adapter per provider type
- [ ] DB migration: `models.status`, `models.discovered_at`, `models.last_seen_at`, `models.deprecation_replacement_id`
- [ ] `agentbreeder model sync` refreshes registry
- [ ] Daily cron in cloud workspace
- [ ] Dashboard `/models` shows status badges + replacement suggestions
- [ ] Audit events emitted on lifecycle transitions

---

## §3 Track H — Gateway-as-First-Class (LiteLLM + OpenRouter)

### Problem
`connectors/litellm/` and `connectors/openrouter/` exist but aren't first-class onboarding paths. They're discoverable only by reading code.

### Spec

Promote both into the **provider catalog** with a new `type: gateway` distinction:

| Provider type | Behavior |
|---|---|
| `openai_compatible` | Talks to one upstream provider's models |
| `gateway` | Talks to many upstream providers; user picks model with `<gateway>/<provider>/<model>` syntax |

### `agent.yaml` examples

```yaml
# Direct OpenAI-compatible
model:
  primary: nvidia/meta-llama-3.1-405b-instruct

# Through OpenRouter
model:
  primary: openrouter/moonshotai/kimi-k2

# Through LiteLLM (self-hosted gateway, routes upstream)
model:
  primary: litellm/anthropic/claude-sonnet-4
  gateway: litellm                                  # gateway URL configured at workspace level
```

### Workspace-level gateway config (`workspace.yaml`)

```yaml
gateways:
  litellm:
    url: http://litellm:4000              # or https://litellm.company.internal
    api_key_env: LITELLM_MASTER_KEY
    fallback_policy: fastest               # fastest | cheapest | most-reliable
  openrouter:
    api_key_env: OPENROUTER_API_KEY
```

### Acceptance criteria
- [ ] `type: gateway` recognized in `catalog.yaml` schema
- [ ] OpenRouter + LiteLLM presets in catalog
- [ ] `model.primary` parser handles `<gateway>/<provider>/<model>` triples
- [ ] Workspace `gateways:` config block (lands with Track A workspace primitive)
- [ ] Dashboard `/models` shows 3 tabs: **Direct providers**, **Gateways**, **Local**
- [ ] Docs: `website/content/docs/gateways.mdx` (LiteLLM vs OpenRouter, when to use each)

---

## §4 Track I — Polyglot Phase 2/3 + Runtime Contract

### Problem
Phase 1 (TS) is in flight (Track C / PR #136). Adding Go, Kotlin, Rust, .NET without a stable contract means duplicating tracing/cost/auth/A2A in N languages.

### Spec — Three Tiers

| Tier | Languages | What we maintain | What user does |
|---|---|---|---|
| **1. First-party (SDK + Runtime)** | Python, TypeScript | `agenthub` / `@agentbreeder/sdk`, runtime builder, framework integrations, container template | `agentbreeder init` scaffolds; framework via flag |
| **2. First-party SDK** | Go, Kotlin/Java, Rust, C#/.NET | Thin SDK per language (registry client + deploy client + auth middleware + OpenAPI handlers), generated from OpenAPI | Pick a language-native framework; SDK gives contract handlers |
| **3. BYO contract** | Anything (Swift, Ruby, PHP, Elixir, …) | Container build pipeline + governance + registry — language-blind | Implement the HTTP contract yourself; declare `framework: custom`, `language: <lang>` |

### Runtime Contract v1 (the spec)

Every agent container, regardless of language, exposes:

| Endpoint | Method | Purpose | Auth |
|---|---|---|---|
| `/health` | GET | Liveness probe | open |
| `/invoke` | POST | Run agent (sync) — returns final result | bearer |
| `/stream` | POST | Run agent (streaming, SSE) | bearer |
| `/resume` | POST | Resume from checkpoint (optional, frameworks that support it) | bearer |
| `/mcp` | POST | MCP-protocol passthrough (optional) | bearer |
| `/openapi.json` | GET | Self-describing schema | open |

**Auth:** `Authorization: Bearer ${AGENT_AUTH_TOKEN}` (env var injected at deploy). Token validation is one helper call from each language SDK.

**Invoke shape:**
```json
{ "input": "string or object", "session_id": "uuid?", "metadata": {} }
```
Response: `{ "data": ..., "trace_id": "...", "tokens": {...}, "cost_usd": 0.0034 }`.

### Per-language framework targets (Tier 2)

| Language | Frameworks for `agentbreeder init --framework` |
|---|---|
| Kotlin/Java | langchain4j, spring_ai, koog (#142), anthropic_java_sdk |
| Go | eino, genkit, dapr_agents, langchaingo, anthropic_go_sdk |
| Rust | rig, swiftide, anthropic_rust_sdk |
| C#/.NET | semantic_kernel, autogen_net, anthropic_dotnet_sdk |

### `agent.yaml` change (one new field)
```yaml
language: python              # python | typescript | kotlin | java | go | rust | csharp | custom
framework: langgraph
```

### Acceptance criteria
- [ ] `engine/schema/runtime-contract-v1.md` ships as versioned spec
- [ ] OpenAPI codegen target: `agentbreeder-sdk-{go,kotlin,rust,csharp}` SDKs from CI
- [ ] `engine/runtimes/<lang>/<framework>.py` modules for new (lang, framework) pairs
- [ ] `agentbreeder init --lang go --framework eino` scaffolds working agent
- [ ] One example per language under `examples/`
- [ ] JSON Schema updated to include `language:` field
- [ ] **Gates on Track J (sidecar) for cross-cutting concerns** — without sidecar, Tier 2 SDKs grow too fat

---

## §5 Track J — Sidecar (cross-cutting concerns layer)

### Problem
CLAUDE.md §"Sidecar Pattern (Planned)" describes this. v2 needs it real because polyglot (Track I) hard-blocks on it.

### Spec

**Single Go binary** deployed as a container alongside every agent. One binary, all languages, all clouds. Reasons for Go: small binary, fast cold start, easy cross-compile, mature OTel + gRPC libs.

### What the sidecar handles

| Concern | Mechanism |
|---|---|
| Tracing / OTel | Intercepts agent egress (LLM, MCP, HTTP). Emits OTel spans to configured collector. |
| Cost / token attribution | Parses LLM responses, multiplies by model pricing from registry, emits cost events |
| Guardrails (PII, content filter) | Egress middleware with pluggable rules from `agent.yaml` `guardrails:` |
| A2A protocol | Hosts JSON-RPC client; agent calls `localhost:9090/a2a/<peer>` instead of dealing with auth + discovery |
| MCP server connections | Sidecar runs MCP clients, exposes `localhost:9091/mcp/<server>` to user code |
| Bearer-token validation | Validates `AGENT_AUTH_TOKEN` on inbound; agent code sees pre-validated requests |

### Topology

```
┌──────── Pod / Task / Cloud Run instance ─────────┐
│                                                  │
│  ┌─────────────┐   localhost   ┌──────────────┐  │
│  │  user agent │ ◄───────────► │   sidecar    │  │
│  │  (any lang) │   :8080 in    │  (Go binary) │  │
│  │             │   :9090 a2a   │              │  │
│  │             │   :9091 mcp   │  → OTel      │  │
│  └─────────────┘               │  → cost svc  │  │
│       ▲                        │  → guardrail │  │
│       │ inbound :8080          └──────────────┘  │
└───────┼──────────────────────────────────────────┘
        │
   external traffic (deploy proxy → sidecar → agent)
```

### Acceptance criteria
- [ ] New top-level dir `sidecar/` with Go binary
- [ ] Image published as `rajits/agentbreeder-sidecar:<version>`
- [ ] Auto-injected by deployers (ECS task def, Cloud Run multi-container, K8s pod, docker-compose) when `agent.yaml` has any of: `guardrails:`, `tools:` (with MCP), `a2a:`
- [ ] OTel exporter configurable per workspace
- [ ] Cost events written to `audit_log` and `costs` tables
- [ ] Bypass mode for local dev (env var `AGENTBREEDER_SIDECAR=disabled`)
- [ ] Docs: `website/content/docs/sidecar.mdx`

---

## §6 Track K — Secrets-per-workspace + auto-mirror to cloud

### Problem
`engine/secrets/{env,aws,gcp,vault}_backend.py` exist but there's no workspace-level binding and no path that mirrors local secrets into the cloud's secrets store at deploy time. Users currently set env vars manually in cloud consoles.

### Spec

**One backend per workspace** declared in `workspace.yaml`. Every secret read/write goes through that backend. Per-call backend choice is not a thing — that's a workspace setting only an admin changes.

### Defaults per install mode

| Install mode | Default backend |
|---|---|
| Local CLI / single user | OS keychain (NEW: `engine/secrets/keychain_backend.py` via `keyring` lib) |
| Self-hosted team | Vault if available, else encrypted-at-rest `.env` files |
| Cloud SaaS | AWS Secrets Manager (whatever cloud SaaS runs on) |

### CLI
```
agentbreeder secret set OPENAI_API_KEY        # prompt securely, store in workspace backend
agentbreeder secret list                       # names only, never values
agentbreeder secret rotate OPENAI_API_KEY      # generate, swap, audit
agentbreeder secret sync --target gcp          # mirror selected secrets to target cloud's native store
```

### Auto-mirror at deploy
When `agent.yaml` declares `secrets: [OPENAI_API_KEY]` and `agentbreeder deploy --target gcp` runs:
1. Read secret value from workspace backend
2. Write to GCP Secret Manager under deterministic name `agentbreeder/<agent>/<secret>`
3. Grant agent's runtime SA `secretAccessor` on that specific secret
4. Record secret version + agent deploy in audit log
5. Container references the cloud-native secret path — value never lives in env vars on disk

### Acceptance criteria
- [ ] `engine/secrets/keychain_backend.py` (cross-platform via `keyring`)
- [ ] `engine/secrets/factory.py` reads workspace backend choice
- [ ] `agentbreeder secret {set,list,rotate,sync}` work
- [ ] Auto-mirror logic in each deployer (`engine/deployers/{aws_ecs,gcp_cloudrun,...}`)
- [ ] Audit events: `secret.created`, `secret.rotated`, `secret.mirrored`
- [ ] UI `/settings/secrets` page (lists names, mirrors, rotation actions)
- [ ] Docs: `website/content/docs/secrets.mdx`

---

## Cross-cutting decisions

### Versioning
- This is **v2 of the platform**, not the SDKs. SDK versions (`agentbreeder-sdk` 1.x, `@agentbreeder/sdk` 1.x) keep their own semver.
- `catalog.yaml` versioned (currently `version: 1`). Schema changes bump it.
- Runtime contract has its own version (`v1` initially). Future versions add fields, never break.

### Backward compatibility
- v1 `agent.yaml` files continue to work without modification through v2. New fields (`language:`, etc.) are optional with sensible defaults.
- v1 providers (openai/anthropic/google/ollama) keep their hand-written classes — Track F's `OpenAICompatibleProvider` is **additive**.
- Registry DB migrations are forward-only (no data loss).

### Track ordering (dependency DAG)
```
A (workspace)──┬──► B (/agent-build redesign)
               ├──► H (gateways — needs workspace.yaml gateway block)
               └──► K (secrets per-workspace)

F (provider catalog)──► G (model lifecycle)

J (sidecar) ──► I (polyglot phase 2/3 — needs sidecar to keep SDKs thin)

(C, D, E already speced or shipped)
```

### Release sequence
**Wave 1 (parallel, independent):** F, J, K — all touch different code areas; no conflicts
**Wave 2 (after Wave 1):** G (after F), I (after J), H (after Track A merges)
**Wave 3:** End-to-end docs refresh, examples per language, dashboard tabs polish

---

## Multi-agent execution plan

Six new tracks, all in different code areas → all parallelizable.

| Track | Code area | Parallel-safe with |
|---|---|---|
| F (Provider catalog) | `engine/providers/` | All others |
| G (Model lifecycle) | `engine/providers/discovery.py` + DB migration | F (must merge first) |
| H (Gateway first-class) | `connectors/litellm/`, `connectors/openrouter/`, catalog.yaml | F (must merge first), Track A workspace |
| I (Polyglot Phase 2) | `sdk/{go,kotlin,rust,csharp}/`, `engine/runtimes/<lang>/` | J (must merge first) |
| J (Sidecar) | `sidecar/` (new top-level dir) | All others |
| K (Secrets per workspace) | `engine/secrets/`, `cli/commands/secret.py` | All others |

**Wave 1 (5 parallel agents):** F, J, K, plus design-only writeups for H and I
**Wave 2 (3 parallel agents after Wave 1 lands):** G, H impl, I impl

Each agent:
- Owns one feature branch (`feat/v2-track-<letter>-<slug>`)
- Owns one child issue
- Writes code + tests + docs per the acceptance criteria above
- Opens one PR linked to the child issue

The epic issue tracks all six in one checklist.

---

## Open questions (resolved before merge)

1. **Catalog file location** — `engine/providers/catalog.yaml` (here) or `registry/catalogs/providers.yaml` (alongside other catalogs)? **Decision:** `engine/providers/catalog.yaml` for now; move when registry catalogs proliferate.
2. **Sidecar language** — Go (proposed) vs Rust vs Python? **Decision:** Go. Smallest, fastest cold start, mature OTel.
3. **Per-secret routing** (some secrets local-only, some sync) — needed at v2 launch or follow-on? **Decision:** follow-on. v2 ships all-or-nothing mirroring.
4. **Gateway fallback policy** in `workspace.yaml` — express here or defer to LiteLLM config? **Decision:** express in `workspace.yaml` for parity across gateways; LiteLLM-specific extras live in its own config file.
