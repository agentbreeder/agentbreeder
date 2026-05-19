"""Tests for engine/providers/ — the provider abstraction layer.

Tests cover:
- Pydantic models (GenerateResult, ModelInfo, ProviderConfig, etc.)
- OpenAI provider (mocked HTTP)
- Ollama provider (mocked HTTP)
- Fallback chain logic
- Provider registry / factory
- Error handling (auth, rate limit, model not found, connection)
- Ollama auto-detection
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.base import (
    AuthenticationError,
    ModelNotFoundError,
    ProviderBase,
    ProviderError,
    RateLimitError,
)
from engine.providers.models import (
    FallbackConfig,
    GenerateResult,
    Message,
    ModelInfo,
    ProviderConfig,
    ProviderHealth,
    ProviderType,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    ToolFunction,
    UsageInfo,
)
from engine.providers.ollama_provider import OllamaProvider
from engine.providers.openai_provider import OpenAIProvider
from engine.providers.registry import (
    FallbackChain,
    create_provider,
    create_provider_from_env,
)

# ─── Pydantic models ────────────────────────────────────────────────────────


class TestModels:
    def test_generate_result_defaults(self) -> None:
        result = GenerateResult()
        assert result.content is None
        assert result.tool_calls == []
        assert result.finish_reason == "stop"
        assert result.usage.total_tokens == 0
        assert result.model == ""

    def test_generate_result_with_content(self) -> None:
        result = GenerateResult(
            content="Hello!",
            model="gpt-4o",
            provider="openai",
            usage=UsageInfo(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        assert result.content == "Hello!"
        assert result.usage.total_tokens == 15

    def test_generate_result_with_tool_calls(self) -> None:
        tc = ToolCall(
            id="call_123",
            function_name="get_weather",
            function_arguments='{"city": "London"}',
        )
        result = GenerateResult(tool_calls=[tc], finish_reason="tool_calls")
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].function_name == "get_weather"
        assert result.finish_reason == "tool_calls"

    def test_tool_definition(self) -> None:
        tool = ToolDefinition(
            function=ToolFunction(
                name="search",
                description="Search the web",
                parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            )
        )
        assert tool.type == "function"
        assert tool.function.name == "search"
        dumped = tool.model_dump()
        assert dumped["function"]["name"] == "search"

    def test_message(self) -> None:
        msg = Message(role="user", content="Hi")
        assert msg.role == "user"
        assert msg.content == "Hi"
        assert msg.tool_calls is None

    def test_model_info(self) -> None:
        info = ModelInfo(id="gpt-4o", provider="openai", supports_tools=True, is_local=False)
        assert info.id == "gpt-4o"
        assert info.supports_tools is True
        assert info.is_local is False

    def test_model_info_local(self) -> None:
        info = ModelInfo(id="llama3.2:latest", provider="ollama", is_local=True)
        assert info.is_local is True

    def test_provider_config_defaults(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.openai)
        assert config.timeout == 60.0
        assert config.max_retries == 2
        assert config.api_key is None

    def test_fallback_config(self) -> None:
        primary = ProviderConfig(provider_type=ProviderType.openai, api_key="sk-test")
        fallback = ProviderConfig(provider_type=ProviderType.ollama)
        chain = FallbackConfig(primary=primary, fallbacks=[fallback])
        assert len(chain.fallbacks) == 1

    def test_stream_chunk(self) -> None:
        chunk = StreamChunk(content="Hello", model="gpt-4o")
        assert chunk.content == "Hello"
        assert chunk.finish_reason is None

    def test_usage_info(self) -> None:
        usage = UsageInfo(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert usage.total_tokens == 150

    def test_provider_type_values(self) -> None:
        assert ProviderType.openai == "openai"
        assert ProviderType.ollama == "ollama"

    def test_rate_limit_error_retry_after(self) -> None:
        err = RateLimitError("rate limited", retry_after=30.0)
        assert err.retry_after == 30.0
        assert "rate limited" in str(err)


# ─── OpenAI provider ────────────────────────────────────────────────────────


def _openai_config(api_key: str = "sk-test-key") -> ProviderConfig:
    return ProviderConfig(
        provider_type=ProviderType.openai,
        api_key=api_key,
        default_model="gpt-4o",
    )


def _chat_completion_response(
    content: str = "Hello!",
    model: str = "gpt-4o",
    tool_calls: list | None = None,
) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class TestOpenAIProvider:
    def test_requires_api_key(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.openai)
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(AuthenticationError, match="API key not found"):
                OpenAIProvider(config)

    def test_reads_api_key_from_env(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.openai)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-env-key"}):
            provider = OpenAIProvider(config)
            assert provider._api_key == "sk-env-key"

    def test_name(self) -> None:
        provider = OpenAIProvider(_openai_config())
        assert provider.name == "openai"

    @pytest.mark.asyncio
    async def test_generate_basic(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(200, json=_chat_completion_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.generate(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.content == "Hello!"
        assert result.model == "gpt-4o"
        assert result.provider == "openai"
        assert result.usage.total_tokens == 15
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_generate_with_tool_calls(self) -> None:
        provider = OpenAIProvider(_openai_config())
        tool_calls = [
            {
                "id": "call_abc",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city": "London"}',
                },
            }
        ]
        mock_resp = httpx.Response(
            200, json=_chat_completion_response(content="", tool_calls=tool_calls)
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
        assert result.tool_calls[0].id == "call_abc"

    @pytest.mark.asyncio
    async def test_generate_with_temperature_and_max_tokens(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(200, json=_chat_completion_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        await provider.generate(
            messages=[{"role": "user", "content": "Hi"}],
            temperature=0.5,
            max_tokens=100,
        )

        call_args = provider._client.post.call_args
        payload = call_args[1]["json"]
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 100

    @pytest.mark.asyncio
    async def test_generate_uses_explicit_model(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(200, json=_chat_completion_response(model="gpt-4o-mini"))
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.generate(
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-4o-mini",
        )

        call_args = provider._client.post.call_args
        assert call_args[1]["json"]["model"] == "gpt-4o-mini"
        assert result.model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_generate_auth_error(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(401, text="Unauthorized")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError, match="Invalid OpenAI API key"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_generate_rate_limit_error(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(429, text="Rate limited")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RateLimitError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_generate_model_not_found(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(404, text="Not found")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ModelNotFoundError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_generate_timeout(self) -> None:
        provider = OpenAIProvider(_openai_config())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with pytest.raises(ProviderError, match="timed out"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_generate_connection_error(self) -> None:
        provider = OpenAIProvider(_openai_config())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with pytest.raises(ProviderError, match="Failed to connect"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_list_models(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-4o", "object": "model"},
                    {"id": "gpt-3.5-turbo", "object": "model"},
                    {"id": "text-embedding-3-small", "object": "model"},
                ]
            },
        )
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        models = await provider.list_models()
        assert len(models) == 3
        # Sorted by id
        assert models[0].id == "gpt-3.5-turbo"
        assert models[0].supports_tools is True
        assert models[1].id == "gpt-4o"
        assert models[1].supports_tools is True
        assert models[2].id == "text-embedding-3-small"
        assert models[2].supports_tools is False

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(200, json={"data": []})
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        result = await provider.health_check()
        assert bool(result) is True
        assert result.healthy is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(401, text="Unauthorized")
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        result = await provider.health_check()
        assert bool(result) is False
        assert result.healthy is False

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        provider = OpenAIProvider(_openai_config())
        provider._client = AsyncMock()
        await provider.close()
        provider._client.aclose.assert_called_once()

    def test_model_supports_tools(self) -> None:
        assert OpenAIProvider._model_supports_tools("gpt-4o") is True
        assert OpenAIProvider._model_supports_tools("gpt-4o-mini") is True
        assert OpenAIProvider._model_supports_tools("gpt-3.5-turbo") is True
        assert OpenAIProvider._model_supports_tools("o3-mini") is True
        assert OpenAIProvider._model_supports_tools("o4-mini") is True
        assert OpenAIProvider._model_supports_tools("text-embedding-3-small") is False
        assert OpenAIProvider._model_supports_tools("dall-e-3") is False

    def test_resolve_model_no_default(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.openai, api_key="sk-test")
        provider = OpenAIProvider(config)
        with pytest.raises(ProviderError, match="No model specified"):
            provider._resolve_model(None)

    @pytest.mark.asyncio
    async def test_generate_server_error(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(500, text="Internal Server Error")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ProviderError, match="500"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])


# ─── Ollama provider ────────────────────────────────────────────────────────


def _ollama_config() -> ProviderConfig:
    return ProviderConfig(
        provider_type=ProviderType.ollama,
        base_url="http://localhost:11434",
        default_model="llama3.2",
    )


class TestOllamaProvider:
    def test_name(self) -> None:
        provider = OllamaProvider(_ollama_config())
        assert provider.name == "ollama"

    def test_default_base_url(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.ollama, default_model="llama3.2")
        with patch.dict("os.environ", {}, clear=True):
            provider = OllamaProvider(config)
            assert "localhost:11434" in provider._base_url

    def test_base_url_from_env(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.ollama, default_model="llama3.2")
        with patch.dict("os.environ", {"OLLAMA_BASE_URL": "http://gpu-box:11434"}):
            provider = OllamaProvider(config)
            assert "gpu-box:11434" in provider._base_url

    @pytest.mark.asyncio
    async def test_generate_basic(self) -> None:
        provider = OllamaProvider(_ollama_config())
        mock_resp = httpx.Response(200, json=_chat_completion_response(model="llama3.2"))
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.generate(
            messages=[{"role": "user", "content": "Hi"}],
        )

        assert result.content == "Hello!"
        assert result.model == "llama3.2"
        assert result.provider == "ollama"

    @pytest.mark.asyncio
    async def test_generate_uses_v1_endpoint(self) -> None:
        provider = OllamaProvider(_ollama_config())
        mock_resp = httpx.Response(200, json=_chat_completion_response())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        await provider.generate(messages=[{"role": "user", "content": "Hi"}])

        call_args = provider._client.post.call_args
        assert call_args[0][0] == "/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_generate_connection_error(self) -> None:
        provider = OllamaProvider(_ollama_config())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with pytest.raises(ProviderError, match="Cannot connect to Ollama"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_generate_timeout(self) -> None:
        provider = OllamaProvider(_ollama_config())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with pytest.raises(ProviderError, match="timed out"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_generate_model_not_found(self) -> None:
        provider = OllamaProvider(_ollama_config())
        mock_resp = httpx.Response(404, text="model not found")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ModelNotFoundError, match="ollama pull"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_list_models(self) -> None:
        provider = OllamaProvider(_ollama_config())
        mock_resp = httpx.Response(
            200,
            json={
                "models": [
                    {"name": "llama3.2:latest", "size": 4_000_000_000},
                    {"name": "mistral:latest", "size": 3_000_000_000},
                    {"name": "nomic-embed-text:latest", "size": 500_000_000},
                ]
            },
        )
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        models = await provider.list_models()
        assert len(models) == 3
        assert all(m.is_local for m in models)
        assert all(m.provider == "ollama" for m in models)
        # llama3.2 supports tools
        llama = next(m for m in models if m.id == "llama3.2:latest")
        assert llama.supports_tools is True
        # nomic-embed does not
        nomic = next(m for m in models if m.id == "nomic-embed-text:latest")
        assert nomic.supports_tools is False

    @pytest.mark.asyncio
    async def test_list_models_connection_error(self) -> None:
        provider = OllamaProvider(_ollama_config())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with pytest.raises(ProviderError, match="Cannot connect to Ollama"):
            await provider.list_models()

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        provider = OllamaProvider(_ollama_config())
        mock_resp = httpx.Response(200, text="Ollama is running")
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        result = await provider.health_check()
        assert bool(result) is True
        assert result.healthy is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        provider = OllamaProvider(_ollama_config())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        result = await provider.health_check()
        assert bool(result) is False
        assert result.healthy is False

    @pytest.mark.asyncio
    async def test_detect_running(self) -> None:
        mock_resp = httpx.Response(200, text="Ollama is running")
        with patch("engine.providers.ollama_provider.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await OllamaProvider.detect()
            assert result is True

    @pytest.mark.asyncio
    async def test_detect_not_running(self) -> None:
        with patch("engine.providers.ollama_provider.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await OllamaProvider.detect()
            assert result is False

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        provider = OllamaProvider(_ollama_config())
        provider._client = AsyncMock()
        await provider.close()
        provider._client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_server_error(self) -> None:
        provider = OllamaProvider(_ollama_config())
        mock_resp = httpx.Response(500, text="Internal error")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(ProviderError, match="500"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])


# ─── Provider registry / factory ────────────────────────────────────────────


class TestProviderRegistry:
    def test_create_openai_provider(self) -> None:
        config = ProviderConfig(
            provider_type=ProviderType.openai, api_key="sk-test", default_model="gpt-4o"
        )
        provider = create_provider(config)
        assert isinstance(provider, OpenAIProvider)
        assert provider.name == "openai"

    def test_create_ollama_provider(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.ollama, default_model="llama3.2")
        provider = create_provider(config)
        assert isinstance(provider, OllamaProvider)
        assert provider.name == "ollama"

    def test_create_unsupported_provider(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.openai, api_key="sk-test")
        # Monkey-patch to test unsupported type
        config.provider_type = "nonexistent"  # type: ignore[assignment]
        with pytest.raises(KeyError, match="not supported"):
            create_provider(config)

    def test_create_from_env_openai(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-env"}):
            provider = create_provider_from_env(ProviderType.openai, model="gpt-4o")
            assert isinstance(provider, OpenAIProvider)

    def test_create_from_env_ollama(self) -> None:
        provider = create_provider_from_env(ProviderType.ollama, model="llama3.2")
        assert isinstance(provider, OllamaProvider)


# ─── Fallback chain ─────────────────────────────────────────────────────────


class TestFallbackChain:
    def _make_chain(self) -> FallbackChain:
        primary = ProviderConfig(
            provider_type=ProviderType.openai, api_key="sk-test", default_model="gpt-4o"
        )
        fallback = ProviderConfig(provider_type=ProviderType.ollama, default_model="llama3.2")
        return FallbackChain(FallbackConfig(primary=primary, fallbacks=[fallback]))

    def test_chain_has_correct_providers(self) -> None:
        chain = self._make_chain()
        assert len(chain.providers) == 2
        assert chain.providers[0].name == "openai"
        assert chain.providers[1].name == "ollama"

    @pytest.mark.asyncio
    async def test_primary_succeeds(self) -> None:
        chain = self._make_chain()
        expected = GenerateResult(content="From primary", provider="openai", model="gpt-4o")
        chain._providers[0].generate = AsyncMock(return_value=expected)
        chain._providers[1].generate = AsyncMock()

        result = await chain.generate(messages=[{"role": "user", "content": "Hi"}])

        assert result.content == "From primary"
        chain._providers[0].generate.assert_called_once()
        chain._providers[1].generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self) -> None:
        chain = self._make_chain()
        chain._providers[0].generate = AsyncMock(side_effect=ProviderError("OpenAI down"))
        expected = GenerateResult(content="From fallback", provider="ollama", model="llama3.2")
        chain._providers[1].generate = AsyncMock(return_value=expected)

        result = await chain.generate(messages=[{"role": "user", "content": "Hi"}])

        assert result.content == "From fallback"
        assert result.provider == "ollama"

    @pytest.mark.asyncio
    async def test_all_providers_fail(self) -> None:
        chain = self._make_chain()
        chain._providers[0].generate = AsyncMock(side_effect=ProviderError("OpenAI down"))
        chain._providers[1].generate = AsyncMock(side_effect=ProviderError("Ollama down"))

        with pytest.raises(ProviderError, match="All providers.*failed"):
            await chain.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_list_all_models(self) -> None:
        chain = self._make_chain()
        chain._providers[0].list_models = AsyncMock(
            return_value=[ModelInfo(id="gpt-4o", provider="openai")]
        )
        chain._providers[1].list_models = AsyncMock(
            return_value=[ModelInfo(id="llama3.2", provider="ollama", is_local=True)]
        )

        models = await chain.list_all_models()
        assert len(models) == 2
        ids = {m.id for m in models}
        assert ids == {"gpt-4o", "llama3.2"}

    @pytest.mark.asyncio
    async def test_list_all_models_partial_failure(self) -> None:
        chain = self._make_chain()
        chain._providers[0].list_models = AsyncMock(side_effect=ProviderError("OpenAI down"))
        chain._providers[1].list_models = AsyncMock(
            return_value=[ModelInfo(id="llama3.2", provider="ollama")]
        )

        models = await chain.list_all_models()
        assert len(models) == 1
        assert models[0].id == "llama3.2"

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        chain = self._make_chain()
        chain._providers[0].close = AsyncMock()
        chain._providers[1].close = AsyncMock()

        await chain.close()

        chain._providers[0].close.assert_called_once()
        chain._providers[1].close.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_passes_params(self) -> None:
        chain = self._make_chain()
        chain._providers[0].generate = AsyncMock(side_effect=ProviderError("fail"))
        expected = GenerateResult(content="ok", provider="ollama")
        chain._providers[1].generate = AsyncMock(return_value=expected)

        tools = [ToolDefinition(function=ToolFunction(name="t", description="d"))]
        await chain.generate(
            messages=[{"role": "user", "content": "Hi"}],
            model="test-model",
            temperature=0.5,
            max_tokens=100,
            tools=tools,
        )

        call_kwargs = chain._providers[1].generate.call_args[1]
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["max_tokens"] == 100
        assert call_kwargs["tools"] == tools


# ─── __init__ exports ────────────────────────────────────────────────────────


class TestExports:
    def test_all_exports_importable(self) -> None:
        import engine.providers as ep

        # Provider classes
        assert ep.ProviderBase is ProviderBase
        assert ep.OpenAIProvider is OpenAIProvider
        assert ep.OllamaProvider is OllamaProvider

        # Factory functions
        assert ep.create_provider is create_provider
        assert ep.create_provider_from_env is create_provider_from_env
        assert ep.FallbackChain is FallbackChain

        # Models
        assert ep.ProviderType is ProviderType
        assert ep.ProviderConfig is ProviderConfig
        assert ep.GenerateResult is GenerateResult
        assert ep.StreamChunk is StreamChunk
        assert ep.ModelInfo is ModelInfo
        assert ep.ToolDefinition is ToolDefinition
        assert ep.ToolCall is ToolCall

        # Errors
        assert ep.ProviderError is ProviderError
        assert ep.AuthenticationError is AuthenticationError
        assert ep.RateLimitError is RateLimitError
        assert ep.ModelNotFoundError is ModelNotFoundError


# ─── Wave 4 P1 fixes (W4-19..22) ─────────────────────────────────────────────


def _anthropic_config(api_key: str = "sk-ant-test") -> ProviderConfig:
    return ProviderConfig(
        provider_type=ProviderType.anthropic,
        api_key=api_key,
        default_model="claude-sonnet-4",
    )


class TestW0419Status403Mapping:
    """W4-19: HTTP 403 must map to AuthenticationError on OpenAI + Anthropic.

    Google already does this; verifying parity across the three.
    """

    @pytest.mark.asyncio
    async def test_openai_403_raises_authentication_error(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(403, text="Forbidden")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError, match="Invalid OpenAI API key"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_anthropic_403_raises_authentication_error(self) -> None:
        provider = AnthropicProvider(_anthropic_config())
        mock_resp = httpx.Response(403, text="Forbidden")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError, match="Invalid Anthropic API key"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_openai_401_still_raises_authentication_error(self) -> None:
        # Regression: 401 path must not be broken by 403 addition.
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(401, text="Unauthorized")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_anthropic_401_still_raises_authentication_error(self) -> None:
        provider = AnthropicProvider(_anthropic_config())
        mock_resp = httpx.Response(401, text="Unauthorized")
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(AuthenticationError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])


class TestW0420FallbackChainGenerateStream:
    """W4-20: FallbackChain.generate_stream() — streaming clients also failover."""

    def _make_chain(self) -> FallbackChain:
        primary = ProviderConfig(
            provider_type=ProviderType.openai, api_key="sk-test", default_model="gpt-4o"
        )
        fallback = ProviderConfig(provider_type=ProviderType.ollama, default_model="llama3.2")
        return FallbackChain(FallbackConfig(primary=primary, fallbacks=[fallback]))

    @staticmethod
    async def _aiter(chunks: list[StreamChunk]) -> AsyncIterator[StreamChunk]:
        for c in chunks:
            yield c

    @staticmethod
    async def _aiter_failing() -> AsyncIterator[StreamChunk]:
        raise ProviderError("primary stream failed")
        yield  # pragma: no cover — make this an async generator

    @pytest.mark.asyncio
    async def test_primary_streams_successfully(self) -> None:
        chain = self._make_chain()
        chunks = [
            StreamChunk(content="Hello", model="gpt-4o"),
            StreamChunk(content=" world", model="gpt-4o"),
        ]

        # `generate_stream` is sync method returning an async iterator.
        chain._providers[0].generate_stream = lambda **kwargs: self._aiter(chunks)
        chain._providers[1].generate_stream = AsyncMock()

        out: list[StreamChunk] = []
        async for c in chain.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
            out.append(c)

        assert [c.content for c in out] == ["Hello", " world"]
        # Fallback should never have been invoked
        chain._providers[1].generate_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_streams_when_primary_fails_to_open(self) -> None:
        chain = self._make_chain()

        fallback_chunks = [
            StreamChunk(content="From", model="llama3.2"),
            StreamChunk(content=" fallback", model="llama3.2"),
        ]

        chain._providers[0].generate_stream = lambda **kwargs: self._aiter_failing()
        chain._providers[1].generate_stream = lambda **kwargs: self._aiter(fallback_chunks)

        out: list[StreamChunk] = []
        async for c in chain.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
            out.append(c)

        # All fallback chunks should be present
        assert [c.content for c in out] == ["From", " fallback"]

    @pytest.mark.asyncio
    async def test_all_providers_fail_to_stream(self) -> None:
        chain = self._make_chain()

        chain._providers[0].generate_stream = lambda **kwargs: self._aiter_failing()
        chain._providers[1].generate_stream = lambda **kwargs: self._aiter_failing()

        with pytest.raises(ProviderError, match="failed to stream"):
            async for _ in chain.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
                pass

    @pytest.mark.asyncio
    async def test_midstream_failure_after_first_chunk_propagates(self) -> None:
        """Once committed to a provider, mid-stream failures are NOT retried."""
        chain = self._make_chain()

        async def primary_partial_then_fail() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(content="partial", model="gpt-4o")
            raise ProviderError("died mid-stream")

        # Fallback would be silently called by a buggy implementation; assert NOT called.
        fallback_mock = AsyncMock()
        chain._providers[0].generate_stream = lambda **kwargs: primary_partial_then_fail()
        chain._providers[1].generate_stream = fallback_mock

        out: list[StreamChunk] = []

        async def _consume() -> None:
            async for c in chain.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
                out.append(c)

        with pytest.raises(ProviderError, match="died mid-stream"):
            await _consume()

        # The partial chunk was emitted before the mid-stream failure;
        # the fallback must NOT have been retried.
        assert [c.content for c in out] == ["partial"]
        fallback_mock.assert_not_called()


class TestW0421GenerateResultFallbackFrom:
    """W4-21: GenerateResult.fallback_from populated by FallbackChain."""

    def test_field_default_is_none(self) -> None:
        result = GenerateResult()
        assert result.fallback_from is None

    def test_field_accepts_provider_name(self) -> None:
        result = GenerateResult(provider="ollama", fallback_from="openai")
        assert result.fallback_from == "openai"
        # Existing fields untouched
        assert result.provider == "ollama"

    def _make_chain(self) -> FallbackChain:
        primary = ProviderConfig(
            provider_type=ProviderType.openai, api_key="sk-test", default_model="gpt-4o"
        )
        fallback = ProviderConfig(provider_type=ProviderType.ollama, default_model="llama3.2")
        return FallbackChain(FallbackConfig(primary=primary, fallbacks=[fallback]))

    @pytest.mark.asyncio
    async def test_primary_success_leaves_fallback_from_none(self) -> None:
        chain = self._make_chain()
        expected = GenerateResult(content="ok", provider="openai", model="gpt-4o")
        chain._providers[0].generate = AsyncMock(return_value=expected)

        result = await chain.generate(messages=[{"role": "user", "content": "Hi"}])

        assert result.fallback_from is None
        assert result.provider == "openai"

    @pytest.mark.asyncio
    async def test_fallback_sets_fallback_from_to_primary_name(self) -> None:
        chain = self._make_chain()
        chain._providers[0].generate = AsyncMock(side_effect=ProviderError("down"))
        chain._providers[1].generate = AsyncMock(
            return_value=GenerateResult(content="ok", provider="ollama", model="llama3.2")
        )

        result = await chain.generate(messages=[{"role": "user", "content": "Hi"}])

        assert result.provider == "ollama"
        assert result.fallback_from == "openai"


class TestW0422OllamaTokenCounts:
    """W4-22: Ollama UsageInfo populated from prompt_eval_count / eval_count."""

    def test_extract_usage_openai_compat_shape(self) -> None:
        """OpenAI-compat /v1 endpoint returns standard usage dict."""
        data = {
            "model": "llama3.2",
            "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
        }
        usage = OllamaProvider._extract_usage(data)
        assert usage.prompt_tokens == 12
        assert usage.completion_tokens == 7
        assert usage.total_tokens == 19

    def test_extract_usage_native_shape(self) -> None:
        """Native /api/chat endpoint puts counts at top level."""
        data = {
            "model": "llama3.2",
            "prompt_eval_count": 25,
            "eval_count": 11,
            "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
        }
        usage = OllamaProvider._extract_usage(data)
        assert usage.prompt_tokens == 25
        assert usage.completion_tokens == 11
        # No total_tokens provided → derived as sum
        assert usage.total_tokens == 36

    def test_extract_usage_missing_fields_defaults_to_zero(self) -> None:
        """Models that don't return token counts leave fields at 0."""
        data = {
            "model": "llama3.2",
            "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
        }
        usage = OllamaProvider._extract_usage(data)
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_extract_usage_openai_compat_preferred_over_native(self) -> None:
        """If both shapes appear, OpenAI-compat usage wins (it's authoritative)."""
        data = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "prompt_eval_count": 999,
            "eval_count": 888,
        }
        usage = OllamaProvider._extract_usage(data)
        assert usage.prompt_tokens == 10
        assert usage.completion_tokens == 5
        assert usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_generate_populates_usage_from_native_fields(self) -> None:
        """End-to-end: native response shape produces a populated UsageInfo."""
        provider = OllamaProvider(_ollama_config())
        # Build a response with no `usage` dict, only native fields.
        native_response = {
            "model": "llama3.2",
            "choices": [{"message": {"content": "Hi"}, "finish_reason": "stop"}],
            "prompt_eval_count": 42,
            "eval_count": 9,
        }
        mock_resp = httpx.Response(200, json=native_response)
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=mock_resp)

        result = await provider.generate(messages=[{"role": "user", "content": "Hi"}])

        assert result.usage.prompt_tokens == 42
        assert result.usage.completion_tokens == 9
        assert result.usage.total_tokens == 51


