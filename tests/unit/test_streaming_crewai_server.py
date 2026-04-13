"""Unit tests for the /stream SSE endpoint in crewai_server.py."""

from __future__ import annotations

import asyncio
import sys
import types
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
            response = await client.post("/stream", json={"input": {"topic": "AI"}, "config": None})
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
