"""Memory backend registry and management service.

Backed by PostgreSQL via SQLAlchemy. All data persists across API restarts
and is consistent across replicas.

Supported memory_type values (Phase 2):
  buffer_window — sliding window of the last N messages (default)
  buffer        — unlimited buffer, never truncated
  summary       — auto-condenses old messages into a summary row when a threshold is reached
  entity        — extracts named entities from messages and injects them as context on load
  semantic      — stores per-message embeddings; load supports cosine-similarity ranking
                  (Phase 2: embeddings are stored as None; real vectors are Phase 3)

Supported scope values (Phase 2):
  agent   — isolated per-agent (default)
  team    — shared across agents in the same team; enforces team_id on load/save
  global  — org-wide shared (future)
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from api.database import async_session
from api.models.database import MemoryConfig as MemoryConfigORM
from api.models.database import MemoryEntity as MemoryEntityORM
from api.models.database import MemoryMessage as MemoryMessageORM

logger = logging.getLogger(__name__)

_PHASE2_SCOPES = {"global"}  # "team" is now live; "global" remains Phase 3

# Default threshold: condense into a summary when the session exceeds this many messages
_DEFAULT_SUMMARY_TRIGGER = 20

# Regex patterns used for lightweight entity extraction (no LLM required)
_ENTITY_PATTERNS: list[tuple[str, str]] = [
    # Quoted terms (product names, feature names, decision labels)
    (r'"([A-Z][^"]{1,60})"', "product"),
    (r"'([A-Z][^']{1,60})'", "product"),
    # Proper nouns: Title Case word(s) not at sentence start
    (r"(?<!\. )(?<!\n)\b([A-Z][a-z]+(?:\s[A-Z][a-z]+){0,2})\b", "person"),
    # Dates: ISO-ish or natural language
    (r"\b(\d{4}-\d{2}-\d{2})\b", "date"),
    (r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4})\b", "date"),
]


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


class MemoryEntityModel(BaseModel):
    id: str
    config_id: str
    entity_type: str
    name: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: datetime


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
# Semantic memory helpers (module-level, no LLM required)
# ---------------------------------------------------------------------------


def _hash_embedding(text: str, dim: int = 64) -> list[float]:
    """Produce a deterministic pseudo-embedding from *text* using a hash trick.

    This is a Phase 2 stub — good enough for exact-duplicate detection and
    approximate similarity when real embeddings are unavailable.  Phase 3 will
    replace this with a real embedding model call.
    """
    vec = [0.0] * dim
    for i, ch in enumerate(text):
        vec[i % dim] += ord(ch)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity between two equal-length vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)


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
        if scope in _PHASE2_SCOPES:
            raise HTTPException(
                status_code=400,
                detail=f"scope '{scope}' is not yet available. Use 'agent' or 'team'.",
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
        requesting_team: str | None = None,
    ) -> MemoryMessage | None:
        async with async_session() as db:
            cid = uuid.UUID(config_id)
            config_row = await db.get(MemoryConfigORM, cid)
            if config_row is None:
                return None

            # Team-scope enforcement
            if config_row.scope == "team" and requesting_team is not None:
                if config_row.team != requesting_team:
                    raise PermissionError("Team scope violation")

            cfg = config_row.config or {}
            max_messages = cfg.get("max_messages", 100)
            memory_type = config_row.memory_type

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

            if memory_type == "buffer_window":
                # Keep only the latest max_messages per session
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

            elif memory_type == "summary":
                # Condense into a summary row when the session exceeds the threshold
                trigger = cfg.get("summary_trigger_threshold", _DEFAULT_SUMMARY_TRIGGER)
                count_result = await db.execute(
                    select(func.count())
                    .select_from(MemoryMessageORM)
                    .where(
                        MemoryMessageORM.config_id == cid,
                        MemoryMessageORM.session_id == session_id,
                        MemoryMessageORM.role != "summary",
                    )
                )
                count = count_result.scalar_one()
                if count >= trigger:
                    # Gather all non-summary messages to condense
                    old_result = await db.execute(
                        select(MemoryMessageORM)
                        .where(
                            MemoryMessageORM.config_id == cid,
                            MemoryMessageORM.session_id == session_id,
                            MemoryMessageORM.role != "summary",
                        )
                        .order_by(MemoryMessageORM.created_at)
                    )
                    old_msgs = old_result.scalars().all()
                    old_ids = [m.id for m in old_msgs]

                    # Attempt an LLM-based summary; fall back to a stub string
                    summary_text = await cls._generate_summary(old_msgs)

                    # Delete old individual messages
                    await db.execute(
                        delete(MemoryMessageORM).where(MemoryMessageORM.id.in_(old_ids))
                    )

                    # Insert summary row
                    summary_row = MemoryMessageORM(
                        id=uuid.uuid4(),
                        config_id=cid,
                        session_id=session_id,
                        role="summary",
                        content=summary_text,
                        metadata_={"condensed_count": len(old_ids)},
                    )
                    db.add(summary_row)

            elif memory_type == "entity":
                # Extract entities from the new message and upsert into memory_entities
                await cls._extract_and_store_entities(db, cid, content)

            elif memory_type == "semantic":
                # Phase 2 stub: store None for embedding (real vectors are Phase 3)
                # The flush above already added the row; update its embedding to None explicitly
                # (it defaults to None — nothing extra needed for the stub)
                pass

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
    async def get_conversation(
        cls,
        config_id: str,
        session_id: str,
        *,
        requesting_team: str | None = None,
        query: str | None = None,
        max_messages: int | None = None,
    ) -> list[MemoryMessage]:
        async with async_session() as db:
            cid = uuid.UUID(config_id)
            config_row = await db.get(MemoryConfigORM, cid)
            if config_row is None:
                return []

            # Team-scope enforcement
            if config_row.scope == "team" and requesting_team is not None:
                if config_row.team != requesting_team:
                    raise PermissionError("Team scope violation")

            memory_type = config_row.memory_type
            cfg = config_row.config or {}
            limit = max_messages or cfg.get("max_messages", 100)

            # Fetch stored messages (excluding summary rows — prepended separately below)
            q = (
                select(MemoryMessageORM)
                .where(
                    MemoryMessageORM.config_id == cid,
                    MemoryMessageORM.session_id == session_id,
                )
                .order_by(MemoryMessageORM.created_at)
            )
            result = await db.execute(q)
            all_msgs = result.scalars().all()

            if memory_type == "summary":
                # Separate summary rows from conversation rows
                summary_rows = [m for m in all_msgs if m.role == "summary"]
                conv_rows = [m for m in all_msgs if m.role != "summary"]
                messages = [_message_from_orm(m) for m in summary_rows] + [
                    _message_from_orm(m) for m in conv_rows
                ]
                return messages

            elif memory_type == "entity":
                # Prepend an entity-context system message
                entities_result = await db.execute(
                    select(MemoryEntityORM).where(MemoryEntityORM.config_id == cid)
                )
                entities = entities_result.scalars().all()
                conv_messages = [_message_from_orm(m) for m in all_msgs]
                if entities:
                    entity_parts = [
                        f"{e.name} ({e.entity_type})"
                        + (f": {e.attributes}" if e.attributes else "")
                        for e in entities
                    ]
                    context_content = "Known entities: " + "; ".join(entity_parts) + "."
                    context_msg = MemoryMessage(
                        id=str(uuid.uuid4()),
                        config_id=config_id,
                        session_id=session_id,
                        role="system",
                        content=context_content,
                        metadata={},
                        timestamp=datetime.utcnow(),
                    )
                    return [context_msg] + conv_messages
                return conv_messages

            elif memory_type == "semantic":
                # If a query is provided and embeddings are available, rank by cosine similarity.
                # Phase 2 stub: embeddings are None → fall back to most recent max_messages.
                if query:
                    rows_with_embeddings = [m for m in all_msgs if m.embedding is not None]
                    if rows_with_embeddings:
                        query_vec = _hash_embedding(query)
                        scored = [
                            (m, _cosine_similarity(query_vec, m.embedding))
                            for m in rows_with_embeddings
                        ]
                        scored.sort(key=lambda x: x[1], reverse=True)
                        top = [m for m, _ in scored[:limit]]
                        top.sort(key=lambda m: m.created_at)
                        return [_message_from_orm(m) for m in top]
                # Fallback: most recent limit messages
                return [_message_from_orm(m) for m in all_msgs[-limit:]]

            else:
                # buffer / buffer_window — plain chronological return
                return [_message_from_orm(m) for m in all_msgs]

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

    # -- Phase 2 helpers ----------------------------------------------------

    @staticmethod
    async def _generate_summary(messages: list[MemoryMessageORM]) -> str:
        """Produce a condensed summary of the given messages.

        Attempts an LLM call via the internal playground endpoint; falls back
        to a plain stub string on any error so the caller never blocks.
        """
        try:
            import httpx

            api_base = "http://localhost:8000"
            turns = "\n".join(f"{m.role.upper()}: {m.content[:300]}" for m in messages[:40])
            prompt = (
                "Summarize the following conversation in 2-4 sentences, "
                "preserving all key decisions and facts:\n\n" + turns
            )
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{api_base}/api/v1/playground/complete",
                    json={"messages": [{"role": "user", "content": prompt}]},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    summary = data.get("content") or data.get("data", {}).get("content", "")
                    if summary:
                        return summary
        except Exception:
            logger.debug("Summary LLM call failed — using stub", exc_info=True)

        return f"[Summary of {len(messages)} messages]"

    @staticmethod
    async def _extract_and_store_entities(db: Any, config_id: uuid.UUID, content: str) -> None:
        """Extract named entities from *content* using regex and upsert into memory_entities."""
        extracted: dict[str, tuple[str, str]] = {}  # name → (entity_type, name)

        for pattern, entity_type in _ENTITY_PATTERNS:
            for match in re.finditer(pattern, content):
                name = match.group(1).strip()
                if len(name) < 2:
                    continue
                # Deduplicate; first match wins for a given name
                if name not in extracted:
                    extracted[name] = (entity_type, name)

        for name, (entity_type, _) in extracted.items():
            # Check if entity already exists for this config
            existing_result = await db.execute(
                select(MemoryEntityORM).where(
                    MemoryEntityORM.config_id == config_id,
                    MemoryEntityORM.name == name,
                    MemoryEntityORM.entity_type == entity_type,
                )
            )
            existing = existing_result.scalars().first()
            if existing:
                await db.execute(
                    update(MemoryEntityORM)
                    .where(MemoryEntityORM.id == existing.id)
                    .values(last_seen_at=func.now())
                )
            else:
                new_entity = MemoryEntityORM(
                    id=uuid.uuid4(),
                    config_id=config_id,
                    entity_type=entity_type,
                    name=name,
                    attributes={},
                )
                db.add(new_entity)

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
