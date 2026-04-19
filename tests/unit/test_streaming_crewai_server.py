"""Unit tests for the /stream SSE endpoint and other coverage gaps in crewai_server.py.

Targets (new additions):
  lines 29-30   _engine_tb ImportError fallback
  line  139     startup: raw_tools present but _engine_tb is None (tools skipped)
  line  141     startup: tools exception path sets _crewai_tools=[]
  lines 162-164 startup: _load_agent exception path sets _module=None
  lines 176-179 startup: _detect_mode RuntimeError during crew extraction
  lines 190-202 startup: inject tools+model+temperature+ollama into crew agents
  line  210     health: "loading" when both _module and _crew are None
  lines 224-227 stream: _detect_mode RuntimeError → 503
  lines 287-307 invoke: 503, _SyntheticModule path, schema_errors path, 500 path
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


def _import_server():
    """Import crewai_server freshly (clears any cached module)."""
    for key in list(sys.modules.keys()):
        if "crewai_server" in key:
            del sys.modules[key]
    sys.path.insert(0, "engine/runtimes/templates")
    import crewai_server as srv  # noqa: PLC0415

    return srv


def _make_step_output(description: str = "task done", result: str = "ok") -> MagicMock:
    step = MagicMock()
    step.task = MagicMock()
    step.task.description = description
    step.result = result
    return step


class TestCrewAIStreamEndpoint:
    @pytest.mark.asyncio
    async def test_stream_returns_200_with_event_stream_content_type(self):
        srv = _import_server()

        async def fake_akickoff(inputs, callbacks=None, step_callback=None, **kwargs):
            if step_callback:
                step_callback(_make_step_output("do the thing", "result_1"))
            return MagicMock(raw="final answer")

        mock_crew = MagicMock()
        mock_crew.akickoff = fake_akickoff
        srv._crew = mock_crew
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/stream", json={"input": {"topic": "AI"}, "config": None}
            )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        srv._crew = None

    @pytest.mark.asyncio
    async def test_stream_emits_step_events_for_each_step_callback(self):
        srv = _import_server()
        steps = [_make_step_output("step A", "res_a"), _make_step_output("step B", "res_b")]

        async def fake_akickoff(inputs, callbacks=None, step_callback=None, **kwargs):
            for s in steps:
                if step_callback:
                    step_callback(s)
                await asyncio.sleep(0)
            return MagicMock(raw="done")

        mock_crew = MagicMock()
        mock_crew.akickoff = fake_akickoff
        srv._crew = mock_crew
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": {"topic": "AI"}})
        body = response.text
        assert body.count("event: step") == 2
        assert "step A" in body
        assert "step B" in body
        srv._crew = None

    @pytest.mark.asyncio
    async def test_stream_emits_done_event_at_end(self):
        srv = _import_server()

        async def fake_akickoff(inputs, callbacks=None, step_callback=None, **kwargs):
            return MagicMock(raw="finished")

        mock_crew = MagicMock()
        mock_crew.akickoff = fake_akickoff
        srv._crew = mock_crew
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": {}})
        assert "data: [DONE]" in response.text
        srv._crew = None

    @pytest.mark.asyncio
    async def test_stream_emits_result_event_with_final_output(self):
        srv = _import_server()

        async def fake_akickoff(inputs, callbacks=None, step_callback=None, **kwargs):
            return MagicMock(raw="the final answer")

        mock_crew = MagicMock()
        mock_crew.akickoff = fake_akickoff
        srv._crew = mock_crew
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": {}})
        body = response.text
        assert "event: result" in body
        assert "the final answer" in body
        srv._crew = None

    @pytest.mark.asyncio
    async def test_stream_returns_503_when_crew_not_loaded(self):
        srv = _import_server()
        srv._crew = None
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": {}})
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_stream_falls_back_when_akickoff_not_available(self):
        srv = _import_server()
        mock_crew = MagicMock(spec=["kickoff"])
        mock_crew.kickoff.return_value = MagicMock(raw="sync result")
        srv._crew = mock_crew
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": {"q": "test"}})
        assert response.status_code == 200
        assert "event: result" in response.text
        assert "data: [DONE]" in response.text
        srv._crew = None

    @pytest.mark.asyncio
    async def test_stream_emits_error_event_on_exception(self):
        """If akickoff raises, /stream must emit an error event then [DONE]."""
        srv = _import_server()

        async def fail_akickoff(inputs, callbacks=None, step_callback=None, **kwargs):
            raise RuntimeError("crew exploded")

        mock_crew = MagicMock()
        mock_crew.akickoff = fail_akickoff
        srv._crew = mock_crew

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": {}})

        body = response.text
        assert "event: error" in body
        assert "crew exploded" in body
        assert "data: [DONE]" in body
        srv._crew = None


# ---------------------------------------------------------------------------
# _load_agent — ImportError fallback for _engine_tb (lines 29-30)
# ---------------------------------------------------------------------------


class TestEngineToolBridgeImportFallback:
    def test_engine_tb_is_none_when_import_fails(self):
        """When engine.tool_bridge cannot be imported, _engine_tb must be None."""
        for key in list(sys.modules.keys()):
            if "crewai_server" in key:
                del sys.modules[key]

        # Force engine.tool_bridge to be absent
        with patch.dict(sys.modules, {"engine.tool_bridge": None}):  # type: ignore[dict-item]
            sys.path.insert(0, "engine/runtimes/templates")
            import crewai_server as srv  # noqa: PLC0415

        assert srv._engine_tb is None


# ---------------------------------------------------------------------------
# _check_json_type — integer/boolean edge case (line 139 in _validate_output)
# ---------------------------------------------------------------------------


class TestCheckJsonType:
    def _get_check(self):
        srv = _import_server()
        return srv._check_json_type

    def test_true_for_matching_type(self):
        check = self._get_check()
        assert check("hello", "string") is True
        assert check(42, "integer") is True
        assert check([1, 2], "array") is True
        assert check({}, "object") is True
        assert check(None, "null") is True
        assert check(3.14, "number") is True

    def test_false_for_mismatched_type(self):
        check = self._get_check()
        assert check(42, "string") is False
        assert check("hi", "integer") is False

    def test_boolean_is_not_integer(self):
        """bool is a subclass of int; _check_json_type must return False for (True, 'integer')."""
        check = self._get_check()
        assert check(True, "integer") is False
        assert check(False, "integer") is False

    def test_unknown_type_returns_true(self):
        check = self._get_check()
        assert check("anything", "unknown_type") is True


# ---------------------------------------------------------------------------
# startup — tools skipped when _engine_tb is None (line 139)
# ---------------------------------------------------------------------------


class TestStartupToolsSkippedWhenNoBridge:
    @pytest.mark.asyncio
    async def test_tools_empty_when_engine_tb_none(self, monkeypatch):
        """raw_tools present but _engine_tb is None → _crewai_tools stays []."""
        monkeypatch.setenv("AGENT_TOOLS_JSON", '[{"name": "tool1"}]')
        srv = _import_server()
        srv._engine_tb = None

        fake_module = MagicMock()
        fake_module.crew = MagicMock()

        with patch.object(srv, "_load_agent", return_value=fake_module):
            with patch.object(srv, "_detect_mode", return_value=("crew", fake_module.crew)):
                await srv.startup()

        assert srv._crewai_tools == []
        srv._module = None
        srv._crew = None


# ---------------------------------------------------------------------------
# startup — tools exception path (line 141)
# ---------------------------------------------------------------------------


class TestStartupToolsException:
    @pytest.mark.asyncio
    async def test_crewai_tools_empty_on_exception(self, monkeypatch):
        """Exception during tool loading must set _crewai_tools=[] without crash."""
        monkeypatch.setenv("AGENT_TOOLS_JSON", "NOT_JSON")
        srv = _import_server()

        fake_module = MagicMock()
        fake_module.crew = MagicMock()

        with patch.object(srv, "_load_agent", return_value=fake_module):
            with patch.object(srv, "_detect_mode", return_value=("crew", fake_module.crew)):
                await srv.startup()

        assert srv._crewai_tools == []
        srv._module = None
        srv._crew = None


# ---------------------------------------------------------------------------
# startup — _load_agent exception path (lines 162-164)
# ---------------------------------------------------------------------------


class TestStartupLoadAgentException:
    @pytest.mark.asyncio
    async def test_module_none_when_load_agent_raises(self, monkeypatch):
        """When _load_agent raises, _module stays None and server survives."""
        monkeypatch.delenv("AGENT_TOOLS_JSON", raising=False)
        srv = _import_server()
        srv._module = None
        srv._crew = None

        with patch.object(srv, "_load_agent", side_effect=AttributeError("no module")):
            await srv.startup()

        assert srv._module is None


# ---------------------------------------------------------------------------
# startup — _detect_mode RuntimeError (lines 176-179)
# ---------------------------------------------------------------------------


class TestStartupDetectModeRuntimeError:
    @pytest.mark.asyncio
    async def test_crew_stays_none_when_detect_mode_fails(self, monkeypatch):
        """RuntimeError from _detect_mode during startup must be swallowed → _crew=None."""
        monkeypatch.delenv("AGENT_TOOLS_JSON", raising=False)
        srv = _import_server()
        srv._module = None
        srv._crew = None

        fake_module = MagicMock()

        with patch.object(srv, "_load_agent", return_value=fake_module):
            with patch.object(srv, "_detect_mode", side_effect=RuntimeError("no crew or flow")):
                await srv.startup()

        assert srv._crew is None
        srv._module = None


# ---------------------------------------------------------------------------
# startup — inject tools + model + temperature + ollama (lines 190-202)
# ---------------------------------------------------------------------------


class TestStartupAgentInjection:
    @pytest.mark.asyncio
    async def test_tools_injected_into_crew_agents(self, monkeypatch):
        monkeypatch.delenv("AGENT_TOOLS_JSON", raising=False)
        monkeypatch.setenv("AGENT_MODEL", "claude-sonnet-4-6")
        monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
        srv = _import_server()
        srv._module = None
        srv._crew = None

        mock_agent_obj = MagicMock()
        mock_agent_obj.tools = []
        mock_agent_obj.llm = None  # no llm injection attempted

        mock_crew = MagicMock()
        mock_crew.agents = [mock_agent_obj]

        fake_module = MagicMock()

        fake_tool = MagicMock()

        with patch.object(srv, "_load_agent", return_value=fake_module):
            with patch.object(srv, "_detect_mode", return_value=("crew", mock_crew)):
                srv._crewai_tools = [fake_tool]
                await srv.startup()

        assert fake_tool in mock_agent_obj.tools
        srv._module = None
        srv._crew = None
        srv._crewai_tools = []

    @pytest.mark.asyncio
    async def test_model_and_temperature_injected_into_llm(self, monkeypatch):
        monkeypatch.delenv("AGENT_TOOLS_JSON", raising=False)
        monkeypatch.setenv("AGENT_MODEL", "gpt-4o")
        monkeypatch.setenv("AGENT_TEMPERATURE", "0.5")
        srv = _import_server()
        srv._module = None
        srv._crew = None

        mock_llm = MagicMock()
        mock_llm.model = None
        mock_llm.temperature = None

        mock_agent_obj = MagicMock()
        mock_agent_obj.tools = []
        mock_agent_obj.llm = mock_llm

        mock_crew = MagicMock()
        mock_crew.agents = [mock_agent_obj]

        fake_module = MagicMock()

        with patch.object(srv, "_load_agent", return_value=fake_module):
            with patch.object(srv, "_detect_mode", return_value=("crew", mock_crew)):
                srv._crewai_tools = []
                await srv.startup()

        assert mock_llm.model == "gpt-4o"
        assert mock_llm.temperature == 0.5
        srv._module = None
        srv._crew = None

    @pytest.mark.asyncio
    async def test_ollama_base_url_set_for_ollama_model(self, monkeypatch):
        monkeypatch.delenv("AGENT_TOOLS_JSON", raising=False)
        monkeypatch.setenv("AGENT_MODEL", "ollama/llama3")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://custom-ollama:11434")
        monkeypatch.delenv("AGENT_TEMPERATURE", raising=False)
        srv = _import_server()
        srv._module = None
        srv._crew = None

        mock_llm = MagicMock()
        mock_llm.model = None

        mock_agent_obj = MagicMock()
        mock_agent_obj.tools = []
        mock_agent_obj.llm = mock_llm

        mock_crew = MagicMock()
        mock_crew.agents = [mock_agent_obj]

        fake_module = MagicMock()

        with patch.object(srv, "_load_agent", return_value=fake_module):
            with patch.object(srv, "_detect_mode", return_value=("crew", mock_crew)):
                srv._crewai_tools = []
                await srv.startup()

        assert mock_llm.base_url == "http://custom-ollama:11434"
        srv._module = None
        srv._crew = None


# ---------------------------------------------------------------------------
# health — "loading" when both _module and _crew are None (line 210)
# ---------------------------------------------------------------------------


class TestCrewAIHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_loading_when_both_none(self):
        srv = _import_server()
        srv._module = None
        srv._crew = None

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "loading"

    @pytest.mark.asyncio
    async def test_health_healthy_when_crew_set(self):
        srv = _import_server()
        srv._module = None
        srv._crew = MagicMock()

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

        assert response.json()["status"] == "healthy"
        srv._crew = None


# ---------------------------------------------------------------------------
# stream — detect_mode RuntimeError → 503 (lines 224-227)
# ---------------------------------------------------------------------------


class TestStreamDetectMode503:
    @pytest.mark.asyncio
    async def test_stream_503_when_detect_mode_raises(self):
        """When _crew is None and _detect_mode raises, stream must 503."""
        srv = _import_server()
        srv._crew = None
        srv._module = MagicMock()  # module present, crew absent

        with patch.object(srv, "_detect_mode", side_effect=RuntimeError("no crew or flow")):
            transport = ASGITransport(app=srv.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/stream", json={"input": {"q": "x"}})

        assert response.status_code == 503
        assert "no crew or flow" in response.json()["detail"]
        srv._module = None


# ---------------------------------------------------------------------------
# /invoke — 503, _SyntheticModule, schema_errors, 500 paths (lines 287-307)
# ---------------------------------------------------------------------------


class TestInvokeEndpointCoverage:
    @pytest.mark.asyncio
    async def test_invoke_503_when_both_none(self):
        srv = _import_server()
        srv._module = None
        srv._crew = None

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/invoke", json={"input": {}})

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_invoke_uses_synthetic_module_when_module_none(self):
        """When _module is None but _crew is set, a _SyntheticModule wraps _crew."""
        srv = _import_server()
        srv._module = None

        mock_result = MagicMock()
        mock_result.raw = None
        # Let str(mock_result) be used as output

        mock_crew = MagicMock()
        mock_crew.kickoff = MagicMock(return_value=mock_result)
        srv._crew = mock_crew

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/invoke", json={"input": {"task": "do it"}})

        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "crew"
        srv._crew = None

    @pytest.mark.asyncio
    async def test_invoke_returns_schema_errors_when_output_invalid(self):
        """When output_schema is set and output is invalid, schema_errors is non-null."""
        srv = _import_server()
        srv._module = None

        # Crew returns a non-JSON-parseable result
        mock_result = MagicMock()
        mock_result.__str__ = MagicMock(return_value="not-json")

        mock_crew = MagicMock()
        mock_crew.kickoff = MagicMock(return_value=mock_result)
        srv._crew = mock_crew

        schema = {"required": ["name"], "properties": {"name": {"type": "string"}}}
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/invoke",
                json={"input": {}, "config": {"output_schema": schema}},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["output_schema_errors"] is not None
        assert len(body["output_schema_errors"]) > 0
        srv._crew = None

    @pytest.mark.asyncio
    async def test_invoke_500_on_dispatch_exception(self):
        """Exception during _dispatch must bubble up as HTTP 500."""
        srv = _import_server()
        srv._module = MagicMock()

        with patch.object(srv, "_detect_mode", return_value=("crew", MagicMock())):
            with patch.object(srv, "_dispatch", side_effect=RuntimeError("dispatch failed")):
                transport = ASGITransport(app=srv.app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post("/invoke", json={"input": {}})

        assert response.status_code == 500
        assert "dispatch failed" in response.json()["detail"]
        srv._module = None

    @pytest.mark.asyncio
    async def test_invoke_with_flow_mode(self):
        """Flow dispatch path sets mode='flow' in response."""
        srv = _import_server()

        mock_flow_result = MagicMock()
        mock_flow_result.raw = "flow-output"

        mock_flow = MagicMock()
        mock_flow.kickoff_async = AsyncMock(return_value=mock_flow_result)

        fake_module = MagicMock()
        fake_module.flow = mock_flow
        srv._module = fake_module
        srv._crew = None

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/invoke", json={"input": {"goal": "run"}})

        assert response.status_code == 200
        assert response.json()["mode"] == "flow"
        srv._module = None


# ---------------------------------------------------------------------------
# _validate_output — direct unit tests
# ---------------------------------------------------------------------------


class TestValidateOutput:
    def _get_validate(self):
        return _import_server()._validate_output

    def test_returns_none_when_schema_is_none(self):
        validate = self._get_validate()
        assert validate('{"x": 1}', schema=None) is None

    def test_returns_error_for_non_json_output(self):
        validate = self._get_validate()
        errors = validate("not-json", schema={"required": ["x"]})
        assert errors is not None
        assert any("not valid JSON" in e for e in errors)

    def test_returns_error_for_missing_required_field(self):
        validate = self._get_validate()
        schema = {"required": ["name"], "properties": {}}
        errors = validate('{"age": 30}', schema=schema)
        assert errors is not None
        assert any("name" in e for e in errors)

    def test_returns_error_for_wrong_field_type(self):
        validate = self._get_validate()
        schema = {"properties": {"count": {"type": "integer"}}}
        errors = validate('{"count": "not-an-int"}', schema=schema)
        assert errors is not None
        assert any("count" in e for e in errors)

    def test_returns_none_on_valid_output(self):
        validate = self._get_validate()
        schema = {"required": ["name"], "properties": {"name": {"type": "string"}}}
        result = validate('{"name": "Alice"}', schema=schema)
        assert result is None

    def test_ignores_optional_missing_fields(self):
        validate = self._get_validate()
        schema = {"properties": {"optional_field": {"type": "string"}}}
        result = validate('{"other": "value"}', schema=schema)
        assert result is None
