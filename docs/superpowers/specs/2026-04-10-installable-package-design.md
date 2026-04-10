# Installable Package Distribution Design

**Date:** 2026-04-10
**Status:** Approved

## Summary

AgentBreeder will be distributed through three channels — PyPI, Docker Hub, and Homebrew — to maximize reach across Python developers, container users, and macOS/Linux CLI users. The monolith is split into two PyPI packages: a lightweight SDK and the full CLI+server.

## Distribution Channels

| Channel | Package(s) | Install Command |
|---------|-----------|-----------------|
| PyPI | `agentbreeder`, `agentbreeder-sdk` | `pip install agentbreeder` |
| Docker Hub | `agentbreeder/api`, `agentbreeder/dashboard`, `agentbreeder/cli` | `docker pull agentbreeder/api` |
| Homebrew | `open-agent-garden/agentbreeder` tap | `brew tap open-agent-garden/agentbreeder && brew install agentbreeder` |

## 1. Package Split

### agentbreeder-sdk (lightweight)

The SDK package for programmatic agent definition and deployment.

**What's included:** `sdk/python/agenthub/` — Agent, deploy, model, tool, memory, mcp modules.

**Dependencies (minimal):**
- httpx
- pydantic
- ruamel.yaml

**Install:** `pip install agentbreeder-sdk`

**Usage:**
```python
from agenthub import Agent, deploy

agent = Agent(name="my-agent", framework="langgraph", model="gpt-4o")
deploy(agent, target="local")
```

**pyproject.toml location:** `sdk/python/pyproject.toml`

### agentbreeder (full CLI + server)

The CLI tool, API server, deploy engine, registry, and connectors.

**What's included:** cli, api, engine, registry, connectors packages.

**Dependencies:** FastAPI, SQLAlchemy, asyncpg, alembic, typer, rich, docker, redis, etc. Also depends on `agentbreeder-sdk`.

**Install:** `pip install agentbreeder`

**Usage:**
```bash
agentbreeder init
agentbreeder deploy agent.yaml --target local
agentbreeder validate agent.yaml
```

**pyproject.toml location:** root `pyproject.toml` (existing, modified)

### Changes to existing pyproject.toml

1. Remove `sdk` from `hatch.build.targets.wheel.packages`
2. Add `agentbreeder-sdk>=0.1.0` to `dependencies`
3. Fix repository URL: `https://github.com/open-agent-garden/agentbreeder`
4. Fix homepage URL if needed

## 2. Versioning Strategy

- Single version source across both packages
- Version derived from git tag at build time (e.g., `v0.1.0` → `0.1.0`)
- Both packages are always released together with the same version number
- Use `hatch-vcs` or `setuptools-scm` to read version from git tags
- Fallback version in `__version__.py` for editable installs

## 3. CI/CD Workflows

### ci.yml — PR Checks

Triggered on: pull requests to main

Steps:
1. Checkout code
2. Set up Python 3.11, 3.12 matrix
3. Install dependencies: `pip install -e ".[dev]"`
4. Lint: `ruff check . && ruff format --check .`
5. Type check: `mypy .`
6. Test: `pytest tests/unit/ --cov`
7. Build packages: `python -m build` (root) + `cd sdk/python && python -m build`
8. Build Docker images (no push): `docker build -t agentbreeder/api:test .` etc.
9. Report coverage

### release.yml — Publish on GitHub Release

Triggered on: GitHub Release created (tag pattern `v*`)

Steps:
1. Extract version from release tag (`v0.1.0` → `0.1.0`)
2. Build `agentbreeder-sdk`:
   - `cd sdk/python && python -m build`
   - Publish to PyPI via trusted publisher (OIDC)
3. Build `agentbreeder`:
   - `python -m build` (root)
   - Publish to PyPI via trusted publisher (OIDC)
   - SDK published first because CLI depends on it
4. Build + push Docker images:
   - `agentbreeder/api:$VERSION` + `agentbreeder/api:latest`
   - `agentbreeder/dashboard:$VERSION` + `agentbreeder/dashboard:latest`
   - `agentbreeder/cli:$VERSION` + `agentbreeder/cli:latest`
   - Multi-platform: linux/amd64, linux/arm64
5. Trigger Homebrew tap update

### homebrew-update.yml — Update Tap Formula

Triggered on: workflow dispatch from release.yml

Steps:
1. Download PyPI tarball for new version
2. Compute SHA256
3. Update formula in `open-agent-garden/homebrew-agentbreeder` repo
4. Commit + push to tap repo

## 4. Docker Hub Images

