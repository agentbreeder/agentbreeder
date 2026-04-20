"""Tests for the RAG service — chunking, embedding, search, hybrid search, CRUD."""

from __future__ import annotations

import math

import pytest

from api.services.graph_store import GraphStore
from api.services.rag_service import (
    DEFAULT_ENTITY_MODEL,
    DocumentChunk,
    GraphEdge,
    GraphNode,
    GraphSearchHit,
    IndexType,
    RAGIndex,
    RAGStore,
    VectorIndex,
    _pseudo_embedding,
    chunk_fixed_size,
    chunk_recursive,
    chunk_text,
    cosine_similarity,
    extract_text,
    fulltext_score,
    hybrid_search,
)

# ---------------------------------------------------------------------------
# Chunking Tests
# ---------------------------------------------------------------------------


class TestFixedSizeChunking:
    def test_basic_chunking(self):
        text = "a" * 100
        chunks = chunk_fixed_size(text, chunk_size=30, overlap=0)
        assert len(chunks) == 4
        assert all(len(c) <= 30 for c in chunks)

    def test_overlap(self):
        text = "abcdefghij" * 10  # 100 chars
        chunks = chunk_fixed_size(text, chunk_size=30, overlap=10)
        assert len(chunks) > 3
        # Each chunk after the first should share some content with previous
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-10:]
            assert chunks[i][:10] == prev_tail

    def test_empty_text(self):
        assert chunk_fixed_size("", chunk_size=100, overlap=0) == []
        assert chunk_fixed_size("   ", chunk_size=100, overlap=0) == []

    def test_text_shorter_than_chunk_size(self):
        chunks = chunk_fixed_size("hello world", chunk_size=100, overlap=0)
        assert len(chunks) == 1
        assert chunks[0] == "hello world"

    def test_exact_chunk_size(self):
        text = "x" * 50
        chunks = chunk_fixed_size(text, chunk_size=50, overlap=0)
        assert len(chunks) == 1


class TestRecursiveChunking:
    def test_paragraph_splitting(self):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = chunk_recursive(text, chunk_size=100, overlap=0)
        assert len(chunks) >= 1
        # All content should be preserved
        combined = " ".join(chunks)
        assert "First paragraph" in combined
        assert "Third paragraph" in combined

    def test_sentence_splitting(self):
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        chunks = chunk_recursive(text, chunk_size=40, overlap=0)
        assert len(chunks) >= 2

    def test_falls_back_to_fixed_size(self):
        text = "a" * 200  # No natural split points
        chunks = chunk_recursive(text, chunk_size=50, overlap=0)
        assert len(chunks) >= 4

    def test_empty_text(self):
        assert chunk_recursive("") == []

    def test_short_text(self):
        chunks = chunk_recursive("short", chunk_size=100)
        assert chunks == ["short"]


class TestChunkText:
    def test_fixed_strategy(self):
        text = "a" * 100
        chunks = chunk_text(text, strategy="fixed_size", chunk_size=30, overlap=0)
        assert len(chunks) >= 3

    def test_recursive_strategy(self):
        text = "Para one.\n\nPara two.\n\nPara three."
        chunks = chunk_text(text, strategy="recursive", chunk_size=100, overlap=0)
        assert len(chunks) >= 1


# ---------------------------------------------------------------------------
# File Extraction Tests
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_txt_file(self):
        content = b"Hello, world!"
        result = extract_text("test.txt", content)
        assert result == "Hello, world!"

    def test_md_file(self):
        content = b"# Title\n\nSome markdown content."
        result = extract_text("readme.md", content)
        assert "# Title" in result

    def test_csv_file(self):
        content = b"name,age\nAlice,30\nBob,25"
        result = extract_text("data.csv", content)
        assert "Alice" in result
        assert "Bob" in result

    def test_json_file(self):
        content = b'{"key": "value", "num": 42}'
        result = extract_text("data.json", content)
        assert "key" in result
        assert "value" in result

    def test_unknown_extension(self):
        content = b"some content"
        result = extract_text("file.xyz", content)
        assert result == "some content"


# ---------------------------------------------------------------------------
# Embedding Tests
# ---------------------------------------------------------------------------


