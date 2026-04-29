"""Model lifecycle API routes — Track G (#163).

Adds endpoints on top of the existing ``/api/v1/models`` (which lives in
``api/routes/registry.py`` for historical reasons — it serves model
*reads*). This module owns the *write* paths that mutate model lifecycle:

* ``POST /api/v1/models/sync``       — discover models from configured
  providers and reconcile the registry. Deployer-gated.
* ``POST /api/v1/models/{name}/deprecate`` — operator override; mark a
  model deprecated, optionally pointing at a replacement. Deployer-gated.

Both endpoints are auth-gated via :func:`api.middleware.rbac.require_role`.
The Track F catalog + provider tables are read-only here; we never mutate
secrets or provider configs from this route.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.middleware.rbac import require_role
from api.models.database import Provider, User
from api.models.schemas import ApiMeta, ApiResponse, ModelResponse
from engine.providers.catalog import list_entries
from engine.providers.discovery import (
    DiscoveryError,
    ProviderDiscovery,
    get_discovery,
)
from registry.model_lifecycle import ModelLifecycleService, SyncResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/models", tags=["models"])


# ─── Request / response shapes ─────────────────────────────────────────────


class ModelSyncRequest(BaseModel):
    """Body for ``POST /api/v1/models/sync``.

    Empty body → sync every configured provider (catalog presets that have
    an api-key set in the environment + first-class providers like
    Anthropic/Google/OpenAI).
    """

    providers: list[str] = Field(
        default_factory=list,
        description=(
            "Optional list of provider names to sync. When empty, every "
            "provider that has an api-key configured is synced."
        ),
    )


class ModelDeprecateRequest(BaseModel):
    """Body for ``POST /api/v1/models/{name}/deprecate``."""

    replacement: str | None = Field(
        default=None,
        description="Optional name of the model that supersedes this one.",
    )


class SyncResponse(BaseModel):
    """Flat shape returned by ``/sync`` — wraps :class:`SyncResult`."""

    started_at: str
    finished_at: str
    duration_seconds: float
    providers: list[dict]
    totals: dict[str, int]


# ─── Helpers ───────────────────────────────────────────────────────────────


# First-class providers that ship with hand-written ``ProviderBase`` impls.
_FIRST_CLASS = ("anthropic", "google", "openai")


async def _build_discoveries(
    db: AsyncSession,
    requested: list[str],
) -> dict[str, ProviderDiscovery]:
    """Build the discovery adapters for the providers we should sync.

    Resolution order:

    1. If ``requested`` is non-empty, only include the listed names (must
       resolve via :func:`get_discovery`).
    2. Otherwise: include every catalog preset that has its ``api_key_env``
       set in the environment, plus first-class providers (Anthropic, Google,
       OpenAI) when their well-known env var is set, plus any
       ``providers`` row that has a ``base_url`` configured.
    """
    discoveries: dict[str, ProviderDiscovery] = {}
    if requested:
        for name in requested:
            try:
                discoveries[name] = get_discovery(name)
            except DiscoveryError as exc:
                logger.warning("Skipping provider %s: %s", name, exc)
        return discoveries

    # Catalog presets — only when the api-key env var is present so we don't
    # generate noisy 401 errors during the sync.
    for name, entry in list_entries().items():
        if os.environ.get(entry.api_key_env):
            try:
                discoveries[name] = get_discovery(name, catalog_entry=entry)
            except DiscoveryError as exc:
                logger.debug("Catalog provider %s skipped: %s", name, exc)

    for name in _FIRST_CLASS:
        env_var = {
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "openai": "OPENAI_API_KEY",
        }[name]
        # Anthropic uses a curated list and doesn't strictly need the key for
        # discovery, but we still gate on the env var to mirror operator intent.
        if name == "anthropic" or os.environ.get(env_var):
            try:
                discoveries[name] = get_discovery(name)
            except DiscoveryError as exc:
                logger.debug("First-class provider %s skipped: %s", name, exc)

    # Providers configured in the DB — pick up custom base_urls.
    from sqlalchemy import select

    rows = await db.execute(select(Provider).where(Provider.is_enabled.is_(True)))
    for prov in rows.scalars().all():
        # Skip if we already have a discovery for this name.
        if prov.name in discoveries:
            continue
        try:
            discoveries[prov.name] = get_discovery(prov.name, base_url=prov.base_url or None)
        except DiscoveryError:
            continue
    return discoveries


# ─── Routes ────────────────────────────────────────────────────────────────


@router.get("", response_model=ApiResponse[list[ModelResponse]])
async def list_models(
    _user: User = Depends(get_current_user),
    provider: str | None = Query(None),
    status: str | None = Query(
        None,
        description="Filter by lifecycle status (active|beta|deprecated|retired).",
    ),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ModelResponse]]:
    """List models with their lifecycle status.

    Differs from the legacy ``/api/v1/registry/models`` endpoint in two ways:

    * Filters by ``status`` (lifecycle), not ``source``.
    * Returns deprecated + retired rows by default — the legacy endpoint
      filters them out.

    The dashboard ``/models`` page reads from this endpoint going forward.
    """
    from sqlalchemy import func, select

    from api.models.database import Model as ModelOrm

    stmt = select(ModelOrm)
    if provider:
        stmt = stmt.where(ModelOrm.provider == provider)
    if status:
        stmt = stmt.where(ModelOrm.status == status)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0
    stmt = stmt.order_by(ModelOrm.name).offset((page - 1) * per_page).limit(per_page)
    rows = list((await db.execute(stmt)).scalars().all())
    return ApiResponse(
        data=[ModelResponse.model_validate(m) for m in rows],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.post("/sync", response_model=ApiResponse[SyncResponse])
async def sync_models(
    body: ModelSyncRequest = Body(default_factory=ModelSyncRequest),
    user: User = Depends(require_role("deployer")),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[SyncResponse]:
    """Discover models from each configured provider and reconcile the registry.

    See :class:`registry.model_lifecycle.ModelLifecycleService` for the diff
    rules. Audit events ``model.added`` / ``model.deprecated`` /
    ``model.retired`` are emitted as side-effects.
    """
    discoveries = await _build_discoveries(db, body.providers)
    if not discoveries:
        raise HTTPException(
            status_code=400,
            detail=(
                "No providers to sync. Configure an api-key (e.g. set "
                "OPENAI_API_KEY) or pass an explicit list in the request body."
            ),
        )

    actor = getattr(user, "email", None) or "system"
    service = ModelLifecycleService(actor=actor)
    result: SyncResult = await service.sync(db, discoveries=discoveries)
    await db.commit()
    logger.info(
        "Model sync done: providers=%d added=%d deprecated=%d retired=%d",
        len(result.providers),
        result.total_added,
        result.total_deprecated,
        result.total_retired,
    )
    return ApiResponse(data=SyncResponse(**result.as_dict()))


@router.post("/{name}/deprecate", response_model=ApiResponse[dict])
async def deprecate_model(
    name: str,
    body: ModelDeprecateRequest = Body(default_factory=ModelDeprecateRequest),
    user: User = Depends(require_role("deployer")),
    db: AsyncSession = Depends(get_db),
    _: bool = Query(False, include_in_schema=False),
) -> ApiResponse[dict]:
    """Manually deprecate a model (operator override).

    The replacement, if given, must already exist in the registry. The route
    sets ``status="deprecated"``, ``deprecated_at = now`` and emits a
    ``model.deprecated`` audit event with ``reason="manual"``.
    """
    actor = getattr(user, "email", None) or "system"
    service = ModelLifecycleService(actor=actor)
    try:
        model = await service.deprecate(
            db,
            model_name=name,
            replacement_name=body.replacement,
            actor=actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return ApiResponse(
        data={
            "id": str(model.id),
            "name": model.name,
            "status": model.status,
            "deprecated_at": model.deprecated_at.isoformat() if model.deprecated_at else None,
            "replacement": body.replacement,
        }
    )


__all__ = ["router"]
