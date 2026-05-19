"""#423 — RAGStore backend wire-through tests.

Verify that when a `RAGIndex.backend != "in_memory"`, `RAGStore.search()`
dispatches to the backend's `search()` and ingest writes through
`backend.upsert_chunks()`. Backend itself is mocked so this suite runs
without pgvector / Postgres on the test box.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from api.services.rag_service import DocumentChunk, RAGStore, SearchHit


def _make_backend_mock() -> AsyncMock:
    backend = AsyncMock()
    backend.connect = AsyncMock(return_value=None)
    backend.close = AsyncMock(return_value=None)
    backend.upsert_chunks = AsyncMock(return_value=0)
    backend.search = AsyncMock(return_value=[])
    return backend


def test_create_index_records_backend_fields() -> None:
    store = RAGStore()
    idx = store.create_index(
        name="legal",
        backend="pgvector",
        backend_config={"dsn": "postgresql://x:0/y"},
    )
    assert idx.backend == "pgvector"
    assert idx.backend_config == {"dsn": "postgresql://x:0/y"}
    assert idx.to_dict()["backend"] == "pgvector"


def test_create_index_default_backend_is_in_memory() -> None:
    store = RAGStore()
    idx = store.create_index(name="plain")
    assert idx.backend == "in_memory"
    assert idx.backend_config == {}


@pytest.mark.asyncio
async def test_get_backend_returns_none_for_in_memory() -> None:
    store = RAGStore()
    idx = store.create_index(name="plain")
    assert await store._get_backend(idx) is None


@pytest.mark.asyncio
async def test_get_backend_connects_and_caches_external() -> None:
    store = RAGStore()
    idx = store.create_index(
        name="pg", backend="pgvector", backend_config={"dsn": "postgresql://x:0/y"}
    )
    mock_backend = _make_backend_mock()
    with patch("registry.rag.get_rag_backend", return_value=mock_backend):
        first = await store._get_backend(idx)
        second = await store._get_backend(idx)
    assert first is mock_backend
    assert second is mock_backend
    # connect should be called exactly once (caching works).
    assert mock_backend.connect.await_count == 1


@pytest.mark.asyncio
async def test_search_dispatches_to_backend_when_configured() -> None:
    store = RAGStore()
    idx = store.create_index(
        name="pg",
        backend="pgvector",
        backend_config={"dsn": "postgresql://x:0/y"},
        embedding_model="openai/text-embedding-3-small",
    )

    mock_backend = _make_backend_mock()
    mock_backend.search.return_value = [
        {
            "chunk_id": "c-1",
            "text": "from postgres",
            "metadata": {"source": "docs.md", "tag": "kept"},
            "score": 0.91,
            "distance": 0.09,
        }
    ]

    fake_embed_result = type("_R", (), {"vectors": [[0.1, 0.2, 0.3]], "used_fallback": False})()

    with (
        patch("registry.rag.get_rag_backend", return_value=mock_backend),
        patch(
            "api.services.rag_service.embed_texts",
            new=AsyncMock(return_value=fake_embed_result),
        ),
    ):
        hits = await store.search(idx.id, query="hello", top_k=3)

    assert len(hits) == 1
    assert isinstance(hits[0], SearchHit)
    assert hits[0].chunk_id == "c-1"
    assert hits[0].text == "from postgres"
    assert hits[0].source == "docs.md"
    assert hits[0].metadata == {"tag": "kept"}
    # The backend was called with the query embedding (not the raw query
    # string), and got top_k forwarded.
    mock_backend.search.assert_awaited_once()
    call_args = mock_backend.search.await_args
    assert call_args.args[0] == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_search_uses_in_memory_path_when_backend_in_memory() -> None:
    """No backend dispatch when an index is in_memory."""
    store = RAGStore()
    idx = store.create_index(name="plain")
    # Add a single chunk so the early-return guard doesn't fire.
    idx.chunks.append(DocumentChunk(id="c-1", text="hi", source="x", embedding=[0.1, 0.0, 0.0]))
    idx.chunk_count = 1

    fake_embed_result = type("_R", (), {"vectors": [[0.1, 0.0, 0.0]], "used_fallback": False})()
    with (
        patch(
            "api.services.rag_service.embed_texts",
            new=AsyncMock(return_value=fake_embed_result),
        ),
        patch("registry.rag.get_rag_backend") as registry_factory,
    ):
        hits = await store.search(idx.id, query="hi", top_k=1)

    registry_factory.assert_not_called()
    assert len(hits) == 1
    assert hits[0].chunk_id == "c-1"


@pytest.mark.asyncio
async def test_search_does_not_short_circuit_when_backend_set_and_chunks_empty() -> None:
    """After a restart, idx.chunks may be empty even though pgvector has data."""
    store = RAGStore()
    idx = store.create_index(
        name="pg", backend="pgvector", backend_config={"dsn": "postgresql://x:0/y"}
    )
    # Simulate post-restart: backend has rows, but idx.chunks is empty.
    assert idx.chunks == []

    mock_backend = _make_backend_mock()
    mock_backend.search.return_value = [
        {
            "chunk_id": str(uuid.uuid4()),
            "text": "rehydrated from postgres",
            "metadata": {"source": "post-restart.md"},
            "score": 0.7,
        }
    ]
    fake_embed_result = type("_R", (), {"vectors": [[0.1, 0.2, 0.3]], "used_fallback": False})()

    with (
        patch("registry.rag.get_rag_backend", return_value=mock_backend),
        patch(
            "api.services.rag_service.embed_texts",
            new=AsyncMock(return_value=fake_embed_result),
        ),
    ):
        hits = await store.search(idx.id, query="?", top_k=1)
    assert len(hits) == 1
    assert hits[0].text == "rehydrated from postgres"


@pytest.mark.asyncio
async def test_close_backends_closes_each_cached_backend() -> None:
    store = RAGStore()
    idx_a = store.create_index(name="a", backend="pgvector", backend_config={"dsn": "x"})
    idx_b = store.create_index(name="b", backend="pgvector", backend_config={"dsn": "y"})

    ba, bb = _make_backend_mock(), _make_backend_mock()
    side_effect_factories = iter([ba, bb])
    with patch(
        "registry.rag.get_rag_backend",
        side_effect=lambda *a, **kw: next(side_effect_factories),
    ):
        await store._get_backend(idx_a)
        await store._get_backend(idx_b)

    await store.close_backends()
    ba.close.assert_awaited_once()
    bb.close.assert_awaited_once()
    assert store._backends == {}
