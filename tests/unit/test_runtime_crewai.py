"""Tests for engine/runtimes/crewai.py — CrewAI runtime builder."""

from __future__ import annotations

import tempfile
from pathlib import Path

from engine.config_parser import AgentConfig, FrameworkType
from engine.runtimes.crewai import CrewAIRuntime


def _make_config(**overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "test",
        "owner": "test@example.com",
        "framework": FrameworkType.crewai,
        "model": {"primary": "gpt-4o-mini"},
        "deploy": {"cloud": "local"},
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_agent_dir(files: dict[str, str] | None = None) -> Path:
    """Create a temp directory with the given files."""
    d = Path(tempfile.mkdtemp())
    if files:
        for name, content in files.items():
            (d / name).write_text(content)
    return d


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


class TestCrewAIRuntimeValidate:
    def test_validate_valid_dir_with_crew_py(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "crew.py": "from crewai import Crew\ncrew = Crew(agents=[], tasks=[])",
                "requirements.txt": "crewai>=0.80.0",
            }
        )
        result = runtime.validate(agent_dir, _make_config())
        assert result.valid is True
        assert result.errors == []

    def test_validate_valid_dir_with_agent_py(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "from crewai import Crew\ncrew = Crew(agents=[], tasks=[])",
                "requirements.txt": "crewai>=0.80.0",
            }
        )
        result = runtime.validate(agent_dir, _make_config())
        assert result.valid is True
        assert result.errors == []

    def test_validate_pyproject_accepted_instead_of_requirements(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "crew.py": "crew = None",
                "pyproject.toml": "[project]\nname = 'test'\n",
            }
        )
        result = runtime.validate(agent_dir, _make_config())
        assert result.valid is True
        assert result.errors == []

    def test_validate_missing_crew_and_agent_py(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir({"requirements.txt": "crewai>=0.80.0"})
        result = runtime.validate(agent_dir, _make_config())
        assert result.valid is False
        assert any("crew.py" in e for e in result.errors)
        assert any("agent.py" in e for e in result.errors)

    def test_validate_missing_requirements(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir({"crew.py": "crew = None"})
        result = runtime.validate(agent_dir, _make_config())
        assert result.valid is False
        assert any("requirements" in e.lower() for e in result.errors)

    def test_validate_empty_dir_returns_two_errors(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir({})
        result = runtime.validate(agent_dir, _make_config())
        assert result.valid is False
        assert len(result.errors) == 2  # missing crew file + missing requirements


# ---------------------------------------------------------------------------
# get_requirements()
# ---------------------------------------------------------------------------


class TestCrewAIRuntimeRequirements:
    def test_get_requirements_includes_crewai(self) -> None:
        runtime = CrewAIRuntime()
        reqs = runtime.get_requirements(_make_config())
        assert any("crewai" in r for r in reqs)

    def test_get_requirements_includes_web_server_deps(self) -> None:
        runtime = CrewAIRuntime()
        reqs = runtime.get_requirements(_make_config())
        assert any("fastapi" in r for r in reqs)
        assert any("uvicorn" in r for r in reqs)
        assert any("httpx" in r for r in reqs)
        assert any("pydantic" in r for r in reqs)

    def test_get_requirements_returns_non_empty_list_of_strings(self) -> None:
        runtime = CrewAIRuntime()
        reqs = runtime.get_requirements(_make_config())
        assert isinstance(reqs, list)
        assert len(reqs) > 0
        assert all(isinstance(r, str) for r in reqs)


# ---------------------------------------------------------------------------
# get_entrypoint()
# ---------------------------------------------------------------------------


class TestCrewAIRuntimeEntrypoint:
    def test_get_entrypoint_uses_uvicorn_on_8080(self) -> None:
        runtime = CrewAIRuntime()
        entrypoint = runtime.get_entrypoint(_make_config())
        assert "uvicorn" in entrypoint
        assert "server:app" in entrypoint
        assert "8080" in entrypoint


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


class TestCrewAIRuntimeBuild:
    def test_build_returns_container_image_with_correct_tag(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "crew.py": "crew = None",
                "requirements.txt": "crewai>=0.80.0",
            }
        )
        image = runtime.build(agent_dir, _make_config())
        assert image.tag == "agentbreeder/test-agent:1.0.0"

    def test_build_tag_reflects_config_name_and_version(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "crew.py": "crew = None",
                "requirements.txt": "crewai>=0.80.0",
            }
        )
        image = runtime.build(agent_dir, _make_config(name="my-crew", version="2.0.0"))
        assert image.tag == "agentbreeder/my-crew:2.0.0"

    def test_build_context_dir_contains_dockerfile(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "crew.py": "crew = None",
                "requirements.txt": "crewai>=0.80.0",
            }
        )
        image = runtime.build(agent_dir, _make_config())
        assert image.context_dir.exists()
        assert (image.context_dir / "Dockerfile").exists()
        assert "FROM python:3.11-slim" in image.dockerfile_content

    def test_build_copies_crew_py_to_context(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "crew.py": "crew = None  # my crew",
                "requirements.txt": "crewai>=0.80.0",
            }
        )
        image = runtime.build(agent_dir, _make_config())
        assert (image.context_dir / "crew.py").exists()
        assert "my crew" in (image.context_dir / "crew.py").read_text()

    def test_build_copies_server_template(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "crew.py": "crew = None",
                "requirements.txt": "crewai>=0.80.0",
            }
        )
        image = runtime.build(agent_dir, _make_config())
        server_file = image.context_dir / "server.py"
        assert server_file.exists()
        server_text = server_file.read_text()
        assert "FastAPI" in server_text
        assert "kickoff" in server_text

    def test_build_merges_requirements_with_framework_deps(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "crew.py": "crew = None",
                "requirements.txt": "custom-package>=1.0\ncrewai>=0.80.0",
            }
        )
        image = runtime.build(agent_dir, _make_config())
        reqs = (image.context_dir / "requirements.txt").read_text()
        assert "custom-package>=1.0" in reqs
        assert "crewai" in reqs
        assert "fastapi" in reqs.lower()

    def test_build_skips_hidden_files(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "crew.py": "crew = None",
                "requirements.txt": "crewai>=0.80.0",
                ".env": "OPENAI_API_KEY=secret",
            }
        )
        image = runtime.build(agent_dir, _make_config())
        assert not (image.context_dir / ".env").exists()

    def test_build_skips_pycache(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {
                "crew.py": "crew = None",
                "requirements.txt": "crewai>=0.80.0",
            }
        )
        pycache = agent_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "crew.cpython-311.pyc").write_text("bytecode")
        image = runtime.build(agent_dir, _make_config())
        assert not (image.context_dir / "__pycache__").exists()


