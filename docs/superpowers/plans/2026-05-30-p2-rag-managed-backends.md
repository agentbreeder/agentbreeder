# P2 — RAG managed backends (pgvector on RDS / Cloud SQL / Azure PG)

> Phase 2 of the cloud-agnostic deployment epic (#523). Branch `feat/cloud-agnostic-p2-rag-backends` off the merged P1 (#522). Each increment is a commit; PR opens when the whole phase is green.

## Goal
A deployed agent that declares `knowledge_bases` retrieves KB context **at runtime from a provisioned pgvector store** — on AWS, GCP, or Azure — with the connection string injected by the deployer, never scraped from the deploy host. `agentbreeder teardown` removes the provisioned DB.

## What already exists (verified 2026-05-30)
- **App-side pgvector backend** — `api/services/pgvector_rag_backend.py` (`connect`, `_ensure_table` per-dimension, `upsert_chunks`, `search` via `<=>` cosine). Factory `create_pgvector_backend()`; registry `registry/rag.py` `get_rag_backend("pgvector", ...)`.
- **RAGStore dispatch (#423)** — `api/services/rag_service.py`: ingest mirrors to `backend.upsert_chunks()`; `search()` routes to `backend.search()` when a backend is configured.
- **Managed Postgres provisioning, all 3 clouds** — `engine/provisioners/{aws._provision_rds, gcp._ensure_cloud_sql, azure._ensure_postgres_flexible}`. Each returns endpoint/host + db + secret in `InfraState.resources["rds"|"cloud_sql"|"postgres"]`.
- **Backend-URL contract (P1)** — `engine/resolver.py:~284` sets `KB_PGVECTOR_DSN` from explicit `knowledge_bases[].backend_url`; localhost-on-cloud warning; scraping gated behind `AGENTBREEDER_ALLOW_LOCAL_BACKENDS`.
- **Integration test** — `tests/integration/test_pgvector_testcontainers.py` (real pg+pgvector round-trip).

## The 5 gaps P2 closes
1. **Runtime never queries pgvector.** `engine/runtimes/templates/langgraph_server.py:_inject_kb_context` calls the in-memory `get_rag_store()`, which is empty in the deployed container. Must, when `KB_PGVECTOR_DSN` is set, embed the query and search the pgvector backend directly by index namespace.
2. **Provisioned Postgres → `KB_PGVECTOR_DSN` is never wired.** Provisioner returns the endpoint in `InfraState.resources`, but nothing turns it into a DSN env var on the container. (Resolver only handles the *explicit* `backend_url` path.)
3. **`vector` extension not allow-listed** on managed Postgres → `CREATE EXTENSION vector` fails. RDS needs a parameter group (`shared_preload_libraries`/`rds.allowed_extensions`); Cloud SQL needs `cloudsql.enable_pgvector` database flag; Azure needs the `azure.extensions` server parameter.
4. **Provisioning is gated on memory flags, not RAG.** `AWS_HAS_MEMORY` / `GCP_PROVISION_CLOUD_SQL` / `AZURE_PROVISION_POSTGRES`. Declaring `knowledge_bases` (without `backend_url`) must request a pgvector DB.
5. **Teardown coverage.** Orchestrator `destroy_partial()` calls `provisioner.destroy()`; ensure the RAG-provisioned DB is included and that CLI `agentbreeder teardown` reaches it.

## Architecture (fixed by epic D3)
New provisioning slots **inside `deployer.provision()`** (the sacred pipeline, `engine/builder.py` step 5) — NOT the separate wizard `InfraProvisioner` path. `deployer.provision()` will, when a managed pgvector is needed, provision the DB (reusing the per-cloud `_provision_*`/`_ensure_*` logic), build the DSN, and `config.deploy.env_vars.setdefault("KB_PGVECTOR_DSN", dsn)` so the existing P1 seam carries it to the container.

## Increments (each a commit, TDD)
1. **Runtime embed-on-query (gap 1).** New `KB_EMBEDDING_MODEL` env (resolver writes it from the KB index model). `_inject_kb_context`: when `KB_PGVECTOR_DSN` set → `embed_texts([query], model)` → cached pgvector backend `.search(vec, top_k)` → format. Unit test mocks the backend + embedder. *(start here — self-contained, no cloud)*
2. **DSN builder (gap 2).** `engine/deployers/_pgvector_dsn.py`: `pgvector_dsn_from_resources(cloud, resources, password)` → `postgresql://…`. Pure, unit-tested per cloud shape.
3. **Extension allow-listing (gap 3).** Per provisioner: RDS DB parameter group with `vector` allowed; Cloud SQL `database_flags`; Azure `azure.extensions`. Unit tests assert the SDK calls set the param.
4. **Wire provision→DSN into `deployer.provision()` (gaps 2+4).** When `config.knowledge_bases` present and no explicit `backend_url`, request a pgvector DB, then set `KB_PGVECTOR_DSN`. Unit-test with mocked provisioner returning a resources dict.
5. **Teardown (gap 5).** Ensure provisioned RAG DB is destroyed; cover CLI path. Test.
6. **Docs + integration.** `website/content/docs/rag.mdx` (managed backends + per-cloud notes); extend testcontainers coverage; CHANGELOG.

## P3 reuse note
P3 (memory) reuses increments 2–5: the same per-cloud Postgres provisioning + DSN builder + teardown, plus Redis (ElastiCache/Memorystore/Azure Cache), wiring `MEMORY_BACKEND`/`REDIS_URL`/`MEMORY_DATABASE_URL`. `engine/runtimes/templates/memory_manager.py` already consumes those envs.

## Cross-repo / governance
Sacred pipeline order unchanged (D3). Cross-repo sync: re-grep `agentbreeder-cloud` for `KB_PGVECTOR_DSN` / provisioning before PR. Schema: no new `agent.yaml` field (uses P1's `backend_url`); if `KB_EMBEDDING_MODEL` becomes user-facing, document it.
