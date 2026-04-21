# GraphRAG + Ollama + Website Animation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Ollama-based GraphRAG entity extraction, a sample agent, homepage animation, dashboard Graph UI, docs, and Playwright tests — all in the `feat-graphrag` worktree.

**Architecture:** Route entity extraction via model name prefix (`ollama/` → local Ollama `/api/chat`, anything else → Claude API). Add a `GraphTab` component to the dashboard's RAG builder that conditionally renders for graph/hybrid indexes. Animate the website hero with a pure CSS/SVG knowledge graph.

**Tech Stack:** Python/httpx (extraction), React/TypeScript/SVG (dashboard + animation), pytest + AsyncMock (backend tests), Playwright (E2E tests)

---

## File Map

**Worktree root:** `/Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag`

| Action | Path (relative to worktree) |
|--------|----------------------------|
| Modify | `api/services/graph_extraction.py` |
| Modify | `api/services/rag_service.py` |
| Modify | `tests/unit/test_graph_extraction.py` |
| Create | `examples/graphrag-ollama-agent/agent.yaml` |
| Create | `examples/graphrag-ollama-agent/knowledge_base/architecture.md` |
| Create | `examples/graphrag-ollama-agent/knowledge_base/agent-yaml.md` |
| Create | `examples/graphrag-ollama-agent/knowledge_base/cli-commands.md` |
| Create | `examples/graphrag-ollama-agent/ingest.py` |
| Create | `examples/graphrag-ollama-agent/README.md` |
| Modify | `website/components/hero.tsx` |
| Modify | `dashboard/src/lib/api.ts` |
| Create | `dashboard/src/components/GraphTab.tsx` |
| Modify | `dashboard/src/pages/rag-builder.tsx` |
| Create | `dashboard/tests/e2e/rag-graph.spec.ts` |
| Modify | `README.md` |
| Modify | `website/content/docs/graphrag.mdx` |

---

## Task 1: Ollama entity extraction backend

**Files:**
- Modify: `api/services/rag_service.py` — add `DEFAULT_OLLAMA_ENTITY_MODEL`
- Modify: `api/services/graph_extraction.py` — add `_call_ollama`, update routing
- Modify: `tests/unit/test_graph_extraction.py` — add Ollama tests

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_graph_extraction.py` (after the existing imports, add `MagicMock` and `json` if not already there):

```python
# ---------------------------------------------------------------------------
# Ollama routing tests
# ---------------------------------------------------------------------------
import json as _json  # alias to avoid shadowing the module-level json import

from api.services.graph_extraction import _call_ollama
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_extract_entities_routes_to_ollama_for_ollama_prefix():
    mock_result = {
        "entities": [{"entity": "AgentBreeder", "type": "concept", "description": "A platform"}],
        "relationships": [],
    }
    with patch("api.services.graph_extraction._call_ollama", new_callable=AsyncMock) as mock:
        mock.return_value = mock_result
        nodes, edges = await extract_entities("test text", model="ollama/qwen2.5:7b", cache={})
    mock.assert_called_once_with("test text", "qwen2.5:7b")
    assert len(nodes) == 1
    assert nodes[0].entity == "AgentBreeder"


@pytest.mark.asyncio
async def test_extract_entities_routes_to_claude_for_non_ollama():
    with patch("api.services.graph_extraction._call_claude", new_callable=AsyncMock) as mock:
        mock.return_value = {"entities": [], "relationships": []}
        await extract_entities("test text", model="claude-haiku-4-5-20251001", cache={})
    mock.assert_called_once()


@pytest.mark.asyncio
async def test_call_ollama_returns_empty_on_http_error():
    with patch("api.services.graph_extraction.httpx.AsyncClient") as MockClient:
        inst = AsyncMock()
        inst.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await _call_ollama("text", "qwen2.5:7b")
    assert result == {"entities": [], "relationships": []}


@pytest.mark.asyncio
async def test_call_ollama_returns_empty_on_bad_json():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "not-json"}}
    with patch("api.services.graph_extraction.httpx.AsyncClient") as MockClient:
        inst = AsyncMock()
        inst.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await _call_ollama("text", "qwen2.5:7b")
    assert result == {"entities": [], "relationships": []}


@pytest.mark.asyncio
async def test_call_ollama_parses_valid_entities():
    payload = {
        "entities": [{"entity": "GraphRAG", "type": "concept", "description": "Graph-based RAG"}],
        "relationships": [],
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"message": {"content": _json.dumps(payload)}}
    with patch("api.services.graph_extraction.httpx.AsyncClient") as MockClient:
        inst = AsyncMock()
        inst.post = AsyncMock(return_value=mock_resp)
        MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        result = await _call_ollama("GraphRAG text", "qwen2.5:7b")
    assert result["entities"][0]["entity"] == "GraphRAG"


@pytest.mark.asyncio
async def test_call_ollama_uses_ollama_base_url_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama-server:11434")
    captured_urls: list[str] = []

    async def fake_post(url: str, **kwargs):  # type: ignore[return]
        captured_urls.append(url)
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json.return_value = {"message": {"content": '{"entities":[],"relationships":[]}'}}
        return m

    with patch("api.services.graph_extraction.httpx.AsyncClient") as MockClient:
        inst = AsyncMock()
        inst.post = fake_post
        MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        await _call_ollama("text", "qwen2.5:7b")
    assert captured_urls[0] == "http://ollama-server:11434/api/chat"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
