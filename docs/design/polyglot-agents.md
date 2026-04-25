# Polyglot Agents — Scenario 1: TypeScript & Local Dev

**Date:** 2026-04-25
**Status:** Draft
**Replaces:** `2026-04-25-polyglot-agents-design.md` (original spec, superseded)
**Scope:** TypeScript agent runtime, MCP server authoring in TypeScript/Python, `agentbreeder init --language node`, local docker-compose dev experience
**GitHub Issue:** TBD
**Phase:** 1 of 3

---

## Problem Statement

Engineers building agents in TypeScript have no first-class support in AgentBreeder today. They must maintain a custom server wrapper and lose automatic RAG injection, memory management, tool execution, A2A, tracing, and cost attribution that Python agents get for free.

This spec covers making the local developer experience work end-to-end for TypeScript agents — from `agentbreeder init` on a laptop through `docker compose up` to a working, fully-governed agent.

---

## Architecture Principle (revised from original spec)

**Central Python API is the single source of truth for all platform concerns.**

```
Developer's laptop (docker compose up)
┌───────────────────────────────────────────────────────┐
│                                                       │
│  ┌──────────────────┐    ┌───────────────────────┐   │
│  │ AgentBreeder API │    │ AgentBreeder Dashboard │   │
│  │ (Python — ALL    │    │ (React UI :3000)       │   │
│  │ platform logic)  │    └───────────────────────┘   │
│  │ :8000            │                                 │
│  └──────────────────┘                                 │
│           ▲                                           │
│  ┌────────┴──────────────────────────────────────┐   │
│  │  TypeScript Agent Container                   │   │
│  │                                               │   │
│  │  server template (vercel-ai / mastra / etc)   │   │
│  │  @agentbreeder/aps-client ──► http://agentbreeder-api:8000/api/v1/...│
│  │  developer's agent.ts                         │   │
│  └───────────────────────────────────────────────┘   │
│                                                       │
│  postgres  redis  (same compose network)              │
└───────────────────────────────────────────────────────┘
                    ↓
              git push → GitHub Actions → agentbreeder deploy (cloud)
```

**No new sidecar servers.** The `@agentbreeder/aps-client` npm package is a thin HTTP wrapper — it calls the existing AgentBreeder API. No TypeScript re-implementation of platform logic.

---

## What Changes

### 1. `agent.yaml` — new `runtime:` block

```yaml
# Existing Python agents — unchanged, 100% backward-compatible
framework: langgraph

# New TypeScript agents
runtime:
  language: node         # Enum: python | node | rust | go
  framework: vercel-ai   # Open string — validated against plugin registry
  version: "20"          # Node.js LTS version
  entrypoint: agent.ts   # Optional. Default: agent.ts
```

`language` is a closed enum (drives base image, compiler, package manager).
`framework` is an open string — adding a new TS framework requires only a new template file.

### 2. `engine/config_parser.py` — `RuntimeConfig` model

```python
class RuntimeConfig(BaseModel):
    language: Literal["python", "node", "rust", "go"]
    framework: str
    version: str | None = None
    entrypoint: str | None = None

class AgentType(enum.StrEnum):
    agent = "agent"
    mcp_server = "mcp-server"

class AgentConfig(BaseModel):
    # existing fields unchanged ...
    type: AgentType = AgentType.agent          # new — default "agent"
    framework: FrameworkType | None = None     # now optional (was required)
    runtime: RuntimeConfig | None = None       # new — takes precedence over framework
```

Validation rule: exactly one of `framework` or `runtime` must be set.

### 3. `engine/schema/agent.schema.json` — `runtime:` block

Add `runtime` as an optional object property alongside the existing `framework` string. Add `type` property with enum `["agent", "mcp-server"]`.

### 4. `engine/runtimes/registry.py` — language registry

```python
LANGUAGE_REGISTRY: dict[str, type[LanguageRuntimeFamily]] = {
    "python": PythonRuntimeFamily,
    "node":   NodeRuntimeFamily,
    # "rust": RustRuntimeFamily,   # Phase 2
    # "go":   GoRuntimeFamily,     # Phase 2
}

def get_runtime_from_config(config: AgentConfig) -> RuntimeBuilder:
    if config.runtime:
        family = LANGUAGE_REGISTRY.get(config.runtime.language)
        if not family:
            raise UnsupportedLanguageError(config.runtime.language)
        return family()
    # Legacy path — existing framework: field
    return PythonRuntimeFamily.from_framework(config.framework)
```

### 5. `engine/runtimes/python.py` — `PythonRuntimeFamily`

