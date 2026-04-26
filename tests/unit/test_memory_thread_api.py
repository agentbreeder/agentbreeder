"""Unit tests for memory thread convenience endpoints.

Tests GET /api/v1/memory/thread/{thread_id} and
POST /api/v1/memory/thread — used by @agentbreeder/aps-client.

Auth is handled automatically by the conftest _auto_auth fixture which
patches get_current_user to return a default admin for all unit tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/v1/memory/thread/{thread_id}
# ---------------------------------------------------------------------------


def test_get_thread_returns_empty_for_unknown_thread() -> None:
    """Returns 200 with empty data list when no configs exist (thread not found)."""
    with patch(
        "api.services.memory_service.MemoryService.list_configs",
        new_callable=AsyncMock,
        return_value=([], 0),
    ):
        resp = client.get("/api/v1/memory/thread/unknown-thread")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []


# ---------------------------------------------------------------------------
# POST /api/v1/memory/thread
# ---------------------------------------------------------------------------


def test_save_thread_returns_404_when_no_configs() -> None:
    """Returns 404 when there are no memory configs to save messages into."""
    with patch(
        "api.services.memory_service.MemoryService.list_configs",
        new_callable=AsyncMock,
        return_value=([], 0),
    ):
        resp = client.post(
            "/api/v1/memory/thread",
            json={"thread_id": "t1", "messages": []},
        )

    assert resp.status_code == 404
    body = resp.json()
    assert "No memory config" in body["detail"]
