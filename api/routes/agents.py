"""Agent API routes."""

from __future__ import annotations

import os
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.database import Agent, User
from api.models.enums import AgentStatus
from api.models.schemas import (
    AgentCloneRequest,
    AgentCreate,
    AgentInvokeRequest,
    AgentInvokeResponse,
    AgentResponse,
    AgentUpdate,
    AgentValidationErrorItem,
    AgentValidationResponse,
    AgentYamlRequest,
    ApiMeta,
    ApiResponse,
)
from registry.agents import AgentRegistry, create_from_yaml, validate_config_yaml

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


async def _enforce_acl(
    db: AsyncSession,
    user_email: str,
    resource_id: uuid.UUID,
    action: str,
) -> None:
    """Check ACL for an agent. Raises 403 if explicitly denied.

    Passes silently if: no ACL rows exist, DB unavailable, or permission granted.
    """
    try:
        from sqlalchemy import select

        from api.models.database import ResourcePermission
        from api.services.rbac_service import check_permission

        allowed, reason = await check_permission(
            db,
            user_email=user_email,
            resource_type="agent",
            resource_id=resource_id,
            action=action,
        )
        result = await db.execute(
            select(ResourcePermission)
            .where(
                ResourcePermission.resource_type == "agent",
                ResourcePermission.resource_id == resource_id,
            )
            .limit(1)
        )
        has_acl = result.scalar_one_or_none() is not None
        if has_acl and not allowed:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail=f"Access denied: {reason}")
    except Exception as exc:
        if "403" in str(exc) or "Access denied" in str(exc):
            raise
        # DB unavailable or table not yet migrated — allow access
        pass


