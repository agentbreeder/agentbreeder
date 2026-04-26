"""HITL approval queue API endpoints.

Issue #69: Human-in-the-loop approval patterns.

Agents call POST /api/v1/approvals/ to pause and request human sign-off before
executing a high-risk tool. Operators poll GET /api/v1/approvals/?status=pending
and call /{id}/approve or /{id}/reject to unblock the agent.

Storage: each approval is a Redis hash at key ``approval:{approval_id}``.
Active IDs are tracked in the Redis set ``approvals:all``.
Pending approvals expire after ``timeout_minutes`` seconds; decided approvals
are kept for 24 h so the decision remains readable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user
from api.database import get_redis
from api.middleware.rbac import require_role
from api.models.database import User

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])

# TTL constants
_PENDING_TTL = 1800  # 30 minutes default; overridden per-request by timeout_minutes
_DECIDED_TTL = 86400  # 24 hours — keep the decision readable after it is made


class ApprovalRequest(BaseModel):
    agent_name: str
    tool_name: str
    tool_args: dict
    requested_by: str
    timeout_minutes: int = 30


class ApprovalResponse(BaseModel):
    approval_id: str
    status: str  # pending | approved | rejected | timed_out
    agent_name: str | None = None
    tool_name: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _redis_key(approval_id: str) -> str:
    return f"approval:{approval_id}"


def _build_response(data: dict) -> ApprovalResponse:
    """Convert a Redis hash (all strings) back to an ApprovalResponse."""
    decided_at: datetime | None = None
    raw_decided_at = data.get("decided_at")
    if raw_decided_at:
        decided_at = datetime.fromisoformat(raw_decided_at)
    return ApprovalResponse(
        approval_id=data["approval_id"],
        status=data["status"],
        agent_name=data.get("agent_name") or None,
        tool_name=data.get("tool_name") or None,
        decided_by=data.get("decided_by") or None,
        decided_at=decided_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/", response_model=ApprovalResponse)
async def request_approval(
    request: ApprovalRequest,
    _user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
) -> ApprovalResponse:
    """Submit a tool call for human approval.

    The agent should poll GET /{approval_id} (or subscribe via webhook) and
    block execution until the status transitions out of 'pending'.
    """
    approval_id = str(uuid.uuid4())
    key = _redis_key(approval_id)
    ttl_seconds = request.timeout_minutes * 60

    mapping: dict[str, str] = {
        "approval_id": approval_id,
        "status": "pending",
        "agent_name": request.agent_name,
        "tool_name": request.tool_name,
        "tool_args": str(request.tool_args),
        "requested_by": request.requested_by,
        "created_at": datetime.now(UTC).isoformat(),
        "timeout_minutes": str(request.timeout_minutes),
        "decided_by": "",
        "decided_at": "",
    }

    await redis.hset(key, mapping=mapping)
    await redis.expire(key, ttl_seconds)
    await redis.sadd("approvals:all", approval_id)

    return ApprovalResponse(
        approval_id=approval_id,
        status="pending",
        agent_name=request.agent_name,
        tool_name=request.tool_name,
    )


@router.get("/", response_model=list[ApprovalResponse])
async def list_approvals(
    status: str | None = None,
    _user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
) -> list[ApprovalResponse]:
    """List approval requests, optionally filtered by status.

    IDs whose TTL has expired are silently pruned from the tracking set.
    """
    all_ids: set[str] = await redis.smembers("approvals:all")
    results: list[ApprovalResponse] = []
    expired_ids: list[str] = []

    for approval_id in all_ids:
        key = _redis_key(approval_id)
        data: dict[str, str] = await redis.hgetall(key)
        if not data:
            # Key expired — clean up the tracking set lazily
            expired_ids.append(approval_id)
            continue
        if status and data.get("status") != status:
            continue
        results.append(_build_response(data))

    if expired_ids:
        await redis.srem("approvals:all", *expired_ids)

    return results


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: str,
    _user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis),
) -> ApprovalResponse:
    """Get the current status of an approval request."""
    key = _redis_key(approval_id)
    data: dict[str, str] = await redis.hgetall(key)
    if not data:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return _build_response(data)


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve(
    approval_id: str,
    _user: User = Depends(require_role("admin")),
    decided_by: str = "operator",
    redis: aioredis.Redis = Depends(get_redis),
) -> ApprovalResponse:
    """Approve a pending tool call, unblocking the agent."""
    key = _redis_key(approval_id)
    data: dict[str, str] = await redis.hgetall(key)
    if not data:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if data["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Approval is already '{data['status']}'")

    updates = {
        "status": "approved",
        "decided_by": decided_by,
        "decided_at": datetime.now(UTC).isoformat(),
    }
    await redis.hset(key, mapping=updates)
    await redis.expire(key, _DECIDED_TTL)

    data.update(updates)
    return _build_response(data)


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject(
    approval_id: str,
    _user: User = Depends(require_role("admin")),
    decided_by: str = "operator",
    redis: aioredis.Redis = Depends(get_redis),
) -> ApprovalResponse:
    """Reject a pending tool call — the agent will receive a rejection error."""
    key = _redis_key(approval_id)
    data: dict[str, str] = await redis.hgetall(key)
    if not data:
        raise HTTPException(status_code=404, detail="Approval request not found")
    if data["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Approval is already '{data['status']}'")

    updates = {
        "status": "rejected",
        "decided_by": decided_by,
        "decided_at": datetime.now(UTC).isoformat(),
    }
    await redis.hset(key, mapping=updates)
    await redis.expire(key, _DECIDED_TTL)

    data.update(updates)
    return _build_response(data)
