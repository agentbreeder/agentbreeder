"""Unit tests for cli/_http.py and the new auth/context commands (issue #415).

Covers token storage (env vs keychain precedence), the shared authenticated
HTTP helpers used by ``registry`` / ``model`` / future ``deploy --remote``,
and the user-facing ``login`` / ``logout`` / ``whoami`` / ``context`` flows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from cli import _http
from cli.main import app

# ── Fake keyring ────────────────────────────────────────────────────────────


class _FakeKeyring:
    """In-memory stand-in for the ``keyring`` module.

    Mirrors ``get_password`` / ``set_password`` / ``delete_password`` /
    ``get_keyring`` exactly enough for ``_http`` to behave as if a real OS
    keychain is present.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_keyring(self) -> object:
        return self  # truthy → "backend present"

    def get_password(self, service: str, user: str) -> str | None:
        return self.store.get((service, user))

    def set_password(self, service: str, user: str, value: str) -> None:
        self.store[(service, user)] = value

    def delete_password(self, service: str, user: str) -> None:
        self.store.pop((service, user), None)


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(_http, "_try_keyring", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def isolated_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect ``~/.agentbreeder/`` to a tmp dir for every test."""
    monkeypatch.setenv("AGENTBREEDER_HOME", str(tmp_path))
    monkeypatch.delenv(_http.ENV_TOKEN, raising=False)
    monkeypatch.delenv(_http.ENV_URL, raising=False)
    monkeypatch.delenv(_http.ENV_URL_LEGACY, raising=False)
    return tmp_path


runner = CliRunner()


# ── api_base ────────────────────────────────────────────────────────────────


class TestApiBase:
    def test_defaults_to_localhost(self) -> None:
        assert _http.api_base() == "http://localhost:8000"

    def test_strips_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_http.ENV_URL, "https://api.example.com/")
        assert _http.api_base() == "https://api.example.com"

    def test_new_env_var_wins_over_legacy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_http.ENV_URL, "https://new.example.com")
        monkeypatch.setenv(_http.ENV_URL_LEGACY, "https://legacy.example.com")
        assert _http.api_base() == "https://new.example.com"

    def test_legacy_env_var_used_when_new_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_http.ENV_URL_LEGACY, "https://legacy.example.com")
        assert _http.api_base() == "https://legacy.example.com"


# ── Token storage ───────────────────────────────────────────────────────────


class TestTokenStorage:
    def test_get_token_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "env-token")
        assert _http.get_token() == "env-token"

    def test_get_token_from_keychain(self, fake_keyring: _FakeKeyring) -> None:
        fake_keyring.store[("agentbreeder", "api_token")] = "stored-token"
        assert _http.get_token() == "stored-token"

    def test_env_overrides_keychain(
        self, fake_keyring: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_keyring.store[("agentbreeder", "api_token")] = "stored-token"
        monkeypatch.setenv(_http.ENV_TOKEN, "env-token")
        assert _http.get_token() == "env-token"

    def test_get_token_returns_none_without_keychain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(_http, "_try_keyring", lambda: None)
        assert _http.get_token() is None

    def test_set_token_writes_to_keychain(self, fake_keyring: _FakeKeyring) -> None:
        _http.set_token("new-token")
        assert fake_keyring.store[("agentbreeder", "api_token")] == "new-token"

    def test_set_token_rejects_empty(self, fake_keyring: _FakeKeyring) -> None:
        with pytest.raises(ValueError):
            _http.set_token("   ")

    def test_set_token_raises_without_keychain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_http, "_try_keyring", lambda: None)
        with pytest.raises(RuntimeError):
            _http.set_token("x")

    def test_clear_token_returns_true_when_present(self, fake_keyring: _FakeKeyring) -> None:
        fake_keyring.store[("agentbreeder", "api_token")] = "x"
        assert _http.clear_token() is True
        assert ("agentbreeder", "api_token") not in fake_keyring.store

    def test_clear_token_returns_false_when_absent(self, fake_keyring: _FakeKeyring) -> None:
        assert _http.clear_token() is False


# ── Active team context ─────────────────────────────────────────────────────


class TestActiveTeam:
    def test_round_trip(self) -> None:
        assert _http.get_active_team() is None
        _http.set_active_team("engineering")
        assert _http.get_active_team() == "engineering"

    def test_clear(self) -> None:
        _http.set_active_team("ops")
        _http.clear_active_team()
        assert _http.get_active_team() is None

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError):
            _http.set_active_team("  ")


# ── auth_headers / require_token ────────────────────────────────────────────


class TestAuthHeaders:
    def test_require_token_exits_when_missing(self) -> None:
        import typer

        with pytest.raises(typer.Exit):
            _http.require_token()

    def test_auth_headers_includes_bearer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "tok")
        headers = _http.auth_headers()
        assert headers["Authorization"] == "Bearer tok"
        assert headers["Content-Type"] == "application/json"

    def test_auth_headers_includes_team_when_active(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "tok")
        _http.set_active_team("ops")
        headers = _http.auth_headers()
        assert headers["X-AgentBreeder-Team"] == "ops"

    def test_auth_headers_omits_content_type_for_multipart(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "tok")
        headers = _http.auth_headers(json_body=False)
        assert "Content-Type" not in headers


# ── request() ───────────────────────────────────────────────────────────────


def _patched_client(handler: Any) -> Any:
    """Return a context manager that swaps ``httpx.Client`` for one wired to a mock transport.

    Captures the real ``httpx.Client`` reference before patching so the
    factory we install can still instantiate a real Client (just with a
    mock ``transport=`` injected).
    """
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    return patch("httpx.Client", factory)


class TestRequest:
    def test_get_returns_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "tok")
        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["url"] = str(req.url)
            captured["auth"] = req.headers.get("authorization")
            return httpx.Response(200, json={"ok": True})

        with _patched_client(handler):
            result = _http.request("GET", "/api/v1/foo")
        assert result == {"ok": True}
        assert captured["url"].endswith("/api/v1/foo")
        assert captured["auth"] == "Bearer tok"

    def test_401_exits_with_login_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import typer

        monkeypatch.setenv(_http.ENV_TOKEN, "tok")
        with _patched_client(lambda req: httpx.Response(401, text="bad token")):
            with pytest.raises(typer.Exit):
                _http.request("GET", "/api/v1/foo")

    def test_500_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import typer

        monkeypatch.setenv(_http.ENV_TOKEN, "tok")
        with _patched_client(lambda req: httpx.Response(500, text="boom")):
            with pytest.raises(typer.Exit):
                _http.request("GET", "/api/v1/foo")


# ── login command ───────────────────────────────────────────────────────────


class TestLoginCommand:
    def test_email_password_stores_token(
        self, fake_keyring: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/v1/auth/login":
                return httpx.Response(
                    200, json={"data": {"access_token": "new-jwt", "token_type": "bearer"}}
                )
            if req.url.path == "/api/v1/auth/me":
                assert req.headers["authorization"] == "Bearer new-jwt"
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": "abc",
                            "email": "alice@example.com",
                            "name": "Alice",
                            "team": "engineering",
                            "role": "deployer",
                        }
                    },
                )
            return httpx.Response(404)

        with _patched_client(handler):
            result = runner.invoke(
                app, ["login", "--email", "alice@example.com", "--password", "hunter2"]
            )
        assert result.exit_code == 0, result.output
        assert fake_keyring.store[("agentbreeder", "api_token")] == "new-jwt"
        assert "alice@example.com" in result.output

    def test_invalid_credentials_exits(self, fake_keyring: _FakeKeyring) -> None:
        with _patched_client(
            lambda req: httpx.Response(401, json={"detail": "Invalid email or password"})
        ):
            result = runner.invoke(
                app, ["login", "--email", "alice@example.com", "--password", "bad"]
            )
        assert result.exit_code == 1
        assert ("agentbreeder", "api_token") not in fake_keyring.store

    def test_with_token_flag_validates_via_me(self, fake_keyring: _FakeKeyring) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/api/v1/auth/me"
            assert req.headers["authorization"] == "Bearer pasted-jwt"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "abc",
                        "email": "alice@example.com",
                        "name": "Alice",
                        "team": "engineering",
                        "role": "deployer",
                    }
                },
            )

        with _patched_client(handler):
            result = runner.invoke(app, ["login", "--token", "pasted-jwt"])
        assert result.exit_code == 0, result.output
        assert fake_keyring.store[("agentbreeder", "api_token")] == "pasted-jwt"

    def test_with_token_flag_rejects_bad_token(self, fake_keyring: _FakeKeyring) -> None:
        with _patched_client(lambda req: httpx.Response(401, text="")):
            result = runner.invoke(app, ["login", "--token", "bad-jwt"])
        assert result.exit_code == 1
        assert ("agentbreeder", "api_token") not in fake_keyring.store


# ── logout / whoami ─────────────────────────────────────────────────────────


class TestLogoutCommand:
    def test_clears_stored_token(self, fake_keyring: _FakeKeyring) -> None:
        fake_keyring.store[("agentbreeder", "api_token")] = "old"
        result = runner.invoke(app, ["logout"])
        assert result.exit_code == 0
        assert ("agentbreeder", "api_token") not in fake_keyring.store
        assert "Logged out" in result.output

    def test_when_no_token_prints_yellow(self, fake_keyring: _FakeKeyring) -> None:
        result = runner.invoke(app, ["logout"])
        assert result.exit_code == 0
        assert "No stored token" in result.output


class TestWhoamiCommand:
    def test_prints_user_info(
        self, fake_keyring: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "tok")
        with _patched_client(
            lambda req: httpx.Response(
                200,
                json={
                    "data": {
                        "id": "abc",
                        "email": "alice@example.com",
                        "name": "Alice",
                        "team": "engineering",
                        "role": "deployer",
                    }
                },
            )
        ):
            result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 0, result.output
        assert "alice@example.com" in result.output
        assert "engineering" in result.output

    def test_json_output_includes_active_team(
        self, fake_keyring: _FakeKeyring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "tok")
        _http.set_active_team("ops")
        with _patched_client(
            lambda req: httpx.Response(
                200,
                json={
                    "data": {
                        "id": "abc",
                        "email": "alice@example.com",
                        "name": "Alice",
                        "team": "engineering",
                        "role": "deployer",
                    }
                },
            )
        ):
            result = runner.invoke(app, ["whoami", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["email"] == "alice@example.com"
        assert payload["active_team"] == "ops"

    def test_exits_when_not_logged_in(self) -> None:
        result = runner.invoke(app, ["whoami"])
        assert result.exit_code == 1
        assert "Not logged in" in result.output or "login" in result.output


# ── context command ────────────────────────────────────────────────────────


class TestContextCommand:
    def test_use_sets_team(self) -> None:
        result = runner.invoke(app, ["context", "use", "ops"])
        assert result.exit_code == 0
        assert _http.get_active_team() == "ops"
        assert "ops" in result.output

    def test_show_unset(self) -> None:
        result = runner.invoke(app, ["context", "show"])
        assert result.exit_code == 0
        assert "No active team" in result.output

    def test_show_set(self) -> None:
        _http.set_active_team("ops")
        result = runner.invoke(app, ["context", "show"])
        assert result.exit_code == 0
        assert "ops" in result.output

    def test_clear(self) -> None:
        _http.set_active_team("ops")
        result = runner.invoke(app, ["context", "clear"])
        assert result.exit_code == 0
        assert _http.get_active_team() is None
