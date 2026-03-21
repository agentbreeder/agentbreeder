"""Tests for agentbreeder search CLI command."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


class TestSearchCommand:
    def test_search_no_results(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "my-agent": {
                "name": "my-agent",
                "description": "A test agent",
                "team": "eng",
                "framework": "langgraph",
                "status": "running",
                "tags": [],
            }
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.search.REGISTRY_DIR", d):
            result = runner.invoke(app, ["search", "nonexistent"])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_search_by_name(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "customer-agent": {
                "name": "customer-agent",
                "description": "Support agent",
                "team": "support",
                "framework": "langgraph",
                "status": "running",
                "tags": ["support"],
            },
            "data-agent": {
                "name": "data-agent",
                "description": "Data pipeline",
                "team": "data",
                "framework": "crewai",
                "status": "running",
                "tags": [],
            },
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.search.REGISTRY_DIR", d):
            result = runner.invoke(app, ["search", "customer"])
        assert result.exit_code == 0
        assert "customer-agent" in result.output

    def test_search_by_team(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "agent-a": {
                "name": "agent-a",
                "description": "",
                "team": "platform",
                "framework": "langgraph",
                "status": "running",
                "tags": [],
            },
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.search.REGISTRY_DIR", d):
            result = runner.invoke(app, ["search", "platform"])
        assert result.exit_code == 0
        assert "agent-a" in result.output

    def test_search_by_tag(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "tagged-agent": {
                "name": "tagged-agent",
                "description": "",
                "team": "eng",
                "framework": "langgraph",
                "status": "running",
                "tags": ["production", "critical"],
            },
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.search.REGISTRY_DIR", d):
            result = runner.invoke(app, ["search", "production"])
        assert result.exit_code == 0
        assert "tagged-agent" in result.output

    def test_search_json_output(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "my-agent": {
                "name": "my-agent",
                "description": "test",
                "team": "eng",
                "framework": "langgraph",
                "status": "running",
                "tags": [],
            },
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.search.REGISTRY_DIR", d):
            result = runner.invoke(app, ["search", "my-agent", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert len(output) == 1
        assert output[0]["name"] == "my-agent"

    def test_search_no_registry(self) -> None:
        with patch("cli.commands.search.REGISTRY_DIR", Path(tempfile.mkdtemp())):
            result = runner.invoke(app, ["search", "anything"])
        assert result.exit_code == 0
        assert "No results" in result.output
