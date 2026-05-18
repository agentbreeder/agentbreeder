# Platform Audit & Additive-Only Improvement Plan

**Date:** 2026-05-18
**Author:** Claude (auto-generated from 9 parallel auditors)
**Scope:** Audit of 8 code subsystems + website-vs-implementation, with prioritized additive-only `/loop` task queue
**Risk envelope:** Additive only — no schema changes, no breaking changes, no DB migrations, no cross-repo API contract changes

---

## 1. Executive Summary

Nine parallel auditors reviewed the AgentBreeder platform on 2026-05-18. They found **91 findings** across 9 surfaces. After filtering against the additive-only risk envelope, **85 findings are eligible for the autonomous `/loop`** and **6 require human review** (memory team-scope isolation, GraphRAG schema extensions, deployer scenario-B gap, etc.).

| Subsystem | Findings | P0 | P1 | P2 | Additive-safe | Human-review |
|-----------|---------:|---:|---:|---:|---:|---:|
| Agents | 12 | 0 | 7 | 5 | 12 | 0 |
| Tools | 12 | 1 | 9 | 2 | 11 | 1 |
| Prompts | 10 | 0 | 5 | 5 | 10 | 0 |
| Models/Providers | 8 | 0 | 4 | 4 | 8 | 0 |
| Vector DB (RAG) | 12 | 3 | 4 | 5 | 12 | 0 |
| Graph DB (GraphRAG) | 11 | 0 | 4 | 7 | 8 | 3 |
| Memory | 10 | 2 | 4 | 4 | 8 | 2 |
| Multi-cloud Deploy | 10 | 0 | 3 | 7 | 10 | 0 |
| Website ↔ impl | 6 | — | — | — | 6 | 0 |
| **Total** | **91** | **6** | **40** | **39** | **85** | **6** |

### Critical findings (P0)
1. **Path traversal in markdown_writer** (Tools) — `subdir` parameter unsanitized; agent could write to `../../etc/passwd`. Security-critical.
2. **Silent pseudo-embedding fallback** (RAG) — When OpenAI/Ollama unreachable, system silently falls back to deterministic hash embeddings without alerting; search quality silently degrades.
3. **Weight normalization not validated** (RAG) — Search accepts `vector_weight=1.0, text_weight=1.0`, producing meaningless combined scores >1.0.
4. **No bounds on search numeric params** (RAG) — `top_k=0`, negative `hops`, etc. accepted; undefined behavior + DoS surface.
5. **Memory team-scope isolation not enforced at runtime** (Memory) — `requesting_team` declared but routes never pass it through; cross-team reads/writes possible. *Flagged as `breaking` — needs human review.*
6. **Memory only wired in LangGraph runtime** (Memory) — 3 of the 6 framework runtimes (Claude SDK, OpenAI Agents, CrewAI) have no memory wiring at all, breaking the cross-framework parity promise. *Flagged as `breaking` — needs human review.*

---

## 2. Cross-cutting themes

These themes emerged from multiple subsystems and should be addressed with shared helpers, not per-subsystem duplication.

### T1. Structured logging is absent or inconsistent (8/9 subsystems)
Affects: Agents, Tools, Prompts, Models, RAG, GraphRAG, Memory, Deploy. Each subsystem logs at most a handful of `logger.info()` calls. No structured fields, no correlation IDs, no metrics. **Fix as a shared utility** (`api/observability.py`) with a decorator + helper, then thread it through each subsystem.

### T2. Input/parameter validation gaps (6 subsystems)
Affects: Models (provider config), Prompts (test params), Tools (tool inputs), Memory (content size, query length), RAG (numeric bounds), Memory (backend_type enum). **Fix:** Add Pydantic Field constraints + Literal types where missing; add validators for cross-field invariants.

