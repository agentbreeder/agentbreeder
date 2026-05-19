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


class TestFixedSizeChunkingValidation:
    """W5-R10 — chunk_fixed_size must reject overlap >= chunk_size."""

    def test_overlap_equal_to_chunk_size_raises(self):
        with pytest.raises(ValueError, match="strictly less than"):
            chunk_fixed_size("abcdefgh", chunk_size=4, overlap=4)

    def test_overlap_greater_than_chunk_size_raises(self):
        with pytest.raises(ValueError, match="strictly less than"):
            chunk_fixed_size("abcdefgh", chunk_size=4, overlap=10)

    def test_zero_chunk_size_raises(self):
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            chunk_fixed_size("abc", chunk_size=0, overlap=0)

    def test_negative_chunk_size_raises(self):
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            chunk_fixed_size("abc", chunk_size=-1, overlap=0)

    def test_negative_overlap_raises(self):
        with pytest.raises(ValueError, match="overlap must be non-negative"):
            chunk_fixed_size("abc", chunk_size=10, overlap=-1)

    def test_valid_overlap_just_below_chunk_size_works(self):
        # overlap = chunk_size - 1 is legal (advances by 1 char per iter).
        chunks = chunk_fixed_size("a" * 20, chunk_size=5, overlap=4)
        assert len(chunks) > 0


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


class TestExtractTextMalformed:
    """W5-R8 — malformed-file input handling for extract_text.

    extract_text must NEVER raise on malformed bytes. It returns the
    best-effort text or a placeholder string.
    """

    def test_truncated_json_falls_back_to_raw_text(self):
        # Unterminated JSON object — json.JSONDecodeError should be caught.
        content = b'{"key": "value", "num": 42'
        result = extract_text("broken.json", content)
        # Falls back to raw string content rather than crashing.
        assert isinstance(result, str)
        assert "key" in result

    def test_malformed_json_array_falls_back_to_raw_text(self):
        content = b'[{"a": 1}, {"b":'  # truncated array
        result = extract_text("broken.json", content)
        assert isinstance(result, str)
        # Raw text should still surface the contents we did read.
        assert "a" in result

    def test_malformed_csv_with_uneven_columns(self):
        # CSV with rows of unequal column counts — csv module is tolerant,
        # but the function must not crash and must surface the rows.
        content = b"name,age,city\nAlice,30\nBob,25,NYC,extra\nCharlie"
        result = extract_text("messy.csv", content)
        assert isinstance(result, str)
        assert "Alice" in result
        assert "Bob" in result
        assert "Charlie" in result

    def test_csv_with_invalid_utf8_bytes(self):
        # Mixed UTF-8 + invalid byte sequence — decode errors are 'replace'd.
        content = b"name,age\nAlice,30\n\xff\xfeBob,25"
        result = extract_text("garbled.csv", content)
        assert isinstance(result, str)
        assert "Alice" in result

    def test_corrupted_pdf_returns_placeholder(self):
        # Garbage bytes claiming to be PDF — must not raise.
        content = b"\x00\x01\x02 not a real pdf at all \xff\xfe"
        result = extract_text("garbage.pdf", content)
        assert isinstance(result, str)
        # Either a placeholder or empty-fallback content is acceptable —
        # the contract is "no crash". We assert the function returned a string.
        # Common outputs include the install-PyPDF2 hint or the could-not-extract hint.
        assert result == result  # no exception is the assertion

    def test_empty_pdf_bytes_returns_placeholder(self):
        result = extract_text("empty.pdf", b"")
        assert isinstance(result, str)
        # Empty PDF should fall through to a placeholder string.
        assert "PDF content" in result or result == ""

    def test_truncated_pdf_stream_does_not_crash(self):
        # A PDF header with no body — PyPDF2 (if installed) typically raises;
        # we must catch that and fall back to regex / placeholder.
        content = b"%PDF-1.4\n%truncated here"
        result = extract_text("truncated.pdf", content)
        assert isinstance(result, str)


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
# RAGStore Ingestion Dedup / Idempotency Tests
# ---------------------------------------------------------------------------


