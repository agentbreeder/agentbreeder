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
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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


class IngestJobStatus(StrEnum):
    pending = "pending"
    chunking = "chunking"
    extracting_entities = "extracting_entities"
    embedding = "embedding"
    indexing = "indexing"
    completed = "completed"
    failed = "failed"


@dataclass
class DocumentChunk:
    """A single chunk of text with metadata."""

    id: str
    text: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None


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
        # embedding intentionally excluded from wire format; access .embedding directly for similarity
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source": self.source,
            "score": round(self.score, 6),
            "metadata": self.metadata,
        }


@dataclass
class GraphSearchHit(SearchHit):
    """A search result augmented with knowledge graph traversal metadata."""

    graph_path: list[str] = field(default_factory=list)
    nodes_traversed: int = 0
    edges_traversed: int = 0
    seed_entities: list[str] = field(default_factory=list)
    hop_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "graph_path": self.graph_path,
            "nodes_traversed": self.nodes_traversed,
            "edges_traversed": self.edges_traversed,
            "seed_entities": self.seed_entities,
            "hop_depth": self.hop_depth,
        })
        return base


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_fixed_size(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Split text into fixed-size chunks with overlap."""
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
    """Best-effort PDF text extraction without external libraries."""
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
            return "[PDF content — install PyPDF2 for full extraction]"
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


async def embed_texts(
    texts: list[str],
    model: str = "openai/text-embedding-3-small",
    batch_size: int = 32,
) -> list[list[float]]:
    """Generate embeddings for a list of texts.

    Supports:
    - openai/text-embedding-3-small: calls OpenAI API
    - ollama/nomic-embed-text: calls local Ollama instance
    """
    if not texts:
        return []

    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        if model.startswith("openai/"):
            embeddings = await _embed_openai(batch, model.split("/", 1)[1])
        elif model.startswith("ollama/"):
            embeddings = await _embed_ollama(batch, model.split("/", 1)[1])
        else:
            # Fallback: generate deterministic pseudo-embeddings for dev/test
            dims = EMBEDDING_DIMENSIONS.get(model, 768)
            embeddings = [_pseudo_embedding(t, dims) for t in batch]
        all_embeddings.extend(embeddings)

    return all_embeddings


async def _embed_openai(texts: list[str], model_name: str) -> list[list[float]]:
    """Call OpenAI embeddings API."""
    import os

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — using pseudo-embeddings")
        return [_pseudo_embedding(t, 1536) for t in texts]

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"input": texts, "model": model_name},
            )
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
        except Exception as e:
            logger.error("OpenAI embedding failed: %s", e)
            return [_pseudo_embedding(t, 1536) for t in texts]


async def _embed_ollama(texts: list[str], model_name: str) -> list[list[float]]:
    """Call Ollama embeddings API."""
    base_url = "http://localhost:11434"
    results: list[list[float]] = []

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
                logger.error("Ollama embedding failed: %s", e)
                results.append(_pseudo_embedding(text, 768))

    return results


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
    idx: "RAGIndex",
    top_k: int = 10,
    hops: int | None = None,
    rerank: bool = True,
    seed_entity_limit: int = 5,
    vector_weight: float = 0.6,
    graph_weight: float = 0.4,
) -> list[GraphSearchHit]:
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
    from api.services.graph_store import get_graph_store

    # Step 1: Embed query
    query_embeddings = await embed_texts([query], model=idx.embedding_model)
    if not query_embeddings:
        return []
    query_vector = query_embeddings[0]

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
            )
            for h in fallback_hits
        ]

    # Step 3: Identify seed entities
    # Get the top seed_entity_limit candidate chunk IDs
    top_candidate_chunk_ids = {h.chunk_id for h in candidate_hits[:seed_entity_limit]}

    # Build a chunk_id → chunk map for fast lookup
    chunk_map: dict[str, DocumentChunk] = {c.id: c for c in idx.chunks}

    # Find seed nodes: nodes whose chunk_ids overlap with top candidate chunks
    all_nodes, _ = graph_store.list_nodes(index_id, per_page=10000)
    seed_node_ids: list[str] = []
    seed_entity_names: list[str] = []
    for node in all_nodes:
        if any(cid in top_candidate_chunk_ids for cid in node.chunk_ids):
            seed_node_ids.append(node.id)
            seed_entity_names.append(node.entity)

    # Step 4: BFS traversal
    hops_value = hops if hops is not None else idx.max_hops
    neighbor_nodes = graph_store.get_neighbors(index_id, seed_node_ids, hops=hops_value)

    nodes_traversed = len(seed_node_ids) + len(neighbor_nodes)
    all_edges, total_edges = graph_store.list_edges(index_id, per_page=10000)
    edges_traversed = total_edges

    # Step 5: Collect neighbor chunk IDs (with hop depth tracking)
    # chunk_id → minimum hop_depth at which it was reached
    neighbor_chunk_hop: dict[str, int] = {}

    # Seed entity chunks are at hop_depth=0
    for node in all_nodes:
        if node.id in set(seed_node_ids):
            for cid in node.chunk_ids:
                if cid not in neighbor_chunk_hop:
                    neighbor_chunk_hop[cid] = 0

    # Neighbor chunks — we assign hop_depth=1 for simplicity (BFS doesn't track per-node depth)
    # For a more precise implementation, we'd need get_neighbors to return depth info
    for node in neighbor_nodes:
        for cid in node.chunk_ids:
            if cid not in neighbor_chunk_hop:
                neighbor_chunk_hop[cid] = 1

    # Step 6: Merge + deduplicate
    # Start with candidate chunks (hop_depth=0 for seed candidates)
    candidate_chunk_ids = {h.chunk_id for h in candidate_hits}
    # chunk_id → hop_depth for final scoring
    merged_chunk_hop: dict[str, int] = {}

    for h in candidate_hits:
        merged_chunk_hop[h.chunk_id] = 0  # Direct vector match = hop_depth 0

    # Add neighbor chunks not already in candidate set
    for cid, hop_depth in neighbor_chunk_hop.items():
        if cid not in candidate_chunk_ids:
            merged_chunk_hop[cid] = hop_depth

    # Step 7: Score each chunk
    scored: list[tuple[DocumentChunk, float, int]] = []  # (chunk, score, hop_depth)
    for cid, hop_depth in merged_chunk_hop.items():
        chunk = chunk_map.get(cid)
        if chunk is None or chunk.embedding is None:
            continue
        cos_sim = cosine_similarity(query_vector, chunk.embedding)
        hop_decay = 1.0 / (1 + hop_depth)
        final_score = vector_weight * cos_sim + graph_weight * hop_decay
        scored.append((chunk, final_score, hop_depth))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Step 8: Build GraphSearchHit results
    return [
        GraphSearchHit(
            chunk_id=chunk.id,
            text=chunk.text,
            source=chunk.source,
            score=score,
            metadata=chunk.metadata,
            graph_path=seed_entity_names,
            nodes_traversed=nodes_traversed,
            edges_traversed=edges_traversed,
            seed_entities=seed_entity_names,
            hop_depth=hop_depth,
        )
        for chunk, score, hop_depth in scored[:top_k]
    ]


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
        except ValueError:
            raise ValueError(
                f"Invalid index_type '{index_type}'. Must be one of: {[e.value for e in IndexType]}"
            )
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

        try:
            # Phase 1: Chunking
            job.status = IngestJobStatus.chunking
            all_chunks: list[DocumentChunk] = []

            for filename, content in files:
                text = extract_text(filename, content)
                chunks = chunk_text(
                    text,
                    strategy=idx.chunk_strategy,
                    chunk_size=idx.chunk_size,
                    overlap=idx.chunk_overlap,
                )
                for chunk_text_str in chunks:
                    chunk = DocumentChunk(
                        id=str(uuid.uuid4()),
                        text=chunk_text_str,
                        source=filename,
                        metadata={"filename": filename, "index_id": index_id},
                    )
                    all_chunks.append(chunk)
                job.processed_files += 1

            job.total_chunks = len(all_chunks)

            # Phase 2: Embedding
            job.status = IngestJobStatus.embedding
            texts = [c.text for c in all_chunks]
            embeddings = await embed_texts(texts, model=idx.embedding_model)

            for i, emb in enumerate(embeddings):
                all_chunks[i].embedding = emb
                job.embedded_chunks = i + 1

            # Phase 2.5: Entity extraction (graph/hybrid only)
            if idx.index_type in (IndexType.graph, IndexType.hybrid):
                job.status = IngestJobStatus.extracting_entities
                from api.services.graph_extraction import extract_entities_batch
                from api.services.graph_store import get_graph_store
                graph_store = get_graph_store()

                # Extract entities for all chunks
                chunk_texts = [c.text for c in all_chunks]
                extraction_results = await extract_entities_batch(chunk_texts, model=idx.entity_model)

                for chunk, (nodes, edges) in zip(all_chunks, extraction_results):
                    for node in nodes:
                        node.chunk_ids.append(chunk.id)
                        graph_store.upsert_node(index_id, node)
                    for edge in edges:
                        edge.chunk_ids.append(chunk.id)
                        graph_store.upsert_edge(index_id, edge)

                idx.node_count = graph_store.node_count(index_id)
                idx.edge_count = graph_store.edge_count(index_id)
                idx.updated_at = datetime.now(UTC).isoformat()

            # Phase 3: Indexing (add to in-memory store)
            job.status = IngestJobStatus.indexing
            idx.chunks.extend(all_chunks)
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
        rerank: bool = True,
        seed_entity_limit: int = 5,
    ) -> list[SearchHit]:
        """Search an index using hybrid vector + text search (or graph-augmented search)."""
        idx = self._indexes.get(index_id)
        if not idx:
            raise ValueError(f"Index {index_id} not found")

        if not idx.chunks:
            return []

        if idx.index_type in (IndexType.graph, IndexType.hybrid):
            return await graph_search(
                index_id=index_id,
                query=query,
                idx=idx,
                top_k=top_k,
                hops=hops,
                rerank=rerank,
                seed_entity_limit=seed_entity_limit,
            )

        # Existing vector search path:
        query_embeddings = await embed_texts([query], model=idx.embedding_model)
        if not query_embeddings:
            return []

        return hybrid_search(
            query_embedding=query_embeddings[0],
            query_text=query,
            chunks=idx.chunks,
            top_k=top_k,
            vector_weight=vector_weight,
            text_weight=text_weight,
        )


# Global singleton
_store: RAGStore | None = None


def get_rag_store() -> RAGStore:
    """Get the global RAG store singleton."""
    global _store
    if _store is None:
        _store = RAGStore()
    return _store
