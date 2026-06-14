"""Unit tests for Wave 2 inline-setup support in the agent-builder driver.

The interviewer can call a `request_setup` tool to ask the user for a dependency
(secret | mcp | provider) before finishing the spec. The streaming driver emits a
dedicated `setup_request` event; the non-streaming path sets `ChatTurnResult.setup_request`.
A real spec submission always wins over a setup request in the same turn.
"""
from __future__ import annotations

import json

import pytest

from engine.agent_chat_builder import (
    REQUEST_SETUP_TOOL_NAME,
    SUBMIT_TOOL_NAME,
    ChatStreamEvent,
    SetupRequest,
    run_chat_turn,
    run_chat_turn_stream,
)
from engine.providers.models import GenerateResult, StreamChunk, ToolCall

_VALID_SPEC = (
    '{"name":"my-agent","version":"1.0.0","team":"default",'
    '"owner":"owner@example.com","framework":"langgraph",'
    '"model":{"primary":"claude-sonnet-4-6"},"deploy":{"cloud":"local"}}'
)


class FakeStreamingProvider:
    """Yields a scripted sequence of StreamChunks from generate_stream()."""

    def __init__(self, chunks: list[StreamChunk]) -> None:
        self._chunks = chunks

    async def generate_stream(self, **_kwargs):
        for chunk in self._chunks:
            yield chunk


class FakeProvider:
    """Returns a scripted GenerateResult from generate()."""

    def __init__(self, result: GenerateResult) -> None:
        self._result = result

    async def generate(self, **_kwargs) -> GenerateResult:
        return self._result


def _setup_call(kind: str, name: str, reason: str = "") -> ToolCall:
    return ToolCall(
        id="s1",
        function_name=REQUEST_SETUP_TOOL_NAME,
        function_arguments=json.dumps({"kind": kind, "name": name, "reason": reason}),
    )


@pytest.mark.asyncio
async def test_stream_emits_setup_request_then_done():
    provider = FakeStreamingProvider(
        [
            StreamChunk(content="You'll need Zendesk. "),
            StreamChunk(tool_calls=[_setup_call("mcp", "zendesk", "read tickets")]),
            StreamChunk(finish_reason="tool_calls"),
        ]
    )
    events = [
        e async for e in run_chat_turn_stream(provider, [{"role": "user", "content": "support agent"}])
    ]

    setups = [e for e in events if e.type == "setup_request"]
    done = [e for e in events if e.type == "done"]

    assert len(setups) == 1
    assert isinstance(setups[0].setup, SetupRequest)
    assert setups[0].setup.kind == "mcp"
    assert setups[0].setup.name == "zendesk"

    assert len(done) == 1
    assert done[0].result is not None
    assert done[0].result.agent_yaml is None
    assert done[0].result.setup_request is not None
    assert done[0].result.setup_request.name == "zendesk"


@pytest.mark.asyncio
async def test_stream_spec_submission_wins_over_setup_request():
    provider = FakeStreamingProvider(
        [
            StreamChunk(tool_calls=[_setup_call("secret", "ZENDESK_API_KEY")]),
            StreamChunk(
                tool_calls=[
                    ToolCall(id="t1", function_name=SUBMIT_TOOL_NAME, function_arguments=_VALID_SPEC)
                ]
            ),
            StreamChunk(finish_reason="tool_calls"),
        ]
    )
    events = [e async for e in run_chat_turn_stream(provider, [{"role": "user", "content": "go"}])]

    assert not [e for e in events if e.type == "setup_request"]
    done = [e for e in events if e.type == "done"]
    assert len(done) == 1
    assert done[0].result is not None
    assert done[0].result.agent_yaml is not None
    assert done[0].result.valid is True


@pytest.mark.asyncio
async def test_stream_malformed_setup_args_degrade_to_text_reply():
    provider = FakeStreamingProvider(
        [
            StreamChunk(content="Hmm."),
            StreamChunk(
                tool_calls=[
                    ToolCall(id="s1", function_name=REQUEST_SETUP_TOOL_NAME, function_arguments="{not json")
                ]
            ),
            StreamChunk(finish_reason="tool_calls"),
        ]
    )
    events = [e async for e in run_chat_turn_stream(provider, [{"role": "user", "content": "hi"}])]

    assert not [e for e in events if e.type == "setup_request"]
    done = [e for e in events if e.type == "done"]
    assert len(done) == 1
    assert done[0].result is not None
    assert done[0].result.setup_request is None
    assert done[0].result.agent_yaml is None
    assert done[0].result.assistant_message == "Hmm."


@pytest.mark.asyncio
async def test_non_stream_sets_setup_request_field():
    provider = FakeProvider(
        GenerateResult(content="One thing first.", tool_calls=[_setup_call("provider", "openai")])
    )
    result = await run_chat_turn(provider, [{"role": "user", "content": "use gpt-4o"}])

    assert result.agent_yaml is None
    assert result.setup_request is not None
    assert result.setup_request.kind == "provider"
    assert result.setup_request.name == "openai"
    assert result.assistant_message == "One thing first."


def test_stream_event_supports_setup_type():
    evt = ChatStreamEvent(type="setup_request", setup=SetupRequest(kind="secret", name="X"))
    assert evt.type == "setup_request"
    assert evt.setup is not None and evt.setup.name == "X"
