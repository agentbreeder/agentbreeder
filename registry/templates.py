"""Template & Marketplace registry service — the ONLY place that writes to marketplace tables."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.database import ListingReview, MarketplaceListing, Template
from api.models.enums import ListingStatus, TemplateCategory, TemplateStatus

logger = logging.getLogger(__name__)


class TemplateRegistry:
    """Service class for template CRUD operations."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        name: str,
        version: str,
        description: str,
        category: TemplateCategory,
        framework: str,
        config_template: dict[str, Any],
        parameters: list[dict[str, Any]],
        tags: list[str],
        author: str,
        team: str,
        readme: str = "",
    ) -> Template:
        """Create a new template."""
        template = Template(
            name=name,
            version=version,
            description=description,
            category=category,
            framework=framework,
            config_template=config_template,
            parameters=parameters,
            tags=tags,
            author=author,
            team=team,
            readme=readme,
        )
        session.add(template)
        await session.flush()
        logger.info("Created template '%s' v%s", name, version)
        return template

    @staticmethod
    async def get_by_id(session: AsyncSession, template_id: uuid.UUID) -> Template | None:
        stmt = select(Template).where(Template.id == template_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_name(session: AsyncSession, name: str) -> Template | None:
        stmt = select(Template).where(Template.name == name)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list(
        session: AsyncSession,
        *,
        category: TemplateCategory | None = None,
        framework: str | None = None,
        status: TemplateStatus | None = None,
        team: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Template], int]:
        stmt = select(Template)
        if category:
            stmt = stmt.where(Template.category == category)
        if framework:
            stmt = stmt.where(Template.framework == framework)
        if status:
            stmt = stmt.where(Template.status == status)
        if team:
            stmt = stmt.where(Template.team == team)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Template.created_at.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(stmt)
        return list(result.scalars().all()), total

    @staticmethod
    async def update(
        session: AsyncSession,
        template_id: uuid.UUID,
        **kwargs: Any,
    ) -> Template | None:
        stmt = select(Template).where(Template.id == template_id)
        result = await session.execute(stmt)
        template = result.scalar_one_or_none()
        if not template:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(template, key):
                setattr(template, key, value)
        await session.flush()
        logger.info("Updated template '%s'", template.name)
        return template

    @staticmethod
    async def delete(session: AsyncSession, template_id: uuid.UUID) -> bool:
        stmt = select(Template).where(Template.id == template_id)
        result = await session.execute(stmt)
        template = result.scalar_one_or_none()
        if template:
            await session.delete(template)
            await session.flush()
            logger.info("Deleted template '%s'", template.name)
            return True
        return False

    @staticmethod
    async def increment_use_count(session: AsyncSession, template_id: uuid.UUID) -> None:
        stmt = select(Template).where(Template.id == template_id)
        result = await session.execute(stmt)
        template = result.scalar_one_or_none()
        if template:
            template.use_count += 1
            await session.flush()

    @staticmethod
    async def search(
        session: AsyncSession,
        query: str,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Template], int]:
        pattern = f"%{query}%"
        stmt = select(Template).where(
            or_(
                Template.name.ilike(pattern),
                Template.description.ilike(pattern),
                Template.framework.ilike(pattern),
            )
        )
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0
        stmt = stmt.order_by(Template.created_at.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(stmt)
        return list(result.scalars().all()), total


class MarketplaceRegistry:
    """Service class for marketplace listing CRUD operations."""

    @staticmethod
    async def submit_listing(
        session: AsyncSession,
        *,
        template_id: uuid.UUID,
        submitted_by: str,
    ) -> MarketplaceListing:
        listing = MarketplaceListing(
            template_id=template_id,
            submitted_by=submitted_by,
            status=ListingStatus.pending,
        )
        session.add(listing)
        await session.flush()
        logger.info("Submitted listing for template %s", template_id)
        return listing

    @staticmethod
    async def get_by_id(session: AsyncSession, listing_id: uuid.UUID) -> MarketplaceListing | None:
        stmt = (
            select(MarketplaceListing)
            .options(selectinload(MarketplaceListing.template))
            .where(MarketplaceListing.id == listing_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def approve(
        session: AsyncSession, listing_id: uuid.UUID, reviewed_by: str
    ) -> MarketplaceListing | None:
        stmt = select(MarketplaceListing).where(MarketplaceListing.id == listing_id)
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()
        if not listing:
            return None
        listing.status = ListingStatus.approved
        listing.reviewed_by = reviewed_by
        listing.published_at = datetime.now(UTC)

        # Also mark the template as published
        t_stmt = select(Template).where(Template.id == listing.template_id)
        t_result = await session.execute(t_stmt)
        template = t_result.scalar_one_or_none()
        if template:
            template.status = TemplateStatus.published

        await session.flush()
        logger.info("Approved listing %s", listing_id)
        return listing

    @staticmethod
    async def reject(
        session: AsyncSession,
        listing_id: uuid.UUID,
        reviewed_by: str,
        reason: str,
    ) -> MarketplaceListing | None:
        stmt = select(MarketplaceListing).where(MarketplaceListing.id == listing_id)
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()
        if not listing:
            return None
        listing.status = ListingStatus.rejected
        listing.reviewed_by = reviewed_by
        listing.reject_reason = reason
        await session.flush()
        logger.info("Rejected listing %s", listing_id)
        return listing

    @staticmethod
    async def browse(
        session: AsyncSession,
        *,
        category: TemplateCategory | None = None,
        framework: str | None = None,
        query: str | None = None,
        featured: bool | None = None,
        sort_by: str = "rating",
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[MarketplaceListing], int]:
        """Browse approved marketplace listings."""
        stmt = (
            select(MarketplaceListing)
            .options(selectinload(MarketplaceListing.template))
            .join(Template)
            .where(MarketplaceListing.status == ListingStatus.approved)
        )

        if category:
            stmt = stmt.where(Template.category == category)
        if framework:
            stmt = stmt.where(Template.framework == framework)
        if featured is not None:
            stmt = stmt.where(MarketplaceListing.featured == featured)
        if query:
            pattern = f"%{query}%"
            stmt = stmt.where(
                or_(
                    Template.name.ilike(pattern),
                    Template.description.ilike(pattern),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        if sort_by == "rating":
            stmt = stmt.order_by(MarketplaceListing.avg_rating.desc())
        elif sort_by == "installs":
            stmt = stmt.order_by(MarketplaceListing.install_count.desc())
        elif sort_by == "newest":
            stmt = stmt.order_by(MarketplaceListing.published_at.desc())
        else:
            stmt = stmt.order_by(MarketplaceListing.avg_rating.desc())

        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(stmt)
        return list(result.scalars().all()), total

    @staticmethod
    async def add_review(
        session: AsyncSession,
        *,
        listing_id: uuid.UUID,
        reviewer: str,
        rating: int,
        comment: str = "",
    ) -> ListingReview:
        review = ListingReview(
            listing_id=listing_id,
            reviewer=reviewer,
            rating=rating,
            comment=comment,
        )
        session.add(review)
        await session.flush()

        # Update avg_rating and review_count on the listing
        stmt = select(MarketplaceListing).where(MarketplaceListing.id == listing_id)
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()
        if listing:
            avg_stmt = select(func.avg(ListingReview.rating)).where(
                ListingReview.listing_id == listing_id
            )
            avg = (await session.execute(avg_stmt)).scalar() or 0.0
            count_stmt = select(func.count()).where(ListingReview.listing_id == listing_id)
            count = (await session.execute(count_stmt)).scalar() or 0
            listing.avg_rating = float(avg)
            listing.review_count = count
            await session.flush()

        logger.info("Added review for listing %s (rating: %d)", listing_id, rating)
        return review

    @staticmethod
    async def get_reviews(
        session: AsyncSession,
        listing_id: uuid.UUID,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ListingReview], int]:
        stmt = select(ListingReview).where(ListingReview.listing_id == listing_id)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0
        stmt = stmt.order_by(ListingReview.created_at.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(stmt)
        return list(result.scalars().all()), total

    @staticmethod
    async def increment_install_count(session: AsyncSession, listing_id: uuid.UUID) -> None:
        stmt = select(MarketplaceListing).where(MarketplaceListing.id == listing_id)
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()
        if listing:
            listing.install_count += 1
            await session.flush()
