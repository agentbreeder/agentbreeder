# Changelog

All notable changes to Agent Garden are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

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
- `@agent-garden/sdk` npm package (`sdk/typescript/`)
- Agent, Tool, Model, Orchestration, and Deploy classes with full TypeScript types
- `toYaml()` / `fromYaml()` serialization
- Unit tests for agent and orchestration SDK

#### Other
- `garden eject --sdk typescript` support (`cli/commands/eject.py`)
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

## [1.0.0] — 2026-03-12

### Added
- Evaluation framework (M18) with dashboard and CI/CD integration
- Quality gates for agent evaluation
- Orchestration YAML (M29) for multi-agent workflows
- Observability stack: distributed tracing, OpenTelemetry integration
- Teams and RBAC enforcement
- Cost tracking and audit/lineage trail
- Python SDK (`agent-garden-sdk`)
- Visual playground (No Code builder)
- Git CLI workflow (`garden submit`, `garden review`, `garden publish`)
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

[Unreleased]: https://github.com/open-agent-garden/agent-garden/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/open-agent-garden/agent-garden/releases/tag/v1.0.0
