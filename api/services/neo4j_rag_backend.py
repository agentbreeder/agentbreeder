"""Neo4j RAG Backend — Graph-native vector + relationship storage for AgentBreeder.

Implements the same interface as the in-memory RAGStore backend so it can be
selected via ``backend: neo4j`` in ``rag.yaml``.

Public API (mirrors the in-memory store interface):
    Neo4jRAGBackend.index(documents)   — ingest pre-chunked documents with embeddings
    Neo4jRAGBackend.search(query, ...) — vector similarity + optional graph traversal
    Neo4jRAGBackend.close()            — release the driver connection

Requires: neo4j>=5.0  (pip install agentbreeder[rag])
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Only imported at type-check time so tests can mock without the real driver.
    from neo4j import AsyncDriver


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


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

_GRAPH_NEIGHBOR_CYPHER_TMPL = """
MATCH (e:Entity {{index_id: $index_id}})
WHERE e.name IN $seed_names
MATCH path = (e)-[:RELATES*1..{max_hops}]-(neighbor:Entity)
WITH neighbor, length(path) AS depth
ORDER BY depth ASC
MATCH (neighbor)-[:MENTIONED_IN]->(c:Chunk)
RETURN DISTINCT c.id AS chunk_id, c.text AS text, c.source AS source,
       c.metadata AS metadata_json, 1.0 / (depth + 1) AS score
LIMIT $top_k
"""

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

    def __init__(self, config: Neo4jConfig, index_id: str = "default") -> None:
        self._config = config
        self._index_id = index_id
        self._driver: AsyncDriver | None = None

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

    async def close(self) -> None:
        """Close the Neo4j driver and release all connections."""
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
            logger.debug("Neo4jRAGBackend: driver closed (index=%s)", self._index_id)

    # ------------------------------------------------------------------
    # Index (ingest)
    # ------------------------------------------------------------------

    async def index(self, documents: list[dict[str, Any]]) -> int:
        """Ingest pre-chunked documents with embeddings into Neo4j.

        Creates/merges :Chunk nodes (one per document dict), :Entity nodes for
        any extracted entities, and :RELATES edges for extracted relationships.

        Args:
            documents: List of document dicts (see class docstring for shape).

        Returns:
            Number of chunks successfully written.
        """
        driver = self._get_driver()
        written = 0

        async with driver.session(database=self._config.database) as session:
            for doc in documents:
                chunk_id = doc["id"]
                embedding = doc.get("embedding") or []
                metadata = doc.get("metadata") or {}

                # Upsert the chunk node
                await session.run(
                    _UPSERT_CHUNK_CYPHER,
                    id=chunk_id,
                    text=doc.get("text", ""),
                    source=doc.get("source", ""),
                    embedding=embedding,
                    metadata_json=json.dumps(metadata),
                    index_id=self._index_id,
                )
                written += 1

                # Upsert entity nodes and MENTIONED_IN edges
                for entity in doc.get("entities") or []:
                    await session.run(
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
                    await session.run(
                        _UPSERT_RELATION_CYPHER,
                        subject_id=rel["subject_id"],
                        object_id=rel["object_id"],
                        predicate=rel.get("predicate", "RELATES"),
                        weight=float(rel.get("weight", 1.0)),
                        index_id=self._index_id,
                    )

        logger.info(
            "Neo4jRAGBackend.index: wrote %d chunks to index=%s", written, self._index_id
        )
        return written

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        seed_entities: list[str] | None = None,
        max_hops: int = 2,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant chunks via vector similarity + optional graph traversal.

        If ``seed_entities`` is provided (list of entity name strings), the
        search also performs a multi-hop graph traversal starting from those
        entities and merges the resulting chunks into the result set.

        Args:
            query:           Raw query text (used for logging; embedding does the work).
            query_embedding: Pre-computed embedding for the query.
            top_k:           Maximum number of results to return.
            seed_entities:   Optional entity names to seed graph traversal from.
            max_hops:        Maximum BFS traversal depth (only used when seed_entities given).

        Returns:
            List of result dicts sorted by score descending::

                [{"chunk_id": str, "text": str, "source": str,
                  "score": float, "metadata": dict}, ...]
        """
        driver = self._get_driver()
        results: dict[str, dict[str, Any]] = {}

        async with driver.session(database=self._config.database) as session:
            # --- Vector similarity search ---
            vector_result = await session.run(
                _VECTOR_SEARCH_CYPHER,
                index_id=self._index_id,
                query_embedding=query_embedding,
                top_k=top_k,
            )
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
                )
                async for record in graph_result:
                    cid = record["chunk_id"]
                    # Merge: take max score if chunk already present
                    graph_score = float(record["score"])
                    if cid not in results or results[cid]["score"] < graph_score:
                        results[cid] = {
                            "chunk_id": cid,
                            "text": record["text"],
                            "source": record["source"],
                            "score": graph_score,
                            "metadata": self._parse_metadata(
                                record.get("metadata_json") or "{}"
                            ),
                        }

        sorted_results = sorted(results.values(), key=lambda r: r["score"], reverse=True)
        logger.debug(
            "Neo4jRAGBackend.search: query=%r top_k=%d returned=%d (index=%s)",
            query,
            top_k,
            len(sorted_results),
            self._index_id,
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
    neo4j_config = Neo4jConfig.from_dict(config or {})
    return Neo4jRAGBackend(neo4j_config, index_id=index_id)
