"""Neo4j RAG Backend — Graph-native vector + relationship storage for AgentBreeder.

Implements the same interface as the in-memory RAGStore backend so it can be
selected via ``backend: neo4j`` in ``rag.yaml``.

Public API (mirrors the in-memory store interface):
    Neo4jRAGBackend.index(documents)   — ingest pre-chunked documents with embeddings
    Neo4jRAGBackend.search(query, ...) — vector similarity + optional graph traversal
    Neo4jRAGBackend.close()            — release the driver connection

Requires: neo4j>=5.0  (pip install agentbreeder[rag])

Notes on Neo4j version recommendations:
    * Neo4j >= 5.16 ships native vector indexes (``CREATE VECTOR INDEX``) which
      enable sub-linear cosine similarity over chunk embeddings. The backend
      auto-detects and uses them when available, and otherwise falls back to a
      hand-rolled ``reduce()``-based cosine query that works on Neo4j >= 5.0.
    * Detection can be forced via the ``NEO4J_USE_NATIVE_VECTOR`` env var
      (``true``/``false``) when dynamic server-version probing is unreliable
      (e.g. tests or restricted deployments).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from api.retry import async_retry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Only imported at type-check time so tests can mock without the real driver.
    from neo4j import AsyncDriver


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


# Number of chunks (and their entities/relations) to write per Neo4j
# transaction. Smaller transactions reduce lock contention; larger ones cut
# round-trips. 50 is a reasonable default for typical document sizes.
DEFAULT_INGEST_BATCH_SIZE = 50

# Default upper bound on BFS path expansion in graph traversal. Without this,
# dense graphs can produce path explosions that OOM the driver.
DEFAULT_INTERMEDIATE_LIMIT = 1000

# Retry policy for transient Neo4j failures during ingestion.
INGEST_MAX_ATTEMPTS = 3
INGEST_INITIAL_DELAY = 0.5
INGEST_MAX_DELAY = 10.0


class Neo4jConfig:
    """Connection configuration for the Neo4j RAG backend."""

    def __init__(
        self,
        uri: str = "bolt://neo4j:7687",
        username: str = "neo4j",
        password: str = "password",  # noqa: S107
        database: str = "neo4j",
    ) -> None:
        self.uri = uri
        self.username = username
        self.password = password
        self.database = database

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> Neo4jConfig:
        """Construct from a raw rag.yaml ``config:`` dict."""
        return cls(
            uri=cfg.get("uri", "bolt://neo4j:7687"),
            username=cfg.get("username", "neo4j"),
            password=cfg.get("password", "password"),
            database=cfg.get("database", "neo4j"),
        )


# ---------------------------------------------------------------------------
# Cypher constants
# ---------------------------------------------------------------------------

_UPSERT_CHUNK_CYPHER = """
MERGE (c:Chunk {id: $id})
SET c.text      = $text,
    c.source    = $source,
    c.embedding = $embedding,
    c.metadata  = $metadata_json,
    c.index_id  = $index_id
"""

_UPSERT_ENTITY_CYPHER = """
MERGE (e:Entity {id: $id, index_id: $index_id})
SET e.name        = $name,
    e.entity_type = $entity_type,
    e.description = $description
WITH e
UNWIND $chunk_ids AS cid
MATCH (c:Chunk {id: cid})
MERGE (e)-[:MENTIONED_IN]->(c)
"""

_UPSERT_RELATION_CYPHER = """
MATCH (s:Entity {id: $subject_id, index_id: $index_id})
MATCH (o:Entity {id: $object_id, index_id: $index_id})
MERGE (s)-[r:RELATES {predicate: $predicate}]->(o)
SET r.weight = $weight
"""

# Hand-rolled cosine similarity for Neo4j < 5.16 (no native vector index).
# Computes:  (q · c) / (||q|| * ||c||)  inside the database with reduce().
_VECTOR_SEARCH_CYPHER = """
MATCH (c:Chunk {index_id: $index_id})
WHERE c.embedding IS NOT NULL
WITH c,
     reduce(dot = 0.0, i IN range(0, size(c.embedding)-1) |
         dot + c.embedding[i] * $query_embedding[i]) /
     (sqrt(reduce(s=0.0, x IN c.embedding | s + x*x)) *
      sqrt(reduce(s=0.0, x IN $query_embedding | s + x*x)) + 1e-10) AS score
ORDER BY score DESC
LIMIT $top_k
RETURN c.id AS chunk_id, c.text AS text, c.source AS source,
       c.metadata AS metadata_json, score
