"""Unit test: a built image bundles the agentbreeder runtime (Task 2)
and bakes a ``prompts/<name>`` ref into the Dockerfile ENV (Task 4).
"""

from pathlib import Path

from engine.config_parser import AgentConfig, DeployConfig, ModelConfig, PromptsConfig
from engine.resolver import resolve_dependencies
from engine.runtimes.langgraph import LangGraphRuntime


def _agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agent"
    d.mkdir()
    (d / "prompts").mkdir()
    (d / "prompts" / "sys.md").write_text("You are a baked agent.")
    (d / "agent.py").write_text("graph = None\n")
    (d / "requirements.txt").write_text("langgraph>=0.2.0\n")
    return d


def test_built_image_installs_engine_and_bakes_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "agentbreeder==1.2.3")
    agent_dir = _agent_dir(tmp_path)
    cfg = AgentConfig(
        name="baked",
        version="1.0.0",
        team="t",
        owner="o@e.com",
        framework="langgraph",
        model=ModelConfig(primary="claude-sonnet-4"),
        deploy=DeployConfig(cloud="aws"),
        prompts=PromptsConfig(system="prompts/sys"),
    )
    cfg = resolve_dependencies(cfg, project_root=agent_dir)
    image = LangGraphRuntime().build(agent_dir, cfg)

    reqs = (image.context_dir / "requirements.txt").read_text()
    assert "agentbreeder==1.2.3" in reqs.splitlines()  # engine bundled (Task 2)
    assert (
        'AGENT_SYSTEM_PROMPT="You are a baked agent."' in image.dockerfile_content
    )  # prompt baked (Task 4)
