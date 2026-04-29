"""Provider API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.database import Model, User
from api.models.enums import KeyScopeType, ProviderStatus, ProviderType
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    DiscoveredModel,
    LiteLLMKeyCreate,
    LiteLLMKeyCreateResponse,
    LiteLLMKeyResponse,
    ModelDiscoveryResult,
    OllamaDetectResult,
    ProviderCreate,
    ProviderHealthCheckResult,
    ProviderResponse,
    ProviderStatusSummary,
    ProviderTestResult,
    ProviderUpdate,
)
from api.services import litellm_key_service
from api.tasks.provider_health import check_all_providers
from registry.providers import ProviderRegistry

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])


@router.get("/status", response_model=ApiResponse[ProviderStatusSummary])
async def provider_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[ProviderStatusSummary]:
    """First-run detection: returns provider/model counts for the dashboard."""
    from sqlalchemy import func, select

    providers, total_providers = await ProviderRegistry.list(db, per_page=1)
    model_count_result = await db.execute(
        select(func.count()).select_from(Model).where(Model.status == "active")
    )
    total_models = model_count_result.scalar() or 0

    return ApiResponse(
        data=ProviderStatusSummary(
            has_providers=total_providers > 0,
            provider_count=total_providers,
            total_models=total_models,
        )
    )


@router.post("/health-check", response_model=ApiResponse[list[ProviderHealthCheckResult]])
async def health_check(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ProviderHealthCheckResult]]:
    """Trigger health check for all providers and return updated statuses."""
    results = await check_all_providers(db)
    return ApiResponse(
        data=[ProviderHealthCheckResult(**r) for r in results],
    )


@router.post("/detect-ollama", response_model=ApiResponse[OllamaDetectResult])
async def detect_ollama(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[OllamaDetectResult]:
    """Auto-detect local Ollama instance and register it as a provider."""
    existing = await ProviderRegistry.get_by_type(db, ProviderType.ollama)
    created = False

    if existing:
        provider = existing
    else:
        provider = await ProviderRegistry.create(
            db,
            name="Ollama (local)",
            provider_type=ProviderType.ollama,
            base_url="http://localhost:11434",
        )
        created = True

    raw_models = await ProviderRegistry.discover_models(db, provider)
    await ProviderRegistry.auto_register_models(db, provider, raw_models)
    discovered = [DiscoveredModel(**m) for m in raw_models]

    return ApiResponse(
        data=OllamaDetectResult(
            provider=ProviderResponse.model_validate(provider),
            models=discovered,
            created=created,
        )
    )


@router.get("/catalog", response_model=ApiResponse[list[dict]])
async def list_catalog_providers(
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[dict]]:
    """List built-in + user-local OpenAI-compatible catalog presets.

    Used by the dashboard ``/models`` page to show presets with a Configure
    button. Each entry is read-only — adding a user-local entry happens via the
    CLI for now (``agentbreeder provider add … --type openai_compatible``).

    See ``engine/providers/catalog.yaml`` for the source of truth.
    """
    from engine.providers.catalog import list_entries

    entries = list_entries()
    payload = [
        {
            "name": name,
            "type": entry.type,
            "base_url": str(entry.base_url),
            "api_key_env": entry.api_key_env,
            "default_headers": entry.default_headers,
            "docs": str(entry.docs) if entry.docs else None,
            "discovery": entry.discovery,
            "notable_models": entry.notable_models,
            "source": entry.source,
        }
        for name, entry in sorted(entries.items())
    ]
    return ApiResponse(data=payload)


@router.get("/catalog/status", response_model=ApiResponse[dict[str, bool]])
async def catalog_status(
    workspace: str | None = Query(None),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict[str, bool]]:
    """Per-catalog-provider configuration status (issue #175).

    Returns ``{<provider_name>: <bool>}`` indicating whether the provider's
    api-key secret has been set in the workspace's configured secrets
    backend. The dashboard ``/models`` page uses this to render a green
    "Configured" badge or a "Configure" CTA on each row.

    The lookup checks for *both* the deterministic dashboard key
    (``<provider>/api-key``) and the legacy env-var name
    (``<entry.api_key_env>``) so that secrets imported via the CLI before
    Track K landed are still recognised as configured.
    """
    from engine.providers.catalog import list_entries
    from engine.secrets.factory import get_workspace_backend

    entries = list_entries()
    backend, _ws = get_workspace_backend(workspace=workspace)
    existing_names = {e.name for e in await backend.list()}

    statuses: dict[str, bool] = {}
    for name, entry in entries.items():
        dashboard_key = f"{name}/api-key"
        env_key = entry.api_key_env
        statuses[name] = dashboard_key in existing_names or env_key in existing_names

    return ApiResponse(data=statuses)


@router.get("", response_model=ApiResponse[list[ProviderResponse]])
async def list_providers(
    provider_type: ProviderType | None = Query(None),
    status: ProviderStatus | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[ProviderResponse]]:
    """List registered providers."""
    providers, total = await ProviderRegistry.list(
        db, provider_type=provider_type, status=status, page=page, per_page=per_page
    )
    return ApiResponse(
        data=[ProviderResponse.model_validate(p) for p in providers],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/{provider_id}", response_model=ApiResponse[ProviderResponse])
async def get_provider(
    provider_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[ProviderResponse]:
    """Get provider details by ID."""
    provider = await ProviderRegistry.get(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    return ApiResponse(data=ProviderResponse.model_validate(provider))


@router.post("", response_model=ApiResponse[ProviderResponse], status_code=201)
async def create_provider(
    body: ProviderCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProviderResponse]:
    """Register a new provider."""
    provider = await ProviderRegistry.create(
        db,
        name=body.name,
        provider_type=body.provider_type,
        base_url=body.base_url,
        config=body.config,
    )
    return ApiResponse(data=ProviderResponse.model_validate(provider))


@router.put("/{provider_id}", response_model=ApiResponse[ProviderResponse])
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProviderResponse]:
    """Update a provider configuration."""
    provider = await ProviderRegistry.get(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    provider = await ProviderRegistry.update(
        db,
        provider,
        name=body.name,
        base_url=body.base_url,
        status=body.status,
        config=body.config,
    )
    return ApiResponse(data=ProviderResponse.model_validate(provider))


@router.delete("/{provider_id}", response_model=ApiResponse[dict])
async def delete_provider(
    provider_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Delete a provider."""
    provider = await ProviderRegistry.get(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    name = provider.name
    await ProviderRegistry.delete(db, provider)
    return ApiResponse(data={"message": f"Provider '{name}' deleted"})


@router.post("/{provider_id}/test", response_model=ApiResponse[ProviderTestResult])
async def test_provider(
    provider_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProviderTestResult]:
    """Test a provider connection."""
    provider = await ProviderRegistry.get(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    result = await ProviderRegistry.test_connection(db, provider)
    return ApiResponse(data=ProviderTestResult(**result))


@router.post("/{provider_id}/discover", response_model=ApiResponse[ModelDiscoveryResult])
async def discover_models(
    provider_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ModelDiscoveryResult]:
    """Discover available models from a provider."""
    provider = await ProviderRegistry.get(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    raw_models = await ProviderRegistry.discover_models(db, provider)
    await ProviderRegistry.auto_register_models(db, provider, raw_models)
    discovered = [DiscoveredModel(**m) for m in raw_models]
    return ApiResponse(
        data=ModelDiscoveryResult(
            provider_id=provider.id,
            provider_type=provider.provider_type,
            models=discovered,
            total=len(discovered),
        )
    )


@router.post("/{provider_id}/toggle", response_model=ApiResponse[ProviderResponse])
async def toggle_provider(
    provider_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ProviderResponse]:
    """Enable or disable a provider (toggle is_enabled)."""
    provider = await ProviderRegistry.get(db, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    provider = await ProviderRegistry.toggle(db, provider)
    return ApiResponse(data=ProviderResponse.model_validate(provider))


# ---------------------------------------------------------------------------
# LiteLLM Virtual Key endpoints  (prefix: /api/v1/providers/litellm/keys)
# ---------------------------------------------------------------------------

_keys_router = APIRouter(prefix="/litellm/keys", tags=["providers"])


@_keys_router.post("", response_model=ApiResponse[LiteLLMKeyCreateResponse])
async def create_litellm_key(
    body: LiteLLMKeyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteLLMKeyCreateResponse]:
    """Generate a new LiteLLM virtual key scoped to an org, team, user, agent, or service principal."""
    result = await litellm_key_service.create_key(db, body, created_by=user.email)
    return ApiResponse(data=result)


@_keys_router.get("", response_model=ApiResponse[list[LiteLLMKeyResponse]])
async def list_litellm_keys(
    scope_type: KeyScopeType | None = Query(None),
    team_id: str | None = Query(None),
    agent_name: str | None = Query(None),
    active_only: bool = Query(True),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[LiteLLMKeyResponse]]:
    """List virtual keys with optional filters."""
    keys = await litellm_key_service.list_keys(
        db,
        scope_type=scope_type,
        team_id=team_id,
        agent_name=agent_name,
        active_only=active_only,
    )
    return ApiResponse(data=keys, meta={"page": 1, "per_page": len(keys), "total": len(keys)})


@_keys_router.delete("/{key_alias}", response_model=ApiResponse[dict])
async def revoke_litellm_key(
    key_alias: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Revoke a virtual key — deletes from LiteLLM and marks inactive in AgentBreeder."""
    ok = await litellm_key_service.revoke_key(db, key_alias)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found")
    return ApiResponse(data={"revoked": True, "key_alias": key_alias})


# Mount sub-router onto main providers router
router.include_router(_keys_router)
