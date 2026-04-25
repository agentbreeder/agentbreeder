"""Unit tests for feature #108 — memory field in agent.yaml + MemoryManager.

Covers:
- AgentConfig parses memory: field correctly (stores list)
- AgentConfig without memory: field defaults to None
- JSON Schema accepts and rejects memory: configs
- MemoryManager.load returns [] for a new session (no-backend / redis / postgresql)
- MemoryManager.save + load round-trips (mock redis, mock asyncpg)
- Resolver propagates memory store refs
- LangGraph server template calls load before invoke and save after (mock _memory)
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    f.write(content)
    f.close()
    return Path(f.name)


MINIMAL_YAML = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""

MEMORY_YAML = """\
name: mem-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
memory:
  stores:
    - session-buffer
    - entity-store
deploy:
  cloud: local
"""


# ---------------------------------------------------------------------------
# 1. AgentConfig parses memory: correctly
# ---------------------------------------------------------------------------


class TestAgentConfigMemoryField:
    def test_memory_field_absent_defaults_to_none(self):
        from engine.config_parser import parse_config

        path = _write_yaml(MINIMAL_YAML)
        config = parse_config(path)
        assert config.memory is None

    def test_memory_field_parses_stores(self):
        from engine.config_parser import parse_config

        path = _write_yaml(MEMORY_YAML)
        config = parse_config(path)
        assert config.memory is not None
        assert config.memory.stores == ["session-buffer", "entity-store"]

    def test_memory_empty_stores(self):
        from engine.config_parser import parse_config

        yaml_content = MINIMAL_YAML + "memory:\n  stores: []\n"
        path = _write_yaml(yaml_content)
        config = parse_config(path)
        assert config.memory is not None
        assert config.memory.stores == []

    def test_memory_config_model_direct(self):
        from engine.config_parser import MemoryConfig

        mc = MemoryConfig(stores=["session-buffer"])
        assert mc.stores == ["session-buffer"]

    def test_memory_config_default_empty(self):
        from engine.config_parser import MemoryConfig

        mc = MemoryConfig()
        assert mc.stores == []


# ---------------------------------------------------------------------------
# 2. JSON Schema validation
# ---------------------------------------------------------------------------


class TestSchemaMemoryProperty:
    def test_schema_accepts_valid_memory(self):
        from engine.config_parser import validate_config

        path = _write_yaml(MEMORY_YAML)
        result = validate_config(path)
        assert result.valid, result.errors

    def test_schema_accepts_absent_memory(self):
        from engine.config_parser import validate_config

        path = _write_yaml(MINIMAL_YAML)
        result = validate_config(path)
        assert result.valid, result.errors

    def test_schema_rejects_extra_memory_key(self):
        from engine.config_parser import validate_config

        bad_yaml = MINIMAL_YAML + "memory:\n  stores: []\n  unknown_key: bad\n"
        path = _write_yaml(bad_yaml)
        result = validate_config(path)
        assert not result.valid


# ---------------------------------------------------------------------------
# 3. Resolver propagates memory refs
# ---------------------------------------------------------------------------


class TestResolverMemoryRefs:
    def test_no_memory_no_refs(self):
        from engine.config_parser import parse_config
        from engine.resolver import resolve_dependencies

        path = _write_yaml(MINIMAL_YAML)
        config = parse_config(path)

        with patch("engine.resolver.generate_subagent_tools", return_value=[]):
            resolved = resolve_dependencies(config)

        assert resolved.memory is None

    def test_memory_refs_logged(self, caplog):
        import logging

        from engine.config_parser import parse_config
        from engine.resolver import resolve_dependencies

        path = _write_yaml(MEMORY_YAML)
        config = parse_config(path)

        with patch("engine.resolver.generate_subagent_tools", return_value=[]):
            with caplog.at_level(logging.DEBUG, logger="engine.resolver"):
                resolved = resolve_dependencies(config)

        assert resolved.memory is not None
        assert resolved.memory.stores == ["session-buffer", "entity-store"]


# ---------------------------------------------------------------------------
# 4. MemoryManager — no-backend (MEMORY_BACKEND=none)
# ---------------------------------------------------------------------------


def _import_memory_manager():
    """Import memory_manager from the templates directory."""
    sys.path.insert(0, "engine/runtimes/templates")
    # Force re-import so env changes take effect
    for key in list(sys.modules.keys()):
        if key == "memory_manager":
            del sys.modules[key]
    import memory_manager as mm  # noqa: PLC0415

    return mm


class TestMemoryManagerNoneBackend:
    @pytest.mark.asyncio
    async def test_load_returns_empty_list_new_session(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "none")
        monkeypatch.setenv("AGENT_NAME", "test-agent")
        mm = _import_memory_manager()
        manager = mm.MemoryManager()
        await manager.connect()
        result = await manager.load("session-abc")
        assert result == []

    @pytest.mark.asyncio
    async def test_save_is_noop_for_none_backend(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "none")
        mm = _import_memory_manager()
        manager = mm.MemoryManager()
        await manager.connect()
        # Should not raise
        await manager.save("session-abc", [{"role": "user", "content": "hello"}])

    @pytest.mark.asyncio
    async def test_close_is_safe_when_no_connections(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "none")
        mm = _import_memory_manager()
        manager = mm.MemoryManager()
        await manager.connect()
        await manager.close()  # Should not raise


# ---------------------------------------------------------------------------
# 5. MemoryManager — Redis backend (mock redis.asyncio)
# ---------------------------------------------------------------------------


class TestMemoryManagerRedisBackend:
    def _make_mock_redis(self, stored: dict[str, str] | None = None):
        """Build a mock redis.asyncio client."""
        store = stored or {}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=lambda key: store.get(key))
        mock_client.set = AsyncMock(side_effect=lambda key, value: store.update({key: value}))
        mock_client.aclose = AsyncMock()
        return mock_client, store

    @pytest.mark.asyncio
    async def test_load_returns_empty_list_for_new_session(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "redis")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        monkeypatch.setenv("AGENT_NAME", "test-agent")

        mock_client, _ = self._make_mock_redis()

        fake_aioredis = types.ModuleType("redis.asyncio")
        fake_aioredis.from_url = AsyncMock(return_value=mock_client)  # type: ignore[attr-defined]

        fake_redis = types.ModuleType("redis")
        fake_redis.asyncio = fake_aioredis  # type: ignore[attr-defined]

        mm = _import_memory_manager()
        with patch.dict(sys.modules, {"redis": fake_redis, "redis.asyncio": fake_aioredis}):
            manager = mm.MemoryManager()
            manager._backend = "redis"
            manager._redis = mock_client
            result = await manager.load("session-new")

        assert result == []

    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "redis")
        monkeypatch.setenv("AGENT_NAME", "test-agent")

        mock_client, store = self._make_mock_redis()

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        mm = _import_memory_manager()
        manager = mm.MemoryManager()
        manager._backend = "redis"
        manager._redis = mock_client

        await manager.save("sess-1", messages)
        loaded = await manager.load("sess-1")

        assert loaded == messages

    @pytest.mark.asyncio
    async def test_load_error_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "redis")
        monkeypatch.setenv("AGENT_NAME", "test-agent")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("redis down"))

        mm = _import_memory_manager()
        manager = mm.MemoryManager()
        manager._backend = "redis"
        manager._redis = mock_client

        # Should not raise — returns []
        result = await manager.load("sess-error")
        assert result == []


# ---------------------------------------------------------------------------
# 6. MemoryManager — PostgreSQL backend (mock asyncpg)
# ---------------------------------------------------------------------------


class TestMemoryManagerPostgresBackend:
    def _make_mock_pg_pool(self, row_payload: Any = None):
        """Return a mock asyncpg pool."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=None)
        if row_payload is None:
            mock_conn.fetchrow = AsyncMock(return_value=None)
        else:
            mock_row = {"payload": row_payload}
            mock_conn.fetchrow = AsyncMock(return_value=mock_row)

        # asyncpg uses async context manager for acquire()
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_AsyncContextManager(mock_conn))
        mock_pool.close = AsyncMock()
        return mock_pool, mock_conn

    @pytest.mark.asyncio
    async def test_load_returns_empty_list_for_new_session(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "postgresql")
        monkeypatch.setenv("AGENT_NAME", "test-agent")

        mock_pool, _ = self._make_mock_pg_pool(row_payload=None)

        mm = _import_memory_manager()
        manager = mm.MemoryManager()
        manager._backend = "postgresql"
        manager._pg_pool = mock_pool

        result = await manager.load("sess-new")
        assert result == []

    @pytest.mark.asyncio
    async def test_load_returns_stored_messages(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "postgresql")
        monkeypatch.setenv("AGENT_NAME", "test-agent")

        messages = [{"role": "user", "content": "hello"}]
        # Simulate asyncpg returning already-decoded list
        mock_pool, _ = self._make_mock_pg_pool(row_payload=messages)

        mm = _import_memory_manager()
        manager = mm.MemoryManager()
        manager._backend = "postgresql"
        manager._pg_pool = mock_pool

        result = await manager.load("sess-abc")
        assert result == messages

    @pytest.mark.asyncio
    async def test_save_calls_upsert(self, monkeypatch):
        monkeypatch.setenv("MEMORY_BACKEND", "postgresql")
        monkeypatch.setenv("AGENT_NAME", "test-agent")

        mock_pool, mock_conn = self._make_mock_pg_pool()

        mm = _import_memory_manager()
        manager = mm.MemoryManager()
        manager._backend = "postgresql"
        manager._pg_pool = mock_pool

        messages = [{"role": "user", "content": "hi"}]
        await manager.save("sess-pg", messages)

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        # First positional arg should contain INSERT or UPSERT SQL
        sql = call_args[0][0]
        assert "INSERT" in sql or "insert" in sql.lower()


