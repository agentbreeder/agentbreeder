"""Agent API — v2 (preview).

Changes from v1:
- Cursor-based pagination (``cursor`` + ``limit``) replaces ``page``/``page_size``
- Response envelope includes ``request_id`` and ``api_version``
- ``GET /api/v2/agents`` returns ``next_cursor`` for iteration
- ``POST /api/v2/agents/batch`` for bulk registration (new in v2)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.database import Agent, User
from api.models.enums import AgentStatus
from api.models.schemas import AgentCreate, AgentResponse

router = APIRouter(prefix="/api/v2/agents", tags=["agents-v2"])


def _v2_envelope(data: object, *, next_cursor: str | None = None) -> dict:
    """Wrap data in the v2 response envelope."""
    meta: dict = {"api_version": "v2", "request_id": str(uuid.uuid4())}
    if next_cursor is not None:
        meta["next_cursor"] = next_cursor
    return {"data": data, "meta": meta, "errors": []}


@router.get("")
async def list_agents_v2(
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, description="Opaque cursor from previous response"),
    team: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List agents with cursor-based pagination.

    Returns up to ``limit`` agents. If more results exist, ``meta.next_cursor``
    is set — pass it as ``cursor`` in the next request.
    """
    from sqlalchemy import select

    stmt = select(Agent).where(Agent.status != AgentStatus.failed).order_by(Agent.created_at)

    # Cursor decode — cursor encodes the created_at timestamp of the last seen item
    if cursor:
        try:
            from datetime import datetime

            boundary = datetime.fromisoformat(cursor)
            stmt = stmt.where(Agent.created_at > boundary)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor value") from exc

    if team:
        stmt = stmt.where(Agent.team == team)

    stmt = stmt.limit(limit + 1)  # fetch one extra to detect next page

    result = await db.execute(stmt)
    agents = result.scalars().all()

    next_cursor: str | None = None
    if len(agents) > limit:
        agents = agents[:limit]
        next_cursor = agents[-1].created_at.isoformat()

    return _v2_envelope(
        [AgentResponse.model_validate(a) for a in agents],
        next_cursor=next_cursor,
    )


@router.get("/{agent_id}")
async def get_agent_v2(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get a single agent by ID."""
    from sqlalchemy import select

    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _v2_envelope(AgentResponse.model_validate(agent))


@router.post("/batch")
async def batch_register_agents_v2(
    agents: list[AgentCreate],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Batch-register multiple agents in a single request (new in v2).

    Returns a list of results — each entry has either ``data`` (the created
    agent) or ``error`` (validation/conflict message) so partial success is
    handled gracefully.
    """
    if len(agents) > 50:
        raise HTTPException(status_code=400, detail="Batch size limit is 50 agents")

    results = []
    for agent_create in agents:
        try:
            from registry.agents import AgentRegistry

            registry = AgentRegistry(db)
            created = await registry.create(agent_create, created_by=current_user.id)
            results.append({"data": AgentResponse.model_validate(created), "error": None})
        except Exception as exc:
            results.append({"data": None, "error": str(exc)})

    await db.commit()
    return _v2_envelope(results)
