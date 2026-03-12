"""Memory backend registry and management service.

Provides in-memory storage for memory configurations, messages, and conversations.
Supports two backend types: buffer_window (sliding window) and buffer (full buffer).
PostgreSQL backend is simulated with in-memory store for now.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models (internal)
# ---------------------------------------------------------------------------


class MemoryMessage(BaseModel):
    """A single message stored in memory."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    config_id: str
    session_id: str
    agent_id: str | None = None
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryConfig(BaseModel):
    """Configuration for a memory backend instance."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    backend_type: str  # "in_memory" | "postgresql"
    memory_type: str = "buffer_window"  # "buffer_window" | "buffer"
    max_messages: int = 100
    namespace_pattern: str = "{agent_id}:{session_id}"
    scope: str = "agent"  # "agent" | "team" | "global"
    linked_agents: list[str] = Field(default_factory=list)
    description: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryStats(BaseModel):
    """Stats about a memory config's usage."""

    config_id: str
    backend_type: str
    memory_type: str
    message_count: int = 0
    session_count: int = 0
    storage_size_bytes: int = 0
    linked_agent_count: int = 0


class ConversationSummary(BaseModel):
    """Summary of a conversation (session)."""

    session_id: str
    agent_id: str | None = None
    message_count: int
    first_message_at: datetime | None = None
    last_message_at: datetime | None = None


