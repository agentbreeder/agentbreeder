"""Unit tests for ``agentbreeder model`` CLI — Track G (#163).

The CLI is a thin shim over the API. We use ``typer.testing.CliRunner``
and patch ``cli.commands.model._request`` to return canned payloads — no
real HTTP, no live API server.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cli.commands.model import model_app


@pytest.fixture(autouse=True)
def _api_token(monkeypatch) -> None:
    """Provide a fake auth token so the auth-headers helper doesn't exit."""
    monkeypatch.setenv("AGENTBREEDER_API_TOKEN", "test-token")
    monkeypatch.setenv("AGENTBREEDER_API_URL", "http://api.test")


# ─── list ──────────────────────────────────────────────────────────────────


class TestList:
    def test_list_shows_table(self) -> None:
        canned = {
            "data": [
                {
                    "name": "gpt-4o",
                    "provider": "openai",
                    "status": "active",
                    "context_window": 128_000,
                    "discovered_at": "2026-04-01T00:00:00Z",
                    "last_seen_at": "2026-04-29T00:00:00Z",
                },
                {
                    "name": "gpt-3.5",
                    "provider": "openai",
                    "status": "deprecated",
                    "context_window": 16_000,
                    "discovered_at": "2026-01-01T00:00:00Z",
                    "last_seen_at": "2026-04-01T00:00:00Z",
                },
            ]
        }
        with patch("cli.commands.model._request", return_value=canned):
            result = CliRunner().invoke(model_app, ["list"])
        assert result.exit_code == 0, result.output
        assert "gpt-4o" in result.output
        assert "gpt-3.5" in result.output
        assert "active" in result.output
        assert "deprecated" in result.output

    def test_list_json_output(self) -> None:
        canned = {
            "data": [
                {
                    "name": "x",
                    "provider": "openai",
                    "status": "active",
                }
            ]
        }
        with patch("cli.commands.model._request", return_value=canned):
            result = CliRunner().invoke(model_app, ["list", "--json"])
        assert result.exit_code == 0
        rows = json.loads(result.output)
        assert rows[0]["name"] == "x"

    def test_list_filters_by_status_client_side(self) -> None:
        canned = {
            "data": [
                {"name": "a", "provider": "openai", "status": "active"},
                {"name": "b", "provider": "openai", "status": "deprecated"},
            ]
        }
        with patch("cli.commands.model._request", return_value=canned):
            result = CliRunner().invoke(model_app, ["list", "--status", "deprecated", "--json"])
        assert result.exit_code == 0
        rows = json.loads(result.output)
        assert [r["name"] for r in rows] == ["b"]


# ─── show ──────────────────────────────────────────────────────────────────


class TestShow:
    def test_show_renders_record(self) -> None:
        canned = {
            "data": [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "name": "gpt-4o",
                    "provider": "openai",
                    "status": "active",
                    "context_window": 128_000,
                    "discovered_at": "2026-04-01T00:00:00Z",
                }
            ]
        }
        with patch("cli.commands.model._request", return_value=canned):
            result = CliRunner().invoke(model_app, ["show", "gpt-4o"])
        assert result.exit_code == 0, result.output
        assert "gpt-4o" in result.output
        assert "openai" in result.output
        assert "active" in result.output

    def test_show_unknown(self) -> None:
        with patch("cli.commands.model._request", return_value={"data": []}):
            result = CliRunner().invoke(model_app, ["show", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


# ─── sync ──────────────────────────────────────────────────────────────────


class TestSync:
    def test_sync_calls_api_with_providers(self) -> None:
        canned = {
            "data": {
                "started_at": "2026-04-29T00:00:00Z",
                "finished_at": "2026-04-29T00:00:01Z",
                "duration_seconds": 1.0,
                "providers": [
                    {
                        "provider": "openai",
                        "added": ["gpt-4o"],
                        "seen": [],
                        "deprecated": [],
                        "retired": [],
                        "error": None,
                        "total_seen": 1,
                    }
                ],
                "totals": {"added": 1, "deprecated": 0, "retired": 0},
            }
        }
        with patch("cli.commands.model._request", return_value=canned) as mock_req:
            result = CliRunner().invoke(model_app, ["sync", "--provider", "openai"])
        assert result.exit_code == 0, result.output
        assert "Sync complete" in result.output
        method, path = mock_req.call_args[0][:2]
        assert method == "POST"
        assert path == "/api/v1/models/sync"
        body = mock_req.call_args.kwargs.get("body") or mock_req.call_args[0][2]
        assert body == {"providers": ["openai"]}

    def test_sync_json_output(self) -> None:
        canned = {
            "data": {
                "started_at": "2026-04-29T00:00:00Z",
                "finished_at": "2026-04-29T00:00:00Z",
                "duration_seconds": 0,
                "providers": [],
                "totals": {"added": 0, "deprecated": 0, "retired": 0},
            }
        }
        with patch("cli.commands.model._request", return_value=canned):
            result = CliRunner().invoke(model_app, ["sync", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["totals"]["added"] == 0


# ─── deprecate ─────────────────────────────────────────────────────────────


class TestDeprecate:
    def test_deprecate_with_replacement(self) -> None:
        canned = {
            "data": {
                "id": "x",
                "name": "old",
                "status": "deprecated",
                "deprecated_at": "2026-04-29T00:00:00Z",
                "replacement": "new",
            }
        }
        with patch("cli.commands.model._request", return_value=canned) as mock_req:
            result = CliRunner().invoke(model_app, ["deprecate", "old", "--replacement", "new"])
        assert result.exit_code == 0, result.output
        assert "old" in result.output
        assert "deprecated" in result.output
        method, path = mock_req.call_args[0][:2]
        assert method == "POST"
        assert path == "/api/v1/models/old/deprecate"


# ─── auth handling ─────────────────────────────────────────────────────────


def test_missing_token_exits(monkeypatch) -> None:
    monkeypatch.delenv("AGENTBREEDER_API_TOKEN", raising=False)
    result = CliRunner().invoke(model_app, ["list"])
    assert result.exit_code == 1
    assert "AGENTBREEDER_API_TOKEN" in result.output
