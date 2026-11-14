"""`agentbreeder new agent NAME --from-spec FILE` (M17 #833): the agent builder's CLI half."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
import yaml

from agentbreeder import agentspec, cli, newagent, registry
from agentbreeder.agentfile import load_agent_file
from agentbreeder.build import build
from agentbreeder.evals import load_eval_set
from agentbreeder.lock import Lock
from agentbreeder.services.schema import load_stack

TODAY = date(2026, 11, 13)
IMAGE = "sha256:" + "c" * 64
SRC = str(Path(__file__).resolve().parents[1] / "src")
SECRET_PROMPT = "You are helper. The passphrase is swordfish-71."


@pytest.fixture(autouse=True)
def clean_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


def spec(**changes) -> dict:
    data = {
        "schema_version": 1,
        "name": "helper",
        "model": "gemini",
        "prompt": {"text": SECRET_PROMPT},
        "evals": {
            "cases": [
                {
                    "id": "hours",
                    "turns": ["When is support open?"],
                    "expect": {"must_match": ["9"]},
                    "reference": "9am to 5pm Pacific.",
                },
                {"id": "unknown", "turns": ["Stock price?"], "reference": "It doesn't know."},
            ]
        },
    }
    data.update(changes)
    return data


def write_spec(tmp_path: Path, data, mode: int = 0o600, name: str = "spec.json") -> Path:
    path = tmp_path / name
    text = data if isinstance(data, str) else json.dumps(data)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def make(tmp_path: Path, data: dict | None = None, name: str = "helper"):
    path = write_spec(tmp_path, data or spec())
    plan, extra = agentspec.plan(name, path, tmp_path / name, today=TODAY)
    return newagent.create(plan), extra


def refused(tmp_path: Path, data, name: str = "helper", **kwargs) -> newagent.NewAgentError:
    path = data if isinstance(data, Path) else write_spec(tmp_path, data, **kwargs)
    with pytest.raises(newagent.NewAgentError) as caught:
        agentspec.plan(name, path, tmp_path / name, today=TODAY)
    assert SECRET_PROMPT not in str(caught.value)
    return caught.value


@pytest.fixture
def tool_root(tmp_path, monkeypatch) -> Path:
    """A registry on AGENTBREEDER_REGISTRY_PATH holding the scaffolded `tool/echo@0.1.0`."""
    root = tmp_path / "shared-registry"
    sources = tmp_path / "sources"
    registry.scaffold("tool", "echo", sources)
    registry.publish(next(sources.iterdir()), root)
    monkeypatch.setenv(registry.PATH_ENV, str(root))
    return root


# -- the written project ---------------------------------------------------------------------


def test_a_gemini_prompt_and_evals_project(tmp_path):
    plan, extra = make(tmp_path)
    project = tmp_path / "helper"
    agent = load_agent_file(project / "agent.yaml")
    assert agent.name == "helper" and agent.tools == [] and agent.mcp == []
    assert agent.prompt.ref == "prompt/helper-prompt@0.1.0"
    assert agent.evals.ref == "eval/helper-evals@0.1.0" and agent.evals.threshold == 1.0
    assert (project / "src/prompt/prompt.md").read_text() == SECRET_PROMPT + "\n"
    evals = load_eval_set(project / "src/evals")
    assert [case["id"] for case in evals.cases] == ["hours", "unknown"]
    assert evals.cases[0]["expect"] == {"must_match": ["9"]}
    assert "expect" not in evals.cases[1]
    stack = load_stack(project / "stack.yaml")
    assert stack.name == "helper" and "helper" in stack.environment.agents
    assert stack.mcp == {}
    lock = Lock.load(project / "agent.lock")
    assert set(lock.entries) == {
        "runtime/adk@1.2.0",
        "prompt/helper-prompt@0.1.0",
        "eval/helper-evals@0.1.0",
    }
    assert extra["combination"] == {"parts": ["model", "prompt", "evals"], "verified": True}
    assert extra["keys"] == ["GEMINI_API_KEY"] and extra["grants"] == []
    assert not [p for p in project.iterdir() if p.name.startswith(".agentbreeder")]


def test_the_preview_is_the_written_text(tmp_path, tool_root):
    data = spec(
        tools=["tool/echo@0.1.0"],
        mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me", "list_issues"]}],
    )
    path = write_spec(tmp_path, data)
    plan, extra = agentspec.plan("helper", path, tmp_path / "helper", today=TODAY)
    preview = agentspec.summary(plan, extra, dry_run=True)
    assert not (tmp_path / "helper").exists()
    newagent.create(plan)
    for entry in preview["files"]:
        assert (tmp_path / "helper" / entry["path"]).read_text() == entry["content"]
    assert {entry["path"] for entry in preview["files"]} >= {"agent.yaml", "stack.yaml"}


def test_cli_dry_run_json(tmp_path, capsys):
    path = write_spec(tmp_path, spec())
    code = cli.main(
        ["new", "agent", "helper", "--from-spec", str(path), "--dir", str(tmp_path / "helper")]
        + ["--dry-run", "--json"]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["dry_run"] is True and out["spec_version"] == 1
    assert out["cases"] == ["hours", "unknown"]
    assert out["combination"]["verified"] is True
    assert all("content" in entry for entry in out["files"])
    assert not (tmp_path / "helper").exists()


def test_mcp_servers_are_added_and_granted(tmp_path):
    data = spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me", "list_issues"]}])
    plan, extra = make(tmp_path, data)
    project = tmp_path / "helper"
    stack = load_stack(project / "stack.yaml")
    assert stack.mcp["github"].ref == "mcp/github@1.0.0"
    assert stack.environment.agents["helper"].mcp == {"github": ["get_me", "list_issues"]}
    agent = load_agent_file(project / "agent.yaml")
    assert [(m.ref, m.tools) for m in agent.mcp] == [
        ("mcp/github@1.0.0", ["get_me", "list_issues"])
    ]
    assert extra["grants"] == [{"server": "github", "tools": ["get_me", "list_issues"]}]
    assert extra["keys"] == ["GEMINI_API_KEY", "GITHUB_MCP_TOKEN"]
    assert "GITHUB_MCP_TOKEN=..." in (project / "stack.yaml").read_text()
    assert extra["combination"] == {
        "parts": ["model", "prompt", "evals", "mcp"],
        "verified": False,
    }
    assert extra["refs"] == [
        {
            "ref": "mcp/github@1.0.0",
            "kind": "mcp",
            "root_kind": "builtin",
            "runs_code": True,
            "sha256": extra["refs"][0]["sha256"],
        }
    ]
    assert registry.DIGEST.fullmatch(extra["refs"][0]["sha256"])
    assert extra["runs_code"] is False


def test_a_picked_tool_resolves_from_the_registry_path(tmp_path, tool_root, identity_env):
    plan, extra = make(tmp_path, spec(tools=["tool/echo@0.1.0"]))
    project = tmp_path / "helper"
    assert [t.ref for t in load_agent_file(project / "agent.yaml").tools] == ["tool/echo@0.1.0"]
    assert not (project / "registry" / "tool").exists()  # not copied
    lock = Lock.load(project / "agent.lock")
    assert lock.entries["tool/echo@0.1.0"]["source"] == os.path.relpath(tool_root, project)
    assert lock.entries["prompt/helper-prompt@0.1.0"]["source"] == "registry"
    assert stat.S_IMODE((project / "agent.lock").stat().st_mode) == 0o644
    assert extra["runs_code"] is True
    assert extra["refs"][0]["root_kind"] == "path"
    assert extra["combination"]["verified"] is False

    identity = {"provider": "static", "ref": "agent-1", "environment": "dev"}
    identity_env.config["gateways"] = {
        "litellm": {
            "type": "litellm",
            "url": "http://127.0.0.1:4000",
            "agent_url": "http://host.docker.internal:4000",
        }
    }
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm",
            "models": ["chat", "judge"],
            "ceilings": {"budget_usd": 5, "rpm": 60},
            "bindings": {"DATABASE_URL": "secret://helper-db"},
            "agents": {"helper": {"identity": identity, "candidate_identity": dict(identity)}},
        }
    )
    before = (project / "agent.lock").read_bytes()

    def builder(context, iidfile):
        assert (context / "artifacts" / "tool" / "echo").is_dir()
        return IMAGE

    result = build(
        project / "agent.yaml", identity_env.load(), "dev", mode="locked", builder=builder
    )
    assert result.image == IMAGE
    assert (project / "agent.lock").read_bytes() == before


def test_a_failed_lock_after_the_move_removes_everything(tmp_path, tool_root, monkeypatch):
    def broken(*args, **kwargs):
        raise registry.RegistryError("ENTRY_UNSAFE", "boom")

    monkeypatch.setattr(newagent, "_lock", broken)
    path = write_spec(tmp_path, spec(tools=["tool/echo@0.1.0"]))
    plan, _ = agentspec.plan("helper", path, tmp_path / "helper", today=TODAY)
    with pytest.raises(newagent.NewAgentError) as caught:
        newagent.create(plan)
    assert caught.value.code == "ENTRY_UNSAFE"
    assert not (tmp_path / "helper").exists()


def test_threshold_retries_and_calls_reach_agent_yaml(tmp_path):
    data = spec(max_model_calls=4)
    data["evals"].update({"threshold": 0.5, "retries": 0})
    make(tmp_path, data)
    agent = load_agent_file(tmp_path / "helper" / "agent.yaml")
    assert (agent.evals.threshold, agent.evals.retries) == (0.5, 0)
    assert agent.model.max_model_calls == 4


def test_unicode_text_is_kept(tmp_path):
    data = spec(prompt={"text": "Réponds en français.\n\tMerci."})
    data["evals"]["cases"][0]["reference"] = "9 h à 17 h"
    make(tmp_path, data)
    project = tmp_path / "helper"
    assert (project / "src/prompt/prompt.md").read_text() == "Réponds en français.\n\tMerci.\n"
    assert load_eval_set(project / "src/evals").cases[0]["reference"] == "9 h à 17 h"


def test_zero_width_joiners_are_kept(tmp_path):
    text = "\U0001f468\u200d\U0001f469 \u0645\u200c\u06cc"
    make(tmp_path, spec(prompt={"text": text}))
    assert (tmp_path / "helper/src/prompt/prompt.md").read_text() == text + "\n"


def test_a_yaml_looking_prompt_stays_text(tmp_path):
    text = "kind: agent\nname: other\n---\n{ref: tool/x@1.0.0}"
    make(tmp_path, spec(prompt={"text": text}))
    project = tmp_path / "helper"
    assert (project / "src/prompt/prompt.md").read_text() == text + "\n"
    assert load_agent_file(project / "agent.yaml").name == "helper"


# -- refusals --------------------------------------------------------------------------------


def test_knowledge_is_refused(tmp_path):
    error = refused(tmp_path, spec(knowledge=[{"ref": "knowledge/x@1.0.0"}]))
    assert error.code == "SPEC_UNSUPPORTED"


def test_the_name_must_match(tmp_path):
    assert refused(tmp_path, spec(name="other")).code == "NAME_MISMATCH"


@pytest.mark.parametrize("name", ["Helper", "true", "a" * 32, "x_y"])
def test_names_get_the_starter_checks(tmp_path, name):
    assert refused(tmp_path, spec(name=name), name=name).code == "NAME_INVALID"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"model": "llama"}, "MODEL_UNSUPPORTED"),
        ({"model": "openrouter"}, "MODEL_ID_REQUIRED"),
        ({"model_id": "openai/gpt"}, "MODEL_ID_INVALID"),
        ({"schema_version": 2}, "SPEC_VERSION_UNSUPPORTED"),
        ({"extra": 1}, "SPEC_INVALID"),
        ({"prompt": {"text": "   "}}, "SPEC_INVALID"),
        ({"prompt": {"text": "a\x1b[31mb"}}, "SPEC_INVALID"),
        ({"prompt": {"text": "a‮b"}}, "SPEC_INVALID"),
        ({"prompt": {"text": "a\u00adb"}}, "SPEC_INVALID"),
        ({"prompt": {"text": "a\U000e0041b"}}, "SPEC_INVALID"),
        ({"prompt": {"text": "x" * (agentspec.MAX_PROMPT + 1)}}, "SPEC_INVALID"),
        ({"evals": {"cases": []}}, "SPEC_INVALID"),
        ({"evals": {"cases": [{"id": "a", "turns": ["x"]}]}}, "SPEC_INVALID"),
        ({"max_model_calls": 0}, "SPEC_INVALID"),
        ({"max_model_calls": "2"}, "SPEC_INVALID"),
        ({"tools": ["prompt/helper-prompt@0.1.0"]}, "SPEC_INVALID"),
        ({"tools": ["tool/echo"]}, "SPEC_INVALID"),
        ({"tools": ["tool/missing@1.0.0"]}, "REF_NOT_FOUND"),
        ({"mcp": [{"ref": "mcp/github@1.0.0", "tools": ["delete_repo"]}]}, "MCP_TOOL_UNDECLARED"),
        ({"mcp": [{"ref": "mcp/github@1.0.0", "tools": ["get me"]}]}, "SPEC_INVALID"),
        ({"mcp": [{"ref": "mcp/github@1.0.0", "tools": ["a", "a"]}]}, "SPEC_INVALID"),
        ({"mcp": [{"ref": "mcp/github@1.0.0", "tools": []}]}, "SPEC_INVALID"),
        ({"mcp": [{"ref": "tool/github@1.0.0", "tools": ["get_me"]}]}, "SPEC_INVALID"),
        (
            {
                "mcp": [
                    {"ref": "mcp/github@1.0.0", "tools": ["get_me"]},
                    {"ref": "mcp/github@1.0.0", "tools": ["list_issues"]},
                ]
            },
            "SPEC_INVALID",
        ),
    ],
)
def test_bad_specs_are_refused(tmp_path, changes, code):
    error = refused(tmp_path, spec(**changes))
    assert error.code == code, error


def test_ref_codes_match_the_registry(tmp_path):
    # Whatever code the registry gives for a missing ref, the builder passes it on.
    with pytest.raises(registry.RegistryError) as caught:
        registry.resolve(registry.Ref.parse("tool/missing@1.0.0"), registry.roots(tmp_path))
    assert refused(tmp_path, spec(tools=["tool/missing@1.0.0"])).code == caught.value.code


@pytest.mark.parametrize(
    "case",
    [
        {"id": "-a", "turns": ["x"], "reference": "r"},
        {"id": "a", "turns": [], "reference": "r"},
        {"id": "a", "turns": ["x"] * 11, "reference": "r"},
        {"id": "a", "turns": [1], "reference": "r"},
        {"id": "a", "turns": ["x"], "reference": "r", "expect": {"must_match": ["("]}},
        {"id": "a", "turns": ["x"], "reference": "r", "expect": {"tools_include": ["a b"]}},
        {"id": "a", "turns": ["x"], "reference": "r", "expect": {"other": []}},
        {"id": "a", "turns": ["x\x00"], "reference": "r"},
    ],
)
def test_bad_cases_are_refused(tmp_path, case):
    assert refused(tmp_path, spec(evals={"cases": [case]})).code == "SPEC_INVALID"


def test_duplicate_case_ids_are_refused(tmp_path):
    case = {"id": "a", "turns": ["x"], "reference": "r"}
    assert refused(tmp_path, spec(evals={"cases": [case, case]})).code == "SPEC_INVALID"


def test_duplicate_tool_versions_are_refused(tmp_path, tool_root):
    sources = tmp_path / "v2"
    registry.scaffold("tool", "echo", sources, version="0.2.0")
    registry.publish(next(sources.iterdir()), tool_root)
    data = spec(tools=["tool/echo@0.1.0", "tool/echo@0.2.0"])
    assert refused(tmp_path, data).code == "SPEC_INVALID"


@pytest.mark.parametrize(
    "text",
    [
        '{"schema_version": 1, "schema_version": 1}',
        '{"a": NaN}',
        '{"a": Infinity}',
        "[1]",
        "not json",
        b"\xff\xfe".decode("latin-1"),
    ],
)
def test_bad_json_is_refused(tmp_path, text):
    assert refused(tmp_path, text).code == "SPEC_INVALID"


def test_a_large_spec_is_refused(tmp_path):
    text = json.dumps(spec()) + " " * agentspec.MAX_SPEC_BYTES
    assert refused(tmp_path, text).code == "SPEC_TOO_LARGE"


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o620, 0o602])
def test_a_spec_others_can_read_or_write_is_refused(tmp_path, mode):
    assert refused(tmp_path, spec(), mode=mode).code == "SPEC_UNSAFE"


def test_a_symlinked_spec_is_refused(tmp_path):
    real = write_spec(tmp_path, spec())
    link = tmp_path / "link.json"
    link.symlink_to(real)
    assert refused(tmp_path, link).code == "SPEC_UNSAFE"


def test_a_fifo_spec_is_refused(tmp_path):
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo, 0o600)
    assert refused(tmp_path, fifo).code == "SPEC_UNSAFE"


def test_another_users_spec_is_refused(tmp_path, monkeypatch):
    path = write_spec(tmp_path, spec())
    other = os.geteuid() + 1
    monkeypatch.setattr(agentspec.os, "geteuid", lambda: other)
    assert refused(tmp_path, path).code == "SPEC_UNSAFE"


def test_a_missing_spec_is_refused(tmp_path):
    assert refused(tmp_path, tmp_path / "nope.json").code == "SPEC_MISSING"


def test_a_non_empty_destination_is_refused_on_dry_run(tmp_path, capsys):
    (tmp_path / "helper").mkdir()
    (tmp_path / "helper" / "x").write_text("x")
    path = write_spec(tmp_path, spec())
    code = cli.main(
        ["new", "agent", "helper", "--from-spec", str(path), "--dir", str(tmp_path / "helper")]
        + ["--dry-run", "--json"]
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "DESTINATION_NOT_EMPTY"


@pytest.mark.parametrize("extra", [["--model", "gemini"], ["--model-id", "gemini/x"]])
def test_model_flags_clash_with_from_spec(tmp_path, capsys, extra):
    path = write_spec(tmp_path, spec())
    code = cli.main(["new", "agent", "helper", "--from-spec", str(path), "--json", *extra])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "USAGE"


def test_stack_for_clashes_with_from_spec(tmp_path, capsys):
    path = write_spec(tmp_path, spec())
    code = cli.main(
        ["new", "agent", "helper", "--from-spec", str(path), "--stack-for", str(path), "--json"]
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "USAGE"


def test_model_is_required_without_from_spec(tmp_path, capsys):
    code = cli.main(["new", "agent", "helper", "--dir", str(tmp_path / "h"), "--json"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "USAGE"
    assert not (tmp_path / "h").exists()


def test_errors_never_echo_the_prompt(tmp_path, capsys):
    path = write_spec(tmp_path, spec(prompt={"text": SECRET_PROMPT + "\x07"}))
    code = cli.main(["new", "agent", "helper", "--from-spec", str(path), "--json"])
    captured = capsys.readouterr()
    assert code == 2 and SECRET_PROMPT not in captured.out + captured.err


# -- text output and the subprocess path ------------------------------------------------------


def test_text_output(tmp_path, capsys):
    data = spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}])
    path = write_spec(tmp_path, data)
    target = str(tmp_path / "helper")
    assert cli.main(["new", "agent", "helper", "--from-spec", str(path), "--dir", target]) == 0
    out = capsys.readouterr().out
    assert "MCP grants in stack.yaml: github: get_me." in out
    assert "(model + prompt + evals + mcp) has not been run end to end" in out
    assert "GEMINI_API_KEY=<your key> and GITHUB_MCP_TOKEN=<your key>" in out
    assert out.rstrip().splitlines()[-1].startswith("Next:")


def test_text_output_names_code_from_other_roots(tmp_path, tool_root, capsys):
    path = write_spec(tmp_path, spec(tools=["tool/echo@0.1.0"]))
    argv = ["new", "agent", "helper", "--from-spec", str(path), "--dry-run"]
    assert cli.main([*argv, "--dir", str(tmp_path / "helper")]) == 0
    out = capsys.readouterr().out
    assert "not shipped with AgentBreeder: tool/echo@0.1.0 (path)" in out
    assert "Dry run: nothing was written." in out


def test_end_to_end_subprocess(tmp_path):
    path = write_spec(tmp_path, spec())
    env = {**os.environ, "PYTHONPATH": SRC}
    env.pop(registry.PATH_ENV, None)
    result = subprocess.run(
        [sys.executable, "-m", "agentbreeder", "new", "agent", "helper"]
        + ["--from-spec", str(path), "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["dry_run"] is False and out["published"][0]["sha256"].startswith("sha256:")
    assert yaml.safe_load((tmp_path / "helper" / "agent.yaml").read_text())["name"] == "helper"


# -- defensive paths ---------------------------------------------------------------------------


def _mcp_root(tmp_path: Path, monkeypatch, name: str) -> None:
    root = tmp_path / "mcp-registry"
    sources = tmp_path / "mcp-sources"
    registry.scaffold("mcp", name, sources)
    registry.publish(next(sources.iterdir()), root)
    monkeypatch.setenv(registry.PATH_ENV, str(root))


def test_an_mcp_server_named_like_a_stack_service_is_refused(tmp_path, monkeypatch):
    _mcp_root(tmp_path, monkeypatch, "postgres")
    from agentbreeder import mcp as mcp_kind

    tool = next(
        iter(mcp_kind.load_mcp(next((tmp_path / "mcp-registry").rglob("mcp.yaml")).parent).tools)
    )
    data = spec(mcp=[{"ref": "mcp/postgres@0.1.0", "tools": [tool]}])
    assert refused(tmp_path, data).code == "SPEC_UNSUPPORTED"


def test_an_invalid_mcp_version_is_refused(tmp_path, monkeypatch):
    from agentbreeder import mcp as mcp_kind
    from agentbreeder.services.schema import ServiceError

    def broken(path):
        raise ServiceError("MCP_INVALID", "mcp.yaml: broken")

    monkeypatch.setattr(mcp_kind, "load_mcp", broken)
    data = spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}])
    assert refused(tmp_path, data).code == "MCP_INVALID"


def test_a_refused_grant_is_passed_on(tmp_path, monkeypatch):
    from agentbreeder import mcpgrant

    def broken(*args, **kwargs):
        raise mcpgrant.GrantError("STACK_EDIT_AMBIGUOUS", "no")

    monkeypatch.setattr(mcpgrant, "change", broken)
    data = spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}])
    assert refused(tmp_path, data).code == "STACK_EDIT_AMBIGUOUS"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("kind: agent\n  bad: [\n", "TEMPLATE_INVALID"),
        ("kind: agent\nschema_version: 1\n", "SPEC_INVALID"),
    ],
)
def test_a_template_that_does_not_load_is_refused(tmp_path, monkeypatch, text, code):
    monkeypatch.setattr(agentspec, "_agent_yaml", lambda spec, today: text)
    assert refused(tmp_path, spec()).code == code


def test_a_template_that_differs_from_the_spec_is_refused(tmp_path, monkeypatch):
    real = agentspec._agent_yaml
    monkeypatch.setattr(
        agentspec,
        "_agent_yaml",
        lambda spec, today: real(spec, today).replace("retries: 1", "retries: 2"),
    )
    assert refused(tmp_path, spec()).code == "TEMPLATE_INVALID"


# -- review round 1 ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        "(a+)+$",
        "(a*)*",
        "(a|a)+",
        "(ab|cd)*e",
        "x|(a+)+",
        "(?>a+)+",
        "(?!(a+)+)",
        "(?:x+y?)+",
        r"(a)\1",
        "(?=(a+)+)",
        "(a{2,})+",
        # more than two variable repeats (round 2)
        "a?" * 16 + "a" * 16,
        "(a?){18}a{18}",
        "a*a*a*b",
        ".*.*.*x",
        r"\s*\s*\s*x",
        "(?>a+)b?c?",
        "(?=a?)b?c?",
        "(?!a?)b?c?",
        "x|a?b?c?",
        "(?:a|aa)" * 25 + "c",
        "(?:a|ab){25}c",
        "(xx|y)(ab|cd)(ef|gh)",
    ],
)
def test_patterns_that_backtrack_exponentially_are_refused(tmp_path, pattern):
    case = {"id": "a", "turns": ["x"], "reference": "r", "expect": {"must_match": [pattern]}}
    assert refused(tmp_path, spec(evals={"cases": [case]})).code == "SPEC_INVALID"


@pytest.mark.parametrize(
    "pattern", ["(a|b)*c", "9", "a+b", "(foo)?", "x{2}", "(9|nine)", "(ab)?c+", "\\d+"]
)
def test_ordinary_patterns_are_allowed(tmp_path, pattern):
    case = {"id": "a", "turns": ["x"], "reference": "r", "expect": {"must_not_match": [pattern]}}
    make(tmp_path, spec(evals={"cases": [case]}))


@pytest.mark.parametrize("knowledge", [["x"], [{"ref": "a"}, {"ref": "b"}], [1]])
def test_any_knowledge_is_unsupported(tmp_path, knowledge):
    assert refused(tmp_path, spec(knowledge=knowledge)).code == "SPEC_UNSUPPORTED"


def test_a_long_unknown_key_is_cut_short(tmp_path):
    error = refused(tmp_path, spec(**{"k" * 5000: 1}))
    assert error.code == "SPEC_INVALID" and len(str(error)) < 300


def test_refs_carry_their_version_digest(tmp_path, tool_root):
    path = write_spec(tmp_path, spec(tools=["tool/echo@0.1.0"]))
    _, extra = agentspec.plan("helper", path, tmp_path / "helper", today=TODAY)
    digest = registry.copy_and_hash(tool_root / "tool" / "echo" / "0.1.0")
    assert extra["refs"][0]["sha256"] == digest


def test_the_write_is_bound_to_the_preview(tmp_path, capsys):
    path = write_spec(tmp_path, spec())
    target = str(tmp_path / "helper")
    argv = ["new", "agent", "helper", "--from-spec", str(path), "--dir", target, "--json"]
    assert cli.main([*argv, "--dry-run"]) == 0
    digest = json.loads(capsys.readouterr().out)["plan_sha256"]
    write_spec(tmp_path, spec(prompt={"text": "Something else."}))
    assert cli.main([*argv, "--expect-digest", digest]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "DIGEST_MISMATCH"
    assert not (tmp_path / "helper").exists()
    write_spec(tmp_path, spec())
    assert cli.main([*argv, "--expect-digest", digest]) == 0
    assert json.loads(capsys.readouterr().out)["plan_sha256"] == digest


def test_a_malformed_digest_is_refused(tmp_path):
    path = write_spec(tmp_path, spec())
    with pytest.raises(newagent.NewAgentError) as caught:
        agentspec.plan("helper", path, tmp_path / "helper", expect_digest="x")
    assert caught.value.code == "DIGEST_INVALID"


def test_a_stack_that_does_not_load_is_refused_in_the_preview(tmp_path, monkeypatch):
    real = agentspec.stackfor.render

    def broken(*args, **kwargs):
        files = real(*args, **kwargs)
        files["stack.yaml"] = files["stack.yaml"].replace("gateway: litellm", "gateway: nope")
        return files

    monkeypatch.setattr(agentspec.stackfor, "render", broken)
    assert refused(tmp_path, spec()).code == "TEMPLATE_INVALID"


def test_a_stack_naming_another_agent_is_refused_in_the_preview(tmp_path, monkeypatch):
    real = agentspec.stackfor.render

    def other(*args, **kwargs):
        files = real(*args, **kwargs)
        files["stack.yaml"] = files["stack.yaml"].replace("    helper:\n", "    other:\n")
        return files

    monkeypatch.setattr(agentspec.stackfor, "render", other)
    assert refused(tmp_path, spec()).code == "TEMPLATE_INVALID"


def test_an_unreadable_version_is_refused(tmp_path, monkeypatch):
    def broken(path):
        raise registry.RegistryError("ENTRY_UNSAFE", "no")

    monkeypatch.setattr(registry, "copy_and_hash", broken)
    data = spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}])
    assert refused(tmp_path, data).code == "ENTRY_UNSAFE"


def test_bools_are_not_numbers(tmp_path):
    assert refused(tmp_path, spec(max_model_calls=True)).code == "SPEC_INVALID"


def test_another_provider_through_the_spec(tmp_path):
    data = spec(model="openrouter", model_id="openrouter/google/gemini-x")
    plan, extra = make(tmp_path, data)
    assert extra["keys"] == ["OPENROUTER_API_KEY"]
    assert extra["combination"]["verified"] is False


@pytest.mark.parametrize("picked", ["tool", "mcp"])
def test_a_version_replaced_before_the_lock_is_refused(tmp_path, tool_root, monkeypatch, picked):
    if picked == "tool":
        data = spec(tools=["tool/echo@0.1.0"])
    else:
        data = spec(mcp=[{"ref": "mcp/github@1.0.0", "tools": ["get_me"]}])
    path = write_spec(tmp_path, data)
    plan, _ = agentspec.plan("helper", path, tmp_path / "helper", today=TODAY)
    ref = next(iter(plan.pinned))
    plan = replace(plan, pinned={ref: "sha256:" + "0" * 64})
    with pytest.raises(newagent.NewAgentError) as caught:
        newagent.create(plan)
    assert caught.value.code == "DIGEST_MISMATCH"
    assert not (tmp_path / "helper").exists()
