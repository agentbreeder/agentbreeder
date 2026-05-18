"""Validation tests for the RagSearchRequest model used by POST /api/v1/rag/search.

Also covers W4-23 — 100 MB upload cap on POST /api/v1/rag/indexes/{id}/ingest.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.main import app
from api.models.schemas import RagSearchRequest
from api.services.auth import create_access_token


def test_valid_request_parses() -> None:
    req = RagSearchRequest(index_id="abc", query="hello")
    assert req.top_k == 10
    assert req.vector_weight == 0.7
    assert req.text_weight == 0.3
    assert req.hops is None
    assert req.seed_entity_limit == 5


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="must sum to 1.0"):
        RagSearchRequest(index_id="abc", query="hi", vector_weight=1.0, text_weight=1.0)


def test_weights_tiny_float_drift_allowed() -> None:
    req = RagSearchRequest(index_id="abc", query="hi", vector_weight=0.1, text_weight=0.9)
    assert req.vector_weight + req.text_weight == pytest.approx(1.0)


def test_top_k_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", top_k=0)


def test_top_k_capped_at_thousand() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", top_k=10_000)


def test_negative_hops_rejected() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", hops=-1)


def test_hops_capped_at_ten() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", hops=99)


def test_seed_entity_limit_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", seed_entity_limit=0)


def test_seed_entity_limit_capped_at_fifty() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", seed_entity_limit=100)


def test_individual_weight_bounds() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="hi", vector_weight=1.5, text_weight=0.0)


def test_query_required() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="abc", query="")


def test_index_id_required() -> None:
    with pytest.raises(ValidationError):
        RagSearchRequest(index_id="", query="hi")


# ---------------------------------------------------------------------------
# W4-23 — 100 MB upload-size cap on ingest endpoint
# ---------------------------------------------------------------------------


_client = TestClient(app)


def _deployer_headers() -> dict[str, str]:
    token = create_access_token(str(uuid.uuid4()), "test@test.com", "deployer")
    return {"Authorization": f"Bearer {token}"}


def test_max_upload_size_constants_are_defined() -> None:
    """MAX_UPLOAD_SIZE_MB = 100 and MAX_UPLOAD_SIZE_BYTES = 100*1024*1024."""
    from api.routes import rag

    assert rag.MAX_UPLOAD_SIZE_MB == 100
    assert rag.MAX_UPLOAD_SIZE_BYTES == 100 * 1024 * 1024


@patch("api.routes.rag.get_rag_store")
def test_ingest_rejects_file_over_100mb_with_413(mock_gs) -> None:
    """A single file exceeding MAX_UPLOAD_SIZE_BYTES is rejected with 413."""
    from api.routes import rag

    store = MagicMock()
    store.get_index.return_value = MagicMock(id="idx-1")
    mock_gs.return_value = store

    oversize = b"x" * (rag.MAX_UPLOAD_SIZE_BYTES + 1)
    resp = _client.post(
        "/api/v1/rag/indexes/idx-1/ingest",
        files={"files": ("huge.txt", oversize, "text/plain")},
        headers=_deployer_headers(),
    )
    assert resp.status_code == 413
    detail = resp.json().get("detail", "")
    assert "100 MB" in detail
    assert "huge.txt" in detail
    # Critically — store.ingest_files must NOT have been called.
    store.ingest_files.assert_not_called()


@patch("api.routes.rag.get_rag_store")
def test_ingest_accepts_file_under_100mb(mock_gs) -> None:
    """A small file passes the size gate and reaches the ingest call."""
    from unittest.mock import AsyncMock

    store = MagicMock()
    store.get_index.return_value = MagicMock(id="idx-1")
    fake_job = MagicMock()
    fake_job.to_dict.return_value = {"id": "job-1", "status": "completed"}
    store.ingest_files = AsyncMock(return_value=fake_job)
    mock_gs.return_value = store

    small = b"hello world"
    resp = _client.post(
        "/api/v1/rag/indexes/idx-1/ingest",
        files={"files": ("tiny.txt", small, "text/plain")},
        headers=_deployer_headers(),
    )
    assert resp.status_code == 200
    store.ingest_files.assert_awaited_once()
