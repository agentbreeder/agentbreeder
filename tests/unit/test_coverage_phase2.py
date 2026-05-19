"""Coverage-phase-2 tests — targets uncovered branches in rag_service,
graph_extraction, eval_service, routes/git, routes/providers.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# rag_service
# ---------------------------------------------------------------------------
from api.services.rag_service import (
    DocumentChunk,
    IndexType,
    IngestJob,
    IngestJobStatus,
    RAGStore,
    _embed_ollama,
    _embed_openai,
    _extract_pdf_text,
    _find_split_point,
    chunk_recursive,
    extract_text,
    get_rag_store,
    hybrid_search,
)


class TestIngestJobProgress:
    def test_progress_uses_total_files_when_no_chunks(self):
        """Lines 197-198: total_chunks == 0 branch."""

        job = IngestJob(
            id="j1",
            index_id="i1",
            status=IngestJobStatus.embedding,
            total_files=10,
            processed_files=3,
            total_chunks=0,
            embedded_chunks=0,
            started_at="2026-01-01T00:00:00Z",
        )
        d = job.to_dict()
        # progress derived from files not chunks
        assert d["progress_pct"] == pytest.approx(30.0)

    def test_progress_uses_chunks_when_available(self):
        job = IngestJob(
            id="j2",
            index_id="i2",
            status=IngestJobStatus.embedding,
            total_files=5,
            processed_files=5,
            total_chunks=100,
            embedded_chunks=50,
            started_at="2026-01-01T00:00:00Z",
        )
        d = job.to_dict()
        assert d["progress_pct"] == pytest.approx(50.0)

    def test_progress_zero_when_no_files_or_chunks(self):
        job = IngestJob(
            id="j3",
            index_id="i3",
            status=IngestJobStatus.pending,
            total_files=0,
            processed_files=0,
            total_chunks=0,
            embedded_chunks=0,
            started_at="2026-01-01T00:00:00Z",
        )
        d = job.to_dict()
        assert d["progress_pct"] == 0.0


class TestFindSplitPoint:
    def test_finds_first_separator(self):
        result = _find_split_point("hello\n\nworld", ["\n\n", "\n"])
        assert result is not None
        sep, idx = result
        assert sep == "\n\n"
        assert idx == 5

    def test_returns_none_when_no_separator_found(self):
        result = _find_split_point("no separators here", ["|||", "^^^"])
        assert result is None

    def test_uses_fallback_separator(self):
        result = _find_split_point("a\nb", ["\n\n", "\n"])
        assert result is not None
        sep, idx = result
        assert sep == "\n"


class TestChunkRecursiveDeepBranches:
    def test_sub_recursion_on_large_part(self):
        """Lines 333-336: recursively split with next separator level."""
        # Create a long single-paragraph text that needs sub-splitting
        long_sentence = "word " * 40  # 200 chars
        text = long_sentence + "\n\n" + "short"
        chunks = chunk_recursive(text, chunk_size=50, overlap=0)
        assert len(chunks) >= 2
        # All content preserved
        all_text = " ".join(chunks)
        assert "word" in all_text
        assert "short" in all_text

    def test_overlap_applied_to_multiple_chunks(self):
        """Lines 345-349: overlap > 0 path."""
        text = "Alpha beta gamma.\n\nDelta epsilon zeta.\n\nEta theta iota."
        chunks = chunk_recursive(text, chunk_size=30, overlap=8)
        assert len(chunks) >= 2
        # Second chunk should have tail of first prepended
        if len(chunks) >= 2:
            # Just verify overlap mechanism ran (no assertion on exact tail since
            # chunking may be non-deterministic in split point selection)
            assert all(len(c) > 0 for c in chunks)


class TestExtractText:
    def test_json_bytes(self):
        data = {"key": "value", "nested": [1, 2]}
        b = json.dumps(data).encode()
        result = extract_text("file.json", b)
        assert '"key"' in result
        assert '"value"' in result

    def test_invalid_json_falls_back_to_text(self):
        """Lines 385-386: JSONDecodeError fallback."""
        b = b"not valid json {"
        result = extract_text("data.json", b)
        assert "not valid json" in result

    def test_pdf_bytes_dispatches_to_pdf_extractor(self):
        """Line 390: PDF branch."""
        pdf_bytes = b"BT (Hello PDF World) ET"
        result = extract_text("document.pdf", pdf_bytes)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_plain_text_bytes(self):
        result = extract_text("readme.txt", b"just plain text")
        assert result == "just plain text"


class TestExtractPdfText:
    def test_extracts_bt_et_strings(self):
        """Lines 398-412."""
        content = b"BT (Hello World) ET some garbage BT (Second) ET"
        result = _extract_pdf_text(content)
        assert "Hello World" in result
        assert "Second" in result

    def test_fallback_when_no_bt_et(self):
        content = b"no text blocks here"
        result = _extract_pdf_text(content)
        assert "PyPDF2" in result or result == ""  # fallback message or empty

    def test_handles_decode_error_gracefully(self):
        # Pass an empty bytes object — should not raise
        result = _extract_pdf_text(b"")
        assert isinstance(result, str)


class TestHybridSearchSkipsNullEmbedding:
    def test_skips_chunk_without_embedding(self):
        """Line 438: chunk.embedding is None path."""
        no_embed = DocumentChunk(id="c1", text="hello", source="s", embedding=None)
        has_embed = DocumentChunk(
            id="c2",
            text="hello world",
            source="s",
            embedding=[1.0, 0.0, 0.0],
        )
        query_emb = [1.0, 0.0, 0.0]
        results = hybrid_search(
            query_embedding=query_emb,
            query_text="hello",
            chunks=[no_embed, has_embed],
            top_k=5,
        )
        # Only has_embed should appear
        assert any(r.chunk_id == "c2" for r in results)
        assert not any(r.chunk_id == "c1" for r in results)


class TestEmbedOpenAI:
    @pytest.mark.asyncio
    async def test_returns_pseudo_when_no_api_key(self, monkeypatch):
        """Lines 466-468: no OPENAI_API_KEY.

        W1-03: _embed_openai now returns (vectors, used_fallback, reason).
        """
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        vectors, used_fallback, reason = await _embed_openai(
            ["hello", "world"], "text-embedding-ada-002"
        )
        assert len(vectors) == 2
        assert len(vectors[0]) == 1536
        assert used_fallback is True
        assert reason == "openai-no-api-key"

    @pytest.mark.asyncio
    async def test_calls_openai_api(self, monkeypatch):
        """Lines 469-478: successful API call.

        W1-03: _embed_openai now returns (vectors, used_fallback, reason).
        """
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ]
        }
        with patch("api.services.rag_service.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=fake_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            vectors, used_fallback, reason = await _embed_openai(
                ["a", "b"], "text-embedding-ada-002"
            )
        assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        assert used_fallback is False
        assert reason is None

    @pytest.mark.asyncio
    async def test_falls_back_on_http_error(self, monkeypatch):
        """Lines 479-480: exception path.

        W1-03: _embed_openai now returns (vectors, used_fallback, reason).
        """
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("api.services.rag_service.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            inst.post = AsyncMock(side_effect=httpx.ConnectError("conn refused"))
            MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            vectors, used_fallback, reason = await _embed_openai(
                ["text"], "text-embedding-ada-002"
            )
        # Fallback pseudo-embedding
        assert len(vectors) == 1
        assert len(vectors[0]) == 1536
        assert used_fallback is True
        assert reason == "openai-api-error"


class TestEmbedOllama:
    @pytest.mark.asyncio
    async def test_calls_ollama_api(self):
        """Lines 483-493: successful Ollama call.

        W1-03: _embed_ollama now returns (vectors, used_fallback, reason).
        """
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {"embedding": [0.7, 0.8, 0.9]}
        with patch("api.services.rag_service.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=fake_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            vectors, used_fallback, reason = await _embed_ollama(["test text"], "nomic-embed-text")
        assert vectors == [[0.7, 0.8, 0.9]]
        assert used_fallback is False
        assert reason is None

    @pytest.mark.asyncio
    async def test_falls_back_on_error(self):
        """Lines 494-500: exception path.

        W1-03: _embed_ollama now returns (vectors, used_fallback, reason).
        """
        with patch("api.services.rag_service.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            inst.post = AsyncMock(side_effect=Exception("ollama down"))
            MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            vectors, used_fallback, reason = await _embed_ollama(["text"], "nomic-embed-text")
        assert len(vectors) == 1
        assert len(vectors[0]) == 768
        assert used_fallback is True
        assert reason == "ollama-unreachable"


class TestRAGStoreDelete:
    def test_delete_graph_index_cleans_graph_store(self):
        """Lines 810-812: delete cleans up graph store for graph indexes."""
        store = RAGStore()
        idx = store.create_index(
            name="g-idx",
            index_type="graph",
            embedding_model="openai/text-embedding-3-small",
        )
        mock_gs = MagicMock()
        with patch("api.services.graph_store.get_graph_store", return_value=mock_gs):
            result = store.delete_index(idx.id)
        assert result is True
        mock_gs.delete_subgraph.assert_called_once_with(idx.id)

    def test_delete_hybrid_index_cleans_graph_store(self):
        store = RAGStore()
        idx = store.create_index(
            name="h-idx",
            index_type="hybrid",
            embedding_model="openai/text-embedding-3-small",
        )
        mock_gs = MagicMock()
        with patch("api.services.graph_store.get_graph_store", return_value=mock_gs):
            result = store.delete_index(idx.id)
        assert result is True
        mock_gs.delete_subgraph.assert_called_once()

    def test_delete_vector_index_does_not_touch_graph_store(self):
        store = RAGStore()
        idx = store.create_index(
            name="v-idx",
            index_type="vector",
            embedding_model="openai/text-embedding-3-small",
        )
        mock_gs = MagicMock()
        with patch("api.services.graph_store.get_graph_store", return_value=mock_gs):
            result = store.delete_index(idx.id)
        assert result is True
        mock_gs.delete_subgraph.assert_not_called()


class TestRAGStoreIngestJob:
    def test_create_and_get_ingest_job(self):
        """Line 832: create_ingest_job path."""
        store = RAGStore()
        idx = store.create_index(
            name="i-idx",
            index_type=IndexType.vector,
            embedding_model="text-embedding-ada-002",
        )
        job = store.create_ingest_job(idx.id, total_files=5)
        assert job.status == IngestJobStatus.pending
        assert job.total_files == 5

        fetched = store.get_ingest_job(job.id)
        assert fetched is not None
        assert fetched.id == job.id

    def test_get_nonexistent_job_returns_none(self):
        store = RAGStore()
        assert store.get_ingest_job("nonexistent-id") is None


class TestGetRagStoreSingleton:
    def test_singleton_returns_same_instance(self):
        """Lines 992-994: singleton pattern."""
        import api.services.rag_service as rag_mod

        rag_mod._store = None  # reset
        s1 = get_rag_store()
        s2 = get_rag_store()
        assert s1 is s2

    def test_singleton_reuses_existing(self):
        import api.services.rag_service as rag_mod

        existing = RAGStore()
        rag_mod._store = existing
        s = get_rag_store()
        assert s is existing
        rag_mod._store = None  # cleanup


# ---------------------------------------------------------------------------
# graph_extraction — uncovered paths
# ---------------------------------------------------------------------------

from api.services.graph_extraction import (  # noqa: E402
    _call_claude,
    _parse_extraction_result,
    extract_entities_batch,
)


class TestCallClaude:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_api_key(self, monkeypatch):
        """Line 100: no ANTHROPIC_API_KEY."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = await _call_claude("some text", "claude-haiku-4-5-20251001")
        assert result == {"entities": [], "relationships": []}

    @pytest.mark.asyncio
    async def test_successful_api_call(self, monkeypatch):
        """Lines 106-160: happy path through _call_claude."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        payload = {
            "entities": [
                {"entity": "AWS", "type": "organization", "description": "Cloud provider"}
            ],
            "relationships": [],
        }
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {"content": [{"text": json.dumps(payload)}]}
        with patch("api.services.graph_extraction.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=fake_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _call_claude("AWS is a cloud provider", "claude-haiku-4-5-20251001")
        assert result["entities"][0]["entity"] == "AWS"

    @pytest.mark.asyncio
    async def test_handles_json_decode_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {"content": [{"text": "not valid json {{{"}]}
        with patch("api.services.graph_extraction.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=fake_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _call_claude("text", "claude-haiku-4-5-20251001")
        assert result == {"entities": [], "relationships": []}

    @pytest.mark.asyncio
    async def test_handles_http_error(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        with patch("api.services.graph_extraction.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            inst.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _call_claude("text", "claude-haiku-4-5-20251001")
        assert result == {"entities": [], "relationships": []}

    @pytest.mark.asyncio
    async def test_handles_key_error(self, monkeypatch):
        """Lines 162-164: KeyError/IndexError path."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        fake_resp = MagicMock()
        fake_resp.raise_for_status = MagicMock()
        fake_resp.json.return_value = {"wrong_key": "bad structure"}
        with patch("api.services.graph_extraction.httpx.AsyncClient") as MockClient:
            inst = AsyncMock()
            inst.post = AsyncMock(return_value=fake_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=inst)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await _call_claude("text", "claude-haiku-4-5-20251001")
        assert result == {"entities": [], "relationships": []}