### T3. Missing tests on critical paths (every subsystem)
Particularly: prompt resolver fallback, RAG embedding fallback, openai_agents/node/go runtimes, MCP discovery error paths, deployer partial-failure scenarios, memory team isolation, fallback-chain streaming, standard tools (zero unit tests). **Fix:** Per-subsystem test backfill, each a self-contained task.

### T4. Retry/idempotency inconsistencies (4 subsystems)
Affects: Agents (no retry on invoke), Memory (no retry/circuit-breaker on summary LLM), Deploy (no idempotency on retry across 8 deployers), Models (fallback exists but no retry within a provider). **Fix:** Add shared retry helper with exponential backoff; thread through each subsystem.

### T5. Pseudo / fake / stub fallback hides failures (3 subsystems)
Affects: RAG (hash-embedding fallback), GraphRAG (eval metrics import from non-existent module), Memory (summary LLM swallows exceptions). **Fix:** Alert at WARNING level on first fallback; expose fallback flag in response/result so callers can detect it.

### T6. Cross-repo / cross-deliverable drift
The website overstates feature completeness in several places (deployment targets table, registry agent invoke example, migration link extensions, docker image namespace, CLI flag table). Memory notes `feedback_cross_repo_sync` reinforces this is a standing problem. **Fix:** Website task wave runs first in the loop.

---

## 3. Per-subsystem findings

### 3.1 Agents (12 findings, all additive-safe)
- **A1 (P1):** RuntimeBuilder validation errors are plain strings; switch to structured `RuntimeValidationResult` with path hints.
- **A2 (P1):** Tempfile leaks in all 6 Python runtimes' `build()` on exception. Wrap in `tempfile.TemporaryDirectory()` or try/finally.
- **A3 (P1):** No idempotency key on `invoke_agent`; duplicate retries can corrupt stateful agents.
- **A4 (P1):** No retry/backoff on transient 5xx/timeouts in `invoke_agent`.
- **A5 (P1):** Missing test files for openai_agents, node, go runtimes.
- **A6 (P1):** `claude_sdk` runtime has bespoke `_build_env_block()` diverging from shared helper; some env vars silently dropped.
- **A7 (P1):** No structured logging on agent create/build/invoke paths.
- **A8 (P1):** YAML validation doesn't call framework-specific validators; failures surface only at container build.
- **A9 (P2):** Untyped `Mapped[dict]` / `Mapped[list]` in DB model.
- **A10 (P2):** Missing docstrings on all 6 runtime `validate / get_entrypoint / get_requirements` implementations.
- **A11 (P2):** `validate()` doesn't check `agent_dir` exists; cryptic errors downstream.
- **A12 (P2):** Missing example projects for langgraph, crewai, claude_sdk.

### 3.2 Tools (12 findings, 11 additive-safe, 1 human-review)
- **T1 (P0, ⚠ flagged human-review — schema-change):** Path traversal in `markdown_writer.subdir`. The fix is small (whitelist relative-path components) but the auditor flagged it as a schema constraint change. **My read: this is additive-safe.** A pure server-side validation tightening that rejects malicious inputs has no breaking impact on legitimate callers. **Recommendation: move to P0 of the additive-safe queue.**
- **T2 (P1):** No JSON-Schema validation of tool inputs against declared SCHEMA.
- **T3 (P1):** Zero unit tests for `engine/tools/standard/` (web_search, markdown_writer).
- **T4 (P1):** MCP discovery silently truncates malformed tool defs.
- **T5 (P1):** Sandbox has minimal logging, no metrics.
- **T6 (P1):** The sandbox Python-builtin call used to execute user code runs in a namespace that is too permissive; agents can import `os` and similar. Tighten the namespace and AST-reject imports / dynamic-eval keywords.
- **T7 (P1):** MCP timeouts hardcoded (10s/30s); make per-server configurable.
- **T8 (P1):** Tool registry metadata not validated on register.
- **T9 (P1):** Inconsistent error response shape across MCP routes.
- **T10 (P2):** Add a docs/TOOLS.md contributor guide.
- **T11 (P2):** Tool outputs typed as `dict[str, Any]`; add `TypedDict`.
- **T12 (P2):** No E2E integration test for tool resolve → sandbox execute.

