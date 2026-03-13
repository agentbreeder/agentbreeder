"""Tests for engine/mcp/packager and engine/deployers/mcp_sidecar."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Packager tests
# ---------------------------------------------------------------------------


class TestGenerateDockerfile:
    """Test Dockerfile generation for MCP servers."""

    def test_stdio_default(self):
        from engine.mcp.packager import generate_dockerfile

        df = generate_dockerfile("stdio")
        assert "FROM node:20-slim" in df
        assert 'CMD ["node", "index.js"]' in df
        assert "MCP_TRANSPORT" not in df

    def test_sse_transport(self):
        from engine.mcp.packager import generate_dockerfile

        df = generate_dockerfile("sse")
        assert "ENV MCP_TRANSPORT=sse" in df

    def test_streamable_http_transport(self):
        from engine.mcp.packager import generate_dockerfile

        df = generate_dockerfile("streamable_http")
        assert "ENV MCP_TRANSPORT=streamable_http" in df

    def test_unknown_transport_falls_back_to_stdio(self):
        from engine.mcp.packager import generate_dockerfile

        df = generate_dockerfile("grpc")
        assert "FROM node:20-slim" in df
        assert "MCP_TRANSPORT" not in df

    def test_custom_base_image(self):
        from engine.mcp.packager import generate_dockerfile

        df = generate_dockerfile("stdio", custom_base="python:3.12-slim")
        assert "FROM python:3.12-slim" in df
        assert "FROM node:20-slim" not in df


class TestBuildImageTag:
    """Test Docker image tag generation."""

    def test_basic_tag(self):
        from engine.mcp.packager import build_image_tag

        tag = build_image_tag("zendesk", "1.0.0")
        assert tag == "agent-garden/mcp-zendesk:1.0.0"

    def test_custom_registry(self):
        from engine.mcp.packager import build_image_tag

        tag = build_image_tag("zendesk", "2.0.0", registry_prefix="my-org")
        assert tag == "my-org/mcp-zendesk:2.0.0"

    def test_name_with_spaces(self):
        from engine.mcp.packager import build_image_tag

        tag = build_image_tag("My Server", "1.0.0")
        assert tag == "agent-garden/mcp-my-server:1.0.0"

    def test_name_uppercase(self):
        from engine.mcp.packager import build_image_tag

        tag = build_image_tag("ZENDESK", "1.0.0")
        assert tag == "agent-garden/mcp-zendesk:1.0.0"


class TestGenerateSidecarConfig:
    """Test sidecar container config generation."""

    def test_basic_config(self):
        from engine.mcp.packager import generate_sidecar_config

        config = generate_sidecar_config("zendesk", "agent-garden/mcp-zendesk:1.0.0")
        assert config["name"] == "mcp-zendesk"
        assert config["image"] == "agent-garden/mcp-zendesk:1.0.0"
        assert config["transport"] == "stdio"
        assert config["port"] == 3000
        assert config["environment"]["MCP_TRANSPORT"] == "stdio"
        assert config["health_check"]["path"] == "/health"

    def test_custom_transport_and_port(self):
        from engine.mcp.packager import generate_sidecar_config

        config = generate_sidecar_config("slack", "img:latest", transport="sse", port=4000)
        assert config["transport"] == "sse"
        assert config["port"] == 4000
        assert config["environment"]["MCP_TRANSPORT"] == "sse"


# ---------------------------------------------------------------------------
# MCP Sidecar Deployer tests
# ---------------------------------------------------------------------------


class TestMcpSidecarDeployer:
    """Test MCP sidecar deployment."""

    def test_generate_sidecars(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import McpSidecarDeployer

        deployer = McpSidecarDeployer()
        mcp_servers = [
            McpServerRef(ref="mcp/zendesk", transport="sse"),
            McpServerRef(ref="mcp/slack", transport="stdio"),
        ]

        sidecars = deployer.generate_sidecars(mcp_servers, "my-agent")
        assert len(sidecars) == 2
        assert sidecars[0]["name"] == "mcp-zendesk"
        assert sidecars[0]["transport"] == "sse"
        assert sidecars[0]["port"] == 3000
        assert sidecars[0]["labels"]["agent-garden.agent"] == "my-agent"
        assert sidecars[0]["labels"]["agent-garden.mcp-ref"] == "mcp/zendesk"
        assert sidecars[1]["name"] == "mcp-slack"
        assert sidecars[1]["port"] == 3001

    def test_generate_sidecars_empty(self):
        from engine.deployers.mcp_sidecar import McpSidecarDeployer

        deployer = McpSidecarDeployer()
        sidecars = deployer.generate_sidecars([], "my-agent")
        assert sidecars == []

    def test_generate_sidecars_custom_registry(self):
        from engine.config_parser import McpServerRef
        from engine.deployers.mcp_sidecar import McpSidecarDeployer

        deployer = McpSidecarDeployer()
        mcp_servers = [McpServerRef(ref="mcp/custom", transport="stdio")]

        sidecars = deployer.generate_sidecars(mcp_servers, "agent", registry_prefix="my-registry")
        assert "my-registry/" in sidecars[0]["image"]

    def test_inject_into_compose(self):
        from engine.deployers.mcp_sidecar import McpSidecarDeployer

        deployer = McpSidecarDeployer()
        compose = {"services": {"api": {"image": "api:latest"}}}
        sidecars = [
            {
                "name": "mcp-zendesk",
                "image": "agent-garden/mcp-zendesk:1.0.0",
                "environment": {"MCP_TRANSPORT": "sse"},
                "port": 3000,
                "labels": {"agent-garden.agent": "my-agent"},
            },
        ]

        result = deployer.inject_into_compose(compose, sidecars)
        assert "api" in result["services"]
        assert "mcp-zendesk" in result["services"]
        svc = result["services"]["mcp-zendesk"]
        assert svc["image"] == "agent-garden/mcp-zendesk:1.0.0"
        assert svc["ports"] == ["3000:3000"]
        assert svc["restart"] == "unless-stopped"

    def test_inject_into_compose_empty_services(self):
        from engine.deployers.mcp_sidecar import McpSidecarDeployer

        deployer = McpSidecarDeployer()
        compose = {}
        sidecars = [
            {
                "name": "mcp-slack",
                "image": "img:latest",
                "environment": {},
                "port": 3001,
                "labels": {},
            },
        ]

        result = deployer.inject_into_compose(compose, sidecars)
        assert "mcp-slack" in result["services"]
