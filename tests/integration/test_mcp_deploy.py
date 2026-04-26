"""Integration tests for MCP server deployment.

Build-pipeline tests (no Docker required) verify that NodeRuntimeFamily
produces the correct MCP server container context and that mcp-server.yaml
validates against the JSON schema.
Docker-dependent tests are skipped when Docker is not available.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Docker availability guard
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    try:
        import docker  # noqa: PLC0415

        docker.from_env().ping()
        return True
    except Exception:
        return False


_requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker not available",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_ts_server_dir(tmp_path: Path) -> Path:
    """Create a minimal MCP TypeScript server directory."""
    server_dir = tmp_path / "mcp-ts-server"
    server_dir.mkdir()

    # Minimal tools.ts — the only file the developer writes
    (server_dir / "tools.ts").write_text(
        "export async function search_web({ query }: { query: string }): Promise<string> {\n"
        "  return `Results for: ${query}`\n"
        "}\n"
    )

    # mcp-server.yaml
    (server_dir / "mcp-server.yaml").write_text(
        "name: test-mcp-server\n"
        "version: 1.0.0\n"
        "type: mcp-server\n"
        "team: eng\n"
        "owner: test@test.com\n"
        "runtime:\n"
        "  language: node\n"
        "  framework: mcp-ts\n"
        "  version: '20'\n"
        "transport: http\n"
        "tools:\n"
        "  - name: search_web\n"
        "    description: Search the web\n"
        "deploy:\n"
        "  cloud: local\n"
    )

    # agent.ts is needed for NodeRuntimeFamily.validate() default entrypoint check;
    # for MCP servers the entrypoint is tools.ts — we create a minimal agent.ts too
    # so the validate() step doesn't reject the build in callers that check it first.
    (server_dir / "agent.ts").write_text("// placeholder — MCP server entrypoint is tools.ts\n")

    return server_dir


def _make_mcp_agent_config() -> object:
    """Return an AgentConfig for a mcp-ts server."""
    from engine.config_parser import AgentConfig, RuntimeConfig  # noqa: PLC0415

    return AgentConfig(
        name="test-mcp-server",
        version="1.0.0",
        team="eng",
        owner="test@test.com",
        runtime=RuntimeConfig(language="node", framework="mcp-ts", version="20"),
        model={"primary": "gpt-4o"},
        deploy={"cloud": "local"},
    )


# ---------------------------------------------------------------------------
# Build-pipeline tests — NO Docker required, always run
# ---------------------------------------------------------------------------


def test_mcp_ts_runtime_build_produces_mcp_server(mcp_ts_server_dir: Path) -> None:
    """Verify NodeRuntimeFamily builds a valid MCP server context for mcp-ts framework."""
    from engine.runtimes.node import NodeRuntimeFamily  # noqa: PLC0415

    config = _make_mcp_agent_config()
    runtime = NodeRuntimeFamily()
    image = runtime.build(mcp_ts_server_dir, config)  # type: ignore[arg-type]

    # Verify MCP template was used — template handles tools/list and tools/call
    server_ts = (image.context_dir / "server.ts").read_text()
    assert "tools/list" in server_ts or "tools/call" in server_ts
    assert "test-mcp-server" in server_ts
    assert "{{AGENT_NAME}}" not in server_ts

    # Verify Dockerfile exists with correct Node version
    dockerfile = (image.context_dir / "Dockerfile").read_text()
    assert "FROM node:20" in dockerfile

    # Verify package.json includes MCP SDK dependency
    pkg = json.loads((image.context_dir / "package.json").read_text())
    assert "@modelcontextprotocol/sdk" in pkg["dependencies"]


def test_mcp_ts_build_includes_aps_client(mcp_ts_server_dir: Path) -> None:
    """Verify the MCP server container still gets @agentbreeder/aps-client injected."""
    from engine.runtimes.node import NodeRuntimeFamily  # noqa: PLC0415

    config = _make_mcp_agent_config()
    runtime = NodeRuntimeFamily()
    image = runtime.build(mcp_ts_server_dir, config)  # type: ignore[arg-type]

    pkg = json.loads((image.context_dir / "package.json").read_text())
    assert (image.context_dir / "aps-client.ts").exists()


def test_mcp_ts_build_copies_tools_ts(mcp_ts_server_dir: Path) -> None:
    """Verify the developer's tools.ts is included in the build context."""
    from engine.runtimes.node import NodeRuntimeFamily  # noqa: PLC0415

    config = _make_mcp_agent_config()
    runtime = NodeRuntimeFamily()
    image = runtime.build(mcp_ts_server_dir, config)  # type: ignore[arg-type]

    assert (image.context_dir / "tools.ts").exists()
    tools_ts = (image.context_dir / "tools.ts").read_text()
    assert "search_web" in tools_ts