# ---------------------------------------------------------------------------
# 7. LangGraph server: load before invoke, save after
# ---------------------------------------------------------------------------


def _import_server():
    for key in list(sys.modules.keys()):
        if "langgraph_server" in key:
            del sys.modules[key]
    sys.path.insert(0, "engine/runtimes/templates")
    import langgraph_server as srv  # noqa: PLC0415

    return srv


class TestLangGraphServerMemoryWiring:
    @pytest.mark.asyncio
    async def test_invoke_loads_memory_before_agent(self, monkeypatch):
        """Memory.load is called before the agent is invoked."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("MEMORY_BACKEND", raising=False)

        # Stub out langgraph internals
        _stub_langgraph_modules()

        srv = _import_server()

        prior_msgs = [{"role": "user", "content": "prior"}]
        mock_memory = AsyncMock()
        mock_memory.load = AsyncMock(return_value=prior_msgs)
        mock_memory.save = AsyncMock()

        mock_agent = AsyncMock()
        mock_agent.ainvoke = AsyncMock(
            return_value={"messages": [{"role": "assistant", "content": "hello"}]}
        )
        # No interrupt state
        mock_agent.aget_state = AsyncMock(return_value=MagicMock(next=[]))

        srv._agent = mock_agent
        srv._memory = mock_memory

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=srv.app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/invoke",
                json={
                    "input": {"messages": [{"role": "user", "content": "hello"}]},
                    "thread_id": "t-001",
                },
            )

        assert resp.status_code == 200
        mock_memory.load.assert_called_once_with("t-001")

    @pytest.mark.asyncio
    async def test_invoke_saves_memory_after_agent(self, monkeypatch):
        """Memory.save is called after a successful agent response."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        _stub_langgraph_modules()
        srv = _import_server()

        response_msgs = [{"role": "assistant", "content": "done"}]
        mock_memory = AsyncMock()
        mock_memory.load = AsyncMock(return_value=[])
        mock_memory.save = AsyncMock()

        mock_agent = AsyncMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": response_msgs})
        mock_agent.aget_state = AsyncMock(return_value=MagicMock(next=[]))

        srv._agent = mock_agent
        srv._memory = mock_memory

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=srv.app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/invoke",
                json={"input": {"messages": []}, "thread_id": "t-002"},
            )

        assert resp.status_code == 200
        mock_memory.save.assert_called_once_with("t-002", response_msgs)

    @pytest.mark.asyncio
    async def test_invoke_works_without_memory_manager(self, monkeypatch):
        """When _memory is None (no MEMORY_BACKEND), invoke still works."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        _stub_langgraph_modules()
        srv = _import_server()

        mock_agent = AsyncMock()
        mock_agent.ainvoke = AsyncMock(return_value={"output": "ok"})
        mock_agent.aget_state = AsyncMock(return_value=MagicMock(next=[]))

        srv._agent = mock_agent
        srv._memory = None  # explicitly disabled

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=srv.app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/invoke",
                json={"input": {"messages": []}, "thread_id": "t-003"},
            )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncContextManager:
    """Minimal async context manager wrapping a mock connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def __aenter__(self) -> Any:
        future: Any = AsyncMock(return_value=self._conn)
        return future()

    def __aexit__(self, *args: Any) -> Any:
        future: Any = AsyncMock(return_value=None)
        return future()


def _stub_langgraph_modules() -> None:
    """Inject minimal langgraph stubs so the server template can be imported."""
    fake_lg = types.ModuleType("langgraph")
    fake_graph = types.ModuleType("langgraph.graph")

    class FakeStateGraph:
        pass

    fake_graph.StateGraph = FakeStateGraph  # type: ignore[attr-defined]
    fake_lg.graph = fake_graph  # type: ignore[attr-defined]

    fake_memory_mod = types.ModuleType("langgraph.checkpoint.memory")
    fake_memory_mod.MemorySaver = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

    fake_pg = types.ModuleType("langgraph.checkpoint.postgres.aio")
    fake_pg.AsyncPostgresSaver = MagicMock()  # type: ignore[attr-defined]

    sys.modules.setdefault("langgraph", fake_lg)
    sys.modules.setdefault("langgraph.graph", fake_graph)
    sys.modules.setdefault("langgraph.checkpoint.memory", fake_memory_mod)
    sys.modules.setdefault("langgraph.checkpoint.postgres.aio", fake_pg)
