"""Unit tests for the /stream SSE endpoint in claude_sdk_server.py."""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient


def _import_server():
    for key in list(sys.modules.keys()):
        if "claude_sdk_server" in key:
            del sys.modules[key]
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AsyncAnthropic = type("AsyncAnthropic", (), {})
    fake_anthropic.Anthropic = type("Anthropic", (), {})
    sys.modules["anthropic"] = fake_anthropic
    sys.path.insert(0, "engine/runtimes/templates")
    import claude_sdk_server as srv  # noqa: PLC0415
    return srv


def _make_stream_event(event_type: str, text: str = "") -> MagicMock:
    event = MagicMock()
    event.type = event_type
    if event_type == "content_block_delta":
        event.delta = MagicMock()
        event.delta.type = "text_delta"
        event.delta.text = text
    return event


class _FakeAsyncStream:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self._events:
            yield event


class TestClaudeStreamEndpoint:
    @pytest.mark.asyncio
    async def test_stream_returns_200_with_event_stream_content_type(self):
        srv = _import_server()
        stream_events = [
            _make_stream_event("message_start"),
            _make_stream_event("content_block_delta", "Hello"),
            _make_stream_event("message_stop"),
        ]
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=_FakeAsyncStream(stream_events))
        import anthropic
        mock_client.__class__ = anthropic.AsyncAnthropic
        srv._agent = mock_client
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "Hello Claude"})
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        srv._agent = None

    @pytest.mark.asyncio
    async def test_stream_emits_one_sse_event_per_stream_event(self):
        srv = _import_server()
        stream_events = [
            _make_stream_event("message_start"),
            _make_stream_event("content_block_delta", "part1"),
            _make_stream_event("content_block_delta", "part2"),
            _make_stream_event("message_stop"),
        ]
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=_FakeAsyncStream(stream_events))
        import anthropic
        mock_client.__class__ = anthropic.AsyncAnthropic
        srv._agent = mock_client
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hi"})
        data_lines = [line for line in response.text.splitlines() if line.startswith("data:")]
        assert len(data_lines) == 5  # 4 events + [DONE]
        srv._agent = None

    @pytest.mark.asyncio
    async def test_stream_emits_done_at_end(self):
        srv = _import_server()
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=_FakeAsyncStream([_make_stream_event("message_stop")]))
        import anthropic
        mock_client.__class__ = anthropic.AsyncAnthropic
        srv._agent = mock_client
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hi"})
        assert response.text.rstrip().endswith("data: [DONE]")
        srv._agent = None

    @pytest.mark.asyncio
    async def test_stream_returns_503_when_agent_not_loaded(self):
        srv = _import_server()
        srv._agent = None
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hi"})
        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_stream_event_payload_is_valid_json(self):
        srv = _import_server()
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=_FakeAsyncStream([
            _make_stream_event("content_block_delta", "hello"),
            _make_stream_event("message_stop"),
        ]))
        import anthropic
        mock_client.__class__ = anthropic.AsyncAnthropic
        srv._agent = mock_client
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hi"})
        for line in response.text.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload != "[DONE]":
                    json.loads(payload)
        srv._agent = None

    @pytest.mark.asyncio
    async def test_stream_falls_back_for_non_async_anthropic_agent(self):
        srv = _import_server()
        async def my_agent(input_text: str) -> str:
            return "callable result"
        srv._agent = my_agent
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hi"})
        assert response.status_code == 200
        assert "data: [DONE]" in response.text
        srv._agent = None

    @pytest.mark.asyncio
    async def test_stream_emits_error_event_on_exception(self):
        """If messages.stream() raises, /stream must emit an error event then [DONE]."""
        import anthropic as _anthropic_mod

        srv = _import_server()
        mock_client = MagicMock(spec=_anthropic_mod.AsyncAnthropic)

        class _FailStream:
            async def __aenter__(self):
                raise RuntimeError("anthropic exploded")
            async def __aexit__(self, *args):
                pass
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise StopAsyncIteration

        mock_client.messages = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=_FailStream())
        srv._agent = mock_client

        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/stream", json={"input": "hello"})

        body = response.text
        assert "error" in body
        assert "data: [DONE]" in body
        srv._agent = None

    @pytest.mark.asyncio
    async def test_stream_respects_agent_max_tokens_env(self, monkeypatch):
        srv = _import_server()
        monkeypatch.setenv("AGENT_MAX_TOKENS", "4096")
        captured_kwargs: dict = {}

        def capturing_stream(**kwargs):
            captured_kwargs.update(kwargs)
            return _FakeAsyncStream([_make_stream_event("message_stop")])

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.stream = capturing_stream
        import anthropic
        mock_client.__class__ = anthropic.AsyncAnthropic
        srv._agent = mock_client
        transport = ASGITransport(app=srv.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/stream", json={"input": "hi"})
        assert captured_kwargs.get("max_tokens") == 4096
        srv._agent = None
