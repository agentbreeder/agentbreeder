"""Tests for memory backend service — CRUD, buffer window, search, delete, stats."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.services.memory_service import MemoryService


@pytest.fixture(autouse=True)
def _reset_memory():
    """Clear the in-memory store before each test."""
    MemoryService.reset()
    yield
    MemoryService.reset()


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


class TestConfigCRUD:
    @pytest.mark.asyncio
    async def test_create_config(self) -> None:
        config = await MemoryService.create_config(name="test-mem", backend_type="in_memory")
        assert config.name == "test-mem"
        assert config.backend_type == "in_memory"
        assert config.memory_type == "buffer_window"
        assert config.max_messages == 100
        assert config.id

    @pytest.mark.asyncio
    async def test_create_config_postgresql_backend(self) -> None:
        config = await MemoryService.create_config(
            name="pg-mem", backend_type="postgresql", memory_type="buffer"
        )
        assert config.backend_type == "postgresql"
        assert config.memory_type == "buffer"

    @pytest.mark.asyncio
    async def test_create_config_with_all_fields(self) -> None:
        config = await MemoryService.create_config(
            name="full-config",
            backend_type="in_memory",
            memory_type="buffer_window",
            max_messages=50,
            namespace_pattern="{agent_id}:{user_id}",
            scope="team",
            linked_agents=["agent-1", "agent-2"],
            description="Test description",
        )
        assert config.max_messages == 50
        assert config.namespace_pattern == "{agent_id}:{user_id}"
        assert config.scope == "team"
        assert config.linked_agents == ["agent-1", "agent-2"]
        assert config.description == "Test description"

    @pytest.mark.asyncio
    async def test_list_configs_empty(self) -> None:
        configs, total = await MemoryService.list_configs()
        assert configs == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_list_configs(self) -> None:
        await MemoryService.create_config(name="beta-mem")
        await MemoryService.create_config(name="alpha-mem")
        configs, total = await MemoryService.list_configs()
        assert total == 2
        assert configs[0].name == "alpha-mem"  # sorted by name

    @pytest.mark.asyncio
    async def test_list_configs_pagination(self) -> None:
        for i in range(5):
            await MemoryService.create_config(name=f"mem-{i:02d}")
        configs, total = await MemoryService.list_configs(page=2, per_page=2)
        assert total == 5
        assert len(configs) == 2

    @pytest.mark.asyncio
    async def test_get_config(self) -> None:
        config = await MemoryService.create_config(name="get-test")
        fetched = await MemoryService.get_config(config.id)
        assert fetched is not None
        assert fetched.name == "get-test"

    @pytest.mark.asyncio
    async def test_get_config_not_found(self) -> None:
        result = await MemoryService.get_config("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_config(self) -> None:
        config = await MemoryService.create_config(name="to-delete")
        assert await MemoryService.delete_config(config.id) is True
        assert await MemoryService.get_config(config.id) is None

    @pytest.mark.asyncio
    async def test_delete_config_not_found(self) -> None:
        assert await MemoryService.delete_config("no-such-id") is False

    @pytest.mark.asyncio
    async def test_delete_config_removes_messages(self) -> None:
        config = await MemoryService.create_config(name="with-msgs")
        await MemoryService.store_message(config.id, session_id="s1", role="user", content="hello")
        await MemoryService.delete_config(config.id)
        # Messages should be gone
        assert config.id not in MemoryService._messages


# ---------------------------------------------------------------------------
# Message storage
# ---------------------------------------------------------------------------


class TestMessageStorage:
    @pytest.mark.asyncio
    async def test_store_message(self) -> None:
        config = await MemoryService.create_config(name="msg-test")
        msg = await MemoryService.store_message(
            config.id, session_id="s1", role="user", content="Hello!"
        )
        assert msg is not None
        assert msg.role == "user"
        assert msg.content == "Hello!"
        assert msg.session_id == "s1"

    @pytest.mark.asyncio
    async def test_store_message_with_metadata(self) -> None:
        config = await MemoryService.create_config(name="meta-test")
        msg = await MemoryService.store_message(
            config.id,
            session_id="s1",
            role="assistant",
            content="Hi!",
            agent_id="agent-x",
            metadata={"tokens": 42},
        )
        assert msg is not None
        assert msg.agent_id == "agent-x"
        assert msg.metadata == {"tokens": 42}

    @pytest.mark.asyncio
    async def test_store_message_config_not_found(self) -> None:
        result = await MemoryService.store_message(
            "bad-id", session_id="s1", role="user", content="Hi"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_store_multiple_messages(self) -> None:
        config = await MemoryService.create_config(name="multi-msg")
        for i in range(5):
            await MemoryService.store_message(
                config.id, session_id="s1", role="user", content=f"msg-{i}"
            )
        msgs = await MemoryService.get_conversation(config.id, "s1")
        assert len(msgs) == 5


# ---------------------------------------------------------------------------
# Buffer window truncation
# ---------------------------------------------------------------------------


class TestBufferWindow:
    @pytest.mark.asyncio
    async def test_buffer_window_truncates(self) -> None:
        config = await MemoryService.create_config(
            name="window-test", memory_type="buffer_window", max_messages=3
        )
        for i in range(5):
            await MemoryService.store_message(
                config.id, session_id="s1", role="user", content=f"msg-{i}"
            )
        msgs = await MemoryService.get_conversation(config.id, "s1")
        assert len(msgs) == 3
        # Should keep the last 3
        assert msgs[0].content == "msg-2"
        assert msgs[1].content == "msg-3"
        assert msgs[2].content == "msg-4"

    @pytest.mark.asyncio
    async def test_buffer_window_per_session(self) -> None:
        config = await MemoryService.create_config(
            name="per-session", memory_type="buffer_window", max_messages=2
        )
        for i in range(3):
            await MemoryService.store_message(
                config.id, session_id="s1", role="user", content=f"s1-{i}"
            )
        for i in range(3):
            await MemoryService.store_message(
                config.id, session_id="s2", role="user", content=f"s2-{i}"
            )
        s1_msgs = await MemoryService.get_conversation(config.id, "s1")
        s2_msgs = await MemoryService.get_conversation(config.id, "s2")
        assert len(s1_msgs) == 2
        assert len(s2_msgs) == 2

    @pytest.mark.asyncio
    async def test_buffer_type_no_truncation(self) -> None:
        config = await MemoryService.create_config(
            name="full-buffer", memory_type="buffer", max_messages=3
        )
        for i in range(5):
            await MemoryService.store_message(
                config.id, session_id="s1", role="user", content=f"msg-{i}"
            )
        msgs = await MemoryService.get_conversation(config.id, "s1")
        # buffer type does not truncate
        assert len(msgs) == 5


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class TestConversations:
    @pytest.mark.asyncio
    async def test_list_conversations(self) -> None:
        config = await MemoryService.create_config(name="convos")
        await MemoryService.store_message(config.id, session_id="s1", role="user", content="hi")
        await MemoryService.store_message(config.id, session_id="s2", role="user", content="hello")
        convos, total = await MemoryService.list_conversations(config.id)
        assert total == 2
        assert convos[0].session_id == "s1"
        assert convos[1].session_id == "s2"

    @pytest.mark.asyncio
    async def test_list_conversations_filter_agent(self) -> None:
        config = await MemoryService.create_config(name="agent-filter")
        await MemoryService.store_message(
            config.id, session_id="s1", role="user", content="a1", agent_id="agent-1"
        )
        await MemoryService.store_message(
            config.id, session_id="s2", role="user", content="a2", agent_id="agent-2"
        )
        convos, total = await MemoryService.list_conversations(config.id, agent_id="agent-1")
        assert total == 1
        assert convos[0].session_id == "s1"

    @pytest.mark.asyncio
    async def test_get_conversation_ordered(self) -> None:
        config = await MemoryService.create_config(name="ordered")
        await MemoryService.store_message(config.id, session_id="s1", role="user", content="first")
        await MemoryService.store_message(
            config.id, session_id="s1", role="assistant", content="second"
        )
        msgs = await MemoryService.get_conversation(config.id, "s1")
        assert len(msgs) == 2
        assert msgs[0].content == "first"
        assert msgs[1].content == "second"

    @pytest.mark.asyncio
    async def test_conversation_summary_timestamps(self) -> None:
        config = await MemoryService.create_config(name="ts-test")
        await MemoryService.store_message(config.id, session_id="s1", role="user", content="one")
        await MemoryService.store_message(
            config.id, session_id="s1", role="assistant", content="two"
        )
        convos, _ = await MemoryService.list_conversations(config.id)
        assert convos[0].first_message_at is not None
        assert convos[0].last_message_at is not None
        assert convos[0].first_message_at <= convos[0].last_message_at


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDeleteConversations:
    @pytest.mark.asyncio
    async def test_delete_by_session(self) -> None:
        config = await MemoryService.create_config(name="del-session")
        await MemoryService.store_message(config.id, session_id="s1", role="user", content="a")
        await MemoryService.store_message(config.id, session_id="s2", role="user", content="b")
        deleted = await MemoryService.delete_conversations(config.id, session_id="s1")
        assert deleted == 1
        remaining = await MemoryService.get_conversation(config.id, "s1")
        assert len(remaining) == 0
        s2 = await MemoryService.get_conversation(config.id, "s2")
        assert len(s2) == 1

    @pytest.mark.asyncio
    async def test_delete_by_agent(self) -> None:
        config = await MemoryService.create_config(name="del-agent")
        await MemoryService.store_message(
            config.id, session_id="s1", role="user", content="a", agent_id="a1"
        )
        await MemoryService.store_message(
            config.id, session_id="s2", role="user", content="b", agent_id="a2"
        )
        deleted = await MemoryService.delete_conversations(config.id, agent_id="a1")
        assert deleted == 1

    @pytest.mark.asyncio
    async def test_delete_by_date(self) -> None:
        config = await MemoryService.create_config(name="del-date")
        await MemoryService.store_message(config.id, session_id="s1", role="user", content="old")
        # All messages have now() timestamp; using a future "before" should delete them
        future = datetime(2099, 1, 1, tzinfo=UTC)
        deleted = await MemoryService.delete_conversations(config.id, before=future)
        assert deleted == 1

    @pytest.mark.asyncio
    async def test_delete_no_filter_deletes_nothing(self) -> None:
        config = await MemoryService.create_config(name="no-filter")
        await MemoryService.store_message(config.id, session_id="s1", role="user", content="safe")
        deleted = await MemoryService.delete_conversations(config.id)
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_config(self) -> None:
        deleted = await MemoryService.delete_conversations("no-such-config", session_id="s1")
        assert deleted == 0


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_finds_match(self) -> None:
        config = await MemoryService.create_config(name="search-test")
        await MemoryService.store_message(
            config.id, session_id="s1", role="user", content="How do I reset my password?"
        )
        await MemoryService.store_message(
            config.id, session_id="s1", role="assistant", content="Go to settings."
        )
        results = await MemoryService.search_messages(config.id, query="password")
        assert len(results) == 1
        assert "password" in results[0].highlight.lower()

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self) -> None:
        config = await MemoryService.create_config(name="case-test")
        await MemoryService.store_message(
            config.id, session_id="s1", role="user", content="Hello WORLD"
        )
        results = await MemoryService.search_messages(config.id, query="hello")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_no_match(self) -> None:
        config = await MemoryService.create_config(name="no-match")
        await MemoryService.store_message(
            config.id, session_id="s1", role="user", content="hi there"
        )
        results = await MemoryService.search_messages(config.id, query="zzzzz")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_respects_limit(self) -> None:
        config = await MemoryService.create_config(name="limit-test")
        for i in range(10):
            await MemoryService.store_message(
                config.id, session_id="s1", role="user", content=f"match keyword {i}"
            )
        results = await MemoryService.search_messages(config.id, query="keyword", limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_empty_config(self) -> None:
        config = await MemoryService.create_config(name="empty-search")
        results = await MemoryService.search_messages(config.id, query="anything")
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_empty(self) -> None:
        config = await MemoryService.create_config(name="stats-empty")
        stats = await MemoryService.get_stats(config.id)
        assert stats is not None
        assert stats.message_count == 0
        assert stats.session_count == 0
        assert stats.storage_size_bytes == 0

    @pytest.mark.asyncio
    async def test_stats_with_data(self) -> None:
        config = await MemoryService.create_config(name="stats-data", linked_agents=["a1", "a2"])
        await MemoryService.store_message(config.id, session_id="s1", role="user", content="hello")
        await MemoryService.store_message(config.id, session_id="s2", role="user", content="world")
        stats = await MemoryService.get_stats(config.id)
        assert stats is not None
        assert stats.message_count == 2
        assert stats.session_count == 2
        assert stats.storage_size_bytes > 0
        assert stats.linked_agent_count == 2

    @pytest.mark.asyncio
    async def test_stats_not_found(self) -> None:
        stats = await MemoryService.get_stats("no-such-id")
        assert stats is None
