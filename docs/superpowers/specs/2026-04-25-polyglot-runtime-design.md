# Polyglot Agent Runtime — Implementation Design

**Date:** 2026-04-25
**Status:** Approved
**GitHub Issue:** agentbreeder/agentbreeder#129
**Reference:** `docs/design/polyglot-agents.md` (revised architecture)
**Phase:** 1 of 2 (TypeScript + Node.js)

---

## Problem Statement

AgentBreeder's deploy pipeline is container-agnostic, but all scaffolding is Python-only. TypeScript developers must write their own server wrapper and lose automatic RAG injection, memory management, tool execution, A2A, tracing, and cost attribution. This design makes TypeScript a first-class citizen with the same zero-infrastructure developer experience Python agents have today.

---

## Architecture Decision

**No new sidecar servers.** The `@agentbreeder/aps-client` npm package is a thin typed HTTP wrapper (~200 lines) that calls the existing AgentBreeder Python API. Each deployed agent container receives `AGENTBREEDER_URL` + `AGENTBREEDER_API_KEY` env vars injected by the deployer. No TypeScript re-implementation of platform logic.

```
TypeScript Agent Container
  server.ts (platform-managed template)
  agent.ts  (developer writes only this)
  @agentbreeder/aps-client
      └── http calls ──► AgentBreeder API (:8000)
                              ├── /api/v1/rag/search
                              ├── /api/v1/memory/*
                              ├── /api/v1/costs/record
                              ├── /api/v1/tracing/spans
                              └── /api/v1/a2a/agents/{name}/invoke
```

---

## Delivery Plan

Two sequential PRs — PR 2 merges before PR 3 begins.

### PR 2: Foundation (pipeline + schema changes)

PR 2 is a pure infrastructure PR. No TypeScript code. Touches the deploy pipeline, schema, and deployers.

#### 1. `engine/config_parser.py` — new models

```python
class LanguageType(enum.StrEnum):
    python = "python"
    node = "node"
    # rust, go reserved for Phase 2

class AgentType(enum.StrEnum):
    agent = "agent"
    mcp_server = "mcp-server"

class RuntimeConfig(BaseModel):
    language: LanguageType
    framework: str          # open string — validated by NodeRuntimeFamily, not schema enum
    version: str | None = None      # e.g. "20" for Node LTS
    entrypoint: str | None = None   # default: agent.ts (node), main.py (python)
```

`AgentConfig` additions:
- `type: AgentType = AgentType.agent`
- `runtime: RuntimeConfig | None = None`
- `framework: FrameworkType | None = None` (was required, now optional)

Model validator: exactly one of `framework` or `runtime` must be set. All existing `framework: langgraph` configs pass unchanged — 100% backward compatible.

#### 2. `engine/schema/agent.schema.json`

Add `runtime` as an optional object property with `language` (enum: python, node) and `framework` (open string). Add `type` enum (`agent` | `mcp-server`). JSON Schema `oneOf` enforces mutual exclusivity of `framework` and `runtime`.

#### 3. `engine/deployers/base.py` — `get_aps_env_vars()`

```python
def get_aps_env_vars(self) -> list[dict[str, str]]:
    return [
        {"name": "AGENTBREEDER_URL",     "value": settings.AGENTBREEDER_URL},
        {"name": "AGENTBREEDER_API_KEY", "value": settings.AGENTBREEDER_API_KEY},
    ]
```

`AGENTBREEDER_URL` and `AGENTBREEDER_API_KEY` added to `api/config.py` (Pydantic-settings). `AGENTBREEDER_URL` defaults locally to `http://agentbreeder-api:8000`. `AGENTBREEDER_API_KEY` is a static service API key used for internal service-to-service calls (not a user JWT) — validated by a new `X-Service-Key` header check on the AgentBreeder API side, gated to the cost/trace/rag/memory/a2a endpoints only.

Three deployers call `get_aps_env_vars()` in their `deploy()` methods:
- `engine/deployers/docker_compose.py` — adds to service `environment:` block
- `engine/deployers/aws_ecs.py` — appends to `containerDefinitions[0].environment`
- `engine/deployers/gcp_cloudrun.py` — appends to `spec.template.spec.containers[0].env`

`kubernetes.py`, `azure_container_apps.py`, `aws_app_runner.py` deferred to Phase 2.

#### 4. `engine/runtimes/registry.py` (new file)

```python
# PR 2: only python registered — node added in PR 3 when node.py ships
LANGUAGE_REGISTRY: dict[str, type[LanguageRuntimeFamily]] = {
    "python": PythonRuntimeFamily,
    # "node":   NodeRuntimeFamily,   # added in PR 3
    # "rust":   RustRuntimeFamily,   # Phase 2
    # "go":     GoRuntimeFamily,     # Phase 2
}

def get_runtime_from_config(config: AgentConfig) -> RuntimeBuilder:
    if config.runtime:
        family = LANGUAGE_REGISTRY.get(config.runtime.language)
        if family is None:
            raise UnsupportedLanguageError(config.runtime.language)
        return family()
    return PythonRuntimeFamily.from_framework(config.framework)
```

