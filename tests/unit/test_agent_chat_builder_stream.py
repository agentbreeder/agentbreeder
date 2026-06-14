"""Unit tests for the streaming agent-builder driver."""
from __future__ import annotations

import pytest

from engine.agent_chat_builder import ChatStreamEvent, run_chat_turn_stream
from engine.providers.models import StreamChunk, ToolCall


class FakeStreamingProvider:
    """Yields a scripted sequence of StreamChunks from generate_stream()."""

    def __init__(self, chunks: list[StreamChunk]) -> None:
        self._chunks = chunks
        self.closed = False

    async def generate_stream(self, **_kwargs):
        for chunk in self._chunks:
            yield chunk

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stream_emits_text_tokens_then_done():
    provider = FakeStreamingProvider(
        [
            StreamChunk(content="Hi! "),
            StreamChunk(content="What should it do?"),
            StreamChunk(finish_reason="stop"),
        ]
    )
    events = [e async for e in run_chat_turn_stream(provider, [{"role": "user", "content": "hello"}])]

    tokens = [e.text for e in events if e.type == "token"]
    done = [e for e in events if e.type == "done"]

    assert "".join(tokens) == "Hi! What should it do?"
    assert len(done) == 1
    assert done[0].result.agent_yaml is None
    assert done[0].result.valid is False


@pytest.mark.asyncio
async def test_stream_handles_spec_submission():
    spec = (
        '{"name":"my-agent","version":"1.0.0","team":"default",'
        '"owner":"owner@example.com","framework":"langgraph",'
        '"model":{"primary":"claude-sonnet-4-6"},"deploy":{"cloud":"local"}}'
    )
    provider = FakeStreamingProvider(
        [
            StreamChunk(tool_calls=[ToolCall(id="t1", function_name="submit_agent_spec", function_arguments=spec)]),
            StreamChunk(finish_reason="tool_calls"),
        ]
    )
    events = [e async for e in run_chat_turn_stream(provider, [{"role": "user", "content": "build it"}])]

    done = [e for e in events if e.type == "done"]
    assert len(done) == 1
    assert done[0].result.agent_yaml is not None
    assert done[0].result.valid is True