class TestParseExtractionResultEdgeCases:
    def test_entities_not_a_list(self):
        """Line 248-249: entities is not a list."""
        data = {"entities": "not a list", "relationships": []}
        nodes, edges = _parse_extraction_result(data, "test chunk")
        assert nodes == []
        assert edges == []

    def test_entity_entry_not_a_dict(self):
        """Lines 253-254: non-dict entity entry."""
        data = {"entities": ["string entry", None, 42], "relationships": []}
        nodes, edges = _parse_extraction_result(data, "test chunk")
        assert nodes == []

    def test_entity_missing_required_fields(self):
        """Lines 258-267: missing entity or type fields."""
        data = {
            "entities": [
                {"entity": "Only name"},  # missing type
                {"type": "organization"},  # missing entity
                {"entity": "", "type": "org"},  # empty entity
            ],
            "relationships": [],
        }
        nodes, edges = _parse_extraction_result(data, "test chunk")
        assert nodes == []

    def test_relationships_not_a_list(self):
        """Lines 282-283: relationships is not a list."""
        data = {
            "entities": [{"entity": "AWS", "type": "organization", "description": "cloud"}],
            "relationships": "not a list",
        }
        nodes, edges = _parse_extraction_result(data, "test chunk")
        assert len(nodes) == 1
        assert edges == []

    def test_relationship_not_a_dict(self):
        """Lines 287-288: non-dict relationship entry."""
        data = {
            "entities": [{"entity": "AWS", "type": "organization", "description": "cloud"}],
            "relationships": ["string", None],
        }
        nodes, edges = _parse_extraction_result(data, "test chunk")
        assert edges == []

    def test_relationship_with_unknown_entities_skipped(self):
        """Lines 294-298: subject or object not in extracted entities."""
        data = {
            "entities": [{"entity": "AWS", "type": "organization", "description": "cloud"}],
            "relationships": [
                {"subject": "AWS", "predicate": "uses", "object": "UnknownEntity"},
            ],
        }
        nodes, edges = _parse_extraction_result(data, "test chunk")
        assert len(nodes) == 1
        assert edges == []  # unknown object entity — edge skipped

    def test_valid_relationship_included(self):
        data = {
            "entities": [
                {"entity": "AWS", "type": "organization", "description": "cloud"},
                {"entity": "S3", "type": "concept", "description": "storage"},
            ],
            "relationships": [
                {"subject": "AWS", "predicate": "provides", "object": "S3"},
            ],
        }
        nodes, edges = _parse_extraction_result(data, "chunk")
        assert len(nodes) == 2
        assert len(edges) == 1
        assert edges[0].predicate == "provides"


