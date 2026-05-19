"""Tests for api.models._validators."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from api.models._validators import (
    HopsField,
    SeedEntityLimitField,
    TopKField,
    WeightField,
    make_weights_sum_validator,
)


class _M(BaseModel):
    """Anonymous model used by tests below."""

    top_k: TopKField = 10
    hops: HopsField = None
    seed_entity_limit: SeedEntityLimitField = 5
    vector_weight: WeightField = 0.7
    text_weight: WeightField = 0.3

    _check_weights = make_weights_sum_validator("vector_weight", "text_weight")


def test_defaults_valid() -> None:
    m = _M()
    assert m.top_k == 10
    assert m.hops is None
    assert m.seed_entity_limit == 5
    assert m.vector_weight == 0.7
    assert m.text_weight == 0.3


def test_top_k_low_bound() -> None:
    with pytest.raises(ValidationError):
        _M(top_k=0)


def test_top_k_high_bound() -> None:
    with pytest.raises(ValidationError):
        _M(top_k=1_000_000)


def test_hops_low_bound() -> None:
    with pytest.raises(ValidationError):
        _M(hops=-1)


def test_hops_high_bound() -> None:
    with pytest.raises(ValidationError):
        _M(hops=999)


def test_seed_entity_limit_low_bound() -> None:
    with pytest.raises(ValidationError):
        _M(seed_entity_limit=0)


def test_seed_entity_limit_high_bound() -> None:
    with pytest.raises(ValidationError):
        _M(seed_entity_limit=999)


def test_weight_low_bound() -> None:
    with pytest.raises(ValidationError):
        _M(vector_weight=-0.1, text_weight=1.1)


def test_weight_high_bound() -> None:
    with pytest.raises(ValidationError):
        _M(vector_weight=1.5, text_weight=-0.5)


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="must sum to 1.0"):
        _M(vector_weight=1.0, text_weight=1.0)


def test_weights_within_tolerance() -> None:
    m = _M(vector_weight=0.1, text_weight=0.9)
    assert m.vector_weight + m.text_weight == pytest.approx(1.0)
