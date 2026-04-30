# Contributing to AgentBreeder

AgentBreeder is an open-source platform for deploying, governing, and orchestrating enterprise AI agents. Every contribution — whether it's a new cloud deployer, a framework runtime, a community template, or a typo fix — makes the platform better for everyone.

Read [CLAUDE.md](CLAUDE.md) before diving in. It's the authoritative guide to how this codebase is structured and what the architecture principles are.

---

## Ways to Contribute

| Contribution | Difficulty | Impact |
|---|:---:|:---:|
| Report a bug | Easy | High |
| Fix a [`good first issue`](https://github.com/agentbreeder/agentbreeder/issues?q=label%3A%22good+first+issue%22) | Easy | Medium |
| Add an agent template to the Marketplace | Easy | High |
| Improve documentation | Easy | High |
| Add a connector (LiteLLM, OpenRouter, MCP) | Medium | High |
| Add a framework runtime (`engine/runtimes/`) | Medium | Very High |
| Add a cloud deployer (`engine/deployers/`) | Medium | Very High |
| Add an orchestration strategy | Hard | Very High |
| Suggest a feature | Easy | Medium |

Browse open issues at [github.com/agentbreeder/agentbreeder/issues](https://github.com/agentbreeder/agentbreeder/issues) or start a discussion at [github.com/agentbreeder/agentbreeder/discussions](https://github.com/agentbreeder/agentbreeder/discussions).

---

## Security Vulnerabilities

**Do not open a public GitHub issue for security vulnerabilities.**

Report them privately at **saha.rajit@gmail.com** with the subject line `[SECURITY] AgentBreeder`. Include:
- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact

You'll receive a response within 48 hours. We follow responsible disclosure and will credit reporters in release notes.

---

## Contributing to the Registry

The AgentBreeder registry is more useful when it contains real, battle-tested agents that teams can learn from and adapt. We welcome seed agent contributions.

### What qualifies as a good registry agent

- **Real use case** — solves an actual business problem, not a toy example
- **Tested** — the author has deployed and run this agent (or a close variant) in production or staging
- **Complete** — includes `team`, `owner`, `tags`, at least one `tool` or `knowledge_base`, and a guardrail where appropriate
- **Well-commented** — non-obvious fields are annotated so readers understand the intent

### How to submit a registry seed

1. Copy or write an `agent.yaml` for your agent in the `seeds/` directory
2. Validate it locally:
   ```bash
   agentbreeder validate seeds/your-agent-name.yaml
   ```
3. Add a row for your agent to `seeds/README.md`
4. Open a pull request with the title `registry: add <agent-name> seed`

### Quality checklist

Before opening a PR, confirm:

- [ ] `name` is slug-friendly (lowercase, hyphens only, no spaces)
- [ ] `version` follows SemVer (`1.0.0`)
- [ ] `model.primary` references a model that exists in the AgentBreeder model registry or a well-known provider ID
- [ ] `team` and `owner` are filled in (use placeholder values like `your-team` / `your-name@company.com` if contributing publicly)
- [ ] At least one guardrail is included where the agent handles user input or sensitive data
- [ ] `agentbreeder validate` passes with no errors

---

## Development Setup

### Prerequisites

| Tool | Min version | What it's used for |
|------|-------------|-------------------|
| **Python** | 3.11+ | CLI, API server, engine |
| **Node.js + npm** | 18+ | Dashboard UI, website, MCP servers |
| **Docker Desktop** | 24+ | Local service stack (Postgres, Redis, ChromaDB, Neo4j) |
| **Git** | any | Source control |

---

### 1. Fork and Clone

1. Fork the repo on GitHub: [github.com/agentbreeder/agentbreeder](https://github.com/agentbreeder/agentbreeder) → **Fork**
2. Clone your fork locally:

```bash
git clone https://github.com/YOUR_USERNAME/agentbreeder.git
cd agentbreeder
```

3. Add the upstream remote so you can pull future changes:

```bash
git remote add upstream https://github.com/agentbreeder/agentbreeder.git
```

To sync later: `git fetch upstream && git merge upstream/main`

---

### 2. Python Environment + CLI

```bash
# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate          # macOS / Linux
# venv\Scripts\activate           # Windows

# Install in editable mode (changes to source take effect immediately)
pip install -e ".[dev]"

# Copy example env
cp .env.example .env
```

**Run the CLI locally — two ways:**

```bash
# Option 1 — entry point (after pip install -e .)
agentbreeder --help
agentbreeder validate ./agent.yaml

# Option 2 — run as a module (no install required, useful for rapid iteration)
python -m cli.main --help
python -m cli.main validate ./agent.yaml
```

Both are identical. Use `python -m cli.main` when you want to skip reinstalling after every change.

**Testing a CLI change end-to-end:**

```bash
# Make your change in cli/commands/your_command.py
# Then run it immediately:
python -m cli.main your-command --help
```

---

### 3. API Server

The dashboard and CLI both talk to the FastAPI backend. You need it running for anything that reads from the registry.

```bash
# Start infrastructure first (Postgres + Redis)
docker compose up -d postgres redis

# Run database migrations
alembic upgrade head

# Start the API server with hot reload
uvicorn api.main:app --reload --port 8000
```

The API is now live at:
- **REST API:** `http://localhost:8000`
- **Interactive docs (Swagger):** `http://localhost:8000/docs`
- **Health check:** `http://localhost:8000/health`

Changes to any file under `api/` reload automatically.

**To start the full stack (API + all services) instead:**

```bash
docker compose up -d
uvicorn api.main:app --reload --port 8000
```

---

### 4. Dashboard (Agent Builder UI)

The dashboard is a React + TypeScript app (Vite, Tailwind). It proxies all `/api` requests to the API server at `http://localhost:8000`.

```bash
cd dashboard
npm install        # first time only
npm run dev
```

Dashboard is live at **`http://localhost:3001`** with hot module replacement — changes to any file in `dashboard/src/` appear in the browser instantly without a page reload.

> **Requires the API server to be running** (`uvicorn api.main:app --reload --port 8000`). The Vite dev server proxies `/api/*` and `/health` to port 8000 automatically.

**Lint and type-check the dashboard:**

```bash
cd dashboard
npm run lint       # ESLint
npm run typecheck  # TypeScript (tsc --noEmit)
```

---

### 5. Website (agentbreeder.io)

The website is a Next.js app (Fumadocs) that lives in `website/`. All documentation under `/docs` is rendered from `.mdx` files in `website/content/docs/`.

```bash
cd website
npm install        # first time only
npm run dev
```

Website is live at **`http://localhost:3000`** with fast refresh — edit any `.mdx` file in `website/content/docs/` and the page updates in the browser immediately.

**To preview a docs change:**

1. Edit the relevant `.mdx` file in `website/content/docs/`
2. Save — the browser updates automatically
3. Check that links, callouts, and code blocks render correctly
4. Commit the change **in the same commit as the code it documents** (see [CLAUDE.md](CLAUDE.md) for the docs sync rule)

---

### 6. Running Everything Together

For full end-to-end local development, open four terminal tabs:

| Tab | Command | URL |
|-----|---------|-----|
| Services | `docker compose up -d` | — |
| API | `uvicorn api.main:app --reload --port 8000` | `http://localhost:8000/docs` |
| Dashboard | `cd dashboard && npm run dev` | `http://localhost:3001` |
| Website | `cd website && npm run dev` | `http://localhost:3000` |

The CLI (`python -m cli.main`) works in any additional terminal once the services and API are up.

**Or use `agentbreeder quickstart` for a one-command local stack** (pulls Docker images; good for trying the platform but not for editing dashboard source):

```bash
agentbreeder quickstart
```

---

### 7. Run Tests

```bash
# Unit tests — fast, no external deps required
pytest tests/unit/

# Integration tests — requires docker compose up
pytest tests/integration/

# E2E tests — requires full stack + Playwright
pytest tests/e2e/ --headed

# Coverage report
pytest --cov=. --cov-report=html
open htmlcov/index.html
```

Target: **85%+ coverage on changed files**.

---

### Quick Reference

```bash
# CLI changes
python -m cli.main <command>              # run immediately, no reinstall

# API changes
uvicorn api.main:app --reload --port 8000 # auto-reloads on file save

# Dashboard changes
cd dashboard && npm run dev               # HMR at http://localhost:3001

# Website / docs changes
cd website && npm run dev                 # fast refresh at http://localhost:3000

# Python lint
ruff check . && ruff format .
mypy .

# TypeScript lint
cd dashboard && npm run lint && npm run typecheck
```

---

## Coding Standards

Full standards are in [CLAUDE.md](CLAUDE.md). The essentials:

### Python
- Type hints on every function
- Pydantic for all data validation — no raw dicts
- `async` for all I/O — never block the event loop
- `logger.info(...)` instead of `print()`
- Never bare `except:` — always catch specific exceptions

```bash
ruff check . && ruff format .   # lint + format
mypy .                           # type checking
```

### TypeScript / React
- No `any` — type everything
- React Query for all API calls
- Tailwind CSS — no inline styles
- Always handle loading, empty, and error states

### Tests

Every feature needs:
- Unit tests (`tests/unit/`) — required
- Integration tests (`tests/integration/`) — required for API changes
- E2E tests (`tests/e2e/`) — required for dashboard changes

```bash
pytest tests/unit/                     # fast, no external deps
pytest tests/integration/              # requires docker compose up
pytest tests/e2e/ --headed             # Playwright
pytest --cov=. --cov-report=html       # coverage report
```

Target: **85%+ coverage on changed files**. Tests must run in under 100ms each — mock all external dependencies.

---

## Pull Request Process

### Branch naming
```
feat/add-azure-deployer
fix/yaml-parser-error-messages
docs/update-cli-reference
chore/bump-fastapi
```

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):
```
feat: add Azure Container Apps deployer
fix: improve YAML validation error messages
docs: update agentbreeder search reference
chore: bump FastAPI to 0.110
```

### Before opening a PR

- [ ] `pytest tests/unit/` passes
- [ ] `ruff check . && ruff format .` clean
- [ ] `mypy .` clean
- [ ] New tests written for your changes
- [ ] If you changed `agent.yaml` schema → updated JSON Schema at `engine/schema/agent.schema.json`
- [ ] If you changed the DB schema → wrote an Alembic migration
- [ ] If you added a deployer or runtime → added to README supported stack table
- [ ] If you changed a CLI command → updated `website/content/docs/cli-reference.mdx` in the same commit
- [ ] If you changed any user-facing feature → updated the matching doc page in `website/content/docs/` in the same commit (see the sync table in [CLAUDE.md](CLAUDE.md))

PRs are reviewed within 48 hours. Changes to `engine/` require two maintainer approvals. All other changes require one.

### Contributor License Agreement (CLA)

External contributors sign the **Apache ICLA** (or **CCLA** for corporate
contributions) once before their first PR can be merged. The
[CLA Assistant](https://cla-assistant.io/) bot prompts you on your PR — one
click signs it for all future contributions. Contributors who merged before
**2026-04-30** are grandfathered and don't need to sign.

Full rationale and process: see [CLA.md](CLA.md).

---

## Extension Points

### Adding a Cloud Deployer

Cloud deployers live in `engine/deployers/`. Each is self-contained.

1. Read the interface: `engine/deployers/base.py`
2. Study the reference: `engine/deployers/gcp_cloudrun.py`
3. Create `engine/deployers/your_cloud.py` implementing:
   - `provision()` — create cloud infrastructure
   - `deploy()` — deploy the agent container
   - `health_check()` — verify the agent is running
   - `teardown()` — clean up all resources
   - `get_logs()` — stream agent logs
4. Register in `engine/deployers/__init__.py`
5. Add unit + integration tests
6. Add an example in `examples/`
7. Update the supported stack table in README

### Adding a Framework Runtime

Framework runtimes live in `engine/runtimes/`. Each abstracts one agent framework.

1. Read the interface: `engine/runtimes/base.py`
2. Study the reference: `engine/runtimes/langgraph.py`
3. Create `engine/runtimes/your_framework.py` implementing:
   - `validate()` — verify agent code is framework-valid
   - `build()` — generate Dockerfile and build image
   - `get_entrypoint()` — framework startup command
   - `get_requirements()` — Python dependencies
4. Add a working example in `examples/your-framework-agent/`
5. Register in `engine/__init__.py`
6. Add the framework name to `engine/schema/agent.schema.json` enum
7. Write unit tests

### Adding an Orchestration Strategy

1. Add the strategy to `VALID_STRATEGIES` in `sdk/python/agenthub/orchestration.py`
2. Add it to `OrchestrationStrategy` enum in `engine/orchestration_parser.py`
3. Add it to `engine/schema/orchestration.schema.json`
4. Implement `_execute_<strategy>()` in `engine/orchestrator.py`
5. Add the example in `examples/orchestration/`
6. Write unit tests

### Adding a Connector

Connectors live in `connectors/` and passively ingest external resources into the registry.

1. Read the interface: `connectors/base.py`
2. Create `connectors/your_tool/connector.py` implementing `scan()` and `is_available()`
3. Include a `README.md` with setup instructions
4. Write unit tests with mocked external API responses

### Seeding the Marketplace

The community template library is one of the highest-leverage contributions. A great template dramatically reduces time-to-first-agent for others.

A good template:
- Solves a real, specific use case (e.g. "GitHub PR reviewer", "Zendesk tier-1 support", "SQL analyst")
- Works out of the box with the example `.env` values substituted
- Includes a `README.md` explaining what it does, what credentials it needs, and how to customize it
- Has a tested `agent.yaml` with all required fields

Add templates to `examples/templates/` and open a PR with the label `marketplace:template`.

---

## Issue Labels

| Label | Meaning |
|---|---|
| `good first issue` | Approachable for new contributors |
| `help wanted` | Maintainers want community input |
| `needs design` | Discussion required before implementation; do not open a PR yet |
| `deployer:aws` / `deployer:gcp` / `deployer:azure` | Cloud deployer work |
| `runtime:langgraph` / `runtime:crewai` / `runtime:claude-sdk` | Framework runtime work |
| `area:cli` / `area:dashboard` / `area:registry` / `area:engine` | Component area |
| `type:bug` / `type:feature` / `type:perf` / `type:docs` | Change type |
| `priority:p0` / `priority:p1` / `priority:p2` | Priority level |
| `marketplace:template` | New community agent template |

---

## Code of Conduct

We adopt the [Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/) as
our community standard. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for the
full text and enforcement process. Be constructive, be kind, assume good intent.

Report Code of Conduct issues to **saha.rajit@gmail.com** with the subject
line `[CoC] <topic>`.

---

## License

By contributing to AgentBreeder, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
