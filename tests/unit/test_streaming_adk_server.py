"""Unit tests for the /stream SSE endpoint in google_adk_server.py."""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient


def _import_server():
    for key in list(sys.modules.keys()):
        if "google_adk_server" in key:
            del sys.modules[key]
    fake_google = types.ModuleType("google")
    fake_adk = types.ModuleType("google.adk")
    fake_runners = types.ModuleType("google.adk.runners")
    fake_sessions = types.ModuleType("google.adk.sessions")
    fake_genai = types.ModuleType("google.genai")
    fake_genai_types = types.ModuleType("google.genai.types")
    fake_runners.Runner = MagicMock(name="Runner")
    fake_sessions.InMemorySessionService = MagicMock(name="InMemorySessionService")
    fake_genai_types.Content = MagicMock(name="Content")
    fake_genai_types.Part = MagicMock(name="Part")
    sys.modules.setdefault("google", fake_google)
    sys.modules["google.adk"] = fake_adk
    sys.modules["google.adk.runners"] = fake_runners
    sys.modules["google.adk.sessions"] = fake_sessions
    sys.modules["google.genai"] = fake_genai
    sys.modules["google.genai.types"] = fake_genai_types
    sys.path.insert(0, "engine/runtimes/templates")
    import google_adk_server as srv  # noqa: PLC0415
    return srv


def _make_adk_event(*, is_final: bool = False, text: str | None = None) -> MagicMock:
    event = MagicMock()
    event.is_final_response.return_value = is_final
    if text is not None:
        part = MagicMock()
        part.text = text
        event.content = MagicMock()
        event.content.parts = [part]
    else:
        event.content = None
    event_dict = {"is_final": is_final, "text": text}
    event.model_dump = MagicMock(return_value=event_dict)
    return event


async def _aiter(*items):
    for item in items:
        yield item


class TestADKStreamEndpoint:
    @pytest.mark.asyncio
    async def test_stream_returns_200_with_event_stream_content_type(self):
        srv = _import_server()
        srv._agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        events = [_make_adk_event(text="hello"), _make_adk_event(is_final=True, text="world")]
        mock_runner.run_async = MagicMock(return_value=_aiter(*events))
        srv._runner = mock_runner
        mock_ss = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "sess-1"
        mock_ss.create_session = AsyncMock(return_value=mock_session)
        mock_ss.get_session = AsyncMock(return_value=None)
        srv._session_service = mock_ss
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hello world"})
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        srv._agent = None
        srv._runner = None

    @pytest.mark.asyncio
    async def test_stream_emits_one_sse_event_per_adk_event(self):
        srv = _import_server()
        srv._agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        events = [_make_adk_event(text="thinking..."), _make_adk_event(is_final=True, text="answer")]
        mock_runner.run_async = MagicMock(return_value=_aiter(*events))
        srv._runner = mock_runner
        mock_ss = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "sess-abc"
        mock_ss.create_session = AsyncMock(return_value=mock_session)
        mock_ss.get_session = AsyncMock(return_value=None)
        srv._session_service = mock_ss
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "go"})
        body = response.text
        assert body.count("data:") == 3  # 2 events + [DONE]
        srv._agent = None
        srv._runner = None

    @pytest.mark.asyncio
    async def test_stream_emits_done_at_end(self):
        srv = _import_server()
        srv._agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        mock_runner.run_async = MagicMock(return_value=_aiter(_make_adk_event(is_final=True, text="done")))
        srv._runner = mock_runner
        mock_ss = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "sess-done"
        mock_ss.create_session = AsyncMock(return_value=mock_session)
        mock_ss.get_session = AsyncMock(return_value=None)
        srv._session_service = mock_ss
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hi"})
        assert response.text.rstrip().endswith("data: [DONE]")
        srv._agent = None
        srv._runner = None

    @pytest.mark.asyncio
    async def test_stream_returns_503_when_agent_not_loaded(self):
        srv = _import_server()
        srv._agent = None
        srv._runner = None
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hello"})
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_stream_event_payload_is_valid_json(self):
        srv = _import_server()
        srv._agent = MagicMock(name="agent")
        mock_runner = MagicMock(name="runner")
        ev = _make_adk_event(text="hi", is_final=True)
        mock_runner.run_async = MagicMock(return_value=_aiter(ev))
        srv._runner = mock_runner
        mock_ss = MagicMock()
        mock_session = MagicMock()
        mock_session.id = "sess-json"
        mock_ss.create_session = AsyncMock(return_value=mock_session)
        mock_ss.get_session = AsyncMock(return_value=None)
        srv._session_service = mock_ss
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hi"})
        for line in response.text.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload != "[DONE]":
                    json.loads(payload)
        srv._agent = None
        srv._runner = None

    @pytest.mark.asyncio
    async def test_stream_emits_error_event_on_exception(self):
        """If run_async raises, /stream must emit an error event then [DONE]."""
        srv = _import_server()
        mock_agent = MagicMock()
        mock_runner = MagicMock()

        async def failing_run_async(*args, **kwargs):
            raise RuntimeError("adk exploded")
            yield  # make it an async generator

        mock_runner.run_async = failing_run_async
        srv._agent = mock_agent
        srv._runner = mock_runner

        mock_ss = MagicMock()
        mock_ss.get_session = AsyncMock(return_value=None)
        mock_ss.create_session = AsyncMock(return_value=MagicMock(id="err-session"))
        srv._session_service = mock_ss

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "fail me"})

        body = response.text
        assert "error" in body
        assert "data: [DONE]" in body

        srv._agent = None
        srv._runner = None
        srv._session_service = None