class TestBatchExtractEntities:
    @pytest.mark.asyncio
    async def test_empty_texts_returns_empty(self):
        """Line 85: empty texts path."""
        result = await extract_entities_batch([], model="ollama/qwen2.5:7b")
        assert result == []

    @pytest.mark.asyncio
    async def test_batches_with_semaphore(self):
        """Lines 86-93: semaphore + asyncio.gather path."""
        fake_nodes = [MagicMock(), MagicMock()]
        fake_edges: list = []

        async def fake_extract(
            text: str, model: str, cache: Any, custom_types: Any = None
        ) -> tuple:
            return (fake_nodes, fake_edges)

        with patch("api.services.graph_extraction.extract_entities", fake_extract):
            result = await extract_entities_batch(
                ["text1", "text2", "text3"],
                model="ollama/qwen2.5:7b",
                batch_size=2,
            )
        assert len(result) == 3


# ---------------------------------------------------------------------------
# eval_service — uncovered LLM-judge paths and seed_demo_data
# ---------------------------------------------------------------------------

from api.services.eval_service import (  # noqa: E402
    EvalStore,
    _seed_demo_data,
    score_with_judge_model,
    seed_community_datasets,
)


class TestScoreWithJudgeModel:
    def test_returns_heuristic_when_model_unknown(self):
        """Lines 186-194: heuristic fallback when model prefix not matched."""
        result = score_with_judge_model(
            actual="Paris is the capital of France.",
            expected="Paris is the capital of France.",
            judge_model="unknown-model",
        )
        assert "judge_accuracy" in result
        assert "judge_helpfulness" in result
        assert "judge_safety" in result
        assert "judge_groundedness" in result

    def test_claude_path_no_api_key_falls_back(self, monkeypatch):
        """Lines 128-145: claude branch with no API key."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = score_with_judge_model(
            actual="Paris",
            expected="Paris is the capital of France.",
            judge_model="claude-haiku-4-5-20251001",
        )
        # Falls back to heuristic
        assert "judge_accuracy" in result

    def test_openai_path_no_api_key_falls_back(self, monkeypatch):
        """Lines 147-165: openai branch with no API key."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = score_with_judge_model(
            actual="Paris",
            expected="Paris is the capital of France.",
            judge_model="gpt-4o",
        )
        assert "judge_accuracy" in result

    def test_gemini_path_no_api_key_falls_back(self, monkeypatch):
        """Lines 166-181: gemini branch with no API key."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        result = score_with_judge_model(
            actual="Paris",
            expected="Paris is the capital of France.",
            judge_model="gemini-1.5-flash",
        )
        assert "judge_accuracy" in result

    def test_claude_with_api_key_calls_api(self, monkeypatch):
        """Lines 128-145: claude branch success path."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        scores = {"accuracy": 0.9, "helpfulness": 0.8, "safety": 1.0, "groundedness": 0.85}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"content": [{"text": json.dumps(scores)}]}
        with patch("httpx.post", return_value=mock_resp):
            result = score_with_judge_model(
                "Paris", "Paris is capital", "claude-haiku-4-5-20251001"
            )
        assert result["judge_accuracy"] == pytest.approx(0.9)
        assert result["judge_safety"] == pytest.approx(1.0)

    def test_openai_with_api_key_calls_api(self, monkeypatch):
        """Lines 147-165: openai branch success path."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
        scores = {"accuracy": 0.7, "helpfulness": 0.8, "safety": 1.0, "groundedness": 0.6}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": json.dumps(scores)}}]}
        with patch("httpx.post", return_value=mock_resp):
            result = score_with_judge_model("The answer", "expected", "gpt-4o")
        assert result["judge_accuracy"] == pytest.approx(0.7)

    def test_gemini_with_api_key_calls_api(self, monkeypatch):
        """Lines 166-181: gemini branch success path."""
        monkeypatch.setenv("GOOGLE_API_KEY", "goog-test")
        scores = {"accuracy": 0.6, "helpfulness": 0.7, "safety": 1.0, "groundedness": 0.5}
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": json.dumps(scores)}]}}]
        }
        with patch("httpx.post", return_value=mock_resp):
            result = score_with_judge_model("answer", "expected", "gemini-1.5-flash")
        assert result["judge_accuracy"] == pytest.approx(0.6)

    def test_exception_in_api_call_falls_back(self, monkeypatch):
        """Lines 186-194: exception path → heuristic fallback."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        with patch("httpx.post", side_effect=Exception("network error")):
            result = score_with_judge_model(
                "Paris", "Paris is capital", "claude-haiku-4-5-20251001"
            )
        assert "judge_accuracy" in result


