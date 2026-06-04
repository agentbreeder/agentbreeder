"""Tests for engine/runtimes/ — runtime builder registry and LangGraph runtime."""

from __future__ import annotations

import tempfile
from pathlib import Path

from engine.config_parser import AgentConfig, FrameworkType
from engine.runtimes import get_runtime
from engine.runtimes.base import LITELLM_PREFIXES, _get_litellm_requirements, _is_litellm_model
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

    def test_get_crewai_runtime(self) -> None:
        from engine.runtimes.crewai import CrewAIRuntime

        runtime = get_runtime(FrameworkType.crewai)
        assert isinstance(runtime, CrewAIRuntime)

    def test_get_claude_sdk_runtime(self) -> None:
        from engine.runtimes.claude_sdk import ClaudeSDKRuntime

        runtime = get_runtime(FrameworkType.claude_sdk)
        assert isinstance(runtime, ClaudeSDKRuntime)

    def test_get_google_adk_runtime(self) -> None:
        from engine.runtimes.google_adk import GoogleADKRuntime

        runtime = get_runtime(FrameworkType.google_adk)
        assert isinstance(runtime, GoogleADKRuntime)

    def test_get_custom_runtime(self) -> None:
        from engine.runtimes.custom import CustomRuntime

        runtime = get_runtime(FrameworkType.custom)
        assert isinstance(runtime, CustomRuntime)

    def test_all_framework_types_have_runtime(self) -> None:
        """Every FrameworkType value must have a registered runtime builder."""
        for framework in FrameworkType:
            runtime = get_runtime(framework)
            assert runtime is not None, f"No runtime for {framework.value}"


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

    def test_get_requirements_adds_litellm_for_ollama_model(self) -> None:
        runtime = LangGraphRuntime()
        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        reqs = runtime.get_requirements(config)
        assert any("litellm" in r for r in reqs)

    def test_build_injects_ollama_base_url_for_ollama_model(self, tmp_path: Path) -> None:
        (tmp_path / "agent.py").write_text("graph = None")
        (tmp_path / "requirements.txt").write_text("")
        runtime = LangGraphRuntime()
        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        image = runtime.build(tmp_path, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "OLLAMA_BASE_URL" in dockerfile

    def test_build_does_not_inject_ollama_url_for_non_ollama_model(self, tmp_path: Path) -> None:
        (tmp_path / "agent.py").write_text("graph = None")
        (tmp_path / "requirements.txt").write_text("")
        runtime = LangGraphRuntime()
        config = _make_config(model={"primary": "gpt-4o"})
        image = runtime.build(tmp_path, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "OLLAMA_BASE_URL" not in dockerfile


class TestLiteLLMHelpers:
    def test_is_litellm_model_true_for_ollama(self) -> None:
        assert _is_litellm_model("ollama/gemma3:27b") is True

    def test_is_litellm_model_true_for_groq(self) -> None:
        assert _is_litellm_model("groq/llama3-8b-8192") is True

    def test_is_litellm_model_true_for_bedrock(self) -> None:
        assert _is_litellm_model("bedrock/claude-3") is True

    def test_is_litellm_model_true_for_azure(self) -> None:
        assert _is_litellm_model("azure/gpt-4o") is True

    def test_is_litellm_model_true_for_cohere(self) -> None:
        assert _is_litellm_model("cohere/command-r") is True

    def test_is_litellm_model_true_for_mistral(self) -> None:
        assert _is_litellm_model("mistral/mistral-large") is True

    def test_is_litellm_model_false_for_native_openai(self) -> None:
        assert _is_litellm_model("gpt-4o") is False

    def test_is_litellm_model_false_for_gemini(self) -> None:
        assert _is_litellm_model("gemini-2.0-flash") is False

    def test_is_litellm_model_false_for_claude(self) -> None:
        assert _is_litellm_model("claude-sonnet-4") is False

    def test_get_litellm_requirements_returns_litellm(self) -> None:
        reqs = _get_litellm_requirements()
        assert any("litellm" in r for r in reqs)

    def test_litellm_prefixes_covers_major_providers(self) -> None:
        expected = {"ollama/", "groq/", "bedrock/", "azure/", "cohere/", "mistral/"}
        assert expected.issubset(set(LITELLM_PREFIXES))


class TestLiteLLMRequirementsAcrossRuntimes:
    """All runtimes should add litellm for any LiteLLM-prefixed model, not just ollama/."""

    def test_langgraph_adds_litellm_for_groq_model(self) -> None:
        runtime = LangGraphRuntime()
        config = _make_config(model={"primary": "groq/llama3-8b-8192"})
        assert any("litellm" in r for r in runtime.get_requirements(config))

    def test_langgraph_adds_litellm_for_bedrock_model(self) -> None:
        runtime = LangGraphRuntime()
        config = _make_config(model={"primary": "bedrock/claude-3"})
        assert any("litellm" in r for r in runtime.get_requirements(config))

    def test_langgraph_no_litellm_for_native_model(self) -> None:
        runtime = LangGraphRuntime()
        config = _make_config(model={"primary": "gpt-4o"})
        assert not any("litellm" in r for r in runtime.get_requirements(config))


class TestAgentDirPrecondition:
    """Audit A11: validate() must fail clearly when ``agent_dir`` is missing
    or not a directory, across every supported runtime."""

    def _runtimes(self) -> list:
        from engine.runtimes.claude_sdk import ClaudeSDKRuntime
        from engine.runtimes.crewai import CrewAIRuntime
        from engine.runtimes.custom import CustomRuntime
        from engine.runtimes.google_adk import GoogleADKRuntime
        from engine.runtimes.openai_agents import OpenAIAgentsRuntime

        return [
            LangGraphRuntime(),
            CrewAIRuntime(),
            ClaudeSDKRuntime(),
            OpenAIAgentsRuntime(),
            GoogleADKRuntime(),
            CustomRuntime(),
        ]

    def test_missing_agent_dir_is_rejected(self) -> None:
        config = _make_config()
        for runtime in self._runtimes():
            framework = type(runtime).__name__
            # claude_sdk needs a non-litellm Claude model so validate_config passes
            if framework == "ClaudeSDKRuntime":
                cfg = _make_config(model={"primary": "claude-sonnet-4"})
            else:
                cfg = config
            missing = Path("/tmp/definitely-does-not-exist-agentbreeder-a11")
            result = runtime.validate(missing, cfg)
            assert result.valid is False, f"{framework} accepted a missing agent_dir"
            assert any("does not exist" in e for e in result.errors), (
                f"{framework} did not surface a clear 'does not exist' error: {result.errors}"
            )

    def test_agent_dir_is_a_file_is_rejected(self) -> None:
        config = _make_config()
        # Create a file (not a directory)
        f = Path(tempfile.mkstemp(suffix="-a11-not-a-dir")[1])
        try:
            for runtime in self._runtimes():
                framework = type(runtime).__name__
                if framework == "ClaudeSDKRuntime":
                    cfg = _make_config(model={"primary": "claude-sonnet-4"})
                else:
                    cfg = config
                result = runtime.validate(f, cfg)
                assert result.valid is False, f"{framework} accepted a file as agent_dir"
                assert any("not a directory" in e for e in result.errors), (
                    f"{framework} missing 'not a directory' error: {result.errors}"
                )
        finally:
            f.unlink(missing_ok=True)


def test_langgraph_requirements_add_mcp_adapters_when_mcp_present():
    """langchain-mcp-adapters is bundled only when the agent declares MCP servers."""
    from engine.config_parser import McpServerRef
    from engine.runtimes.langgraph import LangGraphRuntime

    rt = LangGraphRuntime()
    cfg = _make_config()  # helper in this module
    assert not any("mcp-adapters" in d for d in rt.get_requirements(cfg))
    cfg.mcp_servers = [McpServerRef(ref="mcp/x", url="http://x")]
    assert any("langchain-mcp-adapters" in d for d in rt.get_requirements(cfg))