Reorganizes the existing per-framework runtime builders (`LangGraphRuntime`, `CrewAIRuntime`, etc.) under a single `PythonRuntimeFamily` class that dispatches by framework name. No behavior changes — pure reorganization.

### 6. `engine/runtimes/node.py` — `NodeRuntimeFamily`

Dispatches to one of six TypeScript framework templates based on `config.runtime.framework`. Falls back to `CustomNodeTemplate` for unlisted frameworks (warn, don't fail).

```python
class NodeRuntimeFamily(LanguageRuntimeFamily):
    TEMPLATES: dict[str, type[NodeTemplate]] = {
        "vercel-ai":          VercelAITemplate,
        "mastra":             MastraTemplate,
        "langchain-js":       LangChainJSTemplate,
        "openai-agents-ts":   OpenAIAgentsTSTemplate,
        "deepagent":          DeepAgentTemplate,
        "mcp-ts":             MCPTypeScriptTemplate,
        "mcp-py":             MCPPythonTemplate,
        "custom":             CustomNodeTemplate,
    }
```

### 7. TypeScript framework templates

Location: `engine/runtimes/templates/node/`

Each template is a TypeScript file (~80-120 lines) providing:
- Express/Hono server with `GET /health`, `POST /invoke`, `POST /stream`
- `GET /.well-known/agent.json` (A2A agent card)
- APS client wiring (RAG, memory, tools, cost, tracing)
- Developer's file imported at top

**Template files:**
```
engine/runtimes/templates/node/
  vercel_ai_server.ts
  mastra_server.ts
  langchain_js_server.ts
  openai_agents_ts_server.ts
  deepagent_server.ts
  custom_node_server.ts
  mcp_ts_server.ts         # MCP server template (HTTP transport)
  _shared_loader.ts        # APS wiring + health check shared across all templates
```

**Developer's file (`agent.ts`) — the only file they write:**

```typescript
// agent.ts — Vercel AI example
import { openai } from '@ai-sdk/openai'
export const model = openai('gpt-4o')
export const systemPrompt = `You are a helpful assistant.`
export const tools = {}  // optional additional tools beyond APS-injected tools
```

```typescript
// tools.ts — MCP server example
export async function search_web({ query }: { query: string }) {
  return fetch(`https://api.search.example.com?q=${encodeURIComponent(query)}`).then(r => r.json())
}
```

### 8. `@agentbreeder/aps-client` npm package

Location: `engine/sidecar/client/ts/`

A thin, typed HTTP wrapper (~200 lines). **Zero business logic** — only typed HTTP calls to `$AGENTBREEDER_URL`.

```typescript
export class APSClient {
  constructor(private opts: { url: string; apiKey: string }) {}

  rag = {
    search: (query: string, opts: { indexIds: string[]; topK?: number }) =>
      this.get<RagResult[]>('/api/v1/rag/search', { query, ...opts }),
  }

  memory = {
    load:  (threadId: string) =>
      this.get<Message[]>(`/api/v1/memory/conversations/${threadId}`),
    save:  (threadId: string, messages: Message[]) =>
      this.post('/api/v1/memory/messages', { thread_id: threadId, messages }),
  }

  cost = {
    // fire-and-forget — never awaited, never blocks agent
    record: (e: CostEvent) => void this.post('/api/v1/costs/record', e).catch(() => {}),
  }

  trace = {
    span: (e: SpanEvent) => void this.post('/api/v1/tracing/spans', e).catch(() => {}),
  }

  a2a = {
    call: (agentName: string, input: unknown) =>
      this.post(`/api/v1/a2a/agents/${agentName}/invoke`, { input }),
  }
}
```

**Key behaviors:**
- `cost.record()` and `trace.span()` are fire-and-forget (`void` return, `.catch(() => {})` — never throw, never block)
- `rag.search()` and `memory.load()` are awaited — agent needs the result
- Auth: `Authorization: Bearer $AGENTBREEDER_API_KEY` on every request
- Retry: exponential backoff on 5xx, max 3 retries for blocking calls only

### 9. `engine/deployers/base.py` — APS environment injection

Each deployer must inject `AGENTBREEDER_URL` and `AGENTBREEDER_API_KEY` into every agent container's environment at deploy time. The base class provides a helper:

```python
def get_aps_env_vars(self, config: AgentConfig) -> list[dict]:
    return [
        {"name": "AGENTBREEDER_URL",     "value": settings.AGENTBREEDER_URL},
        {"name": "AGENTBREEDER_API_KEY", "value": settings.AGENTBREEDER_API_KEY},
    ]
