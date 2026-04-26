"""Unit tests for the HITL approval queue API.

Issue #69: Human-in-the-loop approval patterns.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from api.database import get_redis
from api.main import app


@pytest.fixture(autouse=True)
def _fake_redis():
    """Inject a fresh in-memory FakeRedis for every test — no real Redis needed."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _override():
        yield fake

    app.dependency_overrides[get_redis] = _override
    yield fake
    app.dependency_overrides.pop(get_redis, None)


client = TestClient(app)

_BASE_REQUEST = {
    "agent_name": "data-analyst",
    "tool_name": "delete_records",
    "tool_args": {"table": "users", "where": "id = 42"},
    "requested_by": "agent-runner",
}


def _create_approval(**overrides) -> dict:
    """Helper: POST a new approval request and return the JSON response."""
    payload = {**_BASE_REQUEST, **overrides}
    resp = client.post("/api/v1/approvals/", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/
# ---------------------------------------------------------------------------


def test_request_approval_returns_pending() -> None:
    data = _create_approval()
    assert data["status"] == "pending"
    assert "approval_id" in data
    assert data["agent_name"] == "data-analyst"
    assert data["tool_name"] == "delete_records"


def test_request_approval_unique_ids() -> None:
    """Each approval request should get a unique ID."""
    id1 = _create_approval()["approval_id"]
    id2 = _create_approval()["approval_id"]
    assert id1 != id2


# ---------------------------------------------------------------------------
# GET /api/v1/approvals/
# ---------------------------------------------------------------------------


def test_list_approvals_returns_list() -> None:
    resp = client.get("/api/v1/approvals/")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_approvals_filtered_by_status() -> None:
    # Create one and approve it so we have mixed statuses
    data = _create_approval(tool_name="filtered_tool")
    approval_id = data["approval_id"]
    client.post(f"/api/v1/approvals/{approval_id}/approve?decided_by=tester")

    pending = client.get("/api/v1/approvals/?status=pending").json()
    approved = client.get("/api/v1/approvals/?status=approved").json()

    assert all(a["status"] == "pending" for a in pending)
    assert all(a["status"] == "approved" for a in approved)
    assert any(a["approval_id"] == approval_id for a in approved)


# ---------------------------------------------------------------------------
# GET /api/v1/approvals/{approval_id}
# ---------------------------------------------------------------------------


def test_get_approval_by_id() -> None:
    approval_id = _create_approval()["approval_id"]
    resp = client.get(f"/api/v1/approvals/{approval_id}")
    assert resp.status_code == 200
    assert resp.json()["approval_id"] == approval_id


def test_get_approval_not_found() -> None:
    resp = client.get("/api/v1/approvals/nonexistent-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/{approval_id}/approve
# ---------------------------------------------------------------------------


def test_approve_request() -> None:
    approval_id = _create_approval()["approval_id"]
    resp = client.post(f"/api/v1/approvals/{approval_id}/approve?decided_by=operator")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["decided_by"] == "operator"
    assert data["decided_at"] is not None


def test_approve_nonexistent_request() -> None:
    resp = client.post("/api/v1/approvals/nonexistent/approve")
    assert resp.status_code == 404


def test_approve_already_decided_returns_409() -> None:
    approval_id = _create_approval()["approval_id"]
    client.post(f"/api/v1/approvals/{approval_id}/approve?decided_by=op1")
    resp = client.post(f"/api/v1/approvals/{approval_id}/approve?decided_by=op2")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/{approval_id}/reject
# ---------------------------------------------------------------------------


def test_reject_request() -> None:
    approval_id = _create_approval()["approval_id"]
    resp = client.post(f"/api/v1/approvals/{approval_id}/reject?decided_by=security-team")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert data["decided_by"] == "security-team"
    assert data["decided_at"] is not None


def test_reject_nonexistent_request() -> None:
    resp = client.post("/api/v1/approvals/nonexistent/reject")
    assert resp.status_code == 404


def test_reject_already_decided_returns_409() -> None:
    approval_id = _create_approval()["approval_id"]
    client.post(f"/api/v1/approvals/{approval_id}/reject?decided_by=op1")
    resp = client.post(f"/api/v1/approvals/{approval_id}/reject?decided_by=op2")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Cross-state transitions
# ---------------------------------------------------------------------------


def test_cannot_approve_rejected_request() -> None:
    approval_id = _create_approval()["approval_id"]
    client.post(f"/api/v1/approvals/{approval_id}/reject")
    resp = client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert resp.status_code == 409


def test_cannot_reject_approved_request() -> None:
    approval_id = _create_approval()["approval_id"]
    client.post(f"/api/v1/approvals/{approval_id}/approve")
    resp = client.post(f"/api/v1/approvals/{approval_id}/reject")
    assert resp.status_code == 409
