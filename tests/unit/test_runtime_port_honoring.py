"""Tests that every Python runtime's container honours the ``$PORT`` env var.

When the observability sidecar is injected, the deployer makes the sidecar the
ingress container on 8080 and moves the agent to an internal port (8081) by
setting ``PORT=8081`` on the agent container. If the agent's ``CMD`` hardcodes
``--port 8080`` it collides with the sidecar and Cloud Run/ECS kills the
revision with ``address already in use`` — the startup probe never passes.

Every runtime must therefore launch uvicorn on ``${PORT:-8080}`` so a single
container still defaults to 8080 while a sidecar'd agent obeys ``PORT=8081``.
Previously only the LangGraph runtime did this; the others silently broke the
sidecar pattern for their frameworks.
"""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

import pytest

from engine.config_parser import AgentConfig, FrameworkType


def _agent_dir(files: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp())
    for name, content in files.items():
        (d / name).write_text(content)
    return d


def _config(framework: FrameworkType) -> AgentConfig:
    return AgentConfig(
        name="port-agent",
        version="1.0.0",
        team="test",
        owner="test@example.com",
        framework=framework,
        model={"primary": "claude-sonnet-4-6"},
        deploy={"cloud": "local"},
    )


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
        FrameworkType.custom,
        "engine.runtimes.custom:CustomRuntime",
        {"agent.py": "app = object()\n", "requirements.txt": "flask\n"},
        id="custom",
    ),
]


def _load_runtime(path: str) -> object:
    module_name, cls_name = path.split(":")
    return getattr(importlib.import_module(module_name), cls_name)()


@pytest.mark.parametrize("framework, runtime_path, files", _RUNTIME_CASES)
def test_dockerfile_cmd_honors_port_env(
    framework: FrameworkType, runtime_path: str, files: dict[str, str]
) -> None:
    runtime = _load_runtime(runtime_path)
    image = runtime.build(_agent_dir(files), _config(framework))
    dockerfile = (image.context_dir / "Dockerfile").read_text()
    # The launch CMD must defer the port to $PORT (defaulting to 8080), never
    # hardcode --port 8080 (which collides with the sidecar on the ingress port).
    assert "${PORT:-8080}" in dockerfile
    assert '"--port", "8080"' not in dockerfile
