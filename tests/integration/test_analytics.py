"""Analytics ingest + funnel aggregation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_ingest_rejects_unknown_event(client):
    # Body validation (event too long) fails before the route body / DB.
    r = client.post("/api/v1/analytics/events", json={"event": "x" * 100})
    assert r.status_code == 422


@pytest.mark.no_auto_auth
def test_funnel_requires_auth(client):
    # Real auth runs (autouse mock disabled); no token -> 401/403 before DB.
    r = client.get("/api/v1/analytics/funnel?period=7d")
    assert r.status_code in (401, 403)
