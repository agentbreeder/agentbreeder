"""Tests for GoogleProvider (Gemini)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from engine.providers.base import (
    AuthenticationError,
    ModelNotFoundError,
    ProviderError,
    RateLimitError,
)
from engine.providers.google_provider import GoogleProvider
from engine.providers.models import (
    ProviderConfig,
    ProviderType,
    ToolDefinition,
    ToolFunction,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _config(api_key: str = "AIza-test-key") -> ProviderConfig:
    return ProviderConfig(
        provider_type=ProviderType.google,
        api_key=api_key,
        default_model="gemini-2.5-pro",
    )


def _generate_content_response(
    text: str = "Hello from Gemini!",
    finish_reason: str = "STOP",
    function_call: dict | None = None,
) -> dict:
    parts = []
    if text:
        parts.append({"text": text})
    if function_call:
        parts.append({"functionCall": function_call})
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": parts},
                "finishReason": finish_reason,
                "index": 0,
            }
        ],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 15},
    }


def _models_response() -> dict:
    return {
        "models": [
            {
                "name": "models/gemini-2.5-pro",
                "displayName": "Gemini 2.5 Pro",
                "inputTokenLimit": 1000000,
                "outputTokenLimit": 8192,
            },
            {
                "name": "models/gemini-2.0-flash",
                "displayName": "Gemini 2.0 Flash",
                "inputTokenLimit": 1000000,
                "outputTokenLimit": 8192,
            },
            {
                "name": "models/gemini-1.5-pro",
                "displayName": "Gemini 1.5 Pro",
                "inputTokenLimit": 2000000,
                "outputTokenLimit": 8192,
            },
        ]
    }


# ── Tests ────────────────────────────────────────────────────────────────────


class TestGoogleProviderInit:
    def test_init_requires_api_key(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.google)
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(AuthenticationError, match="API key not found"):
                GoogleProvider(config)

    def test_init_from_env(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.google)
        with patch.dict("os.environ", {"GOOGLE_AI_API_KEY": "AIza-env-key"}):
            provider = GoogleProvider(config)
            assert provider._api_key == "AIza-env-key"

    def test_name(self) -> None:
        provider = GoogleProvider(_config())
        assert provider.name == "google"

    def test_custom_base_url(self) -> None:
        config = ProviderConfig(
            provider_type=ProviderType.google,
            api_key="AIza-test",
            base_url="https://custom.proxy.com/v1beta",
        )
        provider = GoogleProvider(config)
        assert "custom.proxy.com" in provider._base_url


class TestGoogleProviderGenerate:
    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(200, json=_generate_content_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.generate(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.content == "Hello from Gemini!"
        assert result.model == "gemini-2.5-pro"
        assert result.provider == "google"
        assert result.finish_reason == "stop"
        assert result.usage.prompt_tokens == 20
        assert result.usage.completion_tokens == 15
        assert result.usage.total_tokens == 35

    @pytest.mark.asyncio
    async def test_generate_maps_system_message(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(200, json=_generate_content_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        await provider.generate(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ]
        )

        payload = provider._client.post.call_args[1]["json"]
        # system goes to systemInstruction
        assert payload["systemInstruction"]["parts"][0]["text"] == "You are helpful."
        # contents should only have user message
        assert len(payload["contents"]) == 1
        assert payload["contents"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_generate_maps_assistant_to_model_role(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(200, json=_generate_content_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        await provider.generate(
            messages=[
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "How are you?"},
            ]
        )

        payload = provider._client.post.call_args[1]["json"]
        assert payload["contents"][1]["role"] == "model"

    @pytest.mark.asyncio
    async def test_generate_with_tools(self) -> None:
        fc = {"name": "get_weather", "args": {"city": "London"}}
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(
            200,
            json=_generate_content_response(text="", function_call=fc, finish_reason="STOP"),
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

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].function_name == "get_weather"
        parsed_args = json.loads(result.tool_calls[0].function_arguments)
        assert parsed_args == {"city": "London"}

    @pytest.mark.asyncio
    async def test_generate_stream(self) -> None:
        """Test streaming via SSE events."""
        provider = GoogleProvider(_config())

        event1 = {
            "candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": None}]
        }
        event2 = {
            "candidates": [{"content": {"parts": [{"text": " world"}]}, "finishReason": None}]
        }
        event3 = {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}

        sse_lines = [
            f"data: {json.dumps(event1)}",
            f"data: {json.dumps(event2)}",
            f"data: {json.dumps(event3)}",
        ]

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

    @pytest.mark.asyncio
    async def test_generate_temperature_and_max_tokens(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(200, json=_generate_content_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        await provider.generate(
            messages=[{"role": "user", "content": "Hi"}],
            temperature=0.5,
            max_tokens=512,
        )

        payload = provider._client.post.call_args[1]["json"]
        assert payload["generationConfig"]["temperature"] == 0.5
        assert payload["generationConfig"]["maxOutputTokens"] == 512


class TestGoogleProviderModelList:
    @pytest.mark.asyncio
    async def test_list_models(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(200, json=_models_response())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        models = await provider.list_models()
        assert len(models) == 3
        # sorted by id
        assert models[0].id == "gemini-1.5-pro"
        assert models[0].provider == "google"
        assert models[0].supports_tools is True
        # context_window from inputTokenLimit
        pro = next(m for m in models if m.id == "gemini-2.5-pro")
        assert pro.context_window == 1000000


class TestGoogleProviderHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_true(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(200, json=_models_response())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        assert await provider.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_false(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(403, text="Forbidden")
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        assert await provider.health_check() is False


class TestGoogleProviderStatusCodes:
    @pytest.mark.asyncio
    async def test_check_status_401(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(401, text="API key invalid")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_check_status_403(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(403, text="Forbidden")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_check_status_429(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(429, text="Quota exceeded")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RateLimitError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_check_status_404(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(404, text="Model not found")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ModelNotFoundError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_timeout_raises_provider_error(self) -> None:
        provider = GoogleProvider(_config())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with pytest.raises(ProviderError, match="timed out"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_connect_error_raises_provider_error(self) -> None:
        provider = GoogleProvider(_config())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(ProviderError, match="Failed to connect"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])


class TestGoogleModelSupportsTools:
    def test_gemini_supports_tools(self) -> None:
        assert GoogleProvider._model_supports_tools("gemini-2.5-pro") is True
        assert GoogleProvider._model_supports_tools("gemini-1.5-flash") is True

    def test_non_gemini_no_tools(self) -> None:
        assert GoogleProvider._model_supports_tools("palm-2") is False

    def test_finish_reason_mapping(self) -> None:
        provider = GoogleProvider(_config())
        data = _generate_content_response(finish_reason="MAX_TOKENS")
        # Parse directly
        result = provider._parse_response(data, "gemini-2.5-pro")
        assert result.finish_reason == "length"

    def test_safety_finish_reason(self) -> None:
        provider = GoogleProvider(_config())
        data = _generate_content_response(finish_reason="SAFETY")
        result = provider._parse_response(data, "gemini-2.5-pro")
        assert result.finish_reason == "content_filter"


class TestGoogleProviderStreamAndClose:
    @pytest.mark.asyncio
    async def test_generate_with_stream_true_calls_collect_stream(self) -> None:
        """generate(stream=True) should go through _collect_stream."""
        provider = GoogleProvider(_config())

        event1 = {"candidates": [{"content": {"parts": [{"text": "Hi"}]}, "finishReason": None}]}
        event2 = {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]}

        sse_lines = [
            f"data: {json.dumps(event1)}",
            f"data: {json.dumps(event2)}",
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
        assert result.provider == "google"

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        provider = GoogleProvider(_config())
        provider._client = AsyncMock()
        await provider.close()
        provider._client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_skips_invalid_json(self) -> None:
        provider = GoogleProvider(_config())

        event = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": None}]}
        sse_lines = [
            "data: not-valid-json",
            f"data: {json.dumps(event)}",
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
        provider = GoogleProvider(_config())

        event = {"candidates": [{"content": {"parts": [{"text": "X"}]}, "finishReason": None}]}
        sse_lines = [
            "event: ping",
            "data: ",
            f"data: {json.dumps(event)}",
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

    @pytest.mark.asyncio
    async def test_stream_skips_done_sentinel(self) -> None:
        provider = GoogleProvider(_config())

        event = {"candidates": [{"content": {"parts": [{"text": "Y"}]}, "finishReason": None}]}
        sse_lines = [
            f"data: {json.dumps(event)}",
            "data: [DONE]",
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

        assert any(c.content == "Y" for c in chunks)

    def test_parse_stream_event_no_candidates_returns_none(self) -> None:
        result = GoogleProvider._parse_stream_event({"candidates": []}, "gemini-2.5-pro")
        assert result is None

    @pytest.mark.asyncio
    async def test_check_status_500(self) -> None:
        provider = GoogleProvider(_config())
        mock_resp = httpx.Response(500, text="Internal Server Error")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ProviderError, match="500"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])
