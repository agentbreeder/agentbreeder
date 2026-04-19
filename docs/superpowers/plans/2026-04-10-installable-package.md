# Installable Package Distribution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split AgentBreeder into two PyPI packages (CLI + SDK), fix all stale URLs, add Docker Hub images (api/dashboard/cli), set up a Homebrew tap, and wire up release automation via GitHub Actions.

**Architecture:** The monolith splits at the `sdk/python/` boundary. The SDK gets its own `pyproject.toml` with minimal deps (PyYAML only — no pydantic, no httpx). The CLI package drops `sdk` from its wheel and instead declares a dependency on `agentbreeder-sdk`. Existing CI/release workflows are extended, not replaced. Docker images push to Docker Hub (not just GHCR). Homebrew tap lives in a separate repo.

**Tech Stack:** hatchling (build), GitHub Actions (CI/CD), PyPI OIDC (auth), Docker Buildx (multi-platform images), Homebrew (macOS/Linux CLI distribution)

**Key discovery:** CI (`ci.yml`) and release (`release.yml`) workflows already exist and are functional. The release workflow already publishes to PyPI via OIDC and pushes Docker images to GHCR. This plan extends them rather than creating from scratch.

---

### Task 1: Fix all stale `open-agentbreeder` URLs to `rajitsaha`

**Files:**
- Modify: `pyproject.toml:63-64`
- Modify: `mkdocs.yml:3-5,84`
- Modify: `CHANGELOG.md:116-117`
- Modify: `CONTRIBUTING.md:11-12,18`
- Modify: `SECURITY.md:16`
- Modify: `cli/commands/init_cmd.py:86,426-427`
- Modify: `.github/ISSUE_TEMPLATE/config.yml:4`
- Modify: `.github/ISSUE_TEMPLATE/feature_request.yml:8`
- Modify: `.github/workflows/security.yml:150`
- Modify: `scripts/setup-branch-protection.sh:19`
- Modify: `scripts/create_github_issues.py:39`

- [ ] **Step 1: Replace all `open-agentbreeder` references**

In every file listed above, replace `open-agentbreeder` with `rajitsaha`. There are 17 occurrences across 11 files. Use find-and-replace.

Specific replacements:
```
open-agentbreeder/agentbreeder  →  agentbreeder/agentbreeder
open-agentbreeder.github.io     →  rajitsaha.github.io
```

- [ ] **Step 2: Verify no stale references remain**

Run:
```bash
grep -rn "open-agentbreeder" --include="*.py" --include="*.toml" --include="*.yml" --include="*.yaml" --include="*.md" --include="*.sh" .
```
Expected: zero matches.

- [ ] **Step 3: Run tests to confirm nothing broke**

Run: `python3 -m pytest tests/unit/ -x -q`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "fix: replace all stale open-agentbreeder URLs with rajitsaha"
```

---

### Task 2: Create `sdk/python/pyproject.toml` for `agentbreeder-sdk`

**Files:**
- Create: `sdk/python/pyproject.toml`
- Create: `sdk/python/README.md`

- [ ] **Step 1: Determine SDK dependencies**

The SDK modules import:
- `yaml` (PyYAML) — used in `orchestration.py` and `yaml_utils.py`
- `mcp.server.fastmcp` — used in `mcp.py` (optional, for MCP server authoring)
- Everything else is stdlib (`dataclasses`, `pathlib`, `typing`, `inspect`, `re`, `logging`, `abc`)

- [ ] **Step 2: Create `sdk/python/pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "agentbreeder-sdk"
version = "0.1.0"
description = "AgentBreeder Python SDK — define, validate, and deploy AI agents programmatically."
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.11"
authors = [{ name = "AgentBreeder Contributors" }]
keywords = ["ai", "agents", "sdk", "agentbreeder"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: Apache Software License",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Software Development :: Libraries",
]
dependencies = [
    "PyYAML>=6.0",
]

[project.optional-dependencies]
mcp = ["mcp>=1.0.0"]

[project.urls]
Homepage = "https://github.com/agentbreeder/agentbreeder"
Repository = "https://github.com/agentbreeder/agentbreeder"
Documentation = "https://www.agentbreeder.io"

