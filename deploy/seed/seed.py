#!/usr/bin/env python3
"""AgentBreeder seed script — standalone, runnable independently of quickstart.

Seeds ChromaDB (vector store) and Neo4j (knowledge graph) with sample data
so the rag-agent and graph-agent work immediately.

Usage:
    python deploy/seed/seed.py                    # seed everything
    python deploy/seed/seed.py --chromadb-only    # only vector store
    python deploy/seed/seed.py --neo4j-only       # only graph DB
    python deploy/seed/seed.py --docs ./my-docs/  # ingest custom documents
    python deploy/seed/seed.py --list             # show what's seeded
    python deploy/seed/seed.py --clear            # drop and re-seed

Or via the CLI (preferred):
    agentbreeder seed
    agentbreeder seed --chromadb --docs ./my-docs/
    agentbreeder seed --neo4j --cypher ./my-graph.cypher
    agentbreeder seed --list
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

CHROMADB_BASE = "http://localhost:8001"
CHROMA_TENANT = "default_tenant"
CHROMA_DATABASE = "default_database"
# v2 base path — all collection operations live here
CHROMA_V2 = f"{CHROMADB_BASE}/api/v2/tenants/{CHROMA_TENANT}/databases/{CHROMA_DATABASE}"
NEO4J_HTTP = "http://localhost:7474"
NEO4J_USER = "neo4j"
NEO4J_PASS = "agentbreeder"
COLLECTION = "agentbreeder_knowledge"
DOCS_DIR = Path(__file__).parent / "docs"

NEO4J_SEED_CYPHER = """
MATCH (n:QuickstartNode) DETACH DELETE n;

CREATE (:Framework:QuickstartNode {name:'langgraph', label:'LangGraph', tool_calling:true, streaming:true});
CREATE (:Framework:QuickstartNode {name:'crewai', label:'CrewAI', tool_calling:true, streaming:false});
CREATE (:Framework:QuickstartNode {name:'claude_sdk', label:'Claude SDK', tool_calling:true, streaming:true});
CREATE (:Framework:QuickstartNode {name:'openai_agents', label:'OpenAI Agents', tool_calling:true, streaming:true});
CREATE (:Framework:QuickstartNode {name:'google_adk', label:'Google ADK', tool_calling:true, streaming:true});
CREATE (:Framework:QuickstartNode {name:'custom', label:'Custom', tool_calling:false, streaming:false});

CREATE (:Provider:QuickstartNode {name:'anthropic', label:'Anthropic', local:false, free_tier:false, models:['claude-sonnet-4','claude-haiku-4']});
CREATE (:Provider:QuickstartNode {name:'openai', label:'OpenAI', local:false, free_tier:false, models:['gpt-4o','gpt-4o-mini']});
CREATE (:Provider:QuickstartNode {name:'google', label:'Google AI', local:false, free_tier:true, models:['gemini-2.0-flash','gemini-1.5-pro']});
CREATE (:Provider:QuickstartNode {name:'ollama', label:'Ollama', local:true, free_tier:true, models:['llama3.2','mistral','gemma3']});
CREATE (:Provider:QuickstartNode {name:'openrouter', label:'OpenRouter', local:false, free_tier:false, models:['openai/gpt-4o','anthropic/claude-sonnet-4']});

CREATE (:Tool:QuickstartNode {name:'rag_search', label:'RAG Search', type:'function', backend:'chromadb', description:'Semantic vector search over knowledge base'});
CREATE (:Tool:QuickstartNode {name:'graph_query', label:'Graph Query', type:'function', backend:'neo4j', description:'Query knowledge graph via Cypher'});
CREATE (:Tool:QuickstartNode {name:'web_search', label:'Web Search', type:'function', backend:'http', description:'Search the web for current information'});
CREATE (:Tool:QuickstartNode {name:'mcp_filesystem', label:'MCP Filesystem', type:'mcp', backend:'filesystem', description:'Read and write files via MCP'});
CREATE (:Tool:QuickstartNode {name:'mcp_memory', label:'MCP Memory', type:'mcp', backend:'memory', description:'Persistent key-value memory across sessions'});
CREATE (:Tool:QuickstartNode {name:'call_rag_agent', label:'Call RAG Agent', type:'a2a', backend:'a2a', description:'Delegate knowledge-base questions to rag-agent'});
CREATE (:Tool:QuickstartNode {name:'call_graph_agent', label:'Call Graph Agent', type:'a2a', backend:'a2a', description:'Delegate graph questions to graph-agent'});
CREATE (:Tool:QuickstartNode {name:'call_search_agent', label:'Call Search Agent', type:'a2a', backend:'a2a', description:'Delegate search/file tasks to search-agent'});

