"""Unit tests for ``engine.providers.discovery`` — Track G (#163).

Covers:
- ``DiscoveredModel`` shape + frozen semantics
- ``OpenAICompatibleDiscovery`` happy path / 401 / non-2xx / non-JSON / transport
- ``AnthropicDiscovery`` returns the curated list, sorted, with capability tags
- ``GoogleDiscovery`` parses the v1beta payload, surfaces capabilities
- ``get_discovery`` factory: anthropic / google / openai / catalog / fallback

All HTTP is mocked via ``httpx.MockTransport`` — no network calls.
"""

from __future__ import annotations

import json

import httpx
import pytest

from engine.providers.discovery import (
    ANTHROPIC_CURATED_MODELS,
    AnthropicDiscovery,
    DiscoveredModel,
    DiscoveryError,
    GoogleDiscovery,
    OpenAICompatibleDiscovery,
    get_discovery,
)

# ─── Helpers ───────────────────────────────────────────────────────────────


def _mock_client_factory(handler):
    """Patch ``httpx.AsyncClient`` so it uses the given handler."""

    transport = httpx.MockTransport(handler)

    class _Patched(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    return _Patched


# ─── DiscoveredModel ───────────────────────────────────────────────────────


class TestDiscoveredModel:
    def test_frozen(self) -> None:
        m = DiscoveredModel(id="x", name="X", provider="p")
        with pytest.raises(Exception):  # noqa: B017 — frozen dataclass raises FrozenInstanceError
            m.id = "y"  # type: ignore[misc]

    def test_defaults(self) -> None:
        m = DiscoveredModel(id="x", name="X", provider="p")
        assert m.context_window is None
        assert m.max_output_tokens is None
        assert m.capabilities == ()
        assert m.raw == {}


# ─── OpenAICompatibleDiscovery ─────────────────────────────────────────────


class TestOpenAICompatibleDiscovery:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/models")
            assert request.headers["Authorization"] == "Bearer sk-test"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-4o", "context_length": 128_000},
                        {"id": "gpt-4o-mini", "context_length": 64_000},
                    ]
                },
            )

        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
        d = OpenAICompatibleDiscovery("openai", "https://api.openai.com/v1", api_key="sk-test")
        out = await d.list_models()
        assert [m.id for m in out] == ["gpt-4o", "gpt-4o-mini"]
        assert out[0].context_window == 128_000
        assert "tools" in out[0].capabilities
        assert "streaming" in out[0].capabilities

    @pytest.mark.asyncio
    async def test_default_headers_passed(self, monkeypatch) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["referer"] = request.headers.get("HTTP-Referer", "")
            seen["title"] = request.headers.get("X-Title", "")
            return httpx.Response(200, json={"data": []})

        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
        d = OpenAICompatibleDiscovery(
            "openrouter",
            "https://openrouter.ai/api/v1",
            api_key="k",
            default_headers={"HTTP-Referer": "https://x", "X-Title": "Y"},
        )
        await d.list_models()
        assert seen == {"referer": "https://x", "title": "Y"}

    @pytest.mark.asyncio
    async def test_401_auth_error(self, monkeypatch) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="unauthorized")

        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
        d = OpenAICompatibleDiscovery("openai", "https://api.x/v1", api_key="bad")
        with pytest.raises(DiscoveryError, match="invalid api-key"):
            await d.list_models()

    @pytest.mark.asyncio
    async def test_500_error(self, monkeypatch) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
        d = OpenAICompatibleDiscovery("openai", "https://api.x/v1", api_key="k")
        with pytest.raises(DiscoveryError, match="HTTP 500"):
            await d.list_models()

    @pytest.mark.asyncio
    async def test_non_json(self, monkeypatch) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html/>")

        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
        d = OpenAICompatibleDiscovery("openai", "https://api.x/v1", api_key="k")
        with pytest.raises(DiscoveryError, match="non-JSON"):
            await d.list_models()

    @pytest.mark.asyncio
    async def test_transport_error(self, monkeypatch) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network down")

        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
        d = OpenAICompatibleDiscovery("openai", "https://api.x/v1", api_key="k")
        with pytest.raises(DiscoveryError, match="transport error"):
            await d.list_models()

    @pytest.mark.asyncio
    async def test_skips_entries_without_id(self, monkeypatch) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "ok"}, {"name": "no-id"}]})

        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
        d = OpenAICompatibleDiscovery("p", "https://x/v1", api_key="k")
        out = await d.list_models()
        assert [m.id for m in out] == ["ok"]

    def test_requires_base_url(self) -> None:
        with pytest.raises(DiscoveryError, match="base_url"):
            OpenAICompatibleDiscovery("p", "", api_key="k")


