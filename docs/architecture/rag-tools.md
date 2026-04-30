# RAG Tools — Universal Search / Store / Query Tools

> **Status:** Design (not yet implemented). Tracked under epic `#TBD-D` (linked once filed).
> **Author:** AgentBreeder team, 2026-04-30.
> **Related design:** Sits on top of the RAG platform (`docs/architecture/rag-platform.md`, PR #249) and the AgentOps lifecycle (`docs/architecture/agentops-lifecycle.md`, PR #243).

---

## 1. The problem

Once Epic B (multi-store) and Epic C (lifecycle) land, the platform can index data into 8 vector stores + 4 graph stores across multiple environments. But there's still a gap: **how does an agent actually use it?**

Today the answer is "every framework's authors will hand-roll their own search helpers." That's how every other agent platform got into the same mess:

- LangChain has its own `VectorStoreRetriever` interface
- LlamaIndex has its own `BaseRetriever`
- CrewAI has its own RAG wrapper
- Custom Python agents roll their own `chromadb.query()` calls
- TypeScript / Mastra agents roll their own again
- Go / Kotlin / Rust / C# agents (when those SDKs land via #188-#190) would each need yet another implementation

Without a unified tool surface:
1. Same `rag.yaml` index exhibits different query semantics depending on which framework asks
2. Authorization (who can read/write which index) is enforced inconsistently or not at all
3. Per-env switching (Chroma local → pgvector prod) is hand-wired by each framework's wrapper
4. Hybrid search (vector + graph + lexical fusion) requires every framework to re-implement RRF
5. Citations are inconsistent — some frameworks return source paths, others don't
6. Polyglot story breaks: a Kotlin agent can't easily call the same RAG that a Python agent built

## 2. Goals and non-goals

### Goals
- A **single canonical implementation** of 8 RAG tools (`search`, `query`, `neighborhood`, `cypher`, `upsert`, `delete`, `list_indexes`, `stats`).
- Distribution as an **MCP server** so every framework that speaks MCP (Claude SDK, OpenAI Agents, LangGraph, CrewAI, Google ADK, Mastra, custom) gets all 8 tools for free.
- **Thin SDK wrappers** in Python / TypeScript / Go (and future Kotlin / Rust / C# from #188-#190) that all delegate to the MCP server — no language has its own implementation of the search math.
- **Hybrid search as a first-class tool** — `rag.search` does vector + graph + BM25 + RRF fusion server-side; agents just call one tool.
- **Mandatory citations** in every result — every chunk carries `source_doc_id`, `source_path`, `score`, `metadata`.
- **ResourcePermission ACL** at the index level — `read` for search/query/neighborhood/cypher/stats, `write` for upsert/delete.
- **Per-env resolution** — same tool call works in dev (Chroma + Neo4j) and prod (pgvector + Aura) without code change.
- **No direct cloud creds in agent code** — the MCP server uses the env's service principal (#248).

### Non-goals (this doc)
- Embedding the search-strategy logic on the agent side. Strategies (HyDE, multi-query rewriting, agentic retrieval) are agent code; tools just expose the substrate.
- Custom ranking / re-ranking models. The ranker is configurable in `rag.yaml`'s `search_strategy` block but the tool surface is fixed.
- Streaming search results. Returns are batched (default `k=5`); streaming is a v3 concern.
- Cross-index queries. A single `rag.search` call hits one index. Joining across indexes is agent-side composition.

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                         AGENT (any framework, any language)                       │
│                                                                                    │
│   Python agent              TypeScript agent           Go / Kotlin / Rust agent   │
│   ┌──────────────┐          ┌─────────────┐           ┌──────────────────────┐  │
│   │ from agenthub│          │ import {rag}│           │ rag.Search(ctx, ...) │  │
│   │ .rag import  │          │ from '@ab'  │           │                      │  │
│   │ search       │          │             │           │                      │  │
│   └──────┬───────┘          └──────┬──────┘           └──────────┬───────────┘  │
│          │                          │                              │                │
│          │     OR direct MCP call   │                              │                │
│          │                          │                              │                │
│          └──────────────┬───────────┴──────────────┬───────────────┘                │
│                         │                          │                                │
└─────────────────────────┼──────────────────────────┼────────────────────────────────┘
                          │                          │
                  MCP (stdio or HTTP/SSE)            │
                          │                          │
                          ▼                          │
┌──────────────────────────────────────────────────────────────────────────────────┐
│                  RAG MCP SERVER  (engine/tools/standard/rag_mcp/)                 │
│                                                                                    │
│   ┌──────────────────────────────────────────────────────────────────────────┐  │
│   │  ACL CHECK  →  resolve index from rag.yaml in current env  →  fan out    │  │
│   └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                                    │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │
│   │ VectorStore  │    │ GraphStore   │    │ LexicalIndex │    │ RRF Fuser    │ │
│   │ (Chroma /    │    │ (Neo4j /     │    │ (BM25 over   │    │              │ │
│   │  pgvector /  │    │  Aura /      │    │ chunk text;  │    │              │ │
│   │  Pinecone /  │    │  Neptune)    │    │ in vector    │    │              │ │
│   │  …)          │    │              │    │ store's full │    │              │ │
│   │              │    │              │    │ text index)  │    │              │ │
│   └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘ │
│              │                  │                  │                  │           │
│              └──────────────────┴──────────────────┴──────────────────┘           │
│                                          │                                          │
│                                          ▼                                          │
│                       Per-env service principal (epic #248)                        │
└──────────────────────────────────────────────────────────────────────────────────┘
```

The MCP server is the only place that:
- Reads the index's per-env `vector_store` / `graph_store` from `rag.yaml`
- Holds the cloud-specific connection pools
- Implements RRF fusion across the three retrieval layers
- Enforces ResourcePermission ACL
- Emits audit events for every search / upsert / delete

Every other surface (Python SDK, TypeScript SDK, Go SDK, framework MCP-tool definitions) is a **thin wrapper that calls this server**.

---

## 4. The 8 tools

### `rag.search`

Hybrid semantic search. Default behavior fuses vector + graph + BM25 via reciprocal-rank fusion.

**Input:**
```json
{
  "index": "kb/support-docs",
  "query": "How do I refund an order?",
  "k": 5,
  "filters": { "visibility": "customer-facing", "language": "en" },
  "strategy": "hybrid",
  "rerank": true
}
```

`strategy` ∈ `{"vector", "hybrid", "graph_expanded"}`. Default `"hybrid"`.

**Output:**
```json
{
  "results": [
    {
      "chunk_id": "doc-12345#3",
      "text": "To refund an order, navigate to ...",
      "score": 0.87,
      "rank": 1,
      "source_doc_id": "doc-12345",
      "source_path": "gdrive://folder/abc/refund-policy-v3.pdf",
      "source_kind": "gdrive",
      "metadata": { "visibility": "customer-facing", "page": 3 }
    }
  ],
  "trace_id": "rag-search-7c8d9..."
}
```

ACL: requires `read` on `rag_index/<index_name>`. Emits `rag.search` audit event with `index`, `actor`, `query_hash`, `result_count`.

### `rag.query`

Raw vector similarity. No graph, no BM25. For advanced retrieval-strategy code that wants control over the fusion.

**Input:** `{ index, query, k, filters }` — `query` can be a string (server embeds it) OR a 1024-dim float array (caller pre-embedded).
**Output:** same as `search` but only vector-store results.

### `rag.neighborhood`

Graph traversal from a known entity id, N hops out.

**Input:** `{ index, entity_id, hops, edge_types? }`.
**Output:** `{ nodes: [...], edges: [...], trace_id }` — subgraph with entity metadata + edge labels.

ACL: `read`. Returns 404 if `entity_id` is not in the graph.

### `rag.cypher`

Raw Cypher (Neo4j) or openCypher (Neptune) — escape hatch for power users. Server validates the query is read-only (no `CREATE` / `DELETE` / `SET`) before forwarding.

**Input:** `{ index, query, params }`.
**Output:** `{ rows: [...], trace_id }`.

ACL: `read`. Read-only enforcement is server-side via Cypher AST inspection. Write Cypher requires `write` and currently raises `NotSupported` (use `rag.upsert` instead — keeps the platform consistent).

### `rag.upsert`

Insert or update documents in the index. Idempotent: respects `source_signatures` so re-upserting an unchanged document is a no-op.

**Input:**
```json
{
  "index": "kb/support-docs",
  "documents": [
    {
      "id": "manual-faq-001",
      "content": "...",
      "source_path": "manual://product-faq.md",
      "metadata": { "visibility": "customer-facing" }
    }
  ]
}
```

**Output:** `{ inserted: int, updated: int, skipped: int, trace_id }`.

ACL: `write` on `rag_index/<index_name>`. Emits `rag.upsert` audit event with per-doc counts.

### `rag.delete`

Remove documents by id. No-op for ids not present.

**Input:** `{ index, doc_ids: [...] }`.
**Output:** `{ deleted: int, not_found: int, trace_id }`.

ACL: `write`. Emits `rag.delete` audit event.

### `rag.list_indexes`

Discovery. Returns indexes the calling agent has read access to.

**Input:** `{ filter? : { team?, tag? } }`.
**Output:**
```json
{
  "indexes": [
    {
      "name": "kb/support-docs",
      "version": "1.4.0",
      "env": "prod",
      "total_documents": 12_400,
      "last_indexed_at": "2026-04-30T03:00:00Z",
      "tags": ["customer-facing", "english"]
    }
  ]
}
```

ACL-filtered. No event emitted (read-only metadata).

### `rag.stats`

Index health. Used by the `/agentops` dashboard pages too.

**Input:** `{ index }`.
**Output:** `{ name, version, env, total_documents, index_size_bytes, last_indexed_at, sources: [...], trace_id }`.

ACL: `read`. Read-only; no audit event.

---

## 5. Authorization

Every tool call carries the calling agent's identity, established by the sidecar's `AGENT_AUTH_TOKEN` flow (already shipped, #176). The MCP server resolves the token to:

- `actor_email`
- `team`
- `agent_name` + `agent_version`

For each call, the server checks `ResourcePermission` rows on `rag_index/<name>`:

| Tool | Required | Soft-fail behavior |
|---|---|---|
| `rag.search`, `rag.query`, `rag.neighborhood`, `rag.cypher`, `rag.stats` | `read` | 403 + audit `rag.access.denied` |
| `rag.upsert`, `rag.delete` | `write` | 403 + audit `rag.access.denied` |
| `rag.list_indexes` | (filter to `read`-allowed only) | empty list, no error |

If no `ResourcePermission` row exists for the `(actor, rag_index)` pair, the index's `access.visibility` field decides:
- `public` → allow read; deny write
- `team` → allow read+write to the index's owning team; deny others
- `private` → deny all unless an explicit ALLOW row exists

This mirrors the agent ACL pattern from migration 015 — same primitives, no new schema needed.

---

## 6. Per-env resolution

The MCP server reads `AGENTBREEDER_ENV` (default `dev`) at startup. For each tool call:

1. Look up the index's `rag.yaml` snapshot in the current env's registry (#247)
2. Open / reuse a connection pool to that env's `vector_store` and `graph_store`
3. Execute the tool against the connection
4. Connection pool is per-env, per-index — bounded size, idle-closed after 5min

The same call (`rag.search("kb/support-docs", "...")`) thus:
- In dev → hits ChromaDB + Neo4j on docker-compose
- In staging → hits pgvector RDS + Neo4j Aura
- In prod → hits Pinecone + Neptune

Agent code is identical. Per-env service principals (#248) handle the underlying cloud auth.

---

## 7. Distribution

### 7.1 MCP server (universal)
- Path: `engine/tools/standard/rag_mcp/server.py`
- Wire formats: stdio (for sidecar embedding) and HTTP/SSE (for cross-process callers)
- Packaged as a Docker image: `agentbreeder/agentbreeder-rag-mcp:<version>`
- Auto-installed in the sidecar when any agent declares a `rag_index` reference
- The MCP tool manifest is versioned alongside the server

### 7.2 Python SDK helper
- Path: `sdk/python/agenthub/rag.py`
- API:
  ```python
  from agenthub.rag import search, upsert, delete, list_indexes, neighborhood, stats
  
  results = await search("kb/support-docs", "How do I refund?", k=5)
  for hit in results:
      print(f"{hit.source_path}: {hit.text[:100]}")
  ```
- Internally: opens an MCP client to the local sidecar's stdio port
- Shipped with the existing `agentbreeder-sdk` PyPI package

### 7.3 TypeScript SDK helper
- Path: `sdk/typescript/src/rag.ts`
- Same shape as Python; covered by SDK parity issue #200
- Exported from `@agentbreeder/sdk`

### 7.4 Go SDK helper
- Path: `sdk/go/agentbreeder/rag/`
- Same shape; uses the existing Go MCP client

### 7.5 Future polyglot SDKs
- Kotlin (#188), Rust (#189), C# (#190) inherit automatically when those SDKs land — they get a thin `rag.*` wrapper that calls the MCP server. No re-implementation of search semantics.

### 7.6 Registered tools in the registry
Eight `tool.yaml` entries seeded into `examples/seed/tools/`:
- `tools/rag-search`, `tools/rag-query`, `tools/rag-neighborhood`, `tools/rag-cypher`
- `tools/rag-upsert`, `tools/rag-delete`
- `tools/rag-list-indexes`, `tools/rag-stats`

Each declares the MCP server as its provider. Dashboard `Tools` picker shows them as recommended starter tools. Agents reference them via:

```yaml
tools:
  - ref: tools/rag-search
  - ref: tools/rag-upsert
```

---

## 8. Migration plan

### Phase 1 — MCP server skeleton + 4 read tools
- Server scaffolding (stdio + HTTP/SSE)
- Connection pool management per (env, index)
- ACL middleware
- `rag.search` (hybrid + RRF)
- `rag.query` (vector-only)
- `rag.list_indexes`
- `rag.stats`

### Phase 2 — Write + graph tools
- `rag.upsert` (with `source_signatures` integration)
- `rag.delete`
- `rag.neighborhood`
- `rag.cypher` (read-only enforcement via AST)

### Phase 3 — SDK wrappers
- Python (`agenthub.rag`)
- TypeScript (`@agentbreeder/sdk` / `rag`)
- Go (`sdk/go/agentbreeder/rag/`)

### Phase 4 — Registry seeding + dashboard integration
- 8 `tool.yaml` entries seeded via the first-boot seeder (#180 already shipped)
- Dashboard `Tools` picker highlights them
- Dashboard `/rag/builder` "Test query" button uses the live MCP server

Each phase is independently shippable. Phase 1 is enough for any Python agent to start using the server.

---

## 9. Open questions

1. **Ranking model.** Default RRF is solid for hybrid; do we ship a cross-encoder re-ranker option (e.g. Cohere Rerank, BGE)? Probably yes as a v2 feature; defer.
2. **Embedding-model auto-detection.** When the caller passes a string `query`, the MCP server needs to know which embedder to use. Read it from the index's `rag.yaml.embedder` block — already in scope from epic #252.
3. **Per-call cost attribution.** Each `rag.search` does an embed call. Costs should attribute to the calling agent's team, like every other LLM call. Reuse `cost_events` table.
4. **Tool versioning.** Tools registered in the registry are versioned. When the MCP server's tool surface changes (e.g. new param), do we bump the tool version and let agents pin? Proposal: yes, semver — `tools/rag-search@1.x` is stable, breaking changes mint `2.x`.
5. **Sandbox tool-runner integration.** The existing `engine/tool_runner.py` has a sandbox path. RAG tools should run *inside* the sandbox to inherit OTEL tracing, retries, and cost metering. Confirm via integration test.
6. **MCP transport selection.** Within a single sidecar, stdio is fine. Cross-pod (e.g. centralized RAG MCP serving N agents) needs HTTP/SSE with auth. Both modes ship; the default depends on `AGENTBREEDER_INSTALL_MODE`.

---

## 10. What exists today that we keep

| Primitive | Status | Reuse |
|---|---|---|
| Sidecar (Track J) — MCP passthrough at `localhost:9090/mcp/<server>` | Shipped | RAG MCP server slots into this passthrough; no new networking |
| Tool registry (Track Q) — first-class tool yaml, ACL on resource | Shipped | Add 8 tool entries; ACL re-applies |
| `AGENT_AUTH_TOKEN` flow (#176) | Shipped | Identity carried into every tool call |
| `cost_events` table | Shipped | RAG embed costs attribute to caller's team |
| `audit_log` | Shipped | New events: `rag.search`, `rag.upsert`, `rag.delete`, `rag.access.denied` |
| Per-env service principals (#248, designed) | In flight | MCP server's connection pools assume the env's role |
| `source_signatures` table (#252) | Designed | `rag.upsert` updates these; idempotency derives from them |
| RAG MCP servers and stdio transport (#201) | Designed | Tools ship with stdio support out of the box |
| Polyglot SDKs (#200, #188-#190) | Designed | Each gets a thin `rag.*` wrapper for free |

This epic adds **one server + N thin wrappers**. Everything else is reuse.

---

## 11. Out of scope

- Streaming results (deferred to v3)
- Cross-index joins (compose in agent code instead)
- Custom embedders inside tools (use the index's `rag.yaml.embedder`)
- Tool-level rate-limiting (use the platform's existing rate-limit infra)
- Dashboard "Try it" UI for tools (separate UX work; basic test query lives in `/rag/builder`)

---

## Appendix A — Wire shapes

The MCP tool manifest (excerpt):

```json
{
  "tools": [
    {
      "name": "rag.search",
      "description": "Hybrid semantic search over a RAG index. Returns top-K chunks with citations.",
      "inputSchema": {
        "type": "object",
        "required": ["index", "query"],
        "properties": {
          "index":    { "type": "string", "description": "Registry ref like 'kb/support-docs'" },
          "query":    { "type": "string" },
          "k":        { "type": "integer", "default": 5, "minimum": 1, "maximum": 50 },
          "filters":  { "type": "object", "additionalProperties": true },
          "strategy": { "type": "string", "enum": ["vector", "hybrid", "graph_expanded"], "default": "hybrid" },
          "rerank":   { "type": "boolean", "default": false }
        }
      }
    }
  ]
}
```

Other tools follow the same shape. Output schemas are documented in §4.
