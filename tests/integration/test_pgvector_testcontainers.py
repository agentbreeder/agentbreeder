"""HR-4 / #406 — Real Postgres+pgvector integration test via testcontainers.

Boots a real ``pgvector/pgvector:pg16`` container, runs the full upsert →
search → delete lifecycle against a real database, and asserts that vector
similarity actually orders results.

Skip conditions (any one short-circuits the module):
    * ``testcontainers`` is not installed
    * ``asyncpg`` is not installed
    * Docker is not reachable
    * the user opted out via ``AGENTBREEDER_SKIP_TESTCONTAINERS=1``

Run locally::

    pip install 'testcontainers[postgres]' asyncpg
    pytest tests/integration/test_pgvector_testcontainers.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("testcontainers.postgres")
pytest.importorskip("asyncpg")

if os.environ.get("AGENTBREEDER_SKIP_TESTCONTAINERS") == "1":
    pytest.skip(
        "AGENTBREEDER_SKIP_TESTCONTAINERS=1 — skipping real pgvector integration test",
        allow_module_level=True,
    )


PGVECTOR_IMAGE = "pgvector/pgvector:pg16"


@pytest.fixture(scope="module")
def pg_container():
    """Spin up a Postgres+pgvector container for the lifetime of the module."""
    from testcontainers.postgres import PostgresContainer

    try:
        container = PostgresContainer(PGVECTOR_IMAGE)
        container.start()
    except Exception as exc:  # noqa: BLE001 - Docker missing, etc.
        pytest.skip(f"Could not start pgvector testcontainer (Docker missing?): {exc}")

    try:
        yield container
    finally:
        container.stop()


def _to_asyncpg_dsn(jdbc_or_psql: str) -> str:
    """testcontainers returns a SQLAlchemy URL — asyncpg wants a plain postgres:// DSN."""
    return jdbc_or_psql.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


@pytest.fixture
async def backend(pg_container):
    """Return a connected PgvectorRAGBackend pointed at the live container."""
    from api.services.pgvector_rag_backend import PgvectorConfig, PgvectorRAGBackend

    dsn = _to_asyncpg_dsn(pg_container.get_connection_url())
    b = PgvectorRAGBackend(PgvectorConfig(dsn=dsn), index_id=f"test-{uuid.uuid4()}")
    await b.connect()
    try:
        yield b
    finally:
        await b.delete_index()
        await b.close()


@pytest.mark.asyncio
async def test_upsert_then_search_round_trip(backend) -> None:
    chunks = [
        {
            "id": uuid.uuid4(),
            "text": "The Eiffel Tower stands in Paris.",
            "embedding": [1.0, 0.0, 0.0, 0.0],
            "metadata": {"city": "Paris"},
        },
        {
            "id": uuid.uuid4(),
            "text": "Sushi is a Japanese dish.",
            "embedding": [0.0, 1.0, 0.0, 0.0],
            "metadata": {"city": "Tokyo"},
        },
        {
            "id": uuid.uuid4(),
            "text": "The Louvre is also in Paris.",
            "embedding": [0.95, 0.05, 0.0, 0.0],
            "metadata": {"city": "Paris"},
        },
    ]
    written = await backend.upsert_chunks(chunks)
    assert written == 3
    assert await backend.count() == 3

    # Query close to the first vector — both Paris rows should rank above Tokyo.
    hits = await backend.search([1.0, 0.0, 0.0, 0.0], top_k=3)
    assert len(hits) == 3
    assert hits[0]["text"] == "The Eiffel Tower stands in Paris."
    assert "Paris" in hits[1]["text"]
    assert hits[2]["text"] == "Sushi is a Japanese dish."
    # Cosine similarity score is normalised to roughly [0, 1].
    assert hits[0]["score"] > hits[2]["score"]
    # metadata round-trips.
    assert hits[0]["metadata"]["city"] == "Paris"


@pytest.mark.asyncio
async def test_search_empty_index_returns_empty(backend) -> None:
    hits = await backend.search([1.0, 0.0, 0.0, 0.0], top_k=5)
    assert hits == []


@pytest.mark.asyncio
async def test_upsert_is_idempotent(backend) -> None:
    cid = uuid.uuid4()
    chunk = {
        "id": cid,
        "text": "first",
        "embedding": [1.0, 0.0, 0.0, 0.0],
        "metadata": {},
    }
    await backend.upsert_chunks([chunk])
    chunk["text"] = "second"
    await backend.upsert_chunks([chunk])
    assert await backend.count() == 1
    hits = await backend.search([1.0, 0.0, 0.0, 0.0], top_k=1)
    assert hits[0]["text"] == "second"