PR 3 adds one line to `LANGUAGE_REGISTRY`:
```python
"node": NodeRuntimeFamily,
```

#### 5. `engine/runtimes/python.py` (new file)

Wraps existing per-framework builders (`LangGraphRuntime`, `CrewAIRuntime`, etc.) under `PythonRuntimeFamily` with a `from_framework()` factory. Pure reorganization — no behavior changes. Existing `langgraph.py`, `crewai.py`, etc. untouched.

#### 6. `engine/builder.py` — dispatch update

```python
# Step 4 of deploy pipeline, before:
runtime = get_runtime(config.framework)

# After:
from engine.runtimes.registry import get_runtime_from_config
runtime = get_runtime_from_config(config)
```

Existing `engine/runtimes/__init__.py::get_runtime()` stays for backward compat.

#### PR 2 Tests

- `tests/unit/test_config_parser.py` — `RuntimeConfig` validation: rejects unknown languages, accepts open framework strings, enforces `framework` XOR `runtime`, existing configs unaffected
- `tests/unit/test_deployer_base.py` — `get_aps_env_vars()` returns both vars, reads from settings

---

### PR 3: Node Runtime (depends on PR 2)

#### 7. `engine/runtimes/node.py` — `NodeRuntimeFamily`

Dispatches to one of 8 templates by `config.runtime.framework`. Unknown frameworks fall back to `CustomNodeTemplate` with a warning — never a hard failure.

`build()` generates into the build context:
1. **`Dockerfile`** — multi-stage Node.js build (deps stage + slim runner stage)
2. **`server.ts`** — framework-specific template, written alongside developer's `agent.ts`
3. **`package.json`** — framework SDK deps + `@agentbreeder/aps-client` + `ts-node` + `typescript`
4. **`tsconfig.json`** — ESM, strict mode

Developer's `agent.ts` (or `tools.ts` for MCP) is the only file they write. `Dockerfile` and `server.ts` are platform-managed and not committed to the developer's repo.

Dockerfile shape:
```dockerfile
FROM node:${version}-slim AS deps
WORKDIR /app
COPY package.json tsconfig.json ./
RUN npm ci --only=production

FROM node:${version}-slim AS runner
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NODE_ENV=production
EXPOSE 3000
CMD ["node", "--loader", "ts-node/esm", "server.ts"]
```

#### 8. 8 TypeScript framework templates

Location: `engine/runtimes/templates/node/`

All templates are physical `.ts` files. `NodeRuntimeFamily` reads and does string substitution for agent-specific values (`{{AGENT_NAME}}`, `{{AGENT_VERSION}}`, etc.) at build time.

Every template satisfies the same server contract:
- `GET /health` — liveness probe
- `POST /invoke` — synchronous agent call
- `POST /stream` — SSE streaming response
- `GET /.well-known/agent.json` — A2A agent card

`_shared_loader.ts` handles APS client init, health endpoint, and agent card — imported by all 8 templates.

| Template file | Framework | Core SDK call |
|---|---|---|
| `vercel_ai_server.ts` | Vercel AI SDK | `streamText({ model, messages })` |
| `mastra_server.ts` | Mastra | `agent.generate(messages)` |
| `langchain_js_server.ts` | LangChain.js | `chain.invoke({ input })` |
| `openai_agents_ts_server.ts` | OpenAI Agents TS | `Runner.run(agent, messages)` |
| `deepagent_server.ts` | DeepAgent | `agent.run(input)` |
| `custom_node_server.ts` | Custom | `handler(input)` exported from `agent.ts` |
| `mcp_ts_server.ts` | MCP (TypeScript) | MCP protocol handshake + tool dispatch |
| `mcp_py_server.ts` | MCP (Python) | Spawns Python MCP process, proxies stdio over HTTP |

MCP templates expose MCP wire protocol rather than `/invoke`/`/stream`. `mcp_py_server.ts` is a thin Node.js HTTP adapter that spawns the developer's Python MCP process and proxies stdio transport over HTTP — Python MCP servers go through the same `agentbreeder deploy` pipeline.

#### 9. `@agentbreeder/aps-client` npm package

Location: `engine/sidecar/client/ts/`

```typescript
export class APSClient {
  constructor(opts?: { url?: string; apiKey?: string })
  // reads AGENTBREEDER_URL + AGENTBREEDER_API_KEY from env by default

  rag:    { search(query: string, opts: RagSearchOpts): Promise<RagChunk[]> }
  memory: { load(threadId: string): Promise<Message[]>
            save(threadId: string, messages: Message[]): Promise<void> }
  cost:   { record(e: CostEvent): void }   // fire-and-forget, never throws
  trace:  { span(e: SpanEvent): void }     // fire-and-forget, never throws
  a2a:    { call(agentName: string, input: unknown): Promise<unknown> }
  tools:  { execute(name: string, input: unknown): Promise<unknown> }
  config: { get(): Promise<AgentRuntimeConfig> }
}
```

