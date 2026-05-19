"""PostgreSQL + pgvector RAG backend (HR-4 / #406).

Replaces the silent-fallback-to-in-memory shim with a real adapter.

Storage layout (single table, namespaced by ``index_id``)::

    CREATE TABLE rag_pgvector_chunks (
        index_id   text          NOT NULL,
        chunk_id   uuid          PRIMARY KEY,
        text       text          NOT NULL,
        embedding  vector(N)     NOT NULL,
        metadata   jsonb         NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz   NOT NULL DEFAULT now()
    );
    CREATE INDEX ON rag_pgvector_chunks USING ivfflat (embedding vector_cosine_ops);

The vector dimension is fixed per index — the backend creates one table per
distinct dimension on first use (``rag_pgvector_chunks_d{N}``) so a project
can run multiple embedding models against the same Postgres instance.

This module is intentionally self-contained: it runs ``CREATE EXTENSION IF
NOT EXISTS vector`` and the DDL on connect, so a fresh Docker container
(``docker run -p 5432:5432 -e POSTGRES_PASSWORD=pw pgvector/pgvector:pg16``)
becomes usable without alembic on the local dev path. Production deploys
should still ship the DDL via alembic — see #406 follow-up.

The full rag_service.py search/ingest pipeline is not yet wired to call into
this backend; that's a sibling follow-up (#406 wire-through). Today the
backend can be exercised directly + via the integration test.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import asyncpg


@dataclass
class PgvectorConfig:
    """User-supplied configuration for the pgvector backend."""

    dsn: str
    pool_min_size: int = 1
    pool_max_size: int = 10


class PgvectorRAGBackend:
    """PostgreSQL + pgvector adapter implementing the RAG backend interface.

    Methods are async; all SQL goes through a connection pool. The first call
    to :meth:`connect` is idempotent and ensures the ``vector`` extension and
    the namespaced chunk table exist.
    """

    def __init__(self, config: PgvectorConfig, index_id: str = "default") -> None:
        self._config = config
        self._index_id = index_id
        self._pool: asyncpg.Pool | None = None
        self._initialised_dims: set[int] = set()

    # ---- lifecycle ----------------------------------------------------

    async def connect(self) -> None:
        """Open the connection pool and install the pgvector extension."""
        if self._pool is not None:
            return
        try:
            import asyncpg  # noqa: PLC0415
        except ImportError as e:
            raise ImportError(
                "pgvector backend requires asyncpg. "
                "Install via: pip install 'agentbreeder[rag]' (which pulls pgvector + asyncpg)."
            ) from e

        self._pool = await asyncpg.create_pool(
            self._config.dsn,
            min_size=self._config.pool_min_size,
            max_size=self._config.pool_max_size,
        )
        async with self._pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._initialised_dims.clear()

    # ---- schema -------------------------------------------------------

    def _table_for(self, dims: int) -> str:
        """Return the table name namespaced by embedding dimension."""
        return f"rag_pgvector_chunks_d{int(dims)}"

    async def _ensure_table(self, dims: int) -> None:
        """Create the dimension-specific chunk table + ANN index on first use."""
        if dims in self._initialised_dims:
            return
        assert self._pool is not None, "call connect() first"
        table = self._table_for(dims)
        async with self._pool.acquire() as conn:
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    index_id   text          NOT NULL,
                    chunk_id   uuid          PRIMARY KEY,
                    text       text          NOT NULL,
                    embedding  vector({dims}) NOT NULL,
                    metadata   jsonb         NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at timestamptz   NOT NULL DEFAULT now()
                );
            """)
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_index_id_idx ON {table} (index_id);"
            )
            # IVFFlat index for cosine similarity. Lists=100 is a sensible
            # default for up to ~1M rows; tuning is left to the operator.
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_embedding_idx "
                f"ON {table} USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);"
            )
        self._initialised_dims.add(dims)

    # ---- public API ---------------------------------------------------

    async def upsert_chunks(
        self,
        chunks: list[dict[str, Any]],
    ) -> int:
        """Insert (or replace) chunks. Each chunk must carry an ``embedding`` list[float].

        Required keys per chunk: ``id`` (str | UUID), ``text`` (str), ``embedding`` (list[float]).
        Optional: ``metadata`` (dict).

        Returns the number of chunks written.
        """
        assert self._pool is not None, "call connect() first"
        if not chunks:
            return 0
        dims = len(chunks[0]["embedding"])
        await self._ensure_table(dims)
        table = self._table_for(dims)

        rows: list[tuple[Any, ...]] = []
        for c in chunks:
            cid = c["id"]
            if not isinstance(cid, uuid.UUID):
                cid = uuid.UUID(str(cid))
            embedding = c["embedding"]
            if len(embedding) != dims:
                raise ValueError(
                    f"chunk {cid} has embedding dim {len(embedding)}; expected {dims} for this batch"
                )
            rows.append(
                (
                    self._index_id,
                    cid,
                    c["text"],
                    _vector_literal(embedding),
                    json.dumps(c.get("metadata") or {}),
                )
            )

        async with self._pool.acquire() as conn:
            await conn.executemany(
                f"""
                INSERT INTO {table} (index_id, chunk_id, text, embedding, metadata)
                VALUES ($1, $2, $3, $4::vector, $5::jsonb)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    text = EXCLUDED.text,
                    embedding = EXCLUDED.embedding,
                    metadata = EXCLUDED.metadata;
                """,
                rows,
            )
        return len(rows)

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Cosine-similarity search. Returns top_k hits ordered by ascending distance."""
        assert self._pool is not None, "call connect() first"
        dims = len(query_embedding)
        # If the table doesn't exist yet, there are no chunks to find.
        if dims not in self._initialised_dims:
            return []
        table = self._table_for(dims)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT chunk_id, text, metadata,
                       embedding <=> $1::vector AS distance
                FROM {table}
                WHERE index_id = $2
                ORDER BY distance ASC
                LIMIT $3;
                """,
                _vector_literal(query_embedding),
                self._index_id,
                top_k,
            )
        return [
            {
                "chunk_id": str(row["chunk_id"]),
                "text": row["text"],
                "metadata": json.loads(row["metadata"])
                if isinstance(row["metadata"], str)
                else (row["metadata"] or {}),
                "score": 1.0 - float(row["distance"]),  # 1 = identical, 0 = orthogonal
                "distance": float(row["distance"]),
            }
            for row in rows
        ]

    async def delete_index(self) -> int:
        """Remove every chunk that belongs to this backend's ``index_id``."""
        assert self._pool is not None, "call connect() first"
        total = 0
        async with self._pool.acquire() as conn:
            for dims in list(self._initialised_dims):
                table = self._table_for(dims)
                result = await conn.execute(
                    f"DELETE FROM {table} WHERE index_id = $1;", self._index_id
                )
                # asyncpg returns 'DELETE <n>' as the tag.
                try:
                    total += int(result.rsplit(" ", 1)[-1])
                except (ValueError, IndexError):
                    pass
        return total

    async def count(self) -> int:
        """Total chunk count across every dimension for this ``index_id``."""
        assert self._pool is not None, "call connect() first"
        total = 0
        async with self._pool.acquire() as conn:
            for dims in list(self._initialised_dims):
                table = self._table_for(dims)
                row = await conn.fetchrow(
                    f"SELECT count(*) AS n FROM {table} WHERE index_id = $1;",
                    self._index_id,
                )
                total += int(row["n"]) if row else 0
        return total


