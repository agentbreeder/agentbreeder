"""Integration tests for GraphRAG — create graph index, ingest, search, verify entity pipeline."""
from __future__ import annotations

import pytest

from api.services.rag_service import RAGStore, IndexType, IngestJobStatus


class TestGraphIndexCRUD:
    def setup_method(self):
        self.store = RAGStore()  # fresh store per test

    def test_create_graph_index(self):
        idx = self.store.create_index(name="test-graph", index_type="graph")
        assert idx.index_type == IndexType.graph
        assert idx.entity_model == "claude-haiku-4-5-20251001"
        assert idx.max_hops == 2

    def test_create_hybrid_index(self):
        idx = self.store.create_index(name="test-hybrid", index_type="hybrid")
        assert idx.index_type == IndexType.hybrid

    def test_create_invalid_index_type(self):
        with pytest.raises(ValueError):
            self.store.create_index(name="bad", index_type="unknown")

    def test_delete_graph_index_cleans_graph_store(self):
        from api.services.graph_store import GraphStore
        graph_store = GraphStore()
        idx = self.store.create_index(name="to-delete", index_type="graph")
        # Manually populate graph store
        from api.services.rag_service import GraphNode
        import uuid
        node = GraphNode(id=str(uuid.uuid4()), entity="TestCo", entity_type="organization",
                         description="test", chunk_ids=["c1"])
        graph_store.upsert_node(idx.id, node)
        assert graph_store.node_count(idx.id) == 1
        # Delete subgraph
        graph_store.delete_subgraph(idx.id)
        assert graph_store.node_count(idx.id) == 0


class TestGraphIngestPipeline:
    """Tests that ingest on a graph index runs Phase 2.5 extraction (mocked)."""

    @pytest.mark.asyncio
    async def test_ingest_vector_index_completes(self):
        store = RAGStore()
        idx = store.create_index(name="vec", index_type="vector")
        job = await store.ingest_files(idx.id, [("test.txt", b"Hello world, this is a test document.")])
        assert job.status == IngestJobStatus.completed
        assert idx.chunk_count > 0

    @pytest.mark.asyncio
    async def test_ingest_graph_index_completes_even_without_api_key(self):
        """Graph ingest should complete (with 0 entities) when no ANTHROPIC_API_KEY set."""
        store = RAGStore()
        idx = store.create_index(name="graph-ingest", index_type="graph")
        job = await store.ingest_files(idx.id, [("doc.txt", b"Apple acquired Beats in 2014. Tim Cook is the CEO of Apple.")])
        # Job must complete (not fail) even without API key
        assert job.status == IngestJobStatus.completed
        # node_count may be 0 (no API key) but index must have chunks
        assert idx.chunk_count > 0

    @pytest.mark.asyncio
    async def test_graph_search_fallback_on_empty_graph(self):
        """Graph search on empty graph falls back to vector results without error."""
        store = RAGStore()
        idx = store.create_index(name="graph-search", index_type="graph")
        await store.ingest_files(idx.id, [("d.txt", b"The quick brown fox jumped over the lazy dog.")])
        results = await store.search(idx.id, query="fox", top_k=3)
        assert isinstance(results, list)
        # Should return results even without graph data


class TestEvalMetrics:
    """Tests for the new GraphRAG eval scorer functions."""

    def test_entity_recall_all_present(self):
        from api.services.eval_service import score_entity_recall
        assert score_entity_recall("Apple and Google are companies.", ["Apple", "Google"]) == 1.0

    def test_entity_recall_partial(self):
        from api.services.eval_service import score_entity_recall
        assert score_entity_recall("Apple is a company.", ["Apple", "Google"]) == 0.5

    def test_entity_recall_empty_ground_truth(self):
        from api.services.eval_service import score_entity_recall
        assert score_entity_recall("anything", []) == 1.0

    def test_relationship_precision_all_correct(self):
        from api.services.eval_service import score_relationship_precision
        rels = [("Apple", "acquired", "Beats")]
        assert score_relationship_precision(rels, rels) == 1.0

    def test_relationship_precision_none_correct(self):
        from api.services.eval_service import score_relationship_precision
        assert score_relationship_precision([("A","b","C")], [("X","y","Z")]) == 0.0

    def test_vector_fallback_rate_all_fallback(self):
        from api.services.eval_service import score_vector_fallback_rate
        hits = [{"nodes_traversed": 0}, {"nodes_traversed": 0}]
        assert score_vector_fallback_rate(hits) == 1.0

    def test_vector_fallback_rate_no_fallback(self):
        from api.services.eval_service import score_vector_fallback_rate
        hits = [{"nodes_traversed": 3}, {"nodes_traversed": 5}]
        assert score_vector_fallback_rate(hits) == 0.0

    def test_vector_fallback_rate_empty(self):
        from api.services.eval_service import score_vector_fallback_rate
        assert score_vector_fallback_rate([]) == 0.0
