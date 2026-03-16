# /launch — Pre-flight Pipeline (agent-garden)

Extends the base `/launch` with a Docker build + smoke test phase before the standard pipeline.

## Do NOT ask for permission — execute each step. Stop only if something is truly unfixable.

---

## Phase 0 — Docker Build & Smoke Test (agent-garden specific)

### 0a. Build images
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"
docker build -t agent-garden-api:local .
docker build -t agent-garden-dashboard:local ./dashboard
```

Fix any build errors (Python/dependency issues for API, TypeScript errors for dashboard). Iterate until both succeed.

### 0b. Start full stack
```bash
docker compose --project-directory deploy up -d
docker compose --project-directory deploy ps
```

Expected: `postgres` (healthy), `redis` (healthy), `api` (healthy), `dashboard` (running).

### 0c. Run migrations
```bash
docker compose --project-directory deploy run --rm migrate
```

If migrations fail with connection errors: verify `alembic/env.py` reads `DATABASE_URL` from env.
If duplicate revision IDs: rename files and form a linear chain (001 → 002 → 003).

### 0d. Smoke test
```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/api/v1/agents | head -20
curl -s http://localhost:3001/ | head -5
```

API must return `{"status":"healthy"}`. Dashboard must serve HTML.

### 0e. Cleanup
```bash
docker compose --project-directory deploy down
```

**GATE: Both images build, containers healthy, migrations applied, API + dashboard responding.**

---

## Phase 1 — Tests (invoke `/test`)

Run `/test`. Loop until ≥95% coverage with zero failures.

**GATE: COVERAGE MET ✅**

---

## Phase 2 — Lint (invoke `/lint`)

Run `/lint`. Zero errors across ruff and TypeScript.

**GATE: PASSED ✅**

---

## Phase 3 — Security (invoke `/security`)

Run `/security`. No critical/high vulns, no real secrets.

**GATE: PASSED ✅**

---

## Phase 4 — Docs (invoke `/docs`)

Run `/docs`. Update any stale documentation based on changed files.

---

## Phase 5 — Commit (invoke `/commit`)

Run `/commit`. Use `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`.

---

## Phase 6 — Push (invoke `/push`)

Run `/push`. Remote: `git@github-oag:open-agent-garden/agent-garden.git`.

---

## Summary output

```
=== Launch Summary ===
Phase 0 Docker:     API ✅ | Dashboard ✅ | Migrations: X applied | Smoke ✅
Phase 1 /test:      ✅ COVERAGE MET (XX%)
Phase 2 /lint:      ✅ PASSED
Phase 3 /security:  ✅ PASSED
Phase 4 /docs:      ✅ PASSED
Phase 5 /commit:    <short-hash> <message>
Phase 6 /push:      main -> origin/main ✅
Status:             LAUNCHED ✅
```
