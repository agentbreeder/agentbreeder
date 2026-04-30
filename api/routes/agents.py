"""Agent API routes."""

from __future__ import annotations

import logging
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
    AgentInvokeToolCall,
    AgentResponse,
    AgentUpdate,
    AgentValidationErrorItem,
    AgentValidationResponse,
    AgentYamlRequest,
    ApiMeta,
    ApiResponse,
)
from registry.agents import AgentRegistry, create_from_yaml, validate_config_yaml

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


def _agent_auth_token_secret_name(agent_name: str) -> str:
    """Return the deterministic secret name for an agent's auth token.

    Format: ``agentbreeder/<agent-name>/auth-token``. The dashboard's Invoke
    panel relies on this convention so the API can resolve the token
    server-side and the user never has to paste it.
    """
    return f"agentbreeder/{agent_name}/auth-token"


async def _resolve_agent_auth_token(agent_name: str) -> str | None:
    """Look up an agent's ``AGENT_AUTH_TOKEN`` from the workspace secrets backend.

    Returns the token string if the secret exists, or ``None`` if the secret
    is missing or the backend lookup fails. Failures are logged but never
    raised — the caller falls through to "no token" and the runtime will
    return 401 if it requires auth.
    """
    secret_name = _agent_auth_token_secret_name(agent_name)
    try:
        from engine.secrets.factory import get_workspace_backend

        backend, _ws = get_workspace_backend()
        value = await backend.get(secret_name)
        if value:
            return value.strip() or None
        return None
    except Exception as exc:  # noqa: BLE001 — secrets backend errors must not 500 the proxy
        logger.warning(
            "Failed to resolve auth token from workspace secrets",
            extra={"agent": agent_name, "secret": secret_name, "error": str(exc)},
        )
        return None


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
        # DB unavailable or table not yet migrated — allow access. Roll
        # back so the asyncpg connection isn't left mid-transaction
        # (otherwise the *next* await on this session would fail with
        # "another operation is in progress").
        try:
            await db.rollback()
        except Exception:  # pragma: no cover — rollback on already-bad session
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
    ``agent.endpoint_url`` (from the registry record).

    The bearer token is resolved server-side in this order:

    1. ``body.auth_token`` — explicit override (kept for power users / tests).
    2. The workspace secrets backend, keyed by
       ``agentbreeder/<agent-name>/auth-token`` — set by
       ``agentbreeder secret set <agent>/auth-token``.
    3. The legacy env var ``AGENT_<UPPER_SNAKE>_TOKEN`` (deprecated; kept for
       back-compat with installs that pre-date Track K).

    If none resolve we proxy without an ``Authorization`` header and let the
    runtime return 401 — that surfaces a clear "set the secret" message to
    the caller without leaking 500s on missing secrets.

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

    # 1. Explicit override from request body (kept for backwards-compat).
    token = (body.auth_token or "").strip() or None
    # 2. Workspace secrets backend (post-Track K).
    if not token:
        token = await _resolve_agent_auth_token(agent.name)
    # 3. Legacy env var (deprecated).
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
        # Forward the structured tool-call history from the runtime (#215).
        # Each runtime template emits ``history: list[ToolCall]`` with the
        # same shape; we coerce defensively so a malformed entry doesn't
        # 500 the proxy.
        raw_history = data.get("history") or []
        history: list[AgentInvokeToolCall] = []
        if isinstance(raw_history, list):
            for entry in raw_history:
                if not isinstance(entry, dict):
                    continue
                try:
                    history.append(AgentInvokeToolCall(**entry))
                except Exception:  # noqa: BLE001 — tolerate partial shapes
                    history.append(
                        AgentInvokeToolCall(
                            name=str(entry.get("name", "") or ""),
                            args=entry.get("args", {}) or {},
                            result=str(entry.get("result", "") or ""),
                        )
                    )
        return ApiResponse(
            data=AgentInvokeResponse(
                output=data.get("output", ""),
                session_id=data.get("session_id"),
                duration_ms=duration_ms,
                status_code=resp.status_code,
                history=history,
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


@router.get("/{agent_id}/versions", response_model=ApiResponse[list[dict]])
async def list_agent_versions(
    agent_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[dict]]:
    """Return the agent's version history (newest-first).

    Each entry exposes ``version``, ``config_yaml``, ``created_by``, and
    ``created_at`` so the dashboard's Versions / Compare panels can show
    real history instead of MOCK_VERSIONS (#210).
    """
    agent = await AgentRegistry.get_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    versions = await AgentRegistry.list_versions(db, agent_id)
    payload = [
        {
            "id": str(v.id),
            "version": v.version,
            "config_yaml": v.config_yaml,
            "config_snapshot": v.config_snapshot,
            "created_by": v.created_by,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in versions
    ]
    return ApiResponse(data=payload, meta=ApiMeta(total=len(payload)))


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
