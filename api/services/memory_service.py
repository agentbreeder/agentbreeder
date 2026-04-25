"""Memory backend registry and management service.

Backed by PostgreSQL via SQLAlchemy. All data persists across API restarts
and is consistent across replicas.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from api.database import async_session
from api.models.database import MemoryConfig as MemoryConfigORM
from api.models.database import MemoryMessage as MemoryMessageORM

logger = logging.getLogger(__name__)

_PHASE2_MEMORY_TYPES = {"summary", "entity", "semantic"}
_PHASE2_SCOPES = {"team", "global"}


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class MemoryMessage(BaseModel):
    id: str
    config_id: str
    session_id: str
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class MemoryConfig(BaseModel):
    id: str
    name: str
    backend_type: str
    memory_type: str = "buffer_window"
    max_messages: int = 100
    namespace_pattern: str = "{agent_id}:{session_id}"
    scope: str = "agent"
    linked_agents: list[str] = Field(default_factory=list)
    description: str = ""
    created_at: datetime
    updated_at: datetime


class MemoryStats(BaseModel):
    config_id: str
    backend_type: str
    memory_type: str
    message_count: int = 0
    session_count: int = 0
    storage_size_bytes: int = 0
    linked_agent_count: int = 0


class ConversationSummary(BaseModel):
    session_id: str
    agent_id: str | None = None
    message_count: int
    first_message_at: datetime | None = None
    last_message_at: datetime | None = None


class MemorySearchResult(BaseModel):
    message: MemoryMessage
    score: float = 1.0
    highlight: str = ""


# ---------------------------------------------------------------------------
# ORM helpers
# ---------------------------------------------------------------------------


def _config_from_orm(row: MemoryConfigORM) -> MemoryConfig:
    cfg = row.config or {}
    return MemoryConfig(
        id=str(row.id),
        name=row.name,
        backend_type=row.backend,
        memory_type=row.memory_type,
        max_messages=cfg.get("max_messages", 100),
        namespace_pattern=cfg.get("namespace_pattern", "{agent_id}:{session_id}"),
        scope=row.scope,
        linked_agents=cfg.get("linked_agents", []),
        description=cfg.get("description", ""),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _message_from_orm(row: MemoryMessageORM) -> MemoryMessage:
    return MemoryMessage(
        id=str(row.id),
        config_id=str(row.config_id),
        session_id=row.session_id,
        role=row.role,
        content=row.content,
        metadata=row.metadata_ or {},
        timestamp=row.created_at,
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MemoryService:
    """Database-backed memory service."""

    @staticmethod
    def reset() -> None:
        """No-op: kept for test compatibility; data lives in the database now."""

    # -- Config CRUD --------------------------------------------------------

    @classmethod
    async def create_config(
        cls,
        *,
        name: str,
        backend_type: str = "postgresql",
        memory_type: str = "buffer_window",
        max_messages: int = 100,
        namespace_pattern: str = "{agent_id}:{session_id}",
        scope: str = "agent",
        linked_agents: list[str] | None = None,
        description: str = "",
        team: str = "default",
        owner: str = "",
        tags: list[str] | None = None,
    ) -> MemoryConfig:
        if memory_type in _PHASE2_MEMORY_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"memory_type '{memory_type}' is planned for Phase 2 and not yet available. "
                "Use 'buffer_window' or 'buffer'.",
            )
        if scope in _PHASE2_SCOPES:
            raise HTTPException(
                status_code=400,
                detail=f"scope '{scope}' is planned for Phase 2 and not yet available. Use 'agent'.",
            )

        row = MemoryConfigORM(
            id=uuid.uuid4(),
            name=name,
            team=team,
            owner=owner,
            memory_type=memory_type,
            backend=backend_type,
            scope=scope,
            tags=tags or [],
            config={
                "max_messages": max_messages,
                "namespace_pattern": namespace_pattern,
                "linked_agents": linked_agents or [],
                "description": description,
            },
        )
        async with async_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            logger.info("Created memory config %s (%s)", row.name, row.id)
            return _config_from_orm(row)

    @classmethod
    async def list_configs(
        cls, *, page: int = 1, per_page: int = 20
    ) -> tuple[list[MemoryConfig], int]:
        async with async_session() as session:
            total_result = await session.execute(select(func.count()).select_from(MemoryConfigORM))
            total = total_result.scalar_one()
            offset = (page - 1) * per_page
            rows_result = await session.execute(
                select(MemoryConfigORM)
                .order_by(MemoryConfigORM.name)
                .offset(offset)
                .limit(per_page)
            )
            rows = rows_result.scalars().all()
            return [_config_from_orm(r) for r in rows], total

    @classmethod
    async def get_config(cls, config_id: str) -> MemoryConfig | None:
        async with async_session() as session:
            row = await session.get(MemoryConfigORM, uuid.UUID(config_id))
            return _config_from_orm(row) if row else None

    @classmethod
    async def delete_config(cls, config_id: str) -> bool:
        async with async_session() as session:
            row = await session.get(MemoryConfigORM, uuid.UUID(config_id))
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            logger.info("Deleted memory config %s", config_id)
            return True

    # -- Stats --------------------------------------------------------------

    @classmethod
    async def get_stats(cls, config_id: str) -> MemoryStats | None:
        async with async_session() as session:
            row = await session.get(MemoryConfigORM, uuid.UUID(config_id))
            if row is None:
                return None

            cid = uuid.UUID(config_id)
            msg_count_result = await session.execute(
                select(func.count())
                .select_from(MemoryMessageORM)
                .where(MemoryMessageORM.config_id == cid)
            )
            msg_count = msg_count_result.scalar_one()

            session_count_result = await session.execute(
                select(func.count(MemoryMessageORM.session_id.distinct())).where(
                    MemoryMessageORM.config_id == cid
                )
            )
            session_count = session_count_result.scalar_one()

            size_result = await session.execute(
                select(func.coalesce(func.sum(func.length(MemoryMessageORM.content)), 0)).where(
                    MemoryMessageORM.config_id == cid
                )
            )
            size = size_result.scalar_one()

            cfg = row.config or {}
            return MemoryStats(
                config_id=config_id,
                backend_type=row.backend,
                memory_type=row.memory_type,
                message_count=msg_count,
                session_count=session_count,
                storage_size_bytes=size,
                linked_agent_count=len(cfg.get("linked_agents", [])),
            )

    # -- Message storage ----------------------------------------------------

    @classmethod
    async def store_message(
        cls,
        config_id: str,
        *,
        session_id: str,
        role: str,
        content: str,
        agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryMessage | None:
        async with async_session() as db:
            cid = uuid.UUID(config_id)
            config_row = await db.get(MemoryConfigORM, cid)
            if config_row is None:
                return None

            cfg = config_row.config or {}
            max_messages = cfg.get("max_messages", 100)

            msg = MemoryMessageORM(
                id=uuid.uuid4(),
                config_id=cid,
                session_id=session_id,
                role=role,
                content=content,
                metadata_=metadata or {},
            )
            db.add(msg)
            await db.flush()

            # buffer_window: keep only the latest max_messages per session
            if config_row.memory_type == "buffer_window":
                count_result = await db.execute(
                    select(func.count())
                    .select_from(MemoryMessageORM)
                    .where(
                        MemoryMessageORM.config_id == cid,
                        MemoryMessageORM.session_id == session_id,
                    )
                )
                count = count_result.scalar_one()
                if count > max_messages:
                    excess = count - max_messages
                    oldest_result = await db.execute(
                        select(MemoryMessageORM.id)
                        .where(
                            MemoryMessageORM.config_id == cid,
                            MemoryMessageORM.session_id == session_id,
                        )
                        .order_by(MemoryMessageORM.created_at)
                        .limit(excess)
                    )
                    oldest_ids = [r[0] for r in oldest_result]
                    await db.execute(
                        delete(MemoryMessageORM).where(MemoryMessageORM.id.in_(oldest_ids))
                    )

            await db.commit()
            await db.refresh(msg)
            return _message_from_orm(msg)

    # -- Conversations ------------------------------------------------------

    @classmethod
    async def list_conversations(
        cls,
        config_id: str,
        *,
        agent_id: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[ConversationSummary], int]:
        async with async_session() as db:
            cid = uuid.UUID(config_id)
            q = (
                select(
                    MemoryMessageORM.session_id,
                    func.count().label("message_count"),
                    func.min(MemoryMessageORM.created_at).label("first_message_at"),
                    func.max(MemoryMessageORM.created_at).label("last_message_at"),
                )
                .where(MemoryMessageORM.config_id == cid)
                .group_by(MemoryMessageORM.session_id)
                .order_by(MemoryMessageORM.session_id)
            )
            rows_result = await db.execute(q)
            all_rows = rows_result.all()
            total = len(all_rows)
            start = (page - 1) * per_page
            page_rows = all_rows[start : start + per_page]
            return (
                [
                    ConversationSummary(
                        session_id=r.session_id,
                        message_count=r.message_count,
                        first_message_at=r.first_message_at,
                        last_message_at=r.last_message_at,
                    )
                    for r in page_rows
                ],
                total,
            )

    @classmethod
    async def get_conversation(cls, config_id: str, session_id: str) -> list[MemoryMessage]:
        async with async_session() as db:
            result = await db.execute(
                select(MemoryMessageORM)
                .where(
                    MemoryMessageORM.config_id == uuid.UUID(config_id),
                    MemoryMessageORM.session_id == session_id,
                )
                .order_by(MemoryMessageORM.created_at)
            )
            return [_message_from_orm(r) for r in result.scalars().all()]

    # -- Delete conversations -----------------------------------------------

    @classmethod
    async def delete_conversations(
        cls,
        config_id: str,
        *,
        session_id: str | None = None,
        agent_id: str | None = None,
        before: datetime | None = None,
    ) -> int:
        async with async_session() as db:
            cid = uuid.UUID(config_id)
            stmt = delete(MemoryMessageORM).where(MemoryMessageORM.config_id == cid)
            if session_id:
                stmt = stmt.where(MemoryMessageORM.session_id == session_id)
            if before:
                stmt = stmt.where(MemoryMessageORM.created_at < before)
            result = await db.execute(stmt)
            await db.commit()
            deleted = result.rowcount
            logger.info("Deleted %d messages from config %s", deleted, config_id)
            return deleted

    # -- Search -------------------------------------------------------------

    @classmethod
    async def search_messages(
        cls,
        config_id: str,
        *,
        query: str,
        limit: int = 50,
    ) -> list[MemorySearchResult]:
        async with async_session() as db:
            result = await db.execute(
                select(MemoryMessageORM)
                .where(
                    MemoryMessageORM.config_id == uuid.UUID(config_id),
                    MemoryMessageORM.content.ilike(f"%{query}%"),
                )
                .order_by(MemoryMessageORM.created_at)
                .limit(limit)
            )
            rows = result.scalars().all()
            results: list[MemorySearchResult] = []
            for row in rows:
                content_lower = row.content.lower()
                query_lower = query.lower()
                idx = content_lower.find(query_lower)
                if idx >= 0:
                    start = max(0, idx - 40)
                    end = min(len(row.content), idx + len(query) + 40)
                    highlight = row.content[start:end]
                    if start > 0:
                        highlight = "..." + highlight
                    if end < len(row.content):
                        highlight = highlight + "..."
                else:
                    highlight = row.content[:80]
                results.append(
                    MemorySearchResult(message=_message_from_orm(row), highlight=highlight)
                )
            return results