def _vector_literal(values: list[float]) -> str:
    """Format a list[float] as a pgvector literal — '[0.1, 0.2, ...]'.

    asyncpg does not register a default codec for the ``vector`` type, so we
    pass it as text and cast with ``::vector`` at the call site. This avoids
    a dependency on the optional `pgvector` Python wrapper for the basic
    upsert + search round-trip.
    """
    return "[" + ",".join(f"{float(v):.7g}" for v in values) + "]"


def create_pgvector_backend(
    config: dict[str, Any],
    index_id: str,
) -> PgvectorRAGBackend:
    """Factory used by :mod:`registry.rag`. Pulls DSN from config or env."""
    dsn = (
        config.get("dsn")
        or config.get("url")
        or os.environ.get("PGVECTOR_DSN")
        or os.environ.get("DATABASE_URL")
    )
    if not dsn:
        raise ValueError(
            "pgvector backend requires a connection string. "
            "Provide it via config.dsn, PGVECTOR_DSN, or DATABASE_URL."
        )
    return PgvectorRAGBackend(
        config=PgvectorConfig(
            dsn=dsn,
            pool_min_size=int(config.get("pool_min_size", 1)),
            pool_max_size=int(config.get("pool_max_size", 10)),
        ),
        index_id=index_id,
    )