"""

# Native vector index path (Neo4j >= 5.16). Requires
# ``CREATE VECTOR INDEX chunk_embedding_idx`` on (:Chunk(embedding)). We use
# ``db.index.vector.queryNodes`` which is logarithmic in node count.
_VECTOR_SEARCH_CYPHER_NATIVE = """
CALL db.index.vector.queryNodes('chunk_embedding_idx', $top_k, $query_embedding)
YIELD node AS c, score
WHERE c.index_id = $index_id
RETURN c.id AS chunk_id, c.text AS text, c.source AS source,
       c.metadata AS metadata_json, score
ORDER BY score DESC
LIMIT $top_k
"""

# BFS-style graph traversal. The intermediate ``LIMIT`` after the path match
# caps path expansion before the (potentially expensive) MENTIONED_IN join.
_GRAPH_NEIGHBOR_CYPHER_TMPL = """
MATCH (e:Entity {{index_id: $index_id}})
WHERE e.name IN $seed_names
MATCH path = (e)-[:RELATES*1..{max_hops}]-(neighbor:Entity)
WITH neighbor, length(path) AS depth
ORDER BY depth ASC
LIMIT $intermediate_limit
MATCH (neighbor)-[:MENTIONED_IN]->(c:Chunk)
RETURN DISTINCT c.id AS chunk_id, c.text AS text, c.source AS source,
       c.metadata AS metadata_json, 1.0 / (depth + 1) AS score