@pytest.mark.asyncio
async def test_delete_index_removes_only_this_index(backend) -> None:
    from api.services.pgvector_rag_backend import PgvectorConfig, PgvectorRAGBackend

    # Spin a sibling backend with a different index_id on the same DB.
    sibling = PgvectorRAGBackend(
        PgvectorConfig(dsn=backend._config.dsn),
        index_id=f"sibling-{uuid.uuid4()}",
    )
    await sibling.connect()

    try:
        await backend.upsert_chunks(
            [
                {
                    "id": uuid.uuid4(),
                    "text": "mine",
                    "embedding": [1.0, 0.0, 0.0, 0.0],
                }
            ]
        )
        await sibling.upsert_chunks(
            [
                {
                    "id": uuid.uuid4(),
                    "text": "theirs",
                    "embedding": [1.0, 0.0, 0.0, 0.0],
                }
            ]
        )
        assert await backend.count() == 1
        assert await sibling.count() == 1

        deleted = await backend.delete_index()
        assert deleted == 1

        # Sibling's row survives.
        assert await sibling.count() == 1
    finally:
        await sibling.delete_index()
        await sibling.close()


def test_factory_uses_real_backend_no_fallback() -> None:
    """The registry factory must return PgvectorRAGBackend (not the in-memory store)."""
    from api.services.pgvector_rag_backend import PgvectorRAGBackend
    from registry.rag import get_rag_backend

    instance = get_rag_backend(
        "pgvector",
        config={"dsn": "postgresql://nowhere:0/x"},  # not connected — factory only
        index_id="factory-test",
    )
    assert isinstance(instance, PgvectorRAGBackend)


def test_factory_raises_when_dsn_missing(monkeypatch) -> None:
    """No silent fallback — missing DSN should explicitly fail."""
    monkeypatch.delenv("PGVECTOR_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from registry.rag import get_rag_backend

    with pytest.raises(ValueError, match="requires a connection string"):
        get_rag_backend("pgvector", config={}, index_id="no-dsn")


# ---------------------------------------------------------------------------
# #423 wire-through — full RAGStore.search() round-trip via the live container.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_store_search_dispatches_to_pgvector_backend(pg_container) -> None:
    """RAGStore.search() with backend='pgvector' returns rows from Postgres."""
    from unittest.mock import AsyncMock, patch

    from api.services.rag_service import RAGStore

    dsn = _to_asyncpg_dsn(pg_container.get_connection_url())
    store = RAGStore()
    idx = store.create_index(
        name="wirethrough-roundtrip",
        backend="pgvector",
        backend_config={"dsn": dsn},
        embedding_model="openai/text-embedding-3-small",
    )

    backend = await store._get_backend(idx)
    assert backend is not None
    try:
        await backend.upsert_chunks(
            [
                {
                    "id": uuid.uuid4(),
                    "text": "Paris is the capital of France.",
                    "embedding": [1.0, 0.0, 0.0, 0.0],
                    "metadata": {"source": "europe.md"},
                },
                {
                    "id": uuid.uuid4(),
                    "text": "Tokyo is the capital of Japan.",
                    "embedding": [0.0, 1.0, 0.0, 0.0],
                    "metadata": {"source": "asia.md"},
                },
            ]
        )

        fake_embed = type("_R", (), {"vectors": [[1.0, 0.0, 0.0, 0.0]], "used_fallback": False})()
        with patch(
            "api.services.rag_service.embed_texts",
            new=AsyncMock(return_value=fake_embed),
        ):
            hits = await store.search(idx.id, query="ignored", top_k=2)

        assert len(hits) == 2
        assert hits[0].text == "Paris is the capital of France."
        assert hits[0].source == "europe.md"
        assert "source" not in hits[0].metadata
    finally:
        await backend.delete_index()
        await store.close_backends()


@pytest.mark.asyncio
async def test_rag_store_search_survives_simulated_restart(pg_container) -> None:
    """After a fresh RAGStore, search still finds rows persisted in Postgres."""
    from unittest.mock import AsyncMock, patch

    from api.services.rag_service import RAGStore

    dsn = _to_asyncpg_dsn(pg_container.get_connection_url())
    fake_embed = type("_R", (), {"vectors": [[1.0, 0.0]], "used_fallback": False})()

    store1 = RAGStore()
    idx1 = store1.create_index(
        name="restart-test",
        backend="pgvector",
        backend_config={"dsn": dsn},
    )
    b1 = await store1._get_backend(idx1)
    cid = uuid.uuid4()
    try:
        await b1.upsert_chunks(
            [
                {
                    "id": cid,
                    "text": "persisted across restarts",
                    "embedding": [1.0, 0.0],
                    "metadata": {"source": "x"},
                }
            ]
        )
    finally:
        await store1.close_backends()

    # Fresh store — simulates a server restart with empty in-memory state.
    store2 = RAGStore()
    idx2 = store2.create_index(
        name="restart-test-2",
        backend="pgvector",
        backend_config={"dsn": dsn},
    )
    # PgvectorRAGBackend scopes by index_id; reuse phase-1's id so the row is
    # visible in this run.
    idx2.id = idx1.id
    store2._indexes = {idx2.id: idx2}

    try:
        with patch(
            "api.services.rag_service.embed_texts",
            new=AsyncMock(return_value=fake_embed),
        ):
            hits = await store2.search(idx2.id, query="?", top_k=1)
        assert len(hits) == 1
        assert hits[0].text == "persisted across restarts"
        assert hits[0].chunk_id == str(cid)
    finally:
        b2 = await store2._get_backend(idx2)
        await b2.delete_index()
        await store2.close_backends()
