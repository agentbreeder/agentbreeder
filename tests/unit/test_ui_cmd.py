"""Unit tests for cli/commands/ui.py."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DOCKER_OK = CompletedProcess(args=[], returncode=0, stdout="Docker version 24", stderr="")
_DOCKER_FAIL = CompletedProcess(args=[], returncode=1, stdout="", stderr="Cannot connect")


def _mock_run(returncode: int = 0) -> CompletedProcess:
    return CompletedProcess(args=[], returncode=returncode, stdout="", stderr="")


# ---------------------------------------------------------------------------
# _check_docker
# ---------------------------------------------------------------------------


def test_check_docker_not_found():
    """Exit 1 with helpful message when docker binary is missing."""
    with patch("cli.commands.ui.shutil.which", return_value=None):
        result = runner.invoke(app, ["ui"])
    assert result.exit_code == 1
    assert "Docker not found" in result.output


def test_check_docker_daemon_not_running():
    """Exit 1 with helpful message when daemon is not running."""
    with (
        patch("cli.commands.ui.shutil.which", return_value="/usr/bin/docker"),
        patch("cli.commands.ui.subprocess.run", return_value=_DOCKER_FAIL),
    ):
        result = runner.invoke(app, ["ui"])
    assert result.exit_code == 1
    assert "not running" in result.output


def test_check_docker_compose_missing():
    """Exit 1 when docker compose v2 is not available."""
    with (
        patch("cli.commands.ui.shutil.which", return_value="/usr/bin/docker"),
        patch(
            "cli.commands.ui.subprocess.run",
            side_effect=[
                _DOCKER_OK,  # docker info
                _mock_run(returncode=1),  # docker compose version — fails
            ],
        ),
    ):
        result = runner.invoke(app, ["ui"])
    assert result.exit_code == 1
    assert "Compose" in result.output


# ---------------------------------------------------------------------------
# _write_compose_file
# ---------------------------------------------------------------------------


def test_write_compose_file_creates_file(tmp_path: Path):
    """Compose file is written to ~/.agentbreeder/docker-compose.ui.yml."""
    from cli.commands import ui as ui_module

    original_path = ui_module._COMPOSE_PATH
    original_dir = ui_module._AGENTBREEDER_DIR
    try:
        ui_module._AGENTBREEDER_DIR = tmp_path
        ui_module._COMPOSE_PATH = tmp_path / "docker-compose.ui.yml"
        result_path = ui_module._write_compose_file(api_port=8000, dashboard_port=3001)
        assert result_path.exists()
        content = result_path.read_text()
        assert "8000:8000" in content
        assert "3001:3001" in content
        assert "rajits/agentbreeder-api:latest" in content
        assert "rajits/agentbreeder-dashboard:latest" in content
    finally:
        ui_module._AGENTBREEDER_DIR = original_dir
        ui_module._COMPOSE_PATH = original_path


def test_write_compose_file_custom_ports(tmp_path: Path):
    """Custom ports are substituted correctly."""
    from cli.commands import ui as ui_module

    original_path = ui_module._COMPOSE_PATH
    original_dir = ui_module._AGENTBREEDER_DIR
    try:
        ui_module._AGENTBREEDER_DIR = tmp_path
        ui_module._COMPOSE_PATH = tmp_path / "docker-compose.ui.yml"
        ui_module._write_compose_file(api_port=9000, dashboard_port=4000)
        content = (tmp_path / "docker-compose.ui.yml").read_text()
        assert "9000:8000" in content
        assert "4000:3001" in content
    finally:
        ui_module._AGENTBREEDER_DIR = original_dir
        ui_module._COMPOSE_PATH = original_path


# ---------------------------------------------------------------------------
# _poll_health
# ---------------------------------------------------------------------------


def test_poll_health_returns_true_on_200():
    """Returns True immediately when health endpoint responds 200."""
    from cli.commands.ui import _poll_health

    mock_resp = MagicMock()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.status = 200

    with patch("cli.commands.ui.urllib.request.urlopen", return_value=mock_resp):
        assert _poll_health(api_port=8000, timeout=5) is True


def test_poll_health_returns_false_on_timeout():
    """Returns False when health endpoint never responds within timeout."""
    import urllib.error

    from cli.commands.ui import _poll_health

    with (
        patch(
            "cli.commands.ui.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ),
        patch("cli.commands.ui.time.sleep"),
        patch("cli.commands.ui.time.monotonic", side_effect=[0, 0, 91]),
    ):
        assert _poll_health(api_port=8000, timeout=90) is False


# ---------------------------------------------------------------------------
# Full command — happy path
# ---------------------------------------------------------------------------


def _all_ok_run(compose_path_placeholder):
    """Return a side_effect list for subprocess.run covering the happy path."""
    return [
        _DOCKER_OK,  # docker info
        _DOCKER_OK,  # docker compose version
        _DOCKER_OK,  # docker compose pull
        _DOCKER_OK,  # docker compose up -d
    ]


def test_ui_happy_path(tmp_path: Path):
    """Full happy-path: Docker ok, pull ok, stack starts, health passes."""
    from cli.commands import ui as ui_module

    original_path = ui_module._COMPOSE_PATH
    original_dir = ui_module._AGENTBREEDER_DIR
    try:
        ui_module._AGENTBREEDER_DIR = tmp_path
        ui_module._COMPOSE_PATH = tmp_path / "docker-compose.ui.yml"

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with (
            patch("cli.commands.ui.shutil.which", return_value="/usr/bin/docker"),
            patch("cli.commands.ui.subprocess.run", side_effect=_all_ok_run(None)),
            patch("cli.commands.ui.urllib.request.urlopen", return_value=mock_resp),
        ):
            result = runner.invoke(app, ["ui", "--no-pull"])

        assert result.exit_code == 0
        assert "localhost:3001" in result.output
        assert "localhost:8000" in result.output
    finally:
        ui_module._AGENTBREEDER_DIR = original_dir
        ui_module._COMPOSE_PATH = original_path


def test_ui_stack_start_failure(tmp_path: Path):
    """Exit 1 with helpful message when docker compose up fails."""
    from cli.commands import ui as ui_module

    original_path = ui_module._COMPOSE_PATH
    original_dir = ui_module._AGENTBREEDER_DIR
    try:
        ui_module._AGENTBREEDER_DIR = tmp_path
        ui_module._COMPOSE_PATH = tmp_path / "docker-compose.ui.yml"

        with (
            patch("cli.commands.ui.shutil.which", return_value="/usr/bin/docker"),
            patch(
                "cli.commands.ui.subprocess.run",
                side_effect=[
                    _DOCKER_OK,  # docker info
                    _DOCKER_OK,  # docker compose version
                    _mock_run(1),  # docker compose up -d — fails
                ],
            ),
        ):
            result = runner.invoke(app, ["ui", "--no-pull"])

        assert result.exit_code == 1
        assert "Failed to start" in result.output
    finally:
        ui_module._AGENTBREEDER_DIR = original_dir
        ui_module._COMPOSE_PATH = original_path


def test_ui_health_timeout(tmp_path: Path):
    """Exit 1 with helpful message when API health never responds."""
    import urllib.error

    from cli.commands import ui as ui_module

    original_path = ui_module._COMPOSE_PATH
    original_dir = ui_module._AGENTBREEDER_DIR
    try:
        ui_module._AGENTBREEDER_DIR = tmp_path
        ui_module._COMPOSE_PATH = tmp_path / "docker-compose.ui.yml"

        with (
            patch("cli.commands.ui.shutil.which", return_value="/usr/bin/docker"),
            patch(
                "cli.commands.ui.subprocess.run",
                side_effect=[
                    _DOCKER_OK,  # docker info
                    _DOCKER_OK,  # docker compose version
                    _DOCKER_OK,  # docker compose up -d
                ],
            ),
            patch(
                "cli.commands.ui.urllib.request.urlopen",
                side_effect=urllib.error.URLError("refused"),
            ),
            patch("cli.commands.ui.time.sleep"),
            patch("cli.commands.ui.time.monotonic", side_effect=[0, 0, 91]),
        ):
            result = runner.invoke(app, ["ui", "--no-pull"])

        assert result.exit_code == 1
        assert "healthy" in result.output.lower() or "health" in result.output.lower()
    finally:
        ui_module._AGENTBREEDER_DIR = original_dir
        ui_module._COMPOSE_PATH = original_path


# ---------------------------------------------------------------------------
# Custom ports
# ---------------------------------------------------------------------------


def test_ui_custom_ports(tmp_path: Path):
    """Custom --port and --api-port are reflected in output."""
    from cli.commands import ui as ui_module

    original_path = ui_module._COMPOSE_PATH
    original_dir = ui_module._AGENTBREEDER_DIR
    try:
        ui_module._AGENTBREEDER_DIR = tmp_path
        ui_module._COMPOSE_PATH = tmp_path / "docker-compose.ui.yml"

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with (
            patch("cli.commands.ui.shutil.which", return_value="/usr/bin/docker"),
            patch(
                "cli.commands.ui.subprocess.run",
                side_effect=[_DOCKER_OK, _DOCKER_OK, _DOCKER_OK],
            ),
            patch("cli.commands.ui.urllib.request.urlopen", return_value=mock_resp),
        ):
            result = runner.invoke(
                app, ["ui", "--no-pull", "--port", "4000", "--api-port", "9000"]
            )

        assert result.exit_code == 0
        assert "localhost:4000" in result.output
        assert "localhost:9000" in result.output
    finally:
        ui_module._AGENTBREEDER_DIR = original_dir
        ui_module._COMPOSE_PATH = original_path
