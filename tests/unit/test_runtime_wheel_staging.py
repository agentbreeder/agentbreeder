"""Tests for local-wheel staging in Python runtime Dockerfiles.

When an agent is deployed from an unpublished dev checkout, the runtime is
bundled as a local ``*.whl`` referenced via
``AGENTBREEDER_RUNTIME_REQUIREMENT=./<wheel>``. That wheel must be ``COPY``ed
into the image *before* ``pip install -r requirements.txt`` runs, otherwise the
path requirement is unresolvable and the build fails with a non-zero pip exit.

These tests cover the shared :func:`inject_wheel_copies` helper and verify each
Python runtime's ``build()`` honours a bundled wheel — previously only the
LangGraph runtime did, breaking dev-checkout deploys of every other framework.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from engine.config_parser import AgentConfig, FrameworkType
from engine.runtimes.base import inject_wheel_copies


def _agent_dir(files: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp())
    for name, content in files.items():
        (d / name).write_text(content)
    return d


class TestInjectWheelCopies:
    def test_no_wheels_is_noop(self, tmp_path: Path) -> None:
        template = (
            "FROM python:3.11-slim\nCOPY requirements.txt .\nRUN pip install -r requirements.txt\n"
        )
        assert inject_wheel_copies(template, tmp_path) == template

    def test_single_wheel_copied_before_pip(self, tmp_path: Path) -> None:
        (tmp_path / "agentbreeder-2.1.1.dev1-py3-none-any.whl").write_text("")
        template = "COPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\n"
        result = inject_wheel_copies(template, tmp_path)
        assert "COPY agentbreeder-2.1.1.dev1-py3-none-any.whl ./" in result
        # Wheel COPY must precede the pip install line.
        assert result.index("COPY agentbreeder-2.1.1.dev1") < result.index("pip install")
        # And must come after the requirements.txt COPY (cache ordering preserved).
        assert result.index("COPY requirements.txt") < result.index("COPY agentbreeder-2.1.1.dev1")

    def test_multiple_wheels_sorted(self, tmp_path: Path) -> None:
        (tmp_path / "b_pkg.whl").write_text("")
        (tmp_path / "a_pkg.whl").write_text("")
        template = "COPY requirements.txt .\nRUN pip install -r requirements.txt\n"
        result = inject_wheel_copies(template, tmp_path)
        assert result.index("COPY a_pkg.whl") < result.index("COPY b_pkg.whl")

    def test_only_first_requirements_copy_replaced(self, tmp_path: Path) -> None:
        (tmp_path / "x.whl").write_text("")
        template = "COPY requirements.txt .\nRUN pip install -r requirements.txt\nCOPY requirements.txt .\n"
        result = inject_wheel_copies(template, tmp_path)
        assert result.count("COPY x.whl ./") == 1


def _config(framework: FrameworkType, model: str = "claude-sonnet-4-6") -> AgentConfig:
    return AgentConfig(
        name="wheel-agent",
        version="1.0.0",
        team="test",
        owner="test@example.com",
        framework=framework,
        model={"primary": model},
        deploy={"cloud": "local"},
    )


# (framework, runtime import path, agent files) for each pip-based Python runtime.
_RUNTIME_CASES = [
    pytest.param(
        FrameworkType.claude_sdk,
        "engine.runtimes.claude_sdk:ClaudeSDKRuntime",
        {
            "agent.py": "import anthropic\nagent = anthropic.AsyncAnthropic()",
            "requirements.txt": "anthropic\n",
        },
        id="claude_sdk",
    ),
    pytest.param(
        FrameworkType.langgraph,
        "engine.runtimes.langgraph:LangGraphRuntime",
        {"agent.py": "graph = object()\n", "requirements.txt": "langgraph\n"},
        id="langgraph",
    ),
    pytest.param(
        FrameworkType.crewai,
        "engine.runtimes.crewai:CrewAIRuntime",
        {"agent.py": "crew = object()\n", "requirements.txt": "crewai\n"},
        id="crewai",
    ),
    pytest.param(
        FrameworkType.openai_agents,
        "engine.runtimes.openai_agents:OpenAIAgentsRuntime",
        {"agent.py": "agent = object()\n", "requirements.txt": "openai-agents\n"},
        id="openai_agents",
    ),
    pytest.param(
        FrameworkType.google_adk,
        "engine.runtimes.google_adk:GoogleADKRuntime",
        {"agent.py": "root_agent = object()\n", "requirements.txt": "google-adk\n"},
        id="google_adk",
    ),
    pytest.param(
        # Custom fallback path (no BYO Dockerfile): the generated fallback
        # Dockerfile must stage wheels. BYO Dockerfiles are intentionally left
        # untouched — those users own their own wheel handling.
        FrameworkType.custom,
        "engine.runtimes.custom:CustomRuntime",
        {"agent.py": "app = object()\n", "requirements.txt": "flask\n"},
        id="custom",
    ),
]


def _load_runtime(path: str) -> object:
    module_name, cls_name = path.split(":")
    import importlib

    return getattr(importlib.import_module(module_name), cls_name)()


@pytest.mark.parametrize("framework, runtime_path, files", _RUNTIME_CASES)
def test_build_stages_bundled_wheel(
    framework: FrameworkType, runtime_path: str, files: dict[str, str]
) -> None:
    agent_dir = _agent_dir(files)
    (agent_dir / "agentbreeder-2.1.1.dev112-py3-none-any.whl").write_text("")
    runtime = _load_runtime(runtime_path)
    image = runtime.build(agent_dir, _config(framework))
    dockerfile = (image.context_dir / "Dockerfile").read_text()
    assert "COPY agentbreeder-2.1.1.dev112-py3-none-any.whl ./" in dockerfile
    assert dockerfile.index("agentbreeder-2.1.1.dev112") < dockerfile.index("pip install")


@pytest.mark.parametrize("framework, runtime_path, files", _RUNTIME_CASES)
def test_build_without_wheel_has_no_copy(
    framework: FrameworkType, runtime_path: str, files: dict[str, str]
) -> None:
    agent_dir = _agent_dir(files)
    runtime = _load_runtime(runtime_path)
    image = runtime.build(agent_dir, _config(framework))
    dockerfile = (image.context_dir / "Dockerfile").read_text()
    assert ".whl ./" not in dockerfile
