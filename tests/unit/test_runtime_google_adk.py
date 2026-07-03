"""Tests for engine/runtimes/google_adk.py — Google ADK runtime builder."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from engine.config_parser import AgentConfig, FrameworkType
from engine.runtimes.google_adk import GoogleADKRuntime


def _make_config(**overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "test",
        "owner": "test@example.com",
        "framework": FrameworkType.google_adk,
        "model": {"primary": "gemini-2.0-flash"},
        "deploy": {"cloud": "gcp"},
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_agent_dir(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """Create a temp directory with agent files using pytest's tmp_path."""
    if files:
        for name, content in files.items():
            (tmp_path / name).write_text(content)
    return tmp_path


class TestGoogleADKRuntimeValidate:
    def test_validate_valid_agent(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {
                "agent.py": "from google.adk.agents import Agent\nroot_agent = Agent(name='t')",
                "requirements.txt": "google-adk>=0.3.0",
            },
        )
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is True
        assert result.errors == []

    def test_validate_missing_agent_py(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(tmp_path, {"requirements.txt": "google-adk>=0.3.0"})
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert any("agent.py" in e for e in result.errors)

    def test_validate_missing_requirements(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(tmp_path, {"agent.py": "root_agent = None"})
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert any("requirements" in e.lower() for e in result.errors)

    def test_validate_pyproject_accepted(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {
                "agent.py": "root_agent = None",
                "pyproject.toml": "[project]\nname='test'",
            },
        )
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is True

    def test_validate_multiple_errors_empty_dir(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(tmp_path, {})
        config = _make_config()
        result = runtime.validate(agent_dir, config)
        assert result.valid is False
        assert len(result.errors) == 2  # missing agent.py + missing requirements

    def test_validate_missing_google_cloud_project_is_non_fatal(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Missing GOOGLE_CLOUD_PROJECT reference should warn but not fail validation."""
        import logging

        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {
                # agent.py has no GOOGLE_CLOUD_PROJECT reference
                "agent.py": "root_agent = None",
                "requirements.txt": "google-adk>=0.3.0",
            },
        )
        config = _make_config()
        with caplog.at_level(logging.WARNING, logger="engine.runtimes.google_adk"):
            result = runtime.validate(agent_dir, config)
        assert result.valid is True
        assert result.errors == []
        assert any("GOOGLE_CLOUD_PROJECT" in record.message for record in caplog.records)


class TestGoogleADKRuntimeBuild:
    def test_build_creates_container_image(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {
                "agent.py": "root_agent = None",
                "requirements.txt": "google-adk>=0.3.0",
            },
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        assert image.tag == "agentbreeder/test-agent:1.0.0"
        assert image.context_dir.exists()
        assert (image.context_dir / "Dockerfile").exists()
        assert (image.context_dir / "requirements.txt").exists()
        assert (image.context_dir / "agent.py").exists()
        assert "FROM python:3.11-slim" in image.dockerfile_content

    def test_build_copies_server_template(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {
                "agent.py": "root_agent = None",
                "requirements.txt": "google-adk>=0.3.0",
            },
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        server_file = image.context_dir / "server.py"
        assert server_file.exists()
        server_content = server_file.read_text()
        assert "FastAPI" in server_content
        assert "Google ADK" in server_content

    def test_build_merges_requirements(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {
                "agent.py": "root_agent = None",
                "requirements.txt": "custom-package>=1.0\ngoogle-adk>=0.3.0",
            },
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        reqs = (image.context_dir / "requirements.txt").read_text()
        assert "custom-package>=1.0" in reqs
        assert "google-adk" in reqs
        assert "fastapi" in reqs.lower()
        assert "uvicorn" in reqs.lower()

    def test_build_skips_hidden_files(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {
                "agent.py": "root_agent = None",
                "requirements.txt": "google-adk>=0.3.0",
                ".env": "GOOGLE_CLOUD_PROJECT=secret",
            },
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        assert not (image.context_dir / ".env").exists()

    def test_build_skips_pycache(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {
                "agent.py": "root_agent = None",
                "requirements.txt": "google-adk>=0.3.0",
            },
        )
        pycache = agent_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "agent.cpython-311.pyc").write_text("bytecode")
        config = _make_config()
        image = runtime.build(agent_dir, config)
        assert not (image.context_dir / "__pycache__").exists()

    def test_build_tag_format(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {
                "agent.py": "root_agent = None",
                "requirements.txt": "google-adk>=0.3.0",
            },
        )
        config = _make_config(name="my-adk-agent", version="3.2.1")
        image = runtime.build(agent_dir, config)
        assert image.tag == "agentbreeder/my-adk-agent:3.2.1"

    def test_dockerfile_contains_adc_comment(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {
                "agent.py": "root_agent = None",
                "requirements.txt": "google-adk>=0.3.0",
            },
        )
        config = _make_config()
        image = runtime.build(agent_dir, config)
        assert "GOOGLE_APPLICATION_CREDENTIALS" in image.dockerfile_content


class TestGoogleADKRuntimeEntrypoint:
    def test_get_entrypoint(self) -> None:
        runtime = GoogleADKRuntime()
        config = _make_config()
        entrypoint = runtime.get_entrypoint(config)
        assert "uvicorn" in entrypoint
        assert "8080" in entrypoint
        assert "server:app" in entrypoint


class TestGoogleADKRuntimeRequirements:
    def test_get_requirements_adds_litellm_for_ollama_model(self) -> None:
        from engine.runtimes.google_adk import GoogleADKRuntime

        config = _make_config(model={"primary": "ollama/gemma3:27b"})
        runtime = GoogleADKRuntime()
        reqs = runtime.get_requirements(config)
        assert any("litellm" in r for r in reqs)

    def test_server_loader_uses_litellm_for_ollama_model(self) -> None:
        from engine.runtimes.google_adk import SERVER_LOADER_CONTENT

        assert "LiteLlm" in SERVER_LOADER_CONTENT
        assert "ollama/" in SERVER_LOADER_CONTENT or "startswith" in SERVER_LOADER_CONTENT

    def test_server_template_uses_litellm_for_model_override(self) -> None:
        template = (
            Path(__file__).parent.parent.parent / "engine/runtimes/templates/google_adk_server.py"
        ).read_text()
        assert "LiteLlm" in template
        assert "OLLAMA_BASE_URL" in template

    def test_get_requirements_includes_google_adk(self) -> None:
        runtime = GoogleADKRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        assert any("google-adk" in r for r in reqs)

    def test_get_requirements_includes_core_deps(self) -> None:
        runtime = GoogleADKRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        assert any("google-generativeai" in r for r in reqs)
        assert any("fastapi" in r for r in reqs)
        assert any("uvicorn" in r for r in reqs)
        assert any("httpx" in r for r in reqs)
        assert any("pydantic" in r for r in reqs)

    def test_get_requirements_returns_list(self) -> None:
        runtime = GoogleADKRuntime()
        config = _make_config()
        reqs = runtime.get_requirements(config)
        assert isinstance(reqs, list)
        assert len(reqs) > 0
        assert all(isinstance(r, str) for r in reqs)


# ---------------------------------------------------------------------------
# build() — env var injection
# ---------------------------------------------------------------------------


class TestGoogleADKRuntimeEnvVarInjection:
    def test_build_writes_agent_model_env_var(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {"agent.py": "root_agent = None", "requirements.txt": "google-adk>=0.3.0"},
        )
        config = _make_config(model={"primary": "gemini-1.5-pro"})
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "AGENT_MODEL" in dockerfile
        assert "gemini-1.5-pro" in dockerfile

    def test_build_writes_deploy_env_vars(self, tmp_path: Path) -> None:
        runtime = GoogleADKRuntime()
        agent_dir = _make_agent_dir(
            tmp_path,
            {"agent.py": "root_agent = None", "requirements.txt": "google-adk>=0.3.0"},
        )
        config = _make_config(
            deploy={"cloud": "gcp", "env_vars": {"GOOGLE_CLOUD_PROJECT": "my-proj"}}
        )
        image = runtime.build(agent_dir, config)
        dockerfile = (image.context_dir / "Dockerfile").read_text()
        assert "GOOGLE_CLOUD_PROJECT" in dockerfile
        assert "my-proj" in dockerfile


# ---------------------------------------------------------------------------
# BUG-1: module-level Runner reuse + session_id in request/response
# ---------------------------------------------------------------------------


def _load_adk_server_module(module_alias: str) -> types.ModuleType:
    """Load google_adk_server.py with google.adk stubs injected into sys.modules."""
    # Clean up any previously loaded alias
    for key in list(sys.modules.keys()):
        if module_alias in key:
            del sys.modules[key]

    # Build minimal google.adk namespace stubs
    fake_google = types.ModuleType("google")
    fake_adk = types.ModuleType("google.adk")
    fake_runners = types.ModuleType("google.adk.runners")
    fake_sessions = types.ModuleType("google.adk.sessions")
    fake_genai = types.ModuleType("google.genai")
    fake_genai_types = types.ModuleType("google.genai.types")

    fake_runners.Runner = MagicMock()
    fake_sessions.InMemorySessionService = MagicMock()
    fake_google.adk = fake_adk
    fake_google.genai = fake_genai
    fake_genai.types = fake_genai_types

    # Snapshot the real google.* modules so we can restore them afterwards.
    # Without this, the fake `google` namespace leaks into sys.modules and
    # breaks later tests that import the real google.cloud.run_v2 protos
    # (e.g. the Cloud Run deployer's idempotency tests).
    stub_keys = (
        "google",
        "google.adk",
        "google.adk.runners",
        "google.adk.sessions",
        "google.genai",
        "google.genai.types",
    )
    saved = {key: sys.modules.get(key) for key in stub_keys}

    sys.modules["google"] = fake_google
    sys.modules["google.adk"] = fake_adk
    sys.modules["google.adk.runners"] = fake_runners
    sys.modules["google.adk.sessions"] = fake_sessions
    sys.modules["google.genai"] = fake_genai
    sys.modules["google.genai.types"] = fake_genai_types

    try:
        spec = importlib.util.spec_from_file_location(
            module_alias,
            "engine/runtimes/templates/google_adk_server.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # The loaded module has already bound the fake Runner/session symbols at
        # import time, so it keeps working after we restore the real modules.
        return mod
    finally:
        for key, original in saved.items():
            if original is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = original


class TestGoogleADKServerRunnerReuse:
    """The module-level _runner must be reused across requests."""

    def test_invoke_request_schema_accepts_session_id(self) -> None:
        """InvokeRequest should have an optional session_id field."""
        mod = _load_adk_server_module("google_adk_server_test1")

        req = mod.InvokeRequest(input="hello")
        assert hasattr(req, "session_id"), "InvokeRequest must have session_id field"
        assert req.session_id is None  # default None

        req_with_session = mod.InvokeRequest(input="hello", session_id="abc-123")
        assert req_with_session.session_id == "abc-123"

    def test_invoke_response_has_session_id(self) -> None:
        """InvokeResponse must echo back a session_id."""
        mod = _load_adk_server_module("google_adk_server_test2")

        resp = mod.InvokeResponse(output="hello", session_id="abc-123")
        assert resp.session_id == "abc-123"
