# Self-Hosting AgentBreeder

This guide walks through deploying AgentBreeder on your own infrastructure using Docker and Docker Compose.

---

## Prerequisites

| Requirement | Minimum Version | Notes |
|-------------|----------------|-------|
| Docker | 24.0+ | BuildKit enabled by default |
| Docker Compose | v2.20+ | Bundled with Docker Desktop; `docker compose` (not `docker-compose`) |
| RAM | 4 GB | 6 GB recommended for the full stack including LiteLLM |
| Disk | 10 GB | For images, volumes, and build cache |
| Open ports | 8000, 3001, 4000 | API, dashboard, LiteLLM gateway |

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/agentbreeder/agentbreeder.git
cd agentbreeder
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and set at minimum the two secret keys:

```bash
# Generate a random 256-bit key (requires openssl)
SECRET_KEY=$(openssl rand -hex 32)
JWT_SECRET_KEY=$(openssl rand -hex 32)
```

Edit `.env` and replace the placeholder values:

```
SECRET_KEY=<output of openssl rand -hex 32>
JWT_SECRET_KEY=<output of openssl rand -hex 32>
```

### 3. Start the stack

```bash
docker compose -f deploy/docker-compose.yml up -d
```

This starts six services: `postgres`, `redis`, `migrate` (runs once), `api`, `dashboard`, and `litellm`.

### 4. Run database migrations

Migrations run automatically via the `migrate` service on first start. Verify they completed:

```bash
docker compose -f deploy/docker-compose.yml logs migrate
```

You should see `INFO  [alembic.runtime.migration] Running upgrade ...` lines ending with no errors.

If migrations need to be re-run manually:

```bash
docker compose -f deploy/docker-compose.yml run --rm migrate
```

### 5. Verify the stack is healthy

```bash
# Check all services are running
docker compose -f deploy/docker-compose.yml ps

# Verify the API health endpoint
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "healthy", "service": "agentbreeder-api", "version": "..."}
```

