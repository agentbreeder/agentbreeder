"""Unit tests for ``agentbreeder list <entity>`` (issue #560, bug #6).

Covers the four flows promised by the bug fix:

* empty list — table emits a "No agents found" hint
* three agents — table contains all three names
* API unreachable — clean stderr message, exit code 1
* unauthenticated — "Run `agentbreeder login` first." and exit code 2
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def _ok_response(data: list[dict[str, Any]], *, total: int | None = None) -> httpx.Response:
    """Build a fake 2xx ApiResponse with ``data`` + ``meta`` envelope."""
    body = {
        "data": data,
        "meta": {"page": 1, "per_page": 20, "total": total if total is not None else len(data)},
    }
    return httpx.Response(200, json=body)


@pytest.fixture(autouse=True)
def _stub_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: a token is configured. Individual tests override to test unauth."""
    monkeypatch.setenv("AGENTBREEDER_API_TOKEN", "test-token-xyz")
    monkeypatch.delenv("AGENTBREEDER_URL", raising=False)
    monkeypatch.delenv("AGENTBREEDER_API_URL", raising=False)


def _patched_client(response: httpx.Response | Exception) -> Any:
    """Return a context-manager mock yielding a client whose .get() returns ``response``.

    If ``response`` is an exception, ``.get()`` raises it (simulates transport
    errors like ConnectError / ReadTimeout).
    """

    class _FakeClient:
        def __enter__(self) -> "_FakeClient":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def get(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            if isinstance(response, Exception):
                raise response
            return response

    return lambda *_a, **_kw: _FakeClient()


# ── Tests ──────────────────────────────────────────────────────────────────


class TestListAgents:
    def test_empty_list_table(self) -> None:
        with patch("cli.commands.list_cmd.httpx.Client", _patched_client(_ok_response([]))):
            result = runner.invoke(app, ["list", "agents"])
        assert result.exit_code == 0, result.stderr
        assert "No agents found" in result.stdout

    def test_empty_list_json(self) -> None:
        with patch("cli.commands.list_cmd.httpx.Client", _patched_client(_ok_response([]))):
            result = runner.invoke(app, ["list", "agents", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert parsed == []

    def test_three_agents_render(self) -> None:
        agents = [
            {
                "name": "support-bot",
                "version": "1.0.0",
                "team": "cs",
                "framework": "langgraph",
                "status": "running",
                "endpoint_url": "https://a.example.com",
            },
            {
                "name": "research-bot",
                "version": "2.1.0",
                "team": "eng",
                "framework": "crewai",
                "status": "pending",
                "endpoint_url": "https://b.example.com",
            },
            {
                "name": "ops-bot",
                "version": "0.3.0",
                "team": "ops",
                "framework": "claude_sdk",
                "status": "running",
                "endpoint_url": "https://c.example.com",
            },
        ]
        with patch("cli.commands.list_cmd.httpx.Client", _patched_client(_ok_response(agents))):
            result = runner.invoke(app, ["list", "agents"])
        assert result.exit_code == 0, result.stderr
        # All three names + a representative framework column should appear.
        for name in ("support-bot", "research-bot", "ops-bot"):
            assert name in result.stdout
        assert "langgraph" in result.stdout

    def test_three_agents_json(self) -> None:
        agents = [
            {"name": "a", "version": "1", "team": "x", "framework": "f", "status": "s"},
            {"name": "b", "version": "1", "team": "x", "framework": "f", "status": "s"},
            {"name": "c", "version": "1", "team": "x", "framework": "f", "status": "s"},
        ]
        with patch("cli.commands.list_cmd.httpx.Client", _patched_client(_ok_response(agents))):
            result = runner.invoke(app, ["list", "agents", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.stdout)
        assert [a["name"] for a in parsed] == ["a", "b", "c"]

    def test_api_unreachable(self) -> None:
        err = httpx.ConnectError("Connection refused")
        with patch("cli.commands.list_cmd.httpx.Client", _patched_client(err)):
            result = runner.invoke(app, ["list", "agents"])
        assert result.exit_code == 1
        # Error must NOT be a stub agent — should mention reachability.
        assert "Could not reach" in result.stderr
        # And must NOT print fake data on the happy path.
        assert "test-agent" not in result.stdout
        assert "stub" not in result.stdout.lower()

    def test_unauthenticated_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Drop the auto-injected token and ensure no keychain backend is found.
        monkeypatch.delenv("AGENTBREEDER_API_TOKEN", raising=False)
        monkeypatch.setattr("cli._http._try_keyring", lambda: None)
        result = runner.invoke(app, ["list", "agents"])
        assert result.exit_code == 2
        assert "Run `agentbreeder login` first." in result.stderr

    def test_401_response_treated_as_unauthenticated(self) -> None:
        resp = httpx.Response(401, json={"detail": "expired"})
        with patch("cli.commands.list_cmd.httpx.Client", _patched_client(resp)):
            result = runner.invoke(app, ["list", "agents"])
        assert result.exit_code == 2
        assert "Unauthorized" in result.stderr


class TestListOtherEntities:
    def test_providers_calls_providers_endpoint(self) -> None:
        captured: dict[str, Any] = {}

        class _Capture:
            def __enter__(self) -> "_Capture":
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def get(self, url: str, **kwargs: Any) -> httpx.Response:
                captured["url"] = url
                return _ok_response([])

        with patch("cli.commands.list_cmd.httpx.Client", lambda *_a, **_kw: _Capture()):
            result = runner.invoke(app, ["list", "providers"])
        assert result.exit_code == 0
        assert "/api/v1/providers" in captured["url"]

    def test_tools_reads_local_registry(self, tmp_path: Any, monkeypatch: Any) -> None:
        """`list tools` reads from the local JSON registry written by `scan`."""
        monkeypatch.setattr("cli.commands.list_cmd.REGISTRY_DIR", tmp_path)
        result = runner.invoke(app, ["list", "tools"])
        assert result.exit_code == 0
        # Empty registry → friendly hint to run `scan`
        assert "No tools" in result.stdout or "scan" in result.stdout