### 3.3 Prompts (10 findings, all additive-safe)
- **P1 (P1):** No dedicated unit tests for resolver (local → API → inline fallback chain).
- **P2 (P1):** `PromptTestRequest` accepts `temperature=999`, `max_tokens=-1`. Add Field constraints.
- **P3 (P1):** Test endpoint has no auth-failure test.
- **P4 (P1):** Lexicographic version sort — `"1.10.0" < "1.9.0"`. Use `packaging.version.Version`.
- **P5 (P1):** Orphaned `_enforce_acl()` — wire into a guard or add test that future CRUD routes call it.
- **P6 (P2):** Template var regex only matches `\w+`; document constraint.
- **P7 (P2):** No in-process cache on resolver; every call hits the API.
- **P8 (P2):** No structured logging of cost/usage on prompt test panel.
- **P9 (P2):** Resolver hardcoded to `.md`; support `.txt`, `.prompt`.
- **P10 (P2):** All registry errors collapsed to a single warning + None.

### 3.4 Models/Providers (8 findings, all additive-safe)
- **M1 (P1):** Anthropic/OpenAI providers don't map HTTP 403 → AuthenticationError; Google does.
- **M2 (P1):** `FallbackChain` has no `generate_stream()`; streaming clients never get failover.
- **M3 (P1):** Cost attributed to the *succeeding* provider after fallback, losing record of the primary attempt.
- **M4 (P1):** Ollama never populates `UsageInfo` token counts; budget checks silently broken.
- **M5 (P2):** `health_check()` returns bool, conflating auth failure with reachability failure.
- **M6 (P2):** Missing fallback-chain streaming tests.
- **M7 (P2):** Ollama 404 error message assumes CLI access.
- **M8 (P2):** `ProviderConfig` accepts empty `default_model`, invalid `base_url`.

### 3.5 Vector DB / RAG (12 findings, all additive-safe)
- **R1 (P0):** Search accepts unnormalized weights — `vector_weight+text_weight≠1.0`. Validate.
- **R2 (P0):** Silent fallback to hash-embeddings when OpenAI/Ollama unreachable. Add WARN on first fallback + flag in response.
- **R3 (P0):** `top_k`, `hops`, `seed_entity_limit` unbounded. Add `Query(ge=…, le=…)`.
- **R4 (P1):** No max file size on uploads (DoS surface).
- **R5 (P1):** No content-hash deduplication on ingestion.
- **R6 (P1):** Partial graph-extraction failures leak chunks into index without graph nodes.
- **R7 (P1):** Test coverage for embedding API failures = zero.
- **R8 (P2):** Malformed file inputs untested.
- **R9 (P2):** PDF fallback uses fragile BT/ET regex; document or use PyPDF2 optional dep.
- **R10 (P2):** `chunk_overlap ≥ chunk_size` causes infinite loop. Validate.
- **R11 (P2):** No RAG observability metrics.
- **R12 (P2):** No metadata filtering in search request body.

