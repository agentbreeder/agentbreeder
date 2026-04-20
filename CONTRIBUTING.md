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

- Python 3.11+
- Node.js 18+ (dashboard only)
- Docker & Docker Compose
- Git

### Setup

```bash
# Fork and clone
git clone https://github.com/YOUR_USERNAME/agentbreeder.git
cd agentbreeder

# Python environment
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Start local services (Postgres, Redis)
docker compose up -d

# Verify everything works
pytest tests/unit/
agentbreeder --help

# Dashboard (optional)
cd dashboard && npm install && npm run dev
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
- [ ] If you changed a CLI command → updated help text

PRs are reviewed within 48 hours. Changes to `engine/` require two maintainer approvals. All other changes require one.

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
| `deployer:aws` / `deployer:gcp` / `deployer:azure` | Cloud deployer work |
| `runtime:langgraph` / `runtime:crewai` / `runtime:claude-sdk` | Framework runtime work |
| `area:cli` / `area:dashboard` / `area:registry` / `area:engine` | Component area |
| `type:bug` / `type:feature` / `type:perf` / `type:docs` | Change type |
| `priority:p0` / `priority:p1` / `priority:p2` | Priority level |
| `marketplace:template` | New community agent template |

---

## Code of Conduct

We follow the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). Be constructive, be kind, assume good intent. Harassment or discrimination of any kind will not be tolerated.

Report conduct issues to **saha.rajit@gmail.com**.

---

## License

By contributing to AgentBreeder, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
