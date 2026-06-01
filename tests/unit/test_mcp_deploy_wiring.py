"""MCP co-deploy container injection is wired into the deployers (P4)."""

from __future__ import annotations


def test_ecs_injects_codeploy_container():
    from engine.config_parser import McpServerRef
    from engine.deployers.mcp_sidecar import inject_mcp_containers_ecs, resolve_mcp_servers

    resolved = resolve_mcp_servers(
        [McpServerRef(ref="mcp/a", transport="sse", image="img:1", port=3100)]
    )
    td = inject_mcp_containers_ecs({"containerDefinitions": [{"name": "app"}]}, resolved)
    names = {c["name"] for c in td["containerDefinitions"]}
    assert "mcp-a" in names and "app" in names
    mcp = [c for c in td["containerDefinitions"] if c["name"] == "mcp-a"][0]
    assert mcp["image"] == "img:1"
    assert mcp["essential"] is False
    assert mcp["portMappings"][0]["containerPort"] == 3100


def test_ecs_skips_remote_only():
    from engine.config_parser import McpServerRef
    from engine.deployers.mcp_sidecar import inject_mcp_containers_ecs, resolve_mcp_servers

    resolved = resolve_mcp_servers([McpServerRef(ref="mcp/a", transport="sse", url="https://x/sse")])
    td = inject_mcp_containers_ecs({"containerDefinitions": []}, resolved)
    assert td["containerDefinitions"] == []  # remote → no container


def test_ecs_idempotent():
    from engine.config_parser import McpServerRef
    from engine.deployers.mcp_sidecar import inject_mcp_containers_ecs, resolve_mcp_servers

    resolved = resolve_mcp_servers([McpServerRef(ref="mcp/a", transport="sse", image="img:1")])
    td = inject_mcp_containers_ecs({"containerDefinitions": []}, resolved)
    td2 = inject_mcp_containers_ecs(td, resolved)
    assert len([c for c in td2["containerDefinitions"] if c["name"] == "mcp-a"]) == 1


def test_cloudrun_injects_codeploy_container():
    from engine.config_parser import McpServerRef
    from engine.deployers.mcp_sidecar import inject_mcp_containers_cloudrun, resolve_mcp_servers

    resolved = resolve_mcp_servers([McpServerRef(ref="mcp/a", transport="sse", image="img:1")])
    containers = inject_mcp_containers_cloudrun([{"name": "app"}], resolved)
    assert any(c["name"] == "mcp-a" and c["image"] == "img:1" for c in containers)


def test_azure_injects_codeploy_container():
    from engine.config_parser import McpServerRef
    from engine.deployers.mcp_sidecar import inject_mcp_containers_azure, resolve_mcp_servers

    resolved = resolve_mcp_servers([McpServerRef(ref="mcp/a", transport="sse", image="img:1")])
    containers = inject_mcp_containers_azure([{"name": "app"}], resolved)
    assert any(c["name"] == "mcp-a" for c in containers)


def test_compose_injects_codeploy_service():
    from engine.config_parser import McpServerRef
    from engine.deployers.mcp_sidecar import inject_mcp_containers_compose, resolve_mcp_servers

    resolved = resolve_mcp_servers([McpServerRef(ref="mcp/a", transport="sse", image="img:1")])
    services = inject_mcp_containers_compose({"app": {}}, resolved)
    assert "mcp-a" in services and services["mcp-a"]["image"] == "img:1"
