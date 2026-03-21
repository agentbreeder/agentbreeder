# AGENT.md — AgentBreeder AI Skills & Agent Roster

> This file defines the AI agent skills, MCP server configurations, and automated workflows
> used to build, test, design, and ship AgentBreeder. Every skill is a reusable capability
> that contributors and Claude can invoke during development.

---

## 🧠 Philosophy

AgentBreeder is built with AI-assisted development from Day 1. This means:
- Every major task has a defined **skill** (a prompt + context + tools that reliably accomplishes it)
- Skills are composable — complex tasks chain simpler skills
- Skills are version-controlled here so they improve over time
- Contributors can trigger skills from Claude, Cursor, or the CLI

---

## 🗂️ Skill Categories

| Category | Purpose | When to Use |
|----------|---------|-------------|
| **Design** | UI/UX design, component specs, style decisions | Before writing any frontend code |
| **Build** | Code generation, refactoring, feature implementation | Core development work |
| **Test** | Test writing, coverage analysis, E2E flows | After implementing features |
| **Deploy** | Release prep, Docker builds, cloud deployment | Before and during releases |
| **Review** | Code review, security audit, architecture critique | Before merging PRs |
| **Docs** | Documentation, changelog, API docs | After shipping features |
| **Debug** | Root cause analysis, log triage, trace inspection | When things break |
| **Ops** | Infrastructure, monitoring, incident response | Production operations |

---

## 🎨 DESIGN Skills

### `design:component`
**Purpose:** Generate a complete UI component spec before writing code.

**Trigger:** "I need to build a [ComponentName] that [does X]"

**Skill Prompt:**
```
You are a senior UI/UX engineer working on AgentBreeder's React + Tailwind dashboard.

Context: AgentBreeder is a dark-mode-friendly developer tool dashboard. Style reference: Linear, Vercel, GitHub's design language. 
Font: Inter. Colors: slate-900 bg, blue-600 primary, green-500 success, red-500 error.

Design a complete React component for: [COMPONENT_DESCRIPTION]

Output:
1. Component purpose and user story (2-3 sentences)
2. Props interface (TypeScript)
3. Visual layout description (ASCII wireframe if helpful)
4. State management approach (local state vs React Query vs Zustand)
5. Loading, empty, and error states
6. Accessibility requirements (ARIA labels, keyboard nav)
7. Tailwind class structure for the main layout
8. Any child components needed

Do NOT write implementation code yet. Focus on the design spec.
```

**MCP Tools:** `sequential-thinking`, `filesystem` (to check existing components)

---

### `design:page-layout`
**Purpose:** Design a full page layout before implementation.

**Skill Prompt:**
```
You are designing a page for the AgentBreeder dashboard.

Design system context:
- Sidebar navigation (collapsible, 240px wide)
- Top header bar (breadcrumbs + user avatar + notifications)
- Main content area: max-w-7xl, px-6 py-8
- Card style: bg-white border border-gray-200 rounded-xl shadow-sm
- Data tables: stripe pattern, hover highlight, sortable headers
- Empty states: centered illustration + headline + CTA button

Page to design: [PAGE_NAME]
User goal: [WHAT THE USER IS TRYING TO DO]
Key data shown: [DATA_ENTITIES]

Output a complete page layout spec with:
1. Section breakdown (header, filters, main content, sidebar if any)
2. Component list with rough sizing
3. Key interactions (click → what happens, hover states, modals)
4. Mobile/responsive behavior
5. Performance considerations (what to lazy-load)
```

---

### `design:yaml-schema`
**Purpose:** Design or extend the `agent.yaml` schema.

**Skill Prompt:**
```
You are a platform engineer designing the agent.yaml configuration format.

Principles:
- Human-readable and minimal — no boilerplate
- Every field has a sensible default
- Fail fast with clear error messages
- Compatible with GitOps (version-controllable)
- Forward-compatible (new fields never break old configs)

Current schema is in: engine/config_parser.py and engine/schema/agent.schema.json

I need to add/modify: [DESCRIPTION]

Output:
1. YAML example showing the new/modified fields
2. Field definitions (name, type, required/optional, default, description)
3. Validation rules (what makes a value invalid?)
4. Migration path from old schema (if breaking change)
5. Updated JSON Schema fragment
6. 3 example configs that exercise the new fields
```

