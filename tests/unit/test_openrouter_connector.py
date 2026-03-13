"""Tests for OpenRouter connector."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from connectors.openrouter.connector import OpenRouterConnector, _extract_provider

# ── Helpers ─────────────────────────────────────────────────────────────────


def _mock_response(status_code: int = 200, json_data=None, raise_for_status=None):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    if raise_for_status:
        resp.raise_for_status.side_effect = raise_for_status
    else:
        resp.raise_for_status.return_value = None
    return resp


def _mock_client(get_return=None, get_side_effect=None):
    """Create an async context manager mock for httpx.AsyncClient."""
    client = AsyncMock()
    if get_side_effect:
        client.get = AsyncMock(side_effect=get_side_effect)
    else:
        client.get = AsyncMock(return_value=get_return)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _models_data() -> dict:
    return {
        "data": [
            {
                "id": "anthropic/claude-3.5-sonnet",
                "name": "Claude 3.5 Sonnet",
                "context_length": 200000,
                "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            },
            {
                "id": "openai/gpt-4o",
                "name": "GPT-4o",
                "context_length": 128000,
                "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
            },
            {
                "id": "google/gemini-2.5-pro",
                "name": "Gemini 2.5 Pro",
                "context_length": 1000000,
                "pricing": {"prompt": "0.00000125", "completion": "0.000005"},
            },
        ]
    }


# ── Tests ────────────────────────────────────────────────────────────────────


class TestOpenRouterConnectorInit:
    def test_name_property(self) -> None:
        connector = OpenRouterConnector()
        assert connector.name == "openrouter"

    def test_init_from_env(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-env-key"}):
            connector = OpenRouterConnector()
            assert connector._api_key == "sk-or-env-key"

    def test_init_with_explicit_key(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-explicit")
        assert connector._api_key == "sk-or-explicit"

    def test_default_base_url(self) -> None:
        connector = OpenRouterConnector()
        assert "openrouter.ai" in connector._base_url

    def test_custom_base_url(self) -> None:
        connector = OpenRouterConnector(base_url="https://custom.proxy.com/")
        assert connector._base_url == "https://custom.proxy.com"

    def test_env_base_url(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_BASE_URL": "https://env.proxy.com/api/v1"}):
            connector = OpenRouterConnector()
            assert connector._base_url == "https://env.proxy.com/api/v1"


class TestOpenRouterConnectorAvailability:
    @pytest.mark.asyncio
    async def test_is_available_true(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-test")
        client = _mock_client(get_return=_mock_response(200, json_data={"data": []}))
        with patch("connectors.openrouter.connector.httpx.AsyncClient", return_value=client):
            assert await connector.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_false_non_200(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-test")
        client = _mock_client(get_return=_mock_response(503))
        with patch("connectors.openrouter.connector.httpx.AsyncClient", return_value=client):
            assert await connector.is_available() is False

    @pytest.mark.asyncio
    async def test_is_available_false_network_error(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-test")
        client = _mock_client(get_side_effect=httpx.ConnectError("refused"))
        with patch("connectors.openrouter.connector.httpx.AsyncClient", return_value=client):
            assert await connector.is_available() is False


class TestOpenRouterConnectorScan:
    @pytest.mark.asyncio
    async def test_scan_success(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-test")
        resp = _mock_response(200, json_data=_models_data())
        client = _mock_client(get_return=resp)
        with patch("connectors.openrouter.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()

        assert len(models) == 3
        assert models[0]["name"] == "anthropic/claude-3.5-sonnet"
        assert models[0]["provider"] == "anthropic"
        assert models[0]["source"] == "openrouter"
        assert models[1]["provider"] == "openai"
        assert models[2]["provider"] == "google"

    @pytest.mark.asyncio
    async def test_scan_includes_config(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-test")
        resp = _mock_response(200, json_data=_models_data())
        client = _mock_client(get_return=resp)
        with patch("connectors.openrouter.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()

        config = models[0]["config"]
        assert "context_length" in config
        assert "pricing" in config
        assert config["context_length"] == 200000

    @pytest.mark.asyncio
    async def test_scan_empty(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-test")
        resp = _mock_response(200, json_data={"data": []})
        client = _mock_client(get_return=resp)
        with patch("connectors.openrouter.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()
        assert models == []

    @pytest.mark.asyncio
    async def test_scan_network_error(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-test")
        client = _mock_client(get_side_effect=httpx.ConnectError("refused"))
        with patch("connectors.openrouter.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()
        assert models == []

    @pytest.mark.asyncio
    async def test_scan_invalid_json(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-test")
        resp = _mock_response(200)
        resp.json.side_effect = ValueError("bad json")
        client = _mock_client(get_return=resp)
        with patch("connectors.openrouter.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()
        assert models == []

    @pytest.mark.asyncio
    async def test_scan_skips_empty_ids(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-test")
        resp = _mock_response(
            200,
            json_data={
                "data": [
                    {"id": "", "name": "Empty"},
                    {
                        "id": "openai/gpt-4o",
                        "name": "GPT-4o",
                        "context_length": 128000,
                        "pricing": {},
                    },
                ]
            },
        )
        client = _mock_client(get_return=resp)
        with patch("connectors.openrouter.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()
        assert len(models) == 1
        assert models[0]["name"] == "openai/gpt-4o"

    @pytest.mark.asyncio
    async def test_scan_uses_authorization_header(self) -> None:
        connector = OpenRouterConnector(api_key="sk-or-my-key")
        resp = _mock_response(200, json_data={"data": []})
        client = _mock_client(get_return=resp)
        with patch("connectors.openrouter.connector.httpx.AsyncClient", return_value=client):
            await connector.scan()

        call_kwargs = client.get.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer sk-or-my-key"


class TestExtractProvider:
    def test_extract_provider_slash_format(self) -> None:
        assert _extract_provider("anthropic/claude-3.5-sonnet") == "anthropic"

    def test_extract_provider_openai_slash(self) -> None:
        assert _extract_provider("openai/gpt-4o") == "openai"

    def test_extract_provider_google_slash(self) -> None:
        assert _extract_provider("google/gemini-2.5-pro") == "google"

    def test_extract_provider_gpt_prefix(self) -> None:
        assert _extract_provider("gpt-4o") == "openai"

    def test_extract_provider_claude_prefix(self) -> None:
        assert _extract_provider("claude-3-haiku") == "anthropic"

    def test_extract_provider_gemini_prefix(self) -> None:
        assert _extract_provider("gemini-2.5-pro") == "google"

    def test_extract_provider_llama_prefix(self) -> None:
        assert _extract_provider("llama-3.3-70b") == "meta"

    def test_extract_provider_mistral_prefix(self) -> None:
        assert _extract_provider("mistral-large-2") == "mistral"

    def test_extract_provider_unknown(self) -> None:
        assert _extract_provider("custom-mystery-model") == "unknown"

    def test_extract_provider_o3(self) -> None:
        assert _extract_provider("o3-mini") == "openai"
