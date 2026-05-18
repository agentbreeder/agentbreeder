"""Tests for memory backend service — CRUD, buffer window, search, delete, stats.

Database layer is mocked with a FakeSession backed by in-memory dicts so no
real PostgreSQL connection is required for unit tests.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from api.services.memory_service import MemoryService

# ---------------------------------------------------------------------------
# In-memory session fake
# ---------------------------------------------------------------------------


class _Store:
    """Shared in-memory store across fake sessions in a single test."""

    def __init__(self) -> None:
        self.configs: dict[str, Any] = {}
        self.messages: dict[str, Any] = {}


class _FakeRow:
    """Minimal ORM-like object returned by session.get()."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.__dict__.update(data)


class _FakeSession:
    """AsyncMock-compatible session that stores data in a _Store dict."""

    def __init__(self, store: _Store) -> None:
        self._store = store
        self._pending: list[Any] = []

    async def get(self, model: Any, pk: Any) -> Any:
        from api.models.database import MemoryConfig as MCfgORM
        from api.models.database import MemoryMessage as MMsgORM

        if model is MCfgORM:
            row = self._store.configs.get(str(pk))
            return row
        if model is MMsgORM:
            row = self._store.messages.get(str(pk))
            return row
        return None

    def add(self, row: Any) -> None:
        self._pending.append(row)

    async def flush(self) -> None:
        self._commit_pending()

    async def commit(self) -> None:
        self._commit_pending()

    async def refresh(self, row: Any) -> None:
        pass  # row already has all fields set

    async def delete(self, row: Any) -> None:
        from api.models.database import MemoryConfig as MCfgORM

        if isinstance(row, MCfgORM):
            cid = str(row.id)
            self._store.configs.pop(cid, None)
            # cascade
            self._store.messages = {
                k: v for k, v in self._store.messages.items() if str(v.config_id) != cid
            }

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self._store, stmt)

    def _commit_pending(self) -> None:
        from api.models.database import MemoryConfig as MCfgORM
        from api.models.database import MemoryMessage as MMsgORM

        for row in self._pending:
            if isinstance(row, MCfgORM):
                # Set created_at / updated_at if missing
                if not hasattr(row, "created_at") or row.created_at is None:
                    row.created_at = datetime.now(UTC)
                    row.updated_at = datetime.now(UTC)
                self._store.configs[str(row.id)] = row
            elif isinstance(row, MMsgORM):
                if not hasattr(row, "created_at") or row.created_at is None:
                    row.created_at = datetime.now(UTC)
                self._store.messages[str(row.id)] = row
        self._pending.clear()


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value

    def scalars(self) -> _Scalars:
        return _Scalars(self._value if isinstance(self._value, list) else [])

    def all(self) -> list[Any]:
        return self._value if isinstance(self._value, list) else []


class _Scalars:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _FakeResult:
    """Evaluates SQLAlchemy-ish statements against the in-memory store."""

    def __init__(self, store: _Store, stmt: Any) -> None:
        self._store = store
        self._stmt = stmt
        self._value: Any = None
        self._rows: list[Any] = []
        self._evaluated = False

    def _evaluate(self) -> None:
        if self._evaluated:
            return
        self._evaluated = True

        stmt = self._stmt

        # DELETE statement
        if hasattr(stmt, "is_delete") or (hasattr(stmt, "entity_zero") and False):
            pass

        # Try to detect statement type by inspecting the statement
        stmt_str = str(type(stmt).__name__)

        if "Delete" in stmt_str:
            self._handle_delete(stmt)
        elif "Select" in stmt_str:
            self._handle_select(stmt)
        else:
            self._value = 0

    def _handle_delete(self, stmt: Any) -> None:

        # Determine what's being deleted based on table
        table_name = ""
        try:
            table_name = stmt.entity_zero().local_table.name
        except Exception:
            try:
                table_name = stmt.table.name
            except Exception:
                pass

        if "message" in table_name:
            criteria = self._extract_where_criteria(stmt)
            before_count = len(self._store.messages)
            self._store.messages = {
                k: v
                for k, v in self._store.messages.items()
                if not self._message_matches(v, criteria)
            }
            self._value = before_count - len(self._store.messages)
        else:
            self._value = 0

    def _message_matches(self, msg: Any, criteria: dict[str, Any]) -> bool:
        for field, value in criteria.items():
            if field == "config_id" and str(msg.config_id) != str(value):
                return False
            if field == "session_id" and msg.session_id != value:
                return False
            if field == "before" and msg.created_at >= value:
                return False
            if field == "id_in" and msg.id not in value:
                return False
        return True

    def _extract_where_criteria(self, stmt: Any) -> dict[str, Any]:
        # We use a simpler approach: compile the where clauses by inspecting
        # them. This is fragile but sufficient for our specific queries.
        return {}

    def _handle_select(self, stmt: Any) -> None:
        # Detect which entity is being selected from the compiled column list
        try:
            cols = stmt.column_descriptions
            entity = cols[0].get("entity") if cols else None
        except Exception:
            entity = None

        from api.models.database import MemoryConfig as MCfgORM
        from api.models.database import MemoryMessage as MMsgORM

        if entity is MCfgORM:
            items = sorted(self._store.configs.values(), key=lambda x: x.name)
            self._rows = items
            self._value = items
        elif entity is MMsgORM:
            items = sorted(self._store.messages.values(), key=lambda x: x.created_at)
            self._rows = items
            self._value = items
        else:
            # Aggregate or column query — return 0 or empty
            self._value = 0
            self._rows = []

    def scalar_one(self) -> Any:
        self._evaluate()
        return self._value if not isinstance(self._value, list) else len(self._value)

    def scalars(self) -> _Scalars:
        self._evaluate()
        items = self._value if isinstance(self._value, list) else []
        return _Scalars(items)

    def all(self) -> list[Any]:
        self._evaluate()
        return self._rows if self._rows else (self._value if isinstance(self._value, list) else [])

    @property
    def rowcount(self) -> int:
        self._evaluate()
        return self._value if isinstance(self._value, int) else 0