python -m pytest tests/unit/test_graph_extraction.py -k "ollama" -v 2>&1 | tail -20
```

Expected: `ImportError: cannot import name '_call_ollama'` or similar.

- [ ] **Step 3: Add `DEFAULT_OLLAMA_ENTITY_MODEL` to `api/services/rag_service.py`**

Find the line `DEFAULT_ENTITY_MODEL = "claude-haiku-4-5-20251001"` and add immediately after:

```python
DEFAULT_OLLAMA_ENTITY_MODEL = "ollama/qwen2.5:7b"
```

- [ ] **Step 4: Implement Ollama extraction in `api/services/graph_extraction.py`**

**4a.** No import change needed in `graph_extraction.py` — `DEFAULT_OLLAMA_ENTITY_MODEL` lives in `rag_service.py` for external callers; `graph_extraction.py` routes by prefix string, not the constant.

**4b.** Add `_call_ollama` function right after the existing `_call_claude` function (before `_parse_extraction_result`):

```python
async def _call_ollama(text: str, model_name: str) -> dict[str, Any]:
    """Call local Ollama chat API to extract entities and relationships.

    model_name is the bare name (e.g. "qwen2.5:7b"), without the "ollama/" prefix.
    Uses OLLAMA_BASE_URL env var; defaults to http://localhost:11434.
    On any error: logs warning and returns empty result (never raises).
    """
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    system_prompt = (
        "You are an information extraction assistant. "
        "Extract entities and relationships from text. "
        "Return ONLY valid JSON — no prose."
    )
    user_prompt = (
        f"Extract from the following text chunk:\n"
        f"<chunk>{text}</chunk>\n\n"
        "Return JSON with this exact schema:\n"
        "{\n"
        '  "entities": [\n'
        '    {"entity": "string", "type": "organization|person|concept|location|event|other", "description": "string"}\n'
        "  ],\n"
        '  "relationships": [\n'
        '    {"subject": "entity name", "predicate": "relationship verb", "object": "entity name"}\n'
        "  ]\n"
        "}"
    )
    payload = {
        "model": model_name,
        "format": "json",
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["message"]["content"]
            return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("Entity extraction (Ollama): failed to parse JSON response: %s", e)
        return {"entities": [], "relationships": []}
    except httpx.HTTPError as e:
        logger.warning("Entity extraction (Ollama): HTTP error calling Ollama: %s", e)
        return {"entities": [], "relationships": []}
    except (KeyError, IndexError) as e:
        logger.warning("Entity extraction (Ollama): unexpected response structure: %s", e)
        return {"entities": [], "relationships": []}
    except Exception as e:
        logger.warning("Entity extraction (Ollama): unexpected error: %s", e)
        return {"entities": [], "relationships": []}
```

**4c.** Update routing in `extract_entities` — find the line `raw = await _call_claude(text, model)` and replace with:

```python
    if model.startswith("ollama/"):
        raw = await _call_ollama(text, model[len("ollama/"):])
    else:
        raw = await _call_claude(text, model)
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
python -m pytest tests/unit/test_graph_extraction.py -v 2>&1 | tail -30
```

Expected: all existing + new Ollama tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
git add api/services/rag_service.py api/services/graph_extraction.py tests/unit/test_graph_extraction.py
git commit -m "feat(graphrag): add Ollama entity extraction via /api/chat with format:json"
```

---

## Task 2: Sample agent knowledge base files

**Files:**
- Create: `examples/graphrag-ollama-agent/knowledge_base/architecture.md`
- Create: `examples/graphrag-ollama-agent/knowledge_base/agent-yaml.md`
- Create: `examples/graphrag-ollama-agent/knowledge_base/cli-commands.md`

- [ ] **Step 1: Create architecture.md**

```bash
mkdir -p /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag/examples/graphrag-ollama-agent/knowledge_base
```

Create `examples/graphrag-ollama-agent/knowledge_base/architecture.md` with:

```markdown
# AgentBreeder Architecture

## Core Deploy Pipeline

AgentBreeder executes every deployment through a fixed, ordered pipeline:

1. **Parse & Validate YAML** — the agent.yaml is loaded and validated against a JSON Schema
2. **RBAC Check** — permissions are validated before any infrastructure work begins
3. **Dependency Resolution** — all refs (tools, prompts, knowledge bases) are fetched from the registry
4. **Container Build** — a framework-specific Dockerfile is generated and built
5. **Infrastructure Provision** — cloud resources are provisioned via Pulumi
6. **Deploy & Health Check** — the container is deployed and health-checked
7. **Auto-Register in Registry** — the agent is registered in the org-wide registry
8. **Return Endpoint URL** — the live URL is returned to the caller

Every step is atomic. If any step fails, the entire deploy rolls back.

## Governance

Every `agentbreeder deploy` automatically:
- Validates RBAC before any action
- Registers the agent in the registry after success
- Attributes cost to the deploying team
- Writes an audit log entry

There is no "quick deploy" that skips governance. This is intentional.

## Three-Tier Builder Model

AgentBreeder supports three ways to build agents:

- **No Code** — Visual drag-and-drop UI with ReactFlow canvas
- **Low Code** — YAML config (agent.yaml, orchestration.yaml) in any IDE
- **Full Code** — Python/TypeScript SDK with programmatic control

All three compile to the same internal representation and use the same deploy pipeline. Users can "eject" from No Code to YAML, or from YAML to Full Code with the `agentbreeder eject` command.

## Framework Agnosticism

The engine/runtimes/ layer abstracts all framework differences. Supported frameworks:
- LangGraph
- CrewAI
- Claude SDK (Anthropic)
- OpenAI Agents
- Google ADK
- Custom (bring your own)

## Multi-Cloud Deploy Targets

AgentBreeder deploys to: AWS ECS Fargate, AWS App Runner, AWS EKS, GCP Cloud Run, GCP GKE, Azure Container Apps, Kubernetes (self-hosted), and local Docker Compose.
```

- [ ] **Step 2: Create agent-yaml.md**

Create `examples/graphrag-ollama-agent/knowledge_base/agent-yaml.md`:

```markdown
# agent.yaml Specification

The agent.yaml file is the single config file that defines an agent.

## Required Fields

- `name` — slug-friendly agent name
- `version` — SemVer (e.g. "1.0.0")
- `team` — must match a team registered in the registry
- `owner` — email of the responsible engineer
- `framework` — one of: langgraph, crewai, claude_sdk, openai_agents, google_adk, custom
- `model.primary` — model reference (e.g. claude-sonnet-4, gpt-4o, ollama/qwen2.5:7b)
- `deploy.cloud` — one of: aws, gcp, azure, kubernetes, local, claude-managed

## Model Configuration

```yaml
model:
  primary: claude-sonnet-4
  fallback: gpt-4o          # used if primary unavailable
  gateway: litellm          # optional org gateway
  temperature: 0.7
  max_tokens: 4096
```

## Knowledge Bases

Knowledge bases are referenced from the registry:

```yaml
knowledge_bases:
  - ref: kb/product-docs
  - ref: kb/return-policy
```

## Deployment Configuration

```yaml
deploy:
  cloud: aws
  runtime: ecs-fargate      # default for aws
  region: us-east-1
  scaling:
    min: 1
    max: 10
    target_cpu: 70
  resources:
    cpu: "1"
    memory: "2Gi"
  secrets:
    - OPENAI_API_KEY
    - ZENDESK_API_KEY
```

## Access Control

```yaml
access:
  visibility: team          # public | team | private
  allowed_callers:
    - team:engineering
  require_approval: false
```

## RAG Knowledge Base Schema

When creating a knowledge base with GraphRAG:

```yaml
index_type: graph           # vector | graph | hybrid
embedding_model: ollama/nomic-embed-text
entity_model: ollama/qwen2.5:7b
max_hops: 2
```
```

- [ ] **Step 3: Create cli-commands.md**

Create `examples/graphrag-ollama-agent/knowledge_base/cli-commands.md`:

```markdown
# AgentBreeder CLI Reference

## Core Commands

### agentbreeder init
Scaffold a new agent project in the current directory. Generates agent.yaml, Dockerfile, and example code.

```bash
agentbreeder init --framework langgraph --cloud aws
```

### agentbreeder deploy
Deploy an agent from an agent.yaml file. This is the primary command.

```bash
agentbreeder deploy                      # deploy from current directory
agentbreeder deploy --target aws         # explicit cloud target
agentbreeder deploy --target local       # deploy locally via Docker Compose
agentbreeder deploy agent.yaml           # explicit file path
```

Governance is automatic: RBAC check, container build, deploy, registry registration.

### agentbreeder validate
Validate an agent.yaml against the JSON Schema and registry refs. Does not deploy.

```bash
agentbreeder validate agent.yaml
```

### agentbreeder chat
Open an interactive chat session with a deployed agent.

```bash
agentbreeder chat --agent my-agent
agentbreeder chat --agent my-agent "What can you help with?"
```

### agentbreeder eject
Eject from No Code to YAML, or from YAML to Full Code (Python SDK).

```bash
agentbreeder eject --to yaml              # generates agent.yaml from visual builder state
agentbreeder eject --to code              # generates Python SDK code from agent.yaml
```

### agentbreeder eval
Run evaluations on a deployed agent.

```bash
agentbreeder eval --agent my-agent --dataset evals/qa.jsonl
```

## Registry Commands

### agentbreeder search
Search the org registry for agents, tools, prompts, models.

```bash
agentbreeder search "customer support"
agentbreeder search --type tool zendesk
```

### agentbreeder list
List registered agents.

```bash
agentbreeder list
agentbreeder list --team engineering
```

## Secrets Management

### agentbreeder secret
Manage secrets across backends (env, AWS Secrets Manager, GCP Secret Manager, Vault).

```bash
agentbreeder secret set OPENAI_API_KEY --backend aws
agentbreeder secret get OPENAI_API_KEY
agentbreeder secret list
```
```

- [ ] **Step 4: Commit**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
git add examples/graphrag-ollama-agent/knowledge_base/
git commit -m "feat(example): add graphrag-ollama-agent knowledge base documents"
```

---

## Task 3: Sample agent yaml + ingest script + README

**Files:**
- Create: `examples/graphrag-ollama-agent/agent.yaml`
- Create: `examples/graphrag-ollama-agent/ingest.py`
- Create: `examples/graphrag-ollama-agent/README.md`

- [ ] **Step 1: Create agent.yaml**

```yaml
name: graphrag-demo-agent
version: "1.0.0"
description: "Demo agent that answers questions using GraphRAG over AgentBreeder technical docs"
team: examples
owner: demo@agentbreeder.dev
framework: claude_sdk
model:
  primary: ollama/qwen2.5:7b
knowledge_bases:
  - ref: kb/agentbreeder-docs
deploy:
  cloud: local
```

- [ ] **Step 2: Create ingest.py**

```python
#!/usr/bin/env python3
"""Ingest AgentBreeder knowledge base documents into a local GraphRAG index.

Prerequisites:
    1. Local stack running: docker compose up -d
    2. Ollama running with models pulled:
       ollama pull qwen2.5:7b
       ollama pull nomic-embed-text

Usage:
    python examples/graphrag-ollama-agent/ingest.py
    python examples/graphrag-ollama-agent/ingest.py --api-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

KNOWLEDGE_BASE_DIR = Path(__file__).parent / "knowledge_base"
DEFAULT_API_URL = "http://localhost:8000"
INDEX_NAME = "agentbreeder-docs"


def create_index(client: httpx.Client, api_url: str) -> str:
    """Create (or find existing) graph index. Returns index_id."""
    # Check if index already exists
    resp = client.get(f"{api_url}/api/v1/rag/indexes")
    resp.raise_for_status()
    indexes = resp.json().get("data", [])
    for idx in indexes:
        if idx.get("name") == INDEX_NAME:
            print(f"Using existing index: {idx['id']}")
            return idx["id"]

    # Create new graph index
    resp = client.post(
        f"{api_url}/api/v1/rag/indexes",
        json={
            "name": INDEX_NAME,
            "description": "AgentBreeder technical documentation for GraphRAG demo",
            "embedding_model": "ollama/nomic-embed-text",
            "entity_model": "ollama/qwen2.5:7b",
            "index_type": "graph",
            "chunk_strategy": "recursive",
            "chunk_size": 512,
            "chunk_overlap": 64,
            "max_hops": 2,
        },
    )
    resp.raise_for_status()
    idx = resp.json()["data"]
    print(f"Created index: {idx['id']}")
    return idx["id"]


def ingest_file(client: httpx.Client, api_url: str, index_id: str, path: Path) -> str:
    """Upload a file for ingestion. Returns job_id."""
    with path.open("rb") as f:
        resp = client.post(
            f"{api_url}/api/v1/rag/indexes/{index_id}/ingest",
            files={"files": (path.name, f, "text/markdown")},
            timeout=120.0,
        )
    resp.raise_for_status()
    job = resp.json()["data"]
    return job["id"]


def wait_for_job(client: httpx.Client, api_url: str, index_id: str, job_id: str) -> dict:
    """Poll job status until complete. Returns final job dict."""
    for _ in range(120):
        resp = client.get(f"{api_url}/api/v1/rag/indexes/{index_id}/ingest/{job_id}")
        resp.raise_for_status()
        job = resp.json()["data"]
        status = job.get("status", "pending")
        pct = job.get("progress_pct", 0)
        print(f"  {status} ({pct:.0f}%)...", end="\r")
        if status in ("completed", "failed"):
            print()
            return job
        time.sleep(1)
    raise TimeoutError(f"Job {job_id} did not complete in 120 seconds")


def print_graph_stats(client: httpx.Client, api_url: str, index_id: str) -> None:
    """Print graph metadata after ingestion."""
    resp = client.get(f"{api_url}/api/v1/rag/indexes/{index_id}/graph")
    if resp.status_code != 200:
        print("Could not fetch graph stats (index may not be graph type)")
        return
    meta = resp.json()["data"]
    print("\n=== Graph Statistics ===")
    print(f"Nodes (entities):  {meta['node_count']}")
    print(f"Edges (relations): {meta['edge_count']}")
    print("\nEntity types:")
    for et in meta.get("entity_types", []):
        print(f"  {et['type']}: {et['count']}")
    print("\nTop entities:")
    for ent in meta.get("top_entities", [])[:5]:
        print(f"  {ent['entity']} ({ent['type']}) — {ent['chunk_count']} chunks")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest AgentBreeder docs into GraphRAG index")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    args = parser.parse_args()

    md_files = sorted(KNOWLEDGE_BASE_DIR.glob("*.md"))
    if not md_files:
        print(f"No .md files found in {KNOWLEDGE_BASE_DIR}")
        sys.exit(1)

    with httpx.Client(timeout=30.0) as client:
        # Health check
        try:
            client.get(f"{args.api_url}/health").raise_for_status()
        except Exception as e:
            print(f"Cannot reach API at {args.api_url}: {e}")
            print("Make sure `docker compose up -d` is running.")
            sys.exit(1)

        index_id = create_index(client, args.api_url)

        for md_file in md_files:
            print(f"\nIngesting {md_file.name}...")
            job_id = ingest_file(client, args.api_url, index_id, md_file)
            job = wait_for_job(client, args.api_url, index_id, job_id)
            if job["status"] == "failed":
                print(f"  FAILED: {job.get('error', 'unknown error')}")
            else:
                chunks = job.get("total_chunks", 0)
                print(f"  Done — {chunks} chunks embedded")

        print_graph_stats(client, args.api_url, index_id)
        print(f"\nIndex ID for querying: {index_id}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create README.md**

```markdown
# GraphRAG + Ollama Sample Agent

Demonstrates GraphRAG (graph-indexed knowledge base) using a local Ollama model for both
entity extraction and embeddings. No API keys required.

## Prerequisites

1. **Ollama** — [install](https://ollama.com/download), then pull the required models:

```bash
ollama pull qwen2.5:7b          # entity extraction
ollama pull nomic-embed-text    # embeddings
```

2. **Local stack running:**

```bash
docker compose up -d            # from the repo root
```

3. **Python dependencies** (already installed if you ran `pip install -e .`):

```bash
pip install httpx
```

## Ingest the Knowledge Base

```bash
python examples/graphrag-ollama-agent/ingest.py
```

Expected output:

```
Created index: <uuid>

Ingesting architecture.md...
  completed (100%)...
  Done — 8 chunks embedded

Ingesting agent-yaml.md...
  ...

=== Graph Statistics ===
Nodes (entities):  42
Edges (relations): 31

Entity types:
  concept: 28
  organization: 5
  ...

Top entities:
  AgentBreeder (concept) — 12 chunks
  Deploy Pipeline (concept) — 8 chunks
  ...
```

## Query the Graph

```bash
agentbreeder chat --agent graphrag-demo-agent \
  "What are the steps in the AgentBreeder deploy pipeline?"
```

Or query the search API directly:

```bash
curl -s -X POST http://localhost:8000/api/v1/rag/search \
  -H "Content-Type: application/json" \
  -d '{
    "index_id": "<index-id-from-ingest>",
    "query": "how does RBAC work in AgentBreeder?",
    "hops": 2
  }' | jq '.data.hits[].text'
```

## What GraphRAG Does Differently

Standard RAG retrieves chunks by vector similarity. GraphRAG also:

1. Extracts entities and relationships from each chunk using qwen2.5:7b
2. Builds a knowledge graph (nodes = entities, edges = relationships)
3. At query time, finds seed entities matching the query, then traverses the graph (BFS up to `max_hops`) to pull in related context

This gives richer, multi-hop answers for questions that span multiple documents.
```

- [ ] **Step 4: Commit**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
git add examples/graphrag-ollama-agent/
git commit -m "feat(example): add graphrag-ollama-agent with ingest script and README"
```

---

## Task 4: Homepage animation

**Files:**
- Modify: `website/components/hero.tsx`

The Hero function body starts at line 53. The background glow `div` (with `pointer-events-none absolute inset-0 overflow-hidden`) contains three gradient divs. The animated SVG goes inside that div, after the third gradient div.

- [ ] **Step 1: Read the closing structure of the background glow div**

```bash
sed -n '57,80p' /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag/website/components/hero.tsx
```

Confirm the third gradient div ends with `/>` followed by `</div>` closing the pointer-events-none wrapper.

- [ ] **Step 2: Insert the animated SVG**

Find this text in `website/components/hero.tsx`:

```tsx
          style={{ background: 'radial-gradient(circle, rgba(96,165,250,0.04) 0%, transparent 65%)' }}
        />
      </div>
```

Replace with:

```tsx
          style={{ background: 'radial-gradient(circle, rgba(96,165,250,0.04) 0%, transparent 65%)' }}
        />

        {/* Animated knowledge graph */}
        <svg
          className="absolute inset-0 h-full w-full opacity-[0.12]"
          viewBox="0 0 400 300"
          xmlns="http://www.w3.org/2000/svg"
          aria-hidden="true"
        >
          <defs>
            <style>{`
              .ab-edge { stroke-dasharray: 160; animation: ab-edge-travel 3s linear infinite; }
              .ab-e2 { animation-delay: 0.75s; }
              .ab-e3 { animation-delay: 1.5s; }
              .ab-e4 { animation-delay: 2.25s; }
              .ab-node { animation: ab-node-pulse 2s ease-in-out infinite; }
              .ab-n2 { animation-delay: 0.5s; }
              .ab-n3 { animation-delay: 1s; }
              .ab-n4 { animation-delay: 1.5s; }
              @keyframes ab-edge-travel {
                0% { stroke-dashoffset: 160; }
                100% { stroke-dashoffset: -160; }
              }
              @keyframes ab-node-pulse {
                0%, 100% { opacity: 0.4; }
                50% { opacity: 1; }
              }
            `}</style>
          </defs>
          {/* Edges: center → outer nodes */}
          <line x1="200" y1="150" x2="310" y2="65" stroke="#22c55e" strokeWidth="1.5" className="ab-edge" />
          <line x1="200" y1="150" x2="310" y2="235" stroke="#a855f7" strokeWidth="1.5" className="ab-edge ab-e2" />
          <line x1="200" y1="150" x2="90" y2="235" stroke="#3b82f6" strokeWidth="1.5" className="ab-edge ab-e3" />
          <line x1="200" y1="150" x2="90" y2="65" stroke="#f59e0b" strokeWidth="1.5" className="ab-edge ab-e4" />
          {/* Outer nodes */}
          <circle cx="310" cy="65" r="7" fill="#22c55e" className="ab-node" />
          <circle cx="310" cy="235" r="7" fill="#a855f7" className="ab-node ab-n2" />
          <circle cx="90" cy="235" r="7" fill="#3b82f6" className="ab-node ab-n3" />
          <circle cx="90" cy="65" r="7" fill="#f59e0b" className="ab-node ab-n4" />
          {/* Center node */}
          <circle cx="200" cy="150" r="9" fill="white" className="ab-node" />
          {/* Labels */}
          <text x="310" y="51" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">deploy</text>
          <text x="328" y="250" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">RBAC</text>
          <text x="72" y="250" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">cost</text>
          <text x="90" y="51" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">registry</text>
          <text x="200" y="137" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">agent.yaml</text>
        </svg>
      </div>
```

- [ ] **Step 3: Type-check the website**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag/website
npx tsc -b --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
git add website/components/hero.tsx
git commit -m "feat(website): add animated knowledge graph to hero section"
```

---

## Task 5: Dashboard API client — add graph types and methods

**Files:**
- Modify: `dashboard/src/lib/api.ts`

- [ ] **Step 1: Add `index_type` and `entity_model` to `VectorIndex` interface**

Find in `dashboard/src/lib/api.ts`:

```typescript
export interface VectorIndex {
  id: string;
  name: string;
  description: string;
  embedding_model: string;
  chunk_strategy: string;
  chunk_size: number;
  chunk_overlap: number;
  dimensions: number;
  source: string;
  doc_count: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}
```

Replace with:

```typescript
export interface VectorIndex {
  id: string;
  name: string;
  description: string;
  embedding_model: string;
  entity_model: string;
  chunk_strategy: string;
  chunk_size: number;
  chunk_overlap: number;
  dimensions: number;
  source: string;
  index_type: "vector" | "graph" | "hybrid";
  doc_count: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}
```

- [ ] **Step 2: Add new graph interfaces**

After the `RAGSearchHit` interface, add:

```typescript
export interface GraphEntity {
  id: string;
  entity: string;
  entity_type: string;
  description: string;
  chunk_ids: string[];
}

export interface GraphRelationship {
  id: string;
  subject_id: string;
  predicate: string;
  object_id: string;
  subject_entity: string;
  object_entity: string;
  chunk_ids: string[];
  weight: number;
}

export interface GraphMetadata {
  index_id: string;
  index_type: string;
  node_count: number;
  edge_count: number;
  entity_types: { type: string; count: number }[];
  top_entities: { entity: string; type: string; chunk_count: number }[];
}
```

- [ ] **Step 3: Add API methods to `api.rag`**

Find the `deleteIndex` method in the `rag` object:
```typescript
    deleteIndex: (id: string) =>
      request<{ deleted: boolean }>(`/rag/indexes/${id}`, { method: "DELETE" }),
```

Add immediately after:

```typescript
    getGraphMeta: (indexId: string) =>
      request<GraphMetadata>(`/rag/indexes/${indexId}/graph`),
    listEntities: (indexId: string, params?: { page?: number; per_page?: number; entity_type?: string }) => {
      const qs = new URLSearchParams();
      if (params?.page) qs.set("page", String(params.page));
      if (params?.per_page) qs.set("per_page", String(params.per_page));
      if (params?.entity_type) qs.set("entity_type", params.entity_type);
      const q = qs.toString();
      return request<GraphEntity[]>(`/rag/indexes/${indexId}/entities${q ? `?${q}` : ""}`);
    },
    listRelationships: (indexId: string, params?: { page?: number; per_page?: number; predicate?: string }) => {
      const qs = new URLSearchParams();
      if (params?.page) qs.set("page", String(params.page));
      if (params?.per_page) qs.set("per_page", String(params.per_page));
      if (params?.predicate) qs.set("predicate", params.predicate);
      const q = qs.toString();
      return request<GraphRelationship[]>(`/rag/indexes/${indexId}/relationships${q ? `?${q}` : ""}`);
    },
```

- [ ] **Step 4: Type-check dashboard**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag/dashboard
npx tsc -b --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
git add dashboard/src/lib/api.ts
git commit -m "feat(dashboard): add graph entity/relationship types and API methods"
```

---

## Task 6: GraphTab component

**Files:**
- Create: `dashboard/src/components/GraphTab.tsx`

- [ ] **Step 1: Create the component**

Create `dashboard/src/components/GraphTab.tsx`:

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type GraphEntity, type GraphRelationship } from "@/lib/api";
import { Loader2 } from "lucide-react";

interface GraphTabProps {
  indexId: string;
}

const ENTITY_TYPE_COLORS: Record<string, string> = {
  concept: "#22c55e",
  organization: "#a855f7",
  person: "#3b82f6",
  location: "#f59e0b",
  event: "#ef4444",
  other: "#6b7280",
};

function entityColor(type: string): string {
  return ENTITY_TYPE_COLORS[type] ?? ENTITY_TYPE_COLORS.other;
}

function EntityTypeBadge({ type }: { type: string }) {
  const color = entityColor(type);
  return (
    <span
      className="rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase"
      style={{ background: `${color}22`, color }}
    >
      {type}
    </span>
  );
}

// --- Ego Graph SVG ---

interface EgoGraphProps {
  center: GraphEntity;
  neighbors: { entity: GraphEntity; predicate: string }[];
  onSelectEntity: (entity: GraphEntity) => void;
}

function EgoGraph({ center, neighbors, onSelectEntity }: EgoGraphProps) {
  const W = 360;
  const H = 280;
  const cx = W / 2;
  const cy = H / 2;
  const radius = 100;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${W} ${H}`}
      className="select-none"
      xmlns="http://www.w3.org/2000/svg"
    >
      {neighbors.map((n, i) => {
        const angle = (2 * Math.PI * i) / Math.max(neighbors.length, 1) - Math.PI / 2;
        const nx = cx + radius * Math.cos(angle);
        const ny = cy + radius * Math.sin(angle);
        const color = entityColor(n.entity.entity_type);
        const midX = (cx + nx) / 2;
        const midY = (cy + ny) / 2;
        return (
          <g key={n.entity.id}>
            <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="#374151" strokeWidth="1" />
            <text
              x={midX}
              y={midY - 3}
              textAnchor="middle"
              fill="#6b7280"
              fontSize="8"
              fontFamily="monospace"
            >
              {n.predicate.length > 12 ? n.predicate.slice(0, 12) + "…" : n.predicate}
            </text>
            <circle
              cx={nx}
              cy={ny}
              r="18"
              fill="#1c1c1e"
              stroke={color}
              strokeWidth="1.5"
              className="cursor-pointer hover:opacity-80"
              onClick={() => onSelectEntity(n.entity)}
            />
            <text
              x={nx}
              y={ny + 3}
              textAnchor="middle"
              fill={color}
              fontSize="7"
              fontFamily="monospace"
            >
              {n.entity.entity.length > 9 ? n.entity.entity.slice(0, 9) + "…" : n.entity.entity}
            </text>
          </g>
        );
      })}
      {/* Center node */}
      <circle cx={cx} cy={cy} r="22" fill="#1c1c1e" stroke="white" strokeWidth="2" />
      <text x={cx} y={cy + 4} textAnchor="middle" fill="white" fontSize="7" fontFamily="monospace">
        {center.entity.length > 11 ? center.entity.slice(0, 11) + "…" : center.entity}
      </text>
    </svg>
  );
}

// --- Main Component ---

export function GraphTab({ indexId }: GraphTabProps) {
  const [page, setPage] = useState(1);
  const [entityTypeFilter, setEntityTypeFilter] = useState<string>("");
  const [selectedEntity, setSelectedEntity] = useState<GraphEntity | null>(null);

  const PER_PAGE = 20;

  const { data: metaResp, isLoading: metaLoading } = useQuery({
    queryKey: ["graph-meta", indexId],
    queryFn: () => api.rag.getGraphMeta(indexId),
  });

  const { data: entitiesResp, isLoading: entitiesLoading } = useQuery({
    queryKey: ["graph-entities", indexId, page, entityTypeFilter],
    queryFn: () =>
      api.rag.listEntities(indexId, {
        page,
        per_page: PER_PAGE,
        entity_type: entityTypeFilter || undefined,
      }),
  });

  const { data: relsResp } = useQuery({
    queryKey: ["graph-rels", indexId],
    queryFn: () => api.rag.listRelationships(indexId, { per_page: 200 }),
    enabled: !!selectedEntity,
  });

  const meta = metaResp?.data;
  const entities = entitiesResp?.data ?? [];
  const totalEntities = entitiesResp?.meta?.total ?? 0;
  const totalPages = Math.ceil(totalEntities / PER_PAGE);
  const allRels: GraphRelationship[] = relsResp?.data ?? [];

  // Build ego graph neighbors for selected entity
  const neighbors: { entity: GraphEntity; predicate: string }[] = [];
  if (selectedEntity) {
    const neighborIds = new Set<string>();
    const neighborPredicates = new Map<string, string>();
    for (const rel of allRels) {
      if (rel.subject_id === selectedEntity.id && !neighborIds.has(rel.object_id)) {
        neighborIds.add(rel.object_id);
        neighborPredicates.set(rel.object_id, rel.predicate);
      } else if (rel.object_id === selectedEntity.id && !neighborIds.has(rel.subject_id)) {
        neighborIds.add(rel.subject_id);
        neighborPredicates.set(rel.subject_id, rel.predicate);
      }
    }
    for (const ent of entities) {
      if (neighborIds.has(ent.id)) {
        neighbors.push({ entity: ent, predicate: neighborPredicates.get(ent.id) ?? "related" });
      }
    }
  }

  const entityTypes = meta?.entity_types?.map((et) => et.type) ?? [];

  return (
    <div className="flex h-full flex-col gap-4 p-4">
      {/* Stats bar */}
      {metaLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" /> Loading graph stats...
        </div>
      ) : meta ? (
        <div className="flex items-center gap-4 rounded-md border border-border bg-muted/30 px-4 py-2">
          <div className="text-center">
            <p className="text-xs text-muted-foreground">Entities</p>
            <p className="text-lg font-semibold">{meta.node_count}</p>
          </div>
          <div className="h-8 w-px bg-border" />
          <div className="text-center">
            <p className="text-xs text-muted-foreground">Relationships</p>
            <p className="text-lg font-semibold">{meta.edge_count}</p>
          </div>
          <div className="h-8 w-px bg-border" />
          <div className="flex flex-wrap gap-1">
            {meta.entity_types.map((et) => (
              <EntityTypeBadge key={et.type} type={et.type} />
            ))}
          </div>
        </div>
      ) : null}

      {/* Split panel */}
      <div className="grid flex-1 grid-cols-[280px_1fr] gap-4 overflow-hidden">
        {/* Left: Entity list */}
        <div className="flex flex-col overflow-hidden rounded-md border border-border">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider flex-1">
              Entities
            </span>
            {entityTypes.length > 0 && (
              <select
                className="rounded border border-border bg-background px-1.5 py-0.5 text-xs text-foreground"
                value={entityTypeFilter}
                onChange={(e) => { setEntityTypeFilter(e.target.value); setPage(1); }}
              >
                <option value="">All types</option>
                {entityTypes.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            )}
          </div>

          <div className="flex-1 overflow-y-auto">
            {entitiesLoading ? (
              <div className="flex h-20 items-center justify-center">
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
              </div>
            ) : entities.length === 0 ? (
              <p className="p-4 text-xs text-muted-foreground">No entities found.</p>
            ) : (
              entities.map((ent) => (
                <button
                  key={ent.id}
                  className={`w-full border-b border-border px-3 py-2 text-left transition-colors hover:bg-muted/50 ${
                    selectedEntity?.id === ent.id ? "bg-muted" : ""
                  }`}
                  onClick={() => setSelectedEntity(ent)}
                >
                  <div className="flex items-center gap-2">
                    <span className="truncate text-xs font-medium">{ent.entity}</span>
                    <EntityTypeBadge type={ent.entity_type} />
                  </div>
                  {ent.description && (
                    <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                      {ent.description}
                    </p>
                  )}
                </button>
              ))
            )}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t border-border px-3 py-1.5">
              <button
                className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-40"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                ← Prev
              </button>
              <span className="text-[10px] text-muted-foreground">{page} / {totalPages}</span>
              <button
                className="text-xs text-muted-foreground hover:text-foreground disabled:opacity-40"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next →
              </button>
            </div>
          )}
        </div>

        {/* Right: Ego graph */}
        <div className="flex flex-col overflow-hidden rounded-md border border-border">
          {selectedEntity ? (
            <>
              <div className="border-b border-border px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium">{selectedEntity.entity}</span>
                  <EntityTypeBadge type={selectedEntity.entity_type} />
                </div>
                {selectedEntity.description && (
                  <p className="mt-0.5 text-[10px] text-muted-foreground">{selectedEntity.description}</p>
                )}
              </div>
              <div className="flex flex-1 items-center justify-center p-4">
                {neighbors.length === 0 ? (
                  <div className="text-center">
                    <div
                      className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full border-2 border-white/30"
                    >
                      <span className="text-xs font-mono text-white/60">
                        {selectedEntity.entity.slice(0, 3)}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground">No connected entities</p>
                  </div>
                ) : (
                  <EgoGraph
                    center={selectedEntity}
                    neighbors={neighbors}
                    onSelectEntity={(ent) => setSelectedEntity(ent)}
                  />
                )}
              </div>
            </>
          ) : (
            <div className="flex h-full items-center justify-center">
              <p className="text-xs text-muted-foreground">Click an entity to explore its connections</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag/dashboard
npx tsc -b --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
git add dashboard/src/components/GraphTab.tsx
git commit -m "feat(dashboard): add GraphTab component with entity list and ego graph SVG"
```

---

## Task 7: Wire GraphTab into rag-builder

**Files:**
- Modify: `dashboard/src/pages/rag-builder.tsx`

The `IndexDetailView` component (lines ~517–630) has a 3-column grid layout. Add a tab bar for graph/hybrid indexes.

- [ ] **Step 1: Add import**

Find the imports at the top of `rag-builder.tsx`. After the last import, add:

```tsx
import { GraphTab } from "@/components/GraphTab";
```

- [ ] **Step 2: Add tab state to `IndexDetailView`**

In the `IndexDetailView` function body, after `const index = indexResp?.data;`, add:

```tsx
  const isGraphIndex =
    index?.index_type === "graph" || index?.index_type === "hybrid";
  const [activeTab, setActiveTab] = useState<"overview" | "graph">("overview");
```

Also add `useState` to the import if it's not already there (it should already be in scope).

- [ ] **Step 3: Add tab bar and conditional Graph tab in the render**

Find the `return` block of `IndexDetailView`. The content area currently starts with:

```tsx
      {/* Content: 3-column */}
      <div className="grid flex-1 grid-cols-[280px_1fr_320px] overflow-hidden">
```

Replace this entire content area with:

```tsx
      {/* Tab bar — only for graph/hybrid indexes */}
      {isGraphIndex && (
        <div className="flex border-b border-border px-6">
          {(["overview", "graph"] as const).map((tab) => (
            <button
              key={tab}
              className={`px-4 py-2 text-xs font-medium capitalize transition-colors ${
                activeTab === tab
                  ? "border-b-2 border-accent text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setActiveTab(tab)}
            >
              {tab === "graph" ? "Knowledge Graph" : "Overview"}
            </button>
          ))}
        </div>
      )}

      {/* Tab content */}
      {(!isGraphIndex || activeTab === "overview") && (
        <div className="grid flex-1 grid-cols-[280px_1fr_320px] overflow-hidden">
          {/* Left: Stats */}
          <div className="overflow-y-auto border-r border-border p-4">
```

And at the very end of the 3-column grid (just before the closing `</div>` of the grid), add a closing `</div>` for the conditional wrapper:

```tsx
        </div>
      )}

      {isGraphIndex && activeTab === "graph" && (
        <div className="flex-1 overflow-hidden">
          <GraphTab indexId={indexId} />
        </div>
      )}
```

> **Note:** Read the full `IndexDetailView` render carefully before editing — the 3-column grid has nested divs. Make sure the conditional wrapper closes at exactly the same level as the opening `<div className="grid flex-1 grid-cols-...">`.

- [ ] **Step 4: Type-check and lint**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag/dashboard
npx tsc -b --noEmit 2>&1 | head -20
npm run lint 2>&1 | tail -20
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
git add dashboard/src/pages/rag-builder.tsx
git commit -m "feat(dashboard): add Knowledge Graph tab to RAG builder for graph/hybrid indexes"
```

---

## Task 8: Playwright E2E tests

**Files:**
- Create: `dashboard/tests/e2e/rag-graph.spec.ts`

Pattern: uses the `authedPage` fixture from `fixtures.ts`, mocks API endpoints with `page.route()`.

- [ ] **Step 1: Create the test file**

Create `dashboard/tests/e2e/rag-graph.spec.ts`:

```typescript
import { test, expect } from "./fixtures";

const MOCK_GRAPH_INDEX = {
  id: "graph-idx-001",
  name: "agentbreeder-docs",
  description: "AgentBreeder technical documentation",
  embedding_model: "ollama/nomic-embed-text",
  entity_model: "ollama/qwen2.5:7b",
  chunk_strategy: "recursive",
  chunk_size: 512,
  chunk_overlap: 64,
  dimensions: 768,
  source: "manual",
  index_type: "graph",
  doc_count: 3,
  chunk_count: 24,
  created_at: "2026-04-20T12:00:00Z",
  updated_at: "2026-04-20T12:00:00Z",
};

const MOCK_ENTITIES = [
  { id: "ent-001", entity: "AgentBreeder", entity_type: "concept", description: "AI agent platform", chunk_ids: ["c1", "c2"] },
  { id: "ent-002", entity: "Deploy Pipeline", entity_type: "concept", description: "Ordered deploy steps", chunk_ids: ["c1"] },
  { id: "ent-003", entity: "RBAC", entity_type: "concept", description: "Role-based access control", chunk_ids: ["c2"] },
];

const MOCK_RELATIONSHIPS = [
  { id: "rel-001", subject_id: "ent-001", predicate: "uses", object_id: "ent-002", subject_entity: "AgentBreeder", object_entity: "Deploy Pipeline", chunk_ids: ["c1"], weight: 1.0 },
  { id: "rel-002", subject_id: "ent-001", predicate: "enforces", object_id: "ent-003", subject_entity: "AgentBreeder", object_entity: "RBAC", chunk_ids: ["c2"], weight: 1.0 },
];

const MOCK_GRAPH_META = {
  index_id: "graph-idx-001",
  index_type: "graph",
  node_count: 3,
  edge_count: 2,
  entity_types: [{ type: "concept", count: 3 }],
  top_entities: [
    { entity: "AgentBreeder", type: "concept", chunk_count: 2 },
    { entity: "Deploy Pipeline", type: "concept", chunk_count: 1 },
  ],
};

async function mockGraphAPIs(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/rag/indexes**", (route) => {
    const url = route.request().url();
    if (url.includes("/graph")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: MOCK_GRAPH_META, meta: { page: 1, per_page: 20, total: 1 }, errors: [] }),
      });
    } else if (url.includes("/entities")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: MOCK_ENTITIES, meta: { page: 1, per_page: 20, total: 3 }, errors: [] }),
      });
    } else if (url.includes("/relationships")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: MOCK_RELATIONSHIPS, meta: { page: 1, per_page: 200, total: 2 }, errors: [] }),
      });
    } else if (url.match(/\/rag\/indexes\/graph-idx-001$/)) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: MOCK_GRAPH_INDEX, meta: { page: 1, per_page: 20, total: 1 }, errors: [] }),
      });
    } else {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: [MOCK_GRAPH_INDEX], meta: { page: 1, per_page: 20, total: 1 }, errors: [] }),
      });
    }
  });
}

test.describe("GraphRAG UI", () => {
  test("shows Knowledge Graph tab for graph-type index", async ({ authedPage: page }) => {
    await mockGraphAPIs(page);
    await page.goto("/rag-builder");

    // Click the graph index in the list
    await page.getByText("agentbreeder-docs").click();

    // Knowledge Graph tab should be visible
    await expect(page.getByRole("button", { name: /knowledge graph/i })).toBeVisible();
  });

  test("entity list renders after clicking Knowledge Graph tab", async ({ authedPage: page }) => {
    await mockGraphAPIs(page);
    await page.goto("/rag-builder");
    await page.getByText("agentbreeder-docs").click();

    // Click Knowledge Graph tab
    await page.getByRole("button", { name: /knowledge graph/i }).click();

    // Stat badges visible
    await expect(page.getByText("3")).toBeVisible(); // node_count
    await expect(page.getByText("2")).toBeVisible(); // edge_count

    // Entity list renders
    await expect(page.getByText("AgentBreeder")).toBeVisible();
    await expect(page.getByText("Deploy Pipeline")).toBeVisible();
    await expect(page.getByText("RBAC")).toBeVisible();
  });

  test("entity type filter is visible when entity types exist", async ({ authedPage: page }) => {
    await mockGraphAPIs(page);
    await page.goto("/rag-builder");
    await page.getByText("agentbreeder-docs").click();
    await page.getByRole("button", { name: /knowledge graph/i }).click();

    await expect(page.getByRole("combobox")).toBeVisible();
    await expect(page.getByRole("option", { name: "concept" })).toBeAttached();
  });

  test("clicking entity shows ego graph panel", async ({ authedPage: page }) => {
    await mockGraphAPIs(page);
    await page.goto("/rag-builder");
    await page.getByText("agentbreeder-docs").click();
    await page.getByRole("button", { name: /knowledge graph/i }).click();

    // Click AgentBreeder entity
    await page.getByText("AgentBreeder").first().click();

    // Ego graph SVG should appear
    await expect(page.locator("svg")).toBeVisible();
    // Relationship predicate should appear in the SVG
    await expect(page.locator("text").filter({ hasText: /uses|enforces/ }).first()).toBeVisible();
  });

  test("overview tab still works for graph index", async ({ authedPage: page }) => {
    await mockGraphAPIs(page);
    await page.goto("/rag-builder");
    await page.getByText("agentbreeder-docs").click();

    // Should default to Overview tab showing existing stats
    await expect(page.getByText("Documents")).toBeVisible();
  });
});
```

- [ ] **Step 2: Run the tests**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag/dashboard
npx playwright test tests/e2e/rag-graph.spec.ts --config=playwright.config.ts 2>&1 | tail -30
```

Expected: all 5 tests pass.

If any fail, read the error output and fix the selector or mock response shape.

- [ ] **Step 3: Commit**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
git add dashboard/tests/e2e/rag-graph.spec.ts
git commit -m "test(e2e): add Playwright tests for GraphRAG Knowledge Graph UI"
```

---

## Task 9: Documentation updates

**Files:**
- Modify: `README.md`
- Modify: `website/content/docs/graphrag.mdx`

- [ ] **Step 1: Add GraphRAG section to README.md**

Find the section in `README.md` that covers RAG (search for `## RAG` or `knowledge base`). Add a new `## GraphRAG` section after it:

```markdown
## GraphRAG — Graph-Indexed Knowledge Bases

GraphRAG extends vector RAG by building a knowledge graph during ingestion. Entities and
relationships are extracted from each chunk, then combined into a graph that enables
multi-hop traversal at query time.

**When to use graph vs vector:**
- **Vector** — fast retrieval for exact-match or similarity queries
- **Graph** — better for multi-hop reasoning ("what teams use agents that depend on X?")
- **Hybrid** — best of both: vector similarity + graph traversal

**Quick start with local Ollama:**

```bash
# Pull models
ollama pull qwen2.5:7b
ollama pull nomic-embed-text

# Start stack
docker compose up -d

# Ingest sample knowledge base
python examples/graphrag-ollama-agent/ingest.py
```

Create a graph index in agent.yaml:

```yaml
knowledge_bases:
  - ref: kb/agentbreeder-docs

# In your RAG index config:
# index_type: graph
# embedding_model: ollama/nomic-embed-text
# entity_model: ollama/qwen2.5:7b
```

See [GraphRAG documentation](website/content/docs/graphrag.mdx) and the
[sample agent](examples/graphrag-ollama-agent/) for a full walkthrough.
```

- [ ] **Step 2: Update graphrag.mdx with Ollama section**

Find `website/content/docs/graphrag.mdx`. After the existing content, add or update to include a section on Ollama:

```mdx
## Local Extraction with Ollama

No API keys required — use a local Ollama model for entity extraction.

### Prerequisites

```bash
ollama pull qwen2.5:7b          # entity extraction
ollama pull nomic-embed-text    # embeddings (768-dim)
```

### Configure your index

When creating a GraphRAG index, set both models to Ollama:

```yaml
# Via API or dashboard
embedding_model: ollama/nomic-embed-text
entity_model: ollama/qwen2.5:7b
index_type: graph
```

The `entity_model` field accepts any model with the `ollama/` prefix. The extraction
call uses Ollama's `/api/chat` endpoint with `format: "json"` for structured output.

### Custom Ollama endpoint

If your Ollama instance isn't running on `localhost:11434`, set the env var:

```bash
OLLAMA_BASE_URL=http://my-ollama-server:11434 docker compose up -d
```

### Sample agent

See `examples/graphrag-ollama-agent/` for a complete working example with:
- Three knowledge base documents (AgentBreeder architecture, agent.yaml spec, CLI reference)
- An `ingest.py` script that populates the graph index
- A sample `agent.yaml` that queries the graph at runtime

Run it:

```bash
python examples/graphrag-ollama-agent/ingest.py
```
```

- [ ] **Step 3: Commit**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
git add README.md website/content/docs/graphrag.mdx
git commit -m "docs: add GraphRAG + Ollama section to README and graphrag.mdx"
```

---

## Task 10: Lint, type-check, merge to main

- [ ] **Step 1: Run full lint + type-check**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag

# Python
python -m ruff check . --fix
python -m ruff format .

# Dashboard
cd dashboard && npx tsc -b --noEmit && npm run lint
cd ..

# Website
cd website && npx tsc -b --noEmit
cd ..
```

Fix any errors before proceeding.

- [ ] **Step 2: Run all Python unit tests**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
python -m pytest tests/unit/ -v 2>&1 | tail -30
```

Expected: all pass.

- [ ] **Step 3: Run Playwright tests**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag/dashboard
npx playwright test tests/e2e/rag-graph.spec.ts --config=playwright.config.ts 2>&1 | tail -20
```

Expected: 5/5 pass.

- [ ] **Step 4: Commit any lint fixes**

```bash
cd /Users/rajit/personal-github/agentbreeder/.worktrees/feat-graphrag
git add -u
git diff --staged --stat
# If there are lint/format changes:
git commit -m "chore: lint and format fixes before merge"
```

- [ ] **Step 5: Merge feat-graphrag to main**

```bash
cd /Users/rajit/personal-github/agentbreeder
git merge .worktrees/feat-graphrag --no-ff -m "feat(graphrag): Ollama entity extraction, sample agent, dashboard Graph UI, homepage animation

- Add _call_ollama() for local entity extraction via ollama/qwen2.5:7b
- Add examples/graphrag-ollama-agent/ with ingest script and knowledge base
- Animate hero with SVG knowledge graph (pure CSS, zero deps)
- Add Knowledge Graph tab to dashboard RAG builder (split panel: entity list + ego graph)
- Add Playwright E2E tests for GraphRAG UI
- Update README and graphrag.mdx with Ollama docs

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

- [ ] **Step 6: Push**

```bash
git push origin main
```
