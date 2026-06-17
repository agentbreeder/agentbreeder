"""Runtime-routing tests for `agentbreeder down` (issue #560, bug #7).

Verifies that when the runtime cache pins podman, the `down` command issues
podman/podman-compose calls — never `docker`. Previously this command
hardcoded the docker socket and silently misreported "No AgentBreeder
services are running" when podman or nerdctl was in use.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cli.commands import down as down_module


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the runtime cache file into tmp_path so tests don't leak."""
    cache = tmp_path / "quickstart-runtime.json"
    monkeypatch.setattr(
        "cli.commands.quickstart.RUNTIME_CACHE_PATH",
        cache,
        raising=True,
    )
    return cache


def _make_completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ── _resolve_runtime: cache vs live detection ───────────────────────────────


def test_resolve_runtime_prefers_cache(isolated_cache: Path) -> None:
    """A cached podman entry must win over live detection."""
    isolated_cache.write_text(json.dumps({"runtime": "podman", "compose": "podman compose"}))

    with patch("cli.commands.quickstart._detect_runtime") as mock_detect:
        runtime = down_module._resolve_runtime()
        mock_detect.assert_not_called()

    assert runtime == ("podman", ["podman", "compose"])


def test_resolve_runtime_falls_back_to_detect(isolated_cache: Path) -> None:
    """No cache → fall back to `_detect_runtime()`."""
    assert not isolated_cache.exists()

    with patch(
        "cli.commands.quickstart._detect_runtime",
        return_value=("nerdctl", "nerdctl compose"),
    ):
        runtime = down_module._resolve_runtime()

    assert runtime == ("nerdctl", ["nerdctl", "compose"])


def test_resolve_runtime_returns_none_when_nothing_installed(isolated_cache: Path) -> None:
    with patch("cli.commands.quickstart._detect_runtime", return_value=None):
        assert down_module._resolve_runtime() is None


# ── _qs_is_running + _stop_qs: route via the right binary ──────────────────


def test_qs_is_running_uses_podman_when_cached(isolated_cache: Path) -> None:
    """`docker ps` must NOT be called when podman is the active runtime."""
    isolated_cache.write_text(json.dumps({"runtime": "podman", "compose": "podman compose"}))

    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        return _make_completed(stdout="agentbreeder-qs-api\n")

    with patch("cli.commands.down.subprocess.run", side_effect=fake_run):
        assert down_module._qs_is_running() is True

    assert captured["cmd"][0] == "podman"
    assert "docker" not in captured["cmd"]


def test_qs_is_running_uses_docker_when_cached(isolated_cache: Path) -> None:
    isolated_cache.write_text(json.dumps({"runtime": "docker", "compose": "docker compose"}))

    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        return _make_completed(stdout="")

    with patch("cli.commands.down.subprocess.run", side_effect=fake_run):
        down_module._qs_is_running()

    assert captured["cmd"][0] == "docker"


def test_stop_qs_uses_podman_compose_with_volumes(isolated_cache: Path) -> None:
    """`--clean` (volumes=True) must drive `podman compose ... down --volumes`."""
    isolated_cache.write_text(json.dumps({"runtime": "podman", "compose": "podman compose"}))

    mock_run = MagicMock(return_value=_make_completed())
    with patch("cli.commands.down.subprocess.run", mock_run):
        rc = down_module._stop_qs(volumes=True)

    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[:2] == ["podman", "compose"]
    assert "--project-name" in cmd
    assert cmd[-2:] == ["down", "--volumes"]


def test_stop_qs_uses_docker_compose_when_cached_as_docker(isolated_cache: Path) -> None:
    isolated_cache.write_text(json.dumps({"runtime": "docker", "compose": "docker compose"}))

    mock_run = MagicMock(return_value=_make_completed())
    with patch("cli.commands.down.subprocess.run", mock_run):
        down_module._stop_qs(volumes=False)

    cmd = mock_run.call_args[0][0]
    assert cmd[:2] == ["docker", "compose"]
    assert "--volumes" not in cmd


def test_stop_qs_handles_missing_runtime(isolated_cache: Path) -> None:
    """No cache + no detectable runtime → non-zero returncode, no crash."""
    with patch("cli.commands.quickstart._detect_runtime", return_value=None):
        rc = down_module._stop_qs(volumes=False)
    assert rc != 0


# ── Cache lifecycle ────────────────────────────────────────────────────────


def test_clean_clears_runtime_cache(isolated_cache: Path) -> None:
    """`down --clean` removes the cache so the next bootstrap re-detects."""
    isolated_cache.write_text(json.dumps({"runtime": "podman", "compose": "podman compose"}))
    assert isolated_cache.exists()

    from cli.commands.quickstart import _clear_runtime_cache

    _clear_runtime_cache()
    assert not isolated_cache.exists()


def test_save_and_load_runtime_cache(isolated_cache: Path) -> None:
    from cli.commands.quickstart import _load_runtime_cache, _save_runtime_cache

    _save_runtime_cache("nerdctl", "nerdctl compose")
    assert isolated_cache.exists()
    assert _load_runtime_cache() == ("nerdctl", "nerdctl compose")
