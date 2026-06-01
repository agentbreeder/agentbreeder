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

        ref = McpServerRef(
            ref="mcp/slack", transport="sse", image="acme/mcp-slack:1.2.3", port=3100
        )
        assert ref.image == "acme/mcp-slack:1.2.3"
        assert ref.port == 3100


class TestResolveMcpServers:
    def test_remote_url(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import resolve_mcp_servers

        out = resolve_mcp_servers(
            [McpServerRef(ref="mcp/z", transport="sse", url="https://x/sse")]
        )
        assert len(out) == 1
        r = out[0]
        assert r.name == "z"
        assert r.co_deploy is False
        assert r.url == "https://x/sse"
        assert r.image is None

    def test_inline_image_autoport(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import resolve_mcp_servers

        out = resolve_mcp_servers(
            [McpServerRef(ref="mcp/a", transport="sse", image="img:1")],
            base_port=3100,
        )
        r = out[0]
        assert r.co_deploy is True
        assert r.image == "img:1"
        assert r.port == 3100
        assert r.url == "http://localhost:3100"

    def test_convention_fallback(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import resolve_mcp_servers

        out = resolve_mcp_servers([McpServerRef(ref="mcp/slack", transport="sse")])
        r = out[0]
        assert r.co_deploy is True
        assert r.image == "agentbreeder/mcp-slack:latest"
        assert r.url == "http://localhost:3100"

    def test_registry_lookup_endpoint(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import McpServerInfo, resolve_mcp_servers

        def lookup(name):
            return McpServerInfo(name=name, transport="sse", endpoint="https://reg/sse")

        out = resolve_mcp_servers([McpServerRef(ref="mcp/z")], registry_lookup=lookup)
        assert out[0].url == "https://reg/sse"
        assert out[0].co_deploy is False

    def test_env_map_skips_remote_stdio(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import build_sidecar_env_map, resolve_mcp_servers

        out = resolve_mcp_servers([McpServerRef(ref="mcp/z", transport="stdio", url="http://x")])
        env = build_sidecar_env_map(out)
        assert env == {}  # remote stdio can't be forwarded

    def test_env_map_normalises_codeploy_stdio_to_http(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import build_sidecar_env_map, resolve_mcp_servers

        out = resolve_mcp_servers([McpServerRef(ref="mcp/a", transport="stdio", image="img:1")])
        env = build_sidecar_env_map(out)
        assert env == {"a": {"transport": "http", "url": "http://localhost:3100"}}
