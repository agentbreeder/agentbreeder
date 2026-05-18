"""G10 — Real-Neo4j integration test via testcontainers.

This test boots a real Neo4j container, indexes a small corpus through
``Neo4jRAGBackend``, runs a vector + graph search, and asserts that results
come back. It exists to catch regressions that mocked unit tests miss
(schema setup, native vector index usage, JSON metadata round-tripping,
graph traversal Cypher).

The test is **skipped** whenever any of the following is true:
    * ``testcontainers`` is not installed
    * the ``neo4j`` Python driver is not installed
    * Docker is not reachable from the test environment
    * the user opted out via ``AGENTBREEDER_SKIP_TESTCONTAINERS=1``

Run locally with::

    pip install testcontainers[neo4j] neo4j
    pytest tests/integration/test_neo4j_testcontainers.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest

# Skip the entire module if optional deps are missing — keeps CI green when
# testcontainers isn't on the install list.
pytest.importorskip("testcontainers.neo4j")
pytest.importorskip("neo4j")

if os.environ.get("AGENTBREEDER_SKIP_TESTCONTAINERS") == "1":
    pytest.skip(
        "AGENTBREEDER_SKIP_TESTCONTAINERS=1 — skipping real Neo4j integration test",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def neo4j_container():
    """Spin up a Neo4j container for the lifetime of the module."""
    from testcontainers.neo4j import Neo4jContainer

    try:
        container = Neo4jContainer("neo4j:5.20-community")
        container.start()
    except Exception as exc:  # noqa: BLE001 — Docker not available, etc.
        pytest.skip(f"Could not start Neo4j testcontainer (Docker missing?): {exc}")

    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
def neo4j_backend(neo4j_container):
    """Build a Neo4jRAGBackend pointed at the real container."""
    from api.services.neo4j_rag_backend import Neo4jConfig, Neo4jRAGBackend

    cfg = Neo4jConfig(
        uri=neo4j_container.get_connection_url(),
        user="neo4j",
        password=neo4j_container.NEO4J_ADMIN_PASSWORD
        if hasattr(neo4j_container, "NEO4J_ADMIN_PASSWORD")
        else "password",
        database="neo4j",
    )
    return Neo4jRAGBackend(index_id=f"itest-{uuid.uuid4().hex[:8]}", config=cfg)


@pytest.mark.asyncio
async def test_real_neo4j_index_then_search(neo4j_backend):
    """Index a tiny corpus, then run a vector search — asserts results return."""
    documents = [
        {
            "chunk_id": "c1",
            "text": "Apple is a technology company headquartered in Cupertino.",
            "source": "wiki/apple",
            "embedding": [0.10, 0.20, 0.30, 0.40],
            "metadata": {"topic": "tech"},
            "entities": [
                {"name": "Apple", "type": "organization", "description": "tech company"},
                {"name": "Cupertino", "type": "location", "description": "city"},
            ],
            "relationships": [
                {
                    "subject": "Apple",
                    "predicate": "headquartered_in",
                    "object": "Cupertino",
                }
            ],
        },
        {
            "chunk_id": "c2",
            "text": "Steve Jobs co-founded Apple in 1976.",
            "source": "wiki/jobs",
            "embedding": [0.11, 0.21, 0.29, 0.41],
            "metadata": {"topic": "people"},
            "entities": [
                {"name": "Steve Jobs", "type": "person", "description": "co-founder"},
                {"name": "Apple", "type": "organization", "description": "tech company"},
            ],
            "relationships": [
                {"subject": "Steve Jobs", "predicate": "co_founded", "object": "Apple"}
            ],
        },
    ]

    indexed = await neo4j_backend.index(documents)
    assert indexed == 2

    # Vector-only search
    hits = await neo4j_backend.search(
        query="who founded Apple",
        query_embedding=[0.11, 0.21, 0.29, 0.41],
        top_k=2,
    )
    assert len(hits) >= 1
    assert all("chunk_id" in h and "score" in h for h in hits)

    # Graph-augmented search (seed by entity name)
    hits_graph = await neo4j_backend.search(
        query="apple founders",
        query_embedding=[0.10, 0.20, 0.30, 0.40],
        top_k=5,
        seed_entities=["Apple"],
        max_hops=2,
    )
    assert len(hits_graph) >= 1
    returned_ids = {h["chunk_id"] for h in hits_graph}
    # At least one of the seeded entity's chunks should surface.
    assert returned_ids & {"c1", "c2"}

    await neo4j_backend.close()
