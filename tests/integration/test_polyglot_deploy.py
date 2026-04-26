"""Integration tests for polyglot (Node.js) agent deployment.

Build-pipeline tests (no Docker required) verify that NodeRuntimeFamily
produces the correct container context. Docker-dependent tests are skipped
when Docker is not available.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Docker availability guard — used only by the live-deploy test section
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
def vercel_ai_agent_dir(tmp_path: Path) -> Path:
    """Create a minimal Vercel AI agent directory."""
    agent_dir = tmp_path / "vercel-ai-agent"
    agent_dir.mkdir()

    # Minimal agent.ts — the only file the developer writes
    (agent_dir / "agent.ts").write_text(
        "export const model = null\n"
        "export const systemPrompt = 'You are a test assistant.'\n"
        "export const tools = {}\n"
    )

    # agent.yaml
    (agent_dir / "agent.yaml").write_text(
        "name: test-vercel-agent\n"
        "version: 1.0.0\n"
        "team: eng\n"
        "owner: test@test.com\n"
        "runtime:\n"
        "  language: node\n"
        "  framework: vercel-ai\n"
        "  version: '20'\n"
        "model:\n"
        "  primary: gpt-4o\n"
        "deploy:\n"
        "  cloud: local\n"
    )

    return agent_dir


# ---------------------------------------------------------------------------
# Build-pipeline tests — NO Docker required, always run
# ---------------------------------------------------------------------------


def test_node_runtime_build_produces_valid_dockerfile(vercel_ai_agent_dir: Path) -> None:
    """Verify NodeRuntimeFamily.build() produces a buildable container context."""
    from engine.config_parser import parse_config  # noqa: PLC0415
    from engine.runtimes.node import NodeRuntimeFamily  # noqa: PLC0415

    config = parse_config(vercel_ai_agent_dir / "agent.yaml")
    runtime = NodeRuntimeFamily()
    image = runtime.build(vercel_ai_agent_dir, config)

    assert image.context_dir.exists()
    assert (image.context_dir / "Dockerfile").exists()
    assert (image.context_dir / "server.ts").exists()
    assert (image.context_dir / "package.json").exists()
    assert (image.context_dir / "_shared_loader.ts").exists()

    # Verify Dockerfile has correct Node version
    dockerfile = (image.context_dir / "Dockerfile").read_text()
    assert "FROM node:20" in dockerfile

    # Verify server.ts has agent name substituted
    server_ts = (image.context_dir / "server.ts").read_text()
    assert "test-vercel-agent" in server_ts
    assert "{{AGENT_NAME}}" not in server_ts

    # Verify package.json has aps-client
    pkg = json.loads((image.context_dir / "package.json").read_text())
    assert (image.context_dir / "aps-client.ts").exists()


def test_node_runtime_build_copies_developer_entrypoint(vercel_ai_agent_dir: Path) -> None:
    """Verify that the developer's agent.ts is included in the build context."""
    from engine.config_parser import parse_config  # noqa: PLC0415
    from engine.runtimes.node import NodeRuntimeFamily  # noqa: PLC0415

    config = parse_config(vercel_ai_agent_dir / "agent.yaml")
    runtime = NodeRuntimeFamily()
    image = runtime.build(vercel_ai_agent_dir, config)

    # Developer's agent.ts must be preserved alongside the platform server.ts
    assert (image.context_dir / "agent.ts").exists()
    agent_ts = (image.context_dir / "agent.ts").read_text()
    assert "systemPrompt" in agent_ts


def test_node_runtime_image_tag_includes_name_and_version(vercel_ai_agent_dir: Path) -> None:
    """Verify ContainerImage tag follows agentbreeder/<name>:<version> convention."""
    from engine.config_parser import parse_config  # noqa: PLC0415
    from engine.runtimes.node import NodeRuntimeFamily  # noqa: PLC0415

    config = parse_config(vercel_ai_agent_dir / "agent.yaml")
    runtime = NodeRuntimeFamily()
    image = runtime.build(vercel_ai_agent_dir, config)

    assert image.tag == "agentbreeder/test-vercel-agent:1.0.0"


# ---------------------------------------------------------------------------
# Deployer env-var injection test — NO Docker required, always run
# ---------------------------------------------------------------------------


def test_agentbreeder_url_injected_into_node_container_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify AGENTBREEDER_URL and API key are returned by the deployer."""
    from engine.deployers.docker_compose import DockerComposeDeployer  # noqa: PLC0415

    monkeypatch.setenv("AGENTBREEDER_URL", "http://agentbreeder-api:8000")
    monkeypatch.setenv("AGENTBREEDER_API_KEY", "test-key")

    deployer = DockerComposeDeployer()
    env_vars = deployer.get_aps_env_vars()

    assert env_vars["AGENTBREEDER_URL"] == "http://agentbreeder-api:8000"
    assert "AGENTBREEDER_API_KEY" in env_vars
    assert env_vars["AGENTBREEDER_API_KEY"] == "test-key"


def test_agentbreeder_url_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_aps_env_vars returns a sensible default when env vars are absent."""
    from engine.deployers.docker_compose import DockerComposeDeployer  # noqa: PLC0415

    monkeypatch.delenv("AGENTBREEDER_URL", raising=False)
    monkeypatch.delenv("AGENTBREEDER_API_KEY", raising=False)

    deployer = DockerComposeDeployer()
    env_vars = deployer.get_aps_env_vars()

    # Default must be a non-empty string pointing at the expected service name
    assert env_vars["AGENTBREEDER_URL"] == "http://agentbreeder-api:8000"
    assert "AGENTBREEDER_API_KEY" in env_vars


# ---------------------------------------------------------------------------
# Live-deploy tests — REQUIRE Docker, skipped otherwise
# ---------------------------------------------------------------------------


@_requires_docker
def test_docker_image_builds_without_error(vercel_ai_agent_dir: Path) -> None:
    """Verify the generated Dockerfile builds successfully with Docker.

    This test actually runs `docker build`, so it requires Docker to be running
    and node:20-slim to be pullable. It is skipped in CI unless Docker is
    available.
    """
    import subprocess  # noqa: PLC0415

    from engine.config_parser import parse_config  # noqa: PLC0415
    from engine.runtimes.node import NodeRuntimeFamily  # noqa: PLC0415

    config = parse_config(vercel_ai_agent_dir / "agent.yaml")
    runtime = NodeRuntimeFamily()
    image = runtime.build(vercel_ai_agent_dir, config)

    result = subprocess.run(
        ["docker", "build", "-t", image.tag, str(image.context_dir)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"docker build failed (exit {result.returncode}):\n{result.stderr}"
    )