# ─── AnthropicDiscovery ────────────────────────────────────────────────────


class TestAnthropicDiscovery:
    @pytest.mark.asyncio
    async def test_returns_curated_list(self) -> None:
        d = AnthropicDiscovery()
        out = await d.list_models()
        assert len(out) == len(ANTHROPIC_CURATED_MODELS)
        ids = [m.id for m in out]
        assert ids == sorted(ids), "must be sorted by id"
        assert all(m.provider == "anthropic" for m in out)

    @pytest.mark.asyncio
    async def test_capabilities_surface(self) -> None:
        d = AnthropicDiscovery()
        out = await d.list_models()
        # Sonnet 4.7 should surface vision + thinking
        sonnet = next((m for m in out if m.id == "claude-sonnet-4-7"), None)
        assert sonnet is not None
        assert "vision" in sonnet.capabilities
        assert "thinking" in sonnet.capabilities

    @pytest.mark.asyncio
    async def test_custom_models_arg(self) -> None:
        d = AnthropicDiscovery(
            models=({"id": "x", "name": "X", "context_window": 1, "capabilities": ()},)
        )
        out = await d.list_models()
        assert len(out) == 1
        assert out[0].id == "x"


# ─── GoogleDiscovery ───────────────────────────────────────────────────────


class TestGoogleDiscovery:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params.get("key") == "test-key"
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-pro",
                            "displayName": "Gemini 2.5 Pro",
                            "inputTokenLimit": 2_000_000,
                            "outputTokenLimit": 8_192,
                            "supportedGenerationMethods": [
                                "generateContent",
                                "streamGenerateContent",
                            ],
                        }
                    ]
                },
            )

        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
        d = GoogleDiscovery(api_key="test-key")
        out = await d.list_models()
        assert len(out) == 1
        m = out[0]
        assert m.id == "gemini-2.5-pro"
        assert m.name == "Gemini 2.5 Pro"
        assert m.context_window == 2_000_000
        assert "streaming" in m.capabilities
        assert "generate" in m.capabilities

    @pytest.mark.asyncio
    async def test_invalid_key(self, monkeypatch) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="forbidden")

        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
        d = GoogleDiscovery(api_key="bad")
        with pytest.raises(DiscoveryError, match="invalid api-key"):
            await d.list_models()

    def test_requires_api_key(self) -> None:
        with pytest.raises(DiscoveryError, match="GOOGLE_API_KEY"):
            GoogleDiscovery(api_key=None)

    @pytest.mark.asyncio
    async def test_skips_entries_without_name(self, monkeypatch) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": [{"name": ""}, {"foo": "bar"}]})

        monkeypatch.setattr(httpx, "AsyncClient", _mock_client_factory(handler))
        d = GoogleDiscovery(api_key="k")
        out = await d.list_models()
        assert out == []


# ─── Factory ───────────────────────────────────────────────────────────────


class TestFactory:
    def test_anthropic(self) -> None:
        d = get_discovery("anthropic")
        assert isinstance(d, AnthropicDiscovery)

    def test_google(self, monkeypatch) -> None:
        monkeypatch.setenv("GOOGLE_API_KEY", "k")
        d = get_discovery("google")
        assert isinstance(d, GoogleDiscovery)

    def test_openai_default_base_url(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        d = get_discovery("openai")
        assert isinstance(d, OpenAICompatibleDiscovery)
        assert d.provider_name == "openai"

    def test_catalog_provider(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
        d = get_discovery("groq")
        assert isinstance(d, OpenAICompatibleDiscovery)
        assert d.provider_name == "groq"

    def test_unknown_raises(self) -> None:
        with pytest.raises(DiscoveryError, match="unknown provider"):
            get_discovery("not-a-real-provider")

    def test_explicit_base_url_fallback(self) -> None:
        d = get_discovery("byo", api_key="x", base_url="https://byo.example.com/v1")
        assert isinstance(d, OpenAICompatibleDiscovery)


# ─── Integration: payload round-trip with json module ──────────────────────


def test_curated_anthropic_models_serialise() -> None:
    """Defensive: every entry round-trips through json (no datetime/Path values)."""
    json.dumps(ANTHROPIC_CURATED_MODELS)
