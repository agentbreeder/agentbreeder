"""Secrets management API (Track K).

The dashboard never sees secret *values*, only names + metadata + mirror
destinations. Mutating endpoints (create, rotate, sync) require an
authenticated user with at least the ``deployer`` role; the actual value
is never returned to the client — it must be supplied as request body and
is forwarded to the configured workspace backend.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.middleware.rbac import require_role
from api.models.database import User
from api.models.schemas import ApiMeta, ApiResponse
from engine.secrets.factory import SUPPORTED_BACKENDS, _instantiate, get_workspace_backend
from engine.secrets.workspace import save_workspace_secrets_config

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


class CreateSecretRequest(BaseModel):
    """Body schema for ``POST /api/v1/secrets``.

    ``backend`` is optional — when omitted, the workspace's configured
    backend (resolved via ``get_workspace_backend``) is used. When provided
    explicitly, it must be one of :data:`SUPPORTED_BACKENDS`.
    """

    name: str = Field(..., min_length=1, max_length=255)
    value: str = Field(..., min_length=1)
    backend: str | None = Field(default=None)


class SyncRequest(BaseModel):
    target: str
    secret_names: list[str] | None = None


class SetBackendRequest(BaseModel):
    """Body schema for ``PUT /api/v1/secrets/workspace``.

    Switches the workspace's configured secrets backend. ``options`` is the
    backend-specific kwargs block (e.g. ``{"region": "us-east-1"}`` for AWS).
    """

    backend: str = Field(..., min_length=1)
    options: dict[str, str] | None = Field(default=None)


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


@router.put("/workspace", response_model=ApiResponse[WorkspaceBackendInfo])
async def set_workspace_backend(
    body: SetBackendRequest,
    workspace: str | None = Query(None),
    user: User = Depends(require_role("admin")),
) -> ApiResponse[WorkspaceBackendInfo]:
    """Switch the workspace's configured secrets backend.

    Persists the choice to ``~/.agentbreeder/workspace.yaml`` (the same file
    consulted by ``load_workspace_secrets_config``). Existing secrets in the
    *previous* backend are NOT migrated automatically — operators should
    re-mirror or re-set them under the new backend. Admin role required.
    """
    if body.backend not in SUPPORTED_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported backend '{body.backend}'. "
                f"Must be one of: {', '.join(SUPPORTED_BACKENDS)}"
            ),
        )

    # Validate that the new backend can actually be instantiated before we
    # persist the change — surfaces ImportError for missing optional deps
    # (e.g. boto3) instead of silently breaking the workspace.
    try:
        _instantiate(body.backend, dict(body.options or {}))
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot initialize '{body.backend}' backend: {exc}",
        ) from exc

    saved = save_workspace_secrets_config(
        backend=body.backend,
        options=body.options or {},
        workspace=workspace,
    )

    try:
        from api.services.audit_service import AuditService

        await AuditService.log_event(
            actor=user.email,
            action="secret.backend_changed",
            resource_type="workspace",
            resource_name=saved.workspace,
            details={"backend": saved.backend, "options": saved.options},
        )
    except Exception as exc:  # pragma: no cover - audit is best-effort
        logger.debug("audit emit failed for secret.backend_changed: %s", exc)

    return ApiResponse(
        data=WorkspaceBackendInfo(
            workspace=saved.workspace,
            backend=saved.backend,
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


@router.post("", response_model=ApiResponse[SecretSummary], status_code=201)
async def create_secret(
    body: CreateSecretRequest,
    workspace: str | None = Query(None),
    user: User = Depends(require_role("deployer")),
) -> ApiResponse[SecretSummary]:
    """Create (or update) a secret in the configured workspace backend.

    Used by the dashboard ``/models`` Configure modal (issue #175) to wire
    a freshly-typed API key into the workspace secrets store. Values never
    round-trip back to the client — only a masked summary is returned.

    Requires the ``deployer`` role; viewers receive 403.
    """
    if body.backend is not None and body.backend not in SUPPORTED_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported backend '{body.backend}'. "
                f"Must be one of: {', '.join(SUPPORTED_BACKENDS)}"
            ),
        )

    if body.backend is None:
        # Use the workspace's configured backend (the common case).
        backend, ws_cfg = get_workspace_backend(workspace=workspace)
    else:
        # Caller pinned a specific backend (e.g. "env" from the dashboard
        # Configure modal). Wrap it in the workspace context so the
        # response carries the workspace name.
        _, ws_cfg = get_workspace_backend(workspace=workspace)
        backend = _instantiate(body.backend, {})

    try:
        await backend.set(body.name, body.value)
    except Exception as exc:  # backend errors are surfaced as 500
        logger.error("secret.create failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to write secret: {exc}") from exc

    # Audit
    try:
        from api.services.audit_service import AuditService

        await AuditService.log_event(
            actor=user.email,
            action="secret.created",
            resource_type="secret",
            resource_name=body.name,
            details={"workspace": ws_cfg.workspace, "backend": backend.backend_name},
        )
    except Exception as exc:  # pragma: no cover - audit is best-effort
        logger.debug("audit emit failed for secret.created: %s", exc)

    entries = {e.name: e for e in await backend.list()}
    entry = entries.get(body.name)
    summary = SecretSummary(
        name=body.name,
        masked_value=entry.masked_value if entry else "••••",
        backend=backend.backend_name,
        workspace=ws_cfg.workspace,
        updated_at=entry.updated_at.isoformat() if (entry and entry.updated_at) else None,
    )
    return ApiResponse(data=summary)


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
