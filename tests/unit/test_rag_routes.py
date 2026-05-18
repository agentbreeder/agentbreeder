"""Validation tests for the RagSearchRequest model used by POST /api/v1/rag/search."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.models.schemas import RagSearchRequest


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