### 3.6 Graph DB / GraphRAG (11 findings, 8 additive-safe, 3 human-review)
- **G1 (P1):** Neo4j ingestion has no connection-pool reuse, no transaction batching, no retry.
- **G2 (P1):** No `CREATE INDEX IF NOT EXISTS` on `(Chunk.id, index_id)`, `(Entity.id, index_id)`. Query latency O(n*m).
- **G3 (P1):** Vector similarity hand-computed in Cypher via `reduce()` instead of Neo4j 5.16+ native vector index.
- **G4 (P1):** BFS Cypher has no intermediate `LIMIT` — potential path explosion on connected graphs.
- **G5 (P2, ⚠ additive-safe but spans schema):** Docs promise `search_mode`, `entity_path`, `vector_score`, `graph_score` in search response; only flat chunks returned. Adding response fields is backward-compatible.
- **G6 (P2, ⚠ schema-change, human-review):** Docs promise `custom_entity_types` in agent.yaml; not in schema. Requires `agent.yaml` schema extension. **Filed as GitHub issue, not loop task.**
- **G7 (P2):** Module-level extraction cache has no invalidation.
- **G8 (P2):** Eval service imports from non-existent module; evals are fake. Implement or remove from docs.
- **G9 (P2):** No observability on Neo4j queries.
- **G10 (P2):** No real-Neo4j integration tests (all mocked).
- **G11 (P2, ⚠ breaking, human-review):** `pgvector` backend listed but silently falls back to in_memory. Either implement or remove. **Filed as GitHub issue.**

### 3.7 Memory (10 findings, 8 additive-safe, 2 human-review)
- **MM1 (P0, ⚠ breaking, human-review):** Team-scope isolation declared but never enforced at runtime — cross-team reads/writes possible. **Critical data-isolation gap. Filed as GitHub issue with security label.**
- **MM2 (P0, ⚠ breaking, human-review):** Memory wiring missing from Claude SDK, OpenAI Agents, CrewAI runtimes. Breaks multi-framework promise. **Filed as GitHub issue.**
- **MM3 (P1):** `backend_type`/`memory_type` accept any string; add Literal types + validator.
- **MM4 (P1):** LIKE query unescaped — agents can inject `%`/`_` wildcards.
- **MM5 (P1):** Memory content unbounded — add `Field(max_length=…)`.
- **MM6 (P1):** Summary LLM uses sync POST with swallowed exceptions; add circuit breaker + async background task.
- **MM7 (P2):** No team-isolation tests.
- **MM8 (P2):** TTL only via env var; expose as per-config field.
- **MM9 (P2):** No PII/GDPR endpoint.
- **MM10 (P2):** No memory observability metrics.

### 3.8 Multi-cloud Deploy (10 findings, all additive-safe)
- **D1 (P1):** No idempotency check — retrying `deploy()` collides with existing resources.
- **D2 (P1):** Phase boundaries between deploy()/health_check()/teardown() have no structured logs; partial-failure forensics impossible.
- **D3 (P1):** AWS ECS swallows teardown scale-to-zero failures; orphaned services possible.
- **D4 (P1):** MCP sidecar injection has no pre-validation; broken sidecars fail at health-check, not at submit.
- **D5 (P2):** Health-check retry semantics differ across AWS (deadline+interval) vs GCP/Azure (fixed sleep). Unify into base.py helper.
- **D6 (P2):** `identity.py` IAM provisioning is silent — no `identity_created` / `identity_failed` events.
- **D7 (P2):** Missing cross-deployer test: "deploy succeeds, health-check fails → teardown is called AND succeeds."
- **D8 (P2):** `RUNTIME_DEPLOYERS` precedence rule (runtime > cloud) not documented or tested.
- **D9 (P2):** Credential resolution logs nothing — operators can't tell which source supplied a key.
- **D10 (P2):** No greenfield (scenario B) per-cloud prerequisite docs — but this is a known ~970-LOC gap, **flagged for human-review** (it's not just docs, it's missing implementation).

### 3.9 Website ↔ implementation (6 findings, all additive-safe)
- **W1 (BROKEN, quickstart):** `--reset` documented in quickstart.mdx but missing from cli-reference.mdx. Add to reference table.
- **W2 (BROKEN, how-to):** Migration links use `.md` but real files are `.mdx`. Fix link paths.
- **W3 (BROKEN, how-to):** `agentbreeder registry agent invoke …` example doesn't exist as a subcommand. Replace with `curl` / `api/v1/agents/{id}/invoke` example or remove.
- **W4 (BROKEN, how-to):** Docker image references use stale `rajits/` namespace. Update to current registry path (verify in `.github/workflows/release.yml`).
- **W5 (STALE, how-to):** Deployment-targets table claims AWS App Runner, Azure, K8s, Claude Managed are shipped. The Deploy auditor found these *implementations exist and conform to interface*, but per `D10` they lack greenfield provisioning. **Reconciliation: keep the rows, but add a "Prereqs" column that lists what each target requires (existing VPC, project, etc.) — that's truthful and doesn't gate the loop.**
- **W6 (BROKEN, cli-reference):** quickstart flags table missing `--reset`, `--no-ollama`, `--ollama-model`.

