"""Tests for AnthropicProvider streaming tool-use event handling.

Verifies that generate_stream() correctly accumulates tool_use content blocks
across the multi-event Anthropic SSE sequence and yields StreamChunk(tool_calls=[...])
with fully-assembled function_arguments JSON.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.models import ProviderConfig, ProviderType, StreamChunk, ToolCall


# ── Helpers ─────────────────────────────────────────────────────────────────


def _config() -> ProviderConfig:
    return ProviderConfig(
        provider_type=ProviderType.anthropic,
        api_key="sk-ant-test",
        default_model="claude-sonnet-4-6",
    )


def _make_stream_ctx(sse_lines: list[str]) -> AsyncMock:
    """Build a mock streaming context manager that yields the given SSE lines."""

    async def _fake_lines():
        for line in sse_lines:
            yield line

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)
    ctx.status_code = 200
    ctx.aiter_lines = _fake_lines
    return ctx


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}"


# ── SSE event builders (matching Anthropic wire format exactly) ──────────────


def _message_start(model: str = "claude-sonnet-4-6") -> str:
    return _sse({"type": "message_start", "message": {"model": model, "usage": {}}})


def _content_block_start_tool(index: int, tool_id: str, tool_name: str) -> str:
    return _sse(
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "tool_use",
                "id": tool_id,
                "name": tool_name,
                "input": {},
            },
        }
    )


def _content_block_start_text(index: int) -> str:
    return _sse(
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        }
    )


def _input_json_delta(index: int, partial_json: str) -> str:
    return _sse(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial_json},
        }
    )


def _text_delta(index: int, text: str) -> str:
    return _sse(
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        }
    )


def _content_block_stop(index: int) -> str:
    return _sse({"type": "content_block_stop", "index": index})


def _message_delta(stop_reason: str) -> str:
    return _sse({"type": "message_delta", "delta": {"stop_reason": stop_reason}})


def _message_stop() -> str:
    return _sse({"type": "message_stop"})


# ── Tests ────────────────────────────────────────────────────────────────────


class TestStreamToolUse:
    """generate_stream() assembles tool_use blocks from partial_json fragments."""

    @pytest.mark.asyncio
    async def test_single_tool_call_assembled_from_fragments(self) -> None:
        """Core test: fragments are concatenated and a ToolCall chunk is yielded."""
        provider = AnthropicProvider(_config())

        # The tool input JSON arrives in 3 fragments
        fragment_1 = '{"name": "My'
        fragment_2 = " Agent"
        fragment_3 = '", "framework": "langgraph"}'
        full_json = fragment_1 + fragment_2 + fragment_3

        sse_lines = [
            _message_start(),
            _content_block_start_tool(0, "toolu_01abc", "submit_agent_spec"),
            _input_json_delta(0, fragment_1),
            _input_json_delta(0, fragment_2),
            _input_json_delta(0, fragment_3),
            _content_block_stop(0),
            _message_delta("tool_use"),
            _message_stop(),
        ]

        provider._client.stream = MagicMock(return_value=_make_stream_ctx(sse_lines))

        chunks: list[StreamChunk] = []
        async for chunk in provider.generate_stream(messages=[{"role": "user", "content": "go"}]):
            chunks.append(chunk)

        tool_chunks = [c for c in chunks if c.tool_calls]
        assert len(tool_chunks) == 1, "Expected exactly one chunk carrying tool_calls"

        tool_call = tool_chunks[0].tool_calls[0]
        assert isinstance(tool_call, ToolCall)
        assert tool_call.id == "toolu_01abc"
        assert tool_call.function_name == "submit_agent_spec"

        # function_arguments must be the full concatenated JSON string
        assert tool_call.function_arguments == full_json
        parsed = json.loads(tool_call.function_arguments)
        assert parsed == {"name": "My Agent", "framework": "langgraph"}

    @pytest.mark.asyncio
    async def test_tool_call_finish_reason_is_tool_calls(self) -> None:
        """finish_reason should map 'tool_use' -> 'tool_calls'."""
        provider = AnthropicProvider(_config())

        sse_lines = [
            _message_start(),
            _content_block_start_tool(0, "toolu_xyz", "get_weather"),
            _input_json_delta(0, '{"city": "London"}'),
            _content_block_stop(0),
            _message_delta("tool_use"),
            _message_stop(),
        ]

        provider._client.stream = MagicMock(return_value=_make_stream_ctx(sse_lines))

        chunks: list[StreamChunk] = []
        async for chunk in provider.generate_stream(messages=[{"role": "user", "content": "go"}]):
            chunks.append(chunk)

        finish_chunks = [c for c in chunks if c.finish_reason]
        assert finish_chunks, "Expected at least one chunk with finish_reason"
        assert finish_chunks[-1].finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self) -> None:
        """Two tool blocks at different indices both produce ToolCall entries."""
        provider = AnthropicProvider(_config())

        sse_lines = [
            _message_start(),
            # Block 0: search tool
            _content_block_start_tool(0, "toolu_aaa", "search"),
            _input_json_delta(0, '{"query": "AI frameworks"}'),
            _content_block_stop(0),
            # Block 1: weather tool
            _content_block_start_tool(1, "toolu_bbb", "get_weather"),
            _input_json_delta(1, '{"city": "SF"}'),
            _content_block_stop(1),
            _message_delta("tool_use"),
            _message_stop(),
        ]

        provider._client.stream = MagicMock(return_value=_make_stream_ctx(sse_lines))

        chunks: list[StreamChunk] = []
        async for chunk in provider.generate_stream(messages=[{"role": "user", "content": "go"}]):
            chunks.append(chunk)

        tool_chunks = [c for c in chunks if c.tool_calls]
        # Either one chunk with two ToolCalls or two chunks each with one — collect all calls
        all_tool_calls = [tc for c in tool_chunks for tc in c.tool_calls]
        assert len(all_tool_calls) == 2

        by_name = {tc.function_name: tc for tc in all_tool_calls}
        assert "search" in by_name
        assert "get_weather" in by_name
        assert json.loads(by_name["search"].function_arguments) == {"query": "AI frameworks"}
        assert json.loads(by_name["get_weather"].function_arguments) == {"city": "SF"}
        assert by_name["search"].id == "toolu_aaa"
        assert by_name["get_weather"].id == "toolu_bbb"

    @pytest.mark.asyncio
    async def test_tool_call_with_empty_input(self) -> None:
        """Tool blocks that send zero input_json_delta fragments produce empty-string arguments."""
        provider = AnthropicProvider(_config())

        sse_lines = [
            _message_start(),
            _content_block_start_tool(0, "toolu_no_input", "ping"),
            # no input_json_delta events at all
            _content_block_stop(0),
            _message_delta("tool_use"),
            _message_stop(),
        ]

        provider._client.stream = MagicMock(return_value=_make_stream_ctx(sse_lines))

        chunks: list[StreamChunk] = []
        async for chunk in provider.generate_stream(messages=[{"role": "user", "content": "go"}]):
            chunks.append(chunk)

        tool_chunks = [c for c in chunks if c.tool_calls]
        assert len(tool_chunks) == 1
        tc = tool_chunks[0].tool_calls[0]
        assert tc.function_name == "ping"
        assert tc.function_arguments == ""


class TestStreamTextNoRegression:
    """Existing plain-text streaming must still work correctly after the refactor."""

    @pytest.mark.asyncio
    async def test_plain_text_streaming_unchanged(self) -> None:
        provider = AnthropicProvider(_config())

        sse_lines = [
            _message_start(),
            _content_block_start_text(0),
            _text_delta(0, "Hello"),
            _text_delta(0, " world"),
            _content_block_stop(0),
            _message_delta("end_turn"),
            _message_stop(),
        ]

        provider._client.stream = MagicMock(return_value=_make_stream_ctx(sse_lines))

        chunks: list[StreamChunk] = []
        async for chunk in provider.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
            chunks.append(chunk)

        text_chunks = [c for c in chunks if c.content]
        assert [c.content for c in text_chunks] == ["Hello", " world"]

        finish_chunks = [c for c in chunks if c.finish_reason]
        assert finish_chunks[-1].finish_reason == "stop"

        # No spurious tool_calls on a text-only response
        assert not any(c.tool_calls for c in chunks)

    @pytest.mark.asyncio
    async def test_text_and_tool_in_same_response(self) -> None:
        """A response with both a text block and a tool block works correctly."""
        provider = AnthropicProvider(_config())

        sse_lines = [
            _message_start(),
            # Text block first
            _content_block_start_text(0),
            _text_delta(0, "Thinking..."),
            _content_block_stop(0),
            # Then tool block
            _content_block_start_tool(1, "toolu_mixed", "do_thing"),
            _input_json_delta(1, '{"key": "val"}'),
            _content_block_stop(1),
            _message_delta("tool_use"),
            _message_stop(),
        ]

        provider._client.stream = MagicMock(return_value=_make_stream_ctx(sse_lines))

        chunks: list[StreamChunk] = []
        async for chunk in provider.generate_stream(messages=[{"role": "user", "content": "go"}]):
            chunks.append(chunk)

        text_chunks = [c for c in chunks if c.content]
        assert any(c.content == "Thinking..." for c in text_chunks)

        tool_chunks = [c for c in chunks if c.tool_calls]
        assert len(tool_chunks) == 1
        assert tool_chunks[0].tool_calls[0].function_name == "do_thing"
        assert json.loads(tool_chunks[0].tool_calls[0].function_arguments) == {"key": "val"}

    @pytest.mark.asyncio
    async def test_input_json_delta_without_prior_block_start_is_ignored(self) -> None:
        """Orphaned input_json_delta events (no matching block start) do not crash."""
        provider = AnthropicProvider(_config())

        sse_lines = [
            _message_start(),
            # input_json_delta for index 5 but no content_block_start for it
            _input_json_delta(5, '{"orphan": true}'),
            _text_delta(0, "safe"),
            _message_delta("end_turn"),
            _message_stop(),
        ]

        provider._client.stream = MagicMock(return_value=_make_stream_ctx(sse_lines))

        chunks: list[StreamChunk] = []
        async for chunk in provider.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
            chunks.append(chunk)

        # Should not raise; text still comes through
        assert any(c.content == "safe" for c in chunks)
        # No tool chunks for the orphaned delta
        assert not any(c.tool_calls for c in chunks)
