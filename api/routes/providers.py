"""Provider API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.database import User
from api.models.enums import ProviderStatus, ProviderType
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    DiscoveredModel,
    ModelDiscoveryResult,
    ProviderCreate,
    ProviderResponse,
    ProviderTestResult,
    ProviderUpdate,
)
from registry.providers import ProviderRegistry

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])


@router.get("", response_model=ApiResponse[list[ProviderResponse]])
async def list_providers(
    provider_type: ProviderType | None = Query(None),
    status: ProviderStatus | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
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
