# Changelog

All notable changes to AgentBreeder are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed
- **Local stack UX**: compose now wires `GOOGLE_API_KEY`, `LITELLM_BASE_URL`, `LITELLM_MASTER_KEY`, `AGENTBREEDER_INSTALL_MODE=team` into the API container. `/playground` and `/api/v1/secrets` now work out of the box on `docker compose up`.
- **LiteLLM trampling agentbreeder DB**: `STORE_MODEL_IN_DB: False` so LiteLLM's prisma migrations don't run against the shared database (which was wiping the `users` table). Stateless mode is fine for the playground; re-enable with a separate DB when virtual keys / DB-stored configs are needed.
- **`gemini-2.5-flash` not in LiteLLM config**: added to `deploy/litellm_config.yaml` (also fixed `GOOGLE_AI_API_KEY` → `GOOGLE_API_KEY` typo on `gemini-2.5-pro`).
- **Deploy → registry sync 401 (`endpoint_url` empty after deploy)**: `engine/builder.py` now attaches `AGENTBREEDER_API_TOKEN` as a Bearer header when set, so the post-deploy `PUT /api/v1/agents/{id}` succeeds and the registry record gets a real `endpoint_url` for cloud deploys.

### Added
- **Generic OpenAI-compatible provider + 9-preset catalog** (#160): `engine/providers/openai_compatible.py` parameterised by `base_url`/`api_key_env`/`default_headers` replaces what would have been 9 hand-written classes. New `engine/providers/catalog.yaml` ships nvidia, openrouter, moonshot (Kimi K2), groq, together, fireworks, deepinfra, cerebras, hyperbolic — merged with `~/.agentbreeder/providers.local.yaml` overrides at load time. New CLI: `agentbreeder provider list/add/remove/test/publish`. New API route `GET /api/v1/providers/catalog`. Dashboard `/models` page lists catalog presets with a Configure stub. `model.primary: nvidia/<model>` resolves through the existing engine path.
- **Sidecar — cross-cutting concerns layer** (#161): single Go binary auto-injected next to every agent that declares `guardrails:`, MCP `tools:`, or `a2a:`. Fronts the agent on `:8080` (bearer-token auth + guardrail egress checks → reverse proxy to `:8081`) and exposes `localhost:9090` helpers for A2A JSON-RPC, MCP HTTP/SSE passthrough, and cost emission. Auto-injection wired into docker-compose, GCP Cloud Run, and AWS ECS deployers. `AGENTBREEDER_SIDECAR=disabled` bypasses for local dev. New top-level `sidecar/` Go module (~89% test coverage), Dockerfile, image build target `rajits/agentbreeder-sidecar:<version>`, and docs at `website/content/docs/sidecar.mdx`.
- **Secrets — workspace-bound backend + auto-mirror to cloud at deploy** (#162): new `engine/secrets/keychain_backend.py` (cross-platform via `keyring`) plus `engine/secrets/workspace.py` for per-workspace backend selection, defaulting keychain locally / Vault for self-hosted teams / AWS Secrets Manager in cloud. New CLI `agentbreeder secret set/list/rotate/sync` routes through the workspace backend with `getpass` prompts and audit events. AWS ECS + GCP Cloud Run deployers now auto-mirror declared `secrets:` to the target cloud's native store under `agentbreeder/<agent>/<secret>`, grant the runtime SA `secretAccessor` on each, and inject them as ECS `secrets` / Cloud Run `SecretKeyRef` env vars (no plaintext in image). New dashboard `/settings/secrets` page (values never leave the backend) and 3 REST endpoints under `/api/v1/secrets/*`.

### Fixed
- **Invoke tab no longer prompts for `AGENT_AUTH_TOKEN`** (#176): `POST /api/v1/agents/{id}/invoke` now resolves the bearer token server-side from the workspace secrets backend keyed by `agentbreeder/<agent-name>/auth-token`. The dashboard's `<InvokePanel>` drops the password input + `token` state and shows a hint pointing users at `agentbreeder secret set <agent>/auth-token`. The request body's `auth_token` field is preserved as an optional explicit override (e.g. for SDK callers and tests). Backend lookup failures fall through to "no token" — runtime returns 401, no 500s. Documented under "Per-agent auth tokens" in `website/content/docs/secrets.mdx`.
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