# ─── Wave 5 P2 fixes (W5 M5-M8) ──────────────────────────────────────────────


class TestW05M5ProviderHealth:
    """W5-M5: health_check() returns a structured ProviderHealth result.

    The legacy bool API is preserved via ``__bool__``: ``if not result:``
    keeps working for existing callers (e.g. cli/commands/chat.py).
    """

    def test_provider_health_truthy_when_healthy(self) -> None:
        h = ProviderHealth(healthy=True)
        assert bool(h) is True
        assert h.healthy is True
        assert h.reason is None
        assert h.error_code is None

    def test_provider_health_falsy_when_unhealthy(self) -> None:
        h = ProviderHealth(healthy=False, reason="auth failed", error_code=401)
        assert bool(h) is False
        # Legacy idiom: ``if not result`` should branch into the failure path.
        assert not h
        assert h.reason == "auth failed"
        assert h.error_code == 401

    @pytest.mark.asyncio
    async def test_openai_health_distinguishes_auth_from_reachability(self) -> None:
        # Auth failure → error_code=401
        provider = OpenAIProvider(_openai_config())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=httpx.Response(401, text="Unauthorized"))

        result = await provider.health_check()
        assert isinstance(result, ProviderHealth)
        assert result.healthy is False
        assert result.error_code == 401
        assert result.reason is not None

        # Reachability failure → error_code is None
        provider2 = OpenAIProvider(_openai_config())
        provider2._client = AsyncMock()
        provider2._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        result2 = await provider2.health_check()
        assert result2.healthy is False
        assert result2.error_code is None
        assert "connect" in (result2.reason or "").lower()

    @pytest.mark.asyncio
    async def test_openai_health_rate_limit_reported_as_429(self) -> None:
        provider = OpenAIProvider(_openai_config())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=httpx.Response(429, text="slow down"))

        result = await provider.health_check()
        assert result.healthy is False
        assert result.error_code == 429

    @pytest.mark.asyncio
    async def test_openai_health_server_error_reported_as_500(self) -> None:
        provider = OpenAIProvider(_openai_config())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=httpx.Response(500, text="Server error"))

        result = await provider.health_check()
        assert result.healthy is False
        assert result.error_code == 500

    @pytest.mark.asyncio
    async def test_ollama_health_distinguishes_reachability_from_http_error(self) -> None:
        # Connection refused → no error_code
        provider = OllamaProvider(_ollama_config())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        result = await provider.health_check()
        assert isinstance(result, ProviderHealth)
        assert result.healthy is False
        assert result.error_code is None

        # HTTP 503 from Ollama → error_code=503
        provider2 = OllamaProvider(_ollama_config())
        provider2._client = AsyncMock()
        provider2._client.get = AsyncMock(return_value=httpx.Response(503, text="busy"))

        result2 = await provider2.health_check()
        assert result2.healthy is False
        assert result2.error_code == 503

    @pytest.mark.asyncio
    async def test_anthropic_health_returns_provider_health(self) -> None:
        provider = AnthropicProvider(_anthropic_config())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=httpx.Response(200, json={"data": []}))

        result = await provider.health_check()
        assert isinstance(result, ProviderHealth)
        assert result.healthy is True