**MCP Tools:** `filesystem` (read current schema), `sequential-thinking`

---

## 🏗️ BUILD Skills

### `build:deployer`
**Purpose:** Implement a new cloud deployer (e.g., Azure Container Apps, Oracle Cloud, Render).

**Skill Prompt:**
```
You are implementing a new deployer for AgentBreeder. Study the existing deployers first.

Read these files before starting:
- engine/deployers/base.py (abstract interface)
- engine/deployers/docker_compose.py (reference implementation — local)
- engine/deployers/gcp_cloudrun.py (reference implementation — cloud)

New deployer: [CLOUD_PROVIDER] / [SERVICE_NAME]

Implement:
1. Class inheriting from BaseDeployer
2. All required methods: provision(), deploy(), health_check(), teardown(), get_logs()
3. Pulumi resource definitions for the cloud infrastructure
4. IAM/permission setup
5. Auto-scaling configuration
6. The sidecar injection pattern (must match existing deployers)
7. Return format matching DeployResult schema

Requirements:
- All I/O must be async
- Comprehensive error handling with typed exceptions
- Log every significant step with structured logging
- No hard-coded values — all config from AgentConfig
- Add deployer to the deployer registry in engine/deployers/__init__.py
```

**MCP Tools:** `filesystem`, `fetch` (cloud provider docs), `sequential-thinking`, `docker`

---

### `build:runtime`
**Purpose:** Add a new agent framework runtime builder.

**Skill Prompt:**
```
You are adding support for a new agent framework to AgentBreeder.

Read these files first:
- engine/runtimes/base.py (RuntimeBuilder interface)
- engine/runtimes/langgraph.py (reference implementation)
- engine/runtimes/openai_agents.py (second reference)

New framework: [FRAMEWORK_NAME] (version [VERSION])

Implement:
1. RuntimeBuilder subclass in engine/runtimes/[framework_name].py
2. validate() — check agent code is valid for this framework
3. build() — generate Dockerfile and build the container image
4. get_entrypoint() — framework-specific startup command
5. get_requirements() — pip/npm dependencies to inject
6. generate_sidecar_config() — OTel integration for this framework

Also:
- Add a complete working example in examples/[framework-name]-agent/
- Add framework to the enum in engine/schema/agent.schema.json
- Write unit tests in tests/unit/runtimes/test_[framework_name].py
- Update README.md supported stack table

The example agent must: receive a message, call at least one tool, return a response.
```

**MCP Tools:** `filesystem`, `fetch` (framework docs), `docker` (test the build)

---

### `build:sdk-orchestration`
**Purpose:** Extend the Full Code Orchestration SDK with a new strategy, router type, or advanced pattern.

**Skill Prompt:**
```
You are extending the AgentBreeder Full Code Orchestration SDK.

Read these files first:
- sdk/python/agenthub/orchestration.py (Python SDK — Orchestration, Pipeline, FanOut, Supervisor, Router classes)
- sdk/typescript/src/orchestration.ts (TypeScript SDK — same API surface)
- engine/orchestration_parser.py (YAML config models — OrchestrationConfig, strategies)
- engine/orchestrator.py (runtime execution — _execute_* methods)
- engine/schema/orchestration.schema.json (JSON Schema for validation)
- tests/unit/test_sdk_orchestration.py (test patterns to follow)

Task: [DESCRIPTION]

Requirements:
- Python SDK and TypeScript SDK must have matching API surfaces
- New strategy names must be added to VALID_STRATEGIES and the JSON Schema enum
- New subclasses follow the builder pattern (all methods return self)
- YAML round-trip: to_yaml() + from_yaml() must be faithful (test this explicitly)
- validate() must catch invalid configs before deploy
- Engine execution: add _execute_<strategy>() in engine/orchestrator.py
- Tests: follow test_sdk_orchestration.py — test builder, validate, yaml round-trip, deploy
- TypeScript tests: add to sdk/typescript/tests/orchestration.test.ts
```

**MCP Tools:** `filesystem` (read existing SDK + engine), `sequential-thinking` (plan before coding)

---

### `build:api-endpoint`
**Purpose:** Implement a new REST API endpoint.

