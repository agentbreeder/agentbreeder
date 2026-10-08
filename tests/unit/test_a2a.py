"""Tests for engine/a2a/client — AgentInvocationClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# ---------------------------------------------------------------------------
# Client tests
# ---------------------------------------------------------------------------


class TestAgentInvocationClient:
    """Test the async HTTP invocation client."""

    @pytest.mark.asyncio
    async def test_invoke_success(self):
        from engine.a2a.client import AgentInvocationClient

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"output": "hello", "tokens": 50}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        result = await client.invoke("http://agent:8000", "test message")
        assert result.status == "success"
        assert result.output == "hello"
        assert result.tokens == 50
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_invoke_http_error(self):
        from engine.a2a.client import AgentInvocationClient

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        result = await client.invoke("http://agent:8000", "test")
        assert result.status == "error"
        assert "HTTP 500" in result.error

    @pytest.mark.asyncio
    async def test_invoke_timeout(self):
        from engine.a2a.client import AgentInvocationClient

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client.is_closed = False

        client = AgentInvocationClient(timeout=1.0)
        client._client = mock_client

        result = await client.invoke("http://agent:8000", "test")
        assert result.status == "error"
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_invoke_connection_error(self):
        from engine.a2a.client import AgentInvocationClient

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        result = await client.invoke("http://agent:8000", "test")
        assert result.status == "error"
        assert "Connection failed" in result.error

    @pytest.mark.asyncio
    async def test_close(self):
        from engine.a2a.client import AgentInvocationClient

        mock_client = AsyncMock()
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        await client.close()
        mock_client.aclose.assert_called_once()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_close_already_closed(self):
        from engine.a2a.client import AgentInvocationClient

        client = AgentInvocationClient()
        client._client = None
        await client.close()  # should not raise

    @pytest.mark.asyncio
    async def test_get_client_creates_new(self):
        from engine.a2a.client import AgentInvocationClient

        client = AgentInvocationClient(auth_token="test-token")
        http_client = await client._get_client()
        assert http_client is not None
        assert client._client is http_client
        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_reuses_existing(self):
        from engine.a2a.client import AgentInvocationClient

        mock_client = AsyncMock()
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        result = await client._get_client()
        assert result is mock_client

    @pytest.mark.asyncio
    async def test_invoke_url_construction(self):
        from engine.a2a.client import AgentInvocationClient

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"output": "ok"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        client = AgentInvocationClient()
        client._client = mock_client

        await client.invoke("http://agent:8000/", "test")
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://agent:8000/invoke"

    def test_invocation_result_defaults(self):
        from engine.a2a.client import AgentInvocationResult

        result = AgentInvocationResult(output="hello")
        assert result.tokens == 0
        assert result.latency_ms == 0
        assert result.status == "success"
        assert result.error is None