[tool.hatch.build.targets.wheel]
packages = ["agenthub"]
```

- [ ] **Step 3: Create `sdk/python/README.md`**

```markdown
# agentbreeder-sdk

The lightweight Python SDK for [AgentBreeder](https://github.com/agentbreeder/agentbreeder) — define, validate, and deploy AI agents programmatically.

## Install

```bash
pip install agentbreeder-sdk
```

## Quick Start

```python
from agenthub import Agent

agent = (
    Agent("my-agent", version="1.0.0", team="engineering")
    .with_model(primary="claude-sonnet-4")
    .with_prompt(system="You are a helpful assistant.")
    .with_deploy(cloud="local")
)

# Save to agent.yaml
agent.save("agent.yaml")
```

## Full Documentation

See the [AgentBreeder docs](https://www.agentbreeder.io).
```

- [ ] **Step 4: Verify the SDK builds independently**

Run:
```bash
cd sdk/python && python3 -m build
```
Expected: `dist/agentbreeder_sdk-0.1.0.tar.gz` and `dist/agentbreeder_sdk-0.1.0-py3-none-any.whl` are created.

- [ ] **Step 5: Verify the SDK installs and imports**

Run:
```bash
pip install sdk/python/dist/agentbreeder_sdk-0.1.0-py3-none-any.whl
python3 -c "from agenthub import Agent; print('SDK OK')"
```
Expected: prints `SDK OK`.

- [ ] **Step 6: Commit**

```bash
git add sdk/python/pyproject.toml sdk/python/README.md
git commit -m "feat: add pyproject.toml for agentbreeder-sdk package"
```

---

### Task 3: Update root `pyproject.toml` to depend on SDK

**Files:**
- Modify: `pyproject.toml:22-40,67`

- [ ] **Step 1: Remove `sdk` from wheel packages**

In `pyproject.toml`, change line 67:
```toml
# Before:
packages = ["engine", "api", "cli", "sdk", "registry", "connectors"]

# After:
packages = ["engine", "api", "cli", "registry", "connectors"]
```

- [ ] **Step 2: Add `agentbreeder-sdk` to dependencies**

Add `"agentbreeder-sdk>=0.1.0"` to the `dependencies` list in `pyproject.toml`:
```toml
dependencies = [
    "agentbreeder-sdk>=0.1.0",
    "fastapi>=0.110.0",
    # ... rest unchanged
]
```

- [ ] **Step 3: Align version — fix `sdk/python/agenthub/__init__.py`**

The SDK `__init__.py` says `__version__ = "1.3.0"` but pyproject.toml says `0.1.0`. Align to `0.1.0`:

Change `sdk/python/agenthub/__init__.py` line 49:
```python
# Before:
__version__ = "1.3.0"

# After:
__version__ = "0.1.0"
```

- [ ] **Step 4: Verify root package still builds**

Run:
```bash
python3 -m build
```
Expected: builds successfully, and `sdk/` is NOT included in the wheel.

- [ ] **Step 5: Verify the wheel contents**

Run:
```bash
python3 -m zipfile -l dist/agentbreeder-0.1.0-py3-none-any.whl | head -20
```
Expected: contains `engine/`, `api/`, `cli/`, `registry/`, `connectors/` — but NOT `sdk/`.

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/unit/ -x -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml sdk/python/agenthub/__init__.py
git commit -m "feat: split SDK out of main package, depend on agentbreeder-sdk"
```

---

### Task 4: Create `Dockerfile.cli`

**Files:**
- Create: `Dockerfile.cli`

- [ ] **Step 1: Create `Dockerfile.cli`**

```dockerfile
FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/agentbreeder/agentbreeder"
LABEL org.opencontainers.image.description="AgentBreeder CLI for CI/CD pipelines"

RUN pip install --no-cache-dir agentbreeder

ENTRYPOINT ["agentbreeder"]
CMD ["--help"]
```

- [ ] **Step 2: Verify it builds (if Docker is available)**

Run:
```bash
docker build -f Dockerfile.cli -t agentbreeder/cli:local . 2>&1 || echo "Docker not available — skip"
```

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.cli
git commit -m "feat: add Dockerfile.cli for lightweight CLI image"
```

---

### Task 5: Create `dashboard/Dockerfile` and `dashboard/nginx.conf`

**Files:**
- Create: `dashboard/Dockerfile`
- Create: `dashboard/nginx.conf`

- [ ] **Step 1: Create `dashboard/nginx.conf`**

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    # SPA routing — all non-file requests go to index.html
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Proxy API requests to backend
    location /api/ {
        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Cache static assets
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

- [ ] **Step 2: Create `dashboard/Dockerfile`**

```dockerfile
FROM node:22-slim AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine

LABEL org.opencontainers.image.source="https://github.com/agentbreeder/agentbreeder"
LABEL org.opencontainers.image.description="AgentBreeder Dashboard"

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/Dockerfile dashboard/nginx.conf
git commit -m "feat: add dashboard Dockerfile and nginx config"
```

---

### Task 6: Update `release.yml` to publish SDK + Docker Hub + Homebrew

**Files:**
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1: Add SDK build job**

Add a new job `build-sdk` after `pre-release-check`:

```yaml
  build-sdk:
    name: Build SDK Package
    runs-on: ubuntu-latest
    needs: pre-release-check
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Build SDK wheel and sdist
        run: |
          pip install build
          cd sdk/python && python -m build

      - name: Verify SDK package
        run: |
          pip install twine
          twine check sdk/python/dist/*

      - name: Upload SDK build artifacts
        uses: actions/upload-artifact@v4
        with:
          name: sdk-dist
          path: sdk/python/dist/
```

- [ ] **Step 2: Add SDK PyPI publish job**

Add `publish-sdk-pypi` job — must run before `publish-pypi`:

```yaml
  publish-sdk-pypi:
    name: Publish SDK to PyPI
    runs-on: ubuntu-latest
    needs: github-release
    environment:
      name: pypi
      url: https://pypi.org/project/agentbreeder-sdk/
    permissions:
      id-token: write
    steps:
      - name: Download SDK dist
        uses: actions/download-artifact@v4
        with:
          name: sdk-dist
          path: dist/

      - name: Publish SDK to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
```

- [ ] **Step 3: Update `publish-pypi` to depend on `publish-sdk-pypi`**

Change the `needs` of the existing `publish-pypi` job:

```yaml
  publish-pypi:
    name: Publish to PyPI
    runs-on: ubuntu-latest
    needs: [github-release, publish-sdk-pypi]  # SDK must be on PyPI first
```

- [ ] **Step 4: Add Docker Hub login + CLI image to `build-images`**

In the `build-images` job, add Docker Hub login alongside GHCR, and add CLI image build:

```yaml
      - name: Log in to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      # After existing API and Dashboard builds, add:
      - name: Build and push CLI image
        uses: docker/build-push-action@v6
        with:
          file: ./Dockerfile.cli
          push: true
          platforms: linux/amd64,linux/arm64
          tags: |
            agentbreeder/cli:${{ steps.version.outputs.version }}
            agentbreeder/cli:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Also update the existing API and Dashboard image builds to push to Docker Hub in addition to GHCR. Add these tags to each:
```yaml
          tags: |
            ghcr.io/${{ github.repository }}/api:${{ steps.version.outputs.version }}
            ghcr.io/${{ github.repository }}/api:latest
            agentbreeder/api:${{ steps.version.outputs.version }}
            agentbreeder/api:latest
```
(Same pattern for dashboard.)

- [ ] **Step 5: Add Homebrew tap update job**

Add at the end of the workflow:

```yaml
  update-homebrew:
    name: Update Homebrew Tap
    runs-on: ubuntu-latest
    needs: publish-pypi
    steps:
      - name: Wait for PyPI propagation
        run: sleep 30

      - name: Update Homebrew formula
        uses: mislav/bump-homebrew-formula-action@v3
        with:
          formula-name: agentbreeder
          homebrew-tap: agentbreeder/homebrew-agentbreeder
          download-url: https://files.pythonhosted.org/packages/source/a/agentbreeder/agentbreeder-${{ needs.build-python.outputs.version }}.tar.gz
        env:
          COMMITTER_TOKEN: ${{ secrets.HOMEBREW_TAP_TOKEN }}
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "feat: extend release pipeline — SDK publish, Docker Hub, Homebrew tap"
```

---

### Task 7: Update `ci.yml` to build SDK package

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add SDK build step to `docker-build` job**

In the `docker-build` job, add SDK build verification before the Docker builds:

```yaml
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Build SDK package
        run: |
          pip install build
          cd sdk/python && python -m build

      - name: Build CLI package
        run: |
          pip install build
          python -m build
```

- [ ] **Step 2: Add CLI Docker image build**

Add after the existing Dashboard image build:

```yaml
      - name: Build CLI image
        uses: docker/build-push-action@v6
        with:
          file: ./Dockerfile.cli
          push: false
          tags: agentbreeder-cli:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "feat: add SDK and CLI image builds to CI pipeline"
```

---

### Task 8: Create Homebrew tap repository structure

**Files:**
- Create: `homebrew/Formula/agentbreeder.rb` (staged locally, for reference — actual repo is separate)
- Create: `homebrew/README.md`

Note: The actual Homebrew tap lives in a separate repo (`agentbreeder/homebrew-agentbreeder`). This task creates the formula files locally so they can be pushed to that repo.

- [ ] **Step 1: Create local `homebrew/` directory**

```bash
mkdir -p homebrew/Formula
```

- [ ] **Step 2: Create `homebrew/Formula/agentbreeder.rb`**

```ruby
class Agentbreeder < Formula
  include Language::Python::Virtualenv

  desc "Define Once. Deploy Anywhere. Govern Automatically."
  homepage "https://github.com/agentbreeder/agentbreeder"
  url "https://files.pythonhosted.org/packages/source/a/agentbreeder/agentbreeder-0.1.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256"
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

- [ ] **Step 3: Create `homebrew/README.md`**

```markdown
# homebrew-agentbreeder

Homebrew tap for [AgentBreeder](https://github.com/agentbreeder/agentbreeder).

## Install

```bash
brew tap agentbreeder/agentbreeder
brew install agentbreeder
```

## Update

```bash
brew update
brew upgrade agentbreeder
```
```

- [ ] **Step 4: Commit**

```bash
git add homebrew/
git commit -m "feat: add Homebrew formula template for tap repo"
```

---

### Task 9: Update README.md with install instructions

**Files:**
- Modify: `README.md` (install section)

- [ ] **Step 1: Read current README install section**

Read `README.md` and find the install/quickstart section.

- [ ] **Step 2: Add multi-channel install instructions**

Add or update the install section with:

```markdown
## Install

### PyPI (recommended)

```bash
# Full CLI + API server
pip install agentbreeder

# Lightweight SDK only
pip install agentbreeder-sdk
```

### Homebrew (macOS / Linux)

```bash
brew tap agentbreeder/agentbreeder
brew install agentbreeder
```

### Docker

```bash
# API server
docker pull agentbreeder/api
docker run -p 8000:8000 agentbreeder/api

# Dashboard
docker pull agentbreeder/dashboard
docker run -p 80:80 agentbreeder/dashboard

# CLI (for CI/CD pipelines)
docker pull agentbreeder/cli
docker run agentbreeder/cli deploy agent.yaml --target gcp
```
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add multi-channel install instructions (PyPI, Homebrew, Docker)"
```

---

### Task 10: Final verification and push

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest tests/unit/ -v`
Expected: all tests pass.

- [ ] **Step 2: Run lint**

Run: `python3 -m ruff check . && python3 -m ruff format --check .`
Expected: no errors.

- [ ] **Step 3: Verify no stale URLs**

Run:
```bash
grep -rn "open-agentbreeder" --include="*.py" --include="*.toml" --include="*.yml" --include="*.yaml" --include="*.md" --include="*.sh" --include="*.rb" .
```
Expected: zero matches.

- [ ] **Step 4: Verify SDK builds independently**

Run:
```bash
cd sdk/python && python3 -m build && cd ../..
```
Expected: successful build.

- [ ] **Step 5: Verify root package builds**

Run:
```bash
python3 -m build
```
Expected: successful build, wheel does NOT contain `sdk/`.

- [ ] **Step 6: Push all commits**

```bash
git push origin main
```
