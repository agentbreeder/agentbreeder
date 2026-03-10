"""Tests for the garden logs command."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def _make_state(agents: dict | None = None) -> dict:
    return {"agents": agents or {}, "next_port": 8080}


def _sample_state() -> dict:
    return _make_state({
        "my-agent": {
            "port": 8080,
            "endpoint_url": "http://localhost:8080",
            "container_id": "abc123def456",
            "container_name": "garden-my-agent",
            "status": "running",
            "deployed_at": "2026-03-09T10:00:00",
        }
    })


class TestLogsCommand:
    def test_logs_agent_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_make_state()))
            with patch("cli.commands.logs.STATE_FILE", state_file):
                result = runner.invoke(app, ["logs", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_logs_agent_not_found_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_make_state()))
            with patch("cli.commands.logs.STATE_FILE", state_file):
                result = runner.invoke(app, ["logs", "nonexistent", "--json"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_logs_shows_available_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with patch("cli.commands.logs.STATE_FILE", state_file):
                result = runner.invoke(app, ["logs", "nonexistent"])
        assert result.exit_code == 1
        assert "my-agent" in result.output

    def test_logs_no_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "nonexistent" / "state.json"
            with patch("cli.commands.logs.STATE_FILE", state_file):
                result = runner.invoke(app, ["logs", "my-agent"])
        assert result.exit_code == 1

    @patch("cli.commands.logs.asyncio")
    def test_logs_success(self, mock_asyncio: MagicMock) -> None:
        mock_asyncio.run.return_value = [
            "2026-03-09T10:00:01 INFO Starting agent...",
            "2026-03-09T10:00:02 INFO Agent ready.",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with patch("cli.commands.logs.STATE_FILE", state_file):
                result = runner.invoke(app, ["logs", "my-agent"])
        assert result.exit_code == 0
        assert "Starting agent" in result.output
        assert "Agent ready" in result.output

    @patch("cli.commands.logs.asyncio")
    def test_logs_json_output(self, mock_asyncio: MagicMock) -> None:
        mock_asyncio.run.return_value = ["line1", "line2"]
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with patch("cli.commands.logs.STATE_FILE", state_file):
                result = runner.invoke(app, ["logs", "my-agent", "--json"])
        assert result.exit_code == 0
        assert '"agent": "my-agent"' in result.output
        assert '"count": 2' in result.output

    @patch("cli.commands.logs.asyncio")
    def test_logs_line_limit(self, mock_asyncio: MagicMock) -> None:
        mock_asyncio.run.return_value = [f"line {i}" for i in range(100)]
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with patch("cli.commands.logs.STATE_FILE", state_file):
                result = runner.invoke(app, ["logs", "my-agent", "--lines", "5", "--json"])
        assert result.exit_code == 0
        assert '"count": 5' in result.output

    def test_logs_stopped_agent_warns(self) -> None:
        state = _make_state({
            "my-agent": {
                "port": 8080,
                "status": "stopped",
                "endpoint_url": "http://localhost:8080",
            }
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(state))
            with (
                patch("cli.commands.logs.STATE_FILE", state_file),
                patch("cli.commands.logs.asyncio") as mock_asyncio,
            ):
                mock_asyncio.run.return_value = ["old log line"]
                result = runner.invoke(app, ["logs", "my-agent"])
        assert result.exit_code == 0
        assert "stopped" in result.output.lower()

    @patch("cli.commands.logs.asyncio")
    def test_logs_color_coding(self, mock_asyncio: MagicMock) -> None:
        mock_asyncio.run.return_value = [
            "2026-03-09 ERROR something broke",
            "2026-03-09 WARNING low memory",
            "2026-03-09 INFO all good",
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with patch("cli.commands.logs.STATE_FILE", state_file):
                result = runner.invoke(app, ["logs", "my-agent"])
        assert result.exit_code == 0
        assert "something broke" in result.output
        assert "low memory" in result.output

    def test_parse_since_valid(self) -> None:
        from cli.commands.logs import _parse_since

        assert _parse_since("5m") is not None
        assert _parse_since("1h") is not None
        assert _parse_since("2d") is not None
        assert _parse_since("30s") is not None

    def test_parse_since_invalid(self) -> None:
        from cli.commands.logs import _parse_since

        assert _parse_since("abc") is None
        assert _parse_since("") is None
        assert _parse_since("5x") is None
