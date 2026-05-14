"""Tests for the agenthub.rag SDK module (RagIndex client)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make the SDK importable without installing it.
_SDK = Path(__file__).resolve().parents[2] / "sdk" / "python"
if str(_SDK) not in sys.path:
    sys.path.insert(0, str(_SDK))

from agenthub.rag import (  # noqa: E402
    ALLOWED_EXTS,
    IngestResult,
    RagIndex,
    RagIndexError,
)


@pytest.fixture(autouse=True)
def _set_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTBREEDER_API_TOKEN", "test-token")
    monkeypatch.setenv("AGENTBREEDER_API_URL", "http://localhost:8000")


def _mock_response(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


class TestRagIndexConstruction:
    def test_requires_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENTBREEDER_API_TOKEN", raising=False)
        with pytest.raises(RagIndexError, match="AGENTBREEDER_API_TOKEN"):
            RagIndex("docs", token=None)

    def test_strips_trailing_slash_from_base_url(self) -> None:
        idx = RagIndex("docs", base_url="http://example.com/", token="t")
        assert idx.base_url == "http://example.com"

    def test_accepts_explicit_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AGENTBREEDER_API_TOKEN", raising=False)
        idx = RagIndex("docs", token="explicit-token")
        # Token is stored privately; check via headers
        headers = idx._headers()
        assert headers["Authorization"] == "Bearer explicit-token"


class TestResolveId:
    def test_uuid_passthrough(self) -> None:
        uid = "11111111-2222-3333-4444-555555555555"
        idx = RagIndex(uid)
        assert idx.index_id == uid

    def test_name_lookup(self) -> None:
        idx = RagIndex("docs-index")
        with patch("agenthub.rag.httpx.Client") as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.return_value = _mock_response(
                {
                    "data": [
                        {"id": "aaa", "name": "other"},
                        {"id": "bbb", "name": "docs-index"},
                    ]
                }
            )
            assert idx.index_id == "bbb"

    def test_name_not_found_raises(self) -> None:
        idx = RagIndex("missing-index")
        with patch("agenthub.rag.httpx.Client") as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.get.return_value = _mock_response({"data": []})
            with pytest.raises(RagIndexError, match="not found"):
                _ = idx.index_id


class TestIngest:
    def test_empty_files_raises(self) -> None:
        idx = RagIndex("11111111-2222-3333-4444-555555555555")
        with pytest.raises(RagIndexError, match="At least one file"):
            idx.ingest([])

    def test_missing_file_raises(self) -> None:
        idx = RagIndex("11111111-2222-3333-4444-555555555555")
        with pytest.raises(RagIndexError, match="File not found"):
            idx.ingest(["/nonexistent/path.md"])

    def test_unsupported_extension_raises(self) -> None:
        idx = RagIndex("11111111-2222-3333-4444-555555555555")
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"bin")
            bad = f.name
        try:
            with pytest.raises(RagIndexError, match="Unsupported file type"):
                idx.ingest([bad])
        finally:
            os.unlink(bad)

    def test_ingest_returns_result(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# hello\nbody text")
            md_path = f.name
        try:
            uid = "11111111-2222-3333-4444-555555555555"
            idx = RagIndex(uid)
            with patch("agenthub.rag.httpx.Client") as MockClient:
                client_ctx = MockClient.return_value.__enter__.return_value
                client_ctx.post.return_value = _mock_response(
                    {
                        "data": {
                            "id": "job-1",
                            "status": "completed",
                            "total_files": 1,
                            "processed_files": 1,
                            "total_chunks": 2,
                            "embedded_chunks": 2,
                        }
                    }
                )
                res = idx.ingest([md_path])
                # POST called with files multipart
                _, kwargs = client_ctx.post.call_args
                assert "files" in kwargs
                field, (fname, content, ctype) = kwargs["files"][0]
                assert field == "files"
                assert ctype == "text/markdown"
                assert b"hello" in content
            assert isinstance(res, IngestResult)
            assert res.embedded_chunks == 2
            assert res.status == "completed"
        finally:
            os.unlink(md_path)

    def test_ingest_replace_kwarg_sends_form_field(self) -> None:
        """``RagIndex.ingest(replace=True)`` posts ``data={"replace": "true"}``.

        Regression test for the dedup fix — keeps the SDK aligned with the
        new ``replace`` form parameter on ``POST /api/v1/rag/indexes/{id}/ingest``.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# v2")
            md_path = f.name
        try:
            uid = "11111111-2222-3333-4444-555555555555"
            idx = RagIndex(uid)
            with patch("agenthub.rag.httpx.Client") as MockClient:
                client_ctx = MockClient.return_value.__enter__.return_value
                client_ctx.post.return_value = _mock_response(
                    {
                        "data": {
                            "id": "j-1",
                            "status": "completed",
                            "total_files": 1,
                            "processed_files": 1,
                            "total_chunks": 1,
                            "embedded_chunks": 1,
                        }
                    }
                )

                # Default: no data field on the POST.
                idx.ingest([md_path])
                _, kwargs = client_ctx.post.call_args
                assert kwargs.get("data") is None

                # replace=True: form field populated.
                idx.ingest([md_path], replace=True)
                _, kwargs = client_ctx.post.call_args
                assert kwargs.get("data") == {"replace": "true"}
        finally:
            os.unlink(md_path)

    def test_ingest_propagates_http_error(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hi")
            path = f.name
        try:
            idx = RagIndex("11111111-2222-3333-4444-555555555555")
            with patch("agenthub.rag.httpx.Client") as MockClient:
                client_ctx = MockClient.return_value.__enter__.return_value
                client_ctx.post.return_value = _mock_response({"error": "boom"}, status_code=500)
                with pytest.raises(RagIndexError, match="500"):
                    idx.ingest([path])
        finally:
            os.unlink(path)


class TestSearch:
    def test_search_posts_body_and_normalizes(self) -> None:
        idx = RagIndex("11111111-2222-3333-4444-555555555555")
        with patch("agenthub.rag.httpx.Client") as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.post.return_value = _mock_response(
                {
                    "data": {
                        "results": [
                            {
                                "score": 0.87,
                                "source": "docs/intro.md",
                                "text": "AgentBreeder builds agents.",
                                "metadata": {"page": 1},
                            }
                        ]
                    }
                }
            )
            hits = idx.search("what is agentbreeder", top_k=3)
            _, kwargs = client_ctx.post.call_args
            assert kwargs["json"] == {
                "index_id": "11111111-2222-3333-4444-555555555555",
                "query": "what is agentbreeder",
                "top_k": 3,
            }
        assert hits[0]["score"] == 0.87
        assert hits[0]["source"] == "docs/intro.md"
        assert "AgentBreeder" in hits[0]["text"]
        assert hits[0]["metadata"] == {"page": 1}

    def test_search_falls_back_to_similarity_field(self) -> None:
        idx = RagIndex("11111111-2222-3333-4444-555555555555")
        with patch("agenthub.rag.httpx.Client") as MockClient:
            client_ctx = MockClient.return_value.__enter__.return_value
            client_ctx.post.return_value = _mock_response(
                {"data": {"results": [{"similarity": 0.5, "content": "x"}]}}
            )
            hits = idx.search("q")
        assert hits[0]["score"] == 0.5
        assert hits[0]["text"] == "x"


def test_allowed_exts_matches_api_allowlist() -> None:
    """Regression: keep SDK's allowlist in sync with api/routes/rag.py."""
    assert ALLOWED_EXTS == {".pdf", ".txt", ".md", ".csv", ".json"}
