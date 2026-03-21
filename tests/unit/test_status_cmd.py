"""Tests for the agentbreeder status command."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def _make_state(agents: dict | None = None) -> dict:
    return {"agents": agents or {}, "next_port": 8080}


def _sample_state() -> dict:
    return _make_state(
        {
            "my-agent": {
                "port": 8080,
                "endpoint_url": "http://localhost:8080",
                "container_id": "abc123def456",
                "status": "running",
                "deployed_at": "2026-03-09T10:00:00",
            },
            "other-agent": {
                "port": 8081,
                "endpoint_url": "http://localhost:8081",
                "status": "stopped",
                "deployed_at": "2026-03-08T09:00:00",
            },
        }
    )


def _sample_registry() -> dict:
    return {
        "my-agent": {
            "name": "my-agent",
            "version": "1.0.0",
            "team": "eng",
            "framework": "langgraph",
            "model_primary": "gpt-4o",
            "status": "running",
        }
    }


class TestStatusCommand:
    def test_status_no_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_make_state()))
            with (
                patch("cli.commands.status.STATE_FILE", state_file),
                patch("cli.commands.status.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "No agents" in result.output

    def test_status_no_agents_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_make_state()))
            with (
                patch("cli.commands.status.STATE_FILE", state_file),
                patch("cli.commands.status.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0
        assert "[]" in result.output

    def test_status_all_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with (
                patch("cli.commands.status.STATE_FILE", state_file),
                patch("cli.commands.status.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "my-agent" in result.output
        assert "other-agent" in result.output

    def test_status_all_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with (
                patch("cli.commands.status.STATE_FILE", state_file),
                patch("cli.commands.status.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert len(output) == 2
        names = {a["name"] for a in output}
        assert "my-agent" in names
        assert "other-agent" in names

    def test_status_single_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            registry_dir = Path(tmpdir) / "registry"
            registry_dir.mkdir()
            (registry_dir / "agents.json").write_text(json.dumps(_sample_registry()))
            with (
                patch("cli.commands.status.STATE_FILE", state_file),
                patch("cli.commands.status.REGISTRY_DIR", registry_dir),
                patch("cli.commands.status._get_container_status", return_value=None),
            ):
                result = runner.invoke(app, ["status", "my-agent"])
        assert result.exit_code == 0
        assert "my-agent" in result.output
        assert "running" in result.output.lower()
        assert "localhost:8080" in result.output

    def test_status_single_agent_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            registry_dir = Path(tmpdir) / "registry"
            registry_dir.mkdir()
            (registry_dir / "agents.json").write_text(json.dumps(_sample_registry()))
            with (
                patch("cli.commands.status.STATE_FILE", state_file),
                patch("cli.commands.status.REGISTRY_DIR", registry_dir),
            ):
                result = runner.invoke(app, ["status", "my-agent", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["status"] == "running"
        assert output["framework"] == "langgraph"

    def test_status_agent_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with (
                patch("cli.commands.status.STATE_FILE", state_file),
                patch("cli.commands.status.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["status", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_status_agent_not_found_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with (
                patch("cli.commands.status.STATE_FILE", state_file),
                patch("cli.commands.status.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["status", "nonexistent", "--json"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_status_shows_registry_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            registry_dir = Path(tmpdir) / "registry"
            registry_dir.mkdir()
            (registry_dir / "agents.json").write_text(json.dumps(_sample_registry()))
            with (
                patch("cli.commands.status.STATE_FILE", state_file),
                patch("cli.commands.status.REGISTRY_DIR", registry_dir),
                patch("cli.commands.status._get_container_status", return_value=None),
            ):
                result = runner.invoke(app, ["status", "my-agent"])
        assert result.exit_code == 0
        assert "langgraph" in result.output
        assert "eng" in result.output

    def test_status_no_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "nonexistent" / "state.json"
            with (
                patch("cli.commands.status.STATE_FILE", state_file),
                patch("cli.commands.status.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "No agents" in result.output

    def test_format_time(self) -> None:
        from cli.commands.status import _format_time

        assert _format_time("2026-03-09T10:00:00") == "2026-03-09 10:00:00"
        assert _format_time("") == "N/A"
        assert _format_time("invalid") == "invalid"
