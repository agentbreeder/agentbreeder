"""Tests for MCP Server Auto-Discovery connector."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from connectors.mcp_scanner.scanner import MCPScanner, _extract_package_name


def _mock_response(status_code: int = 200, json_data=None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or (json.dumps(json_data) if json_data else "")
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("not json")
    return resp


def _mock_client(get_return=None, get_side_effect=None):
    client = AsyncMock()
    if get_side_effect:
        client.get = AsyncMock(side_effect=get_side_effect)
    else:
        client.get = AsyncMock(return_value=get_return)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestMCPScannerConfigFile:
    def test_scan_config_file(self) -> None:
        d = Path(tempfile.mkdtemp())
        config = {
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    "description": "Read/write files",
                },
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                },
            }
        }
        config_path = d / ".mcp.json"
        config_path.write_text(json.dumps(config))

        scanner = MCPScanner(config_paths=[config_path], probe_ports=[])
        results = scanner._scan_config_file(config_path)

        assert len(results) == 2
        fs = next(r for r in results if r["name"] == "filesystem")
        assert fs["description"] == "Read/write files"
        assert fs["tool_type"] == "mcp_server"
        assert fs["source"] == "mcp_scanner"
        assert fs["schema_definition"]["package"] == "@modelcontextprotocol/server-filesystem"

        gh = next(r for r in results if r["name"] == "github")
        assert gh["description"] == "MCP server: github"

    def test_scan_missing_config(self) -> None:
        scanner = MCPScanner(config_paths=[], probe_ports=[])
        results = scanner._scan_config_file(Path("/nonexistent/.mcp.json"))
        assert results == []

    def test_scan_invalid_json(self) -> None:
        d = Path(tempfile.mkdtemp())
        config_path = d / ".mcp.json"
        config_path.write_text("not json{{{")

        scanner = MCPScanner(config_paths=[config_path], probe_ports=[])
        results = scanner._scan_config_file(config_path)
        assert results == []

    def test_scan_empty_servers(self) -> None:
        d = Path(tempfile.mkdtemp())
        config_path = d / ".mcp.json"
        config_path.write_text(json.dumps({"mcpServers": {}}))

        scanner = MCPScanner(config_paths=[config_path], probe_ports=[])
        results = scanner._scan_config_file(config_path)
        assert results == []


class TestMCPScannerProbe:
    @pytest.mark.asyncio
    async def test_probe_healthy_server_json(self) -> None:
        scanner = MCPScanner(config_paths=[], probe_ports=[9999])
        resp = _mock_response(200, json_data={"name": "test-mcp", "description": "Test server"})
        client = _mock_client(get_return=resp)

        with patch("connectors.mcp_scanner.scanner.httpx.AsyncClient", return_value=client):
            results = await scanner._probe_ports_for_mcp()

        assert len(results) == 1
        assert results[0]["name"] == "test-mcp"
        assert results[0]["endpoint"] == "http://localhost:9999"

    @pytest.mark.asyncio
    async def test_probe_unreachable_port(self) -> None:
        scanner = MCPScanner(config_paths=[], probe_ports=[9999])
        client = _mock_client(get_side_effect=httpx.ConnectError("refused"))

        with patch("connectors.mcp_scanner.scanner.httpx.AsyncClient", return_value=client):
            results = await scanner._probe_ports_for_mcp()
        assert results == []

    @pytest.mark.asyncio
    async def test_probe_non_json_response(self) -> None:
        scanner = MCPScanner(config_paths=[], probe_ports=[9999])
        resp = _mock_response(200, text="OK")  # json_data=None -> json() raises
        client = _mock_client(get_return=resp)

        with patch("connectors.mcp_scanner.scanner.httpx.AsyncClient", return_value=client):
            results = await scanner._probe_ports_for_mcp()

        assert len(results) == 1
        assert results[0]["name"] == "mcp-9999"


class TestMCPScannerFull:
    @pytest.mark.asyncio
    async def test_full_scan_config_only(self) -> None:
        d = Path(tempfile.mkdtemp())
        config = {
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                    "description": "FS server",
                }
            }
        }
        (d / ".mcp.json").write_text(json.dumps(config))

        scanner = MCPScanner(config_paths=[d / ".mcp.json"], probe_ports=[])
        results = await scanner.scan()

        assert len(results) == 1
        assert results[0]["name"] == "filesystem"

    @pytest.mark.asyncio
    async def test_is_available(self) -> None:
        scanner = MCPScanner()
        assert await scanner.is_available() is True

    def test_name(self) -> None:
        scanner = MCPScanner()
        assert scanner.name == "mcp_scanner"

    @pytest.mark.asyncio
    async def test_dedup_config_and_probe(self) -> None:
        d = Path(tempfile.mkdtemp())
        config = {
            "mcpServers": {
                "my-server": {
                    "command": "npx",
                    "args": [],
                    "description": "From config",
                }
            }
        }
        (d / ".mcp.json").write_text(json.dumps(config))

        probe_result = {
            "name": "my-server",
            "description": "From probe",
            "tool_type": "mcp_server",
            "source": "mcp_scanner",
            "endpoint": "http://localhost:3000",
            "schema_definition": {},
        }

        scanner = MCPScanner(config_paths=[d / ".mcp.json"], probe_ports=[3000])
        with patch.object(scanner, "_probe_ports_for_mcp", new_callable=AsyncMock, return_value=[probe_result]):
            results = await scanner.scan()

        assert len(results) == 1
        assert results[0]["description"] == "From config"


class TestExtractPackageName:
    def test_scoped_package(self) -> None:
        assert _extract_package_name(["-y", "@modelcontextprotocol/server-filesystem"]) == "@modelcontextprotocol/server-filesystem"

    def test_no_args(self) -> None:
        assert _extract_package_name([]) is None

    def test_only_flags(self) -> None:
        assert _extract_package_name(["-y", "--verbose"]) is None