class MemorySearchResult(BaseModel):
    """A search hit within stored messages."""

    message: MemoryMessage
    score: float = 1.0
    highlight: str = ""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MemoryService:
    """In-memory implementation of the memory backend service.

    All data is stored in class-level dicts so it persists across requests
    within the same process. Both "in_memory" and "postgresql" backend types
    are simulated in-process for v0.3.
    """

    _configs: dict[str, MemoryConfig] = {}
    _messages: dict[str, list[MemoryMessage]] = {}  # keyed by config_id

    # -- Config CRUD --------------------------------------------------------

    @classmethod
    def reset(cls) -> None:
        """Clear all data (used in tests)."""
        cls._configs.clear()
        cls._messages.clear()

    @classmethod
    async def create_config(
        cls,
        *,
        name: str,
        backend_type: str = "in_memory",
        memory_type: str = "buffer_window",
        max_messages: int = 100,
        namespace_pattern: str = "{agent_id}:{session_id}",
        scope: str = "agent",
        linked_agents: list[str] | None = None,
        description: str = "",
    ) -> MemoryConfig:
        config = MemoryConfig(
            name=name,
            backend_type=backend_type,
            memory_type=memory_type,
            max_messages=max_messages,
            namespace_pattern=namespace_pattern,
            scope=scope,
            linked_agents=linked_agents or [],
            description=description,
        )
        cls._configs[config.id] = config
        cls._messages[config.id] = []
        logger.info("Created memory config %s (%s)", config.name, config.id)
        return config

    @classmethod
    async def list_configs(
        cls, *, page: int = 1, per_page: int = 20
    ) -> tuple[list[MemoryConfig], int]:
        all_configs = sorted(cls._configs.values(), key=lambda c: c.name)
        total = len(all_configs)
        start = (page - 1) * per_page
        return all_configs[start : start + per_page], total

    @classmethod
    async def get_config(cls, config_id: str) -> MemoryConfig | None:
        return cls._configs.get(config_id)

    @classmethod
    async def delete_config(cls, config_id: str) -> bool:
        if config_id not in cls._configs:
            return False
        del cls._configs[config_id]
        cls._messages.pop(config_id, None)
        logger.info("Deleted memory config %s", config_id)
        return True

    # -- Stats --------------------------------------------------------------

    @classmethod
    async def get_stats(cls, config_id: str) -> MemoryStats | None:
        config = cls._configs.get(config_id)
        if config is None:
            return None
        msgs = cls._messages.get(config_id, [])
        sessions = {m.session_id for m in msgs}
        # Rough size estimate: sum of content lengths
        size = sum(len(m.content.encode("utf-8")) for m in msgs)
        return MemoryStats(
            config_id=config_id,
            backend_type=config.backend_type,
            memory_type=config.memory_type,
            message_count=len(msgs),
            session_count=len(sessions),
            storage_size_bytes=size,
            linked_agent_count=len(config.linked_agents),
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
        config = cls._configs.get(config_id)
        if config is None:
            return None

        msg = MemoryMessage(
            config_id=config_id,
            session_id=session_id,
            agent_id=agent_id,
            role=role,
            content=content,
            metadata=metadata or {},
        )

        if config_id not in cls._messages:
            cls._messages[config_id] = []

        cls._messages[config_id].append(msg)

        # Apply buffer_window truncation per session
        if config.memory_type == "buffer_window":
            session_msgs = [m for m in cls._messages[config_id] if m.session_id == session_id]
            if len(session_msgs) > config.max_messages:
                # Keep only the latest max_messages for this session
                to_remove = len(session_msgs) - config.max_messages
                removed = 0
                new_list: list[MemoryMessage] = []
                for m in cls._messages[config_id]:
                    if m.session_id == session_id and removed < to_remove:
                        removed += 1
                        continue
                    new_list.append(m)
                cls._messages[config_id] = new_list

        return msg

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
        msgs = cls._messages.get(config_id, [])
        if agent_id:
            msgs = [m for m in msgs if m.agent_id == agent_id]

        # Group by session
        sessions: dict[str, list[MemoryMessage]] = {}
        for m in msgs:
            sessions.setdefault(m.session_id, []).append(m)

        summaries: list[ConversationSummary] = []
        for sid, session_msgs in sorted(sessions.items()):
            sorted_msgs = sorted(session_msgs, key=lambda x: x.timestamp)
            summaries.append(
                ConversationSummary(
                    session_id=sid,
                    agent_id=session_msgs[0].agent_id,
                    message_count=len(session_msgs),
                    first_message_at=sorted_msgs[0].timestamp,
                    last_message_at=sorted_msgs[-1].timestamp,
                )
            )

        total = len(summaries)
        start = (page - 1) * per_page
        return summaries[start : start + per_page], total

    @classmethod
    async def get_conversation(cls, config_id: str, session_id: str) -> list[MemoryMessage]:
        msgs = cls._messages.get(config_id, [])
        session_msgs = [m for m in msgs if m.session_id == session_id]
        return sorted(session_msgs, key=lambda x: x.timestamp)

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
        """Delete conversations matching filters. Returns number of messages deleted."""
        if config_id not in cls._messages:
            return 0

        original_count = len(cls._messages[config_id])
        filtered: list[MemoryMessage] = []

        for m in cls._messages[config_id]:
            should_delete = True
            if session_id and m.session_id != session_id:
                should_delete = False
            if agent_id and m.agent_id != agent_id:
                should_delete = False
            if before and m.timestamp >= before:
                should_delete = False

            # If no filter matched specifically, only delete if at least one filter was given
            if not session_id and not agent_id and not before:
                should_delete = False

            if not should_delete:
                filtered.append(m)

        cls._messages[config_id] = filtered
        deleted = original_count - len(filtered)
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
        """Simple full-text search across messages in a config."""
        msgs = cls._messages.get(config_id, [])
        query_lower = query.lower()
        results: list[MemorySearchResult] = []

        for m in msgs:
            content_lower = m.content.lower()
            if query_lower in content_lower:
                # Find a highlight snippet around the match
                idx = content_lower.index(query_lower)
                start = max(0, idx - 40)
                end = min(len(m.content), idx + len(query) + 40)
                highlight = m.content[start:end]
                if start > 0:
                    highlight = "..." + highlight
                if end < len(m.content):
                    highlight = highlight + "..."

                results.append(
                    MemorySearchResult(
                        message=m,
                        score=1.0,
                        highlight=highlight,
                    )
                )
                if len(results) >= limit:
                    break

        return results