- Dashboard UI: [http://localhost:3001](http://localhost:3001)
- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- LiteLLM gateway: [http://localhost:4000](http://localhost:4000)

---

## Environment Variables Reference

### Required

| Variable | Default in .env.example | Description |
|----------|------------------------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://agentbreeder:agentbreeder@localhost:5432/agentbreeder` | PostgreSQL connection string. Use the Docker service name (`postgres`) when running inside Compose. |
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string. Use `redis://redis:6379` inside Compose. |
| `SECRET_KEY` | `change-me-to-a-random-256-bit-key` | **Must be changed.** Used for session signing and encryption. Generate with `openssl rand -hex 32`. |
| `AGENTBREEDER_ENV` | `development` | Runtime environment. Set to `production` in production deployments. |

### Auth

| Variable | Default in .env.example | Description |
|----------|------------------------|-------------|
| `JWT_SECRET_KEY` | `change-me` | **Must be changed.** Signs JWT tokens. Generate with `openssl rand -hex 32`. |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Token lifetime (24 hours). Reduce for higher-security environments. |

### Optional — Cloud Credentials

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_ACCESS_KEY_ID` | _(empty)_ | Required only when deploying agents to AWS. |
| `AWS_SECRET_ACCESS_KEY` | _(empty)_ | Required only when deploying agents to AWS. |
| `AWS_DEFAULT_REGION` | `us-east-1` | AWS region for agent deployments. |
| `GOOGLE_APPLICATION_CREDENTIALS` | _(empty)_ | Path to GCP service account JSON. Required only for GCP deployments. |
| `GOOGLE_CLOUD_PROJECT` | _(empty)_ | GCP project ID. Required only for GCP deployments. |

### Optional — Integrations

| Variable | Default | Description |
|----------|---------|-------------|
| `LITELLM_BASE_URL` | `http://localhost:4000` | LiteLLM gateway URL. Already configured in Compose. |
| `LANGSMITH_API_KEY` | _(empty)_ | Enable LangSmith tracing. |
| `OPENTELEMETRY_ENDPOINT` | `http://localhost:4317` | OTLP endpoint for distributed traces. |

---

## Upgrading

### 1. Pull the latest images and code

```bash
git pull origin main
docker compose -f deploy/docker-compose.yml pull
```

### 2. Apply database migrations

```bash
docker compose -f deploy/docker-compose.yml run --rm migrate
```

### 3. Restart services

```bash
docker compose -f deploy/docker-compose.yml up -d
```

Docker Compose will recreate only the containers whose images or configuration have changed. Running containers serving traffic are replaced with zero manual steps.

---

## Troubleshooting

### PostgreSQL is not ready / API fails to connect

The `api` service depends on `postgres` being healthy. If startup is slow (e.g., slow disk or constrained CI runner), Postgres may not be ready when the API starts.

Check Postgres status:

```bash
docker compose -f deploy/docker-compose.yml logs postgres
```

Wait for the line: `database system is ready to accept connections`

If it never appears, check for disk space (`df -h`) or memory pressure (`free -h`).

### Port conflicts

If ports 8000, 3001, or 4000 are already in use on the host:

```bash
# Find what is using a port
lsof -i :8000
```

Either stop the conflicting process or change the host-side port mapping in `deploy/docker-compose.yml` (e.g., `"8080:8000"` to expose the API on port 8080 instead).

### Migration fails

Check the migrate service logs:

```bash
docker compose -f deploy/docker-compose.yml logs migrate
```

Common causes:
- **Database does not exist yet** — Postgres healthcheck may not have passed before migrate ran. Re-run with: `docker compose -f deploy/docker-compose.yml run --rm migrate`
- **Alembic version conflict** — If you have a local development database with a different migration head, run `alembic stamp head` inside the container to mark the current state, then retry.

### LiteLLM fails to start

LiteLLM requires a `litellm_config.yaml` file mounted at `/app/config.yaml`. The Compose file maps `deploy/litellm_config.yaml`. If that file is missing:

```bash
ls deploy/litellm_config.yaml
```

Create a minimal config if needed:

```yaml
model_list:
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY
```

### Viewing logs for all services

```bash
docker compose -f deploy/docker-compose.yml logs -f
```

Append a service name to tail logs for a specific service:

```bash
docker compose -f deploy/docker-compose.yml logs -f api
```

---

## Production Hardening

Before exposing AgentBreeder to the internet or real users, complete the following:

### Secrets

- **Replace all placeholder keys.** `SECRET_KEY` and `JWT_SECRET_KEY` must be strong random values. The defaults in `docker-compose.yml` are not safe for production.
- Store secrets in a secrets manager (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault) and inject them as environment variables at runtime. Do not commit `.env` to version control.

### Reverse proxy and HTTPS

Run a reverse proxy in front of the API and dashboard. Example with Caddy (automatic HTTPS):

```
agentbreeder.example.com {
    reverse_proxy localhost:8000
}

app.agentbreeder.example.com {
    reverse_proxy localhost:3001
}
```

Or use nginx with Certbot for Let's Encrypt certificates.

### Set `AGENTBREEDER_ENV=production`

This enables production-mode logging and disables debug features. Add it to your `.env`:

```
AGENTBREEDER_ENV=production
```

### Database backups

The `pgdata` Docker volume holds all persistent state. Schedule regular backups:

```bash
docker compose -f deploy/docker-compose.yml exec postgres \
  pg_dump -U agentbreeder agentbreeder | gzip > backup-$(date +%Y%m%d).sql.gz
```

### Restrict port exposure

In production, do not expose ports 5432 (Postgres) or 6379 (Redis) to the host network. Remove or comment out their `ports:` entries in `docker-compose.yml` so they are only accessible within the Docker network.

---

## Optional: MCP Example Server

The `mcp-example` service is included in `deploy/docker-compose.yml` but disabled by default (it uses a Docker Compose profile). To start it:

```bash
docker compose -f deploy/docker-compose.yml --profile examples up -d
```

---

## Stopping the Stack

```bash
# Stop all services (preserves data volumes)
docker compose -f deploy/docker-compose.yml down

# Stop and remove all data volumes (full reset)
docker compose -f deploy/docker-compose.yml down -v
```
