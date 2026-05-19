"""Tests for memory route + service P1 fixes (W4-31..34).

Covers:
- W4-31: Literal validation on ``backend_type`` and ``memory_type`` in
  ``CreateMemoryConfigRequest``.
- W4-32: LIKE wildcard escape + ``max_length`` on the ``q`` query parameter
  in the search route.
- W4-33: ``content`` max_length on ``MemoryMessageCreate``.
- W4-34: Module-level circuit breaker around ``_generate_summary`` so a slow
  or broken playground endpoint does not block ``store_message``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.services import memory_service
from api.services.memory_service import (
    _SUMMARY_CIRCUIT_THRESHOLD,
    MemoryService,
    _escape_like_pattern,
    _summary_circuit_is_open,
    _summary_circuit_reset,
)

client = TestClient(app)


# ===================================================================
# W4-31: Literal types on backend_type / memory_type
# ===================================================================


class TestLiteralValidation:
    def test_create_config_rejects_invalid_backend_type(self) -> None:
        resp = client.post(
            "/api/v1/memory/configs",
            json={"name": "bad-backend", "backend_type": "foo"},
        )
        assert resp.status_code == 422
        body = resp.json()
        # Pydantic v2 surfaces literal errors under "detail" with type "literal_error"
        assert any("backend_type" in str(err) for err in body.get("detail", [])), body

    def test_create_config_rejects_invalid_memory_type(self) -> None:
        resp = client.post(
            "/api/v1/memory/configs",
            json={"name": "bad-mem", "memory_type": "bogus"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert any("memory_type" in str(err) for err in body.get("detail", [])), body

    @patch("api.routes.memory.MemoryService.create_config", new_callable=AsyncMock)
    def test_create_config_accepts_valid_postgresql(self, mock_cc: AsyncMock) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        cfg = MagicMock()
        cfg.model_dump.return_value = {
            "id": "11111111-1111-1111-1111-111111111111",
            "name": "ok",
            "backend_type": "postgresql",
            "memory_type": "buffer_window",
            "max_messages": 100,
            "namespace_pattern": "{agent_id}:{session_id}",
            "scope": "agent",
            "linked_agents": [],
            "description": "",
            "created_at": now,
            "updated_at": now,
        }
        mock_cc.return_value = cfg
        resp = client.post(
            "/api/v1/memory/configs",
            json={
                "name": "ok",
                "backend_type": "postgresql",
                "memory_type": "buffer_window",
            },
        )
        assert resp.status_code == 201, resp.text


# ===================================================================
# W4-32: LIKE escape + max_length on q
# ===================================================================


class TestLikeWildcardEscape:
    def test_escape_like_pattern_escapes_percent(self) -> None:
        assert _escape_like_pattern("a%b") == "a\\%b"

    def test_escape_like_pattern_escapes_underscore(self) -> None:
        assert _escape_like_pattern("a_b") == "a\\_b"

    def test_escape_like_pattern_escapes_backslash(self) -> None:
        # backslash must be escaped FIRST so the wildcard escapes are not re-escaped
        assert _escape_like_pattern("a\\b") == "a\\\\b"

    def test_escape_like_pattern_passes_plain_text(self) -> None:
        assert _escape_like_pattern("hello world") == "hello world"

    @pytest.mark.asyncio
    async def test_search_messages_escapes_user_supplied_wildcards(self) -> None:
        """The query string passed to ilike must have ``%`` / ``_`` escaped."""
        captured: dict[str, Any] = {}

        class _FakeResult:
            def scalars(self) -> _FakeResult:
                return self

            def all(self) -> list[Any]:
                return []

        class _FakeDB:
            async def execute(self, stmt: Any) -> _FakeResult:
                # Walk the compiled WHERE clause and pull out the ilike pattern.
                # SQLAlchemy stores it as a bind param; render the SQL for inspection.
                captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
                return _FakeResult()

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_session_ctx() -> Any:
            yield _FakeDB()

        with patch("api.services.memory_service.async_session", _fake_session_ctx):
            await MemoryService.search_messages(
                "00000000-0000-0000-0000-000000000001",
                query="50% off_today",
                limit=10,
            )

        sql = captured["sql"]
        # The escaped form should appear in the rendered SQL.
        # ilike("%<safe>%") should contain "50\\%" and "off\\_today" with escape=\\.
        assert "50\\%" in sql or "\\%" in sql, sql
        assert "off\\_today" in sql or "\\_today" in sql, sql

    def test_search_rejects_q_over_200_chars(self) -> None:
        resp = client.get(
            "/api/v1/memory/configs/cfg1/search",
            params={"q": "x" * 201},
        )
        assert resp.status_code == 422, resp.text


# ===================================================================
# W4-33: content max_length
# ===================================================================


class TestContentMaxLength:
    def test_store_message_rejects_content_over_100k(self) -> None:
        resp = client.post(
            "/api/v1/memory/configs/cfg1/messages",
            json={
                "session_id": "s1",
                "role": "user",
                "content": "x" * 100_001,
            },
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert any("content" in str(err) for err in body.get("detail", [])), body

    @patch("api.routes.memory.MemoryService.store_message", new_callable=AsyncMock)
    def test_store_message_accepts_content_at_limit(self, mock_sm: AsyncMock) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        msg = MagicMock()
        msg.model_dump.return_value = {
            "id": "22222222-2222-2222-2222-222222222222",
            "config_id": "cfg1",
            "session_id": "s1",
            "agent_id": None,
            "role": "user",
            "content": "x" * 100_000,
            "metadata": {},
            "timestamp": now,
        }
        mock_sm.return_value = msg
        resp = client.post(
            "/api/v1/memory/configs/cfg1/messages",
            json={
                "session_id": "s1",
                "role": "user",
                "content": "x" * 100_000,
            },
        )
        assert resp.status_code == 201, resp.text


class TestMemoryRouteTeamScope:
    """HR-1 (#403): routes forward caller's team; cross-team -> 403."""

    def test_store_message_returns_403_on_cross_team(self) -> None:
        with patch(
            "api.routes.memory.MemoryService.store_message",
            side_effect=PermissionError("Team scope violation"),
        ):
            resp = client.post(
                "/api/v1/memory/configs/cfg1/messages",
                json={"session_id": "s1", "role": "user", "content": "hi"},
            )
        assert resp.status_code == 403
        assert "Team scope violation" in resp.json()["detail"]

    def test_get_conversation_returns_403_on_cross_team(self) -> None:
        cfg = MagicMock()
        cfg.model_dump.return_value = {"id": "cfg1"}
        with (
            patch(
                "api.routes.memory.MemoryService.get_config",
                new=AsyncMock(return_value=cfg),
            ),
            patch(
                "api.routes.memory.MemoryService.get_conversation",
                side_effect=PermissionError("Team scope violation"),
            ),
        ):
            resp = client.get("/api/v1/memory/configs/cfg1/conversations/s1")
        assert resp.status_code == 403


# ===================================================================
# W4-34: Summary LLM circuit breaker
# ===================================================================


class _FakeMsg:
    def __init__(self, role: str = "user", content: str = "hi") -> None:
        self.role = role
        self.content = content


class TestSummaryCircuitBreaker:
    def setup_method(self) -> None:
        _summary_circuit_reset()

    def teardown_method(self) -> None:
        _summary_circuit_reset()

    def test_circuit_starts_closed(self) -> None:
        assert _summary_circuit_is_open() is False

    def test_circuit_opens_after_three_failures(self) -> None:
        memory_service._summary_record_failure()
        memory_service._summary_record_failure()
        assert _summary_circuit_is_open() is False
        memory_service._summary_record_failure()
        assert _summary_circuit_is_open() is True

    @pytest.mark.asyncio
    async def test_generate_summary_returns_stub_when_circuit_open(self) -> None:
        # Force the circuit open
        for _ in range(_SUMMARY_CIRCUIT_THRESHOLD):
            memory_service._summary_record_failure()
        assert _summary_circuit_is_open() is True

        # When the circuit is open, httpx should NEVER be called.
        with patch("httpx.AsyncClient") as mock_client:
            result = await MemoryService._generate_summary([_FakeMsg(), _FakeMsg()])
            assert mock_client.call_count == 0
        assert result == "[Summary of 2 messages]"

    @pytest.mark.asyncio
    async def test_generate_summary_records_failure_on_exception(self) -> None:
        """A raising LLM call should increment the failure counter and return the stub."""

        class _ExplodingClient:
            def __init__(self, *a: Any, **kw: Any) -> None:
                pass

            async def __aenter__(self) -> _ExplodingClient:
                return self

            async def __aexit__(self, *a: Any) -> None:
                pass

            async def post(self, *a: Any, **kw: Any) -> Any:
                raise RuntimeError("boom")

        with patch("httpx.AsyncClient", _ExplodingClient):
            out = await MemoryService._generate_summary([_FakeMsg()])
        assert out == "[Summary of 1 messages]"
        # One failure recorded but circuit not yet open
        assert _summary_circuit_is_open() is False
        # Two more should open it
        with patch("httpx.AsyncClient", _ExplodingClient):
            await MemoryService._generate_summary([_FakeMsg()])
            await MemoryService._generate_summary([_FakeMsg()])
        assert _summary_circuit_is_open() is True

    @pytest.mark.asyncio
    async def test_generate_summary_returns_llm_response_on_success(self) -> None:
        class _Response:
            status_code = 200

            def json(self) -> dict[str, Any]:
                return {"content": "Condensed summary of the chat."}

        class _OKClient:
            def __init__(self, *a: Any, **kw: Any) -> None:
                pass

            async def __aenter__(self) -> _OKClient:
                return self

            async def __aexit__(self, *a: Any) -> None:
                pass

            async def post(self, *a: Any, **kw: Any) -> _Response:
                return _Response()

        with patch("httpx.AsyncClient", _OKClient):
            out = await MemoryService._generate_summary([_FakeMsg()])
        assert out == "Condensed summary of the chat."
        # No failures recorded; circuit remains closed.
        assert _summary_circuit_is_open() is False


# ===================================================================
# Sanity: ensure existing /search endpoint still rejects empty q
# ===================================================================


class TestSearchQueryValidation:
    def test_search_rejects_empty_q(self) -> None:
        resp = client.get("/api/v1/memory/configs/cfg1/search", params={"q": ""})
        assert resp.status_code == 422


# ===================================================================
# MM8: ttl_seconds field on CreateMemoryConfigRequest
# ===================================================================


class TestTtlField:
    def test_ttl_seconds_must_be_positive(self) -> None:
        """Pydantic should reject zero/negative ttl_seconds (ge=1)."""
        resp = client.post(
            "/api/v1/memory/configs",
            json={"name": "bad-ttl", "ttl_seconds": 0},
        )
        assert resp.status_code == 422, resp.text

    def test_ttl_seconds_rejects_negative(self) -> None:
        resp = client.post(
            "/api/v1/memory/configs",
            json={"name": "neg-ttl", "ttl_seconds": -1},
        )
        assert resp.status_code == 422, resp.text

    @patch("api.routes.memory.MemoryService.create_config", new_callable=AsyncMock)
    def test_create_config_passes_ttl_to_service(self, mock_cc: AsyncMock) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        cfg = MagicMock()
        cfg.model_dump.return_value = {
            "id": "33333333-3333-3333-3333-333333333333",
            "name": "ttl-ok",
            "backend_type": "postgresql",
            "memory_type": "buffer_window",
            "max_messages": 100,
            "namespace_pattern": "{agent_id}:{session_id}",
            "scope": "agent",
            "linked_agents": [],
            "description": "",
            "ttl_seconds": 7200,
            "created_at": now,
            "updated_at": now,
        }
        mock_cc.return_value = cfg
        resp = client.post(
            "/api/v1/memory/configs",
            json={"name": "ttl-ok", "ttl_seconds": 7200},
        )
        assert resp.status_code == 201, resp.text
        # The service must have been called with the ttl_seconds kwarg.
        _, kwargs = mock_cc.call_args
        assert kwargs.get("ttl_seconds") == 7200


# ===================================================================
# MM9: GDPR delete-by-user route
# ===================================================================


class TestGdprDeleteRoute:
    def test_delete_user_rejects_blank_user_id(self) -> None:
        """A whitespace-only user_id is rejected at the route layer."""
        # URL-encoded space — FastAPI will route, route handler validates.
        resp = client.delete("/api/v1/memory/user/%20")
        # Either 400 (blank rejected) or 404/422 if the path matcher rejects
        # the encoded space — both indicate the request was not silently
        # treated as a valid deletion.
        assert resp.status_code in {400, 404, 422}, resp.text

    @patch(
        "api.routes.memory.MemoryService.delete_messages_by_user_id",
        new_callable=AsyncMock,
    )
    def test_delete_user_calls_service_with_user_id(self, mock_del: AsyncMock) -> None:
        mock_del.return_value = 7
        resp = client.delete("/api/v1/memory/user/alice@example.com")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["deleted_count"] == 7
        assert body["data"]["user_id"] == "alice@example.com"
        mock_del.assert_awaited_once_with("alice@example.com")


# ===================================================================
# MM8: TTL cleanup route
# ===================================================================


class TestTtlCleanupRoute:
    @patch("api.routes.memory.MemoryService.cleanup_expired_messages", new_callable=AsyncMock)
    @patch("api.routes.memory.MemoryService.get_config", new_callable=AsyncMock)
    def test_cleanup_returns_deleted_count(
        self, mock_get: AsyncMock, mock_cleanup: AsyncMock
    ) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        cfg = MagicMock()
        cfg.id = "44444444-4444-4444-4444-444444444444"
        cfg.name = "ttl-cfg"
        cfg.backend_type = "postgresql"
        cfg.memory_type = "buffer_window"
        cfg.max_messages = 100
        cfg.namespace_pattern = "{agent_id}:{session_id}"
        cfg.scope = "agent"
        cfg.linked_agents = []
        cfg.description = ""
        cfg.ttl_seconds = 60
        cfg.created_at = now
        cfg.updated_at = now
        mock_get.return_value = cfg
        mock_cleanup.return_value = 3

        resp = client.post(
            f"/api/v1/memory/configs/{cfg.id}/cleanup",
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["data"]["deleted_count"] == 3
        mock_cleanup.assert_awaited_once()

    @patch("api.routes.memory.MemoryService.get_config", new_callable=AsyncMock)
    def test_cleanup_returns_404_when_config_missing(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = None
        resp = client.post("/api/v1/memory/configs/nonexistent/cleanup")
        assert resp.status_code == 404, resp.text