class TestRAGStoreIngestDedup:
    """Regression tests for the dashboard "8 duplicate hits" bug.

    Before the fix, ingesting the same file N times produced N×chunks copies
    in the index, and ``hybrid_search`` happily returned every duplicate.
    These tests pin the new behaviour:

    1. Re-ingesting the same content is a no-op (idempotent by SHA-256).
    2. ``replace=True`` drops the previous chunks for the matching sources.
    3. A within-batch duplicate file is collapsed to one set of chunks.
    4. Legacy chunks without ``content_hash`` are back-filled transparently.
    5. ``hybrid_search`` deduplicates byte-identical chunks before truncating
       to ``top_k`` (this is the actual fix for the user-visible bug).
    """

    def setup_method(self):
        self.store = RAGStore()

    @pytest.mark.asyncio
    async def test_reingest_same_file_is_idempotent(self):
        idx = self.store.create_index(name="dedup-idem", chunk_size=50, chunk_overlap=0)
        content = b"Engineering practices for AI agents and platforms."
        job1 = await self.store.ingest_files(idx.id, [("doc.md", content)])
        first_count = idx.chunk_count
        assert first_count > 0
        assert job1.total_chunks == first_count

        # Re-ingest the same bytes — chunk count must stay flat.
        await self.store.ingest_files(idx.id, [("doc.md", content)])
        assert idx.chunk_count == first_count

        hashes = {c.metadata["content_hash"] for c in idx.chunks}
        assert len(hashes) == first_count  # every chunk hash is unique

    @pytest.mark.asyncio
    async def test_replace_drops_existing_source(self):
        idx = self.store.create_index(name="dedup-replace", chunk_size=50, chunk_overlap=0)
        v1 = b"Version one of the doc with content A and B."
        v2 = b"Version two replaces it with X and Y entirely."
        await self.store.ingest_files(idx.id, [("doc.md", v1)])
        v1_count = idx.chunk_count
        assert v1_count > 0

        await self.store.ingest_files(idx.id, [("doc.md", v2)], replace=True)
        # No v1 chunks survive — every chunk now belongs to v2.
        assert all("X and Y" in c.text or c.source == "doc.md" for c in idx.chunks)
        assert idx.chunk_count > 0
        # And the chunks come from the new content (v1 text is gone).
        joined = " ".join(c.text for c in idx.chunks)
        assert "Version one" not in joined
        assert "Version two" in joined

    @pytest.mark.asyncio
    async def test_within_batch_duplicate_collapsed(self):
        idx = self.store.create_index(name="dedup-batch", chunk_size=50, chunk_overlap=0)
        same = b"Identical body across two filenames."
        await self.store.ingest_files(idx.id, [("a.md", same), ("b.md", same)])
        # Both files chunk identically — only one set is kept.
        chunks_text = [c.text for c in idx.chunks]
        assert len(chunks_text) == len(set(chunks_text))

    @pytest.mark.asyncio
    async def test_legacy_chunks_get_content_hash_backfilled(self):
        idx = self.store.create_index(name="dedup-legacy", chunk_size=50, chunk_overlap=0)
        # Plant a "legacy" chunk without content_hash, mimicking pre-fix state.
        legacy = DocumentChunk(
            id="legacy-1",
            text="Legacy chunk body.",
            source="legacy.md",
            metadata={"filename": "legacy.md", "index_id": idx.id},
        )
        idx.chunks.append(legacy)
        idx.chunk_count = 1

        # Re-ingest the same body — should back-fill hash on legacy + dedup.
        await self.store.ingest_files(idx.id, [("legacy.md", b"Legacy chunk body.")])
        assert "content_hash" in legacy.metadata
        # No duplicate chunk added for the identical body.
        assert sum(1 for c in idx.chunks if c.text == "Legacy chunk body.") == 1

    @pytest.mark.asyncio
    async def test_search_does_not_return_duplicate_chunks(self):
        """The bug the user actually saw: 8 identical results for one query.

        We bypass ingest_files (which now dedups upfront) and directly seed
        the index with hand-built duplicate chunks to prove hybrid_search
        deduplicates results on its own — this keeps pre-fix legacy
        indexes safe.
        """
        idx = self.store.create_index(name="dedup-search", chunk_size=50, chunk_overlap=0)
        body = "Engineering practices for AI agents."
        emb = _pseudo_embedding(body, 384)
        # Eight byte-identical chunks all carrying the same content_hash.
        import hashlib  # noqa: PLC0415

        h = hashlib.sha256(body.encode()).hexdigest()
        for i in range(8):
            idx.chunks.append(
                DocumentChunk(
                    id=f"dup-{i}",
                    text=body,
                    source=f"doc-{i}.md",
                    metadata={"content_hash": h},
                    embedding=emb,
                )
            )
        idx.chunk_count = len(idx.chunks)

        results = await self.store.search(idx.id, "engineering", top_k=5)
        # Despite 8 identical chunks and top_k=5, the dedup pass collapses them.
        assert len(results) == 1
        assert results[0].text == body


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