---

## 4. `/loop` task queue (additive-safe only)

The loop pulls top-of-queue, implements the task, runs `/launch` (excluding release/tag phase), commits, then advances. Quickstart/how-to/website fixes are pinned to **Wave 0** per user instruction. Each task is self-contained — no implicit dependency on prior unmerged tasks.

### Wave 0 — Website corrections (highest priority, user-facing)
*Goal: quickstart.mdx and how-to.mdx are 100% correct against current implementation.*

| ID | File | Change | Acceptance |
|----|------|--------|------------|
| W-01 | `website/content/docs/cli-reference.mdx` | Add `--reset`, `--no-ollama`, `--ollama-model NAME` rows to `agentbreeder quickstart` flags table | Flags match `cli/commands/quickstart.py` signature; rendered table includes all 3 |
| W-02 | `website/content/docs/how-to.mdx` | Fix migration link paths: `.md` → `.mdx`, normalize `FROM_FOO` → `from-foo` | Every link 200s in built site |
| W-03 | `website/content/docs/how-to.mdx` | Replace non-existent `agentbreeder registry agent invoke …` example with working `curl` / proxy example | Sample command executes against a running quickstart stack |
| W-04 | `website/content/docs/how-to.mdx`, any other `*.mdx` referencing `rajits/agentbreeder-*` | Replace stale `rajits/` Docker namespace with current path from `.github/workflows/release.yml` | grep across `website/content/docs/` finds no `rajits/agentbreeder-` references |
| W-05 | `website/content/docs/how-to.mdx` | Add "Prereqs" column to Deployment-targets table (existing VPC for AWS, project for GCP, etc.); call out scenario-B (greenfield) gap | Each row honestly describes what's needed |
| W-06 | `website/content/docs/quickstart.mdx` | Confirm `--reset` description matches `cli/commands/quickstart.py` behavior (tear down + start fresh) | Doc + code match |

### Wave 1 — P0 correctness / security
| ID | Subsystem | Title | Files |
|----|-----------|-------|-------|
| W1-01 | Tools | Sanitize `markdown_writer.subdir` against path traversal | `engine/tools/standard/markdown_writer.py`, new test |
| W1-02 | RAG | Validate `vector_weight + text_weight == 1.0` in search route | `api/routes/rag.py` |
| W1-03 | RAG | Alert (WARN log + response flag) on pseudo-embedding fallback | `api/services/rag_service.py` |
| W1-04 | RAG | Add `ge=` / `le=` bounds to `top_k`, `hops`, `seed_entity_limit` | `api/routes/rag.py` |

### Wave 2 — Shared utility introduction (unblocks Wave 3)
| ID | Title | Files |
|----|-------|-------|
| W2-01 | Create `api/observability.py` with structured-log helper + decorator | new module + tests |
| W2-02 | Create `api/retry.py` with exponential-backoff helper | new module + tests |
| W2-03 | Create `engine/deployers/_health.py` with shared health-check retry loop | new module + tests |

### Wave 3 — Cross-cutting application of Wave 2 helpers
Each task threads a shared helper through one subsystem (small, isolated, parallelizable).

