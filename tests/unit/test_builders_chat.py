"""Unit tests for POST /api/v1/builders/chat.

The AnthropicProvider and workspace secrets backend are mocked throughout.
No real network calls, no real API key.

Key security assertions:
- The API key string must NEVER appear in the response body.
- The API key string must NEVER appear in any captured log record.
- Missing key → HTTP 400 (not 500), provider never constructed.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SECRET_NAME = "AGENTBREEDER_CLAUDE_BUILDER_KEY"
FAKE_API_KEY = "sk-ant-TEST-SUPER-SECRET-KEY-DO-NOT-LOG"

# A valid history payload (below the 40-message cap)
SIMPLE_HISTORY = [{"role": "user", "content": "I want to build a data pipeline agent"}]

# Minimal valid spec the mocked run_chat_turn will return
VALID_SPEC_YAML = (
    "name: my-agent\nversion: 1.0.0\nteam: engineering\n"
    "owner: alice@example.com\nframework: langgraph\n"
    "model:\n  primary: claude-sonnet-4-6\ndeploy:\n  cloud: aws\n"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chat_request(messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"messages": messages or SIMPLE_HISTORY}


def _mock_backend_with_key(key: str) -> AsyncMock:
    """Return a mock secrets backend that returns *key* for SECRET_NAME."""
    backend = AsyncMock()
    backend.get = AsyncMock(return_value=key)
    return backend


def _mock_backend_no_key() -> AsyncMock:
    """Return a mock secrets backend that has no key set."""
    backend = AsyncMock()
    backend.get = AsyncMock(return_value=None)
    return backend


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------

_BUILDERS_MODULE = "api.routes.builders"


def _patch_backend(backend_mock: Any):
    """Patch get_workspace_backend() to return (mock_backend, mock_ws_config)."""
    ws_config = MagicMock()
    ws_config.workspace = "default"
    return patch(f"{_BUILDERS_MODULE}.get_workspace_backend", return_value=(backend_mock, ws_config))


def _patch_run_chat_turn(result: Any):
    """Patch run_chat_turn in the builders module."""
    return patch(f"{_BUILDERS_MODULE}.run_chat_turn", new=AsyncMock(return_value=result))


def _patch_provider_class(instance: Any):
    """Patch AnthropicProvider so its constructor returns *instance*."""
    return patch(f"{_BUILDERS_MODULE}.AnthropicProvider", return_value=instance)


# ---------------------------------------------------------------------------
# Test: missing key → HTTP 400, provider never constructed
# ---------------------------------------------------------------------------


class TestMissingKey:
    def test_missing_key_returns_400(self) -> None:
        """When the secret is absent, the endpoint must return 400, not 500."""
        backend = _mock_backend_no_key()
        provider_mock = MagicMock()

        with _patch_backend(backend), _patch_provider_class(provider_mock):
            resp = client.post("/api/v1/builders/chat", json=_chat_request())

        assert resp.status_code == 400
        body = resp.json()
        # Must have a helpful message pointing to the chat-to-build key
        assert "claude" in body["detail"].lower() or "key" in body["detail"].lower()

    def test_missing_key_provider_never_constructed(self) -> None:
        """The AnthropicProvider must not be instantiated when the key is absent."""
        backend = _mock_backend_no_key()
        provider_class = MagicMock()

        with _patch_backend(backend), _patch_provider_class(provider_class):
            client.post("/api/v1/builders/chat", json=_chat_request())

        provider_class.assert_not_called()

    def test_missing_key_body_does_not_leak_secret_name_as_value(self) -> None:
        """The 400 body must not contain something that looks like an API key value."""
        backend = _mock_backend_no_key()

        with _patch_backend(backend), _patch_provider_class(MagicMock()):
            resp = client.post("/api/v1/builders/chat", json=_chat_request())

        body_text = resp.text
        # We're just checking nothing that starts with 'sk-ant' leaks in the error body
        assert "sk-ant" not in body_text


# ---------------------------------------------------------------------------
# Test: key present + text-turn response → 200
# ---------------------------------------------------------------------------


class TestTextTurn:
    def test_text_turn_returns_200(self) -> None:
        from engine.agent_chat_builder import ChatTurnResult

        text_result = ChatTurnResult(
            assistant_message="What framework would you like?",
            agent_yaml=None,
            valid=False,
            errors=[],
        )
        backend = _mock_backend_with_key(FAKE_API_KEY)
        provider_instance = AsyncMock()
        provider_instance.close = AsyncMock()

        with _patch_backend(backend), _patch_provider_class(provider_instance), _patch_run_chat_turn(text_result):
            resp = client.post("/api/v1/builders/chat", json=_chat_request())

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["assistant_message"] == "What framework would you like?"
        assert data["agent_yaml"] is None
        assert data["valid"] is False

    def test_api_key_not_in_response_body(self) -> None:
        """The fake API key must never appear in the response body."""
        from engine.agent_chat_builder import ChatTurnResult

        text_result = ChatTurnResult(
            assistant_message="Hello!",
            agent_yaml=None,
            valid=False,
            errors=[],
        )
        backend = _mock_backend_with_key(FAKE_API_KEY)
        provider_instance = AsyncMock()
        provider_instance.close = AsyncMock()

        with _patch_backend(backend), _patch_provider_class(provider_instance), _patch_run_chat_turn(text_result):
            resp = client.post("/api/v1/builders/chat", json=_chat_request())

        # The API key must NEVER appear in the response
        assert FAKE_API_KEY not in resp.text

    def test_api_key_not_in_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        """The API key must never appear in any log record."""
        from engine.agent_chat_builder import ChatTurnResult

        text_result = ChatTurnResult(
            assistant_message="Hello!",
            agent_yaml=None,
            valid=False,
            errors=[],
        )
        backend = _mock_backend_with_key(FAKE_API_KEY)
        provider_instance = AsyncMock()
        provider_instance.close = AsyncMock()

        with caplog.at_level(logging.DEBUG, logger="api.routes.builders"):
            with _patch_backend(backend), _patch_provider_class(provider_instance), _patch_run_chat_turn(text_result):
                client.post("/api/v1/builders/chat", json=_chat_request())

        for record in caplog.records:
            assert FAKE_API_KEY not in record.getMessage(), (
                f"API key found in log: {record.getMessage()!r}"
            )


# ---------------------------------------------------------------------------
# Test: key present + valid tool-use spec → 200 with agent_yaml + valid: true
# ---------------------------------------------------------------------------


class TestValidSpecTurn:
    def test_valid_spec_response(self) -> None:
        from engine.agent_chat_builder import ChatTurnResult

        spec_result = ChatTurnResult(
            assistant_message="",
            agent_yaml=VALID_SPEC_YAML,
            valid=True,
            errors=[],
        )
        backend = _mock_backend_with_key(FAKE_API_KEY)
        provider_instance = AsyncMock()
        provider_instance.close = AsyncMock()

        with _patch_backend(backend), _patch_provider_class(provider_instance), _patch_run_chat_turn(spec_result):
            resp = client.post("/api/v1/builders/chat", json=_chat_request())

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["agent_yaml"] == VALID_SPEC_YAML
        assert data["valid"] is True
        assert data["errors"] == []

    def test_key_not_in_valid_spec_response(self) -> None:
        from engine.agent_chat_builder import ChatTurnResult

        spec_result = ChatTurnResult(
            assistant_message="",
            agent_yaml=VALID_SPEC_YAML,
            valid=True,
            errors=[],
        )
        backend = _mock_backend_with_key(FAKE_API_KEY)
        provider_instance = AsyncMock()
        provider_instance.close = AsyncMock()

        with _patch_backend(backend), _patch_provider_class(provider_instance), _patch_run_chat_turn(spec_result):
            resp = client.post("/api/v1/builders/chat", json=_chat_request())

        assert FAKE_API_KEY not in resp.text


# ---------------------------------------------------------------------------
# Test: message count/size limits
# ---------------------------------------------------------------------------


class TestSizeLimits:
    def test_too_many_messages_returns_422(self) -> None:
        """Payloads with more than 40 messages must be rejected (Pydantic → 422)."""
        big_history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
            for i in range(41)
        ]
        resp = client.post("/api/v1/builders/chat", json={"messages": big_history})
        assert resp.status_code == 422

    def test_oversized_message_content_returns_422(self) -> None:
        """A single message with enormous content must be rejected (Pydantic → 422)."""
        huge_history = [{"role": "user", "content": "x" * 100_001}]
        resp = client.post("/api/v1/builders/chat", json={"messages": huge_history})
        assert resp.status_code == 422

    def test_empty_messages_returns_422(self) -> None:
        """An empty messages list should be rejected (Pydantic validation)."""
        resp = client.post("/api/v1/builders/chat", json={"messages": []})
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Test: upstream Anthropic errors → clean error, key never leaked
# ---------------------------------------------------------------------------


class TestUpstreamErrors:
    def test_auth_error_returns_502_or_400(self) -> None:
        """An AnthropicProvider AuthenticationError → clean HTTP error, no key leak."""
        from engine.providers.base import AuthenticationError

        backend = _mock_backend_with_key(FAKE_API_KEY)
        provider_instance = AsyncMock()
        provider_instance.generate = AsyncMock(side_effect=AuthenticationError("Invalid key"))
        provider_instance.close = AsyncMock()

        with _patch_backend(backend), _patch_provider_class(provider_instance):
            resp = client.post("/api/v1/builders/chat", json=_chat_request())

        assert resp.status_code in (400, 502)
        # Key must never appear in the error body
        assert FAKE_API_KEY not in resp.text

    def test_provider_error_key_not_leaked(self) -> None:
        """Generic ProviderError → key never in response body."""
        from engine.providers.base import ProviderError

        backend = _mock_backend_with_key(FAKE_API_KEY)
        provider_instance = AsyncMock()
        provider_instance.generate = AsyncMock(
            side_effect=ProviderError(f"Upstream error: key={FAKE_API_KEY}")
        )
        provider_instance.close = AsyncMock()

        with _patch_backend(backend), _patch_provider_class(provider_instance):
            resp = client.post("/api/v1/builders/chat", json=_chat_request())

        # Even if the raw error contained the key, the response must not
        assert FAKE_API_KEY not in resp.text
