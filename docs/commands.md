> Extracted from CLAUDE.md (thin-router refactor). Read on demand — see CLAUDE.md router for when.

## 💻 Development Commands

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Local dev stack (starts postgres, redis, API, dashboard)
docker compose up -d

# Run API server
uvicorn api.main:app --reload --port 8000

# Run CLI locally
python -m cli.main --help
agentbreeder --help              # after pip install -e .
agentbreeder validate            # validate agent.yaml
agentbreeder deploy --target local  # deploy locally

# Run tests
pytest tests/unit/                    # Unit tests
pytest tests/integration/             # Integration (requires docker compose)
pytest tests/e2e/ --headed            # E2E with Playwright
pytest --cov=. --cov-report=html      # Coverage

# Frontend
cd dashboard && npm install && npm run dev

# Linting + formatting
ruff check . && ruff format .         # Python
mypy .                                 # Python type checking
cd dashboard && npm run lint           # TypeScript
cd dashboard && npm run typecheck      # TypeScript type checking

# Database migrations
alembic upgrade head                  # Apply migrations
alembic revision --autogenerate -m "description"  # Create migration

# Build CLI package
pip install build && python -m build
```

---

## 📦 Environment Variables

```bash
# Required
DATABASE_URL=postgresql+asyncpg://agentbreeder:agentbreeder@localhost:5432/agentbreeder
REDIS_URL=redis://localhost:6379
SECRET_KEY=<random-256-bit-key>
AGENTBREEDER_ENV=development

# Optional — Cloud credentials (set per environment)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-1
GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
GOOGLE_CLOUD_PROJECT=

# Optional — Integrations
LITELLM_BASE_URL=http://localhost:4000
LANGSMITH_API_KEY=
OPENTELEMETRY_ENDPOINT=http://localhost:4317

# Optional — Auth
JWT_SECRET_KEY=
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
```

---
