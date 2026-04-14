# Changelog

All notable changes to AgentBreeder are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

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
- Homebrew: `brew tap rajitsaha/agentbreeder && brew install agentbreeder` via `Formula/agentbreeder.rb` (Python virtualenv pattern)

#### Release Automation
- `.github/workflows/release.yml`: single workflow publishes to all four distribution channels on each tagged release
- PyPI publishing via OIDC trusted publishers — no long-lived API tokens required
- Homebrew formula auto-updated on every release via the `rajitsaha/homebrew-agentbreeder` tap repo

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

[Unreleased]: https://github.com/rajitsaha/agentbreeder/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/rajitsaha/agentbreeder/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/rajitsaha/agentbreeder/compare/v1.0.0...v1.4.0
[1.0.0]: https://github.com/rajitsaha/agentbreeder/releases/tag/v1.0.0