```

For local docker-compose: `AGENTBREEDER_URL=http://agentbreeder-api:8000` (same compose network).
For cloud: `AGENTBREEDER_URL` is the production admin console URL.

### 10. `cli/commands/init_cmd.py` — `--language` and `--type` flags

```bash
# TypeScript agent
agentbreeder init --language node --framework vercel-ai --name my-agent

# MCP server in TypeScript
agentbreeder init --type mcp-server --language node --name my-tools

# MCP server in Python (existing, now explicit)
agentbreeder init --type mcp-server --language python --name my-tools
```

The `init` command scaffolds:
- `agent.ts` (or `tools.ts` for MCP) — developer's file only
- `agent.yaml` pre-filled with `runtime:` block
- `package.json` with correct deps (`@agentbreeder/aps-client`, framework SDK)
- `tsconfig.json`
- `.gitignore`

**Not scaffolded:** Dockerfile, server wrapper — those are platform-managed.

### 11. MCP Server support (`type: mcp-server`)

```yaml
# mcp-server.yaml
name: my-search-tools
version: 1.0.0
type: mcp-server
runtime:
  language: node
  framework: mcp-ts
  version: "20"
transport: http          # http | stdio
tools:
  - name: search_web
    description: "Search the web"
    schema:
      type: object
      properties:
        query: { type: string }
      required: [query]
```

Developer writes only `tools.ts`. The `mcp-ts` template wraps it into a fully compliant MCP server with protocol handshake, tool dispatch, and transport. Once deployed, any agent can reference it: `tools: [{ ref: tools/my-search-tools }]`.

New schema file: `engine/schema/mcp-server.schema.json`

---

## `engine/builder.py` Changes (minimal)

One new function, zero changes to existing code:

```python
def get_runtime(config: AgentConfig) -> RuntimeBuilder:
    """Route to the correct runtime builder."""
    if config.runtime:
        from engine.runtimes.registry import LANGUAGE_REGISTRY, get_runtime_from_config
        return get_runtime_from_config(config)
    # existing path
    return _RUNTIMES[config.framework]()
```

All existing `if framework == "langgraph"` paths are untouched.

---

## Local Dev `docker-compose.yml` Changes

Add `AGENTBREEDER_URL` and `AGENTBREEDER_API_KEY` to the environment of any agent service spun up locally. This is handled automatically by `agentbreeder deploy --target local` — the deployer injects these via `get_aps_env_vars()`.

---

## Testing Strategy

- **Unit:** `NodeRuntimeFamily.build()` returns a valid `ContainerImage` with correct `package.json` and Dockerfile for each template
- **Unit:** `RuntimeConfig` validation rejects unknown languages, accepts open framework strings
- **Unit:** `APSClient` fires cost/trace as fire-and-forget and does not throw on failure
- **Integration:** `agentbreeder deploy --target local` with a TypeScript Vercel AI agent; verify it starts and responds at `/health`
- **Integration:** `agentbreeder deploy --target local` with an MCP server; verify tool is callable
- **E2E:** TypeScript agent calls a Python agent via A2A through the local API

---

## File Inventory

### New files
```
engine/runtimes/registry.py
engine/runtimes/python.py
engine/runtimes/node.py
engine/runtimes/templates/node/vercel_ai_server.ts
engine/runtimes/templates/node/mastra_server.ts
engine/runtimes/templates/node/langchain_js_server.ts
engine/runtimes/templates/node/openai_agents_ts_server.ts
engine/runtimes/templates/node/deepagent_server.ts
engine/runtimes/templates/node/custom_node_server.ts
engine/runtimes/templates/node/mcp_ts_server.ts
engine/runtimes/templates/node/_shared_loader.ts
engine/sidecar/client/ts/package.json
engine/sidecar/client/ts/src/index.ts
engine/schema/mcp-server.schema.json
```

### Changed files
```
engine/builder.py          +15 lines
engine/config_parser.py    +30 lines
engine/schema/agent.schema.json
engine/deployers/base.py   +15 lines
engine/deployers/docker_compose.py   (inject AGENTBREEDER_URL)
engine/deployers/aws_ecs.py          (inject AGENTBREEDER_URL)
engine/deployers/gcp_cloudrun.py     (inject AGENTBREEDER_URL)
cli/commands/init_cmd.py
tests/unit/test_node_runtime.py      (new)
tests/integration/test_polyglot_deploy.py (new)
```

---

## Out of Scope (Scenario 2)

- Go and Rust runtime families
- Go/Rust APS clients
- `agentbreeder init --language go`, `--language rust`
- Production URL injection for admin console deployments