class TestW05M6FallbackStreamingTests:
    """W5-M6: streaming fallback chain regression coverage.

    Builds on the W4-M2 commit semantics: once a chunk is yielded,
    a mid-stream failure must not be retried. This class restates
    the contract from multiple angles and adds tests for the all-fail
    chain-context message.
    """

    def _make_chain(self) -> FallbackChain:
        primary = ProviderConfig(
            provider_type=ProviderType.openai, api_key="sk-test", default_model="gpt-4o"
        )
        fallback = ProviderConfig(provider_type=ProviderType.ollama, default_model="llama3.2")
        return FallbackChain(FallbackConfig(primary=primary, fallbacks=[fallback]))

    @staticmethod
    async def _aiter(chunks: list[StreamChunk]) -> AsyncIterator[StreamChunk]:
        for c in chunks:
            yield c

    @pytest.mark.asyncio
    async def test_streaming_primary_fails_mid_stream_no_retry(self) -> None:
        """Mid-stream failure (after first chunk) is NEVER retried (W4-M2)."""
        chain = self._make_chain()

        async def primary_yield_then_die() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(content="abc", model="gpt-4o")
            yield StreamChunk(content="def", model="gpt-4o")
            raise ProviderError("network died after 2 chunks")

        fallback_mock = AsyncMock()
        chain._providers[0].generate_stream = lambda **kwargs: primary_yield_then_die()
        chain._providers[1].generate_stream = fallback_mock

        emitted: list[StreamChunk] = []

        async def _consume() -> None:
            async for c in chain.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
                emitted.append(c)

        with pytest.raises(ProviderError, match="network died after 2 chunks"):
            await _consume()

        # Both successful chunks delivered; fallback never invoked.
        assert [c.content for c in emitted] == ["abc", "def"]
        fallback_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_streaming_primary_fails_before_first_chunk_fallback_wins(self) -> None:
        chain = self._make_chain()

        async def primary_dies_immediately() -> AsyncIterator[StreamChunk]:
            raise ProviderError("primary cannot open stream")
            yield  # pragma: no cover — async generator marker

        fallback_chunks = [
            StreamChunk(content="from", model="llama3.2"),
            StreamChunk(content=" fallback", model="llama3.2"),
        ]
        chain._providers[0].generate_stream = lambda **kwargs: primary_dies_immediately()
        chain._providers[1].generate_stream = lambda **kwargs: self._aiter(fallback_chunks)

        emitted: list[StreamChunk] = []
        async for c in chain.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
            emitted.append(c)

        # All fallback chunks present; primary's empty stream produced no chunks.
        contents = [c.content for c in emitted]
        assert "from" in contents
        assert " fallback" in contents

    @pytest.mark.asyncio
    async def test_streaming_all_fail_exception_has_chain_context(self) -> None:
        """When every provider fails, the raised error mentions the chain."""
        chain = self._make_chain()

        async def primary_fail() -> AsyncIterator[StreamChunk]:
            raise ProviderError("primary boom")
            yield  # pragma: no cover

        async def fallback_fail() -> AsyncIterator[StreamChunk]:
            raise ProviderError("fallback boom")
            yield  # pragma: no cover

        chain._providers[0].generate_stream = lambda **kwargs: primary_fail()
        chain._providers[1].generate_stream = lambda **kwargs: fallback_fail()

        with pytest.raises(ProviderError) as excinfo:
            async for _ in chain.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
                pass

        # Chain context is preserved: error message names the chain failure
        # and the last error encountered.
        msg = str(excinfo.value)
        assert "failed to stream" in msg.lower()
        assert "fallback boom" in msg  # last error preserved

    @pytest.mark.asyncio
    async def test_streaming_primary_succeeds_fallback_untouched(self) -> None:
        """Sanity check: when primary streams cleanly, fallback is never touched."""
        chain = self._make_chain()
        primary_chunks = [StreamChunk(content="primary", model="gpt-4o")]
        chain._providers[0].generate_stream = lambda **kwargs: self._aiter(primary_chunks)
        fallback_mock = AsyncMock()
        chain._providers[1].generate_stream = fallback_mock

        async for _ in chain.generate_stream(messages=[{"role": "user", "content": "Hi"}]):
            pass

        fallback_mock.assert_not_called()


