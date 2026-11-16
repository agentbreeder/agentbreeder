"""Sub-agent refs outside build (#1058): `registry used-by`, `agent set-ref` and
`new agent --stack-for` read them where `AgentFile.refs()` and `aliases()` do."""

from __future__ import annotations

import copy
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from agentbreeder import newagent, registry, setref, stackfor
from agentbreeder.agentfile import load_agent_file
from agentbreeder.newagent import NewAgentError
from agentbreeder.registry import Ref, RegistryError

NORTHWIND = Path(registry.__file__).parent / "examples" / "northwind"
ORDERS = "tool/orders@1.0.0"
BILLING = {
    "name": "billing",
    "description": "Billing.",
    "model": {"alias": "billing-model"},
    "prompt": {"ref": "prompt/northwind-support@1.0.1"},
    "tools": [{"ref": ORDERS}],
}


@pytest.fixture(autouse=True)
def no_registry_path(monkeypatch, tmp_path):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv("AGENTBREEDER_STACKS_DIR", str(tmp_path / "stacks"))


def _northwind(tmp_path: Path, subagents: list) -> Path:
    project = tmp_path / "northwind"
    project.mkdir()
    for name in ("agent.yaml", "agent.lock", "stack.yaml", "stack.lock"):
        shutil.copy(NORTHWIND / name, project / name)
    text = (project / "agent.yaml").read_text()
    text = text.replace("runtime/adk@1.3.0", "runtime/adk@1.9.0")
    text += yaml.safe_dump({"subagents": subagents}, sort_keys=False)
    (project / "agent.yaml").write_text(text)
    load_agent_file(project / "agent.yaml")  # the fixture is a valid agent file
    return project


def test_used_by_names_each_subagent_position(tmp_path):
    other = {**copy.deepcopy(BILLING), "name": "returns", "prompt": {"path": "r.md"}}
    project = _northwind(tmp_path, [copy.deepcopy(BILLING), other])
    row = registry.usage(Ref.parse(ORDERS), project)
    assert [d["at"] for d in row["declared"]] == [
        "tools[2].ref",
        "subagents[0].tools[0].ref",
        "subagents[1].tools[0].ref",
    ]
    prompt = registry.usage(Ref.parse("prompt/northwind-support@1.0.1"), project)
    assert [d["at"] for d in prompt["declared"]] == ["prompt.ref", "subagents[0].prompt.ref"]


def test_used_by_ignores_malformed_subagent_entries():
    data = {"kind": "agent", "subagents": ["x", {"prompt": "y", "tools": "z", "mcp": [1]}]}
    assert list(registry._ref_positions("agent", data)) == [("runtime", None)]
    assert list(registry._ref_positions("agent", {"subagents": "x"})) == [("runtime", None)]


# -- set-ref ----------------------------------------------------------------------------------

OLD, NEW = "prompt/hello-prompt@0.1.0", "prompt/hello-prompt@0.1.1"


@pytest.fixture
def starter(tmp_path) -> Path:
    plan = newagent.plan("hello", "gemini", tmp_path / "hello", today=date(2026, 11, 13))
    project = newagent.create(plan).directory
    registry.edit_version(Ref.parse(OLD), project, sets=[("prompt.md", b"Be brief.\n")])
    return project


def _add_subagent(project: Path, prompt: str) -> None:
    path = project / "agent.yaml"
    sub = {"name": "billing", "description": "B.", "model": {"alias": "chat"}}
    sub["prompt"] = {"ref": prompt}
    path.write_text(path.read_text() + yaml.safe_dump({"subagents": [sub]}))


def test_set_ref_moves_a_ref_shared_with_a_subagent_everywhere(starter):
    _add_subagent(starter, OLD)
    result = setref.set_ref(starter, OLD, NEW)
    agent = load_agent_file(starter / "agent.yaml")
    assert agent.prompt.ref == NEW and agent.subagents[0].prompt.ref == NEW
    assert result["at"] == "prompt.ref"
    added = [line for line in result["diff"].splitlines() if line.startswith("+") and NEW in line]
    assert len(added) == 2


def test_set_ref_still_refuses_an_extra_textual_occurrence(starter):
    _add_subagent(starter, OLD)
    path = starter / "agent.yaml"
    path.write_text(path.read_text() + f"# was {OLD}\n")
    with pytest.raises(RegistryError) as caught:
        setref.set_ref(starter, OLD, NEW)
    assert caught.value.code == "REF_AMBIGUOUS"


# -- --stack-for ------------------------------------------------------------------------------


def _stack_for(project: Path) -> stackfor.StackForPlan:
    return stackfor.plan("hello", "gemini", project / "agent.yaml", today=date(2026, 11, 13))


def _bare_starter(tmp_path: Path) -> Path:
    newagent.create(newagent.plan("hello", "gemini", tmp_path / "hello", today=date(2026, 11, 13)))
    project = tmp_path / "hello"
    (project / "stack.yaml").unlink()
    shutil.rmtree(project / "stack")
    return project


def test_stack_for_serves_every_subagent_alias(tmp_path):
    project = _bare_starter(tmp_path)
    path = project / "agent.yaml"
    sub = {"name": "billing", "description": "B.", "model": {"alias": "billing"}}
    sub["prompt"] = {"path": "billing.md"}
    path.write_text(path.read_text() + yaml.safe_dump({"subagents": [sub]}))
    made = _stack_for(project)
    assert made.aliases == ("chat", "billing", "judge")
    stack = yaml.safe_load(made.files["stack.yaml"])
    assert stack["environment"]["models"] == ["chat", "billing", "judge"]
    config = yaml.safe_load(made.files["stack/litellm/config.yaml"])
    assert [m["model_name"] for m in config["model_list"]] == ["chat", "billing", "judge"]


def test_stack_for_refuses_subagent_tools(tmp_path):
    project = _bare_starter(tmp_path)
    path = project / "agent.yaml"
    sub = {"name": "billing", "description": "B.", "model": {"alias": "chat"}}
    sub |= {"prompt": {"path": "b.md"}, "tools": [{"ref": "tool/x@1.0.0"}]}
    path.write_text(path.read_text() + yaml.safe_dump({"subagents": [sub]}))
    with pytest.raises(NewAgentError) as caught:
        _stack_for(project)
    assert caught.value.code == "STACK_FOR_UNSUPPORTED"
    assert "subagents[].tools" in str(caught.value)
