"""Tests for engine/providers/catalog.py and openai_compatible.py.

Covers:
- Catalog YAML parser + Pydantic validation
- Built-in catalog ships the 9 expected presets with the right env vars
- User-local override merging (user-local wins on name collision)
- ``parse_model_ref`` handles slash refs, unknown providers, and edge cases
- ``OpenAICompatibleProvider`` requires base_url + api key, sends headers
- ``from_catalog`` builds a configured provider for catalog names
- ``list_models``, ``health_check``, ``generate`` against mocked HTTP
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from engine.providers.base import (
    AuthenticationError,
    ProviderError,
)
from engine.providers.catalog import (
    Catalog,
    CatalogEntry,
    CatalogError,
    GatewayModelRef,
    _parse_catalog,
    get_entry,
    list_entries,
    load_catalog,
    parse_gateway_ref,
    parse_model_ref,
    reset_cache,
)
from engine.providers.models import ProviderConfig, ProviderType
from engine.providers.openai_compatible import OpenAICompatibleProvider, from_catalog

# ─── Catalog parser ─────────────────────────────────────────────────────────


class TestCatalogParser:
    def test_parse_minimal_entry(self) -> None:
        data = {
            "version": 1,
            "providers": {
                "foo": {
                    "type": "openai_compatible",
                    "base_url": "https://api.foo.com/v1",
                    "api_key_env": "FOO_API_KEY",
                }
            },
        }
        catalog = _parse_catalog(data, source="builtin")
        assert catalog.version == 1
        assert "foo" in catalog.providers
        entry = catalog.providers["foo"]
        assert str(entry.base_url).rstrip("/") == "https://api.foo.com/v1"
        assert entry.api_key_env == "FOO_API_KEY"
        assert entry.type == "openai_compatible"
        assert entry.source == "builtin"

    def test_parse_with_default_headers(self) -> None:
        data = {
            "providers": {
                "openrouter": {
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key_env": "OPENROUTER_API_KEY",
                    "default_headers": {
                        "HTTP-Referer": "https://agentbreeder.io",
                        "X-Title": "AgentBreeder",
                    },
                }
            }
        }
        catalog = _parse_catalog(data, source="builtin")
        entry = catalog.providers["openrouter"]
        assert entry.default_headers["HTTP-Referer"] == "https://agentbreeder.io"
        assert entry.default_headers["X-Title"] == "AgentBreeder"

    def test_invalid_base_url_raises(self) -> None:
        data = {
            "providers": {
                "bad": {"base_url": "not-a-url", "api_key_env": "X"},
            }
        }
        with pytest.raises(CatalogError):
            _parse_catalog(data, source="builtin")

    def test_missing_api_key_env_raises(self) -> None:
        data = {
            "providers": {
                "bad": {"base_url": "https://api.test.com", "api_key_env": ""},
            }
        }
        with pytest.raises(CatalogError):
            _parse_catalog(data, source="builtin")

    def test_top_level_must_be_mapping(self, tmp_path: Path) -> None:
        from engine.providers.catalog import _load_yaml

        bad = tmp_path / "bad.yaml"
        bad.write_text("- just\n- a\n- list\n")
        with pytest.raises(CatalogError):
            _load_yaml(bad)

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        from engine.providers.catalog import _load_yaml

        bad = tmp_path / "bad.yaml"
        bad.write_text("key: : :\nbroken: [unclosed")
        with pytest.raises(CatalogError):
            _load_yaml(bad)


# ─── Built-in catalog ───────────────────────────────────────────────────────


class TestBuiltinCatalog:
    def setup_method(self) -> None:
        reset_cache()

    def teardown_method(self) -> None:
        reset_cache()

    def test_loads_nine_presets(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            catalog = load_catalog()
        expected = {
            "nvidia",
            "openrouter",
            "moonshot",
            "groq",
            "together",
            "fireworks",
            "deepinfra",
            "cerebras",
            "hyperbolic",
        }
        assert expected.issubset(set(catalog.providers))

    def test_nvidia_entry_has_expected_fields(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            entry = get_entry("nvidia")
        assert entry is not None
        assert entry.api_key_env == "NVIDIA_API_KEY"
        assert "integrate.api.nvidia.com" in str(entry.base_url)
        assert entry.source == "builtin"

    def test_openrouter_has_default_headers(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            entry = get_entry("openrouter")
        assert entry is not None
        assert entry.default_headers.get("HTTP-Referer") == "https://agentbreeder.io"
        assert entry.default_headers.get("X-Title") == "AgentBreeder"

    def test_groq_entry(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            entry = get_entry("groq")
        assert entry is not None
        assert entry.api_key_env == "GROQ_API_KEY"
        assert "groq.com" in str(entry.base_url)

    def test_unknown_provider_returns_none(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert get_entry("does-not-exist") is None


# ─── User-local merge ───────────────────────────────────────────────────────


class TestUserLocalMerge:
    def setup_method(self) -> None:
        reset_cache()

    def teardown_method(self) -> None:
        reset_cache()

    def test_user_local_overrides_builtin(self, tmp_path: Path) -> None:
        local = tmp_path / "providers.local.yaml"
        local.write_text(
            "version: 1\n"
            "providers:\n"
            "  nvidia:\n"
            "    type: openai_compatible\n"
            "    base_url: https://internal.nvidia.mirror/v1\n"
            "    api_key_env: INTERNAL_NVIDIA_KEY\n"
        )
        with patch("engine.providers.catalog.USER_LOCAL_PATH", local):
            reset_cache()
            entry = get_entry("nvidia")
        assert entry is not None
        assert "internal.nvidia.mirror" in str(entry.base_url)
        assert entry.api_key_env == "INTERNAL_NVIDIA_KEY"
        assert entry.source == "user-local"

    def test_user_local_adds_new_provider(self, tmp_path: Path) -> None:
        local = tmp_path / "providers.local.yaml"
        local.write_text(
            "version: 1\n"
            "providers:\n"
            "  my-vllm:\n"
            "    type: openai_compatible\n"
            "    base_url: https://vllm.internal/v1\n"
            "    api_key_env: VLLM_KEY\n"
        )
        with patch("engine.providers.catalog.USER_LOCAL_PATH", local):
            reset_cache()
            entries = list_entries()
        assert "my-vllm" in entries
        assert entries["my-vllm"].source == "user-local"
        assert entries["nvidia"].source == "builtin"

    def test_invalid_user_local_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        local = tmp_path / "providers.local.yaml"
        local.write_text("providers:\n  bad:\n    base_url: nope\n    api_key_env: X\n")
        with patch("engine.providers.catalog.USER_LOCAL_PATH", local):
            reset_cache()
            # built-in still loads; bad user-local entry doesn't take down the system
            assert "nvidia" in list_entries()


# ─── parse_model_ref ────────────────────────────────────────────────────────


class TestParseModelRef:
    def setup_method(self) -> None:
        reset_cache()

    def teardown_method(self) -> None:
        reset_cache()

    def test_valid_catalog_ref(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            result = parse_model_ref("nvidia/meta-llama-3.1-405b-instruct")
        assert result == ("nvidia", "meta-llama-3.1-405b-instruct")

    def test_no_slash_returns_none(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert parse_model_ref("gpt-4o") is None

    def test_unknown_provider_returns_none(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert parse_model_ref("acme/some-model") is None

    def test_empty_components(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert parse_model_ref("/foo") is None
            assert parse_model_ref("nvidia/") is None

    def test_model_id_can_contain_slashes(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            result = parse_model_ref("openrouter/anthropic/claude-sonnet-4")
        assert result == ("openrouter", "anthropic/claude-sonnet-4")


# ─── OpenAICompatibleProvider ───────────────────────────────────────────────


def _config(
    api_key: str | None = "test-key",
    base_url: str = "https://api.test.com/v1",
) -> ProviderConfig:
    return ProviderConfig(
        provider_type=ProviderType.openai,
        api_key=api_key,
        base_url=base_url,
        default_model="meta-llama-3.1-8b",
    )


def _models_response() -> dict[str, object]:
    return {
        "object": "list",
        "data": [
            {"id": "meta-llama-3.1-8b", "object": "model"},
            {"id": "mixtral-8x7b", "object": "model", "context_length": 32768},
        ],
    }


def _chat_response(content: str = "Hello!", model: str = "meta-llama-3.1-8b") -> dict:
    return {
        "id": "cmpl-1",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


class TestOpenAICompatibleProvider:
    def test_requires_base_url(self) -> None:
        config = ProviderConfig(provider_type=ProviderType.openai, api_key="x")
        with pytest.raises(ProviderError, match="base_url"):
            OpenAICompatibleProvider(
                config,
                provider_name="test",
                api_key_env="TEST_KEY",
            )

    def test_requires_api_key(self) -> None:
        config = ProviderConfig(
            provider_type=ProviderType.openai,
            base_url="https://api.test.com/v1",
        )
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(AuthenticationError, match="API key not found"):
                OpenAICompatibleProvider(
                    config,
                    provider_name="nvidia",
                    api_key_env="NVIDIA_API_KEY",
                )

    def test_reads_api_key_from_env(self) -> None:
        config = ProviderConfig(
            provider_type=ProviderType.openai,
            base_url="https://api.test.com/v1",
        )
        with patch.dict("os.environ", {"NVIDIA_API_KEY": "nv-test"}):
            provider = OpenAICompatibleProvider(
                config,
                provider_name="nvidia",
                api_key_env="NVIDIA_API_KEY",
            )
        assert provider._api_key == "nv-test"
        assert provider.name == "nvidia"

    def test_default_headers_are_merged(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="openrouter",
            api_key_env="OPENROUTER_API_KEY",
            default_headers={
                "HTTP-Referer": "https://agentbreeder.io",
                "X-Title": "AgentBreeder",
            },
        )
        sent_headers = provider._client.headers
        assert sent_headers.get("HTTP-Referer") == "https://agentbreeder.io"
        assert sent_headers.get("X-Title") == "AgentBreeder"
        assert sent_headers.get("Authorization") == "Bearer test-key"

    def test_strips_trailing_slash_from_base_url(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(base_url="https://api.test.com/v1/"),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        assert provider._base_url == "https://api.test.com/v1"

    @pytest.mark.asyncio
    async def test_list_models(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="nvidia",
            api_key_env="NVIDIA_API_KEY",
        )
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=httpx.Response(200, json=_models_response()))
        models = await provider.list_models()
        assert len(models) == 2
        assert {m.id for m in models} == {"meta-llama-3.1-8b", "mixtral-8x7b"}
        assert all(m.provider == "nvidia" for m in models)
        # context_length surfaces as context_window when present
        mixtral = next(m for m in models if m.id == "mixtral-8x7b")
        assert mixtral.context_window == 32768

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=httpx.Response(200, json=_models_response()))
        assert bool(await provider.health_check()) is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(return_value=httpx.Response(401, text="unauthorized"))
        assert bool(await provider.health_check()) is False

    @pytest.mark.asyncio
    async def test_generate(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="groq",
            api_key_env="GROQ_API_KEY",
        )
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=httpx.Response(200, json=_chat_response()))
        result = await provider.generate(messages=[{"role": "user", "content": "Hi"}])
        assert result.content == "Hello!"
        assert result.provider == "groq"
        assert result.usage.total_tokens == 8

    @pytest.mark.asyncio
    async def test_generate_401_raises_auth_error(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=httpx.Response(401, text="bad key"))
        with pytest.raises(AuthenticationError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_generate_404_raises_model_not_found(self) -> None:
        from engine.providers.base import ModelNotFoundError

        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=httpx.Response(404, text="no such model"))
        with pytest.raises(ModelNotFoundError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_generate_429_raises_rate_limit(self) -> None:
        from engine.providers.base import RateLimitError

        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=httpx.Response(429, text="too many"))
        with pytest.raises(RateLimitError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_generate_500_raises_provider_error(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=httpx.Response(500, text="server error"))
        with pytest.raises(ProviderError):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_request_timeout_wrapped(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        with pytest.raises(ProviderError, match="timed out"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_request_connect_error_wrapped(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(side_effect=httpx.ConnectError("dns failure"))
        with pytest.raises(ProviderError, match="Failed to connect"):
            await provider.generate(messages=[{"role": "user", "content": "Hi"}])

    @pytest.mark.asyncio
    async def test_generate_includes_temperature_max_tokens_tools(self) -> None:
        from engine.providers.models import ToolDefinition, ToolFunction

        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(return_value=httpx.Response(200, json=_chat_response()))

        await provider.generate(
            messages=[{"role": "user", "content": "x"}],
            temperature=0.3,
            max_tokens=99,
            tools=[
                ToolDefinition(
                    function=ToolFunction(
                        name="search",
                        description="Search the web",
                        parameters={"type": "object"},
                    )
                )
            ],
        )
        sent_payload = provider._client.post.call_args.kwargs["json"]
        assert sent_payload["temperature"] == 0.3
        assert sent_payload["max_tokens"] == 99
        assert sent_payload["tools"][0]["function"]["name"] == "search"

    @pytest.mark.asyncio
    async def test_generate_returns_tool_calls(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="groq",
            api_key_env="GROQ_API_KEY",
        )
        provider._client = AsyncMock()
        provider._client.post = AsyncMock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "x",
                    "model": "y",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "f",
                                            "arguments": '{"x": 1}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        )
        result = await provider.generate(messages=[{"role": "user", "content": "x"}])
        assert result.finish_reason == "tool_calls"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].function_name == "f"

    @pytest.mark.asyncio
    async def test_list_models_skips_entries_without_id(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.get = AsyncMock(
            return_value=httpx.Response(
                200,
                json={"data": [{"id": "ok"}, {}, {"id": ""}]},
            )
        )
        models = await provider.list_models()
        assert [m.id for m in models] == ["ok"]

    @pytest.mark.asyncio
    async def test_close_releases_http_client(self) -> None:
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )
        provider._client = AsyncMock()
        provider._client.aclose = AsyncMock()
        await provider.close()
        provider._client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_stream_collects_content(self) -> None:
        """`stream=True` on generate() collects chunks into one GenerateResult."""
        provider = OpenAICompatibleProvider(
            _config(),
            provider_name="t",
            api_key_env="TEST_KEY",
        )

        async def _fake_stream(
            messages, model=None, temperature=None, max_tokens=None, tools=None
        ):
            from engine.providers.models import StreamChunk

            yield StreamChunk(content="Hello ", model="m")
            yield StreamChunk(content="world", model="m")
            yield StreamChunk(finish_reason="stop", model="m")

        provider.generate_stream = _fake_stream  # type: ignore[method-assign]
        result = await provider.generate(messages=[{"role": "user", "content": "x"}], stream=True)
        assert result.content == "Hello world"
        assert result.finish_reason == "stop"
        assert result.provider == "t"


# ─── from_catalog factory ───────────────────────────────────────────────────


class TestFromCatalog:
    def setup_method(self) -> None:
        reset_cache()

    def teardown_method(self) -> None:
        reset_cache()

    def test_unknown_catalog_name_raises(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            with pytest.raises(KeyError):
                from_catalog("does-not-exist")

    def test_builds_nvidia_provider(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            with patch.dict("os.environ", {"NVIDIA_API_KEY": "nv-test"}):
                provider = from_catalog("nvidia", default_model="meta-llama-3.1-405b")
        assert provider.name == "nvidia"
        assert "integrate.api.nvidia.com" in provider._base_url

    def test_builds_openrouter_with_headers(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            with patch.dict("os.environ", {"OPENROUTER_API_KEY": "or-test"}):
                provider = from_catalog("openrouter")
        assert provider._client.headers.get("HTTP-Referer") == "https://agentbreeder.io"
        assert provider._client.headers.get("X-Title") == "AgentBreeder"


# ─── resolve_model_ref through registry ────────────────────────────────────


class TestRegistryResolveModelRef:
    def setup_method(self) -> None:
        reset_cache()

    def teardown_method(self) -> None:
        reset_cache()

    def test_resolves_catalog_ref(self) -> None:
        from engine.providers.registry import resolve_model_ref

        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            with patch.dict("os.environ", {"NVIDIA_API_KEY": "nv-test"}):
                provider = resolve_model_ref("nvidia/meta-llama-3.1-405b-instruct")
        assert provider is not None
        assert provider.name == "nvidia"
        assert provider.config.default_model == "meta-llama-3.1-405b-instruct"

    def test_returns_none_for_non_catalog(self) -> None:
        from engine.providers.registry import resolve_model_ref

        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert resolve_model_ref("gpt-4o") is None
            assert resolve_model_ref("unknown/model") is None


# ─── write_user_local round-trip ───────────────────────────────────────────


class TestWriteUserLocal:
    def setup_method(self) -> None:
        reset_cache()

    def teardown_method(self) -> None:
        reset_cache()

    def test_write_and_reload(self, tmp_path: Path) -> None:
        from engine.providers.catalog import write_user_local

        local = tmp_path / "providers.local.yaml"
        with patch("engine.providers.catalog.USER_LOCAL_PATH", local):
            reset_cache()
            cat = Catalog(
                providers={
                    "my-vllm": CatalogEntry(
                        type="openai_compatible",
                        base_url="https://vllm.internal/v1",  # type: ignore[arg-type]
                        api_key_env="VLLM_KEY",
                        source="user-local",
                    )
                }
            )
            path = write_user_local(cat)
            assert path.exists()
            assert "my-vllm" in path.read_text()

            reset_cache()
            assert get_entry("my-vllm") is not None


# ─── Gateway type entries (Track H / #164) ─────────────────────────────────


class TestGatewayCatalogEntries:
    """Built-in gateway entries — LiteLLM + OpenRouter — exist with type=gateway."""

    def setup_method(self) -> None:
        reset_cache()

    def teardown_method(self) -> None:
        reset_cache()

    def test_openrouter_is_gateway_type(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            entry = get_entry("openrouter")
        assert entry is not None
        assert entry.type == "gateway"

    def test_litellm_preset_present(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            entry = get_entry("litellm")
        assert entry is not None
        assert entry.type == "gateway"
        assert entry.api_key_env == "LITELLM_MASTER_KEY"
        assert "4000" in str(entry.base_url)

    def test_litellm_notable_models_seeded(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            entry = get_entry("litellm")
        assert entry is not None
        # Cross-check that compose-shipped LiteLLM models are advertised so the
        # quickstart `model.primary: litellm/...` examples actually work.
        assert "openai/gpt-4o" in entry.notable_models
        assert any("claude" in m for m in entry.notable_models)

    def test_direct_provider_keeps_openai_compatible_type(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            for name in ("nvidia", "groq", "moonshot", "fireworks", "cerebras"):
                entry = get_entry(name)
                assert entry is not None, f"missing direct provider: {name}"
                assert entry.type == "openai_compatible", (
                    f"direct provider {name} regressed to non-direct type"
                )


class TestParseGatewayRef:
    def setup_method(self) -> None:
        reset_cache()

    def teardown_method(self) -> None:
        reset_cache()

    def test_openrouter_three_segment_ref(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            ref = parse_gateway_ref("openrouter/moonshotai/kimi-k2")
        assert isinstance(ref, GatewayModelRef)
        assert ref.gateway == "openrouter"
        assert ref.upstream_provider == "moonshotai"
        assert ref.model == "kimi-k2"
        assert ref.upstream_model == "moonshotai/kimi-k2"

    def test_litellm_three_segment_ref(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            ref = parse_gateway_ref("litellm/anthropic/claude-sonnet-4")
        assert ref is not None
        assert ref.gateway == "litellm"
        assert ref.upstream_model == "anthropic/claude-sonnet-4"

    def test_two_segment_returns_none(self) -> None:
        # Two-segment refs are direct refs — gateway parser returns None and
        # callers fall through to parse_model_ref.
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert parse_gateway_ref("openrouter/auto") is None
            assert parse_gateway_ref("nvidia/llama-3.1-405b") is None

    def test_first_segment_must_be_gateway_type(self) -> None:
        # nvidia exists in the catalog but is type=openai_compatible — the
        # 3-segment form must NOT match it (otherwise direct refs whose model
        # contains a slash, e.g. nvidia/meta/llama-3.1, would be miscategorised).
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert parse_gateway_ref("nvidia/meta/llama-3.1-405b") is None

    def test_unknown_gateway_returns_none(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert parse_gateway_ref("acme-gw/foo/bar") is None

    def test_no_slash_returns_none(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert parse_gateway_ref("gpt-4o") is None

    def test_empty_segments_return_none(self) -> None:
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert parse_gateway_ref("openrouter//kimi-k2") is None
            assert parse_gateway_ref("/openrouter/kimi-k2") is None
            assert parse_gateway_ref("openrouter/moonshot/") is None

    def test_model_id_can_contain_extra_slashes(self) -> None:
        # `partition('/')`-style: only the first 2 splits are consumed; extra
        # slashes in the model id stay attached. Useful for OpenRouter refs
        # like openrouter/openai/gpt-4o-2024-08-06 vs nested model paths.
        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            ref = parse_gateway_ref("openrouter/openai/gpt-4o/2024-08-06")
        assert ref is not None
        assert ref.gateway == "openrouter"
        assert ref.upstream_provider == "openai"
        assert ref.model == "gpt-4o/2024-08-06"
        assert ref.upstream_model == "openai/gpt-4o/2024-08-06"

    def test_user_local_can_register_custom_gateway(self, tmp_path: Path) -> None:
        # User-local override can declare type: gateway — it must work end-to-end.
        local = tmp_path / "providers.local.yaml"
        local.write_text(
            "version: 1\n"
            "providers:\n"
            "  myproxy:\n"
            "    type: gateway\n"
            "    base_url: https://gw.internal/v1\n"
            "    api_key_env: MYPROXY_KEY\n"
        )
        with patch("engine.providers.catalog.USER_LOCAL_PATH", local):
            reset_cache()
            ref = parse_gateway_ref("myproxy/anthropic/claude-sonnet-4")
        assert ref is not None
        assert ref.gateway == "myproxy"
        assert ref.upstream_model == "anthropic/claude-sonnet-4"


class TestResolveGatewayModelRef:
    """Track H — resolve_model_ref dispatches 3-segment refs to gateway entries."""

    def setup_method(self) -> None:
        reset_cache()

    def teardown_method(self) -> None:
        reset_cache()

    def test_three_segment_resolves_to_gateway_provider(self) -> None:
        from engine.providers.registry import resolve_model_ref

        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}):
                provider = resolve_model_ref("openrouter/moonshotai/kimi-k2")
        assert provider is not None
        assert provider.name == "openrouter"
        # The wire model joins upstream + model so OpenRouter accepts it as-is.
        assert provider.config.default_model == "moonshotai/kimi-k2"

    def test_three_segment_litellm_resolves(self) -> None:
        from engine.providers.registry import resolve_model_ref

        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            with patch.dict("os.environ", {"LITELLM_MASTER_KEY": "sk-litellm"}):
                provider = resolve_model_ref("litellm/anthropic/claude-sonnet-4")
        assert provider is not None
        assert provider.name == "litellm"
        assert provider.config.default_model == "anthropic/claude-sonnet-4"

    def test_two_segment_direct_still_works(self) -> None:
        # Backwards-compat: existing nvidia/groq/etc. refs MUST keep resolving.
        from engine.providers.registry import resolve_model_ref

        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            with patch.dict("os.environ", {"NVIDIA_API_KEY": "nv-test"}):
                provider = resolve_model_ref("nvidia/meta-llama-3.1-405b-instruct")
        assert provider is not None
        assert provider.name == "nvidia"
        assert provider.config.default_model == "meta-llama-3.1-405b-instruct"

    def test_legacy_two_segment_openrouter_still_routes(self) -> None:
        # `openrouter/auto` was supported pre-Track-H. Even though openrouter
        # is now type=gateway, the 2-segment form falls through to
        # parse_model_ref and resolves the same way. This is a hard
        # backwards-compat guarantee.
        from engine.providers.registry import resolve_model_ref

        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            with patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}):
                provider = resolve_model_ref("openrouter/auto")
        assert provider is not None
        assert provider.name == "openrouter"
        assert provider.config.default_model == "auto"

    def test_unknown_three_segment_returns_none(self) -> None:
        from engine.providers.registry import resolve_model_ref

        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            assert resolve_model_ref("acme-gw/anthropic/claude") is None

    def test_gateway_base_url_env_override(self) -> None:
        # `LITELLM_BASE_URL` overrides the catalog default — lets the same
        # entry serve docker-compose, self-hosted, and SaaS deployments.
        from engine.providers.registry import resolve_model_ref

        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            with patch.dict(
                "os.environ",
                {
                    "LITELLM_MASTER_KEY": "sk-test",
                    "LITELLM_BASE_URL": "https://litellm.platform.example.com/v1",
                },
            ):
                provider = resolve_model_ref("litellm/openai/gpt-4o")
        assert provider is not None
        assert provider.config.base_url == "https://litellm.platform.example.com/v1"

    def test_direct_provider_does_not_honour_base_url_override(self) -> None:
        # The override env var only applies to gateways. A plain direct
        # provider keeps its catalog base_url even if a same-named env exists.
        from engine.providers.registry import resolve_model_ref

        with patch("engine.providers.catalog.USER_LOCAL_PATH", Path("/nonexistent/path.yaml")):
            reset_cache()
            with patch.dict(
                "os.environ",
                {
                    "NVIDIA_API_KEY": "nv-test",
                    "NVIDIA_BASE_URL": "https://malicious.example.com/v1",
                },
            ):
                provider = resolve_model_ref("nvidia/meta-llama-3.1-405b-instruct")
        assert provider is not None
        assert "nvidia.com" in (provider.config.base_url or "")
