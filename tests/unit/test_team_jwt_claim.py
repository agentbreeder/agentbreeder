"""Tests for `team_from_jwt` — JWT `current_tenant_slug` claim extraction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt as pyjwt

from api.auth import team_from_jwt
from api.config import settings


def _sign(payload: dict, secret: str | None = None) -> str:
    return pyjwt.encode(
        payload, secret or settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def test_extracts_slug_from_valid_jwt():
    token = _sign(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "current_tenant_slug": "acme-corp",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
    )
    assert team_from_jwt(f"Bearer {token}") == "acme-corp"


def test_missing_header_returns_none():
    assert team_from_jwt(None) is None


def test_non_bearer_header_returns_none():
    assert team_from_jwt("Basic abc") is None


def test_jwt_without_claim_returns_none():
    token = _sign(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
    )
    assert team_from_jwt(f"Bearer {token}") is None


def test_invalid_signature_returns_none():
    token = _sign(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "current_tenant_slug": "x",
            "exp": datetime.now(UTC) + timedelta(hours=1),
        },
        secret="a-completely-different-secret-key",
    )
    assert team_from_jwt(f"Bearer {token}") is None


def test_expired_token_returns_none():
    token = _sign(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "current_tenant_slug": "x",
            "exp": datetime.now(UTC) - timedelta(hours=1),
        }
    )
    assert team_from_jwt(f"Bearer {token}") is None


def test_non_string_claim_returns_none():
    token = _sign(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "current_tenant_slug": 123,
            "exp": datetime.now(UTC) + timedelta(hours=1),
        }
    )
    assert team_from_jwt(f"Bearer {token}") is None