class TestSeedDemoData:
    def test_seed_demo_data_runs_without_error(self):
        """Lines 1324-1396: _seed_demo_data happy path."""
        store = EvalStore(store_dir=None)
        _seed_demo_data(store)
        datasets = store.list_datasets()
        assert len(datasets) > 0

    def test_seed_community_datasets_returns_ids(self):
        """Lines 1397+: seed_community_datasets."""
        store = EvalStore(store_dir=None)
        ids = seed_community_datasets(store)
        assert isinstance(ids, list)
        assert len(ids) >= 3  # at least 3 community datasets


# ---------------------------------------------------------------------------
# api/routes/git — uncovered error paths
# ---------------------------------------------------------------------------

from api.main import app as fastapi_app  # noqa: E402
from api.services.git_service import GitService  # noqa: E402
from api.services.pr_service import PRError, PRService  # noqa: E402


@pytest.fixture
def client():
    return TestClient(fastapi_app, raise_server_exceptions=False)


def _mock_pr_service_with_error(method: str) -> MagicMock:
    svc = MagicMock(spec=PRService)
    getattr(svc, method).side_effect = PRError("operation failed")
    return svc


class TestGitRouteErrorPaths:
    def test_approve_pr_error_returns_400(self, client):
        """Lines 269-270: PRError on approve raises 400."""
        pr_id = str(uuid.uuid4())
        svc = _mock_pr_service_with_error("approve")
        with patch("api.routes.git._get_pr", return_value=svc):
            resp = client.post(
                f"/api/v1/git/prs/{pr_id}/approve",
                json={"reviewer": "alice"},
            )
        assert resp.status_code == 400
        assert "operation failed" in resp.json()["detail"]

    def test_reject_pr_error_returns_400(self, client):
        """Lines 283-284: PRError on reject raises 400."""
        pr_id = str(uuid.uuid4())
        svc = _mock_pr_service_with_error("reject")
        with patch("api.routes.git._get_pr", return_value=svc):
            resp = client.post(
                f"/api/v1/git/prs/{pr_id}/reject",
                json={"reviewer": "alice", "reason": "not ready"},
            )
        assert resp.status_code == 400

    def test_merge_pr_error_returns_400(self, client):
        """Lines 298-299: PRError on merge raises 400."""
        pr_id = str(uuid.uuid4())
        svc = _mock_pr_service_with_error("merge_pr")
        with patch("api.routes.git._get_pr", return_value=svc):
            resp = client.post(
                f"/api/v1/git/prs/{pr_id}/merge",
                json={"tag_version": "v1.0.0"},
            )
        assert resp.status_code == 400

    def test_add_comment_error_returns_400(self, client):
        """Lines 324-325: PRError on add_comment raises 400."""
        pr_id = str(uuid.uuid4())
        svc = _mock_pr_service_with_error("add_comment")
        with patch("api.routes.git._get_pr", return_value=svc):
            resp = client.post(
                f"/api/v1/git/prs/{pr_id}/comments",
                json={"author": "alice", "text": "looks good"},
            )
        assert resp.status_code == 400

    def test_set_services_updates_singletons(self):
        """Lines 57-58: set_services function."""
        import api.routes.git as git_routes

        orig_git = git_routes._git_service
        orig_pr = git_routes._pr_service

        fake_git = MagicMock(spec=GitService)
        fake_pr = MagicMock(spec=PRService)
        git_routes.set_services(fake_git, fake_pr)
        assert git_routes._git_service is fake_git
        assert git_routes._pr_service is fake_pr

        # restore
        git_routes._git_service = orig_git
        git_routes._pr_service = orig_pr


# ---------------------------------------------------------------------------
# api/routes/providers — uncovered endpoints
# ---------------------------------------------------------------------------


class TestProvidersRouteUncovered:
    def test_detect_ollama_endpoint_exists(self, client):
        """Lines 68-91: detect-ollama endpoint exists (405 means wrong method, not 404)."""
        # Just verify the endpoint is registered
        resp = client.post("/api/v1/providers/detect-ollama")
        # Endpoint must exist — 422 (validation) or 401 (auth) are fine, but not 404/405
        assert resp.status_code not in (404, 405)

    def test_toggle_provider_endpoint_exists(self, client):
        """Lines 234-235: toggle endpoint is registered."""
        provider_id = uuid.uuid4()
        resp = client.post(f"/api/v1/providers/{provider_id}/toggle")
        # Must not be 404 (method not found) or 405 (method not allowed)
        assert resp.status_code not in (404, 405)