# ---------------------------------------------------------------------------
# The real mock fixture — patches async_session to use FakeSession
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_db():
    """Patch async_session with a FakeSession backed by in-memory dicts."""
    store = _Store()

    @asynccontextmanager
    async def _fake_session_ctx():
        yield _FakeSession(store)

    with patch("api.services.memory_service.async_session", _fake_session_ctx):
        yield store


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


class TestConfigCRUD:
    @pytest.mark.asyncio
    async def test_create_config(self) -> None:
        config = await MemoryService.create_config(name="test-mem", backend_type="postgresql")
        assert config.name == "test-mem"
        assert config.backend_type == "postgresql"
        assert config.memory_type == "buffer_window"
        assert config.max_messages == 100
        assert config.id

    @pytest.mark.asyncio
    async def test_create_config_buffer_backend(self) -> None:
        config = await MemoryService.create_config(
            name="pg-mem", backend_type="postgresql", memory_type="buffer"
        )
        assert config.backend_type == "postgresql"
        assert config.memory_type == "buffer"

    @pytest.mark.asyncio
    async def test_create_config_accepts_phase2_memory_type(self) -> None:
        # summary/entity/semantic are Phase 2 and now live — must be accepted
        config = await MemoryService.create_config(name="summary-mem", memory_type="summary")
        assert config.memory_type == "summary"

    @pytest.mark.asyncio
    async def test_create_config_accepts_team_scope(self) -> None:
        # team scope is now live (Phase 2) — must be accepted
        config = await MemoryService.create_config(name="team-mem", scope="team")
        assert config.scope == "team"

    @pytest.mark.asyncio
    async def test_create_config_rejects_global_scope(self) -> None:
        from fastapi import HTTPException

        # global scope remains Phase 3 — must be rejected
        with pytest.raises(HTTPException) as exc_info:
            await MemoryService.create_config(name="global-mem", scope="global")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_get_config(self) -> None:
        config = await MemoryService.create_config(name="get-test")
        fetched = await MemoryService.get_config(config.id)
        assert fetched is not None
        assert fetched.name == "get-test"

    @pytest.mark.asyncio
    async def test_get_config_not_found(self, mock_db: _Store) -> None:
        result = await MemoryService.get_config(str(uuid.uuid4()))
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_config(self) -> None:
        config = await MemoryService.create_config(name="to-delete")
        assert await MemoryService.delete_config(config.id) is True
        assert await MemoryService.get_config(config.id) is None

    @pytest.mark.asyncio
    async def test_delete_config_not_found(self) -> None:
        assert await MemoryService.delete_config(str(uuid.uuid4())) is False


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
            metadata={"tokens": 42},
        )
        assert msg is not None
        assert msg.metadata == {"tokens": 42}

    @pytest.mark.asyncio
    async def test_store_message_config_not_found(self) -> None:
        result = await MemoryService.store_message(
            str(uuid.uuid4()), session_id="s1", role="user", content="Hi"
        )
        assert result is None


# ---------------------------------------------------------------------------
# Phase 2 validation
# ---------------------------------------------------------------------------