class TestPseudoEmbedding:
    def test_correct_dimensions(self):
        emb = _pseudo_embedding("test", 768)
        assert len(emb) == 768

    def test_deterministic(self):
        emb1 = _pseudo_embedding("same text", 100)
        emb2 = _pseudo_embedding("same text", 100)
        assert emb1 == emb2

    def test_different_texts(self):
        emb1 = _pseudo_embedding("text one", 100)
        emb2 = _pseudo_embedding("text two", 100)
        assert emb1 != emb2

    def test_unit_vector(self):
        emb = _pseudo_embedding("normalize me", 256)
        norm = math.sqrt(sum(v * v for v in emb))
        assert abs(norm - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Similarity & Search Tests
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(cosine_similarity(a, b)) < 1e-6

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-6


class TestFulltextScore:
    def test_exact_match(self):
        score = fulltext_score("hello world", "hello world")
        assert score == 1.0

    def test_partial_match(self):
        score = fulltext_score("hello world", "hello there")
        assert 0 < score < 1

    def test_no_match(self):
        score = fulltext_score("hello", "goodbye")
        assert score == 0.0

    def test_empty_query(self):
        score = fulltext_score("", "some text")
        assert score == 0.0


class TestHybridSearch:
    def _make_chunks(self) -> list[DocumentChunk]:
        """Create test chunks with pseudo-embeddings."""
        texts = [
            "Python is a programming language",
            "Machine learning uses algorithms",
            "Python machine learning with scikit-learn",
            "JavaScript is used for web development",
        ]
        chunks = []
        for i, text in enumerate(texts):
            chunks.append(
                DocumentChunk(
                    id=f"chunk-{i}",
                    text=text,
                    source=f"doc-{i}.txt",
                    embedding=_pseudo_embedding(text, 100),
                )
            )
        return chunks

    def test_returns_results(self):
        chunks = self._make_chunks()
        query_emb = _pseudo_embedding("Python programming", 100)
        results = hybrid_search(query_emb, "Python programming", chunks, top_k=3)
        assert len(results) <= 3
        assert all(hasattr(r, "score") for r in results)

    def test_scores_sorted_descending(self):
        chunks = self._make_chunks()
        query_emb = _pseudo_embedding("machine learning", 100)
        results = hybrid_search(query_emb, "machine learning", chunks, top_k=4)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_top_k_limits_results(self):
        chunks = self._make_chunks()
        query_emb = _pseudo_embedding("test", 100)
        results = hybrid_search(query_emb, "test", chunks, top_k=2)
        assert len(results) <= 2

    def test_text_weight_affects_results(self):
        chunks = self._make_chunks()
        query_emb = _pseudo_embedding("Python", 100)
        # Full text weight
        text_results = hybrid_search(
            query_emb, "Python", chunks, vector_weight=0.0, text_weight=1.0
        )
        # Full vector weight
        vec_results = hybrid_search(
            query_emb, "Python", chunks, vector_weight=1.0, text_weight=0.0
        )
        # At least some results should be present
        assert len(text_results) > 0
        assert len(vec_results) > 0


# ---------------------------------------------------------------------------
# RAGStore CRUD Tests
# ---------------------------------------------------------------------------


class TestRAGStore:
    def setup_method(self):
        self.store = RAGStore()

    def test_create_index(self):
        idx = self.store.create_index(name="test-index", description="Test")
        assert idx.name == "test-index"
        assert idx.description == "Test"
        assert idx.dimensions == 1536  # default openai model
        assert idx.doc_count == 0
        assert idx.chunk_count == 0

    def test_create_index_ollama(self):
        idx = self.store.create_index(
            name="ollama-index",
            embedding_model="ollama/nomic-embed-text",
        )
        assert idx.dimensions == 768

    def test_get_index(self):
        idx = self.store.create_index(name="findme")
        found = self.store.get_index(idx.id)
        assert found is not None
        assert found.name == "findme"

    def test_get_index_not_found(self):
        assert self.store.get_index("nonexistent") is None

    def test_list_indexes(self):
        self.store.create_index(name="idx-1")
        self.store.create_index(name="idx-2")
        self.store.create_index(name="idx-3")
        indexes, total = self.store.list_indexes()
        assert total == 3
        assert len(indexes) == 3

    def test_list_indexes_pagination(self):
        for i in range(5):
            self.store.create_index(name=f"idx-{i}")
        page1, total = self.store.list_indexes(page=1, per_page=2)
        assert total == 5
        assert len(page1) == 2
        page3, _ = self.store.list_indexes(page=3, per_page=2)
        assert len(page3) == 1

    def test_delete_index(self):
        idx = self.store.create_index(name="deleteme")
        assert self.store.delete_index(idx.id) is True
        assert self.store.get_index(idx.id) is None

    def test_delete_index_not_found(self):
        assert self.store.delete_index("nonexistent") is False

    def test_to_dict(self):
        idx = self.store.create_index(name="dict-test", description="A test")
        d = idx.to_dict()
        assert d["name"] == "dict-test"
        assert d["description"] == "A test"
        assert "chunks" not in d  # chunks should not be in dict


# ---------------------------------------------------------------------------
# RAGStore Ingestion Tests
# ---------------------------------------------------------------------------


class TestRAGStoreIngestion:
    def setup_method(self):
        self.store = RAGStore()

    @pytest.mark.asyncio
    async def test_ingest_txt_file(self):
        idx = self.store.create_index(name="ingest-test", chunk_size=50, chunk_overlap=0)
        content = ("Hello world. " * 20).encode()
        job = await self.store.ingest_files(idx.id, [("test.txt", content)])
        assert job.status.value == "completed"
        assert job.total_files == 1
        assert job.processed_files == 1
        assert job.total_chunks > 0
        assert idx.doc_count == 1
        assert idx.chunk_count > 0

    @pytest.mark.asyncio
    async def test_ingest_multiple_files(self):
        idx = self.store.create_index(name="multi-ingest")
        files = [
            ("doc1.txt", b"First document content"),
            ("doc2.txt", b"Second document content"),
        ]
        job = await self.store.ingest_files(idx.id, files)
        assert job.status.value == "completed"
        assert job.total_files == 2
        assert idx.doc_count == 2

    @pytest.mark.asyncio
    async def test_ingest_invalid_index(self):
        with pytest.raises(ValueError, match="not found"):
            await self.store.ingest_files("nonexistent", [("test.txt", b"data")])

    @pytest.mark.asyncio
    async def test_ingest_job_progress(self):
        idx = self.store.create_index(name="progress-test")
        job = await self.store.ingest_files(idx.id, [("test.txt", b"Some content")])
        d = job.to_dict()
        assert "progress_pct" in d
        assert d["progress_pct"] == 100.0


# ---------------------------------------------------------------------------
# RAGStore Search Tests
# ---------------------------------------------------------------------------


class TestRAGStoreSearch:
    def setup_method(self):
        self.store = RAGStore()

    @pytest.mark.asyncio
    async def test_search_empty_index(self):
        idx = self.store.create_index(name="empty-search")
        results = await self.store.search(idx.id, "test query")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_after_ingest(self):
        idx = self.store.create_index(name="search-test", chunk_size=100, chunk_overlap=0)
        content = b"Python is a great programming language for data science and machine learning."
        await self.store.ingest_files(idx.id, [("doc.txt", content)])
        results = await self.store.search(idx.id, "Python programming")
        assert len(results) > 0
        assert results[0].text  # has text
        assert results[0].source == "doc.txt"

    @pytest.mark.asyncio
    async def test_search_invalid_index(self):
        with pytest.raises(ValueError, match="not found"):
            await self.store.search("nonexistent", "query")

    @pytest.mark.asyncio
    async def test_search_top_k(self):
        idx = self.store.create_index(name="topk-test", chunk_size=20, chunk_overlap=0)
        content = ("word " * 200).encode()
        await self.store.ingest_files(idx.id, [("doc.txt", content)])
        results = await self.store.search(idx.id, "word", top_k=3)
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_search_result_has_score(self):
        idx = self.store.create_index(name="score-test", chunk_size=100, chunk_overlap=0)
        await self.store.ingest_files(idx.id, [("doc.txt", b"Testing search scores")])
        results = await self.store.search(idx.id, "search")
        if results:
            assert isinstance(results[0].score, float)
            d = results[0].to_dict()
            assert "score" in d
            assert "text" in d


# ---------------------------------------------------------------------------
# GraphNode Tests
# ---------------------------------------------------------------------------


class TestGraphNode:
    def _make_node(self) -> GraphNode:
        return GraphNode(
            id="node-1",
            entity="Python",
            entity_type="LANGUAGE",
            description="A programming language",
            chunk_ids=["chunk-1", "chunk-2"],
            embedding=[0.1, 0.2, 0.3],
        )

    def test_to_dict_excludes_embedding(self):
        node = self._make_node()
        d = node.to_dict()
        assert "embedding" not in d

    def test_to_dict_includes_all_other_fields(self):
        node = self._make_node()
        d = node.to_dict()
        assert d["id"] == "node-1"
        assert d["entity"] == "Python"
        assert d["entity_type"] == "LANGUAGE"
        assert d["description"] == "A programming language"
        assert d["chunk_ids"] == ["chunk-1", "chunk-2"]

    def test_embedding_accessible_directly(self):
        node = self._make_node()
        assert node.embedding == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# GraphEdge Tests
# ---------------------------------------------------------------------------


class TestGraphEdge:
    def _make_edge(self) -> GraphEdge:
        return GraphEdge(
            id="edge-1",
            subject_id="node-1",
            predicate="USES",
            object_id="node-2",
            chunk_ids=["chunk-3"],
            weight=0.9,
        )

    def test_to_dict_includes_all_fields(self):
        edge = self._make_edge()
        d = edge.to_dict()
        assert d["id"] == "edge-1"
        assert d["subject_id"] == "node-1"
        assert d["predicate"] == "USES"
        assert d["object_id"] == "node-2"
        assert d["chunk_ids"] == ["chunk-3"]
        assert d["weight"] == 0.9


# ---------------------------------------------------------------------------
# RAGIndex (dataclass) Tests
# ---------------------------------------------------------------------------


class TestRAGIndex:
    def _make_index(self) -> RAGIndex:
        return RAGIndex(
            id="idx-1",
            name="test",
            description="desc",
            embedding_model="openai/text-embedding-3-small",
            chunk_strategy="fixed_size",
            chunk_size=512,
            chunk_overlap=64,
            dimensions=1536,
            source="manual",
            index_type=IndexType.graph,
            entity_model=DEFAULT_ENTITY_MODEL,
            max_hops=3,
            relationship_types=["USES", "IS_A"],
            node_count=5,
            edge_count=7,
        )

    def test_to_dict_includes_graph_fields(self):
        idx = self._make_index()
        d = idx.to_dict()
        assert d["index_type"] == "graph"
        assert d["entity_model"] == DEFAULT_ENTITY_MODEL
        assert d["max_hops"] == 3
        assert d["relationship_types"] == ["USES", "IS_A"]
        assert d["node_count"] == 5
        assert d["edge_count"] == 7

    def test_to_dict_excludes_chunks(self):
        idx = self._make_index()
        d = idx.to_dict()
        assert "chunks" not in d

    def test_vector_index_is_rag_index_alias(self):
        assert VectorIndex is RAGIndex


# ---------------------------------------------------------------------------
# IndexType Tests
# ---------------------------------------------------------------------------


class TestIndexType:
    def test_vector_value(self):
        assert IndexType("vector") == IndexType.vector

    def test_graph_value(self):
        assert IndexType("graph") == IndexType.graph

    def test_hybrid_value(self):
        assert IndexType("hybrid") == IndexType.hybrid

    def test_invalid_raises_value_error(self):
        with pytest.raises(ValueError):
            IndexType("bad")


# ---------------------------------------------------------------------------
# create_index Validation Tests
# ---------------------------------------------------------------------------


class TestCreateIndexValidation:
    def setup_method(self):
        self.store = RAGStore()

    def test_invalid_index_type_raises_descriptive_error(self):
        with pytest.raises(ValueError, match="Invalid index_type 'bad'"):
            self.store.create_index(name="x", index_type="bad")

    def test_error_message_lists_valid_values(self):
        with pytest.raises(ValueError, match="vector"):
            self.store.create_index(name="x", index_type="bad")

    def test_valid_graph_index_type(self):
        idx = self.store.create_index(name="g", index_type="graph")
        assert idx.index_type == IndexType.graph

    def test_valid_hybrid_index_type(self):
        idx = self.store.create_index(name="h", index_type="hybrid")
        assert idx.index_type == IndexType.hybrid


# ---------------------------------------------------------------------------
# Graph search and ingest integration tests
# ---------------------------------------------------------------------------


class TestGraphSearchAndIngest:
    def setup_method(self):
        self.store = RAGStore()

    @pytest.mark.asyncio
    async def test_graph_search_fallback_no_nodes(self):
        """Graph index with no graph data falls back to vector search without raising."""
        idx = self.store.create_index(
            name="graph-fallback",
            index_type="graph",
            chunk_size=100,
            chunk_overlap=0,
        )
        content = b"Python is a high-level programming language."
        await self.store.ingest_files(idx.id, [("doc.txt", content)])
        # No graph data was extracted (no API key) — should fall back to vector results
        results = await self.store.search(idx.id, "Python language")
        # Should not raise and should return results (fallback path)
        assert isinstance(results, list)

    def test_graph_search_hit_to_dict(self):
        """GraphSearchHit.to_dict() includes all fields from both parent and child."""
        hit = GraphSearchHit(
            chunk_id="c1",
            text="hello world",
            source="doc.txt",
            score=0.85,
            metadata={"key": "val"},
            graph_path=[],
            nodes_traversed=3,
            edges_traversed=2,
            seed_entities=["Python", "ML"],
            hop_depth=1,
        )
        d = hit.to_dict()
        # Parent fields
        assert d["chunk_id"] == "c1"
        assert d["text"] == "hello world"
        assert d["source"] == "doc.txt"
        assert abs(d["score"] - 0.85) < 1e-4
        assert d["metadata"] == {"key": "val"}
        # Child fields
        assert d["graph_path"] == []
        assert d["nodes_traversed"] == 3
        assert d["edges_traversed"] == 2
        assert d["seed_entities"] == ["Python", "ML"]
        assert d["hop_depth"] == 1

    @pytest.mark.asyncio
    async def test_ingest_graph_index_sets_status(self):
        """Ingesting into a graph index completes (extraction may return empty, must not fail)."""
        idx = self.store.create_index(
            name="graph-ingest",
            index_type="graph",
            chunk_size=200,
            chunk_overlap=0,
        )
        content = b"Machine learning is a subfield of artificial intelligence."
        job = await self.store.ingest_files(idx.id, [("ml.txt", content)])
        # Job must complete even if entity extraction fails (no API key in tests)
        from api.services.rag_service import IngestJobStatus
        assert job.status == IngestJobStatus.completed

    def test_get_neighbors_returns_depth(self):
        """get_neighbors returns list of (GraphNode, int) tuples with correct depths."""
        from api.services.rag_service import GraphEdge, GraphNode
        gs = GraphStore()
        idx_id = "test-depth"
        # Build: A -[r]-> B -[r]-> C
        for nid, name in [("A", "NodeA"), ("B", "NodeB"), ("C", "NodeC")]:
            gs.upsert_node(idx_id, GraphNode(id=nid, entity=name, entity_type="T", description="", chunk_ids=[]))
        gs.upsert_edge(idx_id, GraphEdge(id="e1", subject_id="A", predicate="r", object_id="B", chunk_ids=[]))
        gs.upsert_edge(idx_id, GraphEdge(id="e2", subject_id="B", predicate="r", object_id="C", chunk_ids=[]))

        results = gs.get_neighbors(idx_id, ["A"], hops=2)
        # Should be list of tuples
        assert isinstance(results, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in results)
        depth_by_id = {node.id: depth for node, depth in results}
        assert depth_by_id["B"] == 1
        assert depth_by_id["C"] == 2