Behaviors:
- `cost.record()` and `trace.span()` are `void` + `.catch(() => {})` — never throw, never block
- Blocking calls use exponential backoff: 3 retries on 5xx, 500ms base delay
- Zero runtime dependencies beyond `node-fetch` (Node 18 fetch polyfill)
- Full TypeScript types for all request/response shapes

All 8 templates import it identically:
```typescript
import { APSClient } from '@agentbreeder/aps-client'
const aps = new APSClient()
```

#### 10. `engine/schema/mcp-server.schema.json` (new file)

Separate schema for `mcp-server.yaml`. Required fields: `name`, `version`, `type` (fixed `mcp-server`), `runtime`, `tools`. Shares `RuntimeConfig` shape with `agent.schema.json`. Adds `transport` enum (`http` | `stdio`, default `http`).

#### 11. `cli/commands/init_cmd.py` — new flags

```bash
agentbreeder init --language node --framework vercel-ai --name my-agent
agentbreeder init --type mcp-server --language node --name my-tools
agentbreeder init --type mcp-server --language python --name my-tools
```

When `--language node`: framework picker shows the 6 Node frameworks instead of Python list.
When `--type mcp-server`: framework picker shows only `mcp-ts` / `mcp-py`.

Scaffolded output for a Node agent:
```
my-agent/
  agent.ts        # developer writes only this
  agent.yaml      # pre-filled with runtime: block
  package.json    # @agentbreeder/aps-client + framework SDK
  tsconfig.json
  .gitignore
```

`Dockerfile` and `server.ts` are NOT scaffolded — platform-managed, generated at deploy time.

#### PR 3 Tests

Unit:
- `tests/unit/test_node_runtime.py` — `NodeRuntimeFamily.build()` returns valid `ContainerImage` with correct `package.json` and `Dockerfile` for each of the 8 templates; unknown framework falls back to `custom` with warning
- `engine/sidecar/client/ts/src/__tests__/aps_client.test.ts` — `cost.record()` and `trace.span()` do not throw on network failure; `rag.search()` retries 3× on 5xx before raising; constructor reads env vars by default

Integration:
- `tests/integration/test_polyglot_deploy.py` — `agentbreeder deploy --target local` with Vercel AI `agent.ts`; verify container starts, `/health` returns 200, `AGENTBREEDER_URL` is in container env
- `tests/integration/test_mcp_deploy.py` — `agentbreeder deploy --target local` with MCP TypeScript server; verify tool callable via MCP protocol

Deferred: cross-language A2A E2E test (TS agent → Python agent) — follow-up issue after both PRs merge.

---

## File Inventory

### PR 2 — new files
```
engine/runtimes/registry.py
engine/runtimes/python.py
```

### PR 2 — changed files
```
engine/config_parser.py          +35 lines  (RuntimeConfig, AgentType, AgentConfig fields)
engine/schema/agent.schema.json             (runtime block, type enum, oneOf)
engine/builder.py                +5 lines   (get_runtime_from_config dispatch)
engine/deployers/base.py         +10 lines  (get_aps_env_vars)
engine/deployers/docker_compose.py          (inject AGENTBREEDER_URL)
engine/deployers/aws_ecs.py                 (inject AGENTBREEDER_URL)
engine/deployers/gcp_cloudrun.py            (inject AGENTBREEDER_URL)
api/config.py                               (AGENTBREEDER_URL, AGENTBREEDER_API_KEY settings)
tests/unit/test_config_parser.py            (RuntimeConfig tests)
tests/unit/test_deployer_base.py            (get_aps_env_vars tests)
```

### PR 3 — new files
```
engine/runtimes/node.py
engine/runtimes/templates/node/_shared_loader.ts
engine/runtimes/templates/node/vercel_ai_server.ts
engine/runtimes/templates/node/mastra_server.ts
engine/runtimes/templates/node/langchain_js_server.ts
engine/runtimes/templates/node/openai_agents_ts_server.ts
engine/runtimes/templates/node/deepagent_server.ts
engine/runtimes/templates/node/custom_node_server.ts
engine/runtimes/templates/node/mcp_ts_server.ts
engine/runtimes/templates/node/mcp_py_server.ts
engine/sidecar/client/ts/package.json
engine/sidecar/client/ts/src/index.ts
engine/sidecar/client/ts/src/__tests__/aps_client.test.ts
engine/schema/mcp-server.schema.json
tests/unit/test_node_runtime.py
tests/integration/test_polyglot_deploy.py
tests/integration/test_mcp_deploy.py
```

### PR 3 — changed files
```
cli/commands/init_cmd.py         (--language, --type flags + Node scaffold templates)
```

---

## Out of Scope (Phase 2)

- Rust runtime family (`RustRuntimeFamily`, `rig_server.rs`, `custom_rust_server.rs`)
- Go runtime family (`GoRuntimeFamily`, `langchaingo_server.go`, `custom_go_server.go`)
- `agentbreeder init --language rust`, `--language go`
- APS client crates/modules for Rust and Go
- MCP templates for Rust and Go
- Env var injection in `kubernetes.py`, `azure_container_apps.py`, `aws_app_runner.py`
- Cross-language A2A E2E test
