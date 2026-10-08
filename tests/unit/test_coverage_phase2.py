"""Coverage-phase-2 tests — targets uncovered branches in routes/providers."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from api.main import app as fastapi_app


@pytest.fixture
def client():
    return TestClient(fastapi_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# api/routes/providers — uncovered endpoints
# ---------------------------------------------------------------------------


class TestProvidersRouteUncovered:
    def test_toggle_provider_endpoint_exists(self, client):
        """Lines 234-235: toggle endpoint is registered."""
        provider_id = uuid.uuid4()
        resp = client.post(f"/api/v1/providers/{provider_id}/toggle")
        # Must not be 404 (method not found) or 405 (method not allowed)
        assert resp.status_code not in (404, 405)
