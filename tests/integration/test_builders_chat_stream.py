"""Integration tests for POST /api/v1/builders/chat/stream."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from engine.agent_chat_builder import ChatStreamEvent, ChatTurnResult, SetupRequest
from engine.providers.base import AuthenticationError, ProviderError


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_chat_stream_requires_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no BYO key stored, the endpoint returns 400 before constructing a provider."""

    class _Backend:
        async def get(self, _name: str) -> None:
            return None

    monkeypatch.setattr(
        "api.routes.builders.get_workspace_backend", lambda: (_Backend(), None)
    )

    resp = client.post(
        "/api/v1/builders/chat/stream",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400
    assert "key" in resp.text.lower()


def test_chat_stream_emits_token_and_done_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSE stream emits token and done events with correct content-type."""

    class _Backend:
        async def get(self, _name: str) -> str:
            return "sk-ant-test"

    monkeypatch.setattr(
        "api.routes.builders.get_workspace_backend", lambda: (_Backend(), None)
    )

    class _Provider:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr("api.routes.builders.AnthropicProvider", _Provider)

    async def _fake_stream(
        _provider: object, _history: list[dict[str, str]]
    ):  # type: ignore[return]
        yield ChatStreamEvent(type="token", text="Hello")
        yield ChatStreamEvent(
            type="done",
            result=ChatTurnResult(
                assistant_message="Hello", agent_yaml=None, valid=False, errors=[]
            ),
        )

    monkeypatch.setattr("api.routes.builders.run_chat_turn_stream", _fake_stream)

    resp = client.post(
        "/api/v1/builders/chat/stream",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    body = resp.text
    assert "event: token" in body
    assert "event: done" in body
    assert "Hello" in body


def test_chat_stream_emits_setup_request_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stream forwards a setup_request event with kind/name to the client."""

    class _Backend:
        async def get(self, _name: str) -> str:
            return "sk-ant-test"

    monkeypatch.setattr(
        "api.routes.builders.get_workspace_backend", lambda: (_Backend(), None)
    )

    class _Provider:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr("api.routes.builders.AnthropicProvider", _Provider)

    async def _fake_stream(
        _provider: object, _history: list[dict[str, str]]
    ):  # type: ignore[return]
        yield ChatStreamEvent(
            type="setup_request",
            setup=SetupRequest(kind="mcp", name="zendesk", reason="read tickets"),
        )
        yield ChatStreamEvent(
            type="done",
            result=ChatTurnResult(
                assistant_message="",
                agent_yaml=None,
                valid=False,
                errors=[],
                setup_request=SetupRequest(kind="mcp", name="zendesk", reason="read tickets"),
            ),
        )

    monkeypatch.setattr("api.routes.builders.run_chat_turn_stream", _fake_stream)

    resp = client.post(
        "/api/v1/builders/chat/stream",
        json={"messages": [{"role": "user", "content": "support agent"}]},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "event: setup_request" in body
    assert "zendesk" in body
    assert '"kind": "mcp"' in body or '"kind":"mcp"' in body


def test_chat_stream_auth_error_yields_sse_error_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When run_chat_turn_stream raises AuthenticationError, the stream emits
    an SSE error event with a sanitised detail and does NOT leak the exception
    text or any raw key material."""

    class _Backend:
        async def get(self, _name: str) -> str:
            return "sk-ant-test"

    monkeypatch.setattr(
        "api.routes.builders.get_workspace_backend", lambda: (_Backend(), None)
    )

    class _Provider:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr("api.routes.builders.AnthropicProvider", _Provider)

    # Simulate an AuthenticationError raised from within the streaming generator.
    # The error message contains a canary string — the test verifies it is
    # NOT forwarded to the client in any form.
    _LEAK_CANARY = "secret-canary-token-must-not-appear-in-response"

    async def _raise_auth(
        _provider: object, _history: list[dict[str, str]]
    ):  # type: ignore[return]
        raise AuthenticationError(f"401 Unauthorized — {_LEAK_CANARY}")
        yield  # make this an async generator

    monkeypatch.setattr("api.routes.builders.run_chat_turn_stream", _raise_auth)

    resp = client.post(
        "/api/v1/builders/chat/stream",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    body = resp.text
    # Must contain an SSE error event
    assert "event: error" in body
    # The detail must be the sanitised message (case-insensitive match)
    assert "authentication failed" in body.lower()
    # The code field must identify the error category for the frontend
    assert "auth_error" in body
    # The raw exception message (including any canary text) must NOT appear
    assert _LEAK_CANARY not in body
    assert "401 Unauthorized" not in body


def test_chat_stream_provider_error_yields_sse_error_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When run_chat_turn_stream raises ProviderError, the stream emits an SSE
    error event with 'upstream_error' code and does NOT leak the raw exception text."""

    class _Backend:
        async def get(self, _name: str) -> str:
            return "sk-ant-test"

    monkeypatch.setattr(
        "api.routes.builders.get_workspace_backend", lambda: (_Backend(), None)
    )

    class _Provider:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr("api.routes.builders.AnthropicProvider", _Provider)

    # The canary text must not be forwarded to the client in any form.
    _LEAK_CANARY = "raw-upstream-detail-must-not-appear-CANARY"

    async def _raise_provider(
        _provider: object, _history: list[dict[str, str]]
    ):  # type: ignore[return]
        raise ProviderError(f"503 Service Unavailable — {_LEAK_CANARY}")
        yield  # make this an async generator

    monkeypatch.setattr("api.routes.builders.run_chat_turn_stream", _raise_provider)

    resp = client.post(
        "/api/v1/builders/chat/stream",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    body = resp.text
    # Must contain an SSE error event
    assert "event: error" in body
    # The sanitised detail must mention 'upstream' (case-insensitive)
    assert "upstream" in body.lower()
    # The code field must identify the error category
    assert "upstream_error" in body
    # The raw exception message must NOT appear
    assert _LEAK_CANARY not in body
    assert "503 Service Unavailable" not in body
