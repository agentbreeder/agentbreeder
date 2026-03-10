# Local Development Guide

This guide covers setting up Agent Garden for local development and contributing.

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11+ | Backend, CLI, engine |
| Node.js | 22+ | Dashboard frontend |
| Docker & Compose | Latest | Local services, container builds |
| Git | Latest | Version control |

## Setup

### 1. Clone and install

```bash
git clone https://github.com/open-agent-garden/agent-garden.git
cd agent-garden

# Python environment
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Copy environment config
cp .env.example .env
```

### 2. Start local services

```bash
# Start PostgreSQL and Redis
docker compose -f deploy/docker-compose.yml up -d postgres redis
```

Wait for services to be healthy:
```bash
docker compose -f deploy/docker-compose.yml ps
```

### 3. Run database migrations

```bash
alembic upgrade head
```

### 4. Start the API server

```bash
uvicorn api.main:app --reload --port 8000
```

API is available at `http://localhost:8000`. OpenAPI docs at `http://localhost:8000/docs`.

### 5. Start the dashboard

```bash
cd dashboard
npm install
npm run dev
```

Dashboard is available at `http://localhost:5173`. It proxies API requests to port 8000 via Vite config.

### 6. Verify the CLI

```bash
garden --help
garden list agents
```

## Full Stack (Docker Compose)

To run everything in Docker (API + Dashboard + Postgres + Redis + migrations):

```bash
docker compose -f deploy/docker-compose.yml up -d
```

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:3001 |
| API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

Default credentials for local dev:
- **DB:** `garden` / `garden` / database `agentgarden`
- **App login:** `admin@agent-garden.local` / `plant`

## Running Tests

### Python unit tests

```bash
pytest tests/unit/                        # All unit tests
pytest tests/unit/test_api_routes.py      # Specific file
pytest tests/unit/ -k "test_deploy"       # Pattern match
pytest tests/unit/ --cov=api --cov-report=html  # With coverage
```

### Playwright E2E tests

```bash
cd dashboard

# Install browsers (first time only)
npx playwright install --with-deps chromium

# Run tests
npx playwright test                    # Headless
npx playwright test --headed           # Watch in browser
npx playwright test --ui               # Interactive UI mode
npx playwright test tests/e2e/agents   # Specific directory
```

### Coverage report

```bash
pytest tests/unit/ \
  --cov=api --cov=engine --cov=cli --cov=registry --cov=connectors \
  --cov-report=html

# Open htmlcov/index.html in your browser
```

## Linting & Formatting

### Python

```bash
# Lint
ruff check .

# Auto-fix
ruff check --fix .

# Format
ruff format .

# Type check
mypy api/ engine/ cli/ registry/ connectors/
```

### TypeScript

```bash
cd dashboard

# Lint
npm run lint

# Type check
npx tsc -b --noEmit
```

## Database Migrations

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration from model changes
alembic revision --autogenerate -m "add new column to agents"

# Downgrade one version
alembic downgrade -1

# View migration history
alembic history
```

## Project Layout

```
agent-garden/
├── api/                    # FastAPI backend
│   ├── main.py             # App entry, middleware, routers
│   ├── routes/             # REST endpoints
│   ├── services/           # Business logic
│   ├── models/             # SQLAlchemy models + Pydantic schemas
│   └── auth.py             # Auth dependencies
├── cli/                    # CLI (Typer + Rich)
│   ├── main.py             # App entry, command registration
│   └── commands/           # One file per command
├── engine/                 # Deploy pipeline
│   ├── config_parser.py    # YAML parsing + validation
│   ├── builder.py          # Container image builder
│   ├── runtimes/           # Framework-specific builders
│   ├── deployers/          # Cloud-specific deployers
│   └── schema/             # JSON Schema for agent.yaml
├── registry/               # Catalog services (CRUD + search)
├── connectors/             # Integration plugins
├── dashboard/              # React frontend
│   ├── src/pages/          # Page components
│   ├── src/components/     # Shared UI components
│   ├── src/hooks/          # React Query hooks
│   ├── src/lib/            # API client, utilities
│   └── tests/e2e/          # Playwright tests
├── deploy/                 # Docker Compose config
├── tests/unit/             # Python unit tests
└── alembic/                # Database migrations
```

## Environment Variables

Key variables in `.env`:

```bash
# Required
DATABASE_URL=postgresql+asyncpg://garden:garden@localhost:5432/agentgarden
REDIS_URL=redis://localhost:6379
SECRET_KEY=dev-secret-key
AGENTHUB_ENV=development

# Auth
JWT_SECRET_KEY=dev-jwt-secret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Optional integrations
LITELLM_BASE_URL=http://localhost:4000
```

## Common Tasks

### Add a new API endpoint

1. Add route in `api/routes/`
2. Add service logic in `api/services/` or `registry/`
3. Add Pydantic schemas in `api/models/schemas.py`
4. Write unit tests in `tests/unit/`

### Add a new CLI command

1. Create `cli/commands/your_command.py`
2. Register in `cli/main.py`: `app.command(name="your-command")(your_module.your_function)`
3. Write unit tests

### Add a new dashboard page

1. Create page in `dashboard/src/pages/`
2. Add route in `dashboard/src/App.tsx`
3. Add navigation link in `dashboard/src/components/shell.tsx`
4. Write Playwright E2E test in `dashboard/tests/e2e/`

### Modify the database schema

1. Update SQLAlchemy model in `api/models/`
2. Create migration: `alembic revision --autogenerate -m "description"`
3. Review the generated migration file
4. Apply: `alembic upgrade head`

## Troubleshooting

**Port already in use:**
```bash
lsof -i :8000    # Find what's using the port
kill -9 <PID>    # Kill it
```

**Database connection refused:**
```bash
docker compose -f deploy/docker-compose.yml ps    # Check if postgres is running
docker compose -f deploy/docker-compose.yml up -d postgres
```

**Stale migrations:**
```bash
alembic downgrade base && alembic upgrade head    # Reset DB
```

**Node modules issues:**
```bash
cd dashboard && rm -rf node_modules && npm install
```
