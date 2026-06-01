"""Tests for MCP server ref parsing + resolution (P4)."""

from __future__ import annotations


class TestMcpServerRefFields:
    def test_ref_only_defaults(self):
        from engine.config_parser import McpServerRef

        ref = McpServerRef(ref="mcp/zendesk")
        assert ref.ref == "mcp/zendesk"
        assert ref.transport == "stdio"
        assert ref.url is None
        assert ref.image is None
        assert ref.port is None

    def test_inline_remote(self):
        from engine.config_parser import McpServerRef

        ref = McpServerRef(ref="mcp/zendesk", transport="sse", url="https://mcp.acme.com/sse")
        assert ref.url == "https://mcp.acme.com/sse"

    def test_inline_image(self):
        from engine.config_parser import McpServerRef

        ref = McpServerRef(ref="mcp/slack", transport="sse", image="acme/mcp-slack:1.2.3", port=3100)
        assert ref.image == "acme/mcp-slack:1.2.3"
        assert ref.port == 3100