**Skill Prompt:**
```
You are implementing a new API endpoint for AgentBreeder's FastAPI backend.

Conventions (read api/routes/agents.py for the pattern):
- Router: APIRouter with prefix and tags
- Always use dependency injection for db session and current_user
- Pydantic schemas for request/response in api/models/
- Business logic in api/services/, never in route handlers
- Return consistent {data, meta, errors} shape
- Log at INFO level for every request with relevant context
- Use HTTP 422 for validation errors, 403 for auth, 404 for not found

Endpoint to implement: [METHOD] [PATH]
Purpose: [WHAT IT DOES]
Request body: [SCHEMA OR DESCRIPTION]
Response: [SCHEMA OR DESCRIPTION]
Auth required: [YES/NO, which roles]

Implement:
1. Pydantic request/response models
2. Service class method with business logic
3. Route handler (thin — delegates to service)
4. SQLAlchemy query/mutation
5. Unit test for the service method
6. Integration test for the route
```

**MCP Tools:** `filesystem`, `postgres` (validate schema), `sequential-thinking`

---

### `build:cli-command`
**Purpose:** Add or modify a CLI command.

**Skill Prompt:**
```
You are implementing a CLI command for the AgentBreeder CLI (built with Typer + Rich).

Style guide (read cli/commands/deploy.py for the pattern):
- Use Rich for ALL terminal output (no plain print())
- Progress bars for long-running operations (rich.progress)
- Tables for list output (rich.table)
- Panels for important messages (rich.panel)
- Color convention: green=success, yellow=warning, red=error, blue=info
- Every command has --help with clear description and examples
- Commands must work with --json flag for scripting/CI use
- Exit codes: 0=success, 1=user error, 2=system error

Command to implement: agentbreeder [COMMAND_NAME]
Purpose: [WHAT IT DOES]
Arguments: [LIST OF ARGS]
Options/flags: [LIST OF OPTIONS]

Implement:
1. Typer command function with full docstring
2. Input validation with helpful error messages
3. Progress display for multi-step operations
4. Success and error output formatted with Rich
5. --json output mode
6. Unit test for the command logic
7. Add to cli/main.py command registration
```

**MCP Tools:** `filesystem`, `sequential-thinking`

---

### `build:connector`
**Purpose:** Build a new integration connector (observability, gateway, git scanner, etc.).

**Skill Prompt:**
```
You are building a new connector for AgentBreeder's connector system.

Read these files first:
- connectors/base.py (BaseConnector interface)
- connectors/litellm/ (reference implementation — gateway connector)
- connectors/mcp_scanner/ (reference implementation — MCP server discovery)

New connector: [TOOL_NAME]
Connector type: [gateway | observability | git_scanner | mcp_scanner | custom]
Purpose: [WHAT DATA IT INGESTS OR WHAT INTEGRATION IT PROVIDES]

Implement:
1. Connector class in connectors/[tool_name]/connector.py
2. Configuration model (API keys, endpoints, polling interval)
3. Data ingestion/sync logic
4. Registry population (what entities does it create/update?)
5. Health check (is the external tool reachable?)
6. Error handling and retry logic
7. Unit tests with mocked external API responses
8. README.md in connectors/[tool_name]/ with setup instructions
```

---

### `build:migration`
**Purpose:** Create a safe database migration.

**Skill Prompt:**
```
You are writing an Alembic database migration for AgentBreeder.

Rules:
- Migrations must be reversible (always implement downgrade())
- Never drop columns in a single migration — deprecate first, drop in a later release
- Add columns as nullable first, then backfill, then add NOT NULL in a separate migration
- Always test upgrade() AND downgrade() before merging
- Migration description must be clear: what entity, what change, why

Change needed: [DESCRIPTION]

Provide:
1. Alembic migration script (upgrade + downgrade)
2. SQLAlchemy model change
3. Pydantic schema change (if applicable)
4. Data backfill script (if migrating existing data)
5. Rollback instructions
6. Any index or constraint changes
```

**MCP Tools:** `postgres` (inspect current schema), `filesystem`

---

## 🧪 TEST Skills

### `test:unit`
**Purpose:** Write comprehensive unit tests for a module or function.