def test_mcp_ts_image_tag_convention(mcp_ts_server_dir: Path) -> None:
    """Verify ContainerImage tag follows agentbreeder/<name>:<version> for MCP servers."""
    from engine.runtimes.node import NodeRuntimeFamily  # noqa: PLC0415

    config = _make_mcp_agent_config()
    runtime = NodeRuntimeFamily()
    image = runtime.build(mcp_ts_server_dir, config)  # type: ignore[arg-type]

    assert image.tag == "agentbreeder/test-mcp-server:1.0.0"


# ---------------------------------------------------------------------------
# Schema validation test — NO Docker required, always run
# ---------------------------------------------------------------------------


def test_mcp_server_yaml_validates_against_schema() -> None:
    """Verify a well-formed mcp-server.yaml validates against mcp-server.schema.json."""
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        pytest.skip("jsonschema not installed")

    schema_path = (
        Path(__file__).parent.parent.parent / "engine" / "schema" / "mcp-server.schema.json"
    )
    schema = json.loads(schema_path.read_text())

    # Build a dict matching the YAML fixture above
    config = {
        "name": "test-mcp-server",
        "version": "1.0.0",
        "type": "mcp-server",
        "team": "eng",
        "owner": "test@test.com",
        "runtime": {"language": "node", "framework": "mcp-ts", "version": "20"},
        "transport": "http",
        "tools": [{"name": "search_web", "description": "Search the web"}],
    }

    # Should not raise
    jsonschema.validate(instance=config, schema=schema)


def test_mcp_server_schema_rejects_invalid_framework() -> None:
    """Verify schema rejects unknown MCP framework values."""
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        pytest.skip("jsonschema not installed")

    schema_path = (
        Path(__file__).parent.parent.parent / "engine" / "schema" / "mcp-server.schema.json"
    )
    schema = json.loads(schema_path.read_text())

    bad_config = {
        "name": "test-mcp-server",
        "version": "1.0.0",
        "type": "mcp-server",
        "runtime": {"language": "node", "framework": "not-a-real-framework"},
        "tools": [{"name": "foo", "description": "bar"}],
    }

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad_config, schema=schema)


# ---------------------------------------------------------------------------
# Live-deploy tests — REQUIRE Docker, skipped otherwise
# ---------------------------------------------------------------------------


@_requires_docker
def test_mcp_docker_image_builds_without_error(mcp_ts_server_dir: Path) -> None:
    """Verify the generated MCP server Dockerfile builds successfully with Docker.

    This test actually runs `docker build`, so it requires Docker to be running
    and node:20-slim to be pullable. It is skipped in CI unless Docker is
    available.
    """
    import subprocess  # noqa: PLC0415

    from engine.runtimes.node import NodeRuntimeFamily  # noqa: PLC0415

    config = _make_mcp_agent_config()
    runtime = NodeRuntimeFamily()
    image = runtime.build(mcp_ts_server_dir, config)  # type: ignore[arg-type]

    result = subprocess.run(
        ["docker", "build", "-t", image.tag, str(image.context_dir)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"docker build failed (exit {result.returncode}):\n{result.stderr}"
    )