@router.get("", response_model=ApiResponse[list[AgentResponse]])
async def list_agents(
    team: str | None = Query(None),
    framework: str | None = Query(None),
    status: AgentStatus | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[AgentResponse]]:
    """List agents from the registry."""
    agents, total = await AgentRegistry.list(
        db, team=team, framework=framework, status=status, page=page, per_page=per_page
    )
    return ApiResponse(
        data=[AgentResponse.model_validate(a) for a in agents],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.post("/validate", response_model=ApiResponse[AgentValidationResponse])
async def validate_agent_yaml(
    body: AgentYamlRequest,
    _user: User = Depends(get_current_user),
) -> ApiResponse[AgentValidationResponse]:
    """Validate raw YAML against the agent schema, returning errors and warnings."""
    result = validate_config_yaml(body.yaml_content)
    return ApiResponse(
        data=AgentValidationResponse(
            valid=result.valid,
            errors=[
                AgentValidationErrorItem(path=e.path, message=e.message, suggestion=e.suggestion)
                for e in result.errors
            ],
            warnings=[
                AgentValidationErrorItem(path=w.path, message=w.message, suggestion=w.suggestion)
                for w in result.warnings
            ],
        )
    )


@router.post("/from-yaml", response_model=ApiResponse[AgentResponse], status_code=201)
async def create_agent_from_yaml(
    body: AgentYamlRequest,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[AgentResponse]:
    """Parse YAML and create/update an agent in the registry."""
    try:
        agent = await create_from_yaml(db, body.yaml_content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=AgentResponse.model_validate(agent))


@router.get("/search", response_model=ApiResponse[list[AgentResponse]])
async def search_agents(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[AgentResponse]]:
    """Search agents by name, description, team, or framework."""
    agents, total = await AgentRegistry.search(db, query=q, page=page, per_page=per_page)
    return ApiResponse(
        data=[AgentResponse.model_validate(a) for a in agents],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/{agent_id}", response_model=ApiResponse[AgentResponse])
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ApiResponse[AgentResponse]:
    """Get agent details by ID.

    Enforces ACL if the current user is authenticated: user must have 'read'
    permission on this agent (or no ACL row exists, which allows open access).
    """
    agent = await AgentRegistry.get_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # ACL enforcement (soft: only blocks if explicit deny rows exist)
    if user is not None:
        await _enforce_acl(db, user.email, agent_id, "read")

    return ApiResponse(data=AgentResponse.model_validate(agent))


@router.post("", response_model=ApiResponse[AgentResponse], status_code=201)
async def create_agent(
    body: AgentCreate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[AgentResponse]:
    """Manually register an agent in the registry (upsert by name)."""
    from engine.config_parser import AgentConfig, FrameworkType

    # Build a minimal AgentConfig for registry
    config = AgentConfig(
        name=body.name,
        version=body.version,
        description=body.description,
        team=body.team,
        owner=body.owner,
        framework=FrameworkType(body.framework),
        model={"primary": body.model_primary, "fallback": body.model_fallback},
        deploy={"cloud": "local"},
        tags=body.tags,
    )
    # AgentRegistry.register performs an upsert, so duplicate names update
    # the existing record rather than raising a DB constraint violation.
    agent = await AgentRegistry.register(db, config, endpoint_url=body.endpoint_url or "")
    await db.commit()
    await db.refresh(agent)
    return ApiResponse(data=AgentResponse.model_validate(agent))


@router.put("/{agent_id}", response_model=ApiResponse[AgentResponse])
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[AgentResponse]:
    """Update an agent's metadata."""
    agent = await AgentRegistry.get_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # ACL enforcement — requires 'write' permission
    await _enforce_acl(db, user.email, agent_id, "write")

    if body.version is not None:
        agent.version = body.version
    if body.description is not None:
        agent.description = body.description
    if body.endpoint_url is not None:
        agent.endpoint_url = body.endpoint_url
    if body.status is not None:
        agent.status = body.status
    if body.tags is not None:
        agent.tags = body.tags

    await db.commit()
    await db.refresh(agent)
    return ApiResponse(data=AgentResponse.model_validate(agent))


@router.post("/{agent_id}/clone", response_model=ApiResponse[AgentResponse], status_code=201)
async def clone_agent(
    agent_id: uuid.UUID,
    body: AgentCloneRequest,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[AgentResponse]:
    """Clone an agent, creating a copy with a new name and version."""
    source = await AgentRegistry.get_by_id(db, agent_id)
    if not source:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check if an agent with the new name already exists
    existing = await AgentRegistry.get(db, body.name)
    if existing:
        raise HTTPException(
            status_code=409, detail=f"Agent with name '{body.name}' already exists"
        )

    cloned = Agent(
        name=body.name,
        version=body.version,
        description=source.description,
        team=source.team,
        owner=source.owner,
        framework=source.framework,
        model_primary=source.model_primary,
        model_fallback=source.model_fallback,
        endpoint_url=None,
        status=AgentStatus.stopped,
        tags=list(source.tags),
        config_snapshot=dict(source.config_snapshot),
    )
    db.add(cloned)
    await db.flush()
    return ApiResponse(data=AgentResponse.model_validate(cloned))


@router.post("/{agent_id}/invoke", response_model=ApiResponse[AgentInvokeResponse])
async def invoke_agent(
    agent_id: uuid.UUID,
    body: AgentInvokeRequest,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[AgentInvokeResponse]:
    """Proxy a chat invocation through to the agent's deployed runtime.

    Resolves the target endpoint in this order: ``body.endpoint_url`` →
    ``agent.endpoint_url`` (from the registry record). Uses the bearer token
    from ``body.auth_token`` if provided, falling back to the env var
    ``AGENT_<UPPER_SNAKE>_TOKEN`` (so callers can keep the secret server-side).

    The request is POSTed to ``<endpoint>/invoke`` with the standard
    InvokeRequest body shape that all framework runtime templates accept.
    """
    agent = await AgentRegistry.get_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    endpoint = (body.endpoint_url or agent.endpoint_url or "").rstrip("/")
    if not endpoint:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Agent '{agent.name}' has no endpoint_url and the request did "
                "not provide one. Set endpoint_url on the agent record or pass "
                "it in the request body."
            ),
        )

    token = body.auth_token
    if not token:
        env_var = "AGENT_" + agent.name.upper().replace("-", "_") + "_TOKEN"
        token = os.environ.get(env_var, "").strip() or None

    payload: dict = {"input": body.input}
    if body.session_id:
        payload["session_id"] = body.session_id
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{endpoint}/invoke", json=payload, headers=headers)
        duration_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code >= 400:
            return ApiResponse(
                data=AgentInvokeResponse(
                    output="",
                    duration_ms=duration_ms,
                    status_code=resp.status_code,
                    error=resp.text[:2000],
                )
            )
        data = resp.json()
        return ApiResponse(
            data=AgentInvokeResponse(
                output=data.get("output", ""),
                session_id=data.get("session_id"),
                duration_ms=duration_ms,
                status_code=resp.status_code,
            )
        )
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return ApiResponse(
            data=AgentInvokeResponse(
                output="",
                duration_ms=int((time.perf_counter() - started) * 1000),
                status_code=0,
                error=f"{type(exc).__name__}: {exc}",
            )
        )


@router.delete("/{agent_id}", response_model=ApiResponse[dict])
async def delete_agent(
    agent_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Soft-delete (archive) an agent."""
    agent = await AgentRegistry.get_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # ACL enforcement — requires 'admin' permission to delete
    await _enforce_acl(db, user.email, agent_id, "admin")

    agent.status = AgentStatus.stopped
    await db.flush()
    return ApiResponse(data={"message": f"Agent '{agent.name}' archived"})