CREATE (:Agent:QuickstartNode {name:'rag-agent', label:'RAG Agent', team:'quickstart', status:'running', endpoint:'http://localhost:8080'});
CREATE (:Agent:QuickstartNode {name:'graph-agent', label:'Graph Agent', team:'quickstart', status:'running', endpoint:'http://localhost:8081'});
CREATE (:Agent:QuickstartNode {name:'search-agent', label:'Search Agent', team:'quickstart', status:'running', endpoint:'http://localhost:8082'});
CREATE (:Agent:QuickstartNode {name:'a2a-orchestrator', label:'A2A Orchestrator', team:'quickstart', status:'running', endpoint:'http://localhost:8083'});
CREATE (:Agent:QuickstartNode {name:'assistant', label:'Assistant', team:'quickstart', status:'running', endpoint:'http://localhost:8084'});

CREATE (:DeployTarget:QuickstartNode {name:'local', label:'Local Docker', provider:'docker'});
CREATE (:DeployTarget:QuickstartNode {name:'aws', label:'AWS ECS Fargate', provider:'aws'});
CREATE (:DeployTarget:QuickstartNode {name:'gcp', label:'GCP Cloud Run', provider:'gcp'});
CREATE (:DeployTarget:QuickstartNode {name:'azure', label:'Azure Container Apps', provider:'azure'});
CREATE (:DeployTarget:QuickstartNode {name:'kubernetes', label:'Kubernetes', provider:'k8s'});

MATCH (a:Agent {name:'rag-agent'}), (f:Framework {name:'langgraph'}) CREATE (a)-[:RUNS_ON]->(f);
MATCH (a:Agent {name:'graph-agent'}), (f:Framework {name:'langgraph'}) CREATE (a)-[:RUNS_ON]->(f);
MATCH (a:Agent {name:'search-agent'}), (f:Framework {name:'langgraph'}) CREATE (a)-[:RUNS_ON]->(f);
MATCH (a:Agent {name:'a2a-orchestrator'}), (f:Framework {name:'langgraph'}) CREATE (a)-[:RUNS_ON]->(f);
MATCH (a:Agent {name:'assistant'}), (f:Framework {name:'langgraph'}) CREATE (a)-[:RUNS_ON]->(f);

MATCH (a:Agent {name:'rag-agent'}), (t:Tool {name:'rag_search'}) CREATE (a)-[:USES_TOOL]->(t);
MATCH (a:Agent {name:'graph-agent'}), (t:Tool {name:'graph_query'}) CREATE (a)-[:USES_TOOL]->(t);
MATCH (a:Agent {name:'search-agent'}), (t:Tool {name:'web_search'}) CREATE (a)-[:USES_TOOL]->(t);
MATCH (a:Agent {name:'search-agent'}), (t:Tool {name:'mcp_filesystem'}) CREATE (a)-[:USES_TOOL]->(t);
MATCH (a:Agent {name:'search-agent'}), (t:Tool {name:'mcp_memory'}) CREATE (a)-[:USES_TOOL]->(t);
MATCH (a:Agent {name:'a2a-orchestrator'}), (t:Tool {name:'call_rag_agent'}) CREATE (a)-[:USES_TOOL]->(t);
MATCH (a:Agent {name:'a2a-orchestrator'}), (t:Tool {name:'call_graph_agent'}) CREATE (a)-[:USES_TOOL]->(t);
MATCH (a:Agent {name:'a2a-orchestrator'}), (t:Tool {name:'call_search_agent'}) CREATE (a)-[:USES_TOOL]->(t);

MATCH (a:Agent), (p:Provider {name:'ollama'}) CREATE (a)-[:CALLS_PROVIDER {role:'primary'}]->(p);
MATCH (a:Agent {name:'rag-agent'}), (p:Provider {name:'anthropic'}) CREATE (a)-[:CALLS_PROVIDER {role:'fallback'}]->(p);
MATCH (a:Agent {name:'graph-agent'}), (p:Provider {name:'anthropic'}) CREATE (a)-[:CALLS_PROVIDER {role:'fallback'}]->(p);
MATCH (a:Agent {name:'assistant'}), (p:Provider {name:'openai'}) CREATE (a)-[:CALLS_PROVIDER {role:'fallback'}]->(p);