class TestW05M7OllamaNotFoundMessage:
    """W5-M7: 404 message must suggest both API endpoint and CLI."""

    @pytest.mark.asyncio
    async def test_404_message_mentions_api_endpoint(self) -> None:
        provider = OllamaProvider(_ollama_config())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=httpx.Response(404, text="not found"))

        with pytest.raises(ModelNotFoundError) as excinfo:
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

        msg = str(excinfo.value)
        assert "/api/v1/providers" in msg
        assert "pull-model" in msg

    @pytest.mark.asyncio
    async def test_404_message_mentions_cli(self) -> None:
        provider = OllamaProvider(_ollama_config())
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=httpx.Response(404, text="not found"))

        with pytest.raises(ModelNotFoundError) as excinfo:
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

        msg = str(excinfo.value)
        assert "ollama pull" in msg
        # Resolved model name is interpolated so users know what to pull.
        assert "llama3.2" in msg

    @pytest.mark.asyncio
    async def test_404_message_falls_back_to_placeholder_when_model_unknown(self) -> None:
        """If no model context is threaded (direct _check_status call), show <model>."""
        provider = OllamaProvider(_ollama_config())
        with pytest.raises(ModelNotFoundError) as excinfo:
            provider._check_status(404)
        msg = str(excinfo.value)
        assert "<model>" in msg
        assert "ollama pull" in msg
        assert "/api/v1/providers" in msg


