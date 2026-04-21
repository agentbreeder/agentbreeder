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
