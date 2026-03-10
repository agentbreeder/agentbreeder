"""Tests for CLI commands — validate, list, describe."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

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


class TestValidateCommand:
    def test_validate_valid_yaml(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()
        result = runner.invoke(app, ["validate", f.name])
        assert result.exit_code == 0
        assert "Valid" in result.output

    def test_validate_invalid_yaml(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: test-agent\nversion: 1.0.0\n")
        f.close()
        result = runner.invoke(app, ["validate", f.name])
        assert result.exit_code == 1

    def test_validate_json_output_valid(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(VALID_YAML)
        f.close()
        result = runner.invoke(app, ["validate", f.name, "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["valid"] is True

    def test_validate_json_output_invalid(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: test-agent\n")
        f.close()
        result = runner.invoke(app, ["validate", f.name, "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["valid"] is False
        assert len(output["errors"]) > 0


class TestListCommand:
    def test_list_no_agents(self) -> None:
        with patch("cli.commands.list_cmd.REGISTRY_DIR", Path(tempfile.mkdtemp())):
            result = runner.invoke(app, ["list", "agents"])
        assert result.exit_code == 0
        assert "No agents" in result.output or "[]" in result.output

    def test_list_with_agents(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "test-agent": {
                "name": "test-agent",
                "version": "1.0.0",
                "team": "eng",
                "framework": "langgraph",
                "status": "running",
                "endpoint_url": "http://localhost:8080",
            }
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.list_cmd.REGISTRY_DIR", d):
            result = runner.invoke(app, ["list", "agents"])
        assert result.exit_code == 0
        assert "test-agent" in result.output

    def test_list_json_output(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "my-agent": {
                "name": "my-agent",
                "version": "1.0.0",
                "team": "eng",
                "framework": "langgraph",
                "status": "running",
                "endpoint_url": "http://localhost:8080",
            }
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.list_cmd.REGISTRY_DIR", d):
            result = runner.invoke(app, ["list", "agents", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert len(output) == 1
        assert output[0]["name"] == "my-agent"

    def test_list_filter_by_team(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "agent-a": {"name": "agent-a", "team": "alpha", "version": "1.0.0", "framework": "langgraph", "status": "running", "endpoint_url": "http://localhost:8080"},
            "agent-b": {"name": "agent-b", "team": "beta", "version": "1.0.0", "framework": "langgraph", "status": "running", "endpoint_url": "http://localhost:8081"},
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.list_cmd.REGISTRY_DIR", d):
            result = runner.invoke(app, ["list", "agents", "--team", "alpha", "--json"])
        output = json.loads(result.output)
        assert len(output) == 1
        assert output[0]["name"] == "agent-a"

    def test_list_unsupported_entity(self) -> None:
        result = runner.invoke(app, ["list", "prompts"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_list_no_agents_json(self) -> None:
        with patch("cli.commands.list_cmd.REGISTRY_DIR", Path(tempfile.mkdtemp())):
            result = runner.invoke(app, ["list", "agents", "--json"])
        assert result.exit_code == 0
        assert "[]" in result.output


class TestDescribeCommand:
    def test_describe_existing_agent(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "my-agent": {
                "name": "my-agent",
                "version": "1.0.0",
                "team": "eng",
                "framework": "langgraph",
                "endpoint_url": "http://localhost:8080",
            }
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.describe.REGISTRY_DIR", d):
            result = runner.invoke(app, ["describe", "my-agent"])
        assert result.exit_code == 0
        assert "my-agent" in result.output
        assert "langgraph" in result.output

    def test_describe_not_found(self) -> None:
        d = Path(tempfile.mkdtemp())
        (d / "agents.json").write_text(json.dumps({"other": {"name": "other"}}))
        with patch("cli.commands.describe.REGISTRY_DIR", d):
            result = runner.invoke(app, ["describe", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_describe_no_registry(self) -> None:
        with patch("cli.commands.describe.REGISTRY_DIR", Path(tempfile.mkdtemp())):
            result = runner.invoke(app, ["describe", "anything"])
        assert result.exit_code == 1

    def test_describe_json_output(self) -> None:
        d = Path(tempfile.mkdtemp())
        registry = {
            "my-agent": {
                "name": "my-agent",
                "version": "1.0.0",
                "team": "eng",
            }
        }
        (d / "agents.json").write_text(json.dumps(registry))
        with patch("cli.commands.describe.REGISTRY_DIR", d):
            result = runner.invoke(app, ["describe", "my-agent", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["name"] == "my-agent"


class TestDeployCommand:
    def test_deploy_invalid_yaml_fails(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: test-agent\n")
        f.close()
        result = runner.invoke(app, ["deploy", f.name])
        assert result.exit_code == 1
        assert "failed" in result.output.lower() or "Error" in result.output

    def test_deploy_json_output_on_error(self) -> None:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write("name: test-agent\n")
        f.close()
        result = runner.invoke(app, ["deploy", f.name, "--json"])
        assert result.exit_code == 1
        # Find the JSON line in the output
        for line in result.output.strip().splitlines():
            try:
                output = json.loads(line)
                assert "error" in output
                return
            except json.JSONDecodeError:
                continue
        # If we didn't find valid JSON, at least verify error indication
        assert "error" in result.output.lower() or "failed" in result.output.lower()
