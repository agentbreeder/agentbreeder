# Contributing to Agent Garden

Thank you for your interest in contributing to Agent Garden! We welcome contributions of all kinds — code, documentation, bug reports, feature ideas, and agent templates.

---

## Ways to Contribute

| Contribution | Difficulty | Impact | Where |
|-------------|:----------:|:------:|-------|
| Report a bug | Easy | High | [GitHub Issues](https://github.com/open-agent-garden/agent-garden/issues) |
| Fix a `good first issue` | Easy | Medium | [Issues labeled `good first issue`](https://github.com/open-agent-garden/agent-garden/issues?q=label%3A%22good+first+issue%22) |
| Add a cloud deployer | Medium | Very High | `engine/deployers/` |
| Add a framework runtime | Medium | Very High | `engine/runtimes/` |
| Add a connector | Medium | High | `connectors/` |
| Create an agent template (Seed) | Easy | Medium | `templates/` |
| Improve documentation | Easy | High | `docs/` |
| Suggest a feature | Easy | Medium | [GitHub Discussions](https://github.com/open-agent-garden/agent-garden/discussions) |

---

## Development Setup

### Prerequisites

- Python 3.11+
- Node.js 18+ (for dashboard)
- Docker & Docker Compose
- Git

### Setup

```bash
# Fork and clone
git clone https://github.com/YOUR_USERNAME/agent-garden.git
cd agent-garden

# Python environment
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Start local services
docker compose up -d

# Verify
pytest tests/unit/
garden --help

# Frontend (optional)
cd dashboard && npm install && npm run dev
```

---

## Coding Standards

Full standards are in [CLAUDE.md](CLAUDE.md). The essentials:

### Python
- Type hints on all functions
- Pydantic for data validation
- Async for all I/O
- `ruff check . && ruff format .` for linting
- `mypy .` for type checking
- Never use `print()` — use the logger

### TypeScript / React
- No `any` types
- React Query for API calls
- Tailwind CSS — no inline styles
- Always handle loading, empty, and error states

### Tests
- Every feature needs tests: unit (minimum), integration (for API changes), E2E (for dashboard)
- Coverage target: 80% on changed files
- Test naming: `test_[unit]_[scenario]_[expected_result]`
- Tests must run in < 100ms each (mock all external dependencies)

---

## Commit & PR Conventions

### Branch naming

```
feat/add-azure-deployer
fix/yaml-parser-error-message
docs/update-cli-reference
```

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Azure Container Apps deployer
fix: improve YAML validation error messages
docs: update CLI reference for garden search
chore: bump FastAPI to 0.110
```

### Pull Request checklist

Before submitting a PR, verify:

- [ ] All existing tests pass (`pytest tests/unit/`)
- [ ] New tests written for your changes
- [ ] Linting passes (`ruff check . && ruff format .`)
- [ ] Type checking passes (`mypy .`)
- [ ] If you changed `agent.yaml` schema → update JSON Schema + docs
- [ ] If you changed the registry schema → write an Alembic migration
- [ ] If you added a deployer or runtime → add to supported stack table in README
- [ ] If you changed a CLI command → update help text

---

## Adding a Cloud Deployer

This is one of the highest-impact contributions. Each deployer is a self-contained module.

1. Read the interface: `engine/deployers/base.py`
2. Study the reference: `engine/deployers/aws_ecs.py`
3. Create `engine/deployers/your_cloud.py` implementing:
   - `provision()` — create cloud infrastructure
   - `deploy()` — deploy the agent container
   - `health_check()` — verify the agent is running
   - `teardown()` — clean up resources
   - `get_logs()` — retrieve agent logs
4. Register in `engine/deployers/__init__.py`
5. Add unit tests in `tests/unit/deployers/`
6. Add integration test with mocked cloud API
7. Add example config to `examples/`
8. Update the supported stack table in README

See the `build:deployer` skill in [AGENT.md](AGENT.md) for a detailed prompt.

---

## Adding a Framework Runtime

1. Read the interface: `engine/runtimes/base.py`
2. Study the reference: `engine/runtimes/langgraph.py`
3. Create `engine/runtimes/your_framework.py` implementing:
   - `validate()` — check agent code is valid
   - `build()` — generate Dockerfile and build container
   - `get_entrypoint()` — framework-specific startup command
   - `get_requirements()` — dependencies to inject
4. Add a working example in `examples/your-framework-agent/`
5. Register in `engine/__init__.py`
6. Add to the `agent.yaml` JSON Schema enum
7. Write unit tests
8. Update README

See the `build:runtime` skill in [AGENT.md](AGENT.md) for a detailed prompt.

---

## Adding a Connector

1. Read the interface: `connectors/base.py`
2. Study references: `connectors/litellm/` and `connectors/langsmith/`
3. Create `connectors/your_tool/connector.py`
4. Include a `README.md` in your connector directory with setup instructions
5. Write unit tests with mocked external API responses

---

## Issue Labels

| Label | Meaning |
|-------|---------|
| `milestone:v0.1` | Required for first release |
| `milestone:v0.2` | Planned for v0.2 |
| `good first issue` | Approachable for new contributors |
| `help wanted` | Community help needed |
| `deployer:aws` / `deployer:gcp` | Cloud deployer work |
| `runtime:langgraph` / `runtime:crewai` | Framework runtime work |
| `area:cli` / `area:dashboard` / `area:registry` | Component area |
| `type:bug` / `type:feature` / `type:perf` | Change type |
| `priority:p0` / `priority:p1` / `priority:p2` | Priority level |

---

## Review Process

- Maintainers aim to review PRs within 48 hours
- Changes to `engine/` require two approvals; everything else requires one
- Feedback is constructive — we're all building this together
- If your PR is blocked, we'll explain why and help you get it merged

---

## License

By contributing to Agent Garden, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
