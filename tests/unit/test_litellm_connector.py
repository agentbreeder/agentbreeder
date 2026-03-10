"""Tests for LiteLLM Gateway connector."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from connectors.litellm.connector import LiteLLMConnector, _extract_provider


def _mock_response(status_code: int = 200, json_data=None, raise_for_status=None):
    """Create a mock httpx.Response (sync .json(), sync .raise_for_status())."""
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


class TestLiteLLMConnectorAvailability:
    @pytest.mark.asyncio
    async def test_is_available_healthy(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        client = _mock_client(get_return=_mock_response(200))
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            assert await connector.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_unreachable(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        client = _mock_client(get_side_effect=httpx.ConnectError("refused"))
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            assert await connector.is_available() is False

    @pytest.mark.asyncio
    async def test_is_available_non_200(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        client = _mock_client(get_return=_mock_response(503))
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            assert await connector.is_available() is False


class TestLiteLLMConnectorScan:
    @pytest.mark.asyncio
    async def test_scan_models(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        resp = _mock_response(200, json_data={
            "data": [
                {"id": "openai/gpt-4o", "object": "model", "owned_by": "openai"},
                {"id": "claude-sonnet-4", "object": "model", "owned_by": "anthropic"},
                {"id": "gemini-pro", "object": "model", "owned_by": "google"},
            ]
        })
        client = _mock_client(get_return=resp)
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()

        assert len(models) == 3
        assert models[0]["name"] == "openai/gpt-4o"
        assert models[0]["provider"] == "openai"
        assert models[0]["source"] == "litellm"
        assert models[1]["provider"] == "anthropic"
        assert models[2]["provider"] == "google"

    @pytest.mark.asyncio
    async def test_scan_empty_response(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        resp = _mock_response(200, json_data={"data": []})
        client = _mock_client(get_return=resp)
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()
        assert models == []

    @pytest.mark.asyncio
    async def test_scan_skips_empty_ids(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        resp = _mock_response(200, json_data={"data": [{"id": ""}, {"id": "gpt-4o"}]})
        client = _mock_client(get_return=resp)
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()
        assert len(models) == 1
        assert models[0]["name"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_scan_http_error(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        client = _mock_client(get_side_effect=httpx.ConnectError("refused"))
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()
        assert models == []

    @pytest.mark.asyncio
    async def test_scan_invalid_json(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        resp = _mock_response(200)
        resp.raise_for_status.return_value = None
        resp.json.side_effect = ValueError("bad json")
        client = _mock_client(get_return=resp)
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            models = await connector.scan()
        assert models == []


class TestLiteLLMConnectorModelInfo:
    @pytest.mark.asyncio
    async def test_get_model_info_success(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        resp = _mock_response(200, json_data={"model": "gpt-4o", "max_tokens": 4096})
        client = _mock_client(get_return=resp)
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            info = await connector.get_model_info("gpt-4o")
        assert info == {"model": "gpt-4o", "max_tokens": 4096}

    @pytest.mark.asyncio
    async def test_get_model_info_not_found(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        resp = _mock_response(404)
        client = _mock_client(get_return=resp)
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            info = await connector.get_model_info("nonexistent")
        assert info is None

    @pytest.mark.asyncio
    async def test_get_model_info_error(self) -> None:
        connector = LiteLLMConnector(base_url="http://localhost:4000")
        client = _mock_client(get_side_effect=httpx.ConnectError("refused"))
        with patch("connectors.litellm.connector.httpx.AsyncClient", return_value=client):
            info = await connector.get_model_info("gpt-4o")
        assert info is None


class TestLiteLLMConnectorConfig:
    def test_name(self) -> None:
        assert LiteLLMConnector().name == "litellm"

    def test_custom_base_url(self) -> None:
        c = LiteLLMConnector(base_url="http://custom:5000/")
        assert c._base_url == "http://custom:5000"

    def test_api_key_in_headers(self) -> None:
        c = LiteLLMConnector(api_key="sk-test-123")
        headers = c._headers()
        assert headers["Authorization"] == "Bearer sk-test-123"

    def test_no_api_key(self) -> None:
        c = LiteLLMConnector()
        headers = c._headers()
        assert "Authorization" not in headers

    def test_env_base_url(self) -> None:
        import os
        with patch.dict(os.environ, {"LITELLM_BASE_URL": "http://env:4000"}):
            c = LiteLLMConnector()
            assert c._base_url == "http://env:4000"


class TestExtractProvider:
    def test_slash_format(self) -> None:
        assert _extract_provider("openai/gpt-4o") == "openai"

    def test_gpt(self) -> None:
        assert _extract_provider("gpt-4o") == "openai"

    def test_claude(self) -> None:
        assert _extract_provider("claude-sonnet-4") == "anthropic"

    def test_gemini(self) -> None:
        assert _extract_provider("gemini-pro") == "google"

    def test_llama(self) -> None:
        assert _extract_provider("llama-3-70b") == "meta"

    def test_mistral(self) -> None:
        assert _extract_provider("mistral-large") == "mistral"

    def test_unknown(self) -> None:
        assert _extract_provider("custom-model") == "unknown"

    def test_o1(self) -> None:
        assert _extract_provider("o1-preview") == "openai"
