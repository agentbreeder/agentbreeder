"""Marketplace routes — /api/v1/marketplace."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.database import User
from api.models.enums import TemplateCategory
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    ListingReviewCreate,
    ListingReviewResponse,
    MarketplaceBrowseItem,
    MarketplaceListingCreate,
    MarketplaceListingResponse,
    MarketplaceListingUpdate,
)
from registry.templates import MarketplaceRegistry, TemplateRegistry

router = APIRouter(prefix="/api/v1/marketplace", tags=["marketplace"])


@router.get("/browse", response_model=ApiResponse[list[MarketplaceBrowseItem]])
async def browse_marketplace(
    category: TemplateCategory | None = Query(None),
    framework: str | None = Query(None),
    q: str | None = Query(None),
    featured: bool | None = Query(None),
    sort: str = Query("rating", pattern="^(rating|installs|newest)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[MarketplaceBrowseItem]]:
    listings, total = await MarketplaceRegistry.browse(
        db,
        category=category,
        framework=framework,
        query=q,
        featured=featured,
        sort_by=sort,
        page=page,
        per_page=per_page,
    )
    items = []
    for listing in listings:
        t = listing.template
        if t:
            items.append(
                MarketplaceBrowseItem(
                    listing_id=listing.id,
                    template_id=t.id,
                    name=t.name,
                    description=t.description,
                    category=t.category,
                    framework=t.framework,
                    tags=t.tags or [],
                    author=t.author,
                    avg_rating=listing.avg_rating,
                    review_count=listing.review_count,
                    install_count=listing.install_count,
                    featured=listing.featured,
                    published_at=listing.published_at,
                )
            )
    return ApiResponse(
        data=items,
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.post("/listings", response_model=ApiResponse[MarketplaceListingResponse], status_code=201)
async def submit_listing(
    body: MarketplaceListingCreate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MarketplaceListingResponse]:
    """Submit a template for marketplace listing (requires admin approval)."""
    template = await TemplateRegistry.get_by_id(db, body.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    listing = await MarketplaceRegistry.submit_listing(
        db, template_id=body.template_id, submitted_by=body.submitted_by
    )
    await db.commit()
    return ApiResponse(data=MarketplaceListingResponse.model_validate(listing))


@router.get("/listings/{listing_id}", response_model=ApiResponse[MarketplaceListingResponse])
async def get_listing(
    listing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[MarketplaceListingResponse]:
    listing = await MarketplaceRegistry.get_by_id(db, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    data = MarketplaceListingResponse.model_validate(listing)
    if listing.template:
        from api.models.schemas import TemplateResponse

        data.template = TemplateResponse.model_validate(listing.template)
    return ApiResponse(data=data)


@router.put("/listings/{listing_id}", response_model=ApiResponse[MarketplaceListingResponse])
async def update_listing(
    listing_id: uuid.UUID,
    body: MarketplaceListingUpdate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MarketplaceListingResponse]:
    listing = await MarketplaceRegistry.get_by_id(db, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if body.status and body.status.value == "approved" and body.reviewed_by:
        listing = await MarketplaceRegistry.approve(db, listing_id, body.reviewed_by)
    elif body.status and body.status.value == "rejected" and body.reviewed_by:
        listing = await MarketplaceRegistry.reject(
            db, listing_id, body.reviewed_by, body.reject_reason or ""
        )
    else:
        # Generic update for featured, etc.
        if body.featured is not None:
            listing.featured = body.featured
        await db.flush()

    await db.commit()
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return ApiResponse(data=MarketplaceListingResponse.model_validate(listing))


@router.post(
    "/listings/{listing_id}/reviews",
    response_model=ApiResponse[ListingReviewResponse],
    status_code=201,
)
async def add_review(
    listing_id: uuid.UUID,
    body: ListingReviewCreate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ListingReviewResponse]:
    listing = await MarketplaceRegistry.get_by_id(db, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    review = await MarketplaceRegistry.add_review(
        db,
        listing_id=listing_id,
        reviewer=body.reviewer,
        rating=body.rating,
        comment=body.comment,
    )
    await db.commit()
    return ApiResponse(data=ListingReviewResponse.model_validate(review))


@router.get(
    "/listings/{listing_id}/reviews",
    response_model=ApiResponse[list[ListingReviewResponse]],
)
async def list_reviews(
    listing_id: uuid.UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[ListingReviewResponse]]:
    reviews, total = await MarketplaceRegistry.get_reviews(
        db, listing_id, page=page, per_page=per_page
    )
    return ApiResponse(
        data=[ListingReviewResponse.model_validate(r) for r in reviews],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.post("/listings/{listing_id}/install")
async def install_from_listing(
    listing_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict[str, bool]]:
    """Increment install count (called after one-click deploy)."""
    await MarketplaceRegistry.increment_install_count(db, listing_id)
    await db.commit()
    return ApiResponse(data={"installed": True})
