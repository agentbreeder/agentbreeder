"""Tests for ``agentbreeder deploy --remote`` (issue #416).

The remote path POSTs ``/api/v1/deploys`` instead of running the deploy
engine in-process, so the team-scoped RBAC gate (#414) and audit log fire
end-to-end. These tests verify:

* ``--remote`` / ``--local`` flag handling and the ``AGENTBREEDER_URL``
  auto-detect rule.
* The HTTP contract: POST body shape, polling cadence, success vs failure
  exit codes, terminal-status detection.
* The team-scope 403 path — the whole reason this issue exists.
* That ``--local`` does NOT hit the API (the in-process pathway is
  preserved as the dev/offline escape hatch).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from cli import _http
from cli.commands import deploy as deploy_cmd
from cli.main import app

runner = CliRunner()


VALID_YAML = """\
name: test-agent
version: 1.0.0
team: engineering
owner: test@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    p = tmp_path / "agent.yaml"
    p.write_text(VALID_YAML)
    return p


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Clear auth/URL env vars and shrink the poll interval for fast tests."""
    monkeypatch.setenv("AGENTBREEDER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(_http.ENV_TOKEN, raising=False)
    monkeypatch.delenv(_http.ENV_URL, raising=False)
    monkeypatch.delenv(_http.ENV_URL_LEGACY, raising=False)
    monkeypatch.setattr(deploy_cmd, "_POLL_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(_http, "_try_keyring", lambda: None)


def _patched_client(handler: Any) -> Any:
    """Wrap ``httpx.Client`` so any constructed Client uses ``handler``."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    return patch("httpx.Client", factory)


# ── _resolve_mode ───────────────────────────────────────────────────────────


class TestResolveMode:
    def test_both_flags_exits(self) -> None:
        import typer

        with pytest.raises(typer.Exit):
            deploy_cmd._resolve_mode(remote=True, local=True)

    def test_remote_flag_wins(self) -> None:
        assert deploy_cmd._resolve_mode(remote=True, local=False) is True

    def test_local_flag_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_http.ENV_URL, "https://api.example.com")
        assert deploy_cmd._resolve_mode(remote=False, local=True) is False

    def test_url_env_triggers_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_http.ENV_URL, "https://api.example.com")
        assert deploy_cmd._resolve_mode(remote=False, local=False) is True

    def test_legacy_url_env_triggers_remote(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_http.ENV_URL_LEGACY, "https://api.example.com")
        assert deploy_cmd._resolve_mode(remote=False, local=False) is True

    def test_no_url_no_flags_means_local(self) -> None:
        assert deploy_cmd._resolve_mode(remote=False, local=False) is False


# ── Remote mode: happy path ─────────────────────────────────────────────────


class TestRemoteHappyPath:
    def test_posts_yaml_and_polls_until_succeeded(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "test-tok")
        captured: dict[str, Any] = {"posts": [], "gets": []}

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST" and req.url.path == "/api/v1/deploys":
                captured["posts"].append(req)
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": "job-123",
                            "agent_id": "agent-abc",
                            "agent_name": "test-agent",
                            "status": "running",
                            "target": "local",
                            "error_message": None,
                            "started_at": "2026-05-20T00:00:00Z",
                            "completed_at": None,
                        }
                    },
                )
            if req.method == "GET" and req.url.path == "/api/v1/deploys/job-123":
                captured["gets"].append(req)
                # First poll: still running; second poll: succeeded
                status = "succeeded" if len(captured["gets"]) >= 2 else "running"
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": "job-123",
                            "agent_id": "agent-abc",
                            "agent_name": "test-agent",
                            "status": status,
                            "target": "local",
                        }
                    },
                )
            return httpx.Response(404, text=f"unexpected {req.method} {req.url.path}")

        with _patched_client(handler):
            result = runner.invoke(app, ["deploy", str(config_file), "--remote"])

        assert result.exit_code == 0, result.output
        assert len(captured["posts"]) == 1
        post_body = captured["posts"][0].read().decode()
        assert "test-agent" in post_body  # yaml made it into the request body
        assert captured["posts"][0].headers["authorization"] == "Bearer test-tok"
        assert len(captured["gets"]) >= 2
        assert "Deploy successful" in result.output

    def test_passes_target_to_api(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "test-tok")
        bodies: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST":
                bodies.append(req.read().decode())
                return httpx.Response(200, json={"data": {"id": "j1", "status": "succeeded"}})
            return httpx.Response(
                200, json={"data": {"id": "j1", "status": "succeeded", "target": "cloud-run"}}
            )

        with _patched_client(handler):
            result = runner.invoke(
                app, ["deploy", str(config_file), "--remote", "--target", "cloud-run"]
            )
        assert result.exit_code == 0, result.output
        assert any("cloud-run" in b for b in bodies), bodies