**Skill Prompt:**
```
You are writing unit tests for AgentBreeder using pytest.

Testing philosophy:
- Test behavior, not implementation
- One assertion per test (or closely related assertions)
- Test the happy path, edge cases, and failure modes
- Mock all external dependencies (DB, cloud APIs, filesystem)
- Tests must run in < 100ms each (no real I/O)
- Use pytest fixtures for shared setup
- Name tests: test_[unit]_[scenario]_[expected_result]

Module to test: [MODULE_PATH]
Function/class: [NAME]

Write tests covering:
1. Happy path (valid inputs → expected output)
2. All validation error cases
3. All exception paths
4. Boundary conditions
5. Any async behavior
6. Any side effects (what should/shouldn't be called)

Target: 100% branch coverage for the tested code.
```

**MCP Tools:** `filesystem` (read the code to test)

---

### `test:integration`
**Purpose:** Write integration tests for API endpoints.

**Skill Prompt:**
```
You are writing integration tests for AgentBreeder's FastAPI endpoints.

Setup: Tests use a real PostgreSQL test database and Redis. Cloud APIs are mocked.
Test client: FastAPI TestClient (synchronous) or httpx AsyncClient (async).
Fixtures in: tests/conftest.py

Read the endpoint implementation first, then write tests covering:
1. Successful request with valid data
2. Authentication required (401 when no token)
3. Authorization failure (403 when wrong role)
4. Validation errors (422 with helpful messages)
5. Not found cases (404)
6. Any business logic edge cases

Endpoint: [METHOD] [PATH]
Implementation: [FILE_PATH]

Also verify:
- Response schema matches documented contract
- Database state is correct after mutations
- Audit log entries are created
- Registry is updated (for deploy endpoints)
```

**MCP Tools:** `filesystem`, `postgres`

---

### `test:e2e`
**Purpose:** Write Playwright E2E tests for dashboard flows.

**Skill Prompt:**
```
You are writing Playwright E2E tests for the AgentBreeder dashboard.

Setup:
- Tests in tests/e2e/
- Base URL: http://localhost:3000
- Test user seeded: admin@test.com / testpassword
- Playwright fixtures in tests/e2e/fixtures.ts

Write E2E tests for: [USER_FLOW]

For each test:
1. Describe the user story being tested
2. Start from a specific page/state
3. Test the complete flow including loading states
4. Assert on final state (URL, visible text, data in table)
5. Test failure states (API error handling, validation messages)
6. Include accessibility checks (axe-core) where applicable

Target flows for v0.1:
- Login and dashboard load
- Browse agent registry
- View agent detail page
- Deploy an agent (mock cloud APIs)
- Search the registry
```

**MCP Tools:** `playwright`, `filesystem`

---

### `test:security`
**Purpose:** Security review and penetration test for new features.

**Skill Prompt:**
```
You are performing a security review of new AgentBreeder code.

Check for:
1. SQL injection (raw queries, string interpolation in queries)
2. Broken authentication (JWT validation, session handling)
3. RBAC bypass (can users access data from other teams?)
4. Secrets exposure (API keys in logs, error messages, responses)
5. SSRF (user-controlled URLs in connectors, MCP scanner)
6. Injection in YAML parsing (safe_load vs unsafe load)
7. Path traversal (user-controlled file paths)
8. Rate limiting (are expensive operations protected?)
9. Input size limits (can a huge YAML crash the server?)
10. Dependency confusion (are registry refs validated?)

Code to review: [FILE_OR_PR]

Output:
- List of findings with severity (Critical/High/Medium/Low)
- Code snippets showing the vulnerability
- Recommended fix for each finding
- Test case to verify the fix
```

---

## 🚀 DEPLOY Skills

### `deploy:release-prep`
**Purpose:** Prepare a new release — changelog, version bump, tag.

**Skill Prompt:**
```
You are preparing the AgentBreeder [VERSION] release.

Read:
- ROADMAP.md (what was planned for this milestone?)
- Git log since last tag: git log [LAST_TAG]..HEAD --oneline
- All merged PRs since last release

Generate:
1. CHANGELOG.md entry with sections:
   - ✨ New Features
   - 🐛 Bug Fixes
   - 🔧 Improvements
   - ⚠️ Breaking Changes (if any)
   - 📦 Dependencies Updated
2. Version bump (pyproject.toml, package.json, __version__.py)
3. Release announcement draft (for GitHub Releases + Discord)
4. Migration guide (if breaking changes)
5. Checklist of manual verification steps before publishing

Tag format: v[MAJOR].[MINOR].[PATCH]
```

