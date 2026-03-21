"""Tests for engine/runtimes/ — runtime builder registry and LangGraph runtime."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from engine.config_parser import AgentConfig, FrameworkType
from engine.runtimes import get_runtime
from engine.runtimes.langgraph import LangGraphRuntime


def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "test",
        "owner": "test@example.com",
        "framework": FrameworkType.langgraph,
        "model": {"primary": "gpt-4o"},
        "deploy": {"cloud": "local"},
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_agent_dir(files: dict[str, str] | None = None) -> Path:
    """Create a temp directory with agent files."""
    d = Path(tempfile.mkdtemp())
    if files:
        for name, content in files.items():
            (d / name).write_text(content)
    return d


class TestGetRuntime:
    def test_get_langgraph_runtime(self) -> None:
        runtime = get_runtime(FrameworkType.langgraph)
        assert isinstance(runtime, LangGraphRuntime)

    def test_unsupported_framework_raises(self) -> None:
        with pytest.raises(KeyError, match="not yet supported"):
            get_runtime(FrameworkType.crewai)

    def test_error_message_lists_supported(self) -> None:
        with pytest.raises(KeyError, match="langgraph"):
            get_runtime(FrameworkType.crewai)


class TestLangGraphRuntime:
    def test_validate_valid_agent(self) -> None:
        runtime = LangGraphRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "graph = None",
                "requirements.txt": "langgraph>=0.2.0",
            }
        )
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is True
        assert result.errors == []

    def test_validate_missing_agent_py(self) -> None:
        runtime = LangGraphRuntime()
        agent_dir = _make_agent_dir({"requirements.txt": "langgraph"})
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert any("agent.py" in e for e in result.errors)

    def test_validate_missing_requirements(self) -> None:
        runtime = LangGraphRuntime()
        agent_dir = _make_agent_dir({"agent.py": "graph = None"})
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert any("requirements" in e.lower() for e in result.errors)

    def test_validate_pyproject_accepted(self) -> None:
        runtime = LangGraphRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "graph = None",
                "pyproject.toml": "[project]\nname='test'",
            }
        )
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is True

    def test_build_creates_container_image(self) -> None:
        runtime = LangGraphRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "graph = None",
                "requirements.txt": "langgraph>=0.2.0",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        assert image.tag == "agentbreeder/test-agent:1.0.0"
        assert image.context_dir.exists()
        assert (image.context_dir / "Dockerfile").exists()
        assert (image.context_dir / "requirements.txt").exists()
        assert (image.context_dir / "agent.py").exists()
        assert "FROM python:3.11-slim" in image.dockerfile_content

    def test_build_copies_server_template(self) -> None:
        runtime = LangGraphRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "graph = None",
                "requirements.txt": "langgraph>=0.2.0",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        server_file = image.context_dir / "server.py"
        assert server_file.exists()
        assert "FastAPI" in server_file.read_text()

    def test_build_merges_requirements(self) -> None:
        runtime = LangGraphRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "graph = None",
                "requirements.txt": "custom-package>=1.0\nlanggraph>=0.2.0",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        reqs = (image.context_dir / "requirements.txt").read_text()
        assert "custom-package>=1.0" in reqs
        assert "langgraph" in reqs
        assert "fastapi" in reqs.lower()

    def test_build_skips_hidden_files(self) -> None:
        runtime = LangGraphRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "graph = None",
                "requirements.txt": "langgraph>=0.2.0",
                ".env": "SECRET=value",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        assert not (image.context_dir / ".env").exists()

    def test_get_entrypoint(self) -> None:
        runtime = LangGraphRuntime()
        config = _make_config()
        entrypoint = runtime.get_entrypoint(config)
        assert "uvicorn" in entrypoint
        assert "8080" in entrypoint

    def test_get_requirements(self) -> None:
        runtime = LangGraphRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        assert any("langgraph" in r for r in reqs)
        assert any("fastapi" in r for r in reqs)
        assert any("uvicorn" in r for r in reqs)
