"""Prompt registry service — manages versioned prompt templates."""

from __future__ import annotations

import difflib
import logging

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Prompt, PromptVersion

logger = logging.getLogger(__name__)


def _bump_patch(version: str) -> str:
    """Bump the patch component of a semver string (1.0.0 -> 1.0.1).

    Falls back to appending ``.1`` for non-semver inputs so the value still
    changes visibly in the UI rather than failing silently.
    """
    parts = version.split(".")
    if len(parts) == 3 and parts[2].isdigit():
        return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    return f"{version}.1"


class PromptRegistry:
    """Service class for prompt CRUD operations."""

    @staticmethod
    async def register(
        session: AsyncSession,
        name: str,
        version: str,
        content: str,
        description: str = "",
        team: str = "",
    ) -> Prompt:
        """Register or update a prompt in the registry."""
        stmt = select(Prompt).where(Prompt.name == name, Prompt.version == version)
        result = await session.execute(stmt)
        prompt = result.scalar_one_or_none()

        if prompt:
            prompt.content = content
            prompt.description = description
            prompt.team = team
            logger.info("Updated prompt '%s' v%s in registry", name, version)
        else:
            prompt = Prompt(
                name=name,
                version=version,
                content=content,
                description=description,
                team=team,
            )
            session.add(prompt)
            logger.info("Registered new prompt '%s' v%s in registry", name, version)

        await session.flush()
        return prompt

    @staticmethod
    async def list(
        session: AsyncSession,
        team: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Prompt], int]:
        """List prompts with optional filters."""
        stmt = select(Prompt)

        if team:
            stmt = stmt.where(Prompt.team == team)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Prompt.name, Prompt.version.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        prompts = list(result.scalars().all())

        return prompts, total

    @staticmethod
    async def get(session: AsyncSession, name: str, version: str | None = None) -> Prompt | None:
        """Get a prompt by name and optionally version (latest if not specified)."""
        stmt = select(Prompt).where(Prompt.name == name)
        if version:
            stmt = stmt.where(Prompt.version == version)
        else:
            stmt = stmt.order_by(Prompt.version.desc())
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_by_id(session: AsyncSession, prompt_id: str) -> Prompt | None:
        """Get a prompt by its UUID."""
        stmt = select(Prompt).where(Prompt.id == prompt_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        session: AsyncSession,
        prompt_id: str,
        content: str | None = None,
        description: str | None = None,
    ) -> Prompt | None:
        """Update a prompt's content and/or description."""
        stmt = select(Prompt).where(Prompt.id == prompt_id)
        result = await session.execute(stmt)
        prompt = result.scalar_one_or_none()
        if not prompt:
            return None
        if content is not None:
            prompt.content = content
        if description is not None:
            prompt.description = description
        await session.flush()
        logger.info("Updated prompt '%s' v%s", prompt.name, prompt.version)
        return prompt

    @staticmethod
    async def update_content(
        session: AsyncSession,
        prompt_id: str,
        content: str,
        change_summary: str = "",
        author: str = "",
    ) -> Prompt | None:
        """Update a prompt's content and auto-create a version snapshot in one transaction.

        Bumps ``prompt.version`` patch (1.0.0 -> 1.0.1) on every save so the UI
        shows the change. To bump minor or major, callers should use
        ``duplicate()`` to create a new Prompt row with the desired version.
        """
        stmt = select(Prompt).where(Prompt.id == prompt_id)
        result = await session.execute(stmt)
        prompt = result.scalar_one_or_none()
        if not prompt:
            return None

        prompt.content = content
        prompt.version = _bump_patch(prompt.version)
        await session.flush()

        count_stmt = select(func.count()).select_from(
            select(PromptVersion).where(PromptVersion.prompt_id == prompt_id).subquery()
        )
        count = (await session.execute(count_stmt)).scalar() or 0
        next_version = str(count + 1)

        ver = PromptVersion(
            prompt_id=prompt_id,
            version=next_version,
            content=content,
            change_summary=change_summary,
            author=author,
        )
        session.add(ver)
        await session.flush()

        logger.info(
            "Updated prompt '%s' content and created version snapshot v%s",
            prompt.name,
            next_version,
        )
        return prompt

    @staticmethod
    async def delete(session: AsyncSession, prompt_id: str) -> bool:
        """Delete a prompt by id. Returns True if deleted."""
        stmt = select(Prompt).where(Prompt.id == prompt_id)
        result = await session.execute(stmt)
        prompt = result.scalar_one_or_none()
        if not prompt:
            return False
        await session.delete(prompt)
        await session.flush()
        logger.info("Deleted prompt '%s' v%s", prompt.name, prompt.version)
        return True

    @staticmethod
    async def get_versions(session: AsyncSession, prompt_id: str) -> list[Prompt]:
        """Get all versions of a prompt (looked up by the name of the given id)."""
        stmt = select(Prompt).where(Prompt.id == prompt_id)
        result = await session.execute(stmt)
        prompt = result.scalar_one_or_none()
        if not prompt:
            return []
        stmt = select(Prompt).where(Prompt.name == prompt.name).order_by(Prompt.version.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def duplicate(session: AsyncSession, prompt_id: str) -> Prompt | None:
        """Duplicate a prompt as a new version (bumps patch version)."""
        stmt = select(Prompt).where(Prompt.id == prompt_id)
        result = await session.execute(stmt)
        source = result.scalar_one_or_none()
        if not source:
            return None

        # Find the latest version for this prompt name to compute next version
        all_versions_stmt = (
            select(Prompt).where(Prompt.name == source.name).order_by(Prompt.version.desc())
        )
        all_result = await session.execute(all_versions_stmt)
        all_versions = list(all_result.scalars().all())

        latest_version = all_versions[0].version if all_versions else source.version
        parts = latest_version.split(".")
        try:
            parts[-1] = str(int(parts[-1]) + 1)
            new_version = ".".join(parts)
        except (ValueError, IndexError):
            new_version = latest_version + ".1"

        new_prompt = Prompt(
            name=source.name,
            version=new_version,
            content=source.content,
            description=source.description,
            team=source.team,
        )
        session.add(new_prompt)
        await session.flush()
        logger.info(
            "Duplicated prompt '%s' v%s -> v%s",
            source.name,
            source.version,
            new_version,
        )
        return new_prompt

    @staticmethod
    async def search(
        session: AsyncSession, query: str, page: int = 1, per_page: int = 20
    ) -> tuple[list[Prompt], int]:
        """Search prompts by name or description."""
        pattern = f"%{query}%"
        stmt = select(Prompt).where(
            or_(Prompt.name.ilike(pattern), Prompt.description.ilike(pattern)),
        )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Prompt.name)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        prompts = list(result.scalars().all())

        return prompts, total

    # --- Prompt Version Snapshot methods ---

    @staticmethod
    async def list_version_snapshots(session: AsyncSession, prompt_id: str) -> list[PromptVersion]:
        """List all version snapshots of a prompt, ordered by created_at descending."""
        stmt = (
            select(PromptVersion)
            .where(PromptVersion.prompt_id == prompt_id)
            .order_by(PromptVersion.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create_version_snapshot(
        session: AsyncSession,
        prompt_id: str,
        version: str,
        content: str,
        change_summary: str = "",
        author: str = "",
    ) -> PromptVersion:
        """Create a new version snapshot for a prompt."""
        ver = PromptVersion(
            prompt_id=prompt_id,
            version=version,
            content=content,
            change_summary=change_summary or "",
            author=author,
        )
        session.add(ver)
        await session.flush()
        logger.info("Created version snapshot %s for prompt %s", version, prompt_id)
        return ver

    @staticmethod
    async def get_version_snapshot(
        session: AsyncSession, prompt_id: str, version_id: str
    ) -> PromptVersion | None:
        """Get a specific version snapshot by ID, scoped to a prompt."""
        stmt = select(PromptVersion).where(
            PromptVersion.id == version_id,
            PromptVersion.prompt_id == prompt_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def diff_version_snapshots(
        session: AsyncSession, prompt_id: str, version_id_1: str, version_id_2: str
    ) -> tuple[PromptVersion | None, PromptVersion | None, str]:
        """Compute a unified diff between two prompt version snapshots."""
        stmt1 = select(PromptVersion).where(
            PromptVersion.id == version_id_1,
            PromptVersion.prompt_id == prompt_id,
        )
        stmt2 = select(PromptVersion).where(
            PromptVersion.id == version_id_2,
            PromptVersion.prompt_id == prompt_id,
        )
        r1 = await session.execute(stmt1)
        r2 = await session.execute(stmt2)
        v1 = r1.scalar_one_or_none()
        v2 = r2.scalar_one_or_none()

        if not v1 or not v2:
            return v1, v2, ""

        diff_lines = difflib.unified_diff(
            v1.content.splitlines(keepends=True),
            v2.content.splitlines(keepends=True),
            fromfile=f"v{v1.version}",
            tofile=f"v{v2.version}",
        )
        return v1, v2, "".join(diff_lines)
