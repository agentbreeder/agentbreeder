"""RAG Service — Vector/Graph/Hybrid index management with in-memory store.

Provides:
- CRUD for RAG indexes (vector, graph, hybrid) (in-memory, pgvector-ready schema)
- File ingestion: PDF, TXT, MD, CSV, JSON -> chunk -> embed -> index
- Chunking strategies: fixed-size, recursive text splitter
- Embedding: OpenAI text-embedding-3-small, Ollama nomic-embed-text
- Search: cosine similarity + optional tsvector full-text (hybrid)
- Graph: entity extraction, relationship mapping, multi-hop traversal
- Ingestion progress tracking
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from engine.observability.degraded_mode import warn_once

logger = logging.getLogger(__name__)

# W5-R9: PyPDF2 is an optional dependency. If installed, we use it for
# high-fidelity PDF text extraction. Otherwise we fall back to the
# regex-based BT/ET parser below, which works for simple text-only PDFs but
# will miss content from compressed streams, fonts with custom encodings,
# and scanned pages. To enable, ``pip install PyPDF2``.
try:  # pragma: no cover — import guard
    import PyPDF2  # type: ignore[import-not-found]

    _PYPDF2_AVAILABLE = True
except Exception:  # noqa: BLE001 — any import failure means we fall back.
    PyPDF2 = None  # type: ignore[assignment]
    _PYPDF2_AVAILABLE = False


# ---------------------------------------------------------------------------
# Enums & Data Classes
# ---------------------------------------------------------------------------


class ChunkStrategy(StrEnum):
    fixed_size = "fixed_size"
    recursive = "recursive"


class EmbeddingModel(StrEnum):
    openai_small = "openai/text-embedding-3-small"
    ollama_nomic = "ollama/nomic-embed-text"


class IndexType(StrEnum):
    vector = "vector"
    graph = "graph"
    hybrid = "hybrid"


DEFAULT_ENTITY_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_OLLAMA_ENTITY_MODEL = "ollama/qwen2.5:7b"


class IngestJobStatus(StrEnum):
    pending = "pending"
    chunking = "chunking"
    extracting_entities = "extracting_entities"
    embedding = "embedding"
    indexing = "indexing"
    completed = "completed"
    failed = "failed"


def _normalize_chunk_text(text: str) -> str:
    """Normalize chunk text for content-hash dedup (W4-24).

    Strips leading/trailing whitespace and collapses internal whitespace runs
    so that cosmetic differences (e.g. CRLF vs LF, double spaces) do not
    defeat dedup.
    """
    return re.sub(r"\s+", " ", text).strip()


def compute_chunk_hash(text: str) -> str:
    """SHA256 hex digest of normalized chunk text (W4-24)."""
    normalized = _normalize_chunk_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass
class DocumentChunk:
    """A single chunk of text with metadata."""

    id: str
    text: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    # SHA256 of normalized text — populated at ingestion time so we can skip
    # duplicate chunks when the same file is re-ingested (W4-24). Default
    # ``None`` keeps the field additive / backward-compatible with any
    # existing in-memory chunks created before this field existed.
    chunk_hash: str | None = None


@dataclass
class GraphNode:
    """A knowledge graph node representing a named entity."""

    id: str
    entity: str
    entity_type: str
    description: str
    chunk_ids: list[str]
    embedding: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        # embedding intentionally excluded from wire format;
        # access .embedding directly for similarity
        return {
            "id": self.id,
            "entity": self.entity,
            "entity_type": self.entity_type,
            "description": self.description,
            "chunk_ids": self.chunk_ids,
        }


@dataclass
class GraphEdge:
    """A directed relationship between two graph nodes."""

    id: str
    subject_id: str
    predicate: str
    object_id: str
    chunk_ids: list[str]
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "predicate": self.predicate,
            "object_id": self.object_id,
            "chunk_ids": self.chunk_ids,
            "weight": self.weight,
        }


@dataclass
class RAGIndex:
    """In-memory RAG index supporting vector, graph, and hybrid retrieval."""

    id: str
    name: str
    description: str
    embedding_model: str
    chunk_strategy: str
    chunk_size: int
    chunk_overlap: int
    dimensions: int
    source: str
    doc_count: int = 0
    chunk_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    chunks: list[DocumentChunk] = field(default_factory=list)
    # Graph-specific fields
    index_type: IndexType = IndexType.vector
    entity_model: str = DEFAULT_ENTITY_MODEL
    max_hops: int = 2
    relationship_types: list[str] = field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "embedding_model": self.embedding_model,
            "chunk_strategy": self.chunk_strategy,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "dimensions": self.dimensions,
            "source": self.source,
            "doc_count": self.doc_count,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "index_type": self.index_type.value,
            "entity_model": self.entity_model,
            "max_hops": self.max_hops,
            "relationship_types": self.relationship_types,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
        }


# Backwards-compatible alias
VectorIndex = RAGIndex


@dataclass
class IngestJob:
    """Tracks progress of a file ingestion job."""

    id: str
    index_id: str
    status: IngestJobStatus
    total_files: int = 0
    processed_files: int = 0
    total_chunks: int = 0
    embedded_chunks: int = 0
    error: str | None = None
    started_at: str = ""
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        progress = 0.0
        if self.total_chunks > 0:
            progress = self.embedded_chunks / self.total_chunks * 100
        elif self.total_files > 0:
            progress = self.processed_files / self.total_files * 100
        return {
            "id": self.id,
            "index_id": self.index_id,
            "status": self.status.value,
            "total_files": self.total_files,
            "processed_files": self.processed_files,
            "total_chunks": self.total_chunks,
            "embedded_chunks": self.embedded_chunks,
            "progress_pct": round(progress, 1),
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class SearchHit:
    """A single search result."""

    chunk_id: str
    text: str
    source: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    degraded: bool = False  # True when query embedding fell back to pseudo-embeddings.

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source": self.source,
            "score": round(self.score, 6),
            "metadata": self.metadata,
            "degraded": self.degraded,
        }


@dataclass
class GraphSearchHit(SearchHit):
    """A search result augmented with knowledge graph traversal metadata.

    Fields:
        graph_path:      Reserved for per-result traversal path (future use).
        nodes_traversed: Total nodes touched by the BFS phase of this query.
        edges_traversed: Total edges touched by the BFS phase of this query.
        seed_entities:   Entity names that seeded the BFS for this query.
        hop_depth:       Minimum BFS hop depth at which this chunk was reached
                         (0 = direct vector match / seed entity chunk).
        entity_path:     Per-result entity chain (subject → predicate → object → …)
                         leading to this chunk. Defaults to ``[]``; populated by
                         graph backends that track path provenance.
        vector_score:    Raw vector-similarity component of the blended score.
                         ``None`` when the score is not decomposable.
        graph_score:     Raw graph-traversal component of the blended score.
                         ``None`` when the score is not decomposable.

    G5 — ``entity_path``, ``vector_score``, ``graph_score`` are optional
    backward-compatible additions: callers that ignore them see no change.
    """

    graph_path: list[str] = field(default_factory=list)
    nodes_traversed: int = 0
    edges_traversed: int = 0
    seed_entities: list[str] = field(default_factory=list)
    hop_depth: int = 0
    entity_path: list[str] = field(default_factory=list)
    vector_score: float | None = None
    graph_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "graph_path": self.graph_path,
                "nodes_traversed": self.nodes_traversed,
                "edges_traversed": self.edges_traversed,
                "seed_entities": self.seed_entities,
                "hop_depth": self.hop_depth,
                "entity_path": self.entity_path,
                "vector_score": (
                    None if self.vector_score is None else round(self.vector_score, 6)
                ),
                "graph_score": (None if self.graph_score is None else round(self.graph_score, 6)),
            }
        )
        return base


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_fixed_size(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Split text into fixed-size chunks with overlap.

    W5-R10: ``overlap`` must be strictly less than ``chunk_size`` — otherwise
    the loop can advance by zero (or negative) characters per iteration, which
    would loop forever. We raise ``ValueError`` early instead.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")
    if overlap < 0:
        raise ValueError(f"overlap must be non-negative, got {overlap}")
    if overlap >= chunk_size:
        raise ValueError(
            f"overlap ({overlap}) must be strictly less than chunk_size ({chunk_size})"
        )
    if not text.strip():
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap if overlap > 0 else end
        if start >= len(text):
            break
    return chunks


def _find_split_point(text: str, separators: list[str]) -> tuple[str, int] | None:
    """Find the best separator and its position in the text."""
    for sep in separators:
        idx = text.find(sep)
        if idx != -1:
            return (sep, idx)
    return None


def chunk_recursive(
    text: str,
    chunk_size: int = 512,
    overlap: int = 64,
    separators: list[str] | None = None,
) -> list[str]:
    """Recursively split text using hierarchical separators.

    Tries splitting on paragraph breaks first, then sentences,
    then words, then characters.
    """
    if separators is None:
        separators = ["\n\n", "\n", ". ", " ", ""]

    if not text.strip():
        return []

    if len(text) <= chunk_size:
        return [text.strip()] if text.strip() else []

    # Find the best separator
    best_sep = ""
    for sep in separators:
        if sep and sep in text:
            best_sep = sep
            break

    if not best_sep:
        # Fall back to fixed-size
        return chunk_fixed_size(text, chunk_size, overlap)

    parts = text.split(best_sep)
    chunks: list[str] = []
    current = ""

    for part in parts:
        candidate = f"{current}{best_sep}{part}" if current else part
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current.strip():
                chunks.append(current.strip())
            if len(part) > chunk_size:
                # Recursively split with next separator level
                remaining_seps = separators[separators.index(best_sep) + 1 :]
                sub_chunks = chunk_recursive(part, chunk_size, overlap, remaining_seps)
                chunks.extend(sub_chunks)
                current = ""
            else:
                current = part

    if current.strip():
        chunks.append(current.strip())

    # Apply overlap by prepending tail of previous chunk
    if overlap > 0 and len(chunks) > 1:
        overlapped: list[str] = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:]
            overlapped.append(f"{prev_tail} {chunks[i]}")
        return overlapped

    return chunks


def chunk_text(
    text: str,
    strategy: str = "fixed_size",
    chunk_size: int = 512,
    overlap: int = 64,
) -> list[str]:
    """Chunk text using the specified strategy."""
    if strategy == "recursive":
        return chunk_recursive(text, chunk_size, overlap)
    return chunk_fixed_size(text, chunk_size, overlap)


# ---------------------------------------------------------------------------
# File Parsing
# ---------------------------------------------------------------------------


def extract_text(filename: str, content: bytes) -> str:
    """Extract text from uploaded file content."""
    lower = filename.lower()
    if lower.endswith(".txt") or lower.endswith(".md"):
        return content.decode("utf-8", errors="replace")
    elif lower.endswith(".csv"):
        text = content.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        return "\n".join([", ".join(row) for row in rows])
    elif lower.endswith(".json"):
        try:
            data = json.loads(content.decode("utf-8", errors="replace"))
            return json.dumps(data, indent=2)
        except json.JSONDecodeError:
            return content.decode("utf-8", errors="replace")
    elif lower.endswith(".pdf"):
        # Lightweight PDF text extraction (no external dependencies)
        # Extracts text between BT/ET blocks — works for simple text PDFs
        return _extract_pdf_text(content)
    else:
        # Treat as plain text
        return content.decode("utf-8", errors="replace")


def _extract_pdf_text(content: bytes) -> str:
    """Best-effort PDF text extraction.

    W5-R9: PyPDF2 is now an *optional* dependency. When installed, we use it
    for high-fidelity extraction (handles compressed streams, multi-page PDFs,
    standard fonts). When unavailable — or when PyPDF2 itself raises on a
    malformed/corrupted PDF — we fall back to a regex parser that scans for
    parenthesized strings between BT/ET markers. The fallback is intentionally
    forgiving: it returns a placeholder string ("[PDF content — ...]") rather
    than crashing on malformed input. To enable high-fidelity extraction:
    ``pip install PyPDF2``.
    """
    if _PYPDF2_AVAILABLE:
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(content))
            pages_text: list[str] = []
            for page in reader.pages:
                try:
                    pages_text.append(page.extract_text() or "")
                except Exception:  # noqa: BLE001 — per-page failure is non-fatal.
                    continue
            result = "\n".join(p for p in pages_text if p).strip()
            if result:
                return result
            # fall through to regex fallback when PyPDF2 yields nothing
        except Exception as e:  # noqa: BLE001
            logger.debug("PyPDF2 extraction failed, falling back to regex: %s", e)

    try:
        text = content.decode("latin-1", errors="replace")
        # Extract text between BT (begin text) and ET (end text) markers
        blocks = re.findall(r"BT\s*(.*?)\s*ET", text, re.DOTALL)
        extracted: list[str] = []
        for block in blocks:
            # Extract parenthesized strings (PDF text objects)
            strings = re.findall(r"\((.*?)\)", block)
            extracted.extend(strings)
        result = " ".join(extracted).strip()
        if not result:
            if not _PYPDF2_AVAILABLE:
                return "[PDF content — install PyPDF2 for full extraction]"
            return "[PDF content — could not extract text]"
        return result
    except Exception:
        return "[PDF content — could not extract text]"


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


EMBEDDING_DIMENSIONS: dict[str, int] = {
    "openai/text-embedding-3-small": 1536,
    "ollama/nomic-embed-text": 768,
}


@dataclass
class EmbeddingResult:
    """Embeddings plus provenance — exposes whether fallback was used.

    When ``used_fallback`` is True, ``vectors`` contains deterministic pseudo
    embeddings (hash-based, not semantically meaningful). Search quality will
    be degraded until the upstream embedding service is reachable again.
    """

    vectors: list[list[float]]
    used_fallback: bool = False
    fallback_reason: str | None = None


async def embed_texts(
    texts: list[str],
    model: str = "openai/text-embedding-3-small",
    batch_size: int = 32,
) -> EmbeddingResult:
    """Generate embeddings for a list of texts.

    Returns an EmbeddingResult. If any batch fell back to pseudo-embeddings,
    ``used_fallback=True`` and ``fallback_reason`` captures the first reason
    encountered. WARN log is emitted at most once per (model, reason) per process.
    """
    if not texts:
        return EmbeddingResult(vectors=[])

    all_embeddings: list[list[float]] = []
    used_fallback = False
    first_reason: str | None = None

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        if model.startswith("openai/"):
            vectors, fb, reason = await _embed_openai(batch, model.split("/", 1)[1])
        elif model.startswith("ollama/"):
            vectors, fb, reason = await _embed_ollama(batch, model.split("/", 1)[1])
        else:
            dims = EMBEDDING_DIMENSIONS.get(model, 768)
            vectors = [_pseudo_embedding(t, dims) for t in batch]
            fb, reason = True, "unknown-model-prefix"
            warn_once("rag.embedding", reason, extra={"model": model})

        all_embeddings.extend(vectors)
        if fb and not used_fallback:
            used_fallback = True
            first_reason = reason

    return EmbeddingResult(
        vectors=all_embeddings,
        used_fallback=used_fallback,
        fallback_reason=first_reason,
    )


async def _embed_openai(
    texts: list[str], model_name: str
) -> tuple[list[list[float]], bool, str | None]:
    """Call OpenAI embeddings API. Returns (vectors, used_fallback, reason)."""
    import os

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        warn_once(
            "rag.embedding",
            "openai-no-api-key",
            extra={"model": f"openai/{model_name}"},
        )
        return (
            [_pseudo_embedding(t, 1536) for t in texts],
            True,
            "openai-no-api-key",
        )

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"input": texts, "model": model_name},
            )
            resp.raise_for_status()
            data = resp.json()
            return ([item["embedding"] for item in data["data"]], False, None)
        except Exception as e:
            warn_once(
                "rag.embedding",
                "openai-api-error",
                extra={"model": f"openai/{model_name}"},
            )
            logger.error("OpenAI embedding failed: %s", e)
            return (
                [_pseudo_embedding(t, 1536) for t in texts],
                True,
                "openai-api-error",
            )


async def _embed_ollama(
    texts: list[str], model_name: str
) -> tuple[list[list[float]], bool, str | None]:
    """Call Ollama embeddings API. Returns (vectors, used_fallback, reason)."""
    base_url = "http://localhost:11434"
    results: list[list[float]] = []
    any_fallback = False

    async with httpx.AsyncClient(timeout=120.0) as client:
        for text in texts:
            try:
                resp = await client.post(
                    f"{base_url}/api/embeddings",
                    json={"model": model_name, "prompt": text},
                )
                resp.raise_for_status()
                data = resp.json()
                results.append(data["embedding"])
            except Exception as e:
                warn_once(
                    "rag.embedding",
                    "ollama-unreachable",
                    extra={"model": f"ollama/{model_name}"},
                )
                logger.error("Ollama embedding failed: %s", e)
                results.append(_pseudo_embedding(text, 768))
                any_fallback = True

    reason = "ollama-unreachable" if any_fallback else None
    return (results, any_fallback, reason)


def _pseudo_embedding(text: str, dimensions: int) -> list[float]:
    """Generate a deterministic pseudo-embedding from text hash.

    Used as fallback when embedding APIs are unavailable.
    Maintains consistent output for the same input text.
    """
    h = hashlib.sha256(text.encode()).hexdigest()
    # Use hash bytes to seed values
    values: list[float] = []
    for i in range(dimensions):
        byte_idx = i % 32
        hex_pair = h[byte_idx * 2 : byte_idx * 2 + 2] if byte_idx < 32 else "80"
        val = (int(hex_pair, 16) - 128) / 128.0
        # Add position-dependent variation
        val = val * math.cos(i * 0.1) * 0.5
        values.append(val)
    # Normalize to unit vector
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


# ---------------------------------------------------------------------------
# Similarity Search
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (norm_a * norm_b)


def fulltext_score(query: str, text: str) -> float:
    """Simple term-frequency full-text score (simulating PostgreSQL tsvector)."""
    query_terms = set(query.lower().split())
    text_lower = text.lower()
    if not query_terms:
        return 0.0
    matches = sum(1 for term in query_terms if term in text_lower)
    return matches / len(query_terms)


def hybrid_search(
    query_embedding: list[float],
    query_text: str,
    chunks: list[DocumentChunk],
    top_k: int = 10,
    vector_weight: float = 0.7,
    text_weight: float = 0.3,
) -> list[SearchHit]:
    """Hybrid search combining cosine similarity + full-text scoring."""
    scored: list[tuple[DocumentChunk, float]] = []

    for chunk in chunks:
        if chunk.embedding is None:
            continue
        vec_score = cosine_similarity(query_embedding, chunk.embedding)
        txt_score = fulltext_score(query_text, chunk.text)
        combined = vector_weight * vec_score + text_weight * txt_score
        scored.append((chunk, combined))

    scored.sort(key=lambda x: x[1], reverse=True)

    return [
        SearchHit(
            chunk_id=chunk.id,
            text=chunk.text,
            source=chunk.source,
            score=score,
            metadata=chunk.metadata,
        )
        for chunk, score in scored[:top_k]
    ]


async def graph_search(
    index_id: str,
    query: str,
    idx: RAGIndex,
    top_k: int = 10,
    hops: int | None = None,
    seed_entity_limit: int = 5,
    vector_weight: float = 0.6,
    graph_weight: float = 0.4,
) -> list[SearchHit]:
    """Graph-augmented search: vector search → seed entities → BFS → merge → rerank.

    Steps:
    1. Embed query → query_vector
    2. Vector search (top 20 candidates via hybrid_search)
    3. Identify seed entities: nodes whose chunk_ids overlap with the top candidate chunks
    4. BFS traversal of graph from seed entities (hops = hops or idx.max_hops)
    5. Fetch source chunks for all neighbor nodes
    6. Merge + deduplicate: candidate chunks (step 2) + neighbor chunks (step 5)
    7. Score each chunk: score = vector_weight * cosine_sim + graph_weight * hop_decay(hop_depth)
       where hop_decay(d) = 1.0 / (1 + d)  (seed entity chunks get hop_depth=0)
    8. Return top_k as GraphSearchHit objects
    """
    from api.services.graph_store import get_graph_store  # lazy to avoid circular import

    # Step 1: Embed query
    query_embedding_result = await embed_texts([query], model=idx.embedding_model)
    if not query_embedding_result.vectors:
        return []
    query_vector = query_embedding_result.vectors[0]
    query_degraded = query_embedding_result.used_fallback

    # Step 2: Vector search — get top 20 candidates
    candidate_hits = hybrid_search(
        query_embedding=query_vector,
        query_text=query,
        chunks=idx.chunks,
        top_k=20,
        vector_weight=vector_weight,
        text_weight=1.0 - vector_weight,
    )

    graph_store = get_graph_store()

    # Check if there are any nodes for this index — if not, fall back to regular search
    if graph_store.node_count(index_id) == 0:
        # Fall back to hybrid_search and return as GraphSearchHit with empty graph fields
        fallback_hits = hybrid_search(
            query_embedding=query_vector,
            query_text=query,
            chunks=idx.chunks,
            top_k=top_k,
            vector_weight=vector_weight,
            text_weight=1.0 - vector_weight,
        )
        return [
            GraphSearchHit(
                chunk_id=h.chunk_id,
                text=h.text,
                source=h.source,
                score=h.score,
                metadata=h.metadata,
                degraded=query_degraded,
                # Vector-only fallback: graph traversal contributed nothing.
                vector_score=h.score,
                graph_score=0.0,
                entity_path=[],
            )
            for h in fallback_hits
        ]

    # Step 3: Identify seed entities
    # Get the top seed_entity_limit candidate chunk IDs
    top_candidate_chunk_ids = {h.chunk_id for h in candidate_hits[:seed_entity_limit]}

    # Build a chunk_id → chunk map for fast lookup
    chunk_map: dict[str, DocumentChunk] = {c.id: c for c in idx.chunks}

    # Find seed nodes: nodes whose chunk_ids overlap with top candidate chunks
    all_nodes = graph_store.get_all_nodes(index_id)
    seed_node_ids: list[str] = []
    seed_entity_names: list[str] = []
    for node in all_nodes:
        if any(cid in top_candidate_chunk_ids for cid in node.chunk_ids):
            seed_node_ids.append(node.id)
            seed_entity_names.append(node.entity)

    # Step 4: BFS traversal — returns list[tuple[GraphNode, int]] (node, depth)
    hops_value = hops if hops is not None else idx.max_hops
    neighbor_results = graph_store.get_neighbors(index_id, seed_node_ids, hops=hops_value)

    nodes_traversed = len(seed_node_ids) + len(neighbor_results)
    edges_traversed = len(neighbor_results)  # approximation: one edge per discovered neighbor

    # Step 5: Collect neighbor chunk IDs (with actual BFS hop depth tracking)
    # chunk_id → minimum hop_depth at which it was reached
    chunk_hop_depth: dict[str, int] = {}

    # Seed entity chunks are at hop_depth=0
    for node in all_nodes:
        if node.id in set(seed_node_ids):
            for cid in node.chunk_ids:
                if cid not in chunk_hop_depth:
                    chunk_hop_depth[cid] = 0

    # Neighbor chunks — use actual BFS depth from get_neighbors
    for neighbor_node, depth in neighbor_results:
        for cid in neighbor_node.chunk_ids:
            if cid not in chunk_hop_depth:
                chunk_hop_depth[cid] = depth  # actual BFS depth, not hardcoded 1

    # Step 6: Merge + deduplicate
    # Start with candidate chunks (hop_depth=0 for seed candidates)
    candidate_chunk_ids = {h.chunk_id for h in candidate_hits}
    # chunk_id → hop_depth for final scoring
    merged_chunk_hop: dict[str, int] = {}

    for h in candidate_hits:
        merged_chunk_hop[h.chunk_id] = 0  # Direct vector match = hop_depth 0

    # Add neighbor chunks not already in candidate set
    for cid, hop_depth in chunk_hop_depth.items():
        if cid not in candidate_chunk_ids:
            merged_chunk_hop[cid] = hop_depth

    # Step 7: Score each chunk
    # (chunk, final_score, hop_depth, vec_component, graph_component)
    scored: list[tuple[DocumentChunk, float, int, float, float]] = []
    for cid, hop_depth in merged_chunk_hop.items():
        chunk = chunk_map.get(cid)
        if chunk is None or chunk.embedding is None:
            continue
        cos_sim = cosine_similarity(query_vector, chunk.embedding)
        hop_decay = 1.0 / (1 + hop_depth)
        vec_component = vector_weight * cos_sim
        graph_component = graph_weight * hop_decay
        final_score = vec_component + graph_component
        scored.append((chunk, final_score, hop_depth, vec_component, graph_component))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Step 8: Build GraphSearchHit results
    # graph_path is reserved for per-result traversal path (future)
    return [
        GraphSearchHit(
            chunk_id=chunk.id,
            text=chunk.text,
            source=chunk.source,
            score=score,
            metadata=chunk.metadata,
            degraded=query_degraded,
            graph_path=[],
            nodes_traversed=nodes_traversed,
            edges_traversed=edges_traversed,
            seed_entities=list(seed_entity_names),
            hop_depth=hop_depth,
            entity_path=[],
            vector_score=vec_component,
            graph_score=graph_component,
        )
        for chunk, score, hop_depth, vec_component, graph_component in scored[:top_k]
    ]


# ---------------------------------------------------------------------------
# Metadata filter helper (W5-R12)
# ---------------------------------------------------------------------------


def _matches_filters(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Return True iff every (k, v) in ``filters`` matches ``metadata`` exactly.

    W5-R12: simple post-retrieval predicate — a chunk is kept when, for every
    filter key, the chunk's metadata contains that key with an equal value.
    Missing keys, or unequal values, exclude the chunk. Empty ``filters`` is
    treated as "no filter" by callers (this function returns True for an
    empty dict).
    """
    for key, expected in filters.items():
        if metadata.get(key) != expected:
            return False
    return True


