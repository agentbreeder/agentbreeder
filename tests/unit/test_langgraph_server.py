"""Unit tests for engine/runtimes/templates/langgraph_server.py.

Tests cover:
- Checkpointer auto-selection (MemorySaver vs AsyncPostgresSaver)
- thread_id generation and propagation
- HITL interrupt detection in /invoke
- /resume endpoint
- Subgraph StateGraph warning
"""

from __future__ import annotations

import sys
import types
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers to import the server module without side-effects
# ---------------------------------------------------------------------------


def _import_server():
    """Import (or re-import) the langgraph_server module cleanly."""
    # Remove any cached version so env-var changes take effect.
    for key in list(sys.modules.keys()):
        if "langgraph_server" in key:
            del sys.modules[key]

    sys.path.insert(0, "engine/runtimes/templates")
    import langgraph_server as srv  # noqa: PLC0415

    return srv


# ---------------------------------------------------------------------------
# _get_checkpointer tests
# ---------------------------------------------------------------------------


class TestGetCheckpointer:
    def test_memory_saver_when_no_database_url(self, monkeypatch):
        """MemorySaver is returned when DATABASE_URL is not set."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        mock_memory_saver_cls = MagicMock(name="MemorySaver")
        mock_memory_saver_instance = MagicMock(name="MemorySaverInstance")
        mock_memory_saver_cls.return_value = mock_memory_saver_instance

        fake_checkpoint_memory = types.ModuleType("langgraph.checkpoint.memory")
        fake_checkpoint_memory.MemorySaver = mock_memory_saver_cls  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"langgraph.checkpoint.memory": fake_checkpoint_memory}):
            srv = _import_server()
            result = srv._get_checkpointer()

        mock_memory_saver_cls.assert_called_once()
        assert result is mock_memory_saver_instance

    def test_postgres_saver_when_database_url_set(self, monkeypatch):
        """AsyncPostgresSaver is returned when DATABASE_URL is set."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")

        mock_pg_cls = MagicMock(name="AsyncPostgresSaver")
        mock_pg_instance = MagicMock(name="AsyncPostgresSaverInstance")
        mock_pg_cls.from_conn_string.return_value = mock_pg_instance

        fake_pg_module = types.ModuleType("langgraph.checkpoint.postgres.aio")
        fake_pg_module.AsyncPostgresSaver = mock_pg_cls  # type: ignore[attr-defined]

        # Also stub the parent package path so Python can resolve the submodule.
        fake_pg_parent = types.ModuleType("langgraph.checkpoint.postgres")
        fake_pg_grandparent = types.ModuleType("langgraph.checkpoint")

        with patch.dict(
            sys.modules,
            {
                "langgraph.checkpoint.postgres.aio": fake_pg_module,
                "langgraph.checkpoint.postgres": fake_pg_parent,
                "langgraph.checkpoint": fake_pg_grandparent,
            },
        ):
            srv = _import_server()
            result = srv._get_checkpointer()

        mock_pg_cls.from_conn_string.assert_called_once_with(
            "postgresql+asyncpg://user:pass@localhost/db"
        )
        assert result is mock_pg_instance

    def test_falls_back_to_memory_saver_when_postgres_import_fails(self, monkeypatch):
        """Falls back to MemorySaver if postgres package is missing despite DATABASE_URL."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")

        mock_memory_saver_cls = MagicMock(name="MemorySaver")
        mock_memory_saver_instance = MagicMock(name="MemorySaverInstance")
        mock_memory_saver_cls.return_value = mock_memory_saver_instance

        fake_checkpoint_memory = types.ModuleType("langgraph.checkpoint.memory")
        fake_checkpoint_memory.MemorySaver = mock_memory_saver_cls  # type: ignore[attr-defined]

        # Force the postgres import to raise ImportError
        with patch.dict(sys.modules, {"langgraph.checkpoint.memory": fake_checkpoint_memory}):
            srv = _import_server()
            with patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": None}):  # type: ignore[dict-item]
                result = srv._get_checkpointer()

        assert result is mock_memory_saver_instance


# ---------------------------------------------------------------------------
# Fixtures — build a fully mocked FastAPI app for endpoint tests
# ---------------------------------------------------------------------------


def _build_mock_app(
    *,
    agent_ainvoke_result: Any = None,
    state_next: list[str] | None = None,
):
    """Return an httpx.AsyncClient wired to a fresh langgraph_server app
    with _agent replaced by a controllable mock."""

    srv = _import_server()
    default_invoke = (
        agent_ainvoke_result if agent_ainvoke_result is not None else {"answer": "hello"}
    )

    # Build a mock agent with async invoke and aget_state.
    mock_agent = MagicMock(name="MockAgent")
    mock_agent.ainvoke = AsyncMock(return_value=default_invoke)

    # aget_state returns a state object whose .next attribute controls HITL.
    mock_state = MagicMock()
    mock_state.next = state_next or []
    mock_agent.aget_state = AsyncMock(return_value=mock_state)

    # Patch _agent directly (startup is not called in these tests).
    srv._agent = mock_agent

    return srv, mock_agent


@pytest_asyncio.fixture
async def normal_client():
    srv, mock_agent = _build_mock_app(agent_ainvoke_result={"answer": "hello"})
    transport = ASGITransport(app=srv.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, mock_agent
        srv._agent = None  # reset


@pytest_asyncio.fixture
async def hitl_client():
    """Client whose agent is mid-interrupt (state.next has pending nodes)."""
    srv, mock_agent = _build_mock_app(
        agent_ainvoke_result={"partial": "output"},
        state_next=["human_review"],
    )
    transport = ASGITransport(app=srv.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, mock_agent, srv
        srv._agent = None


# ---------------------------------------------------------------------------
# /invoke — thread_id tests
# ---------------------------------------------------------------------------


class TestInvokeThreadId:
    @pytest.mark.asyncio
    async def test_thread_id_generated_when_not_provided(self, normal_client):
        client, mock_agent = normal_client
        response = await client.post("/invoke", json={"input": {"query": "hi"}})
        assert response.status_code == 200
        body = response.json()
        assert "thread_id" in body
        assert body["thread_id"] is not None
        # Should be a valid UUID string.
        parsed = uuid.UUID(body["thread_id"])
        assert str(parsed) == body["thread_id"]

    @pytest.mark.asyncio
    async def test_thread_id_preserved_when_provided(self, normal_client):
        client, mock_agent = normal_client
        custom_tid = "my-custom-thread-123"
        response = await client.post(
            "/invoke", json={"input": {"query": "hi"}, "thread_id": custom_tid}
        )
        assert response.status_code == 200
        assert response.json()["thread_id"] == custom_tid

    @pytest.mark.asyncio
    async def test_thread_id_passed_to_agent_as_configurable(self, normal_client):
        """thread_id must be forwarded to the graph via config.configurable."""
        client, mock_agent = normal_client
        custom_tid = "tid-abc"
        await client.post("/invoke", json={"input": {"query": "hi"}, "thread_id": custom_tid})
        call_kwargs = mock_agent.ainvoke.call_args
        config_arg = call_kwargs.kwargs.get("config") or call_kwargs.args[1]
        assert config_arg["configurable"]["thread_id"] == custom_tid

    @pytest.mark.asyncio
    async def test_output_returned_in_response(self, normal_client):
        client, mock_agent = normal_client
        response = await client.post("/invoke", json={"input": {"query": "hi"}})
        assert response.status_code == 200
        assert response.json()["output"] == {"answer": "hello"}


# ---------------------------------------------------------------------------
# /invoke — HITL interrupt detection
# ---------------------------------------------------------------------------


class TestHITLInterrupt:
    @pytest.mark.asyncio
    async def test_interrupted_response_when_state_has_next_nodes(self, hitl_client):
        client, mock_agent, srv = hitl_client
        response = await client.post("/invoke", json={"input": {"query": "approve?"}})
        assert response.status_code == 200
        body = response.json()
        assert body["output"]["status"] == "interrupted"
        assert body["metadata"]["interrupted"] is True
        assert "human_review" in body["output"]["awaiting"]

    @pytest.mark.asyncio
    async def test_thread_id_in_interrupted_response(self, hitl_client):
        client, mock_agent, srv = hitl_client
        tid = "thread-hitl-42"
        response = await client.post(
            "/invoke", json={"input": {"query": "approve?"}, "thread_id": tid}
        )
        assert response.json()["thread_id"] == tid
        assert response.json()["output"]["thread_id"] == tid

    @pytest.mark.asyncio
    async def test_normal_response_when_state_has_no_next_nodes(self, normal_client):
        """If state.next is empty, the response should NOT contain 'interrupted'."""
        client, mock_agent = normal_client
        response = await client.post("/invoke", json={"input": {"query": "hi"}})
        body = response.json()
        assert body.get("metadata") is None or not body.get("metadata", {}).get("interrupted")
        assert body["output"] != {"status": "interrupted"}


# ---------------------------------------------------------------------------
# /resume endpoint
# ---------------------------------------------------------------------------


class TestResumeEndpoint:
    @pytest.mark.asyncio
    async def test_resume_calls_command_with_human_input(self):
        """resume() must call ainvoke(Command(resume=...)) with the correct thread_id."""
        srv = _import_server()

        mock_agent = MagicMock()
        resume_output = {"answer": "done"}
        mock_agent.ainvoke = AsyncMock(return_value=resume_output)
        srv._agent = mock_agent

        # Stub langgraph.types.Command
        mock_command_cls = MagicMock(name="Command")
        mock_command_instance = MagicMock(name="CommandInstance")
        mock_command_cls.return_value = mock_command_instance

        fake_types_module = types.ModuleType("langgraph.types")
        fake_types_module.Command = mock_command_cls  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"langgraph.types": fake_types_module}):
            transport = ASGITransport(app=srv.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/resume",
                    json={"thread_id": "tid-resume-99", "human_input": "yes, proceed"},
                )

        assert response.status_code == 200
        body = response.json()
        assert body["thread_id"] == "tid-resume-99"
        assert body["output"] == resume_output

        # Verify Command was constructed with the human input
        mock_command_cls.assert_called_once_with(resume="yes, proceed")
        # Verify ainvoke was called with the Command instance and correct config
        mock_agent.ainvoke.assert_called_once_with(
            mock_command_instance,
            config={"configurable": {"thread_id": "tid-resume-99"}},
        )

        srv._agent = None  # cleanup

    @pytest.mark.asyncio
    async def test_resume_returns_503_when_agent_not_loaded(self):
        srv = _import_server()
        srv._agent = None

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/resume",
                json={"thread_id": "t1", "human_input": "ok"},
            )
        assert response.status_code == 503


# ---------------------------------------------------------------------------
# _load_agent — StateGraph warning
# ---------------------------------------------------------------------------


class TestLoadAgentStateGraphWarning:
    def test_warning_logged_for_uncompiled_state_graph(self, monkeypatch, caplog):
        """A warning must be logged when agent.py exports an uncompiled StateGraph."""
        import logging

        srv = _import_server()

        # Create a fake StateGraph class and instance
        class FakeStateGraph:
            pass

        fake_state_graph_instance = FakeStateGraph()

        # Fake agent module that exports an uncompiled graph
        fake_agent_module = types.ModuleType("agent")
        fake_agent_module.graph = fake_state_graph_instance  # type: ignore[attr-defined]

        # Fake langgraph.graph module with StateGraph pointing to our fake class
        fake_langgraph_graph = types.ModuleType("langgraph.graph")
        fake_langgraph_graph.StateGraph = FakeStateGraph  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {"agent": fake_agent_module, "langgraph.graph": fake_langgraph_graph},
        ):
            with caplog.at_level(logging.WARNING, logger="agentbreeder.agent"):
                result = srv._load_agent()

        assert result is fake_state_graph_instance
        assert any("uncompiled StateGraph" in record.message for record in caplog.records)

    def test_no_warning_for_compiled_graph(self, caplog):
        """No StateGraph warning when agent.py exports a compiled (non-StateGraph) graph."""
        import logging

        srv = _import_server()

        class CompiledGraph:
            """Represents a compiled graph (not a StateGraph)."""

            async def ainvoke(self, *args, **kwargs):
                return {}

        compiled_instance = CompiledGraph()

        fake_agent_module = types.ModuleType("agent")
        fake_agent_module.graph = compiled_instance  # type: ignore[attr-defined]

        class FakeStateGraph:
            pass

        fake_langgraph_graph = types.ModuleType("langgraph.graph")
        fake_langgraph_graph.StateGraph = FakeStateGraph  # type: ignore[attr-defined]

        with patch.dict(
            sys.modules,
            {"agent": fake_agent_module, "langgraph.graph": fake_langgraph_graph},
        ):
            with caplog.at_level(logging.WARNING, logger="agentbreeder.agent"):
                result = srv._load_agent()

        assert result is compiled_instance
        assert not any("uncompiled StateGraph" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Startup — checkpointer.setup() called when available
# ---------------------------------------------------------------------------


class TestStartupCheckpointerSetup:
    @pytest.mark.asyncio
    async def test_setup_called_on_checkpointer_that_has_it(self, monkeypatch):
        """startup() must await checkpointer.setup() for AsyncPostgresSaver."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        srv = _import_server()

        mock_checkpointer = MagicMock()
        mock_checkpointer.setup = AsyncMock()

        # Fake compiled agent
        mock_agent_obj = MagicMock()
        del mock_agent_obj.compile  # Ensure it looks already compiled

        with (
            patch.object(srv, "_load_agent", return_value=mock_agent_obj),
            patch.object(srv, "_get_checkpointer", return_value=mock_checkpointer),
        ):
            await srv.startup()

        mock_checkpointer.setup.assert_awaited_once()
        srv._agent = None

    @pytest.mark.asyncio
    async def test_setup_not_called_on_memory_saver(self, monkeypatch):
        """startup() must NOT call setup() on MemorySaver (it has no setup method)."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        srv = _import_server()

        # MemorySaver-like object: no setup attribute
        mock_checkpointer = MagicMock(spec=[])  # empty spec = no attributes

        mock_agent_obj = MagicMock()
        del mock_agent_obj.compile

        with (
            patch.object(srv, "_load_agent", return_value=mock_agent_obj),
            patch.object(srv, "_get_checkpointer", return_value=mock_checkpointer),
        ):
            await srv.startup()  # Should not raise

        srv._agent = None


# ---------------------------------------------------------------------------
# _load_agent — AttributeError when no recognised export (lines 86-88)
# ---------------------------------------------------------------------------


class TestLoadAgentAttributeError:
    def test_raises_attribute_error_when_no_recognised_export(self):
        """agent.py missing all known exports must raise AttributeError (lines 86-88)."""
        srv = _import_server()

        import types as _types

        fake_module = _types.ModuleType("agent")
        # No 'graph', 'app', 'workflow', or 'agent' attribute
        fake_module.something_else = "irrelevant"  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"agent": fake_module}):
            with pytest.raises(AttributeError, match="must export one of"):
                srv._load_agent()

    def test_raises_import_error_when_module_missing(self):
        srv = _import_server()

        with patch.dict(sys.modules, {"agent": None}):  # type: ignore[dict-item]
            with pytest.raises((ImportError, AttributeError)):
                srv._load_agent()

    def test_returns_workflow_attr_when_present(self):
        srv = _import_server()

        import types as _types

        fake_obj = object()
        fake_module = _types.ModuleType("agent")
        fake_module.workflow = fake_obj  # type: ignore[attr-defined]

        # Stub langgraph.graph so isinstance check doesn't import-error
        fake_lg_graph = _types.ModuleType("langgraph.graph")
        fake_lg_graph.StateGraph = type("StateGraph", (), {})  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"agent": fake_module, "langgraph.graph": fake_lg_graph}):
            result = srv._load_agent()

        assert result is fake_obj

    def test_returns_agent_attr_when_present(self):
        srv = _import_server()

        import types as _types

        fake_obj = object()
        fake_module = _types.ModuleType("agent")
        fake_module.agent = fake_obj  # type: ignore[attr-defined]

        fake_lg_graph = _types.ModuleType("langgraph.graph")
        fake_lg_graph.StateGraph = type("StateGraph", (), {})  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"agent": fake_module, "langgraph.graph": fake_lg_graph}):
            result = srv._load_agent()

        assert result is fake_obj


# ---------------------------------------------------------------------------
# _load_agent — StateGraph ImportError inside the warning block (lines 107-108)
# ---------------------------------------------------------------------------


class TestLoadAgentStateGraphImportError:
    def test_no_crash_when_langgraph_not_importable_inside_load(self):
        """ImportError for langgraph.graph inside _load_agent must be silently swallowed."""
        srv = _import_server()

        import types as _types

        fake_obj = object()
        fake_module = _types.ModuleType("agent")
        fake_module.graph = fake_obj  # type: ignore[attr-defined]

        # Force langgraph.graph to raise ImportError
        with patch.dict(sys.modules, {"agent": fake_module, "langgraph.graph": None}):  # type: ignore[dict-item]
            result = srv._load_agent()

        assert result is fake_obj


# ---------------------------------------------------------------------------
# startup — _tracer init and StateGraph compile branch (lines 112-116, 134-135)
# ---------------------------------------------------------------------------


class TestStartupTracingAndCompile:
    @pytest.mark.asyncio
    async def test_startup_compiles_state_graph(self):
        """When _load_agent returns a StateGraph, startup() must compile it (line 134-135)."""
        import types as _types

        srv = _import_server()

        class FakeStateGraph:
            pass

        compiled = MagicMock(name="compiled_graph")

        raw_sg = FakeStateGraph()
        raw_sg.compile = MagicMock(return_value=compiled)  # type: ignore[attr-defined]

        fake_lg_graph = _types.ModuleType("langgraph.graph")
        fake_lg_graph.StateGraph = FakeStateGraph  # type: ignore[attr-defined]

        mock_checkpointer = MagicMock(spec=[])  # no setup()

        with (
            patch.object(srv, "_load_agent", return_value=raw_sg),
            patch.object(srv, "_get_checkpointer", return_value=mock_checkpointer),
            patch.dict(sys.modules, {"langgraph.graph": fake_lg_graph}),
        ):
            await srv.startup()

        assert srv._agent is compiled
        srv._agent = None

    @pytest.mark.asyncio
    async def test_startup_skips_compile_for_non_state_graph(self):
        """When _load_agent returns a compiled graph, startup() uses it as-is."""
        import types as _types

        srv = _import_server()

        class FakeStateGraph:
            pass

        # compiled_obj is NOT an instance of FakeStateGraph
        compiled_obj = MagicMock(name="already_compiled")

        fake_lg_graph = _types.ModuleType("langgraph.graph")
        fake_lg_graph.StateGraph = FakeStateGraph  # type: ignore[attr-defined]

        mock_checkpointer = MagicMock(spec=[])

        with (
            patch.object(srv, "_load_agent", return_value=compiled_obj),
            patch.object(srv, "_get_checkpointer", return_value=mock_checkpointer),
            patch.dict(sys.modules, {"langgraph.graph": fake_lg_graph}),
        ):
            await srv.startup()

        assert srv._agent is compiled_obj
        srv._agent = None

    @pytest.mark.asyncio
    async def test_startup_falls_back_when_langgraph_not_importable(self):
        """ImportError for langgraph.graph in startup must fall back to raw agent."""
        srv = _import_server()

        raw_obj = MagicMock(name="raw_agent")
        mock_checkpointer = MagicMock(spec=[])

        with (
            patch.object(srv, "_load_agent", return_value=raw_obj),
            patch.object(srv, "_get_checkpointer", return_value=mock_checkpointer),
            patch.dict(sys.modules, {"langgraph.graph": None}),  # type: ignore[dict-item]
        ):
            await srv.startup()

        assert srv._agent is raw_obj
        srv._agent = None

    @pytest.mark.asyncio
    async def test_startup_tracing_import_error_silently_skipped(self):
        """When _tracing raises ImportError, startup must survive without crashing."""
        srv = _import_server()
        srv._tracer = None

        raw_obj = MagicMock(name="agent")
        mock_checkpointer = MagicMock(spec=[])

        # Inject a broken _tracing module so ImportError fires at init_tracing()
        broken_tracing = types.ModuleType("_tracing")

        def _raise_import(*args, **kwargs):
            raise ImportError("_tracing not available")

        broken_tracing.init_tracing = _raise_import  # type: ignore[attr-defined]

        with (
            patch.object(srv, "_load_agent", return_value=raw_obj),
            patch.object(srv, "_get_checkpointer", return_value=mock_checkpointer),
            patch.dict(sys.modules, {"langgraph.graph": None, "_tracing": broken_tracing}),  # type: ignore[dict-item]
        ):
            # Must not raise — ImportError from init_tracing is swallowed by the try/except
            await srv.startup()

        # Server is up; _agent was set, tracer could be None or NoopTracer — just no crash
        assert srv._agent is raw_obj
        srv._agent = None


# ---------------------------------------------------------------------------
# /invoke — exception path (line 166) and no-aget_state branch (line 176)
# ---------------------------------------------------------------------------


class TestInvokeExtraPathsCoverage:
    @pytest.mark.asyncio
    async def test_invoke_returns_output_without_aget_state(self):
        """When agent has no aget_state, normal response without interrupt check (line 176)."""
        srv = _import_server()

        mock_agent = MagicMock(spec=["ainvoke"])  # no aget_state
        mock_agent.ainvoke = AsyncMock(return_value={"answer": "hi"})
        srv._agent = mock_agent

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/invoke", json={"input": {"q": "test"}})

        assert response.status_code == 200
        assert response.json()["output"] == {"answer": "hi"}
        srv._agent = None

    @pytest.mark.asyncio
    async def test_invoke_500_when_ainvoke_raises(self):
        """Exception in ainvoke must produce HTTP 500 (line 166)."""
        srv = _import_server()

        mock_agent = MagicMock(spec=["ainvoke"])
        mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("graph crash"))
        srv._agent = mock_agent

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/invoke", json={"input": {}})

        assert response.status_code == 500
        assert "graph crash" in response.json()["detail"]
        srv._agent = None


# ---------------------------------------------------------------------------
# /resume — exception path (line 223-225)
# ---------------------------------------------------------------------------


class TestResumeExtraPathsCoverage:
    @pytest.mark.asyncio
    async def test_resume_500_when_ainvoke_raises(self):
        """Exception in resume ainvoke must produce HTTP 500 (lines 223-225)."""
        srv = _import_server()

        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(side_effect=ValueError("resume failed"))
        srv._agent = mock_agent

        fake_types_module = types.ModuleType("langgraph.types")
        fake_types_module.Command = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"langgraph.types": fake_types_module}):
            transport = ASGITransport(app=srv.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/resume", json={"thread_id": "t1", "human_input": "ok"}
                )

        assert response.status_code == 500
        assert "resume failed" in response.json()["detail"]
        srv._agent = None


# ---------------------------------------------------------------------------
# _run_agent — sync invoke branch and TypeError branch (lines 232-236)
# ---------------------------------------------------------------------------


class TestRunAgentExtraDispatch:
    @pytest.mark.asyncio
    async def test_run_agent_uses_sync_invoke_when_no_ainvoke(self):
        """When agent has invoke but not ainvoke, calls invoke() directly (lines 232-234)."""
        srv = _import_server()

        class SyncOnlyAgent:
            def invoke(self, input_data, config=None):
                return {"sync": "result"}

        srv._agent = SyncOnlyAgent()

        result = await srv._run_agent({"query": "hi"}, {"configurable": {"thread_id": "t1"}})
        assert result == {"sync": "result"}
        srv._agent = None

    @pytest.mark.asyncio
    async def test_run_agent_raises_type_error_for_unsupported_agent(self):
        """Agent with neither ainvoke nor invoke raises TypeError (lines 235-236)."""
        srv = _import_server()

        class NoInvokeAgent:
            pass

        srv._agent = NoInvokeAgent()

        with pytest.raises(TypeError, match="does not have invoke or ainvoke"):
            await srv._run_agent({}, {})

        srv._agent = None
