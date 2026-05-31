import pytest

from engine.config_parser import AgentConfig, DeployConfig, ModelConfig
from engine.runtimes.claude_sdk import ClaudeSDKRuntime
from engine.runtimes.crewai import CrewAIRuntime
from engine.runtimes.google_adk import GoogleADKRuntime
from engine.runtimes.langgraph import LangGraphRuntime
from engine.runtimes.openai_agents import OpenAIAgentsRuntime


def _cfg(model="claude-sonnet-4"):
    return AgentConfig(
        # framework is unused by get_requirements(); any valid enum value works here
        name="x", version="1.0.0", team="t", owner="o@e.com", framework="langgraph",
        model=ModelConfig(primary=model), deploy=DeployConfig(cloud="aws"),
    )


@pytest.mark.parametrize("runtime", [
    LangGraphRuntime(), ClaudeSDKRuntime(), OpenAIAgentsRuntime(),
    CrewAIRuntime(), GoogleADKRuntime(),
])
def test_get_requirements_includes_agentbreeder(runtime, monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "agentbreeder==1.2.3")
    deps = runtime.get_requirements(_cfg())
    assert any(d == "agentbreeder==1.2.3" for d in deps), deps


def test_opt_out_excludes_agentbreeder(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "")
    deps = LangGraphRuntime().get_requirements(_cfg())
    assert not any(d.startswith("agentbreeder") for d in deps), deps