# ---------------------------------------------------------------------------
# In-Memory Store
# ---------------------------------------------------------------------------


class RAGStore:
    """In-memory store for vector indexes and ingestion jobs.

    This will be replaced by pgvector + PostgreSQL when the real DB is connected.
    """

    def __init__(self) -> None:
        self._indexes: dict[str, RAGIndex] = {}
        self._jobs: dict[str, IngestJob] = {}

    # --- Index CRUD ---

    def create_index(
        self,
        name: str,
        description: str = "",
        embedding_model: str = "openai/text-embedding-3-small",
        chunk_strategy: str = "fixed_size",
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        source: str = "manual",
        index_type: str = "vector",
        entity_model: str = DEFAULT_ENTITY_MODEL,
        max_hops: int = 2,
        relationship_types: list[str] | None = None,
    ) -> RAGIndex:
        try:
            idx_type = IndexType(index_type)
        except ValueError as exc:
            raise ValueError(
                f"Invalid index_type '{index_type}'."
                f" Must be one of: {[e.value for e in IndexType]}"
            ) from exc
        index_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        dimensions = EMBEDDING_DIMENSIONS.get(embedding_model, 768)
        idx = RAGIndex(
            id=index_id,
            name=name,
            description=description,
            embedding_model=embedding_model,
            chunk_strategy=chunk_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            dimensions=dimensions,
            source=source,
            created_at=now,
            updated_at=now,
            index_type=idx_type,
            entity_model=entity_model,
            max_hops=max_hops,
            relationship_types=relationship_types if relationship_types is not None else [],
        )
        self._indexes[index_id] = idx
        return idx

    def get_index(self, index_id: str) -> RAGIndex | None:
        return self._indexes.get(index_id)

    def list_indexes(self, page: int = 1, per_page: int = 20) -> tuple[list[RAGIndex], int]:
        all_indexes = sorted(self._indexes.values(), key=lambda x: x.created_at, reverse=True)
        total = len(all_indexes)
        start = (page - 1) * per_page
        end = start + per_page
        return all_indexes[start:end], total

    def delete_index(self, index_id: str) -> bool:
        if index_id in self._indexes:
            idx = self._indexes[index_id]
            del self._indexes[index_id]
            # Also delete related jobs
            self._jobs = {k: v for k, v in self._jobs.items() if v.index_id != index_id}
            # Clean up graph store for graph/hybrid indexes
            if idx.index_type in (IndexType.graph, IndexType.hybrid):
                from api.services.graph_store import get_graph_store

                get_graph_store().delete_subgraph(index_id)
            return True
        return False

    # --- Ingestion ---

    def create_ingest_job(self, index_id: str, total_files: int) -> IngestJob:
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        job = IngestJob(
            id=job_id,
            index_id=index_id,
            status=IngestJobStatus.pending,
            total_files=total_files,
            started_at=now,
        )
        self._jobs[job_id] = job
        return job

    def get_ingest_job(self, job_id: str) -> IngestJob | None:
        return self._jobs.get(job_id)

    async def ingest_files(
        self,
        index_id: str,
        files: list[tuple[str, bytes]],
    ) -> IngestJob:
        """Ingest files into a vector index.

        Args:
            index_id: Target index ID.
            files: List of (filename, content_bytes) tuples.

        Returns:
            IngestJob with final status.
        """
        idx = self._indexes.get(index_id)
        if not idx:
            raise ValueError(f"Index {index_id} not found")

        job = self.create_ingest_job(index_id, len(files))
        # W5-R11: track wall-clock duration for observability.
        _ingest_start = time.monotonic()

        try:
            # Phase 1: Chunking + content-hash dedup (W4-24).
            #
            # We compute SHA256 of the normalized chunk text and skip any
            # chunk whose hash already lives in this index. This makes
            # re-ingesting the same file idempotent.
            job.status = IngestJobStatus.chunking
            existing_hashes: set[str] = {
                c.chunk_hash for c in idx.chunks if c.chunk_hash is not None
            }
            new_chunks: list[DocumentChunk] = []
            duplicates_skipped = 0

            for filename, content in files:
                text = extract_text(filename, content)
                chunks = chunk_text(
                    text,
                    strategy=idx.chunk_strategy,
                    chunk_size=idx.chunk_size,
                    overlap=idx.chunk_overlap,
                )
                for chunk_text_str in chunks:
                    h = compute_chunk_hash(chunk_text_str)
                    if h in existing_hashes:
                        duplicates_skipped += 1
                        continue
                    existing_hashes.add(h)
                    chunk = DocumentChunk(
                        id=str(uuid.uuid4()),
                        text=chunk_text_str,
                        source=filename,
                        metadata={"filename": filename, "index_id": index_id},
                        chunk_hash=h,
                    )
                    new_chunks.append(chunk)
                job.processed_files += 1

            if duplicates_skipped:
                logger.info(
                    "Ingest %s for index %s skipped %d duplicate chunks (content-hash dedup).",
                    job.id,
                    index_id,
                    duplicates_skipped,
                )

            job.total_chunks = len(new_chunks)

            # Phase 2: Embedding (only the de-duplicated chunks).
            job.status = IngestJobStatus.embedding
            texts = [c.text for c in new_chunks]
            embedding_result = await embed_texts(texts, model=idx.embedding_model)
            if embedding_result.used_fallback:
                logger.warning(
                    "Ingest %s for index %s used pseudo-embedding fallback "
                    "(reason=%s) — search quality will be degraded.",
                    job.id,
                    index_id,
                    embedding_result.fallback_reason,
                )

            for i, emb in enumerate(embedding_result.vectors):
                new_chunks[i].embedding = emb
                job.embedded_chunks = i + 1

            # Phase 2.5: Entity extraction (graph/hybrid only).
            #
            # W4-25 — atomic graph rollback. We extract entities for ALL
            # chunks BEFORE touching either the index or the graph store.
            # If extraction raises, we abort the whole job and the index is
            # left untouched (no partially-indexed chunks without graph
            # nodes). On success we write the chunks and graph nodes
            # together below.
            pending_graph_writes: list[tuple[DocumentChunk, list[Any], list[Any]]] = []
            if idx.index_type in (IndexType.graph, IndexType.hybrid) and new_chunks:
                job.status = IngestJobStatus.extracting_entities
                from api.services.graph_extraction import extract_entities_batch
                from api.services.graph_store import (
                    get_graph_store,  # lazy to avoid circular import
                )

                graph_store = get_graph_store()
                chunk_texts = [c.text for c in new_chunks]
                # If this raises, the except below marks the job failed and
                # we exit WITHOUT mutating ``idx.chunks`` or graph_store.
                extraction_results = await extract_entities_batch(
                    chunk_texts, model=idx.entity_model
                )
                for chunk, (nodes, edges) in zip(new_chunks, extraction_results, strict=True):
                    pending_graph_writes.append((chunk, nodes, edges))

            # Phase 3: Atomic indexing — write chunks + graph nodes together.
            job.status = IngestJobStatus.indexing
            if pending_graph_writes:
                from api.services.graph_store import get_graph_store

                graph_store = get_graph_store()
                for chunk, nodes, edges in pending_graph_writes:
                    for node in nodes:
                        node.chunk_ids.append(chunk.id)
                        graph_store.upsert_node(index_id, node)
                    for edge in edges:
                        edge.chunk_ids.append(chunk.id)
                        graph_store.upsert_edge(index_id, edge)
                idx.node_count = graph_store.node_count(index_id)
                idx.edge_count = graph_store.edge_count(index_id)

            idx.chunks.extend(new_chunks)
            idx.chunk_count = len(idx.chunks)
            idx.doc_count += len(files)
            idx.updated_at = datetime.now(UTC).isoformat()

            job.status = IngestJobStatus.completed
            job.completed_at = datetime.now(UTC).isoformat()

        except Exception as e:
            job.status = IngestJobStatus.failed
            job.error = str(e)
            job.completed_at = datetime.now(UTC).isoformat()
            logger.error("Ingestion failed for index %s: %s", index_id, e)

        # W5-R11: structured metrics emit on ingestion completion (success or failure).
        _ingest_duration_ms = int((time.monotonic() - _ingest_start) * 1000)
        logger.info(
            "rag.ingest.complete",
            extra={
                "job_id": job.id,
                "index_id": index_id,
                "status": job.status.value,
                "files": len(files),
                "chunks": job.total_chunks,
                "embedded_chunks": job.embedded_chunks,
                "duration_ms": _ingest_duration_ms,
            },
        )

        return job

    # --- Search ---

    async def search(
        self,
        index_id: str,
        query: str,
        top_k: int = 10,
        vector_weight: float = 0.7,
        text_weight: float = 0.3,
        # Graph search params (ignored for vector indexes):
        hops: int | None = None,
        seed_entity_limit: int = 5,
        # W5-R12: optional metadata post-filter (key → required value).
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Search an index using hybrid vector + text search (or graph-augmented search).

        W5-R12: ``filters`` is an optional dict of metadata key → required value.
        Applied as a *post-retrieval* predicate against ``chunk.metadata``: a hit
        is kept iff every (k, v) in ``filters`` matches the chunk's metadata
        (k must be present and equal v). The filter is applied *after* the
        underlying retrieval and *before* truncation to ``top_k`` so we still
        return the strongest matching ``top_k`` hits. This is intentionally
        simple — push-down into the underlying store is a future optimization.
        """
        _search_start = time.monotonic()
        idx = self._indexes.get(index_id)
        if not idx:
            raise ValueError(f"Index {index_id} not found")

        if not idx.chunks:
            logger.info(
                "rag.search.complete",
                extra={
                    "index_id": index_id,
                    "top_k": top_k,
                    "result_count": 0,
                    "duration_ms": int((time.monotonic() - _search_start) * 1000),
                    "degraded": False,
                    "filters": filters or {},
                },
            )
            return []

        # Over-fetch when a filter is applied so we still have ``top_k`` results
        # after post-filtering. Cheap heuristic: 4x for filtered, else top_k.
        effective_k = max(top_k * 4, top_k + 20) if filters else top_k

        if idx.index_type in (IndexType.graph, IndexType.hybrid):
            hits = await graph_search(
                index_id=index_id,
                query=query,
                idx=idx,
                top_k=effective_k,
                hops=hops,
                seed_entity_limit=seed_entity_limit,
                vector_weight=vector_weight,
            )
        else:
            # Existing vector search path:
            query_embedding_result = await embed_texts([query], model=idx.embedding_model)
            if not query_embedding_result.vectors:
                logger.info(
                    "rag.search.complete",
                    extra={
                        "index_id": index_id,
                        "top_k": top_k,
                        "result_count": 0,
                        "duration_ms": int((time.monotonic() - _search_start) * 1000),
                        "degraded": False,
                        "filters": filters or {},
                    },
                )
                return []

            hits = hybrid_search(
                query_embedding=query_embedding_result.vectors[0],
                query_text=query,
                chunks=idx.chunks,
                top_k=effective_k,
                vector_weight=vector_weight,
                text_weight=text_weight,
            )

            # Propagate fallback flag onto each hit so the API layer can surface
            # search-quality degradation to the caller.
            if query_embedding_result.used_fallback:
                for h in hits:
                    h.degraded = True

        # W5-R12: apply metadata post-filter, then truncate to top_k.
        if filters:
            hits = [h for h in hits if _matches_filters(h.metadata, filters)]
        hits = hits[:top_k]

        # W5-R11: structured metrics emit on search completion.
        logger.info(
            "rag.search.complete",
            extra={
                "index_id": index_id,
                "top_k": top_k,
                "result_count": len(hits),
                "duration_ms": int((time.monotonic() - _search_start) * 1000),
                "degraded": any(h.degraded for h in hits),
                "filters": filters or {},
            },
        )

        return hits


# Global singleton
_store: RAGStore | None = None


def get_rag_store() -> RAGStore:
    """Get the global RAG store singleton."""
    global _store
    if _store is None:
        _store = RAGStore()
    return _store
