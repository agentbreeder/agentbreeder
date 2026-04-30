# RAG Platform — Loaders, Stores, Pipeline, Lifecycle

> **Status:** Design (not yet implemented). Tracked under epics `#TBD-A`, `#TBD-B`, `#TBD-C` (linked once filed).
> **Author:** AgentBreeder team, 2026-04-30.
> **Audience:** Platform engineers extending AgentBreeder; data engineers building RAG indexes.
> **Related design:** Builds on `docs/architecture/agentops-lifecycle.md` — RAG artifacts inherit that lifecycle (git → approve → deploy → registry) rather than introducing a parallel governance pipeline.

---

## 1. The problem

AgentBreeder shipped strong RAG primitives in v2.0/v2.1:

- A `rag.yaml` schema (`engine/schema/rag.schema.json`) that defines knowledge bases
- An ingestion API (`/api/v1/rag/*`) for uploading docs
- ChromaDB as the default local vector store
- A graph-RAG track in flight (`feat/graphrag-113`, issue #113) adding Neo4j

But the platform stops short of a complete RAG offering. Specifically:

1. **Connector library is thin.** Today users mostly upload PDFs/text by hand. There is no first-class loader for Google Drive, Google Docs, Sheets, S3, GCS, Postgres, Snowflake, BigQuery, Delta Lake, Confluence, Notion, Slack, or images.
2. **Vector store is single-backend.** ChromaDB is the only option. There is no pluggable abstraction, so an org that wants pgvector / Pinecone / Weaviate / Qdrant / Vertex Vector Search has to fork the platform.
3. **Graph store is single-backend.** The in-flight #113 work adds Neo4j; there is no abstraction for AWS Neptune / ArangoDB / Neptune Analytics.
4. **Multimodal is missing.** Images, audio, video — none have a first-class loader or embedder.
5. **No reindex semantics.** A `rag.yaml` change today does not automatically reindex. There is no `on_merge`, `schedule`, `on_source_change`, or `debounce` policy.
6. **No env parity.** A `rag.yaml` that points at `chroma` for local dev and `pgvector` for prod requires hand-editing — there is no `environments:` block.
7. **No git lifecycle.** Editing a knowledge base via the dashboard saves to the workspace DB, not git. There is no PR review, no approval gate, no per-env promotion. The same gap that the AgentOps lifecycle epic (#244) closes for agents/prompts/tools/MCP/eval also exists for RAG.
8. **No source signatures.** Every reindex re-embeds every document. Incremental reindex is impossible because the platform doesn't track per-document content hashes.

The result: **users can build a RAG index, but they can't operate one in production with multiple data sources, multiple environments, multiple stores, and multiple reviewers.**

---

## 2. Goals and non-goals

### Goals
- A pluggable `VectorStore` abstraction backing 5+ vector databases on day one.
- A pluggable `GraphStore` abstraction (extends #113) backing 2+ graph databases.
- 12+ first-class document loaders covering Google Workspace, object storage, databases, lakehouse, collaboration platforms, web, and images.
- A declarative pipeline (`loader → chunker → embedder → stores`) configurable in `rag.yaml`.
- Full reuse of the AgentOps lifecycle (epic #244) — RAG indexes follow the same git → PR → approve → deploy → registry flow as agents.
- Per-environment store config so the same `rag.yaml` runs against ChromaDB locally and pgvector in prod.
- Incremental reindex via per-document content hashes (`source_signatures`).
- Reindex policies declared in YAML: `on_merge`, `schedule`, `on_source_change` with debounce.

### Non-goals (this doc)
- A bespoke vector database. We integrate existing ones.
- ML-driven query rewriting / agentic retrieval. RAG **builds** the index; query-time strategies belong in agent code or a separate retrieval-strategies doc.
- Cross-region replication. v2.x is single-region per env.
- Embedding model training. We orchestrate embedders; we don't train them.

---

## 3. The target architecture

```
                     ┌───────────────────────────────────────────────────────────┐
                     │                       rag.yaml (in git)                    │
                     │   sources / chunker / embedder / stores / environments     │
                     └───────────────────────────────────────────────────────────┘
                                              │ commit + PR
                                              ▼
                     ┌───────────────────────────────────────────────────────────┐
                     │             AgentOps lifecycle (epic #244)                 │
                     │   submit → review → approve → merge → promote per env     │
                     └───────────────────────────────────────────────────────────┘
                                              │ on_merge
                                              ▼
   ┌─────────────────────────────────────────────────────────────────────────────────────┐
   │                       Pipeline orchestrator (per env)                                 │
   │                                                                                       │
   │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
   │  │   LOADERS    │───▶│   CHUNKER    │───▶│   EMBEDDER   │───▶│   STORE WRITES   │  │
   │  │              │    │              │    │              │    │                  │  │
   │  │ gdrive       │    │ semantic     │    │ openai       │    │ ┌──────────────┐ │  │
   │  │ gdocs        │    │ fixed        │    │ cohere       │    │ │ VectorStore  │ │  │
   │  │ gsheets      │    │ sliding      │    │ vertex       │    │ │  (pgvector)  │ │  │
   │  │ s3 / gcs / az│    │ markdown     │    │ local-st     │    │ └──────────────┘ │  │
   │  │ postgres / … │    │ html         │    │              │    │ ┌──────────────┐ │  │
   │  │ delta_lake   │    │              │    │              │    │ │ GraphStore   │ │  │
   │  │ web          │    │              │    │              │    │ │   (Neo4j)    │ │  │
   │  │ image (OCR)  │    │              │    │              │    │ └──────────────┘ │  │
   │  │ confluence   │    │              │    │              │    │                  │  │
   │  │ notion       │    │              │    │              │    └──────────────────┘  │
   │  │ slack        │    │              │    │              │                          │
   │  └──────────────┘    └──────────────┘    └──────────────┘                          │
   │         │                                                                            │
   │         └──── source_signatures (per-document SHA-256) ─── incremental reindex      │
   └─────────────────────────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
                     ┌───────────────────────────────────────────────────────────┐
                     │            Per-env registry (epic #247)                    │
                     │   rag_indexes(env, name, version, git_ref, deployed_by,    │
                     │                last_indexed_at, total_documents, …)        │
                     └───────────────────────────────────────────────────────────┘
```

The orchestrator is plain asyncio — no Celery, no Airflow. Scheduled reindex piggybacks on the daily-cron infra from #199.

---

## 4. Component design

### 4.1 `rag.yaml` schema (extended)

```yaml
name: support-knowledge-base
version: 1.4.0
description: Customer support docs + order history + product images
team: customer-success
owner: alice@company.com

# 1. Sources — what to load
sources:
  - id: support-docs
    type: gdrive
    folder_id: "0AbC..."
    auth: secret://google-oauth-customer-success
    include: ["*.pdf", "*.docx", "*.gdoc"]
    exclude: ["*-draft.*"]
    metadata:                                 # propagated to every chunk
      visibility: customer-facing

  - id: order-history
    type: postgres
    connection: secret://prod-orders-readonly
    query: |
      SELECT id, summary, body, customer_id, updated_at
      FROM orders
      WHERE updated_at > :since
    incremental_column: updated_at            # for incremental reindex
    primary_key: id

  - id: product-images
    type: gcs
    bucket: product-photos
    prefix: catalog/
    multimodal:
      caption_model: gpt-4o
      embed_model: clip-vit-base-patch32

  - id: telemetry
    type: delta_lake
    catalog: prod
    table: telemetry.user_events
    incremental_column: event_ts

# 2. Chunker — how to split
chunker:
  strategy: semantic                          # fixed | sliding | markdown | html | semantic
  max_tokens: 512
  overlap_tokens: 64

# 3. Embedder — how to vectorize
embedder:
  model: text-embedding-3-large
  provider: openai                            # openai | cohere | vertex | local-st | bedrock
  batch_size: 64

# 4. Stores — where to write (per env)
environments:
  - name: dev
    vector_store:
      type: chroma
      persist_path: ./chroma-data
      collection: support-knowledge-base
    graph_store:
      type: neo4j
      uri: bolt://localhost:7687
      auth: secret://neo4j-dev
    deployer_service_account: null            # local

  - name: prod
    vector_store:
      type: pgvector
      dsn: secret://prod-pgvector-dsn
      table: rag_support_kb_vectors
    graph_store:
      type: neo4j_aura
      uri: secret://prod-aura-uri
      auth: secret://prod-aura-auth
    deployer_service_account: arn:aws:iam::222222222222:role/agentbreeder-prod

# 5. Reindex policy
reindex:
  on_merge: true                              # reindex when rag.yaml changes on `main`
  schedule: "0 3 * * *"                       # also nightly (uses #199 cron infra)
  on_source_change:
    enabled: true
    debounce: 5m

# 6. Access control
access:
  visibility: team
  allowed_callers:
    - team:customer-success
    - team:engineering
```

### 4.2 `VectorStore` abstraction (Epic B)

```python
class VectorStore(Protocol):
    @classmethod
    async def from_config(cls, config: dict[str, Any]) -> "VectorStore": ...
    async def upsert(self, docs: list[VectorDoc]) -> None: ...
    async def delete(self, doc_ids: list[str]) -> None: ...
    async def query(self, embedding: list[float], k: int, filters: dict[str, Any] | None = None) -> list[VectorHit]: ...
    async def count(self) -> int: ...
    async def stats(self) -> StoreStats: ...
    async def close(self) -> None: ...
```

Implementations land in `engine/rag/vector_stores/`:

| Backend | Module | Status | Notes |
|---|---|---|---|
| ChromaDB (local) | `chroma.py` | Exists; refactor | Default local |
| pgvector | `pgvector.py` | NEW | Postgres extension; works with RDS / AlloyDB / Cloud SQL |
| Pinecone | `pinecone.py` | NEW | Managed cloud-only |
| Weaviate | `weaviate.py` | NEW | Cloud or self-hosted |
| Qdrant | `qdrant.py` | NEW | Cloud or self-hosted |
| Vertex Vector Search | `vertex.py` | NEW | GCP managed |
| AWS OpenSearch | `opensearch.py` | NEW | AWS managed |
| Milvus | `milvus.py` | NEW (stretch) | Self-hosted; popular in research |

### 4.3 `GraphStore` abstraction (Epic B; extends #113)

```python
class GraphStore(Protocol):
    @classmethod
    async def from_config(cls, config: dict[str, Any]) -> "GraphStore": ...
    async def upsert_entities(self, entities: list[Entity]) -> None: ...
    async def upsert_relations(self, relations: list[Relation]) -> None: ...
    async def query_cypher(self, cypher: str, params: dict[str, Any]) -> list[dict]: ...
    async def query_neighborhood(self, entity_id: str, hops: int) -> SubGraph: ...
    async def close(self) -> None: ...
```

| Backend | Module | Status | Notes |
|---|---|---|---|
| Neo4j (Docker) | `neo4j.py` | In flight (#113) | Default local |
| Neo4j Aura | `neo4j_aura.py` | NEW | Same protocol; managed connection |
| AWS Neptune | `neptune.py` | NEW | Gremlin + openCypher |
| ArangoDB | `arangodb.py` | NEW (stretch) | Multi-model |

### 4.4 Loader library (Epic A)

Shared interface:

```python
class DocumentLoader(Protocol):
    @classmethod
    def from_source_config(cls, config: dict[str, Any]) -> "DocumentLoader": ...
    def kind(self) -> str: ...                   # "gdrive", "postgres", etc.
    async def load(
        self,
        *,
        since: datetime | None = None,           # incremental cursor
        signature_cache: dict[str, str],         # doc_id → last-known SHA-256
    ) -> AsyncIterator[Document]: ...
```

12-connector first batch (each one a sub-issue under Epic A):

1. **gdrive** — Google Drive folder scan + file fetch (PDF, DOCX, GDoc, GSheet, GSlides). OAuth via per-workspace install.
2. **gdocs** — Single-doc loader with comments + suggestions stripped.
3. **gsheets** — Each row as a document, with column names as keys.
4. **s3** — Bucket+prefix scan with `since` from object `LastModified`.
5. **gcs** — Google Cloud Storage equivalent.
6. **azure_blob** — Azure Blob Storage equivalent.
7. **postgres** — Parameterized SQL query with `:since` cursor; row → document.
8. **mysql** — Same shape as postgres.
9. **bigquery** — BigQuery query loader; row → document.
10. **snowflake** — Snowflake query loader.
11. **delta_lake** — Read Delta tables via the `deltalake` Python crate; incremental via timestamp column.
12. **iceberg** — Apache Iceberg tables (stretch).
13. **confluence** — Space scan + page fetch with attachments.
14. **notion** — Database/page traversal.
15. **slack** — Channel history + thread reconstruction (rate-limit aware).
16. **web** — HTTP fetch with crawl rules (respects robots.txt).
17. **image** — Image files via OCR (`pytesseract` or Vision API) + vision-LLM caption + CLIP embedding.

Each connector is an independent PR. Most are 1-2 days of work + tests.

### 4.5 Pipeline orchestrator

The orchestrator is a single async function in `engine/rag/pipeline.py`:

```python
async def reindex(
    config: RAGConfig,
    *,
    env: str,
    incremental: bool = True,
) -> ReindexResult:
    """Run the full pipeline for `config` against env `env`."""
    vstore = await VectorStore.from_config(config.environments[env].vector_store)
    gstore = (
        await GraphStore.from_config(config.environments[env].graph_store)
        if config.environments[env].graph_store
        else None
    )

    sig_cache = await load_source_signatures(config.name, env)
    new_sigs: dict[str, str] = {}

    async for source in config.sources:
        loader = DocumentLoader.from_source_config(source)
        async for doc in loader.load(
            since=sig_cache.last_run_at if incremental else None,
            signature_cache=sig_cache.by_doc_id,
        ):
            sha = sha256(doc.content)
            if incremental and sig_cache.by_doc_id.get(doc.id) == sha:
                continue                            # unchanged, skip
            new_sigs[doc.id] = sha

            chunks = chunk(doc, config.chunker)
            embeds = await embed(chunks, config.embedder)
            await vstore.upsert([VectorDoc(...)])
            if gstore:
                entities, relations = await extract_graph(doc, config.embedder)
                await gstore.upsert_entities(entities)
                await gstore.upsert_relations(relations)

    await persist_source_signatures(config.name, env, sig_cache | new_sigs)
    return ReindexResult(...)
```

### 4.6 Source signatures + incremental reindex

A new table `rag_source_signatures(rag_name, env, source_id, doc_id, sha, indexed_at)` keeps per-document content hashes. Incremental reindex skips any `(source_id, doc_id)` whose SHA matches the cached one — no re-embed, no store write. This is the difference between a 60-second nightly reindex (incremental) and a 60-minute one (full).

A `--full` flag on `agentbreeder rag reindex` forces full reindex (drops the cache row first).

### 4.7 Git lifecycle (reuses epic #244)

- Dashboard `/rag/builder` grows a `Save & Submit` button (same as agents — closes a piece of #244 Phase 1).
- `rag.yaml` lives in the git repo alongside `agent.yaml`.
- PR review → approve → merge fires the dev reindex automatically.
- `agentbreeder rag promote --env staging` re-runs the pipeline against staging stores.
- `--env prod` requires the same approver count + eval gate + soak as agents.

### 4.8 Local vs cloud parity

The same `rag.yaml` runs in both. The `environments:` block declares per-env stores; nothing else changes. Local docker-compose ships ChromaDB + Neo4j; cloud uses the env's managed equivalents. Auth flows for SaaS sources (Google, Notion, Slack) work identically — the OAuth refresh tokens live in the workspace's secrets backend.

### 4.9 Reindex policies

```yaml
reindex:
  on_merge: true                                # default
  schedule: "0 3 * * *"                         # nightly
  on_source_change:
    enabled: true
    debounce: 5m                                # batch many file events
```

`on_source_change` is implemented per-source where the underlying provider supports webhooks (Google Drive, GitHub, Notion); for the rest, a polling cron checks for new content every `poll_interval`.

---

## 5. Migration plan

### Phase 1 (Epic B — Multi-store abstraction)
- Define `VectorStore` and `GraphStore` Protocols
- Refactor existing ChromaDB code behind the new interface
- Land Neo4j (extends #113)
- Add pgvector + Pinecone + Weaviate + Qdrant + Vertex
- Add Neptune + Neo4j Aura

### Phase 2 (Epic A — Loader library)
- Implement first six loaders: gdrive, gdocs, gsheets, s3, postgres, web
- Implement next six: gcs, azure_blob, delta_lake, mysql, bigquery, snowflake
- Implement remaining four: confluence, notion, slack, image (multimodal)

### Phase 3 (Epic C — Lifecycle integration)
- Extend `rag.yaml` schema with `environments` + `reindex` blocks
- Add `rag_source_signatures` table + incremental reindex
- Wire RAG into the AgentOps lifecycle (#244): same `Save & Submit`, same approval queue, same per-env promotion
- Webhook listeners for `on_source_change`
- Dashboard `/rag/builder` redesign with multi-source + multi-env UI

Each phase is 4-8 PRs; the full plan is roughly v2.3 + v2.4 + v2.5.

---

## 6. Open questions

1. **Embedder cost control.** A full reindex over 100k documents at OpenAI prices is non-trivial. Should we ship a per-rag-index `max_monthly_embedding_cost_usd` budget that fail-closes when exceeded? Probably yes; defer to a follow-up issue.
2. **Multi-tenant isolation.** When two teams point at the same `pgvector` table, namespacing is via an extra `tenant_id` column. Default schema TBD.
3. **Retry semantics.** A single source failing — Google Drive rate-limited, Postgres transient error — shouldn't kill the entire reindex. Proposal: per-source try/except with isolation; final ReindexResult lists per-source success/failure.
4. **Schema drift in source DBs.** A `postgres` source whose underlying schema changes (column dropped) should fail loud, not silently re-index with `NULL`. Add a `expected_columns:` validator.
5. **Secrets in `rag.yaml`.** Source-specific creds reference the secrets backend (`secret://...`) — already supported. But OAuth refresh tokens for Google/Notion/Slack live in a different surface (per-workspace integration) and need a clear UX to wire them up.
6. **Eval gate for RAG promotions.** Today the eval gate (epic #244) is for agent quality. Promoting a RAG index to prod should also be gated on a retrieval-quality eval. Schema for that gate TBD.

---

## 7. What exists today that we keep

| Primitive | Status | Reuse |
|---|---|---|
| `rag.yaml` schema | Shipped (basic) | Extend with `environments`, `reindex`, multimodal sources |
| `/api/v1/rag/*` routes | Shipped | Extend with `/rag/{name}/reindex`, `/rag/{name}/signatures` |
| `dashboard/src/pages/rag-builder.tsx` | Shipped | Multi-source + multi-env UI redesign |
| ChromaDB integration | Shipped | Refactor behind `VectorStore` |
| Neo4j integration | In flight (#113) | Land as the first `GraphStore` impl |
| AgentOps lifecycle (epic #244) | Designed | RAG plugs into the same flow — no parallel pipeline |
| Per-env service principals (#248) | Designed | RAG deploys assume the same env role as agents |
| Daily cron infra (#199) | Shipped | Scheduled reindex uses it |
| Workspace secrets (Track K) | Shipped | All source/store creds + OAuth tokens reuse it |

The pattern is: **RAG inherits everything the AgentOps lifecycle already provides**. RAG-specific work is the loader library + the store abstraction + the pipeline orchestrator. The governance, deployment, and registry pieces all come for free from #244 / #245-#248.

---

## 8. Out of scope for this doc

- Query-time retrieval strategies (HyDE, multi-query rewriting, agentic retrieval) — belongs in agent code, not the index layer.
- Cross-region replication of vector / graph stores.
- ML training of custom embedding models.
- Time-series / event-store indexes (different access pattern).

---

## Appendix A — Summary of touched DB schemas

| Table | Status | Change |
|---|---|---|
| `rag_indexes` | EXISTING | Add `git_ref`, `deployed_by`, `env`, `last_indexed_at`, `total_documents`, `index_size_bytes` columns |
| `rag_source_signatures` | NEW | Per-document SHA-256 cache for incremental reindex |
| `rag_reindex_jobs` | NEW | Job state for async reindex (status, started_at, completed_at, error, per-source counts) |
| `audit_events` | EXISTING | New event types: `rag.reindex.started`, `rag.reindex.completed`, `rag.reindex.failed`, `rag.source.failed` |