**MCP Tools:** `filesystem`, `github`

---

### `deploy:docker`
**Purpose:** Build, test, and optimize Docker images.

**Skill Prompt:**
```
You are building Docker images for AgentBreeder.

Current state: There is a single `Dockerfile` at the project root for the API. The dashboard builds via `npm run build`. Local dev uses `deploy/docker-compose.yml`.

Build targets:
- api: FastAPI backend (the existing Dockerfile)
- dashboard: React frontend (nginx-served static files)

Future targets (not yet implemented):
- cli: CLI tool (packaged as a standalone binary)
- sidecar: Observability sidecar (must be < 50MB, minimal dependencies)

For each image:
1. Multi-stage build to minimize final image size
2. Non-root user for security
3. Health check instruction
4. Build args for version tagging
5. Layer caching optimization (deps before code)
6. .dockerignore to exclude dev files

Build command: docker buildx build --platform linux/amd64,linux/arm64 -t agentbreeder/[IMAGE]:[VERSION] .
```

**MCP Tools:** `docker`, `filesystem`

---

### `deploy:helm`
**Purpose:** Create or update the Helm chart for Kubernetes deployment.

**Skill Prompt:**
```
You are creating or maintaining the AgentBreeder Helm chart in deploy/helm/.

> **Status:** Helm chart does not exist yet. Currently, local deployment uses `deploy/docker-compose.yml`. This skill is for creating the initial chart or maintaining it once created.

The chart should deploy: API server, Redis, PostgreSQL (or external), dashboard.

Target chart structure: deploy/helm/agentbreeder/

Task: [WHAT NEEDS TO CHANGE]

For any Helm work:
1. Maintain backward compatibility in values.yaml
2. Add/update Chart.yaml version (SemVer)
3. Template best practices: always use .Release.Namespace, quote strings
4. RBAC: ServiceAccount, Role, RoleBinding for minimal permissions
5. Security: non-root containers, readOnlyRootFilesystem where possible
6. Resource requests/limits in values.yaml with sane defaults
7. Horizontal Pod Autoscaler template
8. PodDisruptionBudget for HA deployments
9. NetworkPolicy template (optional, disabled by default)
10. Verify with: helm lint deploy/helm/agentbreeder/
            helm template agentbreeder deploy/helm/agentbreeder/ --debug
```

**MCP Tools:** `filesystem`, `docker`

---

## 📝 DOCS Skills

### `docs:api`
**Purpose:** Generate or update API documentation.

**Skill Prompt:**
```
You are writing API documentation for AgentBreeder.

Style: Developer-first, with curl examples for every endpoint.
Format: OpenAPI 3.0 annotations in FastAPI route handlers.

For the endpoint [METHOD] [PATH]:
1. Update the FastAPI route with complete docstring
2. Add response_model with full schema
3. Add OpenAPI examples (request + response)
4. Document all error responses (403, 404, 422, 500)
5. Add to the relevant section in docs/ (create docs/api-reference.md if it doesn't exist)
6. Write a "Quick Example" showing a complete use case

Example format:
"""
Deploy an agent to production.

Triggers the full deployment pipeline: validates RBAC, resolves dependencies,
builds the container, provisions cloud infrastructure, and registers the agent.

Returns a job ID that can be polled for status.
"""
```

---

### `docs:contributor-guide`
**Purpose:** Write contributor documentation for a specific area.

**Skill Prompt:**
```
You are writing contributor documentation for AgentBreeder.

Area: [e.g., "Adding a new cloud deployer" or "Creating an agent template"]

Write a complete guide including:
1. Prerequisites (tools, accounts, knowledge)
2. Local development setup for this area
3. Step-by-step implementation walkthrough
4. Testing requirements (what tests must pass)
5. Code review checklist
6. How to submit the contribution (PR template)
7. Common pitfalls and how to avoid them

Use a real, simple example throughout. The guide should be followable by a
competent engineer who has never contributed to this project before.
```

---

## 🔍 DEBUG Skills

### `debug:deploy-failure`
**Purpose:** Diagnose and fix a failed agent deployment.

