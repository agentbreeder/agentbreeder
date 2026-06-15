"""Analytics ingest + funnel aggregation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_ingest_rejects_overlong_event(client):
    # Body validation (event too long) fails before the route body / DB.
    r = client.post("/api/v1/analytics/events", json={"event": "x" * 100})
    assert r.status_code == 422


def test_ingest_rejects_unknown_event(client):
    # Well-formed but not on the server-side allowlist -> 422 before DB.
    r = client.post("/api/v1/analytics/events", json={"event": "totally_made_up"})
    assert r.status_code == 422


def test_ingest_rejects_bad_session_id(client):
    # Malformed UUID is rejected by pydantic (422) rather than 500 in the route.
    r = client.post(
        "/api/v1/analytics/events",
        json={"event": "spec_validated", "session_id": "not-a-uuid"},
    )
    assert r.status_code == 422


@pytest.mark.no_auto_auth
def test_funnel_requires_auth(client):
    # Real auth runs (autouse mock disabled); no token -> 401/403 before DB.
    r = client.get("/api/v1/analytics/funnel?period=7d")
    assert r.status_code in (401, 403)
