"""Unit tests for Neo4j RAG backend and the RAG backend registry.

All Neo4j driver interactions are mocked — no live Neo4j instance is required.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.neo4j_rag_backend import (
    DEFAULT_INGEST_BATCH_SIZE,
    DEFAULT_INTERMEDIATE_LIMIT,
    Neo4jConfig,
    Neo4jRAGBackend,
    _detect_use_native_vector_from_env,
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
    """Return a backend with a pre-injected mock driver.

    Marks schema/native-vector detection as already complete so that the
    test's `session.run.call_count` reflects only the test-driven calls.
    The dedicated TestSchemaInit / TestNativeVectorDetection suites exercise
    those code paths explicitly.
    """
    config = Neo4jConfig(uri="bolt://localhost:7687", username="neo4j", password="test")
    backend = Neo4jRAGBackend(config, index_id=index_id)
    # Skip schema DDL + native-vector probe in tests that just want to assert
    # business-logic call counts.
    backend._schema_initialized = True
    backend._use_native_vector = False
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


class _FakeSession:
    """Async context-manager session that exposes only ``run()``.

    The real Neo4j ``AsyncSession`` has many other methods (begin_transaction,
    close, ...) — for tests we deliberately omit ``begin_transaction`` so the
    backend's ingest path falls back to the direct ``runner.run()`` mode
    instead of trying to open an explicit transaction.
    """

    def __init__(self, records: list | None = None) -> None:
        self._records = records or []
        # ``run`` is an AsyncMock so tests can inspect call_count, call_args,
        # and swap in side_effect functions to vary results per call.
        self.run = AsyncMock(side_effect=self._default_run_side_effect)

    def _default_run_side_effect(self, *_args, **_kwargs):
        # Return a fresh async iterator on every call so multiple ``await
        # session.run(...)`` invocations each yield the same records list.
        return _make_async_result(self._records)

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False


def _make_mock_driver(records: list | None = None) -> MagicMock:
    """Build a minimal mock of the async Neo4j driver + session context manager.

    The session's run() returns an async-iterable over *records* (default: empty).

    The session intentionally does NOT expose ``begin_transaction`` — the
    backend's ingest path treats that as a signal to use the auto-commit
    fallback (``runner.run(...)`` directly on the session). This keeps the
    mocks small while still exercising every ``run()`` call.
    """
    mock_session = _FakeSession(records=records)

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
        session = mock_driver.session.return_value
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
        session = mock_driver.session.return_value
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
        session = mock_driver.session.return_value
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

        session = mock_driver.session.return_value
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
            _make_record(f"c{i}", f"text {i}", "doc.txt", 1.0 - i * 0.05) for i in range(10)
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

        session = mock_driver.session.return_value
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

        session = mock_driver.session.return_value
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

        session = mock_driver.session.return_value
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

    def test_get_rag_backend_pgvector_returns_real_backend(self):
        """HR-4 (#406) shipped the real adapter; #423 wires it through RAGStore."""
        from api.services.pgvector_rag_backend import PgvectorRAGBackend

        instance = get_rag_backend(
            BACKEND_PGVECTOR,
            config={"dsn": "postgresql://x:0/y"},
        )
        assert isinstance(instance, PgvectorRAGBackend)

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


# ---------------------------------------------------------------------------
# W4-27 — Batched ingestion + retry tests
# ---------------------------------------------------------------------------


class _FakeTxSession:
    """Session mock that exposes ``begin_transaction()`` returning a tx context manager.

    Tracks ``tx_count`` (number of transactions opened) and ``run_calls``
    (every Cypher run() that landed on a transaction).
    """

    def __init__(self, records: list | None = None) -> None:
        self._records = records or []
        self.tx_count = 0
        self.run_calls: list[tuple[str, dict]] = []
        # Auto-commit run() — used by schema init path (DDL outside a tx).
        self.run = AsyncMock(side_effect=self._auto_run_side_effect)

    def _auto_run_side_effect(self, query, **kwargs):
        # Record every direct-on-session call (typically DDL during init).
        self.run_calls.append((query, kwargs))
        return _make_async_result(self._records)

    def begin_transaction(self) -> _FakeTx:
        self.tx_count += 1
        return _FakeTx(self)

    async def __aenter__(self) -> _FakeTxSession:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False


class _FakeTx:
    """Transaction context manager that forwards ``run()`` to the parent session."""

    def __init__(self, session: _FakeTxSession) -> None:
        self._session = session
        # run is an AsyncMock so tests can patch side_effect / assert call_count.
        self.run = AsyncMock(side_effect=self._run_side_effect)

    def _run_side_effect(self, query, **kwargs):
        self._session.run_calls.append((query, kwargs))
        return _make_async_result(self._session._records)

    async def __aenter__(self) -> _FakeTx:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False


def _make_tx_mock_driver(records: list | None = None) -> tuple[MagicMock, _FakeTxSession]:
    """Build a mock driver whose session exposes ``begin_transaction``."""
    session = _FakeTxSession(records=records)
    driver = MagicMock()
    driver.session = MagicMock(return_value=session)
    driver.close = AsyncMock()
    return driver, session


class TestIngestBatching:
    @pytest.mark.asyncio
    async def test_index_batches_documents(self):
        """index() splits documents into batches of self._batch_size."""
        backend = _make_backend()
        backend._batch_size = 3
        driver, session = _make_tx_mock_driver()
        backend._driver = driver

        # 7 docs -> 3 batches (3 + 3 + 1)
        documents = [
            {"id": f"chunk-{i}", "text": "t", "source": "s", "embedding": [0.0], "metadata": {}}
            for i in range(7)
        ]
        written = await backend.index(documents)
        assert written == 7
        # 3 batches → 3 transactions opened
        assert session.tx_count == 3

    @pytest.mark.asyncio
    async def test_index_uses_transactions_when_available(self):
        """index() opens a begin_transaction() when the session supports it."""
        backend = _make_backend()
        backend._batch_size = 50
        driver, session = _make_tx_mock_driver()
        backend._driver = driver

        documents = [
            {"id": "c1", "text": "t", "source": "s", "embedding": [0.1], "metadata": {}},
        ]
        await backend.index(documents)
        assert session.tx_count == 1

    @pytest.mark.asyncio
    async def test_index_retries_on_transient_failure(self):
        """Transient failures during ingest are retried before raising."""
        backend = _make_backend()
        backend._batch_size = 10
        driver, session = _make_tx_mock_driver()
        backend._driver = driver

        # First two transactions raise, third succeeds. We model this by
        # making begin_transaction() return a tx whose run() raises N times
        # then succeeds.
        call_count = {"n": 0}

        original_begin = session.begin_transaction

        def begin_with_failures():
            call_count["n"] += 1
            tx = original_begin()
            attempt = call_count["n"]

            async def flaky_run(query, **kwargs):
                if attempt < 3:
                    raise RuntimeError(f"transient (attempt {attempt})")
                return _make_async_result([])

            tx.run = AsyncMock(side_effect=flaky_run)
            return tx

        session.begin_transaction = begin_with_failures  # type: ignore[assignment]

        # Patch asyncio.sleep so the test runs fast.
        with patch("api.retry.asyncio.sleep", new=AsyncMock()):
            written = await backend.index(
                [{"id": "c1", "text": "t", "source": "s", "embedding": [0.1], "metadata": {}}]
            )
        assert written == 1
        # First 2 begin_transaction() retries failed; 3rd succeeded.
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_index_falls_back_when_no_begin_transaction(self):
        """Sessions without begin_transaction use direct-on-session run() calls."""
        backend = _make_backend()
        backend._batch_size = 10
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        documents = [
            {"id": "c1", "text": "t", "source": "s", "embedding": [0.1], "metadata": {}},
        ]
        written = await backend.index(documents)
        assert written == 1
        # No transaction was opened — direct fallback path was used.
        session = mock_driver.session.return_value
        assert session.run.call_count >= 1

    def test_create_neo4j_backend_uses_batch_size_from_config(self):
        """The factory wires batch_size from rag.yaml config."""
        backend = create_neo4j_backend(config={"batch_size": 17}, index_id="idx")
        assert backend._batch_size == 17

    def test_create_neo4j_backend_default_batch_size(self):
        backend = create_neo4j_backend(config={}, index_id="idx")
        assert backend._batch_size == DEFAULT_INGEST_BATCH_SIZE


# ---------------------------------------------------------------------------
# W4-28 — Schema (index) initialization tests
# ---------------------------------------------------------------------------


class TestSchemaInit:
    @pytest.mark.asyncio
    async def test_schema_init_runs_create_index_statements(self):
        """First I/O triggers CREATE INDEX IF NOT EXISTS for Chunk and Entity."""
        config = Neo4jConfig(uri="bolt://localhost:7687", password="test")
        backend = Neo4jRAGBackend(config, index_id="test")
        # Pre-set native vector to avoid extra probe calls in this test.
        backend._use_native_vector = False
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        await backend.index(
            [{"id": "c1", "text": "t", "source": "s", "embedding": [0.0], "metadata": {}}]
        )

        session = mock_driver.session.return_value
        queries = [c.args[0] for c in session.run.call_args_list]
        assert any("CREATE INDEX" in q and "Chunk" in q for q in queries)
        assert any("CREATE INDEX" in q and "Entity" in q for q in queries)

    @pytest.mark.asyncio
    async def test_schema_init_runs_only_once(self):
        """Schema DDL is idempotent — only issued on the first call."""
        backend = _make_backend()
        # Reset the schema-init flag set by _make_backend() so we exercise
        # the real init path on the first call.
        backend._schema_initialized = False
        backend._use_native_vector = False
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        await backend.index(
            [{"id": "c1", "text": "t", "source": "s", "embedding": [0.0], "metadata": {}}]
        )
        await backend.index(
            [{"id": "c2", "text": "t", "source": "s", "embedding": [0.0], "metadata": {}}]
        )

        session = mock_driver.session.return_value
        create_calls = [c for c in session.run.call_args_list if "CREATE INDEX" in c.args[0]]
        # Exactly 2 CREATE INDEX statements (Chunk + Entity), not 4.
        assert len(create_calls) == 2

    @pytest.mark.asyncio
    async def test_schema_ddl_failures_swallowed(self):
        """DDL errors don't crash ingestion (best-effort schema init)."""
        backend = _make_backend()
        backend._schema_initialized = False
        backend._use_native_vector = False
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        session = mock_driver.session.return_value

        original_side_effect = session.run.side_effect

        def maybe_fail(query, **kwargs):
            if "CREATE INDEX" in query:
                raise RuntimeError("simulated DDL failure")
            return original_side_effect(query, **kwargs)

        session.run = AsyncMock(side_effect=maybe_fail)

        # Should not raise even though every DDL fails.
        written = await backend.index(
            [{"id": "c1", "text": "t", "source": "s", "embedding": [0.0], "metadata": {}}]
        )
        assert written == 1


# ---------------------------------------------------------------------------
# W4-29 — Native vector index detection tests
# ---------------------------------------------------------------------------


class TestNativeVectorDetection:
    def test_env_override_true(self, monkeypatch):
        monkeypatch.setenv("NEO4J_USE_NATIVE_VECTOR", "true")
        assert _detect_use_native_vector_from_env() is True

    def test_env_override_false(self, monkeypatch):
        monkeypatch.setenv("NEO4J_USE_NATIVE_VECTOR", "false")
        assert _detect_use_native_vector_from_env() is False

    def test_env_override_unset(self, monkeypatch):
        monkeypatch.delenv("NEO4J_USE_NATIVE_VECTOR", raising=False)
        assert _detect_use_native_vector_from_env() is None

    def test_env_override_garbage_returns_none(self, monkeypatch):
        monkeypatch.setenv("NEO4J_USE_NATIVE_VECTOR", "maybe")
        assert _detect_use_native_vector_from_env() is None

    @pytest.mark.asyncio
    async def test_detection_caches_result(self, monkeypatch):
        """After the first probe, subsequent calls don't issue SHOW PROCEDURES."""
        monkeypatch.delenv("NEO4J_USE_NATIVE_VECTOR", raising=False)
        backend = _make_backend()
        # Reset the cached flag so detection actually runs.
        backend._use_native_vector = None

        session = _FakeSession(records=[{"n": 0}])  # 0 procedures match
        first = await backend._detect_native_vector_support(session)
        # Cache hit on second call — no new run().
        second = await backend._detect_native_vector_support(session)
        assert first is False
        assert second is False
        # SHOW PROCEDURES was called exactly once.
        assert session.run.call_count == 1

    @pytest.mark.asyncio
    async def test_detection_returns_true_when_procedure_exists(self, monkeypatch):
        monkeypatch.delenv("NEO4J_USE_NATIVE_VECTOR", raising=False)
        backend = _make_backend()
        backend._use_native_vector = None

        session = _FakeSession(records=[{"n": 1}])
        result = await backend._detect_native_vector_support(session)
        assert result is True

    @pytest.mark.asyncio
    async def test_detection_returns_false_on_probe_error(self, monkeypatch):
        """A failing SHOW PROCEDURES probe falls back to hand-rolled cosine."""
        monkeypatch.delenv("NEO4J_USE_NATIVE_VECTOR", raising=False)
        backend = _make_backend()
        backend._use_native_vector = None

        session = _FakeSession(records=[])
        session.run = AsyncMock(side_effect=RuntimeError("no such procedure"))

        result = await backend._detect_native_vector_support(session)
        assert result is False

    @pytest.mark.asyncio
    async def test_search_uses_native_cypher_when_supported(self):
        """search() picks db.index.vector.queryNodes when native is on."""
        backend = _make_backend()
        backend._use_native_vector = True
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        await backend.search(query="q", query_embedding=[0.1, 0.2], top_k=3)

        session = mock_driver.session.return_value
        queries = [c.args[0] for c in session.run.call_args_list]
        assert any("db.index.vector.queryNodes" in q for q in queries)

    @pytest.mark.asyncio
    async def test_search_uses_handrolled_cypher_when_native_disabled(self):
        """search() picks reduce()-based cosine when native is off."""
        backend = _make_backend()
        backend._use_native_vector = False
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        await backend.search(query="q", query_embedding=[0.1, 0.2], top_k=3)

        session = mock_driver.session.return_value
        queries = [c.args[0] for c in session.run.call_args_list]
        # No call to the native procedure
        assert not any("db.index.vector.queryNodes" in q for q in queries)
        # And we did call the reduce() variant
        assert any("reduce(dot = 0.0" in q for q in queries)

    @pytest.mark.asyncio
    async def test_search_native_failure_falls_back_to_handrolled(self):
        """If the native query throws (e.g. index missing), fall back gracefully."""
        backend = _make_backend()
        backend._use_native_vector = True
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        session = mock_driver.session.return_value
        call_count = {"n": 0}

        def selective_failure(query, **kwargs):
            call_count["n"] += 1
            if "db.index.vector.queryNodes" in query:
                raise RuntimeError("index not found")
            return _make_async_result([])

        session.run = AsyncMock(side_effect=selective_failure)

        # Should not raise — fallback path kicks in.
        await backend.search(query="q", query_embedding=[0.1, 0.2], top_k=3)
        # We expect at least 2 calls: native (failed) + fallback handrolled.
        assert call_count["n"] >= 2
        # And the cached flag was flipped to False.
        assert backend._use_native_vector is False


# ---------------------------------------------------------------------------
# W4-30 — BFS intermediate_limit tests
# ---------------------------------------------------------------------------


class TestBFSIntermediateLimit:
    @pytest.mark.asyncio
    async def test_search_passes_intermediate_limit(self):
        """graph-traversal Cypher receives the configured intermediate_limit."""
        backend = _make_backend()
        backend._intermediate_limit = 250
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        await backend.search(
            query="q",
            query_embedding=[0.1, 0.2],
            top_k=5,
            seed_entities=["Alice"],
        )

        session = mock_driver.session.return_value
        # Find the graph-traversal call (the one with seed_names kwarg).
        graph_calls = [c for c in session.run.call_args_list if "seed_names" in c.kwargs]
        assert graph_calls, "expected a graph-traversal call"
        assert graph_calls[0].kwargs["intermediate_limit"] == 250

    @pytest.mark.asyncio
    async def test_search_intermediate_limit_override(self):
        """The intermediate_limit search kwarg overrides the instance default."""
        backend = _make_backend()
        backend._intermediate_limit = 1000
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        await backend.search(
            query="q",
            query_embedding=[0.1, 0.2],
            top_k=5,
            seed_entities=["Alice"],
            intermediate_limit=42,
        )

        session = mock_driver.session.return_value
        graph_calls = [c for c in session.run.call_args_list if "seed_names" in c.kwargs]
        assert graph_calls[0].kwargs["intermediate_limit"] == 42

    @pytest.mark.asyncio
    async def test_graph_cypher_contains_limit_clause(self):
        """The BFS Cypher template includes the LIMIT $intermediate_limit clause."""
        backend = _make_backend()
        mock_driver = _make_mock_driver(records=[])
        backend._driver = mock_driver

        await backend.search(
            query="q",
            query_embedding=[0.1, 0.2],
            top_k=5,
            seed_entities=["Alice"],
        )

        session = mock_driver.session.return_value
        graph_calls = [c for c in session.run.call_args_list if "seed_names" in c.kwargs]
        assert "$intermediate_limit" in graph_calls[0].args[0]

    def test_create_neo4j_backend_uses_intermediate_limit_from_config(self):
        backend = create_neo4j_backend(config={"intermediate_limit": 75}, index_id="idx")
        assert backend._intermediate_limit == 75

    def test_create_neo4j_backend_default_intermediate_limit(self):
        backend = create_neo4j_backend(config={}, index_id="idx")
        assert backend._intermediate_limit == DEFAULT_INTERMEDIATE_LIMIT


# ---------------------------------------------------------------------------
# Lifecycle: close() resets cached flags
# ---------------------------------------------------------------------------


class TestCloseResetsCachedFlags:
    @pytest.mark.asyncio
    async def test_close_resets_schema_and_vector_flags(self):
        """close() forgets cached detection so a fresh connection re-probes."""
        backend = _make_backend()
        mock_driver = _make_mock_driver()
        backend._driver = mock_driver
        backend._schema_initialized = True
        backend._use_native_vector = True

        await backend.close()

        assert backend._schema_initialized is False
        assert backend._use_native_vector is None