**Skill Prompt:**
```
You are debugging a failed AgentBreeder deployment.

Information available:
- Deploy job ID: [ID]
- Error output: [ERROR]
- Last successful deploy: [DETAILS]
- Agent config (agent.yaml): [CONTENT]
- Cloud provider: [AWS/GCP/K8s]

Diagnose:
1. Which step in the pipeline failed? (Parse → RBAC → Resolve → Build → Provision → Deploy → Register)
2. Is this a config error, permissions error, cloud API error, or bug?
3. What is the root cause?
4. What is the fix?
5. How to verify the fix worked?

Also check:
- Are cloud credentials configured and not expired?
- Does the registry have the required tool/model refs?
- Is the framework version compatible?
- Are there resource quota issues in the cloud account?
```

**MCP Tools:** `postgres` (check deploy job logs), `docker` (check container build logs), `fetch` (cloud provider status)

---

### `debug:performance`
**Purpose:** Identify and fix performance bottlenecks.

**Skill Prompt:**
```
You are investigating a performance issue in AgentBreeder.

Symptom: [DESCRIPTION — e.g., "Registry search takes 3+ seconds for large orgs"]

Investigate:
1. Profile the slow path (use cProfile or py-spy)
2. Check database query count and timing (SQLAlchemy echo=True)
3. Check N+1 query patterns
4. Check missing database indexes
5. Check Redis cache hit rate
6. Check any synchronous I/O in async code

For each finding, provide:
- Root cause
- Fix (code change or index)
- Expected improvement
- How to measure the improvement

Target: p99 latency for registry search < 200ms at 10,000 agents.
```

**MCP Tools:** `postgres`, `filesystem`, `sequential-thinking`

---

## 🔧 COMPOSITE Workflows

These workflows chain multiple skills together for larger tasks.

### `workflow:new-framework-support`
Adds complete support for a new agent framework. Chains:
1. `design:yaml-schema` — add framework to the enum
2. `build:runtime` — implement the runtime builder
3. `test:unit` — unit tests for the runtime
4. `test:integration` — integration test for deploy with this framework
5. `deploy:docker` — verify the Docker build works
6. `docs:contributor-guide` — how to use this framework
7. `deploy:release-prep` — add to changelog

### `workflow:new-cloud-deployer`
Adds complete support for a new cloud provider. Chains:
1. `design:yaml-schema` — add cloud to the deploy.cloud enum
2. `build:deployer` — implement the deployer
3. `test:unit` — unit tests for the deployer
4. `test:integration` — integration test with mocked cloud API
5. `deploy:helm` — add any Helm chart changes
6. `docs:contributor-guide` — setup guide for this cloud
7. `deploy:release-prep` — add to changelog

### `workflow:new-dashboard-page`
Adds a complete new dashboard page. Chains:
1. `design:page-layout` — full page design spec
2. `design:component` × N — each component spec
3. `build:api-endpoint` — any new API endpoints needed
4. Component implementation (React + TypeScript + Tailwind)
5. `test:e2e` — Playwright tests for the page
6. Accessibility audit

### `workflow:milestone`
**Purpose:** Start a new milestone with a clean slate. Run `/clear` before beginning work on any new milestone to reset context and ensure focused, clean execution.

**Trigger:** "Start milestone X", "Next milestone", or `/milestone`

**Chains:**
1. `/clear` — reset conversation context
2. Re-read ROADMAP.md to identify the next milestone tasks
3. Re-read CLAUDE.md and AGENT.md for coding standards
4. Begin implementation

---

### `workflow:launch`
**Purpose:** The pre-release quality gate. Runs the full chain: unit tests with 95%+ coverage, security audit with critical/high fixes, commit, and push. Use this before any release, merge to main, or public milestone.

**Trigger:** "Launch it", "Ship it", "Prepare for release", or `/launch`

**Chains:**
1. `launch:test` — run all unit tests with 95%+ coverage enforcement
2. `launch:security` — run security audit, fix all critical and high vulnerabilities
3. `launch:commit` — stage, commit, and push all changes
4. `launch:verify` — final verification that everything is clean

---

## 🚀 LAUNCH Skills

### `launch:test`
**Purpose:** Run the full test suite and enforce 95%+ code coverage. Fail fast if coverage drops below threshold.

