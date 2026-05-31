# Cloud-Agnostic Deployment — Epic Index

> **For agentic workers:** This is the **epic index**. Each sub-plan (P1–P5) is its own
> document and produces working, testable software on its own. Implement them in order.
> P1 is the prerequisite gate for P2–P4. Write the full bite-sized plan for P2–P5
> (using `superpowers:writing-plans`) **immediately before** executing each — not now —
> so they reflect the code as P1 leaves it.

**Goal:** Make AgentBreeder's "Define Once. Deploy Anywhere." real for *every* artifact
(agents, prompts, tools, MCP, RAG, memory) on AWS / GCP / Azure, and make **Studio itself**
runnable in local **or** any cloud — not just local Docker Compose.

**Date:** 2026-05-30
**Owner:** Rajit (saha.rajit@gmail.com)
**Scope decision (locked with user):** Both layers, phased (Layer 2 → Layer 3),
with **real managed-backend provisioning**, plan-first.

---

## Review findings (why this epic exists)

Three layers, very uneven maturity:

| Layer | State | Evidence |
|---|---|---|
| **L1 — Agent container deployers** (ECS, App Runner, Cloud Run, Container Apps, K8s, Claude-managed) | ✅ **Done.** Real SDK calls, full `provision→deploy→health→teardown→get_logs`, secret-mirroring, per-agent IAM, scaling, sidecar injection. | `engine/deployers/*` — only gap is `aws_ecs.py:466` port-8081 TODO |
| **L2 — Artifact data-plane** (prompts, tools, MCP, RAG, memory) | ❌ **Broken for cloud.** | see below |
| **L3 — Studio self-hosting** | ❌ **Compose-only.** No Helm/K8s/IaC; `dashboard/nginx.conf:4` hardcodes `http://api:8000`; CORS localhost-only; no self-hosting docs. | `deploy/*.yml`, `dashboard/nginx.conf` |

**L2 root causes (confirmed by reading the code):**
1. **Engine never bundled.** Every runtime `build()` does `COPY . .` (agent dir + `server.py`) and
   `pip install -r requirements.txt`, where requirements are framework deps + litellm only
   (`engine/runtimes/langgraph.py:85-141`, `get_requirements` `:153-181`). But shipped server
   templates / examples import `from engine.*` and `from api.services.*`
   (`langgraph_server.py:183`, `claude_sdk_server.py:186/318`, `google_adk_server.py:29-30`,
   `openai_agents_server.py:170`, `examples/ai-news-digest/agent.py:30`) → **ImportError in any
   non-local container.**
2. **No data-plane provisioning.** `engine/resolver.py:151-181` scrapes the **deploy host's local**
   `REDIS_URL`/`DATABASE_URL`/`NEO4J_URL` and forwards them to the cloud agent → unreachable.
   `RAGStore` is an in-memory singleton in the API process (`api/services/rag_service.py:948`).
3. **MCP is dead code.** `engine/deployers/mcp_sidecar.py` + `engine/mcp/packager.py` have zero
   callers; the injected Go sidecar isn't told MCP endpoints (`engine/sidecar/config.py:77-88`).

---

## Sub-plan sequence

| # | Plan | Layer | Delivers | Depends on |
|---|---|---|---|---|
| **P1** | `2026-05-30-p1-artifact-bundling-foundation.md` | 2 | Engine bundled into agent images; `prompts/<name>` baked at deploy; first-party tool refs validated at deploy; **backend-URL injection contract** (the seam P2/P3 fill) | — |
| **P2** | RAG managed-backend provisioning | 2 | pgvector on RDS / Cloud SQL / Azure PG; embed-on-ingest + embed-on-query client; `KB_PGVECTOR_DSN` wired; teardown | P1 |
| **P3** | Memory managed-backend provisioning | 2 | Redis (ElastiCache / Memorystore / Azure Cache) + managed Postgres; `MEMORY_*` wired; teardown | P1 |
| **P4** | MCP server deployment | 2 | Build+push+run MCP images; feed endpoints to Go sidecar; revive `packager.py`/`mcp_sidecar.py` | P1 |
| **P5** | Studio self-hosting | 3 | Helm chart + K8s manifests; env-driven nginx upstream / CORS / TLS / ingress; `self-hosting.mdx` | — (parallelizable) |

---

## Cross-cutting decisions (apply to all sub-plans)

- **D1 — Bundling mechanism:** add `agentbreeder=={installed-version}` to each **Python** runtime's
  image requirements via a shared `runtime_support_requirement()` helper. Rationale: the dist already
  contains `engine` + `api`; mirrors `Dockerfile.cli`; zero template/example refactor. Override via
  `AGENTBREEDER_RUNTIME_REQUIREMENT` (pinned version / VCS URL / local wheel / empty=opt-out).
  *Known trade-off:* image size — a slim `agentbreeder-runtime` package is logged as future work, not P1.
  Node runtimes are out of scope (they don't import the Python engine).
- **D2 — Backend-URL contract (the integration seam):** the resolver stops scraping the deploy host's
  local env by default. Backends reach the agent only via **explicit** sources:
  `agent.yaml` → `memory.backend_url` / `knowledge_bases[].backend_url` (user BYO), **or** a deployer
  that provisions a managed backend and sets the same env vars (P2/P3). Local-dev env scraping is
  preserved **only** when `AGENTBREEDER_ALLOW_LOCAL_BACKENDS=1` (so `docker compose up` keeps working).
  Canonical env vars: `KB_PGVECTOR_DSN`, `NEO4J_URL` (RAG); `MEMORY_BACKEND`, `REDIS_URL`,
  `DATABASE_URL` (memory — names kept in P1, P3 may namespace them).
- **D3 — Governance untouched:** the sacred pipeline order (parse→RBAC→resolve→build→provision→deploy→
  health→register) stays. New provisioning slots **inside** `deployer.provision()`, new resolution
  **inside** `resolve_dependencies()`. Never add a bypass.
- **D4 — Cross-repo sync (standing rule):** any `agent.yaml` schema field added here →
  update `engine/schema/agent.schema.json` + `website/content/docs/agent-yaml.mdx` **in the same commit**,
  and grep `agentbreeder-cloud` for the field. New CLI flags → `cli-reference.mdx` same commit.
- **D5 — Branch & PR discipline:** one feature branch per sub-plan, incremental commits, **PR opened
  only after the whole sub-plan + local tests pass** (preserve wave-by-wave history; no squash).

---

## Definition of done (epic)

- A non-trivial agent (registry-ref prompt + first-party tool + a KB + memory) deploys to **AWS, GCP,
  and Azure** and at runtime: loads its prompt, imports its tools, retrieves KB context from a
  provisioned vector store, and persists memory to a provisioned Redis/Postgres — **with no value
  scraped from the deploying machine.**
- `agentbreeder teardown` removes every provisioned data-plane backend it created.
- Studio runs from a Helm chart on any K8s cluster with externalized Postgres/Redis, configurable
  API upstream + CORS + TLS, documented in `self-hosting.mdx`.
- All new code TDD'd; changed-file coverage ≥ 80%; docs updated in-commit per D4.
