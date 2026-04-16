"""Tests for engine/runtimes/claude_sdk.py — Claude SDK runtime builder."""

from __future__ import annotations

import tempfile
from pathlib import Path

from engine.config_parser import AgentConfig, FrameworkType
from engine.runtimes.claude_sdk import ClaudeSDKRuntime


def _make_config(**overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "test",
        "owner": "test@example.com",
        "framework": FrameworkType.claude_sdk,
        "model": {"primary": "claude-sonnet-4-6"},
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


class TestClaudeSDKRuntimeValidate:
    def test_validate_valid_agent(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "import anthropic\nagent = anthropic.AsyncAnthropic()",
                "requirements.txt": "anthropic>=0.40.0",
                ".env.example": "ANTHROPIC_API_KEY=your-key-here",
            }
        )
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is True
        assert result.errors == []

    def test_validate_missing_agent_py(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir({"requirements.txt": "anthropic>=0.40.0"})
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert any("agent.py" in e for e in result.errors)

    def test_validate_missing_requirements(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir({"agent.py": "agent = None"})
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert any("requirements" in e.lower() for e in result.errors)

    def test_validate_pyproject_accepted(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "pyproject.toml": "[project]\nname = 'test'",
            }
        )
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is True

    def test_validate_multiple_errors(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir({})  # empty directory
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert len(result.errors) == 2  # missing agent.py + missing requirements

    def test_validate_missing_api_key_does_not_fail(self) -> None:
        """ANTHROPIC_API_KEY warning must not add an error to the result."""
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "anthropic>=0.40.0",
                # no .env.example mentioning ANTHROPIC_API_KEY
            }
        )
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is True
        assert result.errors == []

    def test_validate_rejects_ollama_model(self) -> None:
        """Claude SDK only supports Claude models — ollama/ must be rejected."""
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "root_agent = None",
                "requirements.txt": "anthropic-ai-sdk>=0.1.0\n",
            }
        )
        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        result = runtime.validate(agent_dir, config)
        assert not result.valid
        assert any("ollama" in e.lower() or "Claude" in e for e in result.errors)


class TestClaudeSDKRuntimeBuild:
    def test_build_creates_container_image(self, tmp_path: Path) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "import anthropic\nagent = anthropic.AsyncAnthropic()",
                "requirements.txt": "anthropic>=0.40.0",
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
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "anthropic>=0.40.0",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        server_file = image.context_dir / "server.py"
        assert server_file.exists()
        assert "FastAPI" in server_file.read_text()
        assert "anthropic" in server_file.read_text()

    def test_build_merges_requirements(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "custom-package>=1.0\nanthropic>=0.40.0",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        reqs = (image.context_dir / "requirements.txt").read_text()
        assert "custom-package>=1.0" in reqs
        assert "anthropic" in reqs
        assert "fastapi" in reqs.lower()
        assert "uvicorn" in reqs.lower()

    def test_build_skips_hidden_files(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "anthropic>=0.40.0",
                ".env": "ANTHROPIC_API_KEY=secret",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        assert not (image.context_dir / ".env").exists()

    def test_build_skips_pycache(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "anthropic>=0.40.0",
            }
        )
        pycache = agent_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "agent.cpython-311.pyc").write_text("bytecode")
        config = _make_config()
        image = runtime.build(agent_dir, config)
        assert not (image.context_dir / "__pycache__").exists()

    def test_build_tag_format(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "anthropic>=0.40.0",
            }
        )
        config = _make_config(name="my-cool-agent", version="2.3.1")
        image = runtime.build(agent_dir, config)
        assert image.tag == "agentbreeder/my-cool-agent:2.3.1"

    def test_build_dockerfile_written(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "anthropic>=0.40.0",
            }
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        dockerfile_content = (image.context_dir / "Dockerfile").read_text()
        assert "EXPOSE 8080" in dockerfile_content
        assert "uvicorn" in dockerfile_content
        assert "server:app" in dockerfile_content
        assert "HEALTHCHECK" in dockerfile_content


class TestClaudeSDKRuntimeEntrypoint:
    def test_get_entrypoint(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config()
        entrypoint = runtime.get_entrypoint(config)
        assert "uvicorn" in entrypoint
        assert "8080" in entrypoint
        assert "server:app" in entrypoint

    def test_get_entrypoint_host_and_port(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config()
        entrypoint = runtime.get_entrypoint(config)
        assert "0.0.0.0" in entrypoint  # noqa: S104


class TestClaudeSDKRuntimeRequirements:
    def test_get_requirements_includes_anthropic(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        assert any("anthropic" in r for r in reqs)

    def test_get_requirements_includes_core_deps(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        assert any("fastapi" in r for r in reqs)
        assert any("uvicorn" in r for r in reqs)
        assert any("httpx" in r for r in reqs)
        assert any("pydantic" in r for r in reqs)

    def test_get_requirements_returns_list_of_strings(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        assert isinstance(reqs, list)
        assert len(reqs) > 0
        assert all(isinstance(r, str) for r in reqs)

    def test_get_requirements_anthropic_version_constraint(self) -> None:
        runtime = ClaudeSDKRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        anthropic_req = next(r for r in reqs if "anthropic" in r)
        assert "0.50.0" in anthropic_req


class TestClaudeSDKServerMaxTokens:
    """max_tokens must come from AGENT_MAX_TOKENS env var, not be hardcoded."""

    def test_build_sets_agent_max_tokens_env_var(self) -> None:
        """build() must write AGENT_MAX_TOKENS into the Dockerfile."""
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "anthropic>=0.40.0",
            }
        )
        config = _make_config(model={"primary": "claude-opus-4-6", "max_tokens": 4096})
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "AGENT_MAX_TOKENS" in dockerfile
        assert "4096" in dockerfile

    def test_build_omits_max_tokens_env_when_not_set(self) -> None:
        """If model.max_tokens is None, AGENT_MAX_TOKENS should not appear in Dockerfile."""
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {
                "agent.py": "agent = None",
                "requirements.txt": "anthropic>=0.40.0",
            }
        )
        # _make_config() uses no max_tokens by default
        config = _make_config()
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "AGENT_MAX_TOKENS" not in dockerfile


class TestBuildEnvBlockSecurity:
    """_build_env_block must sanitize values against Dockerfile injection."""

    def test_build_strips_newlines_from_model_primary(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {"agent.py": "agent = None", "requirements.txt": "anthropic>=0.40.0"}
        )
        config = _make_config(model={"primary": "claude-sonnet\nRUN rm -rf /"})
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        # Verify newline is stripped (converted to space) —
        # injection becomes a string value, not a command
        assert 'ENV AGENT_MODEL="claude-sonnet RUN rm -rf /"' in dockerfile
        # Verify it's not a standalone RUN instruction that would execute
        assert "\nRUN rm -rf /" not in dockerfile

    def test_build_escapes_quotes_in_deploy_env_vars(self) -> None:
        runtime = ClaudeSDKRuntime()
        agent_dir = _make_agent_dir(
            {"agent.py": "agent = None", "requirements.txt": "anthropic>=0.40.0"}
        )
        config = _make_config(deploy={"cloud": "local", "env_vars": {"MY_KEY": 'val"ue'}})
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert 'MY_KEY="val\\"ue"' in dockerfile
