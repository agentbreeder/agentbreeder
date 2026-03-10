"""Tests for the garden teardown command."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

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


def _sample_registry() -> dict:
    return {
        "my-agent": {
            "name": "my-agent",
            "version": "1.0.0",
            "team": "eng",
            "framework": "langgraph",
            "status": "running",
        }
    }


class TestTeardownCommand:
    def test_teardown_agent_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_make_state()))
            with (
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["teardown", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.output

    def test_teardown_agent_not_found_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_make_state()))
            with (
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["teardown", "nonexistent", "--json"])
            assert result.exit_code == 1
            assert "not found" in result.output

    def test_teardown_shows_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with (
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["teardown", "nonexistent"])
            assert result.exit_code == 1
            assert "my-agent" in result.output

    def test_teardown_abort(self) -> None:
        """Answering 'n' to confirmation should abort."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with (
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["teardown", "my-agent"], input="n\n")
            assert result.exit_code == 0
            state = json.loads(state_file.read_text())
            assert state["agents"]["my-agent"]["status"] == "running"

    def test_teardown_confirm(self) -> None:
        """Answering 'y' should proceed with teardown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            registry_dir = Path(tmpdir) / "registry"
            registry_dir.mkdir()
            (registry_dir / "agents.json").write_text(json.dumps(_sample_registry()))
            with (
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch("cli.commands.teardown._teardown_container", return_value=True),
            ):
                result = runner.invoke(app, ["teardown", "my-agent"], input="y\n")
            assert result.exit_code == 0
            assert "Torn down" in result.output
            state = json.loads(state_file.read_text())
            assert state["agents"]["my-agent"]["status"] == "stopped"
            registry = json.loads((registry_dir / "agents.json").read_text())
            assert registry["my-agent"]["status"] == "stopped"

    def test_teardown_force(self) -> None:
        """--force should skip confirmation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            registry_dir = Path(tmpdir) / "registry"
            registry_dir.mkdir()
            (registry_dir / "agents.json").write_text(json.dumps(_sample_registry()))
            with (
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch("cli.commands.teardown._teardown_container", return_value=True),
            ):
                result = runner.invoke(app, ["teardown", "my-agent", "--force"])
            assert result.exit_code == 0
            assert "Torn down" in result.output

    def test_teardown_json_output(self) -> None:
        """--json should produce JSON and skip confirmation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            registry_dir = Path(tmpdir) / "registry"
            registry_dir.mkdir()
            (registry_dir / "agents.json").write_text(json.dumps(_sample_registry()))
            with (
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", registry_dir),
                patch("cli.commands.teardown._teardown_container", return_value=True),
            ):
                result = runner.invoke(app, ["teardown", "my-agent", "--json"])
            assert result.exit_code == 0
            assert '"agent": "my-agent"' in result.output
            assert '"status": "stopped"' in result.output

    def test_teardown_stopped_agent(self) -> None:
        """Tearing down an already-stopped agent should still update state."""
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
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", Path(tmpdir)),
            ):
                result = runner.invoke(app, ["teardown", "my-agent", "--force"])
            assert result.exit_code == 0

    def test_teardown_container_failure_still_updates_state(self) -> None:
        """If Docker teardown fails, state should still be updated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            with (
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", Path(tmpdir)),
                patch("cli.commands.teardown._teardown_container", return_value=False),
            ):
                result = runner.invoke(app, ["teardown", "my-agent", "--force"])
            assert result.exit_code == 0
            state = json.loads(state_file.read_text())
            assert state["agents"]["my-agent"]["status"] == "stopped"

    def test_teardown_no_registry_file(self) -> None:
        """Teardown should work even without a registry file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state_file.write_text(json.dumps(_sample_state()))
            empty_registry = Path(tmpdir) / "empty_registry"
            empty_registry.mkdir()
            with (
                patch("cli.commands.teardown.STATE_FILE", state_file),
                patch("cli.commands.teardown.REGISTRY_DIR", empty_registry),
                patch("cli.commands.teardown._teardown_container", return_value=False),
            ):
                result = runner.invoke(app, ["teardown", "my-agent", "--force"])
            assert result.exit_code == 0