| ID | Subsystem | Change |
|----|-----------|--------|
| W3-01 … W3-08 | All 8 subsystems | Thread `observability` decorator through public API/service methods (one task per subsystem) |
| W3-09 … W3-12 | Agents, Memory, Deploy, Models | Thread `retry` helper through the corresponding invoke / store / deploy / generate paths |
| W3-13 … W3-16 | aws_ecs, aws_app_runner, gcp_cloudrun, azure_container_apps | Replace bespoke health-check loops with `_health.poll_until_ready()`. Kubernetes / docker_compose / claude_managed / mcp_sidecar use different patterns and are evaluated separately in Wave 5 D7 (cross-deployer partial-failure test). |

### Wave 4 — P1 per-subsystem fixes
Each row is one /loop task with file scope, test requirement, and acceptance criteria spelled out.

| ID | Subsystem | Finding | Implementation note |
|----|-----------|---------|---------------------|
| W4-01 | Agents | A1 — Structured `RuntimeValidationResult` | New typed dataclass; existing string-returning callers wrap into new shape |
| W4-02 | Agents | A2 — Tempfile cleanup in all 6 runtimes | Switch each `mkdtemp` to `TemporaryDirectory()` context |
| W4-03 | Agents | A3 — Idempotency-Key header propagation | Generate UUID, forward in httpx.post, document |
| W4-04 | Agents | A5 — Add test files for openai_agents / node / go runtimes | New `tests/unit/test_runtime_*.py` files |
| W4-05 | Agents | A6 — Consolidate claude_sdk env-block onto shared helper | Delete bespoke method, fall back to `build_env_block` |
| W4-06 | Agents | A8 — Wire framework validators into YAML validation | Call runtime validators in `validate_config_yaml` |
| W4-07 | Tools | T2 — JSON-schema validate tool inputs against SCHEMA | `engine/tool_resolver.py` |
| W4-08 | Tools | T3 — Backfill unit tests for `engine/tools/standard/*` | New `tests/unit/test_standard_tools.py` |
| W4-09 | Tools | T4 — Validate MCP discovery responses; reject malformed | `registry/mcp_servers.py` |
| W4-10 | Tools | T6 — Tighten sandbox namespace + AST-reject imports / dynamic-eval keywords | `api/services/sandbox_service.py` |
| W4-11 | Tools | T7 — Add `timeout_seconds` to McpServer (default 10, range 1-120) | model + routes; backwards-compatible default |
| W4-12 | Tools | T8 — Pydantic `ToolRegistryMetadata` validator | `registry/tools.py`, `engine/tool_resolver.py` |
| W4-13 | Tools | T9 — Standardize MCP route error shape on `HTTPException(detail=…)` | `api/routes/mcp_servers.py` |
| W4-14 | Prompts | P1 — Backfill resolver tests (fallback chain, version pin, error cases) | `tests/unit/test_prompt_resolver.py` |
| W4-15 | Prompts | P2 — `Field(ge / le)` on `temperature` / `max_tokens` | `api/models/schemas.py` |
| W4-16 | Prompts | P3 — Add 401 test for prompt-test endpoint | `tests/unit/test_prompt_test.py` |
| W4-17 | Prompts | P4 — Semver-aware sort using `packaging.version` | `engine/prompt_resolver.py`, `registry/prompts.py` |
| W4-18 | Prompts | P5 — Add wiring/lint check that prompt routes call `_enforce_acl()` | test + docstring |
| W4-19 | Models | M1 — Map 403 → AuthenticationError in Anthropic + OpenAI | `engine/providers/{anthropic,openai}_provider.py` |
| W4-20 | Models | M2 — Implement `FallbackChain.generate_stream()` | `engine/providers/registry.py` |
| W4-21 | Models | M3 — Add `fallback_from` field to `GenerateResult`; emit cost from chain layer | `engine/providers/models.py`, `registry.py` |
| W4-22 | Models | M4 — Populate `UsageInfo` from Ollama response or document fallback | `engine/providers/ollama_provider.py` |
| W4-23 | RAG | R4 — `MAX_UPLOAD_SIZE_MB=100` constant + 413 check | `api/routes/rag.py` |
| W4-24 | RAG | R5 — Content-hash dedup at chunk ingestion | `api/services/rag_service.py` |
| W4-25 | RAG | R6 — Roll back chunk insertion if graph extraction fails on any chunk | `api/services/rag_service.py` |
| W4-26 | RAG | R7 — Add embedding-failure + fallback-path tests | `tests/unit/test_rag_service.py` |
| W4-27 | GraphRAG | G1 — Batched transactions + retry for Neo4j ingestion | `api/services/neo4j_rag_backend.py` |
| W4-28 | GraphRAG | G2 — `CREATE INDEX IF NOT EXISTS` on driver init | `api/services/neo4j_rag_backend.py` |
| W4-29 | GraphRAG | G3 — Detect Neo4j ≥5.16 native vector index; fall back to current Cypher | `api/services/neo4j_rag_backend.py` |
| W4-30 | GraphRAG | G4 — Add `LIMIT $intermediate_limit` to BFS Cypher | `api/services/neo4j_rag_backend.py` |
| W4-31 | Memory | MM3 — Literal types on `backend_type`, `memory_type` | `api/models/schemas.py` |
| W4-32 | Memory | MM4 — Escape `%`/`_` in LIKE query + max_length on `q` | `api/services/memory_service.py`, route |
| W4-33 | Memory | MM5 — `Field(max_length=100_000)` on `content` | `api/routes/memory.py` |
| W4-34 | Memory | MM6 — Move summary LLM to background task with circuit breaker | `api/services/memory_service.py` |
| W4-35 | Deploy | D1 — Idempotency check at start of each deployer's `deploy()` | all 8 deployers |
| W4-36 | Deploy | D3 — Retry-with-backoff on AWS ECS scale-to-zero teardown | `engine/deployers/aws_ecs.py` |
| W4-37 | Deploy | D4 — Pre-validate sidecar config before any cloud API call | new validator in `engine/sidecar/` |

