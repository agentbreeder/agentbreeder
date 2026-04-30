"""Unit tests for ``api.services.gateway_logs_service``.

Covers raw LiteLLM /spend/logs row normalization and the unreachable-proxy
error path. The HTTP boundary is mocked so these tests run offline.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from api.services.gateway_logs_service import (
    GatewayLogsUnavailableError,
    _infer_tier,
    _normalize_entry,
    _normalize_provider,
    fetch_spend_logs,
)


class TestNormalizeProvider:
    def test_uses_explicit_provider(self) -> None:
        assert _normalize_provider("gpt-4o", "OpenAI") == "openai"

    def test_falls_back_to_model_prefix(self) -> None:
        assert _normalize_provider("anthropic/claude-sonnet-4-6", "") == "anthropic"

    def test_unknown(self) -> None:
        assert _normalize_provider("", "") == "unknown"


class TestInferTier:
    def test_litellm_provider(self) -> None:
        assert _infer_tier("gpt-4o", "litellm") == "litellm"
        assert _infer_tier("gpt-4o", "openai_proxy") == "litellm"

    def test_provider_prefix_in_model(self) -> None:
        assert _infer_tier("anthropic/claude", "") == "litellm"

    def test_direct(self) -> None:
        assert _infer_tier("gpt-4o", "openai") == "direct"

    def test_empty(self) -> None:
        assert _infer_tier("", "") == "direct"


class TestNormalizeEntry:
    def test_full_row(self) -> None:
        row = {
            "request_id": "req-1",
            "startTime": "2026-04-29T10:00:00+00:00",
            "endTime": "2026-04-29T10:00:01+00:00",
            "model": "gpt-4o",
            "custom_llm_provider": "openai",
            "prompt_tokens": 500,
            "completion_tokens": 100,
            "spend": 0.0035,
            "user": "alice",
            "metadata": {"agent_name": "support-agent"},
            "status": "success",
        }
        out = _normalize_entry(row)
        assert out["id"] == "req-1"
        assert out["model"] == "gpt-4o"
        assert out["provider"] == "openai"
        assert out["gateway_tier"] == "direct"
        assert out["input_tokens"] == 500
        assert out["output_tokens"] == 100
        assert out["cost_usd"] == 0.0035
        assert out["agent"] == "support-agent"
        assert out["status"] == "success"
        # Latency derived from start/end
        assert out["latency_ms"] == 1000

    def test_string_metadata_is_parsed(self) -> None:
        row = {
            "request_id": "req-2",
            "model": "anthropic/claude-sonnet-4-6",
            "metadata": '{"agent_name": "review-agent"}',
        }
        out = _normalize_entry(row)
        assert out["agent"] == "review-agent"
        assert out["gateway_tier"] == "litellm"

    def test_handles_missing_fields(self) -> None:
        out = _normalize_entry({})
        assert out["model"] == ""
        assert out["provider"] == "unknown"
        assert out["input_tokens"] == 0
        assert out["output_tokens"] == 0
        assert out["cost_usd"] == 0.0
        assert out["status"] == "success"

    def test_explicit_api_response_ms(self) -> None:
        out = _normalize_entry(
            {
                "request_id": "x",
                "model": "gpt-4o",
                "api_response_ms": 750,
            }
        )
        assert out["latency_ms"] == 750


class TestFetchSpendLogs:
    @pytest.mark.asyncio
    async def test_returns_normalized_rows(self) -> None:
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "data": [
                {
                    "request_id": "r1",
                    "model": "gpt-4o",
                    "custom_llm_provider": "openai",
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "spend": 0.001,
                }
            ]
        }

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rows = await fetch_spend_logs(limit=10)

        assert len(rows) == 1
        assert rows[0]["model"] == "gpt-4o"
        assert rows[0]["provider"] == "openai"
        assert rows[0]["input_tokens"] == 100

    @pytest.mark.asyncio
    async def test_raises_when_proxy_unreachable(self) -> None:
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(GatewayLogsUnavailableError):
                await fetch_spend_logs()

    @pytest.mark.asyncio
    async def test_raises_on_non_200(self) -> None:
        fake_response = MagicMock()
        fake_response.status_code = 401
        fake_response.text = "unauthorized"

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(GatewayLogsUnavailableError):
                await fetch_spend_logs()

    @pytest.mark.asyncio
    async def test_handles_bare_list_payload(self) -> None:
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = [
            {"request_id": "r1", "model": "gpt-4o-mini", "custom_llm_provider": "openai"}
        ]

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rows = await fetch_spend_logs()

        assert len(rows) == 1
        assert rows[0]["model"] == "gpt-4o-mini"