class TestW05M8ProviderConfigValidators:
    """W5-M8: ProviderConfig field validators."""

    def test_default_model_must_be_non_empty(self) -> None:
        # None is allowed (means "no default")
        ProviderConfig(provider_type=ProviderType.openai, default_model=None)
        # Empty string is rejected
        with pytest.raises(ValueError, match="default_model"):
            ProviderConfig(provider_type=ProviderType.openai, default_model="")
        # Whitespace-only is rejected
        with pytest.raises(ValueError, match="default_model"):
            ProviderConfig(provider_type=ProviderType.openai, default_model="   ")
        # Normal value is accepted
        cfg = ProviderConfig(provider_type=ProviderType.openai, default_model="gpt-4o")
        assert cfg.default_model == "gpt-4o"

    def test_api_key_cannot_be_whitespace(self) -> None:
        # None is allowed
        ProviderConfig(provider_type=ProviderType.openai, api_key=None)
        # Empty string rejected
        with pytest.raises(ValueError, match="api_key"):
            ProviderConfig(provider_type=ProviderType.openai, api_key="")
        # Whitespace-only rejected
        with pytest.raises(ValueError, match="api_key"):
            ProviderConfig(provider_type=ProviderType.openai, api_key="  \t \n ")
        # Normal key accepted
        cfg = ProviderConfig(provider_type=ProviderType.openai, api_key="sk-abc123")
        assert cfg.api_key == "sk-abc123"

    def test_base_url_must_be_valid_url(self) -> None:
        # None is allowed
        ProviderConfig(provider_type=ProviderType.openai, base_url=None)
        # Empty string rejected
        with pytest.raises(ValueError, match="base_url"):
            ProviderConfig(provider_type=ProviderType.openai, base_url="")
        # Whitespace-only rejected
        with pytest.raises(ValueError, match="base_url"):
            ProviderConfig(provider_type=ProviderType.openai, base_url="   ")
        # Missing scheme rejected
        with pytest.raises(ValueError, match="base_url"):
            ProviderConfig(provider_type=ProviderType.openai, base_url="localhost:11434")
        # Wrong scheme rejected
        with pytest.raises(ValueError, match="base_url"):
            ProviderConfig(provider_type=ProviderType.openai, base_url="ftp://example.com")
        # Missing host rejected
        with pytest.raises(ValueError, match="base_url"):
            ProviderConfig(provider_type=ProviderType.openai, base_url="http://")
        # Valid HTTP accepted
        cfg = ProviderConfig(provider_type=ProviderType.ollama, base_url="http://localhost:11434")
        assert cfg.base_url == "http://localhost:11434"
        # Valid HTTPS accepted
        cfg = ProviderConfig(
            provider_type=ProviderType.openai, base_url="https://api.openai.com/v1"
        )
        assert cfg.base_url == "https://api.openai.com/v1"
