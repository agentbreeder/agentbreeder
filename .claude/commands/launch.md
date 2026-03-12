# /launch — Pre-flight Pipeline: Build, Test, Lint, Secure, Document, Commit & Push

You are a release engineer. Execute all quality steps in order, fix issues at each gate, then commit and push clean code to main.

## Do NOT ask for permission — execute each step. Stop only if something is truly unfixable.

---

## STEP 0: DOCKER BUILD & DEPLOY

Build and deploy the full stack locally to verify everything compiles and runs.

### 0a. Build Docker images

```bash
docker build -t agent-garden-api:local .
docker build -t agent-garden-dashboard:local ./dashboard
```

- If the API image fails to build: read the error, fix the Python/dependency issue, rebuild.
- If the dashboard image fails to build: typically TypeScript errors. Read the errors, fix unused variables/imports/type issues in the `.tsx` files, rebuild.
- Iterate until both images build successfully.

### 0b. Start the full stack

```bash
docker compose --project-directory deploy up -d
```

Wait for all containers to be healthy:
```bash
docker compose --project-directory deploy ps
```

Expected containers: `postgres` (healthy), `redis` (healthy), `api` (healthy), `dashboard` (running).

### 0c. Run database migrations

```bash
docker compose --project-directory deploy run --rm migrate
```

- If migrations fail with connection errors: check that `alembic/env.py` reads `DATABASE_URL` from environment (Docker sets it to `postgres` hostname, not `localhost`).
- If migrations fail with duplicate revision IDs: rename conflicting files and update `revision`/`down_revision` to form a linear chain (001 → 002 → 003 → ...).
- Verify all migrations applied by checking logs show "Running upgrade" for each revision.

### 0d. Smoke test

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/api/v1/agents | head -20
curl -s http://localhost:3001/ | head -5
```

- API health endpoint must return `{"status":"healthy"}`.
- API list endpoints must return valid JSON with `data`, `meta`, `errors` fields.
- Dashboard must serve HTML (the React SPA).

### 0e. Cleanup

```bash
docker compose --project-directory deploy down
```

**GATE: Both images build, all containers healthy, migrations applied, API + dashboard responding. Do not proceed until met.**

Record: build status, container count, migration count, smoke test results.

---

## STEP 1: TESTS

Run tests and iterate until all pass with 95%+ coverage.

```bash
./venv/bin/python -m pytest tests/unit/ --cov=. --cov-report=term-missing -q
```

- All tests must pass (0 failures).
- Coverage must be >= 95% on changed/new files.
- If tests fail: fix the code or tests, re-run, iterate until green.
- If coverage < 95%: write tests for uncovered lines, re-run.
- Avoid mocks wherever possible — use real implementations, fixtures, factory functions.
- Use `typer.testing.CliRunner` for CLI tests, `httpx.AsyncClient` for API tests.
- Do NOT skip, disable, or xfail tests.

**GATE: All tests pass AND coverage >= 95%. Do not proceed until met.**

Record: test count, coverage %.

---

## STEP 2: LINT & FORMAT

```bash
./venv/bin/ruff check . --fix
./venv/bin/ruff format .
```

Check remaining:
```bash
./venv/bin/ruff check .
./venv/bin/ruff format --check .
```

- Fix any remaining errors that `--fix` could not handle by reading and editing the files.
- Re-run until both check and format report zero errors.

**GATE: Zero lint errors, format clean. Do not proceed until met.**

Record: errors found, errors fixed.

---

## STEP 3: SECURITY

### 3a. Dependency vulnerabilities
```bash
./venv/bin/pip-audit 2>/dev/null || (./venv/bin/pip install pip-audit && ./venv/bin/pip-audit)
```
- Critical/High: MUST fix (upgrade in `pyproject.toml`, reinstall, re-audit).
- Medium: fix if < 5 min, otherwise note.
- Low: note only.

### 3b. Secret scan
```bash
grep -rn "sk-proj-\|sk-ant-\|AKIA\|ghp_\|gho_" --include="*.py" --include="*.ts" --include="*.tsx" --include="*.yaml" --include="*.yml" --include="*.json" . | grep -v node_modules | grep -v venv | grep -v __pycache__ | grep -v ".claude/" || echo "No secrets found"
```
- Ignore test fixtures (fake keys in test files are fine).
- Remove any real secrets immediately. NEVER commit real keys/tokens.

### 3c. Type check (non-blocking)
```bash
./venv/bin/mypy . --ignore-missing-imports 2>&1 || true
```
- Report errors. Fix critical ones if straightforward. Does not block pipeline.

**GATE: No critical/high vulns remaining AND no real secrets found. Do not proceed until met.**

Record: vulns found/fixed/noted, secrets status, type error count.

---

## STEP 4: DOCS

Detect code changes and update corresponding documentation.

```bash
git diff --name-only HEAD 2>/dev/null || true
git diff --name-only --cached 2>/dev/null || true
git ls-files --others --exclude-standard 2>/dev/null || true
```

Map changes to docs:
- `cli/commands/*.py`, `cli/main.py` -> `docs/cli-reference.md`
- `api/routes/*.py` -> `docs/api-reference.md`
- `engine/config_parser.py` -> `docs/agent-yaml.md`
- `engine/**` -> `ARCHITECTURE.md`
- `pyproject.toml`, `docker-compose.yml` -> `docs/quickstart.md`, `docs/local-development.md`
- Major changes -> `README.md`
- Removed features -> remove stale references

For each affected doc: read current doc, read changed source, update doc.

Standards: GFM, language-tagged code blocks, usage examples for CLI/API, tables for structured data.

Validate: check internal links exist, no unclosed code blocks, docs match current code.

If no code changes affect docs, skip this step.

Record: docs updated/created/removed.

---

## STEP 5: COMMIT & PUSH

```bash
git add -A
git status
git diff --cached --stat
```

Review staged files — ensure no secrets, no `.env` files, no large binaries.

Commit with conventional message:
```bash
git commit -m "<type>: <summary under 72 chars>

<body with details if multiple change types>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

**Commit message rules:**
- Conventional format: `feat:`, `fix:`, `test:`, `chore:`, `docs:`, `refactor:`
- First line under 72 chars — summarize ALL changes
- Body with details if multiple change types

Push and verify:
```bash
git push origin main
git log --oneline -3
git status
```

---

## Output

```
=== Launch Summary ===
Docker:        API ✅ | Dashboard ✅ | Postgres ✅ | Redis ✅ | Migrations: X applied
Tests:         X passed, 0 failed | Coverage: XX%
Lint:          X found, X fixed | Clean
Security:      Vulns: X fixed, Y noted | Secrets: none
Type check:    Python: X errors | TS: X errors (non-blocking)
Docs:          X updated, Y created, Z removed
Commit:        <short-hash> <message>
Pushed:        main -> origin/main
Status:        LAUNCHED
```
