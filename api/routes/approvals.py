"""HITL approval queue API endpoints.

Issue #69: Human-in-the-loop approval patterns.

Agents call POST /api/v1/approvals/ to pause and request human sign-off before
executing a high-risk tool. Operators poll GET /api/v1/approvals/?status=pending
and call /{id}/approve or /{id}/reject to unblock the agent.

Note: The in-process dict is a v0 stub. Production should replace _approval_queue
with a Redis-backed queue or database table (see models/ for the migration path).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])

# v0 in-process store — replace with Redis / DB in production
_approval_queue: dict[str, dict] = {}


class ApprovalRequest(BaseModel):
    agent_name: str
    tool_name: str
    tool_args: dict
    requested_by: str
    timeout_minutes: int = 30


class ApprovalResponse(BaseModel):
    approval_id: str
    status: str  # pending | approved | rejected | timed_out
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


@router.post("/", response_model=ApprovalResponse)
async def request_approval(request: ApprovalRequest) -> ApprovalResponse:
    """Submit a tool call for human approval.

    The agent should poll GET /{approval_id} (or subscribe via webhook) and
    block execution until the status transitions out of 'pending'.
    """
    approval_id = str(uuid.uuid4())
    _approval_queue[approval_id] = {
        "approval_id": approval_id,
        "status": "pending",
        "agent_name": request.agent_name,
        "tool_name": request.tool_name,
        "tool_args": request.tool_args,
        "requested_by": request.requested_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "timeout_minutes": request.timeout_minutes,
        "decided_by": None,
        "decided_at": None,
    }
    return ApprovalResponse(
        approval_id=approval_id,
        status="pending",
        agent_name=request.agent_name,
        tool_name=request.tool_name,
    )


@router.get("/", response_model=list[ApprovalResponse])
async def list_approvals(status: Optional[str] = None) -> list[ApprovalResponse]:
    """List approval requests, optionally filtered by status."""
    items = list(_approval_queue.values())
    if status:
        items = [i for i in items if i["status"] == status]
    return [
        ApprovalResponse(
            approval_id=i["approval_id"],
            status=i["status"],
            agent_name=i.get("agent_name"),
            tool_name=i.get("tool_name"),
            decided_by=i.get("decided_by"),
            decided_at=i.get("decided_at"),
        )
        for i in items
    ]


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(approval_id: str) -> ApprovalResponse:
    """Get the current status of an approval request."""
    if approval_id not in _approval_queue:
        raise HTTPException(status_code=404, detail="Approval request not found")
    i = _approval_queue[approval_id]
    return ApprovalResponse(
        approval_id=i["approval_id"],
        status=i["status"],
        agent_name=i.get("agent_name"),
        tool_name=i.get("tool_name"),
        decided_by=i.get("decided_by"),
        decided_at=i.get("decided_at"),
    )


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve(approval_id: str, decided_by: str = "operator") -> ApprovalResponse:
    """Approve a pending tool call, unblocking the agent."""
    if approval_id not in _approval_queue:
        raise HTTPException(status_code=404, detail="Approval request not found")
    entry = _approval_queue[approval_id]
    if entry["status"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"Approval is already '{entry['status']}'"
        )
    entry["status"] = "approved"
    entry["decided_by"] = decided_by
    entry["decided_at"] = datetime.now(timezone.utc).isoformat()
    return ApprovalResponse(
        approval_id=entry["approval_id"],
        status=entry["status"],
        agent_name=entry.get("agent_name"),
        tool_name=entry.get("tool_name"),
        decided_by=entry.get("decided_by"),
        decided_at=entry.get("decided_at"),
    )


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject(approval_id: str, decided_by: str = "operator") -> ApprovalResponse:
    """Reject a pending tool call — the agent will receive a rejection error."""
    if approval_id not in _approval_queue:
        raise HTTPException(status_code=404, detail="Approval request not found")
    entry = _approval_queue[approval_id]
    if entry["status"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"Approval is already '{entry['status']}'"
        )
    entry["status"] = "rejected"
    entry["decided_by"] = decided_by
    entry["decided_at"] = datetime.now(timezone.utc).isoformat()
    return ApprovalResponse(
        approval_id=entry["approval_id"],
        status=entry["status"],
        agent_name=entry.get("agent_name"),
        tool_name=entry.get("tool_name"),
        decided_by=entry.get("decided_by"),
        decided_at=entry.get("decided_at"),
    )