class TestPhase2Validation:
    @pytest.mark.asyncio
    async def test_entity_type_accepted(self) -> None:
        # entity memory type is Phase 2 and now live
        config = await MemoryService.create_config(name="entity-mem", memory_type="entity")
        assert config.memory_type == "entity"

    @pytest.mark.asyncio
    async def test_semantic_type_accepted(self) -> None:
        # semantic memory type is Phase 2 and now live
        config = await MemoryService.create_config(name="sem-mem", memory_type="semantic")
        assert config.memory_type == "semantic"

    @pytest.mark.asyncio
    async def test_global_scope_rejected(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await MemoryService.create_config(name="global-mem", scope="global")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_agent_scope_accepted(self) -> None:
        config = await MemoryService.create_config(name="agent-mem", scope="agent")
        assert config.scope == "agent"

    @pytest.mark.asyncio
    async def test_buffer_window_type_accepted(self) -> None:
        config = await MemoryService.create_config(name="bw-mem", memory_type="buffer_window")
        assert config.memory_type == "buffer_window"

    @pytest.mark.asyncio
    async def test_buffer_type_accepted(self) -> None:
        config = await MemoryService.create_config(name="buf-mem", memory_type="buffer")
        assert config.memory_type == "buffer"


# ---------------------------------------------------------------------------
# Stats (basic — aggregate queries return 0 from FakeSession)
# ---------------------------------------------------------------------------


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_not_found(self) -> None:
        stats = await MemoryService.get_stats(str(uuid.uuid4()))
        assert stats is None

    @pytest.mark.asyncio
    async def test_stats_config_found(self) -> None:
        config = await MemoryService.create_config(name="stats-config")
        stats = await MemoryService.get_stats(config.id)
        assert stats is not None
        assert stats.config_id == config.id
        assert stats.backend_type == config.backend_type


# ---------------------------------------------------------------------------
# MM7: Team-isolation documentation tests
#
# The service layer already gates ``store_message`` and ``get_conversation``
# on ``requesting_team`` — but the HTTP route layer DOES NOT yet pass the
# caller's team in. These xfail tests document the gap; HR-1 (the real fix)
# will pass the team through and flip these to passing.
# ---------------------------------------------------------------------------


class TestTeamIsolationCurrentBehavior:
    """Document current memory team-isolation behavior (HR-1 follow-up).

    Service-layer enforcement exists and works (the ``requesting_team``
    kwarg is honoured), but routes do not pass the caller's team into the
    service. These tests are marked xfail so they're tracked in CI without
    blocking; when HR-1 wires the team through, they become real assertions.
    """

    @pytest.mark.asyncio
    async def test_service_layer_blocks_cross_team_store(self) -> None:
        """Service-level enforcement IS implemented — this should pass today."""
        config = await MemoryService.create_config(name="team-a-mem", scope="team", team="team-a")
        # Wrong team trying to write — should raise.
        with pytest.raises(PermissionError):
            await MemoryService.store_message(
                config.id,
                session_id="s1",
                role="user",
                content="leak",
                requesting_team="team-b",
            )

    @pytest.mark.asyncio
    async def test_service_layer_blocks_cross_team_read(self) -> None:
        """Service-level read enforcement IS implemented — this should pass today."""
        config = await MemoryService.create_config(name="team-a-read", scope="team", team="team-a")
        with pytest.raises(PermissionError):
            await MemoryService.get_conversation(
                config.id,
                "s1",
                requesting_team="team-b",
            )

    @pytest.mark.asyncio
    async def test_service_layer_allows_matching_team_store(self) -> None:
        """Same-team writes work — sanity check."""
        config = await MemoryService.create_config(name="team-a-ok", scope="team", team="team-a")
        msg = await MemoryService.store_message(
            config.id,
            session_id="s1",
            role="user",
            content="hello",
            requesting_team="team-a",
        )
        assert msg is not None

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="HR-1: team-scope not yet enforced at runtime")
    async def test_route_layer_passes_caller_team_on_store(self) -> None:
        """Routes SHOULD pass ``requesting_team`` to ``store_message``.

        Today the ``store_message`` route does not forward the caller's team,
        so a cross-team write that should be rejected silently succeeds at
        the HTTP layer. HR-1 will wire the team through; this test flips to
        passing then.
        """
        import inspect

        from api.routes import memory as memory_routes

        source = inspect.getsource(memory_routes.store_message)
        assert "requesting_team" in source, (
            "store_message route does not forward the caller's team — HR-1 needed"
        )

    @pytest.mark.asyncio
    @pytest.mark.xfail(reason="HR-1: team-scope not yet enforced at runtime")
    async def test_route_layer_passes_caller_team_on_read(self) -> None:
        """Routes SHOULD pass ``requesting_team`` to ``get_conversation``.

        Same gap as the write path — HR-1 will close it.
        """
        import inspect

        from api.routes import memory as memory_routes

        source = inspect.getsource(memory_routes.get_conversation)
        assert "requesting_team" in source, (
            "get_conversation route does not forward the caller's team — HR-1 needed"
        )