# ---------------------------------------------------------------------------
# build() — env var injection
# ---------------------------------------------------------------------------


class TestCrewAIRuntimeEnvVarInjection:
    """build() must write model config and deploy.env_vars into the Dockerfile."""

    def test_build_writes_agent_model_env_var(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {"crew.py": "crew = None", "requirements.txt": "crewai>=0.80.0"}
        )
        config = _make_config(model={"primary": "claude-opus-4-6"})
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "AGENT_MODEL" in dockerfile
        assert "claude-opus-4-6" in dockerfile

    def test_build_writes_temperature_env_var(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {"crew.py": "crew = None", "requirements.txt": "crewai>=0.80.0"}
        )
        config = _make_config(model={"primary": "gpt-4o", "temperature": 0.3})
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "AGENT_TEMPERATURE" in dockerfile
        assert "0.3" in dockerfile

    def test_build_writes_deploy_env_vars(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {"crew.py": "crew = None", "requirements.txt": "crewai>=0.80.0"}
        )
        config = _make_config(
            deploy={
                "cloud": "local",
                "env_vars": {"SERPER_API_KEY": "test-key", "LOG_LEVEL": "debug"},
            }
        )
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "SERPER_API_KEY" in dockerfile
        assert "test-key" in dockerfile
        assert "LOG_LEVEL" in dockerfile

    def test_build_skips_optional_env_vars_when_not_set(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {"crew.py": "crew = None", "requirements.txt": "crewai>=0.80.0"}
        )
        config = _make_config()  # no temperature, no max_tokens
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "AGENT_TEMPERATURE" not in dockerfile
        assert "AGENT_MAX_TOKENS" not in dockerfile

    def test_build_sanitises_env_var_keys(self) -> None:
        runtime = CrewAIRuntime()
        agent_dir = _make_agent_dir(
            {"crew.py": "crew = None", "requirements.txt": "crewai>=0.80.0"}
        )
        config = _make_config(deploy={"cloud": "local", "env_vars": {"SAFE_KEY": "value"}})
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "SAFE_KEY" in dockerfile
        assert "value" in dockerfile


class TestCrewAIServerModelConfig:
    """The server template must read AGENT_MODEL / AGENT_TEMPERATURE from env."""

    def test_server_template_references_agent_model_env(self) -> None:
        """crewai_server.py must read AGENT_MODEL env var to allow model override."""
        server_text = Path("engine/runtimes/templates/crewai_server.py").read_text()
        assert "AGENT_MODEL" in server_text, (
            "crewai_server.py must read AGENT_MODEL env var to allow model override"
        )

    def test_server_template_references_agent_temperature_env(self) -> None:
        server_text = Path("engine/runtimes/templates/crewai_server.py").read_text()
        assert "AGENT_TEMPERATURE" in server_text

    def test_get_requirements_adds_litellm_for_ollama_model(self) -> None:
        runtime = CrewAIRuntime()
        config = _make_config(model={"primary": "ollama/llama3:8b"})
        reqs = runtime.get_requirements(config)
        assert any("litellm" in r for r in reqs)

    def test_get_requirements_no_litellm_for_non_ollama_model(self) -> None:
        runtime = CrewAIRuntime()
        config = _make_config(model={"primary": "gpt-4o"})
        reqs = runtime.get_requirements(config)
        assert not any("litellm" in r for r in reqs)

    def test_server_template_sets_ollama_base_url(self) -> None:
        """CrewAI server template must set agent.llm.base_url for ollama/ models."""
        template = (
            Path(__file__).parent.parent.parent / "engine/runtimes/templates/crewai_server.py"
        ).read_text()
        assert "OLLAMA_BASE_URL" in template
        assert "base_url" in template