LIMIT $top_k
"""

# Schema indexes — created idempotently on driver init.
_SCHEMA_INDEXES: tuple[str, ...] = (
    "CREATE INDEX chunk_id_index_id IF NOT EXISTS FOR (c:Chunk) ON (c.id, c.index_id)",
    "CREATE INDEX entity_id_index_id IF NOT EXISTS FOR (e:Entity) ON (e.id, e.index_id)",
)


def _detect_use_native_vector_from_env() -> bool | None:
    """Return True/False if ``NEO4J_USE_NATIVE_VECTOR`` is explicitly set, else None.

    Treats ``1/true/yes/on`` (case-insensitive) as True, ``0/false/no/off`` as
    False, anything else as unset.
    """
    raw = os.environ.get("NEO4J_USE_NATIVE_VECTOR")
    if raw is None:
        return None
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return None


# ---------------------------------------------------------------------------
# Neo4jRAGBackend
# ---------------------------------------------------------------------------


class Neo4jRAGBackend:
    """RAG backend that stores document chunks and entity relationships in Neo4j.

    Usage::

        config = Neo4jConfig(uri="bolt://localhost:7687", password="secret")
        backend = Neo4jRAGBackend(config)
        await backend.index(documents)
        results = await backend.search("what is machine learning?",
                                       query_embedding=[...], top_k=5)
        await backend.close()

    The ``index()`` method expects a list of document dicts with the following
    shape (same format produced by the AgentBreeder chunking + embedding pipeline)::

        {
            "id": str,                        # chunk UUID
            "text": str,                      # chunk text
            "source": str,                    # originating filename
            "embedding": list[float],         # vector embedding
            "metadata": dict,                 # arbitrary key/value pairs
            "entities": [                     # optional — from graph extraction
                {"id": str, "name": str, "entity_type": str, "description": str,
                 "chunk_ids": list[str]},
                ...
            ],
            "relations": [                    # optional — from graph extraction
                {"subject_id": str, "predicate": str, "object_id": str,
                 "weight": float},
                ...
            ],
        }

    ``search()`` returns a list of dicts::

        [{"chunk_id": str, "text": str, "source": str, "score": float,
          "metadata": dict}, ...]
    """

    def __init__(
        self,
        config: Neo4jConfig,
        index_id: str = "default",
        *,
        batch_size: int = DEFAULT_INGEST_BATCH_SIZE,
        intermediate_limit: int = DEFAULT_INTERMEDIATE_LIMIT,
    ) -> None:
        self._config = config
        self._index_id = index_id
        self._driver: AsyncDriver | None = None
        self._batch_size = max(1, int(batch_size))
        self._intermediate_limit = max(1, int(intermediate_limit))
        # Lazy: populated on first I/O. None means "not yet detected".
        self._use_native_vector: bool | None = None
        self._schema_initialized: bool = False

    # ------------------------------------------------------------------
    # Driver lifecycle
    # ------------------------------------------------------------------

    def _get_driver(self) -> AsyncDriver:
        """Return (creating lazily) the async Neo4j driver."""
        if self._driver is None:
            try:
                import neo4j  # noqa: PLC0415
            except ImportError as exc:
                raise ImportError(
                    "neo4j package is required for the Neo4j RAG backend. "
                    "Install it with: pip install agentbreeder[rag]"
                ) from exc

            self._driver = neo4j.AsyncGraphDatabase.driver(
                self._config.uri,
                auth=(self._config.username, self._config.password),
            )
        return self._driver

    async def _init_schema(self, session: Any) -> None:
        """Create idempotent indexes on first I/O. No-op on subsequent calls.

        Runs ``CREATE INDEX IF NOT EXISTS`` for (:Chunk(id, index_id)) and
        (:Entity(id, index_id)). Tolerant of older Neo4j versions that lack
        the ``IF NOT EXISTS`` modifier — failures are logged at debug and
        swallowed so ingest/search still work.
        """
        if self._schema_initialized:
            return
        for ddl in _SCHEMA_INDEXES:
            try:
                await session.run(ddl)
            except Exception as exc:  # noqa: BLE001 — best-effort DDL
                logger.debug(
                    "Neo4jRAGBackend: schema DDL failed (continuing): %s — %s",
                    ddl.split("\n", 1)[0],
                    exc,
                )
        self._schema_initialized = True

    async def _detect_native_vector_support(self, session: Any) -> bool:
        """Detect whether the connected Neo4j instance supports native vector indexes.

        Resolution order:
            1. ``NEO4J_USE_NATIVE_VECTOR`` env var (explicit override).
            2. Probe for ``db.index.vector.queryNodes`` procedure via SHOW PROCEDURES.
            3. Fall back to False (use hand-rolled cosine).

        Cached after the first call.
        """
        if self._use_native_vector is not None:
            return self._use_native_vector

        env_override = _detect_use_native_vector_from_env()
        if env_override is not None:
            self._use_native_vector = env_override
            logger.debug(
                "Neo4jRAGBackend: native vector support set via env override = %s",
                env_override,
            )
            return env_override

        # Probe by listing the procedure. SHOW PROCEDURES has been around
        # since Neo4j 4.x; the queryNodes procedure only exists on >= 5.11
        # (the original index plumbing) and is stable from 5.16 onward.
        try:
            probe = await session.run(
                "SHOW PROCEDURES YIELD name "
                "WHERE name = 'db.index.vector.queryNodes' "
                "RETURN count(name) AS n"
            )
            found = False
            async for record in probe:
                # Be tolerant of dict-like or attribute-like Records.
                n = (
                    record["n"]
                    if isinstance(record, dict)
                    else getattr(record, "__getitem__", lambda _k: 0)("n")
                )
                found = int(n) > 0
            self._use_native_vector = found
        except Exception as exc:  # noqa: BLE001 — probe must never break callers
            logger.debug(
                "Neo4jRAGBackend: native-vector probe failed, falling back: %s",
                exc,
            )
            self._use_native_vector = False

        logger.info(
            "Neo4jRAGBackend: native vector index support = %s (index=%s)",
            self._use_native_vector,
            self._index_id,
        )
        return self._use_native_vector

    async def close(self) -> None:
        """Close the Neo4j driver and release all connections."""
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
            self._schema_initialized = False
            self._use_native_vector = None
            logger.debug("Neo4jRAGBackend: driver closed (index=%s)", self._index_id)

    # ------------------------------------------------------------------
    # Index (ingest)
    # ------------------------------------------------------------------

    async def index(self, documents: list[dict[str, Any]]) -> int:
        """Ingest pre-chunked documents with embeddings into Neo4j.

        Creates/merges :Chunk nodes (one per document dict), :Entity nodes for
        any extracted entities, and :RELATES edges for extracted relationships.

        Writes are grouped into transactions of at most ``self._batch_size``
        documents each, and each transaction is retried with exponential
        backoff on transient driver failures.

        Args:
            documents: List of document dicts (see class docstring for shape).

        Returns:
            Number of chunks successfully written.
        """
        if not documents:
            return 0

        driver = self._get_driver()
        written = 0

        async with driver.session(database=self._config.database) as session:
            await self._init_schema(session)

            # Chunk the document list into batches of ``self._batch_size``.
            for start in range(0, len(documents), self._batch_size):
                batch = documents[start : start + self._batch_size]
                written += await self._ingest_batch_with_retry(session, batch)

        logger.info(
            "Neo4jRAGBackend.index: wrote %d chunks to index=%s (batches of %d)",
            written,
            self._index_id,
            self._batch_size,
        )
        return written

    async def _ingest_batch_with_retry(
        self,
        session: Any,
        batch: list[dict[str, Any]],
    ) -> int:
        """Wrap ``_ingest_batch_tx`` in async_retry with exponential backoff."""

        async def _attempt() -> int:
            return await self._ingest_batch_tx(session, batch)

        return await async_retry(
            _attempt,
            max_attempts=INGEST_MAX_ATTEMPTS,
            initial_delay=INGEST_INITIAL_DELAY,
            max_delay=INGEST_MAX_DELAY,
            backoff_factor=2.0,
            jitter=True,
            # Retry any Exception subclass — Neo4j driver raises many transient
            # types (ServiceUnavailable, TransientError, SessionExpired, ...)
            # and we don't want to import the driver just for the type tuple.
            retry_on=(Exception,),
        )

    async def _ingest_batch_tx(
        self,
        session: Any,
        batch: list[dict[str, Any]],
    ) -> int:
        """Write a single batch within an explicit Neo4j transaction.

        Falls back to driver-managed auto-commit semantics when the session
        does not expose ``begin_transaction`` (e.g. in unit tests where the
        session is an ``AsyncMock`` without that attribute).
        """
        begin_tx = getattr(session, "begin_transaction", None)
        if begin_tx is None:
            # Fallback path — useful for tests with simple session mocks.
            return await self._ingest_batch_direct(session, batch)

        # Real driver path — explicit transaction with begin_transaction().
        # ``begin_transaction()`` may be sync (returns context manager) or
        # async (returns an awaitable yielding a context manager); we tolerate
        # both shapes.
        tx_ctx = begin_tx()
        if hasattr(tx_ctx, "__aenter__"):
            tx_manager = tx_ctx
        else:
            tx_manager = await tx_ctx  # type: ignore[assignment]

        async with tx_manager as tx:  # type: ignore[union-attr]
            return await self._ingest_batch_direct(tx, batch)

    async def _ingest_batch_direct(
        self,
        runner: Any,
        batch: list[dict[str, Any]],
    ) -> int:
        """Issue MERGE statements for every chunk/entity/relation in ``batch``.

        ``runner`` is anything that exposes an async ``run(query, **params)``
        — either a transaction object or a session.
        """
        count = 0
        for doc in batch:
            chunk_id = doc["id"]
            embedding = doc.get("embedding") or []
            metadata = doc.get("metadata") or {}

            # Upsert the chunk node
            await runner.run(
                _UPSERT_CHUNK_CYPHER,
                id=chunk_id,
                text=doc.get("text", ""),
                source=doc.get("source", ""),
                embedding=embedding,
                metadata_json=json.dumps(metadata),
                index_id=self._index_id,
            )
            count += 1

            # Upsert entity nodes and MENTIONED_IN edges
            for entity in doc.get("entities") or []:
                await runner.run(
                    _UPSERT_ENTITY_CYPHER,
                    id=entity["id"],
                    index_id=self._index_id,
                    name=entity.get("name", ""),
                    entity_type=entity.get("entity_type", "UNKNOWN"),
                    description=entity.get("description", ""),
                    chunk_ids=entity.get("chunk_ids", [chunk_id]),
                )

            # Upsert relationship edges
            for rel in doc.get("relations") or []:
                await runner.run(
                    _UPSERT_RELATION_CYPHER,
                    subject_id=rel["subject_id"],
                    object_id=rel["object_id"],
                    predicate=rel.get("predicate", "RELATES"),
                    weight=float(rel.get("weight", 1.0)),
                    index_id=self._index_id,
                )

        return count

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(  # noqa: C901 — multi-step search w/ observability
        self,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        seed_entities: list[str] | None = None,
        max_hops: int = 2,
        intermediate_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant chunks via vector similarity + optional graph traversal.

        If ``seed_entities`` is provided (list of entity name strings), the
        search also performs a multi-hop graph traversal starting from those
        entities and merges the resulting chunks into the result set.

        Args:
            query:              Raw query text (used for logging; embedding does the work).
            query_embedding:    Pre-computed embedding for the query.
            top_k:              Maximum number of results to return.
            seed_entities:      Optional entity names to seed graph traversal from.
            max_hops:           Maximum BFS traversal depth (only used when seed_entities given).
            intermediate_limit: Cap on intermediate BFS path rows before the
                                MENTIONED_IN join. Defaults to the backend's
                                configured ``intermediate_limit`` (1000).

        Returns:
            List of result dicts sorted by score descending::

                [{"chunk_id": str, "text": str, "source": str,
                  "score": float, "metadata": dict}, ...]
        """
        # G9 — observability: wall-clock duration + per-phase neighbor counts.
        _t_start = time.perf_counter()
        _neighbors_found = 0
        driver = self._get_driver()
        results: dict[str, dict[str, Any]] = {}
        effective_intermediate = (
            int(intermediate_limit) if intermediate_limit is not None else self._intermediate_limit
        )

        async with driver.session(database=self._config.database) as session:
            await self._init_schema(session)

            # --- Vector similarity search (native if available) ---
            use_native = await self._detect_native_vector_support(session)
            vector_cypher = _VECTOR_SEARCH_CYPHER_NATIVE if use_native else _VECTOR_SEARCH_CYPHER
            try:
                vector_result = await session.run(
                    vector_cypher,
                    index_id=self._index_id,
                    query_embedding=query_embedding,
                    top_k=top_k,
                )
            except Exception as exc:  # noqa: BLE001
                if use_native:
                    # Native query failed (index missing, etc.) — fall back
                    # to the hand-rolled query and cache the negative result.
                    logger.warning(
                        "Neo4jRAGBackend: native vector query failed, "
                        "falling back to reduce(): %s",
                        exc,
                    )
                    self._use_native_vector = False
                    vector_result = await session.run(
                        _VECTOR_SEARCH_CYPHER,
                        index_id=self._index_id,
                        query_embedding=query_embedding,
                        top_k=top_k,
                    )
                else:
                    raise

            async for record in vector_result:
                cid = record["chunk_id"]
                results[cid] = {
                    "chunk_id": cid,
                    "text": record["text"],
                    "source": record["source"],
                    "score": float(record["score"]),
                    "metadata": self._parse_metadata(record.get("metadata_json") or "{}"),
                }

            # --- Graph traversal (optional) ---
            if seed_entities:
                # Inject max_hops literally into the query (it is an integer, not user input)
                graph_cypher = _GRAPH_NEIGHBOR_CYPHER_TMPL.format(max_hops=int(max_hops))
                graph_result = await session.run(
                    graph_cypher,
                    index_id=self._index_id,
                    seed_names=seed_entities,
                    top_k=top_k,
                    intermediate_limit=effective_intermediate,
                )
                async for record in graph_result:
                    cid = record["chunk_id"]
                    _neighbors_found += 1
                    # Merge: take max score if chunk already present
                    graph_score = float(record["score"])
                    if cid not in results or results[cid]["score"] < graph_score:
                        results[cid] = {
                            "chunk_id": cid,
                            "text": record["text"],
                            "source": record["source"],
                            "score": graph_score,
                            "metadata": self._parse_metadata(record.get("metadata_json") or "{}"),
                        }

        sorted_results = sorted(results.values(), key=lambda r: r["score"], reverse=True)
        _duration_ms = round((time.perf_counter() - _t_start) * 1000.0, 2)
        # G9 — structured info log for every graph search call (success path).
        logger.info(
            "graphrag.search.complete",
            extra={
                "index_id": self._index_id,
                "top_k": top_k,
                "neighbors_found": _neighbors_found,
                "results_returned": min(len(sorted_results), top_k),
                "seed_entities_count": len(seed_entities) if seed_entities else 0,
                "max_hops": max_hops if seed_entities else 0,
                "duration_ms": _duration_ms,
            },
        )
        logger.debug(
            "Neo4jRAGBackend.search: query=%r top_k=%d returned=%d (index=%s) duration_ms=%.2f",
            query,
            top_k,
            len(sorted_results),
            self._index_id,
            _duration_ms,
        )
        return sorted_results[:top_k]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_metadata(raw: str) -> dict[str, Any]:
        """Parse metadata stored as a JSON string."""
        try:
            val = json.loads(raw)
            return val if isinstance(val, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def create_neo4j_backend(
    config: dict[str, Any],
    index_id: str = "default",
) -> Neo4jRAGBackend:
    """Create a ``Neo4jRAGBackend`` from a raw ``rag.yaml`` ``config:`` dict.

    Args:
        config:   The value of ``config:`` key in rag.yaml (may be empty dict).
        index_id: Logical index identifier used to namespace Neo4j nodes.

    Returns:
        Configured ``Neo4jRAGBackend`` instance (driver not yet connected).
    """
    cfg = config or {}
    neo4j_config = Neo4jConfig.from_dict(cfg)
    batch_size = int(cfg.get("batch_size", DEFAULT_INGEST_BATCH_SIZE))
    intermediate_limit = int(cfg.get("intermediate_limit", DEFAULT_INTERMEDIATE_LIMIT))
    return Neo4jRAGBackend(
        neo4j_config,
        index_id=index_id,
        batch_size=batch_size,
        intermediate_limit=intermediate_limit,
    )
