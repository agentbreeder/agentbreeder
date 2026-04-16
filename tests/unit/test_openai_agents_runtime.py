"""Tests for engine/runtimes/openai_agents.py — OpenAI Agents SDK runtime builder."""

from __future__ import annotations

import tempfile
from pathlib import Path

from engine.config_parser import AgentConfig, FrameworkType
from engine.runtimes import get_runtime
from engine.runtimes.openai_agents import OpenAIAgentsRuntime


def _make_config(**overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "test",
        "owner": "test@example.com",
        "framework": FrameworkType.openai_agents,
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
    def test_get_openai_agents_runtime(self) -> None:
        runtime = get_runtime(FrameworkType.openai_agents)
        assert isinstance(runtime, OpenAIAgentsRuntime)


class TestOpenAIAgentsRuntimeValidate:
    def test_validate_valid_agent_with_agent_py(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "from agents import Agent\nagent = Agent(name='t', instructions='hi')",
                "requirements.txt": "openai-agents>=0.1.0",
            }
        )
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is True
        assert result.errors == []

    def test_validate_valid_agent_with_main_py(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir(
            {
                "main.py": "from agents import Agent\nagent = Agent(name='t', instructions='hi')",
                "requirements.txt": "openai-agents>=0.1.0",
            }
        )
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is True
        assert result.errors == []

    def test_validate_missing_agent_and_main_py(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir({"requirements.txt": "openai-agents"})
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert any("agent.py" in e for e in result.errors)
        assert any("main.py" in e for e in result.errors)

    def test_validate_missing_requirements(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir({"agent.py": "agent = None"})
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert any("requirements" in e.lower() for e in result.errors)

    def test_validate_pyproject_accepted(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "pyproject.toml": "[project]\nname='test'",
            }
        )
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is True

    def test_validate_multiple_errors(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir({})  # empty directory
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert len(result.errors) == 2  # missing agent file + missing requirements


class TestOpenAIAgentsRuntimeBuild:
    def test_build_creates_container_image(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "from agents import Agent\nagent = Agent(name='t', instructions='hi')",
                "requirements.txt": "openai-agents>=0.1.0",
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
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "openai-agents>=0.1.0",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        server_file = image.context_dir / "server.py"
        assert server_file.exists()
        assert "FastAPI" in server_file.read_text()
        assert "OpenAI Agents SDK" in server_file.read_text()

    def test_build_merges_requirements(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "custom-package>=1.0\nopenai-agents>=0.1.0",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        reqs = (image.context_dir / "requirements.txt").read_text()
        assert "custom-package>=1.0" in reqs
        assert "openai-agents" in reqs
        assert "fastapi" in reqs.lower()
        assert "openai>=" in reqs

    def test_build_skips_hidden_files(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "openai-agents>=0.1.0",
                ".env": "OPENAI_API_KEY=secret",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        assert not (image.context_dir / ".env").exists()

    def test_build_skips_pycache(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "openai-agents>=0.1.0",
            }
        )
        pycache = agent_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "agent.cpython-311.pyc").write_text("bytecode")
        config = _make_config()
        image = runtime.build(agent_dir, config)
        assert not (image.context_dir / "__pycache__").exists()

    def test_build_tag_format(self) -> None:
        runtime = OpenAIAgentsRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "openai-agents>=0.1.0",
            }
        )
        config = _make_config(name="my-cool-agent", version="2.3.1")
        image = runtime.build(agent_dir, config)
        assert image.tag == "agentbreeder/my-cool-agent:2.3.1"


class TestOpenAIAgentsRuntimeEntrypoint:
    def test_get_entrypoint(self) -> None:
        runtime = OpenAIAgentsRuntime()
        config = _make_config()
        entrypoint = runtime.get_entrypoint(config)
        assert "uvicorn" in entrypoint
        assert "8080" in entrypoint
        assert "server:app" in entrypoint


class TestOpenAIAgentsRuntimeRequirements:
    def test_get_requirements_includes_core_deps(self) -> None:
        runtime = OpenAIAgentsRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        assert any("openai-agents" in r for r in reqs)
        assert any("openai>=" in r for r in reqs)
        assert any("fastapi" in r for r in reqs)
        assert any("uvicorn" in r for r in reqs)
        assert any("httpx" in r for r in reqs)
        assert any("pydantic" in r for r in reqs)

    def test_get_requirements_returns_list(self) -> None:
        runtime = OpenAIAgentsRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        assert isinstance(reqs, list)
        assert len(reqs) > 0
        assert all(isinstance(r, str) for r in reqs)

    def test_get_requirements_adds_litellm_for_ollama_model(self) -> None:
        runtime = OpenAIAgentsRuntime()
        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        reqs = runtime.get_requirements(config)
        assert any("litellm" in r for r in reqs)

    def test_build_injects_ollama_base_url(self, tmp_path: Path) -> None:
        (tmp_path / "agent.py").write_text("agent = None")
        (tmp_path / "requirements.txt").write_text("")
        runtime = OpenAIAgentsRuntime()
        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        image = runtime.build(tmp_path, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "OLLAMA_BASE_URL" in dockerfile