MATCH (o:Agent {name:'a2a-orchestrator'}), (r:Agent {name:'rag-agent'}) CREATE (o)-[:CALLS_AGENT {via:'a2a'}]->(r);
MATCH (o:Agent {name:'a2a-orchestrator'}), (g:Agent {name:'graph-agent'}) CREATE (o)-[:CALLS_AGENT {via:'a2a'}]->(g);
MATCH (o:Agent {name:'a2a-orchestrator'}), (s:Agent {name:'search-agent'}) CREATE (o)-[:CALLS_AGENT {via:'a2a'}]->(s);

MATCH (a:Agent), (d:DeployTarget {name:'local'}) CREATE (a)-[:DEPLOYED_ON]->(d)
"""


# ── ChromaDB helpers ────────────────────────────────────────────────────────


def _chroma_client():
    """Return an HttpClient connected to the local ChromaDB instance."""
    import chromadb

    return chromadb.HttpClient(host="localhost", port=8001)


# Sentinel string used by callers to detect "package not installed" so they can
# print a precise install hint instead of the generic "not reachable" message.
CHROMADB_NOT_INSTALLED = "chromadb-package-not-installed"


def _chroma_status() -> tuple[bool, str | None]:
    """Return (ok, error_code).

    error_code is one of:
      None                       — chromadb client is installed AND server is reachable
      CHROMADB_NOT_INSTALLED     — chromadb python package is not installed
      "unreachable: <details>"   — package installed, but server cannot be reached
    """
    try:
        import chromadb  # noqa: F401
    except ImportError:
        return False, CHROMADB_NOT_INSTALLED
    try:
        _chroma_client().heartbeat()
        return True, None
    except Exception as exc:
        return False, f"unreachable: {exc}"


def _chroma_is_up() -> bool:
    ok, _ = _chroma_status()
    return ok


def _load_docs_from_dir(docs_dir: Path) -> list[dict]:
    """Read all .md and .txt files from a directory as seed documents."""
    docs = []
    for path in sorted(docs_dir.glob("**/*.md")) + sorted(docs_dir.glob("**/*.txt")):
        text = path.read_text(encoding="utf-8")
        # Split into ~500-char chunks (simple sentence-boundary split)
        chunks = _chunk_text(text, max_chars=600)
        for i, chunk in enumerate(chunks):
            doc_id = f"{path.stem}-{i}"
            docs.append(
                {
                    "id": doc_id,
                    "text": chunk,
                    "metadata": {
                        "source": path.name,
                        "topic": path.stem,
                        "chunk": i,
                        "path": str(path.relative_to(docs_dir)),
                    },
                }
            )
    return docs


def _chunk_text(text: str, max_chars: int = 600) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    import re

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks if chunks else [text[:max_chars]]


def _resolve_embedding_function(embedding_model: str):
    """Return a ChromaDB embedding function for the given model spec.

    Supported values:
      "default"                         – ChromaDB built-in (all-MiniLM-L6-v2)
      "openai:text-embedding-3-small"   – OpenAI via OPENAI_API_KEY
      "openai:<any-openai-model>"       – any OpenAI embedding model
      "ollama:<model>"                  – Ollama local embeddings
    """
    import chromadb.utils.embedding_functions as ef

    if embedding_model == "default":
        return None  # let ChromaDB use its default

    if embedding_model.startswith("openai:"):
        model_name = embedding_model[len("openai:") :]
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print(
                "  ⚠ OPENAI_API_KEY not set — falling back to default embedding model",
                file=sys.stderr,
            )
            return None
        return ef.OpenAIEmbeddingFunction(api_key=api_key, model_name=model_name)

    if embedding_model.startswith("ollama:"):
        model_name = embedding_model[len("ollama:") :]
        return ef.OllamaEmbeddingFunction(
            url="http://localhost:11434/api/embeddings",
            model_name=model_name,
        )

    # Unknown spec — warn and fall back
    print(
        f"  ⚠ Unknown embedding model '{embedding_model}' — falling back to default",
        file=sys.stderr,
    )
    return None


def seed_chromadb(
    docs_dir: Path | None = None,
    collection: str = COLLECTION,
    clear: bool = False,
    embedding_model: str = "default",
) -> dict:
    """Seed ChromaDB with documents from docs_dir. Returns a status dict."""
    ok, err = _chroma_status()
    if not ok:
        if err == CHROMADB_NOT_INSTALLED:
            return {
                "ok": False,
                "error": (
                    "chromadb python client not installed. "
                    "Install/upgrade with: pip install --upgrade agentbreeder"
                ),
                "code": CHROMADB_NOT_INSTALLED,
            }
        return {"ok": False, "error": f"ChromaDB not reachable at {CHROMADB_BASE} ({err})"}

    source_dir = docs_dir or DOCS_DIR

    try:
        client = _chroma_client()

        if clear:
            try:
                client.delete_collection(collection)
            except Exception:
                pass

        emb_fn = _resolve_embedding_function(embedding_model)
        if emb_fn is not None:
            col = client.get_or_create_collection(collection, embedding_function=emb_fn)
        else:
            col = client.get_or_create_collection(collection)

        docs = _load_docs_from_dir(source_dir)
        if not docs:
            return {"ok": False, "error": f"No .md/.txt files found in {source_dir}"}

        ids = [d["id"] for d in docs]
        documents = [d["text"] for d in docs]
        metadatas = [d["metadata"] for d in docs]

        col.upsert(ids=ids, documents=documents, metadatas=metadatas)

        return {
            "ok": True,
            "collection": collection,
            "collection_id": col.id,
            "documents_seeded": len(docs),
            "source": str(source_dir),
            "embedding_model": embedding_model,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def list_chromadb() -> dict:
    """Return info about seeded collections."""
    ok, err = _chroma_status()
    if not ok:
        if err == CHROMADB_NOT_INSTALLED:
            return {
                "ok": False,
                "error": (
                    "chromadb python client not installed. "
                    "Install/upgrade with: pip install --upgrade agentbreeder"
                ),
                "code": CHROMADB_NOT_INSTALLED,
            }
        return {"ok": False, "error": f"ChromaDB not reachable at {CHROMADB_BASE} ({err})"}
    try:
        client = _chroma_client()
        collections = client.list_collections()
        result = [
            {"name": col.name, "id": str(col.id), "count": col.count()} for col in collections
        ]
        return {"ok": True, "collections": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Neo4j helpers ───────────────────────────────────────────────────────────


def _neo4j_is_up() -> bool:
    try:
        resp = httpx.get(NEO4J_HTTP, timeout=5.0, auth=(NEO4J_USER, NEO4J_PASS))
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def _run_cypher(cypher_block: str) -> dict:
    """Execute one or more semicolon-separated Cypher statements."""
    url = f"{NEO4J_HTTP}/db/neo4j/tx/commit"
    statements = [{"statement": stmt.strip()} for stmt in cypher_block.split(";") if stmt.strip()]
    try:
        resp = httpx.post(
            url,
            json={"statements": statements},
            auth=(NEO4J_USER, NEO4J_PASS),
            timeout=30.0,
        )
        data = resp.json()
        errors = data.get("errors", [])
        return {
            "ok": resp.status_code == 200 and not errors,
            "errors": errors,
            "statements_run": len(statements),
        }
    except Exception as exc:
        return {"ok": False, "errors": [str(exc)], "statements_run": 0}


def _apoc_is_available() -> bool:
    """Return True if APOC is installed in Neo4j (probe with apoc.version())."""
    url = f"{NEO4J_HTTP}/db/neo4j/tx/commit"
    try:
        resp = httpx.post(
            url,
            json={"statements": [{"statement": "RETURN apoc.version() AS version"}]},
            auth=(NEO4J_USER, NEO4J_PASS),
            timeout=5.0,
        )
        data = resp.json()
        return resp.status_code == 200 and not data.get("errors")
    except Exception:
        return False


def _strip_apoc_statements(cypher_block: str) -> tuple[str, int]:
    """Remove CALL apoc.* statements from a Cypher block.

    Returns (filtered_block, stripped_count).
    """
    import re

    statements = [s.strip() for s in cypher_block.split(";") if s.strip()]
    kept = []
    skipped = 0
    for stmt in statements:
        if re.search(r"\bCALL\s+apoc\.", stmt, re.IGNORECASE):
            skipped += 1
        else:
            kept.append(stmt)
    return ";\n".join(kept), skipped


def seed_neo4j(cypher_file: Path | None = None, clear: bool = False) -> dict:
    """Seed Neo4j with the knowledge graph. Returns a status dict."""
    if not _neo4j_is_up():
        return {"ok": False, "error": f"Neo4j not reachable at {NEO4J_HTTP}"}

    if cypher_file:
        cypher = cypher_file.read_text(encoding="utf-8")
    else:
        cypher = NEO4J_SEED_CYPHER

    apoc_available = _apoc_is_available()
    apoc_skipped = 0
    if not apoc_available:
        cypher, apoc_skipped = _strip_apoc_statements(cypher)
        if apoc_skipped:
            print(
                "  ⚠ APOC not available (common on arm64) — skipping"
                f" {apoc_skipped} APOC-dependent seed step(s)",
                file=sys.stderr,
            )

    if clear:
        _run_cypher("MATCH (n:QuickstartNode) DETACH DELETE n")

    result = _run_cypher(cypher)
    return {
        **result,
        "source": str(cypher_file) if cypher_file else "built-in",
        "apoc_available": apoc_available,
        "apoc_skipped": apoc_skipped,
    }


def list_neo4j() -> dict:
    """Return a summary of what's in the graph."""
    if not _neo4j_is_up():
        return {"ok": False, "error": f"Neo4j not reachable at {NEO4J_HTTP}"}

    queries = {
        "agents": "MATCH (a:Agent) RETURN count(a) AS n",
        "tools": "MATCH (t:Tool) RETURN count(t) AS n",
        "frameworks": "MATCH (f:Framework) RETURN count(f) AS n",
        "providers": "MATCH (p:Provider) RETURN count(p) AS n",
        "relationships": "MATCH ()-[r]->() RETURN count(r) AS n",
    }

    url = f"{NEO4J_HTTP}/db/neo4j/tx/commit"
    statements = [{"statement": q} for q in queries.values()]
    try:
        resp = httpx.post(
            url,
            json={"statements": statements},
            auth=(NEO4J_USER, NEO4J_PASS),
            timeout=10.0,
        )
        data = resp.json()
        results = data.get("results", [])
        counts = {}
        for key, result in zip(queries.keys(), results):
            rows = result.get("data", [])
            counts[key] = rows[0]["row"][0] if rows else 0
        return {"ok": True, "counts": counts}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── CLI entrypoint ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed ChromaDB and Neo4j with AgentBreeder sample data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--chromadb-only", action="store_true", help="Only seed ChromaDB")
    parser.add_argument("--neo4j-only", action="store_true", help="Only seed Neo4j")
    parser.add_argument(
        "--docs", type=Path, help="Directory of .md/.txt files to ingest into ChromaDB"
    )
    parser.add_argument(
        "--collection",
        default=COLLECTION,
        help=f"ChromaDB collection name (default: {COLLECTION})",
    )
    parser.add_argument(
        "--embedding-model",
        default="default",
        help=(
            "Embedding model for ChromaDB. "
            "Options: default | openai:text-embedding-3-small | ollama:<model>"
        ),
    )
    parser.add_argument("--cypher", type=Path, help="Custom .cypher file for Neo4j")
    parser.add_argument("--clear", action="store_true", help="Drop existing data before seeding")
    parser.add_argument("--list", action="store_true", help="Show what's currently seeded")
    args = parser.parse_args()

    do_chroma = not args.neo4j_only
    do_neo4j = not args.chromadb_only

    if args.list:
        print("\n── ChromaDB ─────────────────────────────")
        result = list_chromadb()
        if result["ok"]:
            for col in result.get("collections", []):
                print(f"  Collection: {col['name']}  ({col['count']} documents)")
        else:
            print(f"  {result.get('error')}")

        print("\n── Neo4j ────────────────────────────────")
        result = list_neo4j()
        if result["ok"]:
            for label, count in result["counts"].items():
                print(f"  {label:15}: {count}")
        else:
            print(f"  {result.get('error')}")
        print()
        return

    if do_chroma:
        print("\n── ChromaDB ─────────────────────────────")
        print(f"  Target:     {CHROMADB_BASE}")
        print(f"  Collection: {args.collection}")
        print(f"  Source:     {args.docs or DOCS_DIR}")
        if args.clear:
            print("  Mode:       clear + re-seed")
        result = seed_chromadb(
            docs_dir=args.docs,
            collection=args.collection,
            clear=args.clear,
            embedding_model=args.embedding_model,
        )
        if result["ok"]:
            print(f"  ✓ Seeded {result['documents_seeded']} document chunks")
            print(f"  ✓ Collection ID: {result['collection_id']}")
        else:
            print(f"  ✗ Failed: {result.get('error')}")
            if not do_neo4j:
                sys.exit(1)

    if do_neo4j:
        print("\n── Neo4j ────────────────────────────────")
        print(f"  Target: {NEO4J_HTTP}")
        print(f"  Auth:   {NEO4J_USER} / (configured)")
        print(f"  Source: {args.cypher or 'built-in quickstart graph'}")
        if args.clear:
            print("  Mode:   clear + re-seed")
        result = seed_neo4j(cypher_file=args.cypher, clear=args.clear)
        if result["ok"]:
            print(f"  ✓ Ran {result['statements_run']} Cypher statements")
            # Show what was created
            summary = list_neo4j()
            if summary["ok"]:
                for label, count in summary["counts"].items():
                    print(f"  ✓ {label:15}: {count}")
        else:
            print("  ✗ Failed")
            for err in result.get("errors", []):
                print(f"    {err}")
            sys.exit(1)

    print()


if __name__ == "__main__":
    main()
