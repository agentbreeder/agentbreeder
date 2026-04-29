"""Secrets management API (Track K).

Read-only at the workspace level: the dashboard never sees secret *values*,
only names + metadata + mirror destinations. Mutating endpoints (rotate,
sync) require an authenticated user; the actual value is never returned to
the client — it must be supplied as request body and is forwarded to the
backend.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.models.database import User
from api.models.schemas import ApiMeta, ApiResponse
from engine.secrets.factory import SUPPORTED_BACKENDS, get_workspace_backend

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/secrets", tags=["secrets"])


# ── schemas ─────────────────────────────────────────────────────────────────


class SecretSummary(BaseModel):
    name: str
    masked_value: str
    backend: str
    workspace: str
    updated_at: str | None = None
    mirror_destinations: list[str] = Field(default_factory=list)


class WorkspaceBackendInfo(BaseModel):
    workspace: str
    backend: str
    supported_backends: list[str]


class RotateRequest(BaseModel):
    new_value: str = Field(..., min_length=1)


class SyncRequest(BaseModel):
    target: str
    secret_names: list[str] | None = None


# ── routes ──────────────────────────────────────────────────────────────────


@router.get("/workspace", response_model=ApiResponse[WorkspaceBackendInfo])
async def get_workspace_info(
    workspace: str | None = Query(None),
    _user: User = Depends(get_current_user),
) -> ApiResponse[WorkspaceBackendInfo]:
    """Return the workspace's configured backend and supported backend list."""
    backend, ws_cfg = get_workspace_backend(workspace=workspace)
    return ApiResponse(
        data=WorkspaceBackendInfo(
            workspace=ws_cfg.workspace,
            backend=backend.backend_name,
            supported_backends=list(SUPPORTED_BACKENDS),
        )
    )


@router.get("", response_model=ApiResponse[list[SecretSummary]])
async def list_secrets(
    workspace: str | None = Query(None),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[SecretSummary]]:
    """List all secrets in the workspace (names + masked metadata only)."""
    backend, ws_cfg = get_workspace_backend(workspace=workspace)
    entries = await backend.list()
    summaries = [
        SecretSummary(
            name=e.name,
            masked_value=e.masked_value,
            backend=e.backend,
            workspace=ws_cfg.workspace,
            updated_at=e.updated_at.isoformat() if e.updated_at else None,
            mirror_destinations=[],
        )
        for e in entries
    ]
    return ApiResponse(data=summaries, meta=ApiMeta(total=len(summaries)))


@router.post("/{name}/rotate", response_model=ApiResponse[SecretSummary])
async def rotate_secret(
    name: str,
    body: RotateRequest,
    workspace: str | None = Query(None),
    _user: User = Depends(get_current_user),
) -> ApiResponse[SecretSummary]:
    """Rotate a secret to a new value (the value never leaves the request body)."""
    backend, ws_cfg = get_workspace_backend(workspace=workspace)
    try:
        await backend.rotate(name, body.new_value)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Audit
    try:
        from api.services.audit_service import AuditService

        await AuditService.log_event(
            actor=_user.email,
            action="secret.rotated",
            resource_type="secret",
            resource_name=name,
            details={"workspace": ws_cfg.workspace, "backend": backend.backend_name},
        )
    except Exception as exc:  # pragma: no cover - audit is best-effort
        logger.debug("audit emit failed for secret.rotated: %s", exc)

    entries = {e.name: e for e in await backend.list()}
    entry = entries.get(name)
    summary = SecretSummary(
        name=name,
        masked_value=entry.masked_value if entry else "••••",
        backend=backend.backend_name,
        workspace=ws_cfg.workspace,
        updated_at=entry.updated_at.isoformat() if (entry and entry.updated_at) else None,
    )
    return ApiResponse(data=summary)