# ---------------------------------------------------------------------------
# MM8: TTL field + cleanup_expired_messages
# ---------------------------------------------------------------------------


class TestTtlField:
    @pytest.mark.asyncio
    async def test_create_config_default_ttl_is_none(self) -> None:
        config = await MemoryService.create_config(name="no-ttl")
        assert config.ttl_seconds is None

    @pytest.mark.asyncio
    async def test_create_config_accepts_ttl_seconds(self) -> None:
        config = await MemoryService.create_config(name="with-ttl", ttl_seconds=3600)
        assert config.ttl_seconds == 3600

    @pytest.mark.asyncio
    async def test_get_config_round_trips_ttl(self) -> None:
        config = await MemoryService.create_config(name="rt-ttl", ttl_seconds=86400)
        fetched = await MemoryService.get_config(config.id)
        assert fetched is not None
        assert fetched.ttl_seconds == 86400

    @pytest.mark.asyncio
    async def test_cleanup_expired_messages_no_ttl_is_noop(self) -> None:
        """Configs without ttl_seconds are skipped — nothing deleted."""
        config = await MemoryService.create_config(name="no-ttl-cleanup")
        await MemoryService.store_message(
            config.id, session_id="s1", role="user", content="keep-me"
        )
        deleted = await MemoryService.cleanup_expired_messages(config_id=config.id)
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_cleanup_expired_messages_returns_int(self) -> None:
        """Method exists and returns an int — global sweep is a no-op here."""
        result = await MemoryService.cleanup_expired_messages()
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# MM9: GDPR delete-by-user
# ---------------------------------------------------------------------------


class TestDeleteByUserId:
    @pytest.mark.asyncio
    async def test_delete_messages_by_empty_user_id_is_noop(self) -> None:
        """Empty user_id returns 0 immediately without touching the DB."""
        deleted = await MemoryService.delete_messages_by_user_id("")
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_delete_messages_by_user_id_returns_int(self) -> None:
        """The method signature returns an int — exercised against a fake DB."""
        # Patch the DB call to a no-op so we don't try to execute raw SQL on
        # the FakeSession.
        from contextlib import asynccontextmanager
        from unittest.mock import patch as _patch

        class _Result:
            rowcount = 0

        class _DB:
            async def execute(self, *_a: Any, **_kw: Any) -> _Result:
                return _Result()

            async def commit(self) -> None:
                pass

        @asynccontextmanager
        async def _ctx() -> Any:
            yield _DB()

        with _patch("api.services.memory_service.async_session", _ctx):
            deleted = await MemoryService.delete_messages_by_user_id("alice@example.com")
        assert deleted == 0


# ---------------------------------------------------------------------------
# MM10: Observability log emission
# ---------------------------------------------------------------------------


class TestObservabilityLogs:
    @pytest.mark.asyncio
    async def test_store_message_emits_structured_log(self, caplog) -> None:
        import logging

        config = await MemoryService.create_config(name="obs-store")
        with caplog.at_level(logging.INFO, logger="api.services.memory_service"):
            await MemoryService.store_message(
                config.id, session_id="s1", role="user", content="hello world"
            )

        # Find the store_message log entry
        store_records = [r for r in caplog.records if r.message == "memory.store_message"]
        assert store_records, f"no memory.store_message log emitted: {caplog.records}"
        rec = store_records[-1]
        # Verify the structured "extra" fields are attached.
        assert getattr(rec, "config_id", None) == config.id
        assert getattr(rec, "size_bytes", None) == len(b"hello world")

    @pytest.mark.asyncio
    async def test_search_messages_emits_structured_log(self, caplog) -> None:
        import logging

        config = await MemoryService.create_config(name="obs-search")
        with caplog.at_level(logging.INFO, logger="api.services.memory_service"):
            await MemoryService.search_messages(config.id, query="needle", limit=10)

        search_records = [r for r in caplog.records if r.message == "memory.search"]
        assert search_records, f"no memory.search log emitted: {caplog.records}"
        rec = search_records[-1]
        assert getattr(rec, "config_id", None) == config.id
        assert getattr(rec, "query_len", None) == len("needle")
        assert hasattr(rec, "duration_ms")
        assert hasattr(rec, "result_count")
