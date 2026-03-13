"""Tests for AnthropicProvider."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.base import (
    AuthenticationError,
    ModelNotFoundError,
    ProviderError,
    RateLimitError,
)
from engine.providers.models import (
    ProviderConfig,
    ProviderType,
    ToolDefinition,
    ToolFunction,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _config(api_key: str = "sk-ant-test") -> ProviderConfig:
    return ProviderConfig(
        provider_type=ProviderType.anthropic,
        api_key=api_key,
        default_model="claude-sonnet-4-6",
    )


def _messages_response(
    content: str = "Hello from Claude!",
    model: str = "claude-sonnet-4-6",
    tool_use: list | None = None,
    stop_reason: str = "end_turn",
) -> dict:
    content_blocks = []
    if content:
        content_blocks.append({"type": "text", "text": content})
    if tool_use:
        content_blocks.extend(tool_use)
    return {
        "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
        "type": "message",
        "model": model,
        "role": "assistant",
        "content": content_blocks,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 25, "output_tokens": 12},
    }


def _models_response() -> dict:
    return {
        "data": [
            {"id": "claude-3-haiku-20240307", "display_name": "Claude 3 Haiku"},
            {"id": "claude-3-sonnet-20240229", "display_name": "Claude 3 Sonnet"},
            {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6"},
        ]
    }


# ── Tests ────────────────────────────────────────────────────────────────────


class TestAnthropicProviderInit:
    def test_init_requires_api_key(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.anthropic)
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(AuthenticationError, match="API key not found"):
                AnthropicProvider(config)

    def test_init_from_env(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.anthropic)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-env"}):
            provider = AnthropicProvider(config)
            assert provider._api_key == "sk-ant-env"

    def test_name(self) -> None:
        provider = AnthropicProvider(_config())
        assert provider.name == "anthropic"

    def test_custom_base_url(self) -> None:
        config = ProviderConfig(
            provider_type=ProviderType.anthropic,
            api_key="sk-ant-test",
            base_url="https://custom.proxy.com/v1",
        )
        provider = AnthropicProvider(config)
        assert "custom.proxy.com" in provider._base_url


class TestAnthropicProviderGenerate:
    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(200, json=_messages_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.generate(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.content == "Hello from Claude!"
        assert result.model == "claude-sonnet-4-6"
        assert result.provider == "anthropic"
        assert result.finish_reason == "stop"
        assert result.usage.prompt_tokens == 25
        assert result.usage.completion_tokens == 12
        assert result.usage.total_tokens == 37

    @pytest.mark.asyncio
    async def test_generate_with_system_message(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(200, json=_messages_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        await provider.generate(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ]
        )

        call_payload = provider._client.post.call_args[1]["json"]
        assert call_payload["system"] == "You are helpful."
        # system message should NOT be in the messages list
        assert not any(m.get("role") == "system" for m in call_payload["messages"])

    @pytest.mark.asyncio
    async def test_generate_with_tools(self) -> None:
        tool_use_block = {
            "type": "tool_use",
            "id": "toolu_01A09q90qw90lq917835lq9",
            "name": "get_weather",
            "input": {"city": "London"},
        }
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(
            200,
            json=_messages_response(content="", tool_use=[tool_use_block], stop_reason="tool_use"),
        )
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        tools = [
            ToolDefinition(
                function=ToolFunction(
                    name="get_weather",
                    description="Get weather",
                    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
                )
            )
        ]

        result = await provider.generate(
            messages=[{"role": "user", "content": "Weather in London?"}],
            tools=tools,
        )

        assert result.finish_reason == "tool_calls"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].function_name == "get_weather"
        assert result.tool_calls[0].id == "toolu_01A09q90qw90lq917835lq9"
        parsed_args = json.loads(result.tool_calls[0].function_arguments)
        assert parsed_args == {"city": "London"}

    @pytest.mark.asyncio
    async def test_generate_stream(self) -> None:
        """Test streaming: mock the streaming client and yield SSE events."""
        provider = AnthropicProvider(_config())

        _start = '{"type":"message_start","message":{"model":"claude-sonnet-4-6","usage":{}}}'
        _d1 = '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}'
        _d2 = '{"type":"content_block_delta","delta":{"type":"text_delta","text":" world"}}'
        _stop = '{"type":"message_delta","delta":{"stop_reason":"end_turn"}}'
        sse_lines = [f"data: {_start}", f"data: {_d1}", f"data: {_d2}", f"data: {_stop}"]

        async def _fake_lines():
            for line in sse_lines:
                yield line

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_stream_ctx.status_code = 200
        mock_stream_ctx.aiter_lines = _fake_lines

        # stream() must return the context manager directly (not a coroutine)
        provider._client.stream = MagicMock(return_value=mock_stream_ctx)

        chunks = []
        async for chunk in provider.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
            chunks.append(chunk)

        text_chunks = [c for c in chunks if c.content]
        assert len(text_chunks) == 2
        assert text_chunks[0].content == "Hello"
        assert text_chunks[1].content == " world"

        finish_chunks = [c for c in chunks if c.finish_reason]
        assert finish_chunks[-1].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_generate_max_tokens_default(self) -> None:
        """max_tokens is required by Anthropic — should default to 1024."""
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(200, json=_messages_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        await provider.generate(messages=[{"role": "user", "content": "Hi"}])

        payload = provider._client.post.call_args[1]["json"]
        assert payload["max_tokens"] == 1024

    @pytest.mark.asyncio
    async def test_generate_explicit_max_tokens(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(200, json=_messages_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        await provider.generate(messages=[{"role": "user", "content": "Hi"}], max_tokens=512)

        payload = provider._client.post.call_args[1]["json"]
        assert payload["max_tokens"] == 512


class TestAnthropicProviderModelList:
    @pytest.mark.asyncio
    async def test_list_models(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(200, json=_models_response())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        models = await provider.list_models()
        assert len(models) == 3
        # sorted by id
        assert models[0].id == "claude-3-haiku-20240307"
        assert models[0].provider == "anthropic"
        assert models[0].supports_tools is True
        assert models[0].supports_streaming is True


class TestAnthropicProviderHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_true(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(200, json=_models_response())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        assert await provider.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_false(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(401, text="Unauthorized")
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        assert await provider.health_check() is False


class TestAnthropicProviderStatusCodes:
    @pytest.mark.asyncio
    async def test_check_status_401(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(401, text="Invalid API key")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError, match="Invalid Anthropic API key"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_check_status_429(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(429, text="Rate limited")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RateLimitError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_check_status_404(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(404, text="Model not found")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ModelNotFoundError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_check_status_500(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(500, text="Internal Server Error")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ProviderError, match="500"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_timeout_raises_provider_error(self) -> None:
        provider = AnthropicProvider(_config())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with pytest.raises(ProviderError, match="timed out"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_connect_error_raises_provider_error(self) -> None:
        provider = AnthropicProvider(_config())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with pytest.raises(ProviderError, match="Failed to connect"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])


class TestAnthropicModelSupportsTools:
    def test_claude3_supports_tools(self) -> None:
        assert AnthropicProvider._model_supports_tools("claude-3-opus-20240229") is True

    def test_claude_sonnet_supports_tools(self) -> None:
        assert AnthropicProvider._model_supports_tools("claude-sonnet-4-6") is True

    def test_claude_haiku_supports_tools(self) -> None:
        assert AnthropicProvider._model_supports_tools("claude-haiku-3-5") is True

    def test_claude4_supports_tools(self) -> None:
        assert AnthropicProvider._model_supports_tools("claude-4-opus") is True

    def test_unknown_model_no_tools(self) -> None:
        assert AnthropicProvider._model_supports_tools("some-unknown-model") is False


class TestAnthropicProviderStreamAndClose:
    @pytest.mark.asyncio
    async def test_generate_with_stream_true_calls_collect_stream(self) -> None:
        """generate(stream=True) should go through _collect_stream."""
        provider = AnthropicProvider(_config())

        sse_lines = [
            'data: {"type": "message_start", "message": {"model": "claude-sonnet-4-6"}}',
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}',
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}',
        ]

        async def _fake_lines():
            for line in sse_lines:
                yield line

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_stream_ctx.status_code = 200
        mock_stream_ctx.aiter_lines = _fake_lines
        provider._client.stream = MagicMock(return_value=mock_stream_ctx)

        result = await provider.generate(messages=[{"role": "user", "content": "Hi"}], stream=True)
        assert result.content == "Hi"
        assert result.finish_reason == "stop"
        assert result.provider == "anthropic"

    @pytest.mark.asyncio
    async def test_generate_with_temperature(self) -> None:
        provider = AnthropicProvider(_config())
        mock_resp = httpx.Response(200, json=_messages_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        await provider.generate(messages=[{"role": "user", "content": "Hi"}], temperature=0.3)
        payload = provider._client.post.call_args[1]["json"]
        assert payload["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        provider = AnthropicProvider(_config())
        provider._client = AsyncMock()
        await provider.close()
        provider._client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_skips_invalid_json(self) -> None:
        """Lines with invalid JSON are skipped with a warning."""
        provider = AnthropicProvider(_config())

        sse_lines = [
            "data: not-valid-json",
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}}',
        ]

        async def _fake_lines():
            for line in sse_lines:
                yield line

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_stream_ctx.status_code = 200
        mock_stream_ctx.aiter_lines = _fake_lines
        provider._client.stream = MagicMock(return_value=mock_stream_ctx)

        chunks = []
        async for chunk in provider.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
            chunks.append(chunk)

        assert any(c.content == "ok" for c in chunks)

    @pytest.mark.asyncio
    async def test_stream_skips_empty_data_lines(self) -> None:
        """Empty data lines and non-data lines are skipped."""
        provider = AnthropicProvider(_config())

        sse_lines = [
            "event: ping",
            "data: ",
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "X"}}',
        ]

        async def _fake_lines():
            for line in sse_lines:
                yield line

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_stream_ctx.status_code = 200
        mock_stream_ctx.aiter_lines = _fake_lines
        provider._client.stream = MagicMock(return_value=mock_stream_ctx)

        chunks = []
        async for chunk in provider.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
            chunks.append(chunk)

        assert any(c.content == "X" for c in chunks)

    def test_parse_stream_event_unknown_type_returns_none(self) -> None:
        result = AnthropicProvider._parse_stream_event({"type": "unknown_event"})
        assert result is None

    def test_parse_stream_event_message_start_no_model(self) -> None:
        result = AnthropicProvider._parse_stream_event({"type": "message_start", "message": {}})
        assert result is None
