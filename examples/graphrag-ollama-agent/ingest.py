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
    resp = client.get(f"{api_url}/api/v1/rag/indexes")
    resp.raise_for_status()
    indexes = resp.json().get("data", [])
    for idx in indexes:
        if idx.get("name") == INDEX_NAME:
            print(f"Using existing index: {idx['id']}")
            return idx["id"]

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