### Wave 5 — P2 polish
Tests, docs, type hints, examples. 38 tasks. Generated mechanically from per-subsystem P2 findings.

| Subsystem | P2 task count |
|-----------|--------------:|
| Agents | 4 |
| Tools | 3 |
| Prompts | 5 |
| Models | 4 |
| RAG | 5 |
| GraphRAG | 6 |
| Memory | 4 |
| Deploy | 7 |
| **Total** | **38** |

(Itemized P2 list in `## 7. Appendix — P2 task list (Wave 5 detail)` below.)

---

## 5. Human-review backlog (not entering `/loop`)

These findings either require breaking changes, schema extensions, or company decisions. They will be filed as GitHub issues at the end of the audit synthesis. The loop will not touch them.

| ID | Subsystem | Finding | Why human-review |
|----|-----------|---------|------------------|
| HR-1 | Memory | MM1 — Team-scope isolation not enforced at runtime | Touches all memory routes; signatures change; needs RBAC integration plan |
| HR-2 | Memory | MM2 — Memory missing from 3 framework runtimes | New runtime feature wiring; multi-runtime test plan; potential perf impact |
| HR-3 | GraphRAG | G6 — `custom_entity_types` in agent.yaml | agent.yaml schema bump → website + cloud sync required |
| HR-4 | GraphRAG | G11 — `pgvector` backend silent fallback | Either implement (large) or remove (breaking) |
| HR-5 | Deploy | D10 — Greenfield (scenario B) infra provisioning | ~970 LOC across 3 clouds; aligns with `project_comprehensive_arch_plan` epics |
| HR-6 | Tools | T1 — Path traversal in `markdown_writer.subdir` | The *fix* is safe and additive; the auditor's `schema-change` tag is conservative. **Recommendation: bypass human-review and promote to W1-01 in the additive queue.** Listed here only for visibility. |

---

## 6. Loop operating contract

