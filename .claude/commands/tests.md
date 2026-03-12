# /tests — Test Runner

You are a test engineer. Run tests, fix failures, and iterate until all pass with 95%+ coverage.

## Do NOT ask for permission — just run tests, fix failures, repeat.

## Rules

1. Avoid mocks wherever possible. Use real implementations, test databases, fixtures, factory functions. Only mock external APIs that cannot run locally.
2. Fix failing tests — do not skip, disable, or xfail. Iterate until every test passes.
3. Target 95%+ coverage on changed/new files.
4. Do NOT run lint or format — that is /lint's job.

## Test Types (in priority order)

### Unit Tests (`tests/unit/`)
- Individual functions, classes, methods
- Edge cases: empty inputs, None, invalid types, boundaries
- Error paths: proper exceptions raised
- Use `pytest` fixtures, `tmp_path` for file ops
- Use `typer.testing.CliRunner` for CLI commands

### Integration Tests (`tests/integration/`)
- API routes via `httpx.AsyncClient` with real FastAPI app
- Database ops with real test DB (SQLite in-memory or test PostgreSQL)
- CLI -> API -> DB round trips
- Registry services with real DB sessions

### E2E Tests (`tests/e2e/`)
- Full workflow tests (create agent -> deploy -> verify registry)
- Frontend E2E in `dashboard/tests/e2e/` via Playwright (if applicable)

## Test Patterns

### API Route Tests
```python
from httpx import ASGITransport, AsyncClient
from api.main import app

async def test_list_agents():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 200
```

### CLI Tests
```python
from typer.testing import CliRunner
from cli.main import app
runner = CliRunner()

def test_command():
    result = runner.invoke(app, ["command", "arg"])
    assert result.exit_code == 0
```

### Database Tests
```python
from sqlalchemy.ext.asyncio import create_async_engine
engine = create_async_engine("sqlite+aiosqlite:///:memory:")
```

## Execution

1. Run current tests with coverage:
   ```bash
   ./venv/bin/python -m pytest tests/unit/ --cov=. --cov-report=term-missing -q
   ```

2. Identify files/functions below 95% coverage.

3. Write tests — prioritize: new/changed files, core engine, API routes, CLI commands, registry services.

4. Run and fix:
   ```bash
   ./venv/bin/python -m pytest tests/unit/ -v --tb=short
   ```

5. Repeat until all pass AND coverage >= 95%.

## Output

```
=== Tests Summary ===
Tests:    X passed, 0 failed
Coverage: XX%
Status:   PASSED | FAILED
```
