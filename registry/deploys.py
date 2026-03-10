"""Deploy job registry service — manages deployment jobs."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.database import DeployJob
from api.models.enums import DeployJobStatus

logger = logging.getLogger(__name__)


class DeployRegistry:
    """Service class for deploy job CRUD operations."""

    @staticmethod
    async def list(
        session: AsyncSession,
        agent_id: uuid.UUID | None = None,
        status: DeployJobStatus | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[DeployJob], int]:
        """List deploy jobs with optional filters."""
        base = select(DeployJob)

        if agent_id:
            base = base.where(DeployJob.agent_id == agent_id)
        if status:
            base = base.where(DeployJob.status == status)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = base.options(selectinload(DeployJob.agent))
        stmt = stmt.order_by(DeployJob.started_at.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        jobs = list(result.scalars().all())

        return jobs, total

    @staticmethod
    async def get(session: AsyncSession, job_id: uuid.UUID) -> DeployJob | None:
        """Get a deploy job by ID."""
        stmt = (
            select(DeployJob)
            .options(selectinload(DeployJob.agent))
            .where(DeployJob.id == job_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
