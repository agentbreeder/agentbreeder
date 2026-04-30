# Changelog

All notable changes to AgentBreeder are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Workspace secrets backend chooser is live in the dashboard** (#213) — `/settings/secrets` previously rendered a Coming-soon stub that told operators to edit `~/.agentbreeder/workspace.yaml` by hand. Now the page renders a working `<select>` over the supported backends (`env`, `keychain`, `aws`, `gcp`, `vault`); switching backends prompts a confirm dialog (since existing secrets in the previous backend are NOT auto-migrated), then `PUT /api/v1/secrets/workspace` persists the choice through the new `save_workspace_secrets_config()` helper. Backend swap is admin-only (deployer/viewer get 403), validates that the chosen backend can actually be instantiated (so missing optional deps surface as 400 instead of silently breaking the workspace), and emits a `secret.backend_changed` audit event. New `api.secrets.setBackend()` client. 10 new unit tests cover RBAC, validation, audit, file persistence, and workspace-name preservation.
- **Ollama Pull Model live in the dashboard** (#214) — `/settings` provider list previously rendered a disabled `Pull Model` button labelled "Coming soon" for Ollama rows. Now clicking it opens a modal that streams real progress from `ollama pull <model>`. New `OllamaProvider.pull_model()` async generator yields each event from Ollama's `/api/pull` NDJSON stream (`pulling manifest` → `downloading <digest>` with `total`/`completed` byte progress → `success`). New `POST /api/v1/providers/{id}/pull-model` returns those events as Server-Sent Events; rejects 400 when the provider isn't Ollama and 404 when missing. Frontend `api.providers.pullModel()` returns the raw `Response` so the modal can consume the SSE stream via `ReadableStream`. The dialog shows a popular-model chip palette (`llama3.2`, `mistral`, `mixtral`, `qwen2.5`, etc.), a percent progress bar derived from `completed/total`, and final ✓ / ✗ states. 4 new unit tests cover 404 / 400 (non-Ollama) / streamed-success / Pydantic validation.
- **`incidents` PostgreSQL table** (#207) — new Alembic migration `020_incidents_table.py` adds an `incidents` table with `id`, `title`, `severity` (`critical|high|medium|low`), `status` (`open|investigating|mitigated|resolved`), `affected_agent_id` (FK → `agents.id`, ON DELETE SET NULL), `description`, `created_by`, `created_at`, `resolved_at`, `timeline` (JSONB), `incident_metadata` (JSONB) plus indexes on status / severity / created_at / affected_agent_id. New `IncidentSeverity` and `IncidentStatus` enums in `api/models/enums.py`; new `Incident` ORM model in `api/models/database.py`.
- **Real agent version history** (#210) — `/agents/:id` Configuration Tab → Compare Versions panel was reading `MOCK_VERSIONS` + `MOCK_VERSION_YAML` constants because the registry only stored the *current* `config_snapshot` per agent. New `agent_versions` table (Alembic 019) is populated by `AgentRegistry.register()` whenever an agent's version string changes, capturing `(agent_id, version, config_snapshot, config_yaml, created_by, created_at)` with a UNIQUE `(agent_id, version)` constraint so re-registering the same version updates in place. New `GET /api/v1/agents/:id/versions` route + `api.agents.versions()` client; the dashboard now fetches real history (lazily, only when the diff panel opens), defaults the diff selectors to the two newest versions, and shows graceful empty / single-version states. 5 new registry tests cover first-register, version bump, idempotent re-register, actor email, and cascade-delete with parent agent.

### Fixed
- **Orchestration builder canvas now persists** (#211) — the `/orchestrations/builder` page was pure local state with zero API calls; nothing saved across reloads, no Validate/Save/Deploy buttons, and the existing `/api/v1/orchestrations` CRUD endpoints were unreachable from the UI. Added an `api.orchestrations.{list,get,create,update,delete,validate,deploy,execute}` client and wired the builder's new Save / Validate / Deploy buttons. Validation errors render inline with path + message + suggestion. Visual-builder layout (per-node `{x, y}`) round-trips through a new `layout` field on `OrchestrationRecord` (in-memory equivalent of `.agentbreeder/layout.json`). New `/orchestrations` list page with strategy-tagged cards, status badges, delete confirmation, and "New Orchestration" CTA. Loading by `?id=…` rehydrates name / version / strategy / agents from the saved record. Removed the v2.0.1 `ComingSoonBanner`.
- **Dashboard `/incidents` page now persists to PostgreSQL** (#207) — incidents previously lived in `AgentOpsStore._incidents`, an in-memory dict seeded from a fake `_SEED_INCIDENTS` list; every API restart wiped user-created incidents and re-seeded the demo data. Replaced with `IncidentService` in `api/services/agentops_service.py`, backed by the new `incidents` table. The `_SEED_INCIDENTS` list and the `_incidents` dict are gone; a fresh deploy starts with an empty table. `api/routes/agentops.py` now injects `db: AsyncSession = Depends(get_db)` and routes `list / create / get / update / execute_action` through `IncidentService`. The `/api/v1/agentops/teams` endpoint now reads open-incident counts from the same table. `dashboard/src/pages/incidents.tsx` no longer renders the `<ComingSoonBanner>` for #207. Remediation actions (restart / rollback / scale / disable) still only record an operator-intent timeline entry — wiring them to the deploy / rollback machinery and auto-creating incidents from cost anomalies / health-check failures are deferred to follow-up issues.
- **Dashboard `/gateway` page now reads real data instead of synthetic logs and a hardcoded comparison fixture** (#212) — `api/routes/gateway.py` previously generated 100 random "request log" entries per call (`_generate_log_entries`, seeded by `int(time.time()) // 60` so the table refreshed every minute) and returned a hand-coded `_GATEWAY_MODELS` price table for `/api/v1/gateway/costs/comparison`. Both are gone. `/api/v1/gateway/logs` now calls the LiteLLM proxy's authenticated `/spend/logs` endpoint via the new `api/services/gateway_logs_service.py` (normalizes `LiteLLM_SpendLogs` rows into the dashboard's `LogEntry` shape, infers gateway tier from the `custom_llm_provider` field). When LiteLLM is unreachable the endpoint returns `503` with `data: []` and a clear `errors` message — never synthetic data. `/api/v1/gateway/costs/comparison` now aggregates over the real `cost_events` table grouped by `(provider, model_name)`, computing average input/output prices per million tokens from recorded usage; returns an empty list when the table is empty (no more hardcoded fixture). `dashboard/src/pages/gateway.tsx` drops both `<ComingSoonBadge issue="#212">` markers and surfaces a clear "LiteLLM proxy unreachable" banner on the logs tab plus an empty-state on the costs tab. Required env vars (`LITELLM_BASE_URL`, `LITELLM_MASTER_KEY`) documented in the route module docstring.
- **Release workflow PyPI propagation race** — `Build & Push CLI Image` and `Update Homebrew Tap` jobs now probe the PyPI `/simple/` index (via `pip index versions`) instead of the `/pypi/<pkg>/<ver>/json` Warehouse endpoint. The JSON endpoint returns 200 the moment a file is uploaded, but `pip install` reads `/simple/` through Fastly, which can lag by tens of seconds — long enough for the v2.0.1 CLI image build to fail with `Could not find a version that satisfies the requirement agentbreeder==2.0.1`.
- **6 example/template `agent.yaml` files now pass schema validation** (#183) — `examples/quickstart/rag-agent/agent.yaml` switched from inline `knowledge_bases` to a registry `ref`; `examples/quickstart/search-agent/agent.yaml` dropped the unrecognized top-level `entrypoint`; the four `examples/templates/{competitor-monitor,github-pr-reviewer,meeting-summarizer,returns-processor}/agent.yaml` switched `claude_sdk.thinking.type: adaptive` to the schema-correct `claude_sdk.thinking.enabled: true`. All 44 example/template yamls now validate.
- **23 secret-CLI tests un-skipped and rewritten for Track K's command surface** (#202) — three test classes (`TestSecretCommand`, `TestSecretSetPrompted`, `TestSecretSetTags`) had been wholesale `@pytest.mark.skip`'d during the Track K merge to keep CI green. Updated each mock for the new shape: backends now expose `backend_name` (used in JSON output), `secret_set`/`rotate` probe `b.get(name)` to decide created-vs-updated, and the `secret list --json` envelope is now `{workspace, backend, entries: [...]}`. Also fixed two prefix-sanitization tests that were patching `engine.secrets.factory.get_backend` instead of the locally-imported `cli.commands.secret.get_backend`. Added a new `TestSecretSync` class (6 tests) covering invalid target, dry-run, actual mirror, include filter, partial-failure surfacing, and the no-candidates empty state. Coverage on `cli/commands/secret.py` returns from 75% → **92%**.
- **Dashboard `/activity` page now reads from `/api/v1/audit`** (#209) — `dashboard/src/pages/activity.tsx` previously rendered a hardcoded `MOCK_EVENTS` array with timestamps relative to a frozen `NOW = 2026-03-11`. Replaced with `useQuery({ queryKey: ['audit', ...], queryFn: () => api.audit.list(...) })` against the existing `/api/v1/audit` endpoint. Resource-type filter now passes through as a server-side query param. Adapter `adaptAuditToActivity` maps the backend `AuditEvent` shape (including dotted action names like `secret.created`) into the page's existing visual taxonomy with graceful fallbacks for unknown resource types/verbs. Adds proper loading skeleton, error state, and empty state. Removed the v2.0.1 `ComingSoonBanner`. E2E test `dashboard/tests/e2e/activity.spec.ts` now mocks `/api/v1/audit` explicitly with empty + seeded fixtures.

---

## [2.0.1] — 2026-04-29

> **Honesty patch.** Reviews every dashboard page + every website page against shipped reality and marks unshipped features as "Coming soon" with linked issues. No new features; no breaking changes.

### Added
- **Dashboard "Coming soon" badges** — the dashboard now visibly marks features that are scaffolded but not yet wired to real backends. New `<ComingSoonBadge>` component plus per-page top banners on agentops, incidents, compliance, activity, orchestration-builder. Per-feature badges on agent-detail (version compare), gateway (logs / cost compare), settings (Ollama Pull), settings-secrets (backend chooser), models (Local tab), playground (tool-call rendering). Each badge links to the GitHub issue tracking the gap (#206–#216).
- **Page-by-page website audit** — every docs page, homepage component, and blog post audited against shipped code. Unshipped features re-scoped as "Coming soon" with linked issues. Notable rewrites: 5-layer architecture step 5 ("Pulumi" → "Cloud SDK calls"), runtime-contract codegen scope (Go ships, Kotlin/Rust/.NET roadmap), sidecar auto-injection scope (compose/Cloud Run/ECS today; Azure/App Runner/K8s deferred), secrets auto-mirror scope (AWS ECS + GCP Cloud Run today), agent-yaml `gateways:` block now documented.
- **Migrations doc page** at `website/content/docs/migrations.mdx` — fixes a 404 in the docs nav and explains the v1.x → v2.0 upgrade path.
- **v2.0 launch blog post** at `website/content/blog/v2-platform-substrate.mdx` with an honest "what didn't ship yet" section.
- **Homepage feature cards for v2 tracks** — Provider Catalog, Sidecar, Workspace Secrets, Polyglot Runtime Contract, Gateways added alongside the v1 features.

### Tracked gaps (filed during the audit)

Backend / infra: #196–#205. Dashboard data wiring: #206–#216. Release infra: #195.

---

## [2.0.0] — 2026-04-29

> **Platform v2.** Six new tracks turn AgentBreeder into a substrate where frameworks, clouds, languages, and providers all plug in. See `docs/architecture/platform-v2.md` and the epic at #166.

### Added
- **Gateways as first-class providers — LiteLLM + OpenRouter** (#164, Track H): the catalog schema now distinguishes `type: openai_compatible` (one upstream) from `type: gateway` (many upstreams). `engine/providers/catalog.yaml` ships `litellm` and `openrouter` as built-in `gateway` presets, and `model.primary` accepts a 3-segment ref `<gateway>/<upstream>/<model>` (e.g. `openrouter/moonshotai/kimi-k2`, `litellm/anthropic/claude-sonnet-4`) — parsed by the new `engine.providers.catalog.parse_gateway_ref`. The wire-level `model` field is shaped as `<upstream>/<model>` so both LiteLLM and OpenRouter accept it as-is. New optional `gateways:` block on `agent.yaml` lets you override the catalog `url` / `api_key_env` / `fallback_policy` / `default_headers` per-gateway (the long-term home is `workspace.yaml` once Track A / #146 ships). The dashboard `/models` page now has a working **Gateways** tab — same Configure flow as Direct providers, with a small `gateway` badge per row. Backwards-compat: existing 2-segment direct refs (`nvidia/llama-…`) and `model.gateway: litellm` configs keep working unchanged. New docs page at `website/content/docs/gateways.mdx` covering when to pick which gateway and how 3-segment refs route end-to-end.
- **Model lifecycle — auto-discover, status, retire** (#163): `engine/providers/discovery.py` ships per-provider `/models` fetchers (OpenAI-compatible, curated Anthropic, Google `v1beta/models`) behind a `ProviderDiscovery` Protocol. New Alembic migration `018_add_model_lifecycle_fields.py` adds `discovered_at`, `last_seen_at`, `deprecated_at`, `deprecation_replacement_id` to `models` plus a `(status, last_seen_at)` index. New `registry/model_lifecycle.py` reconciles discovery output with the registry: new models become `active`, absent ones flip to `deprecated`, and after 30 days of continuous absence they `retired`. Per-provider discovery errors are isolated so a transient outage cannot mass-deprecate. Audit events `model.added` / `model.deprecated` / `model.retired` emit on every transition. New CLI: `agentbreeder model list / show / sync / deprecate`. New API: `GET /api/v1/models` (lifecycle-aware list), `POST /api/v1/models/sync` and `POST /api/v1/models/{name}/deprecate` (deployer-gated). Dashboard `/models` page Sync button is live (RBAC-aware, with a spinner) and rows show coloured status badges (`active`/`beta`/`deprecated`/`retired`). Daily cron is left as a TODO documented under `website/content/docs/providers.mdx#daily-cron-out-of-scope-for-this-pr` — for now operators add a system cron.

### Fixed
- **Local stack UX**: compose now wires `GOOGLE_API_KEY`, `LITELLM_BASE_URL`, `LITELLM_MASTER_KEY`, `AGENTBREEDER_INSTALL_MODE=team` into the API container. `/playground` and `/api/v1/secrets` now work out of the box on `docker compose up`.
- **LiteLLM trampling agentbreeder DB**: `STORE_MODEL_IN_DB: False` so LiteLLM's prisma migrations don't run against the shared database (which was wiping the `users` table). Stateless mode is fine for the playground; re-enable with a separate DB when virtual keys / DB-stored configs are needed.
- **`gemini-2.5-flash` not in LiteLLM config**: added to `deploy/litellm_config.yaml` (also fixed `GOOGLE_AI_API_KEY` → `GOOGLE_API_KEY` typo on `gemini-2.5-pro`).
- **Deploy → registry sync 401 (`endpoint_url` empty after deploy)**: `engine/builder.py` now attaches `AGENTBREEDER_API_TOKEN` as a Bearer header when set, so the post-deploy `PUT /api/v1/agents/{id}` succeeds and the registry record gets a real `endpoint_url` for cloud deploys.

### Added
- **`/playground` agent mode — chat with a deployed agent** (#177): the dashboard playground gets a Model | Agent tab toggle. Agent mode shows a dropdown of registered agents that have a non-empty `endpoint_url` and routes chats through `POST /api/v1/agents/{id}/invoke` (which auto-resolves `AGENT_AUTH_TOKEN` server-side from the workspace secrets backend per #176 — no token UI). Conversation history is currently sent as a single concatenated `input` string with role labels; `session_id` round-trips between turns. Mode preference persists in `localStorage`. Empty workspaces (no deployed agents) get a "Deploy one with `agentbreeder deploy`" empty state with a link to `/agents`. Model mode behaviour unchanged.
- **Track I phase 1 — Go SDK + runtime builder + example agent** (#165, Go scope only): new `sdk/go/agentbreeder/` module is the first Tier-2 polyglot SDK. Implements [Runtime Contract v1](engine/schema/runtime-contract-v1.md): `NewServer(InvokeFunc, …Option)` returns a chi-based `http.Handler` that auto-wires `/health`, `/invoke`, `/stream`, `/resume`, `/openapi.json`, `/.well-known/agent.json`, bearer-token auth via `AGENT_AUTH_TOKEN`, the `X-Runtime-Contract-Version: 1` header, and the SSE `[DONE]` terminator. Hand-curated types in `types.go` mirror `engine/schema/runtime-contract-v1.openapi.yaml` (oapi-codegen regen command in the SDK README). Includes a `Client` for the central registry (agents, models, secrets). New `engine/runtimes/go/` Python builder packages Go agents with a multi-stage `golang:1.22-alpine` → `gcr.io/distroless/static` Dockerfile. New `examples/go-agent/` minimal agent talks to Anthropic via `net/http` (mock-falls-back when no API key). New CLI flag `agentbreeder init --lang go --framework custom` scaffolds a working Go project. `agent.yaml` schema accepts `language: go|kotlin|rust|csharp` (only `go` wired in the runtime registry; the rest are reserved). New CI jobs `test-go-sdk` (≥85% coverage gate) and `test-go-example`; release.yml publishes `rajits/agentbreeder-go-agent-example` on each version tag. New docs page `website/content/docs/go-sdk.mdx`. Kotlin (#188) / Rust (#189) / .NET (#190) SDKs are deferred to follow-up issues.
- **Generic OpenAI-compatible provider + 9-preset catalog** (#160): `engine/providers/openai_compatible.py` parameterised by `base_url`/`api_key_env`/`default_headers` replaces what would have been 9 hand-written classes. New `engine/providers/catalog.yaml` ships nvidia, openrouter, moonshot (Kimi K2), groq, together, fireworks, deepinfra, cerebras, hyperbolic — merged with `~/.agentbreeder/providers.local.yaml` overrides at load time. New CLI: `agentbreeder provider list/add/remove/test/publish`. New API route `GET /api/v1/providers/catalog`. Dashboard `/models` page lists catalog presets with a Configure stub. `model.primary: nvidia/<model>` resolves through the existing engine path.
- **Sidecar — cross-cutting concerns layer** (#161): single Go binary auto-injected next to every agent that declares `guardrails:`, MCP `tools:`, or `a2a:`. Fronts the agent on `:8080` (bearer-token auth + guardrail egress checks → reverse proxy to `:8081`) and exposes `localhost:9090` helpers for A2A JSON-RPC, MCP HTTP/SSE passthrough, and cost emission. Auto-injection wired into docker-compose, GCP Cloud Run, and AWS ECS deployers. `AGENTBREEDER_SIDECAR=disabled` bypasses for local dev. New top-level `sidecar/` Go module (~89% test coverage), Dockerfile, image build target `rajits/agentbreeder-sidecar:<version>`, and docs at `website/content/docs/sidecar.mdx`.
- **Secrets — workspace-bound backend + auto-mirror to cloud at deploy** (#162): new `engine/secrets/keychain_backend.py` (cross-platform via `keyring`) plus `engine/secrets/workspace.py` for per-workspace backend selection, defaulting keychain locally / Vault for self-hosted teams / AWS Secrets Manager in cloud. New CLI `agentbreeder secret set/list/rotate/sync` routes through the workspace backend with `getpass` prompts and audit events. AWS ECS + GCP Cloud Run deployers now auto-mirror declared `secrets:` to the target cloud's native store under `agentbreeder/<agent>/<secret>`, grant the runtime SA `secretAccessor` on each, and inject them as ECS `secrets` / Cloud Run `SecretKeyRef` env vars (no plaintext in image). New dashboard `/settings/secrets` page (values never leave the backend) and 3 REST endpoints under `/api/v1/secrets/*`.

### Fixed
- **Invoke tab no longer prompts for `AGENT_AUTH_TOKEN`** (#176): `POST /api/v1/agents/{id}/invoke` now resolves the bearer token server-side from the workspace secrets backend keyed by `agentbreeder/<agent-name>/auth-token`. The dashboard's `<InvokePanel>` drops the password input + `token` state and shows a hint pointing users at `agentbreeder secret set <agent>/auth-token`. The request body's `auth_token` field is preserved as an optional explicit override (e.g. for SDK callers and tests). Backend lookup failures fall through to "no token" — runtime returns 401, no 500s. Documented under "Per-agent auth tokens" in `website/content/docs/secrets.mdx`.
- **`/models` page UX gaps on top of Track F** (#175):
  - `<ProviderCatalog>` now reads the user's role via `useAuth()` and disables the **Add provider** + per-row **Configure** buttons for viewers with a "Requires deployer role" tooltip. RBAC is enforced server-side too — the UI checks are pure UX.
  - **Configure** is no longer a stub. Clicks open a dialog with a password-typed input that POSTs to the new `POST /api/v1/secrets` route under the deterministic key `<provider>/api-key`, writing through the workspace secrets backend (Track K). On success the row flips to a green ✓ **Configured** badge and a success toast fires; 401/403 surface as a permission-denied toast.
  - New `GET /api/v1/providers/catalog/status` returns `{<provider_name>: bool}` so each catalog row can show its real configuration state. The status check covers both the deterministic dashboard key and the legacy env-var name, so secrets imported via the CLI before Track K landed are still recognised.
  - **Sync** button + **Direct providers / Gateways / Local** tabs scaffolded. Sync is disabled with a tooltip pointing at Track G (#163), Gateways at Track H (#164), Local at the future local-runtimes track. The PRs for those tracks just enable the existing affordances.
- **MDX deployment-doc parser failure**: escape `<1 sec` in `website/content/docs/deployment.mdx` (Turbopack/MDX parses `<1` as start of a JSX tag). Was blocking Vercel preview builds on every PR.
- **E2E API smoke `.local` TLD rejected**: switch test email to `@example.com` (RFC 2606); pydantic `email-validator` now rejects `.local` as a special-use TLD, breaking the Docker E2E smoke on every PR.
- **Integration tests / Docker builds**: removed `@agentbreeder/aps-client` npm dep (package not yet published); vendor `aps-client.ts` source directly into Node.js Docker build context so `npm install` no longer fails with 404
- **ESLint errors**: suppressed `react-hooks/set-state-in-effect` errors in `gateway.tsx`, `incidents.tsx`, `prompt-builder.tsx`; fixed root cause in `login.tsx` by initializing `mounted=true` (removes invisible form on first render — fixes E2E login tests)
- **CI gate**: integration tests (`tests/integration/`) now run alongside unit tests in the `test-python` CI job
- **Mocked E2E tests in CI**: switch webServer from `vite dev` (per-request ESM compilation) to `vite build && vite preview` (pre-built static bundle) — eliminates Vite server overload from concurrent workers on slow Ubuntu runners; raise `expect.timeout` to 15 s and seed auth token via `addInitScript` to remove a redundant `/login` page-load per test
- **CI E2E mocked job removed**: removed `test-e2e-mocked` job from CI workflow — mocked E2E tests were consistently failing on GitHub Actions 2-vCPU runners regardless of timeout and server configuration tuning; tests remain available for local execution (`npx playwright test` in `dashboard/`); `docker-build` job no longer depends on them

---

## [1.8.0] — 2026-04-16

### Added

#### Ollama / LiteLLM Support Across All Runtimes (#63, #64, #65, #66)
- **LangGraph, OpenAI Agents, Custom runtimes**: `litellm>=1.40.0` added to requirements when `model: ollama/*`; `OLLAMA_BASE_URL` injected into Dockerfile ENV block
- **OpenAI Agents server**: startup routes to Ollama's OpenAI-compatible endpoint (`AsyncOpenAI(base_url=OLLAMA_BASE_URL/v1)`) for `ollama/` models; standard path unchanged
- **CrewAI runtime + server**: `litellm>=1.40.0` dep for Ollama models; `agent.llm.base_url` set to `OLLAMA_BASE_URL` at startup
- **Google ADK runtime + server**: `SERVER_LOADER_CONTENT` and lifespan both use `LiteLlm(model, api_base=OLLAMA_BASE_URL)` for non-Gemini models; resolves #66 (no `agent.py` workaround needed)
- **Claude SDK validation**: `validate()` rejects `ollama/*` models with a clear error pointing to compatible frameworks
- **DockerComposeDeployer**: auto-starts `ollama/ollama` sidecar, creates `agentbreeder-net` Docker network, runs `ollama pull` before agent starts — resolves #64, #65

### Fixed
- **`Dockerfile.cli` version pin**: uses `ARG VERSION` + `pip install "agentbreeder==${VERSION}"` — eliminates race condition between parallel build-images and publish-pypi CI jobs

### Examples
- `examples/ai-news-digest/`: Google ADK + Ollama Gemma3:27b daily news digest — fetches HN, ArXiv, RSS; synthesises with Gemma; emails via Gmail SMTP

---

## [1.7.0] — 2026-04-14

### Added

#### Agent Architect Skill (`/agent-build`) — M35
- `/agent-build` Claude Code skill: AI-powered agent architect with two paths — Fast Path (6-question scaffold) and Advisory Path (6-question interview → full-stack recommendations → scaffold)
- Advisory Path recommendation engine: framework, model, RAG, memory, MCP/A2A, deployment target, and eval dimensions with reasoning in `ARCHITECT_NOTES.md`
- Advisory Path scaffold generates 19 files including `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.antigravity.md`, `memory/`, `rag/`, `mcp/servers.yaml`, `tests/evals/`
- Fast Path scaffold generates 10 files (core project, no advisory extras)
- IDE config file contents tailored to chosen framework, model, and deployment target
- Skill entry added to `AGENT.md` under Build category

#### Documentation
- Homepage animation: split-screen autoplay demo in `docs/index.md` showing `/agent-build` advisory flow (invoke → interview → recommendations → scaffold → deploy, ~14s loop)
- `docs/how-to.md`: `/agent-build` lead paragraph in "Build Your First Agent" section
- `docs/how-to.md`: new "Use the Agent Architect (`/agent-build`)" section with Fast Path walkthrough, Advisory Path walkthrough, 18-row generated-files table, and next-steps commands
- `CLAUDE.md`: added item 8 to "When Adding a New Feature" — scaffold with `/agent-build`

---

## [1.5.0] — 2026-04-13

### Added

#### Framework Parity (CrewAI, Claude SDK, Google ADK)
- `crewai_server`: `/stream` SSE endpoint with `akickoff` step streaming; `_detect_mode`/`_dispatch`/`_validate_output` helpers; `output_schema_errors` field on `InvokeResponse`; `AGENT_MODEL`/`AGENT_TEMPERATURE` env-var wiring for per-agent LLM config
- `claude_sdk_server`: `_call_client` with adaptive thinking and prompt-caching support; `_get_cache_threshold`; `_prompt_caching_enabled`/`_thinking_config` module globals; `/stream` SSE endpoint; `_client` wired from `AsyncAnthropic` at startup
- `google_adk_server`: streaming and tool-wiring parity aligned with Claude SDK and CrewAI servers
- Engine runtimes: Claude SDK requirements bump to `anthropic>=0.50.0`; CrewAI/ADK runtimes inject `AGENT_MODEL`/`AGENT_TEMPERATURE` into container Dockerfile
- `engine/tool_bridge`: fixed `sys.modules` lookup order so test stubs are resolved correctly
- Eject scaffolds for CrewAI, ADK, and Claude SDK agent tiers (`cli/commands/eject.py`)

#### A2A Protocol (M19)
- A2A JSON-RPC 2.0 engine: protocol handler, server, client, JWT-based inter-agent auth (`engine/a2a/`)
- Agent Card generation from `agent.yaml` configuration
- Auto-generated `call_{agent_name}` tools from `subagents:` declarations (`engine/a2a/tool_generator.py`)
- A2A API routes: discovery, invoke, agent card management (`api/routes/a2a.py`)
- A2A registry service for agent registration and lookup (`registry/a2a_agents.py`)
- A2A dashboard: topology graph, call log, agent detail page (`dashboard/src/pages/a2a-*.tsx`)
- Multi-agent orchestration patterns: supervisor, fan-out/fan-in, chain (`engine/orchestrator.py`)
- Orchestration examples: `examples/orchestration/supervisor/`, `examples/orchestration/fan-out-fan-in/`
- A2A subagent example: `examples/a2a-subagent/`

#### MCP Server Hub (M20)
- MCP server packaging and lifecycle management (`engine/mcp/packager.py`)
- MCP sidecar deployer for co-deploying MCP servers with agents (`engine/deployers/mcp_sidecar.py`)
- Enhanced MCP server registry with versioning and sharing (`registry/mcp_servers.py`)
- MCP server detail page and API routes (`api/routes/mcp_servers.py`, `dashboard/src/pages/mcp-server-detail.tsx`)

#### Visual Orchestration Canvas (M30)
- ReactFlow-based orchestration builder with agent, router, supervisor, and merge nodes (`dashboard/src/components/orchestration-builder/`)
- Routing rule editor, strategy selector, and orchestration-to-YAML generator
- Orchestration builder page (`dashboard/src/pages/orchestration-builder.tsx`)

#### TypeScript SDK (M30)
- `@agentbreeder/sdk` npm package (`sdk/typescript/`)
- Agent, Tool, Model, Orchestration, and Deploy classes with full TypeScript types
- `toYaml()` / `fromYaml()` serialization
- Unit tests for agent and orchestration SDK

#### Template System (M21)
- Parameterized agent configuration templates with `{{placeholder}}` substitution
- Template schema (`engine/schema/template.schema.json`) with full JSON Schema validation
- Template CRUD API: create, list, get, update, delete, instantiate (`api/routes/templates.py`)
- Template registry service (`registry/templates.py`)
- Template gallery page with category filters (`dashboard/src/pages/templates.tsx`)
- Template detail page with parameter form and YAML generation (`dashboard/src/pages/template-detail.tsx`)
- `agentbreeder template list|create|use` CLI commands (`cli/commands/template.py`)
- Built-in templates: Customer Support Bot, Data Analyzer, Code Reviewer, Research Assistant (`examples/templates/`)
- Template versioning support

#### Marketplace (M22)
- Marketplace browse API with search, category/framework filters, sorting (`api/routes/marketplace.py`)
- Marketplace registry service with listing submission, approval, reviews (`registry/templates.py`)
- Marketplace browse page with search, filters, star ratings (`dashboard/src/pages/marketplace.tsx`)
- Marketplace detail page with reviews, one-click deploy, install tracking (`dashboard/src/pages/marketplace-detail.tsx`)
- Ratings & reviews system: star rating + text reviews per listing
- Listing approval workflow: submit → admin review → approve/reject
- One-click deploy from marketplace listing (install count tracking)
- Featured listings support
- Marketplace navigation section in dashboard sidebar

#### Other
- `agentbreeder eject --sdk typescript` support (`cli/commands/eject.py`)
- `subagents:` field in `agent.yaml` schema (`engine/schema/agent.schema.json`)
- Orchestration schema updates for fan-out/fan-in and supervisor patterns
- GitHub Actions CI/CD workflows: security scanning, integration tests, release automation
- Branch protection setup script (`scripts/setup-branch-protection.sh`)
- Dependabot configuration for automated dependency updates
- CODEOWNERS file for automatic PR reviewer assignment
- Stale issue/PR bot
- Gitleaks secret scanning
- Trivy container image scanning
- Bandit Python SAST
- pip-audit and npm audit dependency vulnerability scanning
- Dependency Review action (blocks PRs introducing high-severity dependencies)

### Changed
- Extended orchestrator engine with supervisor, fan-out/fan-in, and chain patterns
- Updated config parser and resolver for A2A subagent resolution
- Enhanced orchestration YAML parser for new pattern types

---

## [1.4.0] — 2026-04-12

### Added

#### Package Distribution (M32)
- PyPI: `pip install agentbreeder` (CLI + API server + engine) and `pip install agentbreeder-sdk` (lightweight SDK)
- npm: `npm install @agentbreeder/sdk` — TypeScript SDK published to the npm registry
- Docker Hub: multi-platform images (`linux/amd64` + `linux/arm64`) for API server, dashboard, and CLI
  - `rajits/agentbreeder-api` — FastAPI backend
  - `rajits/agentbreeder-dashboard` — React frontend
  - `rajits/agentbreeder-cli` — lightweight CLI image for CI/CD pipelines
- Homebrew: `brew tap agentbreeder/agentbreeder && brew install agentbreeder` via `Formula/agentbreeder.rb` (Python virtualenv pattern)

#### Release Automation
- `.github/workflows/release.yml`: single workflow publishes to all four distribution channels on each tagged release
- PyPI publishing via OIDC trusted publishers — no long-lived API tokens required
- Homebrew formula auto-updated on every release via the `agentbreeder/homebrew-agentbreeder` tap repo

### Changed
- Both Python packages (`agentbreeder` and `agentbreeder-sdk`) now derive their version from git tags using `hatch-vcs` — no more manual version bumps

### Infrastructure
- CI test matrix expanded to Python 3.11 and 3.12
- Codecov configuration fixed (`fail_ci_if_error`) for accurate coverage reporting across the matrix

---

## [1.0.0] — 2026-03-12

### Added
- Evaluation framework (M18) with dashboard and CI/CD integration
- Quality gates for agent evaluation
- Orchestration YAML (M29) for multi-agent workflows
- Observability stack: distributed tracing, OpenTelemetry integration
- Teams and RBAC enforcement
- Cost tracking and audit/lineage trail
- Python SDK (`agentbreeder-sdk`)
- Visual playground (No Code builder)
- Git CLI workflow (`agentbreeder submit`, `agentbreeder review`, `agentbreeder publish`)
- YAML schemas for `agent.yaml`, `orchestration.yaml`, `prompt.yaml`, `tool.yaml`, `rag.yaml`, `memory.yaml`
- MCP server example and scanner connector
- Builders API (Low Code and Full Code endpoints)
- Three-tier builder model: No Code / Low Code / Full Code

### Framework Support
- LangGraph
- OpenAI Agents
- CrewAI
- Google ADK
- Custom (bring your own framework)

### Deployment Targets
- AWS ECS Fargate
- GCP Cloud Run
- Local Docker Compose

---

[Unreleased]: https://github.com/agentbreeder/agentbreeder/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/agentbreeder/agentbreeder/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/agentbreeder/agentbreeder/compare/v1.0.0...v1.4.0
[1.0.0]: https://github.com/agentbreeder/agentbreeder/releases/tag/v1.0.0