**Skill Prompt:**
```
You are running the AgentBreeder test suite before a release.

Steps:
1. Run all unit tests with coverage:
   pytest tests/unit/ --cov=. --cov-report=term-missing --cov-report=html --cov-fail-under=95

2. If coverage is below 95%:
   - Identify all uncovered files and lines from the coverage report
   - Write missing unit tests to bring coverage above 95%
   - Focus on: untested branches, exception paths, edge cases
   - Re-run tests after adding new tests to verify coverage target is met

3. If any tests fail:
   - Read the failure output carefully
   - Fix the root cause in the source code (not by deleting or skipping tests)
   - Re-run to verify the fix
   - Never mark a test as @pytest.mark.skip to pass the gate

4. Run integration tests if available:
   pytest tests/integration/

5. Run type checking:
   mypy .

6. Run linting:
   ruff check . && ruff format --check .

Output:
- Coverage percentage per module
- List of any tests added
- List of any source fixes made
- Final PASS/FAIL status

The gate PASSES only when:
- All unit tests pass
- Coverage >= 95%
- mypy has zero errors
- ruff has zero warnings
```

**MCP Tools:** `filesystem`

---

### `launch:security`
**Purpose:** Run a comprehensive security audit and fix all critical and high severity vulnerabilities before release.

**Skill Prompt:**
```
You are performing a pre-release security audit of the AgentBreeder codebase.

Phase 1 — Automated Scanning:
1. Check Python dependencies for known CVEs:
   pip audit
   safety check

2. Check JavaScript dependencies (if dashboard exists):
   cd dashboard && npm audit

3. Check for secrets accidentally committed:
   - Scan all files for patterns: API keys, tokens, passwords, private keys
   - Check .env files are in .gitignore
   - Check no credentials in YAML examples or test fixtures

Phase 2 — Code Review (OWASP Top 10):
Scan ALL Python and TypeScript source files for:

1. SQL Injection
   - Any raw SQL queries or string interpolation in queries
   - All database access must use SQLAlchemy ORM with parameterized queries
   - Fix: replace with SQLAlchemy query builder

2. Broken Authentication
   - JWT validation: verify signature, expiration, issuer
   - Session handling: secure cookies, proper logout
   - Fix: add missing validation checks

3. Broken Access Control (RBAC Bypass)
   - Every API endpoint must check permissions
   - Users must not access data from other teams
   - Fix: add RBAC middleware or decorator

4. Injection in YAML Parsing
   - Must use yaml.safe_load() everywhere, never yaml.load() or yaml.unsafe_load()
   - Fix: replace unsafe calls

5. Cross-Site Scripting (XSS)
   - All user input rendered in dashboard must be escaped
   - No dangerouslySetInnerHTML without sanitization
   - Fix: use DOMPurify or remove unsafe rendering

6. Server-Side Request Forgery (SSRF)
   - User-controlled URLs in connectors and MCP scanner
   - Fix: validate URLs against allowlist, block internal IPs

7. Path Traversal
   - User-controlled file paths in config parser or template loader
   - Fix: canonicalize paths and validate within allowed directories

8. Secrets Exposure
   - API keys in error messages, logs, or API responses
   - Fix: sanitize error outputs, use structured logging

9. Input Size Limits
   - Large YAML files, huge API payloads that could cause DoS
   - Fix: add size limits to API endpoints and config parser

10. Dependency Confusion
    - Registry references (ref: tools/...) must be validated
    - Fix: validate refs against allowlist of known registries

Phase 3 — Fix:
For each finding:
- Classify severity: Critical / High / Medium / Low
- Fix ALL Critical and High issues immediately
- Document Medium and Low as GitHub issues for later
- Write a regression test for each fix

Phase 4 — Verify:
- Re-run all scans after fixes
- Verify no new issues introduced
- Run full test suite to confirm fixes don't break functionality

Output:
- Table of findings: [Severity | Location | Issue | Status (Fixed/Deferred)]
- Code diffs for all fixes
- List of regression tests added
- Final PASS/FAIL — PASSES only when zero Critical and zero High issues remain
```

**MCP Tools:** `filesystem`, `sequential-thinking`

---

### `launch:commit`
**Purpose:** Stage all changes, create a well-formatted commit, and push to the remote.