**Per-task flow:**
1. Pop top of queue.
2. Read referenced files; implement the change.
3. Run `/launch` excluding the release/tag phase. All four `/gate` gates (lint, test, typecheck, security) must pass. Docs sync, build verification, type checks must pass.
4. If `/launch` reports "unfixable" or any gate fails after one retry: pause loop, surface to human.
5. Otherwise: commit with conventional-commit message + cross-repo-sync footnote if Wave 0; advance.

**Batched releases:** No release/tag in the per-task loop. After every 10 completed tasks or on demand, run `/launch` end-to-end (including release phase) to cut a single patch release covering the batch. Final release at end of Wave 5.

**Cross-repo sync:** Wave 0 (website) commits push directly to the agentbreeder repo (website auto-deploys on push to `main` per CLAUDE.md). Waves 1-5 introduce no schema/CLI/API changes, so cloud sync is not required. If any task's implementation drift is detected (lint warns about an unexpected file change), pause loop.

**Safety floors:**
- Never delete production code that has no test coverage — add the test first, then refactor.
- Never modify `engine/schema/*.json` (would be schema-change).
- Never modify migrations (`alembic/versions/`).
- Never touch `agentbreeder-cloud` or the cloud-facing API contract.
- If a task touches >5 files or grows beyond ~200 lines diff, pause and surface for human review.

---

## 7. Appendix — P2 task list (Wave 5 detail)

Generated from per-subsystem P2 findings. Each is a self-contained task; same Wave 5 format as Wave 4 rows.

**Agents (4):** A9 (typed `dict[str, Any]` / `list[str]`), A10 (runtime docstrings), A11 (validate `agent_dir` exists), A12 (example projects for langgraph / crewai / claude_sdk).

**Tools (3):** T10 (docs/TOOLS.md), T11 (TypedDict outputs), T12 (E2E integration test).

**Prompts (5):** P6 (document template-var regex), P7 (resolver LRU cache), P8 (prompt-test panel cost logging), P9 (multi-extension support), P10 (differentiated registry-error logging).

**Models (4):** M5 (structured health-check result), M6 (fallback-chain streaming tests), M7 (Ollama 404 message rewrite), M8 (ProviderConfig validators).

**RAG (5):** R8 (malformed-file tests), R9 (PyPDF2 optional dep + docs), R10 (chunk overlap validation), R11 (metrics), R12 (metadata filter param).

**GraphRAG (6):** G5 (response-field additions for `entity_path` / `vector_score` / `graph_score`), G7 (cache invalidation endpoint), G8 (implement or remove eval metrics), G9 (Neo4j metrics), G10 (testcontainers Neo4j integration test), plus reconcile-doc-with-impl task.

**Memory (4):** MM7 (team-isolation tests — documents current behavior pending HR-1), MM8 (per-config TTL), MM9 (PII / GDPR endpoint), MM10 (memory metrics).

**Deploy (7):** D5 (shared health-check helper — overlaps with Wave 2), D6 (identity logging), D7 (teardown-on-failed-health-check test), D8 (precedence test + docstring), D9 (credential-resolution logging), D10 (greenfield prereqs docs).

---

## 8. Next steps

1. **User reviews this spec** — sign off or request edits.
2. On approval: invoke `superpowers:writing-plans` skill to convert the queue into an executable plan with per-task acceptance criteria and test scaffolds.
3. Configure the `/loop` per §6 and start.
4. File the 5 human-review items (HR-1 through HR-5) as GitHub issues with labels (`epic`, `security`, `cross-repo-sync`, etc.).

**Estimated loop duration** (very rough): Wave 0 ≈ 1 hour, Wave 1 ≈ 2 hours, Wave 2 ≈ 2 hours, Wave 3 ≈ 4 hours, Wave 4 ≈ 12 hours, Wave 5 ≈ 8 hours → ~29 hours of autonomous work, assuming `/launch` adds ~5 minutes per task and tasks average 30 minutes of implementation each.
