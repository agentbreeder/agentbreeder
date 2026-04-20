"""Tests for the BrowserUseConnector."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from connectors.browser_use.connector import COMPUTER_USE_TOOL_TYPE, BrowserUseConnector


class TestBrowserUseConnectorName:
    def test_name(self) -> None:
        assert BrowserUseConnector().name == "browser_use"


class TestBrowserUseConnectorAvailability:
    @pytest.mark.asyncio
    async def test_is_available_when_library_installed(self) -> None:
        import types
        fake_module = types.ModuleType("browser_use")
        with patch.dict("sys.modules", {"browser_use": fake_module}):
            connector = BrowserUseConnector()
            assert await connector.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_when_library_missing(self) -> None:
        with patch.dict("sys.modules", {"browser_use": None}):
            connector = BrowserUseConnector()
            assert await connector.is_available() is False


class TestBrowserUseConnectorScan:
    @pytest.mark.asyncio
    async def test_scan_returns_empty_when_unavailable(self) -> None:
        connector = BrowserUseConnector()
        with patch.object(connector, "is_available", return_value=False):
            result = await connector.scan()
        assert result == []

    @pytest.mark.asyncio
    async def test_scan_returns_tool_descriptor_when_available(self) -> None:
        connector = BrowserUseConnector()
        with patch.object(connector, "is_available", return_value=True):
            result = await connector.scan()

        assert len(result) == 1
        tool = result[0]
        assert tool["name"] == "browser-use"
        assert tool["source"] == "browser_use"
        assert tool["tool_type"] == COMPUTER_USE_TOOL_TYPE
        assert tool["type"] == "mcp_server"
        assert "description" in tool
        assert "tags" in tool
        assert "browser" in tool["tags"]

    @pytest.mark.asyncio
    async def test_scan_tool_type_constant(self) -> None:
        assert COMPUTER_USE_TOOL_TYPE == "computer_use_20260401"