### agentbreeder/api

Full API server. Based on existing `Dockerfile`.

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY api/ api/
COPY cli/ cli/
COPY engine/ engine/
COPY registry/ registry/
COPY connectors/ connectors/
COPY sdk/ sdk/
COPY alembic/ alembic/
COPY alembic.ini ./
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### agentbreeder/dashboard

React frontend. New `dashboard/Dockerfile`.

```dockerfile
FROM node:20-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

### agentbreeder/cli

Lightweight CLI image for CI/CD pipelines.

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir agentbreeder
ENTRYPOINT ["agentbreeder"]
```

Use case: `docker run agentbreeder/cli deploy agent.yaml --target gcp`

### Tagging Strategy

Every release produces:
- `agentbreeder/<image>:<version>` (e.g., `0.1.0`)
- `agentbreeder/<image>:latest`
- `agentbreeder/<image>:<major>.<minor>` (e.g., `0.1`)

### Multi-platform

All images built for `linux/amd64` and `linux/arm64` using `docker buildx`.

## 5. Homebrew Tap

### Repository

New repo: `open-agent-garden/homebrew-agentbreeder`

### Install Flow

```bash
brew tap open-agent-garden/agentbreeder
brew install agentbreeder
```

### Formula Template

```ruby
class Agentbreeder < Formula
  include Language::Python::Virtualenv

  desc "Define Once. Deploy Anywhere. Govern Automatically."
  homepage "https://github.com/open-agent-garden/agentbreeder"
  url "https://files.pythonhosted.org/packages/source/a/agentbreeder/agentbreeder-VERSION.tar.gz"
  sha256 "SHA256_HASH"
  license "Apache-2.0"

  depends_on "python@3.12"
  depends_on "libpq"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "agentbreeder", shell_output("#{bin}/agentbreeder --help")
  end
end
```

### Migration to Homebrew Core

Once the project reaches sufficient traction (stable releases, active users), submit the formula to `homebrew/homebrew-core`. The formula structure is compatible — just needs to meet Homebrew core's acceptance criteria.

## 6. PyPI Authentication

Use PyPI trusted publishers (OIDC) — no API tokens stored in GitHub Secrets.

Setup:
1. Create PyPI accounts for `agentbreeder` and `agentbreeder-sdk`
2. Configure each as a trusted publisher linked to the `open-agent-garden/agentbreeder` GitHub repo
3. The `release.yml` workflow uses `pypa/gh-action-pypi-publish` which handles OIDC automatically

## 7. Namespace Alignment

| System | Namespace |
|--------|-----------|
| GitHub | `open-agent-garden/agentbreeder` |
| PyPI | `agentbreeder`, `agentbreeder-sdk` |
| Docker Hub | `agentbreeder/api`, `agentbreeder/dashboard`, `agentbreeder/cli` |
| Homebrew | `open-agent-garden/homebrew-agentbreeder` |
| Homepage | `agentbreeder.com` |
| Docs | `open-agent-garden.github.io/agentbreeder` |

Fix stale URLs in `pyproject.toml`:
- Repository: `https://github.com/open-agent-garden/agentbreeder`
- Documentation: `https://open-agent-garden.github.io/agentbreeder`

## 8. New Files Summary

```
agentbreeder/
├── sdk/python/pyproject.toml              # NEW — SDK package config
├── Dockerfile.cli                          # NEW — lightweight CLI image
├── dashboard/Dockerfile                    # NEW — frontend image
├── dashboard/nginx.conf                    # NEW — nginx config for dashboard
├── .github/workflows/
│   ├── ci.yml                              # NEW — PR checks
│   ├── release.yml                         # NEW — publish on GitHub Release
│   └── homebrew-update.yml                 # NEW — update tap formula
├── pyproject.toml                          # MODIFIED — remove sdk, fix URLs, add sdk dep
```

**Separate repo:**
```
open-agent-garden/homebrew-agentbreeder/
└── Formula/agentbreeder.rb                 # Homebrew formula
```

## 9. Release Checklist

For each release:
1. Ensure all tests pass on main
2. Create GitHub Release with tag `vX.Y.Z`
3. CI automatically:
   - Builds + publishes `agentbreeder-sdk` to PyPI
   - Builds + publishes `agentbreeder` to PyPI
   - Builds + pushes 3 Docker images to Docker Hub
   - Updates Homebrew tap formula
4. Verify:
   - `pip install agentbreeder==X.Y.Z` works
   - `docker pull agentbreeder/api:X.Y.Z` works
   - `brew upgrade agentbreeder` works
