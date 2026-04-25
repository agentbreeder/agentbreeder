"""Unit tests for Neo4j RAG backend and the RAG backend registry.

All Neo4j driver interactions are mocked — no live Neo4j instance is required.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.neo4j_rag_backend import (
    Neo4jConfig,
    Neo4jRAGBackend,
    create_neo4j_backend,
)
from registry.rag import (
    BACKEND_IN_MEMORY,
    BACKEND_NEO4J,
    BACKEND_PGVECTOR,
    get_rag_backend,
    list_backends,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(index_id: str = "test-index") -> Neo4jRAGBackend:
    """Return a backend with a pre-injected mock driver."""
    config = Neo4jConfig(uri="bolt://localhost:7687", username="neo4j", password="test")
    backend = Neo4jRAGBackend(config, index_id=index_id)
    return backend


class _AsyncIter:
    """Thin async iterator wrapper around a plain iterable."""

    def __init__(self, items: list) -> None:
        self._iter = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as err:
            raise StopAsyncIteration from err


def _make_async_result(records: list | None = None) -> _AsyncIter:
    """Return an async-iterable result wrapping *records*."""
    return _AsyncIter(records or [])


def _make_mock_driver(records: list | None = None) -> MagicMock:
    """Build a minimal mock of the async Neo4j driver + session context manager.

    The session's run() returns an async-iterable over *records* (default: empty).
    """
    async_result = _make_async_result(records)

    mock_session = AsyncMock()
    mock_session.run = AsyncMock(return_value=async_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)
    mock_driver.close = AsyncMock()

    return mock_driver


# ---------------------------------------------------------------------------
# Neo4jConfig tests
# ---------------------------------------------------------------------------


class TestNeo4jConfig:
    def test_defaults(self):
        cfg = Neo4jConfig()
        assert cfg.uri == "bolt://neo4j:7687"
        assert cfg.username == "neo4j"
        assert cfg.password == "password"
        assert cfg.database == "neo4j"

    def test_from_dict_full(self):
        cfg = Neo4jConfig.from_dict(
            {
                "uri": "bolt://myhost:7688",
                "username": "admin",
                "password": "secret",
                "database": "mydb",
            }
        )
        assert cfg.uri == "bolt://myhost:7688"
        assert cfg.username == "admin"
        assert cfg.password == "secret"
        assert cfg.database == "mydb"

    def test_from_dict_empty_uses_defaults(self):
        cfg = Neo4jConfig.from_dict({})
        assert cfg.uri == "bolt://neo4j:7687"
        assert cfg.database == "neo4j"

    def test_from_dict_partial(self):
        cfg = Neo4jConfig.from_dict({"uri": "bolt://custom:7687"})
        assert cfg.uri == "bolt://custom:7687"
        assert cfg.username == "neo4j"  # default


# ---------------------------------------------------------------------------
# Neo4jRAGBackend.index() tests
# ---------------------------------------------------------------------------


class TestNeo4jRAGBackendIndex:
    @pytest.mark.asyncio
    async def test_index_creates_chunk_nodes(self):
        """index() runs MERGE queries for every document chunk."""
        backend = _make_backend()
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        documents = [
            {
                "id": "chunk-1",
                "text": "Python is a programming language",
                "source": "doc.txt",
                "embedding": [0.1, 0.2, 0.3],
                "metadata": {"page": 1},
            },
            {
                "id": "chunk-2",
                "text": "Machine learning is a subfield of AI",
                "source": "doc.txt",
                "embedding": [0.4, 0.5, 0.6],
                "metadata": {"page": 2},
            },
        ]

        written = await backend.index(documents)

        assert written == 2
        # session.run was called at least once per document (chunk upsert)
        session = mock_driver.session.return_value.__aenter__.return_value
        assert session.run.call_count >= 2

    @pytest.mark.asyncio
    async def test_index_upserts_entities_and_relations(self):
        """index() issues additional Cypher for entities and relations."""
        backend = _make_backend()
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        documents = [
            {
                "id": "chunk-1",
                "text": "Python is used for ML",
                "source": "doc.txt",
                "embedding": [0.1, 0.2],
                "metadata": {},
                "entities": [
                    {
                        "id": "ent-python",
                        "name": "Python",
                        "entity_type": "LANGUAGE",
                        "description": "A programming language",
                        "chunk_ids": ["chunk-1"],
                    },
                    {
                        "id": "ent-ml",
                        "name": "Machine Learning",
                        "entity_type": "FIELD",
                        "description": "AI subfield",
                        "chunk_ids": ["chunk-1"],
                    },
                ],
                "relations": [
                    {
                        "subject_id": "ent-python",
                        "predicate": "USED_FOR",
                        "object_id": "ent-ml",
                        "weight": 0.9,
                    }
                ],
            }
        ]

        written = await backend.index(documents)

        assert written == 1
        session = mock_driver.session.return_value.__aenter__.return_value
        # 1 chunk + 2 entities + 1 relation = at least 4 calls
        assert session.run.call_count >= 4

    @pytest.mark.asyncio
    async def test_index_empty_documents_returns_zero(self):
        """index() with an empty list returns 0 without calling the driver."""
        backend = _make_backend()
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        written = await backend.index([])

        assert written == 0
        session = mock_driver.session.return_value.__aenter__.return_value
        assert session.run.call_count == 0

    @pytest.mark.asyncio
    async def test_index_metadata_serialized_as_json(self):
        """Metadata dict is stored as JSON string, not repr."""
        backend = _make_backend()
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        metadata = {"filename": "test.txt", "page": 3}
        documents = [
            {
                "id": "chunk-1",
                "text": "hello",
                "source": "test.txt",
                "embedding": [0.1],
                "metadata": metadata,
            }
        ]

        await backend.index(documents)

        session = mock_driver.session.return_value.__aenter__.return_value
        # Find the call that contains metadata_json kwarg
        chunk_call = session.run.call_args_list[0]
        kwargs = chunk_call.kwargs
        # metadata_json should be a valid JSON string
        parsed = json.loads(kwargs["metadata_json"])
        assert parsed == metadata


# ---------------------------------------------------------------------------
# Neo4jRAGBackend.search() tests
# ---------------------------------------------------------------------------


def _make_record(
    chunk_id: str,
    text: str,
    source: str,
    score: float,
    metadata_json: str = "{}",
) -> dict:
    """Return a plain dict that behaves like a Neo4j Record for our backend."""
    return {
        "chunk_id": chunk_id,
        "text": text,
        "source": source,
        "score": score,
        "metadata_json": metadata_json,
    }


class TestNeo4jRAGBackendSearch:
    @pytest.mark.asyncio
    async def test_search_returns_formatted_results(self):
        """search() formats driver records into the standard result dict shape."""
        backend = _make_backend()
        records = [
            _make_record("c1", "Python is great", "doc.txt", 0.95),
            _make_record("c2", "ML is cool", "doc2.txt", 0.80),
        ]
        mock_driver = _make_mock_driver(records=records)
        backend._driver = mock_driver

        results = await backend.search(
            query="Python",
            query_embedding=[0.1, 0.2, 0.3],
            top_k=5,
        )

        assert len(results) == 2
        assert results[0]["chunk_id"] == "c1"
        assert results[0]["text"] == "Python is great"
        assert results[0]["source"] == "doc.txt"
        assert abs(results[0]["score"] - 0.95) < 1e-6
        assert isinstance(results[0]["metadata"], dict)

    @pytest.mark.asyncio
    async def test_search_respects_top_k(self):
        """search() truncates results to top_k."""
        records = [
            _make_record(f"c{i}", f"text {i}", "doc.txt", 1.0 - i * 0.05)
            for i in range(10)
        ]
        backend = _make_backend()
        mock_driver = _make_mock_driver(records=records)
        backend._driver = mock_driver

        results = await backend.search(
            query="anything",
            query_embedding=[0.5] * 10,
            top_k=3,
        )

        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_search_sorts_by_score_descending(self):
        """search() returns results sorted by score, highest first."""
        records = [
            _make_record("c1", "low", "doc.txt", 0.3),
            _make_record("c2", "high", "doc.txt", 0.9),
            _make_record("c3", "mid", "doc.txt", 0.6),
        ]
        backend = _make_backend()
        mock_driver = _make_mock_driver(records=records)
        backend._driver = mock_driver

        results = await backend.search(
            query="test",
            query_embedding=[0.1, 0.2],
            top_k=5,
        )

        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_search_with_seed_entities_triggers_graph_traversal(self):
        """search() with seed_entities issues a second Cypher call for graph traversal."""
        backend = _make_backend()
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        session = mock_driver.session.return_value.__aenter__.return_value
        # Each call to run() returns a fresh empty async iterator
        session.run = AsyncMock(side_effect=lambda *a, **kw: _make_async_result([]))

        await backend.search(
            query="Python",
            query_embedding=[0.1],
            top_k=5,
            seed_entities=["Python", "Machine Learning"],
        )

        # Two run() calls: one for vector search, one for graph traversal
        assert session.run.call_count == 2

    @pytest.mark.asyncio
    async def test_search_no_seed_entities_no_graph_call(self):
        """search() without seed_entities only calls the vector search Cypher."""
        backend = _make_backend()
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        session = mock_driver.session.return_value.__aenter__.return_value
        session.run = AsyncMock(side_effect=lambda *a, **kw: _make_async_result([]))

        await backend.search(
            query="Python",
            query_embedding=[0.1],
            top_k=5,
        )

        # Only one run() call: the vector search
        assert session.run.call_count == 1

    @pytest.mark.asyncio
    async def test_search_deduplicates_by_chunk_id(self):
        """If the same chunk appears in both vector and graph results, it is deduped."""
        backend = _make_backend()
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        vector_records = [_make_record("c1", "shared chunk", "doc.txt", 0.8)]
        graph_records = [_make_record("c1", "shared chunk", "doc.txt", 0.6)]

        session = mock_driver.session.return_value.__aenter__.return_value
        call_count = {"n": 0}

        def _run_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _make_async_result(vector_records)
            return _make_async_result(graph_records)

        session.run = AsyncMock(side_effect=_run_side_effect)

        results = await backend.search(
            query="test",
            query_embedding=[0.1],
            top_k=5,
            seed_entities=["Python"],
        )

        chunk_ids = [r["chunk_id"] for r in results]
        assert chunk_ids.count("c1") == 1
        # The higher score (0.8 from vector) should be kept
        assert abs(results[0]["score"] - 0.8) < 1e-6


# ---------------------------------------------------------------------------
# Neo4jRAGBackend._parse_metadata tests
# ---------------------------------------------------------------------------


class TestParseMetadata:
    def test_valid_json(self):
        result = Neo4jRAGBackend._parse_metadata('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_empty_json_object(self):
        assert Neo4jRAGBackend._parse_metadata("{}") == {}

    def test_invalid_json_returns_empty_dict(self):
        assert Neo4jRAGBackend._parse_metadata("not-json") == {}

    def test_json_array_returns_empty_dict(self):
        assert Neo4jRAGBackend._parse_metadata("[1, 2, 3]") == {}

    def test_none_like_empty_string_returns_empty_dict(self):
        assert Neo4jRAGBackend._parse_metadata("") == {}


# ---------------------------------------------------------------------------
# Neo4jRAGBackend lifecycle tests
# ---------------------------------------------------------------------------


class TestNeo4jRAGBackendLifecycle:
    def test_driver_not_created_on_init(self):
        """The driver is NOT created until the first I/O call."""
        backend = _make_backend()
        assert backend._driver is None

    @pytest.mark.asyncio
    async def test_close_sets_driver_to_none(self):
        """close() calls driver.close() and sets _driver to None."""
        backend = _make_backend()
        mock_driver = _make_mock_driver()
        backend._driver = mock_driver

        await backend.close()

        mock_driver.close.assert_awaited_once()
        assert backend._driver is None

    @pytest.mark.asyncio
    async def test_close_noop_when_no_driver(self):
        """close() is safe to call when the driver was never created."""
        backend = _make_backend()
        # Should not raise
        await backend.close()

    def test_missing_neo4j_package_raises_import_error(self):
        """_get_driver() raises ImportError with a helpful message if neo4j is missing."""
        backend = _make_backend()
        with patch.dict("sys.modules", {"neo4j": None}):
            with pytest.raises(ImportError, match="pip install agentbreeder\\[rag\\]"):
                backend._get_driver()


# ---------------------------------------------------------------------------
# create_neo4j_backend factory tests
# ---------------------------------------------------------------------------


class TestCreateNeo4jBackend:
    def test_returns_neo4j_rag_backend_instance(self):
        backend = create_neo4j_backend(
            config={"uri": "bolt://host:7687", "password": "pw"},
            index_id="my-index",
        )
        assert isinstance(backend, Neo4jRAGBackend)
        assert backend._index_id == "my-index"
        assert backend._config.uri == "bolt://host:7687"

    def test_empty_config_uses_defaults(self):
        backend = create_neo4j_backend(config={}, index_id="idx")
        assert backend._config.uri == "bolt://neo4j:7687"

    def test_none_config_uses_defaults(self):
        backend = create_neo4j_backend(config=None, index_id="idx")  # type: ignore[arg-type]
        assert backend._config.database == "neo4j"


# ---------------------------------------------------------------------------
# Backend registry (registry/rag.py) tests
# ---------------------------------------------------------------------------


class TestBackendRegistry:
    def test_list_backends_returns_all_three(self):
        backends = list_backends()
        assert BACKEND_IN_MEMORY in backends
        assert BACKEND_PGVECTOR in backends
        assert BACKEND_NEO4J in backends

    def test_list_backends_sorted(self):
        backends = list_backends()
        assert backends == sorted(backends)

    def test_get_rag_backend_in_memory_returns_rag_store(self):
        """in_memory backend returns the global RAGStore instance."""
        from api.services.rag_service import RAGStore

        instance = get_rag_backend(BACKEND_IN_MEMORY)
        assert isinstance(instance, RAGStore)

    def test_get_rag_backend_pgvector_falls_back_to_in_memory(self):
        """pgvector backend currently falls back to RAGStore with a warning."""
        from api.services.rag_service import RAGStore

        instance = get_rag_backend(BACKEND_PGVECTOR)
        assert isinstance(instance, RAGStore)

    def test_get_rag_backend_neo4j_returns_neo4j_rag_backend(self):
        """neo4j backend factory returns a Neo4jRAGBackend."""
        instance = get_rag_backend(
            BACKEND_NEO4J,
            config={"uri": "bolt://localhost:7687", "password": "test"},
            index_id="test-idx",
        )
        assert isinstance(instance, Neo4jRAGBackend)
        assert instance._index_id == "test-idx"

    def test_get_rag_backend_neo4j_default_config(self):
        """neo4j backend factory works with empty config dict."""
        instance = get_rag_backend(BACKEND_NEO4J, config={}, index_id="default")
        assert isinstance(instance, Neo4jRAGBackend)

    def test_get_rag_backend_unknown_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown RAG backend 'badbackend'"):
            get_rag_backend("badbackend")

    def test_get_rag_backend_error_message_lists_valid_backends(self):
        with pytest.raises(ValueError, match="in_memory"):
            get_rag_backend("totally-wrong")