**Skill Prompt:**
```
You are preparing a release commit for AgentBreeder.

Steps:
1. Pre-commit checks:
   - Run ruff check . && ruff format .
   - Run mypy .
   - Run pytest tests/unit/ --cov=. --cov-fail-under=95
   - If any check fails, fix the issue first — never skip checks

2. Review changes:
   - Run git status to see all modified and untracked files
   - Run git diff to review all changes
   - Verify no secrets, credentials, or .env files are staged
   - Verify no large binary files are accidentally included
   - Verify no debug print statements or TODO hacks remain

3. Stage files:
   - Add specific files by name (never git add -A blindly)
   - Exclude: .env, __pycache__/, node_modules/, .coverage, htmlcov/
   - Double-check staged files with git diff --cached

4. Commit:
   - Write a clear commit message following Conventional Commits
   - First line: type(scope): concise description (< 70 chars)
   - Body: what changed and why (bullet points for multi-file changes)
   - Footer: Co-Authored-By if applicable

5. Push:
   - Push to the current branch with -u flag
   - Verify push succeeded
   - Report the commit SHA and remote URL

Output:
- List of files committed
- Commit message used
- Push result (success/failure)
- Remote URL for verification
```

**MCP Tools:** `filesystem`

---

### `launch:verify`
**Purpose:** Final post-push verification that everything is clean and release-ready.

**Skill Prompt:**
```
You are performing the final pre-release verification for AgentBreeder.

Checklist:
1. Git status is clean (no uncommitted changes)
2. All tests pass on the pushed code:
   - pytest tests/unit/ --cov=. --cov-fail-under=95
3. No security vulnerabilities:
   - pip audit (zero critical/high)
4. Linting is clean:
   - ruff check .
   - mypy .
5. Documentation is consistent:
   - README.md references match actual files
   - All linked files in README exist
   - ROADMAP.md milestone items match implemented features
6. Docker build works (if applicable):
   - docker compose build
7. Git log shows clean history:
   - No merge conflicts
   - No WIP commits
   - Commit messages follow conventions

Output:
- Checklist with PASS/FAIL for each item
- Overall verdict: READY TO RELEASE / NOT READY (with blockers listed)
```

**MCP Tools:** `filesystem`, `docker`

---

## 📋 Skill Usage Quick Reference

```bash
# In Claude / Cursor, invoke skills with:
"Use the build:deployer skill to add Azure Container Apps support"
"Use the test:e2e skill to write tests for the agent list page"
"Use the design:component skill to design the CostDashboard component"
"Use the workflow:new-framework-support workflow for Semantic Kernel"

# Launch workflow (pre-release quality gate):
"Use the workflow:launch to ship this release"
"Run launch:test to verify coverage is above 95%"
"Run launch:security to audit and fix vulnerabilities"
"Run launch:commit to commit and push"

# For complex multi-step work, always start with:
"Use sequential-thinking MCP to plan this before we start coding"
```

---

## 🛡️ Agent Safety Rules

When any AI agent is working on AgentBreeder code:

1. **Never deploy to production** without explicit human confirmation
2. **Never modify the database schema** without reviewing the migration in `postgres` MCP first
3. **Never change RBAC logic** without a security review (`test:security` skill)
4. **Never commit credentials** — always use environment variables
5. **Never merge a PR** that breaks the `agentbreeder deploy` happy path test
6. **Always run `pytest tests/unit/` before suggesting a PR** is ready
7. **Always check ROADMAP.md** before adding a feature to ensure it's planned for the current milestone
8. **Always run `workflow:launch`** before any release — tests, security audit, and clean commit are mandatory
9. **Never ship with coverage below 95%** — write the missing tests, don't lower the bar
10. **Never ship with Critical or High security findings** — fix them or don't release
11. **Never ship AI-slop UI** — every frontend component must be innovative, distinctive, and out-of-the-box. No generic templates, no cookie-cutter layouts. Design like a senior product designer, not a chatbot.
12. **Always test UI with Playwright** — every UI feature must have full Playwright E2E tests before it is considered done. No exceptions.
13. **Always /clear before a new milestone** — start each milestone with a fresh context to avoid stale assumptions and context bleed

---

*Last updated: March 2026 — AgentBreeder v0.1*
