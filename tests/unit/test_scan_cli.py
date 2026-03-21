"""Tests for agentbreeder scan CLI command."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


class TestScanCommand:
    @patch("cli.commands.scan.REGISTRY_DIR", Path(tempfile.mkdtemp()))
    @patch("cli.commands.scan.LiteLLMConnector")
    @patch("cli.commands.scan.MCPScanner")
    def test_scan_discovers_tools(self, MockMCP, MockLiteLLM) -> None:
        mock_mcp = MockMCP.return_value
        mock_mcp.is_available = AsyncMock(return_value=True)
        mock_mcp.scan = AsyncMock(
            return_value=[
                {"name": "fs-server", "tool_type": "mcp_server", "source": "mcp_scanner"},
            ]
        )

        mock_litellm = MockLiteLLM.return_value
        mock_litellm.is_available = AsyncMock(return_value=False)

        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 0
        assert "1 tool(s)" in result.output

    @patch("cli.commands.scan.REGISTRY_DIR", Path(tempfile.mkdtemp()))
    @patch("cli.commands.scan.LiteLLMConnector")
    @patch("cli.commands.scan.MCPScanner")
    def test_scan_discovers_models(self, MockMCP, MockLiteLLM) -> None:
        mock_mcp = MockMCP.return_value
        mock_mcp.is_available = AsyncMock(return_value=False)

        mock_litellm = MockLiteLLM.return_value
        mock_litellm.is_available = AsyncMock(return_value=True)
        mock_litellm.scan = AsyncMock(
            return_value=[
                {"name": "gpt-4o", "provider": "openai", "source": "litellm"},
            ]
        )

        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 0
        assert "1 model(s)" in result.output

    @patch("cli.commands.scan.REGISTRY_DIR", Path(tempfile.mkdtemp()))
    @patch("cli.commands.scan.LiteLLMConnector")
    @patch("cli.commands.scan.MCPScanner")
    def test_scan_json_output(self, MockMCP, MockLiteLLM) -> None:
        mock_mcp = MockMCP.return_value
        mock_mcp.is_available = AsyncMock(return_value=True)
        mock_mcp.scan = AsyncMock(
            return_value=[
                {"name": "fs", "tool_type": "mcp_server", "source": "mcp_scanner"},
            ]
        )

        mock_litellm = MockLiteLLM.return_value
        mock_litellm.is_available = AsyncMock(return_value=True)
        mock_litellm.scan = AsyncMock(
            return_value=[
                {"name": "gpt-4o", "provider": "openai", "source": "litellm"},
            ]
        )

        result = runner.invoke(app, ["scan", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert len(output["tools"]) == 1
        assert len(output["models"]) == 1

    @patch("cli.commands.scan.REGISTRY_DIR", Path(tempfile.mkdtemp()))
    @patch("cli.commands.scan.LiteLLMConnector")
    @patch("cli.commands.scan.MCPScanner")
    def test_scan_nothing_available(self, MockMCP, MockLiteLLM) -> None:
        mock_mcp = MockMCP.return_value
        mock_mcp.is_available = AsyncMock(return_value=False)
        mock_litellm = MockLiteLLM.return_value
        mock_litellm.is_available = AsyncMock(return_value=False)

        result = runner.invoke(app, ["scan"])
        assert result.exit_code == 0
        assert "0 resource(s)" in result.output


class TestListToolsCommand:
    def test_list_tools_no_registry(self) -> None:
        with patch("cli.commands.list_cmd.REGISTRY_DIR", Path(tempfile.mkdtemp())):
            result = runner.invoke(app, ["list", "tools"])
        assert result.exit_code == 0
        assert "No tools" in result.output

    def test_list_tools_with_data(self) -> None:
        d = Path(tempfile.mkdtemp())
        tools = {
            "fs-server": {
                "name": "fs-server",
                "tool_type": "mcp_server",
                "description": "File system",
                "source": "mcp_scanner",
                "endpoint": None,
            }
        }
        (d / "tools.json").write_text(json.dumps(tools))
        with patch("cli.commands.list_cmd.REGISTRY_DIR", d):
            result = runner.invoke(app, ["list", "tools"])
        assert result.exit_code == 0
        assert "fs-server" in result.output

    def test_list_tools_json(self) -> None:
        d = Path(tempfile.mkdtemp())
        tools = {
            "t1": {
                "name": "t1",
                "tool_type": "mcp_server",
                "description": "test",
                "source": "manual",
                "endpoint": None,
            }
        }
        (d / "tools.json").write_text(json.dumps(tools))
        with patch("cli.commands.list_cmd.REGISTRY_DIR", d):
            result = runner.invoke(app, ["list", "tools", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert len(output) == 1


class TestListModelsCommand:
    def test_list_models_no_registry(self) -> None:
        with patch("cli.commands.list_cmd.REGISTRY_DIR", Path(tempfile.mkdtemp())):
            result = runner.invoke(app, ["list", "models"])
        assert result.exit_code == 0
        assert "No models" in result.output

    def test_list_models_with_data(self) -> None:
        d = Path(tempfile.mkdtemp())
        models = {
            "gpt-4o": {
                "name": "gpt-4o",
                "provider": "openai",
                "description": "GPT-4o",
                "source": "litellm",
            }
        }
        (d / "models.json").write_text(json.dumps(models))
        with patch("cli.commands.list_cmd.REGISTRY_DIR", d):
            result = runner.invoke(app, ["list", "models"])
        assert result.exit_code == 0
        assert "gpt-4o" in result.output

    def test_list_models_json(self) -> None:
        d = Path(tempfile.mkdtemp())
        models = {
            "m1": {"name": "m1", "provider": "openai", "description": "test", "source": "litellm"}
        }
        (d / "models.json").write_text(json.dumps(models))
        with patch("cli.commands.list_cmd.REGISTRY_DIR", d):
            result = runner.invoke(app, ["list", "models", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert len(output) == 1
