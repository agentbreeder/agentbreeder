"""agent.yaml authoring schema v1 and file-kind dispatch (#729, spec §5)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentbreeder import cli
from agentbreeder.agentfile import AgentFileError, detect, load_agent_file

NORTHWIND = """\
kind: agent
schema_version: 1
name: northwind-support
runtime: runtime/adk@1.0.0
description: Customer support for Northwind.
model:
  alias: support-model
  reasoning: low
  max_model_calls: 4
  budget_usd: 25
  rpm: 120
prompt: {ref: prompt/northwind-support@1.0.0}
knowledge:
  - {ref: knowledge/northwind-help@1.0.0, top_k: 5, extra_searches: 1}
tools:
  - {ref: tool/customer-notes@1.0.0}
  - {ref: tool/orders@1.0.0}
memory: {sessions: postgres, notes: true}
agent_events: postgres
traces: otlp
evals: {ref: eval/northwind-golden@1.0.0, threshold: 1.0, retries: 1}
"""


def write(tmp_path: Path, text: str, name: str = "agent.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def mutate(**changes) -> str:
    data = yaml.safe_load(NORTHWIND)
    for dotted, value in changes.items():
        target = data
        *parents, leaf = dotted.split("__")
        for part in parents:
            target = target[part]
        if value is None:
            target.pop(leaf, None)
        else:
            target[leaf] = value
    return yaml.safe_dump(data)


def test_northwind_example_is_valid(tmp_path):
    agent = load_agent_file(write(tmp_path, NORTHWIND))
    assert agent.name == "northwind-support"
    assert agent.refs() == [
        "runtime/adk@1.0.0",
        "prompt/northwind-support@1.0.0",
        "knowledge/northwind-help@1.0.0",
        "tool/customer-notes@1.0.0",
        "tool/orders@1.0.0",
        "eval/northwind-golden@1.0.0",
    ]
    assert agent.paths() == []
    assert agent.knowledge[0].top_k == 5


def test_minimal_defaults(tmp_path):
    text = (
        "kind: agent\nschema_version: 1\nname: tiny\nruntime: runtime/adk@1.0.0\n"
        "model: {alias: m}\nprompt: {path: prompts/dev.md}\n"
    )
    agent = load_agent_file(write(tmp_path, text))
    assert agent.model.max_model_calls == 4 and agent.memory.sessions == "postgres"
    assert agent.paths() == ["prompts/dev.md"] and agent.evals is None


@pytest.mark.parametrize(
    "changes,needle",
    [
        ({"name": "x" * 54}, "name"),
        ({"name": "Bad_Name"}, "name"),
        ({"runtime": "tool/orders@1.0.0"}, "runtime: must be a runtime/"),
        ({"prompt": {"ref": "tool/orders@1.0.0"}}, "prompt/"),
        ({"prompt": {"ref": "prompt/a@1.0.0", "path": "p.md"}}, "exactly one"),
        ({"prompt": {}}, "exactly one"),
        ({"prompt": {"path": "../outside.md"}}, "inside the project"),
        ({"prompt": {"path": "/etc/passwd"}}, "inside the project"),
        ({"tools": [{"ref": "tool/orders"}]}, "tools[0].ref: not a pinned"),
        ({"tools": [{"ref": "tool/orders@1.0.0"}, {"ref": "tool/orders@1.0.0"}]}, "duplicates"),
        ({"memory__notes": True, "tools": [{"ref": "tool/orders@1.0.0"}]}, "customer-notes"),
        ({"model__max_model_calls": 0}, "max_model_calls"),
        ({"model__reasoning": "extreme"}, "reasoning"),
        ({"model__budget_usd": -1}, "budget_usd"),
        ({"surprise": 1}, "surprise"),
        ({"evals__threshold": 1.5}, "threshold"),
        ({"knowledge": [{"ref": "knowledge/k@1.0.0", "top_k": 99}]}, "top_k"),
        (
            {"knowledge": [{"ref": "knowledge/a@1.0.0"}, {"ref": "knowledge/b@1.0.0"}]},
            "knowledge",
        ),
        ({"memory__sessions": "in_memory"}, "sessions"),
    ],
)
def test_invalid_agent_files(tmp_path, changes, needle):
    with pytest.raises(AgentFileError) as error:
        load_agent_file(write(tmp_path, mutate(**changes)))
    assert error.value.code == "AGENT_INVALID"
    assert needle in str(error.value)


def test_errors_never_echo_values(tmp_path):
    canary = "canary-value-must-not-print"
    with pytest.raises(AgentFileError) as error:
        load_agent_file(write(tmp_path, mutate(model__alias=canary + " !")))
    assert canary not in str(error.value)


def test_detect_kinds():
    assert detect({"kind": "agent"}) == "agent"
    assert detect({"schema_version": 2, "name": "x", "image": "sha256:" + "a" * 64}) == "manifest"
    assert detect({"spec_version": "v1", "name": "x"}) == "legacy"
    assert detect({"name": "x", "framework": "langgraph", "model": {}}) == "legacy"
    assert detect({"hello": 1}) == "unknown"


def test_wrong_file_kinds(tmp_path):
    manifest = write(
        tmp_path, "schema_version: 1\nname: x\nimage: sha256:" + "a" * 64 + "\n", "m.yaml"
    )
    with pytest.raises(AgentFileError) as error:
        load_agent_file(manifest)
    assert error.value.code == "WRONG_FILE_KIND" and "deploy manifest" in str(error.value)
    legacy = write(tmp_path, "name: x\nframework: langgraph\nmodel: {primary: gpt}\n", "l.yaml")
    with pytest.raises(AgentFileError, match="2.x"):
        load_agent_file(legacy)
    with pytest.raises(AgentFileError, match="kind: agent"):
        load_agent_file(write(tmp_path, "hello: 1\n", "u.yaml"))
    with pytest.raises(AgentFileError) as error:
        load_agent_file(write(tmp_path, "a: [\n", "bad.yaml"))
    assert error.value.code == "AGENT_INVALID"


def test_cli_validate_dispatch(tmp_path, capsys):
    agent = write(tmp_path, NORTHWIND)
    assert cli.main(["validate", str(agent)]) == 0
    assert "Valid agent file: northwind-support (6 registry refs)" in capsys.readouterr().out
    assert cli.main(["validate", str(agent), "--json"]) == 0
    assert '"name": "northwind-support"' in capsys.readouterr().out
    legacy = write(tmp_path, "name: x\nframework: langgraph\nmodel: {primary: gpt}\n", "l.yaml")
    assert cli.main(["validate", str(legacy)]) == 2
    assert "2.x" in capsys.readouterr().err
    broken = write(tmp_path, mutate(name="BAD"), "b.yaml")
    assert cli.main(["validate", str(broken)]) == 2
    manifest = write(
        tmp_path, "schema_version: 1\nname: x\nimage: sha256:" + "a" * 64 + "\n", "m.yaml"
    )
    assert cli.main(["validate", str(manifest)]) == 0
    assert "Valid manifest" in capsys.readouterr().out
    missing = tmp_path / "missing.yaml"
    assert cli.main(["validate", str(missing)]) == 2


def test_cli_deploy_refuses_agent_file(tmp_path, capsys):
    agent = write(tmp_path, NORTHWIND)
    code = cli.main(["deploy", str(agent), "--local", "--state-dir", str(tmp_path / "s")])
    assert code == 2
    err = capsys.readouterr().err
    assert "WRONG_FILE_KIND" in err and "agentbreeder build" in err


def test_ref_errors_never_echo_the_ref_value(tmp_path):
    canary = "canary-pasted-into-a-ref"
    for changes in (
        {"tools": [{"ref": canary}]},
        {"runtime": canary},
        {"prompt": {"ref": "prompt/" + canary}},
        {"evals": {"ref": canary}},
    ):
        with pytest.raises(AgentFileError) as error:
            load_agent_file(write(tmp_path, mutate(**changes)))
        assert canary not in str(error.value)
