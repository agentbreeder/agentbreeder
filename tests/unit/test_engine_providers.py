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

from unittest.mock import AsyncMock, patch

import httpx
import pytest

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

        assert await provider.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        provider = OpenAIProvider(_openai_config())
        mock_resp = httpx.Response(401, text="Unauthorized")
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=mock_resp)

        assert await provider.health_check() is False

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

        assert await provider.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        provider = OllamaProvider(_ollama_config())
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        assert await provider.health_check() is False

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