# ── Remote mode: failure paths ──────────────────────────────────────────────


class TestRemoteFailures:
    def test_403_from_team_scope_exits_with_message(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of #416 — when #414's resource_team= says no, we exit."""
        monkeypatch.setenv(_http.ENV_TOKEN, "test-tok")

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={"detail": "deployer role required for team other-team"},
            )

        with _patched_client(handler):
            result = runner.invoke(app, ["deploy", str(config_file), "--remote"])
        assert result.exit_code == 1
        assert "403" in result.output

    def test_401_exits_with_login_hint(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "expired-tok")

        with _patched_client(lambda req: httpx.Response(401, text="expired")):
            result = runner.invoke(app, ["deploy", str(config_file), "--remote"])
        assert result.exit_code == 1
        assert "login" in result.output.lower()

    def test_no_token_exits(self, config_file: Path) -> None:
        with _patched_client(lambda req: httpx.Response(200, json={})):
            result = runner.invoke(app, ["deploy", str(config_file), "--remote"])
        assert result.exit_code == 1
        assert "login" in result.output.lower() or "Not logged in" in result.output

    def test_terminal_failed_status_exits_nonzero(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "tok")

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST":
                return httpx.Response(200, json={"data": {"id": "j", "status": "running"}})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "j",
                        "status": "failed",
                        "error_message": "container build crashed",
                        "target": "local",
                    }
                },
            )

        with _patched_client(handler):
            result = runner.invoke(app, ["deploy", str(config_file), "--remote"])
        assert result.exit_code == 1
        assert "container build crashed" in result.output


# ── Mutually exclusive flags ────────────────────────────────────────────────


class TestModeFlags:
    def test_remote_and_local_both_set_exits(
        self, config_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(_http.ENV_TOKEN, "tok")
        result = runner.invoke(app, ["deploy", str(config_file), "--remote", "--local"])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.output


# ── Local mode preserved ────────────────────────────────────────────────────


class TestLocalPathPreserved:
    def test_explicit_local_skips_api_even_with_url_env(
        self,
        config_file: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Production users with AGENTBREEDER_URL set still get local on demand.

        ``--local`` is the explicit dev/offline escape hatch — it must NOT call
        the API even when the env says we should.
        """
        monkeypatch.setenv(_http.ENV_URL, "https://api.example.com")

        called: dict[str, bool] = {"engine": False, "http": False}

        class FakeEngine:
            def __init__(self, *, on_step: Any) -> None:
                self.on_step = on_step

            async def deploy(self, *, config_path: Any, target: str) -> Any:
                called["engine"] = True
                from types import SimpleNamespace

                ns = SimpleNamespace(
                    agent_name="test-agent",
                    version="1.0.0",
                    endpoint_url="http://local",
                )
                ns.model_dump = lambda: {  # type: ignore[method-assign]
                    "agent_name": "test-agent",
                    "version": "1.0.0",
                    "endpoint_url": "http://local",
                }
                return ns

        def fail_handler(req: httpx.Request) -> httpx.Response:
            called["http"] = True
            return httpx.Response(500)

        with (
            patch("cli.commands.deploy.DeployEngine", FakeEngine),
            _patched_client(fail_handler),
        ):
            result = runner.invoke(app, ["deploy", str(config_file), "--local"])

        assert result.exit_code == 0, result.output
        assert called["engine"] is True
        assert called["http"] is False  # explicit-local path must not hit the API

    def test_no_flags_no_url_uses_local(
        self,
        config_file: Path,
    ) -> None:
        called = {"engine": False}

        class FakeEngine:
            def __init__(self, *, on_step: Any) -> None:
                self.on_step = on_step

            async def deploy(self, *, config_path: Any, target: str) -> Any:
                called["engine"] = True
                from types import SimpleNamespace

                ns = SimpleNamespace(agent_name="x", version="1", endpoint_url="http://local")
                ns.model_dump = lambda: {  # type: ignore[method-assign]
                    "agent_name": "x",
                    "version": "1",
                    "endpoint_url": "http://local",
                }
                return ns

        with patch("cli.commands.deploy.DeployEngine", FakeEngine):
            result = runner.invoke(app, ["deploy", str(config_file)])

        assert result.exit_code == 0, result.output
        assert called["engine"] is True