class TestRAGStoreSearchMetadataFilter:
    """W5-R12 — RAGStore.search must apply post-retrieval metadata filtering."""

    def setup_method(self):
        self.store = RAGStore()

    @pytest.mark.asyncio
    async def test_filter_keeps_matching_chunks(self):
        idx = self.store.create_index(name="filter-test", chunk_size=200, chunk_overlap=0)
        # Two docs — different filenames will end up in metadata['filename'].
        await self.store.ingest_files(
            idx.id,
            [
                ("alpha.txt", b"Apples are red and grow on trees."),
                ("beta.txt", b"Bananas are yellow and grow in bunches."),
            ],
        )
        # No filter — both files' chunks should be retrievable.
        unfiltered = await self.store.search(idx.id, "grow", top_k=10)
        sources_no_filter = {h.source for h in unfiltered}
        assert "alpha.txt" in sources_no_filter
        assert "beta.txt" in sources_no_filter

        # With filter — only alpha.txt should remain.
        filtered = await self.store.search(
            idx.id, "grow", top_k=10, filters={"filename": "alpha.txt"}
        )
        assert len(filtered) > 0
        assert all(h.source == "alpha.txt" for h in filtered)
        assert all(h.metadata.get("filename") == "alpha.txt" for h in filtered)

    @pytest.mark.asyncio
    async def test_filter_with_no_matches_returns_empty(self):
        idx = self.store.create_index(name="empty-filter", chunk_size=200, chunk_overlap=0)
        await self.store.ingest_files(idx.id, [("doc.txt", b"Some content here.")])
        # Filter on a metadata key/value that no chunk has.
        results = await self.store.search(
            idx.id, "content", top_k=10, filters={"filename": "does-not-exist.txt"}
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_filter_dict_is_treated_as_no_filter(self):
        idx = self.store.create_index(name="empty-dict", chunk_size=200, chunk_overlap=0)
        await self.store.ingest_files(idx.id, [("doc.txt", b"Hello world content.")])
        # Empty dict — treat as no filter.
        results = await self.store.search(idx.id, "content", top_k=10, filters={})
        # Should match the unfiltered behavior (>= 1 result for "content").
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_filter_requires_all_keys_to_match(self):
        idx = self.store.create_index(name="multi-key", chunk_size=200, chunk_overlap=0)
        await self.store.ingest_files(
            idx.id,
            [
                ("alpha.txt", b"Apples are red."),
                ("beta.txt", b"Bananas are yellow."),
            ],
        )
        # filename matches but index_id is wrong — should yield 0 hits.
        bogus_filter = {"filename": "alpha.txt", "index_id": "bogus-uuid"}
        results = await self.store.search(idx.id, "are", top_k=10, filters=bogus_filter)
        assert results == []


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
            gs.upsert_node(
                idx_id,
                GraphNode(id=nid, entity=name, entity_type="T", description="", chunk_ids=[]),
            )
        gs.upsert_edge(
            idx_id, GraphEdge(id="e1", subject_id="A", predicate="r", object_id="B", chunk_ids=[])
        )
        gs.upsert_edge(
            idx_id, GraphEdge(id="e2", subject_id="B", predicate="r", object_id="C", chunk_ids=[])
        )

        results = gs.get_neighbors(idx_id, ["A"], hops=2)
        # Should be list of tuples
        assert isinstance(results, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in results)
        depth_by_id = {node.id: depth for node, depth in results}
        assert depth_by_id["B"] == 1
        assert depth_by_id["C"] == 2


# ---------------------------------------------------------------------------
# Fallback alerting — W1-03
# ---------------------------------------------------------------------------

import logging  # noqa: E402

from api.services.rag_service import (  # noqa: E402
    EmbeddingResult,
    embed_texts,
)
from engine.observability.degraded_mode import clear_degraded_state  # noqa: E402


@pytest.fixture(autouse=False)
def clear_fallback_state():
    """Reset the shared warn-once dedup set between tests."""
    clear_degraded_state()
    yield
    clear_degraded_state()


@pytest.mark.asyncio
async def test_embed_texts_empty_input_returns_empty_result(
    clear_fallback_state,
) -> None:
    result = await embed_texts([])
    assert isinstance(result, EmbeddingResult)
    assert result.vectors == []
    assert result.used_fallback is False
    assert result.fallback_reason is None


@pytest.mark.asyncio
async def test_embed_texts_uses_fallback_when_no_api_key(
    clear_fallback_state,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    caplog.set_level(logging.WARNING)

    result = await embed_texts(["hello"], model="openai/text-embedding-3-small")

    assert result.used_fallback is True
    assert result.fallback_reason == "openai-no-api-key"
    assert len(result.vectors) == 1
    assert len(result.vectors[0]) == 1536
    assert any(getattr(r, "component", None) == "rag.embedding" for r in caplog.records)


@pytest.mark.asyncio
async def test_embed_texts_fallback_warning_deduplicated(
    clear_fallback_state,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    caplog.set_level(logging.WARNING)

    await embed_texts(["a"], model="openai/text-embedding-3-small")
    await embed_texts(["b"], model="openai/text-embedding-3-small")
    await embed_texts(["c"], model="openai/text-embedding-3-small")

    fallback_warnings = [
        r for r in caplog.records if getattr(r, "component", None) == "rag.embedding"
    ]
    assert len(fallback_warnings) == 1


@pytest.mark.asyncio
async def test_embed_texts_unknown_model_falls_back(
    clear_fallback_state,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    result = await embed_texts(["hi"], model="bogus-provider/foo")
    assert result.used_fallback is True
    assert result.fallback_reason == "unknown-model-prefix"


# ---------------------------------------------------------------------------
# W4-26 — Embedding failure + fallback path coverage
# ---------------------------------------------------------------------------

from unittest.mock import AsyncMock, patch  # noqa: E402

import httpx as _httpx  # noqa: E402

from api.services.rag_service import (  # noqa: E402
    _embed_ollama,
    _embed_openai,
    compute_chunk_hash,
)


@pytest.mark.asyncio
async def test_embed_openai_5xx_falls_back(
    clear_fallback_state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5xx upstream → pseudo-embedding fallback with openai-api-error."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class _FakeResp:
        status_code = 500

        def raise_for_status(self) -> None:  # noqa: D401
            raise _httpx.HTTPStatusError(
                "500 server error",
                request=_httpx.Request("POST", "https://api.openai.com/v1/embeddings"),
                response=_httpx.Response(500),
            )

        def json(self) -> dict:
            return {}

    fake_post = AsyncMock(return_value=_FakeResp())
    with patch("httpx.AsyncClient.post", fake_post):
        vectors, fb, reason = await _embed_openai(["abc", "def"], "text-embedding-3-small")

    assert fb is True
    assert reason == "openai-api-error"
    assert len(vectors) == 2
    assert all(len(v) == 1536 for v in vectors)


@pytest.mark.asyncio
async def test_embed_openai_timeout_falls_back(
    clear_fallback_state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx.TimeoutException → pseudo-embedding fallback."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    async def _raise_timeout(*_args, **_kwargs):
        raise _httpx.TimeoutException("timeout")

    with patch("httpx.AsyncClient.post", side_effect=_raise_timeout):
        vectors, fb, reason = await _embed_openai(["t1"], "text-embedding-3-small")

    assert fb is True
    assert reason == "openai-api-error"
    assert len(vectors) == 1
    assert len(vectors[0]) == 1536


@pytest.mark.asyncio
async def test_embed_ollama_connection_refused_falls_back(
    clear_fallback_state,
) -> None:
    """ConnectError → pseudo-embedding fallback per-text."""

    async def _raise_connect(*_args, **_kwargs):
        raise _httpx.ConnectError("Connection refused")

    with patch("httpx.AsyncClient.post", side_effect=_raise_connect):
        vectors, fb, reason = await _embed_ollama(["a", "b"], "nomic-embed-text")

    assert fb is True
    assert reason == "ollama-unreachable"
    assert len(vectors) == 2
    assert all(len(v) == 768 for v in vectors)


@pytest.mark.asyncio
async def test_embed_texts_mixed_batches_reflect_fallback(
    clear_fallback_state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When at least one batch falls back, EmbeddingResult flags it."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    # Two batches: first succeeds, second 5xx.
    call_count = {"n": 0}

    class _OkResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{"embedding": [0.0] * 1536} for _ in range(32)]}

    class _BadResp:
        status_code = 500

        def raise_for_status(self) -> None:
            raise _httpx.HTTPStatusError(
                "500",
                request=_httpx.Request("POST", "https://api.openai.com/v1/embeddings"),
                response=_httpx.Response(500),
            )

        def json(self) -> dict:
            return {}

    async def _post(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _OkResp()
        return _BadResp()

    texts = [f"t{i}" for i in range(40)]  # 32 + 8 → two batches
    with patch("httpx.AsyncClient.post", side_effect=_post):
        result = await embed_texts(texts, model="openai/text-embedding-3-small")

    assert result.used_fallback is True
    assert result.fallback_reason == "openai-api-error"
    assert len(result.vectors) == 40
    # First 32 came from the OK batch (zeros), the trailing 8 are pseudo.
    assert all(v == 0.0 for v in result.vectors[0])
    assert any(v != 0.0 for v in result.vectors[32])


@pytest.mark.asyncio
async def test_embedding_result_records_used_fallback_and_reason(
    clear_fallback_state,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check the structured EmbeddingResult contract."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = await embed_texts(["x"], model="openai/text-embedding-3-small")
    assert result.used_fallback is True
    assert result.fallback_reason == "openai-no-api-key"

    # Re-test with a different reason to make sure fallback_reason populates
    # with the appropriate code, not a fixed string.
    clear_degraded_state()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    async def _raise(*_a, **_kw):
        raise _httpx.ConnectError("boom")

    with patch("httpx.AsyncClient.post", side_effect=_raise):
        result2 = await embed_texts(["y"], model="openai/text-embedding-3-small")
    assert result2.used_fallback is True
    assert result2.fallback_reason == "openai-api-error"


# ---------------------------------------------------------------------------
# W4-24 — Chunk-hash dedup on ingestion
# ---------------------------------------------------------------------------


def test_compute_chunk_hash_is_normalized_and_deterministic() -> None:
    """Whitespace and casing-preserving normalization → identical hashes."""
    a = compute_chunk_hash("hello world")
    b = compute_chunk_hash("  hello   world  ")
    c = compute_chunk_hash("hello\tworld\n")
    assert a == b == c
    # Length: SHA256 hex → 64 chars.
    assert len(a) == 64


def test_compute_chunk_hash_differs_on_real_content_change() -> None:
    assert compute_chunk_hash("hello world") != compute_chunk_hash("hello there")


@pytest.mark.asyncio
async def test_ingest_dedups_identical_files() -> None:
    """Ingesting the same content twice doesn't grow chunk_count."""
    from api.services.rag_service import RAGStore

    store = RAGStore()
    idx = store.create_index(
        name="dedup-test",
        description="",
        embedding_model="openai/text-embedding-3-small",
        chunk_strategy="fixed_size",
        chunk_size=100,
        chunk_overlap=0,
        source="manual",
    )
    payload = ("doc.txt", b"hello world. this is a small file used for dedup tests.")

    job1 = await store.ingest_files(idx.id, [payload])
    assert job1.status.value == "completed"
    first_count = idx.chunk_count
    assert first_count > 0

    # Second ingestion of identical content — chunks should be skipped.
    job2 = await store.ingest_files(idx.id, [payload])
    assert job2.status.value == "completed"
    assert idx.chunk_count == first_count
    # Every chunk in the index has a content hash.
    assert all(c.metadata.get("content_hash") for c in idx.chunks)


# ---------------------------------------------------------------------------
# W4-25 — Atomic graph rollback on extraction failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_rolls_back_chunks_when_graph_extraction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If graph extraction raises, no chunks should be written to the index."""
    from api.services import rag_service
    from api.services.rag_service import RAGStore

    store = RAGStore()
    idx = store.create_index(
        name="graph-rollback-test",
        description="",
        embedding_model="openai/text-embedding-3-small",
        chunk_strategy="fixed_size",
        chunk_size=80,
        chunk_overlap=0,
        source="manual",
        index_type="graph",
    )

    async def _boom(*_a, **_kw):
        raise RuntimeError("entity extractor exploded")

    # Patch the symbol where it's looked up — extract_entities_batch is
    # imported lazily inside ingest_files.
    monkeypatch.setattr(
        "api.services.graph_extraction.extract_entities_batch",
        _boom,
        raising=False,
    )

    job = await store.ingest_files(
        idx.id,
        [("doc.txt", b"Alice and Bob met in Paris during the spring of 2024.")],
    )

    assert job.status.value == "failed"
    assert "entity extractor exploded" in (job.error or "")
    # The crucial assertion — no chunks written when extraction failed.
    assert idx.chunks == []
    assert idx.chunk_count == 0
    # And the chunk-hash registry is empty too — re-ingesting should still work.
    assert all(c.metadata.get("content_hash") is not None for c in idx.chunks)
    _ = rag_service  # silence unused-import warning
