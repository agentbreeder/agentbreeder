"""`agentbreeder new agent NAME --stack-for agent.yaml` (#823): a stack for an agent file alone."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from agentbreeder import cli, newagent, registry, stackfor
from agentbreeder.center import files as center_files

TODAY = date(2026, 11, 13)
SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture(autouse=True)
def clean_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


def starter(tmp_path: Path, name: str = "hello") -> tuple[Path, dict]:
    """A starter project with its stack removed; returns it and the starter's stack."""
    newagent.create(newagent.plan(name, "gemini", tmp_path / name, today=TODAY))
    project = tmp_path / name
    original = yaml.safe_load((project / "stack.yaml").read_text())
    (project / "stack.yaml").unlink()
    (project / "stack/litellm/config.yaml").unlink()
    (project / "stack/litellm").rmdir()
    (project / "stack").rmdir()
    return project, original


def plan(project: Path, name: str = "hello") -> stackfor.StackForPlan:
    return stackfor.plan(name, "gemini", project / "agent.yaml", today=TODAY)


def snapshot(root: Path) -> dict:
    return {
        str(path.relative_to(root)): (
            path.lstat().st_mode,
            path.read_bytes() if path.is_file() else b"",
        )
        for path in sorted(root.rglob("*"))
    }


def edit_agent(project: Path, **changes) -> None:
    data = yaml.safe_load((project / "agent.yaml").read_text())
    data.update(changes)
    (project / "agent.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


# -- the written stack ------------------------------------------------------------------------


def test_the_starter_gets_back_the_starter_stack(tmp_path):
    project, original = starter(tmp_path)
    before = snapshot(project)
    result = stackfor.create(plan(project))
    written = yaml.safe_load((project / "stack.yaml").read_text())
    assert written == original  # the stack verified end to end with the starter
    config = yaml.safe_load((project / "stack/litellm/config.yaml").read_text())
    assert [entry["model_name"] for entry in config["model_list"]] == ["chat", "judge"]
    assert config["model_list"][0]["litellm_params"] == {
        "model": "gemini/gemini-3.8-flash",
        "api_key": "os.environ/GEMINI_API_KEY",
    }
    after = snapshot(project)
    added = set(after) - set(before)
    assert added == {"stack", "stack/litellm", "stack.yaml", "stack/litellm/config.yaml"}
    assert all(after[key] == value for key, value in before.items())  # nothing else touched
    for relative in added:
        mode = stat.S_IMODE((project / relative).lstat().st_mode)
        assert mode == (0o755 if (project / relative).is_dir() else 0o644), relative
    assert not [p for p in project.iterdir() if p.name.startswith(".agentbreeder-stack-")]
    assert result.aliases == ("chat", "judge") and result.warnings == ()


def test_validates_through_the_cli(tmp_path, capsys):
    project, _ = starter(tmp_path)
    stackfor.create(plan(project))
    assert cli.main(["validate", str(project / "stack.yaml")]) == 0


def test_aliases_follow_the_agent_and_its_judge(tmp_path):
    project, _ = starter(tmp_path)
    edit_agent(project, model={"alias": "support-model", "reasoning": "low"})
    made = plan(project)
    assert made.aliases == ("support-model", "judge")
    stack = yaml.safe_load(made.files["stack.yaml"])
    assert stack["environment"]["models"] == ["support-model", "judge"]


def test_one_alias_when_the_judge_is_the_model(tmp_path):
    project, _ = starter(tmp_path)
    edit_agent(project, model={"alias": "judge"})
    assert plan(project).aliases == ("judge",)


def test_no_evals_means_only_the_model(tmp_path):
    project, _ = starter(tmp_path)
    data = yaml.safe_load((project / "agent.yaml").read_text())
    del data["evals"]
    (project / "agent.yaml").write_text(yaml.safe_dump(data))
    assert plan(project).aliases == ("chat",)


def test_traces_otlp_warns(tmp_path, capsys):
    project, _ = starter(tmp_path)
    edit_agent(project, traces="otlp")
    assert plan(project).warnings and "no trace collector" in plan(project).warnings[0]
    assert (
        cli.main(
            [
                "new",
                "agent",
                "hello",
                "--model",
                "gemini",
                "--stack-for",
                str(project / "agent.yaml"),
            ]
        )
        == 0
    )
    assert "Warning: traces: otlp" in capsys.readouterr().out


def test_the_identity_seam_is_the_agent_entry():
    entry = yaml.safe_load("agents:\n" + stackfor._agent_entry("hello", "2027-01-01"))
    assert entry == {
        "agents": {
            "hello": {"sponsor": "you@example.com", "review_by": "2027-01-01", "candidate": True}
        }
    }


# -- refusals ---------------------------------------------------------------------------------


def refused(project: Path, name: str = "hello") -> str:
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor.create(plan(project, name))
    return error.value.code


def test_name_must_match_the_agent(tmp_path):
    project, _ = starter(tmp_path)
    assert refused(project, "other") == "NAME_MISMATCH"
    assert not (project / "stack.yaml").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("knowledge", [{"ref": "knowledge/x@1.0.0"}]),
        ("tools", [{"ref": "tool/x@1.0.0"}]),
        ("mcp", [{"ref": "mcp/x@1.0.0", "tools": ["t"]}]),
    ],
)
def test_agents_needing_more_services_are_refused(tmp_path, field, value):
    project, _ = starter(tmp_path)
    edit_agent(project, **{field: value})
    assert refused(project) == "STACK_FOR_UNSUPPORTED"
    assert not (project / "stack.yaml").exists()


def test_an_alias_that_is_not_a_plain_name_is_refused(tmp_path):
    project, _ = starter(tmp_path)
    edit_agent(project, model={"alias": "vendor/model"})
    assert refused(project) == "ALIAS_UNSUPPORTED"


def test_an_unresolvable_eval_set_is_refused(tmp_path):
    project, _ = starter(tmp_path)
    edit_agent(project, evals={"ref": "eval/missing@1.0.0"})
    assert refused(project) == "REF_NOT_FOUND"


@pytest.mark.parametrize("existing", ["stack.yaml", "stack/litellm/config.yaml"])
def test_existing_files_are_never_replaced(tmp_path, existing):
    project, _ = starter(tmp_path)
    (project / existing).parent.mkdir(parents=True, exist_ok=True)
    (project / existing).write_text("mine\n")
    assert refused(project) == "STACK_EXISTS"
    assert (project / existing).read_text() == "mine\n"


def test_a_file_appearing_while_writing_is_kept_and_the_rest_removed(tmp_path, monkeypatch):
    project, _ = starter(tmp_path)
    made = plan(project)
    real_link = os.link

    def racing(source, target, **kwargs):
        if str(target).endswith("config.yaml"):
            Path(target).write_text("theirs\n")
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(stackfor.os, "link", racing)
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor.create(made)
    assert error.value.code == "STACK_EXISTS"
    assert (project / "stack/litellm/config.yaml").read_text() == "theirs\n"
    assert not (project / "stack.yaml").exists()


def test_a_failure_removes_what_it_created(tmp_path, monkeypatch):
    project, _ = starter(tmp_path)
    before = snapshot(project)
    made = plan(project)
    real_link = os.link
    calls = []

    def failing(source, target, **kwargs):
        calls.append(target)
        if len(calls) == 2:
            raise PermissionError(13, "Permission denied")
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(stackfor.os, "link", failing)
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor.create(made)
    assert error.value.code == "WRITE_FAILED"
    assert snapshot(project) == before


def test_a_template_that_does_not_validate_is_refused(tmp_path, monkeypatch):
    project, _ = starter(tmp_path)
    made = plan(project)
    broken = stackfor.StackForPlan(
        made.name,
        made.model,
        made.agent_file,
        made.directory,
        made.review_by,
        made.aliases,
        {**made.files, "stack.yaml": "kind: stack\n"},
    )
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor.create(broken)
    assert error.value.code == "TEMPLATE_INVALID"
    assert not (project / "stack.yaml").exists()


# -- S-01: the registration ownership check ---------------------------------------------------


def test_a_directory_writable_by_others_is_refused(tmp_path):
    project, _ = starter(tmp_path)
    project.chmod(0o777)
    assert refused(project) == "PROJECT_UNSAFE"
    project.chmod(0o755)


def test_an_agent_file_writable_by_others_is_refused(tmp_path):
    project, _ = starter(tmp_path)
    (project / "agent.yaml").chmod(0o666)
    assert refused(project) == "PROJECT_UNSAFE"


def test_a_symlinked_agent_file_is_refused(tmp_path):
    project, _ = starter(tmp_path)
    (project / "agent.yaml").rename(tmp_path / "real.yaml")
    (project / "agent.yaml").symlink_to(tmp_path / "real.yaml")
    assert refused(project) == "PROJECT_UNSAFE"


def test_another_users_files_are_refused(tmp_path, monkeypatch):
    project, _ = starter(tmp_path)
    real = os.getuid()
    monkeypatch.setattr(stackfor.os, "getuid", lambda: real + 1)
    assert refused(project) == "PROJECT_UNSAFE"


def test_a_non_sticky_writable_ancestor_is_refused(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    project, _ = starter(shared)
    shared.chmod(0o777)
    try:
        assert refused(project) == "PROJECT_UNSAFE"
        shared.chmod(0o777 | stat.S_ISVTX)  # sticky, like /tmp: allowed
        stackfor.create(plan(project))
    finally:
        shared.chmod(0o755)


def test_the_check_is_the_registration_check(tmp_path, monkeypatch):
    project, _ = starter(tmp_path)
    seen = []
    monkeypatch.setattr(center_files, "check_ancestors", lambda path: seen.append(path))
    stackfor.create(plan(project))
    assert seen and seen[0] == project.resolve().parent


def test_a_missing_agent_file_is_refused(tmp_path):
    project, _ = starter(tmp_path)
    (project / "agent.yaml").unlink()
    assert refused(project) == "AGENT_FILE_MISSING"
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor.plan("hello", "gemini", tmp_path / "nowhere" / "agent.yaml")
    assert error.value.code == "AGENT_FILE_MISSING"


def test_only_a_path_to_agent_yaml_is_taken(tmp_path):
    project, _ = starter(tmp_path)
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor.plan("hello", "gemini", project / "other.yaml")
    assert error.value.code == "AGENT_FILE_NAME"


def test_control_characters_in_the_path_are_refused_unechoed(tmp_path, capsys):
    bad = tmp_path / "x\x1b]52;c;y\x07" / "agent.yaml"
    code = cli.main(["new", "agent", "hello", "--model", "gemini", "--stack-for", str(bad)])
    assert code == 2
    err = capsys.readouterr().err
    assert "DESTINATION_UNSAFE" in err and "\x1b" not in err


# -- CLI --------------------------------------------------------------------------------------


def test_cli_dry_run_json_writes_nothing(tmp_path, capsys):
    project, _ = starter(tmp_path)
    before = snapshot(project)
    argv = [
        "new",
        "agent",
        "hello",
        "--model",
        "gemini",
        "--stack-for",
        str(project / "agent.yaml"),
    ]
    assert cli.main([*argv, "--dry-run", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert snapshot(project) == before
    assert summary["dry_run"] is True and summary["aliases"] == ["chat", "judge"]
    assert [entry["path"] for entry in summary["files"]] == [
        "stack.yaml",
        "stack/litellm/config.yaml",
    ]
    assert summary["agent_file"] == str(project.resolve() / "agent.yaml")
    assert summary["key"] == "GEMINI_API_KEY" and summary["warnings"] == []


def test_cli_writes_and_points_at_next(tmp_path, capsys):
    project, _ = starter(tmp_path)
    argv = [
        "new",
        "agent",
        "hello",
        "--model",
        "gemini",
        "--stack-for",
        str(project / "agent.yaml"),
    ]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "Wrote a stack for the hello agent" in out and "Next:" in out
    assert "next" in out.splitlines()[-1]
    assert cli.main([*argv, "--json"]) == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["error"]["code"] == "STACK_EXISTS"


def test_dir_and_stack_for_are_exclusive(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["new", "agent", "a", "--model", "gemini", "--dir", "x", "--stack-for", "y"])
    assert "not allowed with argument" in capsys.readouterr().err


def test_cli_subprocess_end_to_end(tmp_path):
    project, original = starter(tmp_path)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentbreeder",
            "new",
            "agent",
            "hello",
            "--model",
            "gemini",
            "--stack-for",
            str(project / "agent.yaml"),
            "--json",
        ],
        env={**os.environ, "PYTHONPATH": SRC},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["dry_run"] is False
    written = yaml.safe_load((project / "stack.yaml").read_text())
    written["environment"]["agents"]["hello"]["review_by"] = original["environment"]["agents"][
        "hello"
    ]["review_by"]
    assert written == original


def test_controls_quoted_from_the_agent_file_are_escaped(tmp_path, capsys):
    project, _ = starter(tmp_path)
    with open(project / "agent.yaml", "a") as handle:
        handle.write('"x\\e]52;c;AAAA\\a": 1\n')
    argv = ["new", "agent", "hello", "--model", "gemini", "--stack-for"]
    assert cli.main([*argv, str(project / "agent.yaml")]) == 2
    err = capsys.readouterr().err
    assert "AGENT_INVALID" in err and "\x1b" not in err and "\x07" not in err


def test_ceilings_cover_the_agents_own_budget_and_rpm(tmp_path):
    project, _ = starter(tmp_path)
    edit_agent(project, model={"alias": "chat", "budget_usd": 20, "rpm": 500})
    stack = yaml.safe_load(plan(project).files["stack.yaml"])
    assert stack["environment"]["ceilings"] == {"budget_usd": 20, "rpm": 500}
    edit_agent(project, model={"alias": "chat", "budget_usd": 2.5, "rpm": 10})
    stack = yaml.safe_load(plan(project).files["stack.yaml"])
    assert stack["environment"]["ceilings"] == {"budget_usd": 5, "rpm": 60}
    edit_agent(project, model={"alias": "chat", "budget_usd": 7.25})
    made = plan(project)
    stackfor.create(made)
    assert yaml.safe_load((project / "stack.yaml").read_text())["environment"]["ceilings"] == {
        "budget_usd": 7.25,
        "rpm": 60,
    }


def test_ancestor_refusals_name_onboarding_not_the_console(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    project, _ = starter(shared)
    shared.chmod(0o777)
    try:
        with pytest.raises(newagent.NewAgentError) as error:
            stackfor.create(plan(project))
    finally:
        shared.chmod(0o755)
    assert "onboarded" in str(error.value) and "console" not in str(error.value)


def test_a_newline_quoted_from_the_agent_file_stays_on_one_line(tmp_path, capsys):
    project, _ = starter(tmp_path)
    with open(project / "agent.yaml", "a") as handle:
        handle.write('"x\\n[FAKE] forged": 1\n')
    argv = ["new", "agent", "hello", "--model", "gemini", "--stack-for"]
    assert cli.main([*argv, str(project / "agent.yaml")]) == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "\\n[FAKE]" in err


# -- what the project brings, and the reviewed agent file (console onboarding, #823) ---------

STACK_FOR = ["new", "agent", "hello", "--model", "gemini", "--stack-for"]


def test_refs_name_each_kind_and_where_it_resolved(tmp_path):
    project, _ = starter(tmp_path)
    summary = plan(project).summary(dry_run=True)
    agent = yaml.safe_load((project / "agent.yaml").read_text())
    assert summary["refs"] == [
        {"ref": agent["runtime"], "kind": "runtime", "root_kind": "builtin", "runs_code": True},
        {
            "ref": "prompt/hello-prompt@0.1.0",
            "kind": "prompt",
            "root_kind": "project",
            "runs_code": False,
        },
        {
            "ref": "eval/hello-evals@0.1.0",
            "kind": "eval",
            "root_kind": "project",
            "runs_code": True,
        },
    ]
    # The project's own eval set is code that does not ship with AgentBreeder.
    assert summary["runs_code"] is True
    raw = (project / "agent.yaml").read_bytes()
    assert summary["agent_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_only_built_in_code_does_not_count(tmp_path):
    project, _ = starter(tmp_path)
    data = yaml.safe_load((project / "agent.yaml").read_text())
    del data["evals"]
    (project / "agent.yaml").write_text(yaml.safe_dump(data))
    summary = plan(project).summary(dry_run=True)
    assert [entry["kind"] for entry in summary["refs"]] == ["runtime", "prompt"]
    assert summary["runs_code"] is False


def test_a_path_root_counts_as_code_from_elsewhere(tmp_path, monkeypatch):
    project, _ = starter(tmp_path)
    shared = tmp_path / "shared"
    (project / "registry").rename(shared)
    monkeypatch.setenv(registry.PATH_ENV, str(shared))
    summary = plan(project).summary(dry_run=True)
    assert {entry["kind"]: entry["root_kind"] for entry in summary["refs"]}["eval"] == "path"
    assert summary["runs_code"] is True


def test_an_unresolvable_ref_is_refused_with_the_registry_code(tmp_path):
    project, _ = starter(tmp_path)
    edit_agent(project, prompt={"ref": "prompt/missing@1.0.0"})
    assert refused(project) == "REF_NOT_FOUND"


def test_expect_digest_writes_the_previewed_file(tmp_path, capsys):
    project, _ = starter(tmp_path)
    argv = [*STACK_FOR, str(project / "agent.yaml")]
    assert cli.main([*argv, "--dry-run", "--json"]) == 0
    digest = json.loads(capsys.readouterr().out)["agent_sha256"]
    assert cli.main([*argv, "--expect-digest", digest, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["agent_sha256"] == digest
    assert (project / "stack.yaml").is_file()


def test_expect_digest_refuses_a_changed_agent_file(tmp_path, capsys):
    project, _ = starter(tmp_path)
    argv = [*STACK_FOR, str(project / "agent.yaml")]
    assert cli.main([*argv, "--dry-run", "--json"]) == 0
    digest = json.loads(capsys.readouterr().out)["agent_sha256"]
    edit_agent(project, description="changed after the preview")
    assert cli.main([*argv, "--expect-digest", digest, "--json"]) == 2
    out = capsys.readouterr()
    assert json.loads(out.out)["error"]["code"] == "DIGEST_MISMATCH"
    assert "[DIGEST_MISMATCH]" in out.err and "preview it again" in out.err
    assert not (project / "stack.yaml").exists()


@pytest.mark.parametrize("value", ["abc", "sha256:" + "A" * 64, "sha256:" + "a" * 63, ""])
def test_expect_digest_must_be_a_digest(tmp_path, capsys, value):
    project, _ = starter(tmp_path)
    assert cli.main([*STACK_FOR, str(project / "agent.yaml"), "--expect-digest", value]) == 2
    assert "[DIGEST_INVALID]" in capsys.readouterr().err
    assert not (project / "stack.yaml").exists()


def test_expect_digest_needs_stack_for(tmp_path, capsys):
    argv = ["new", "agent", "hello", "--model", "gemini", "--dir", str(tmp_path / "x")]
    assert cli.main([*argv, "--expect-digest", "sha256:" + "a" * 64, "--json"]) == 2
    out = capsys.readouterr()
    assert json.loads(out.out)["error"]["code"] == "USAGE"
    assert not (tmp_path / "x").exists()


def test_a_change_between_plan_and_write_is_refused(tmp_path):
    project, _ = starter(tmp_path)
    made = plan(project)
    edit_agent(project, description="changed after the plan")
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor.create(made)
    assert error.value.code == "AGENT_CHANGED"
    assert not (project / "stack.yaml").exists()


def test_a_change_while_reading_is_refused(tmp_path, monkeypatch):
    project, _ = starter(tmp_path)
    real = stackfor._digest
    calls = []

    def changing(path):
        calls.append(path)
        return real(path) if len(calls) == 1 else "sha256:" + "0" * 64

    monkeypatch.setattr(stackfor, "_digest", changing)
    with pytest.raises(newagent.NewAgentError) as error:
        plan(project)
    assert error.value.code == "AGENT_CHANGED"


def test_an_unreadable_agent_file_is_refused(tmp_path, monkeypatch):
    project, _ = starter(tmp_path)

    def unreadable(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("builtins.open", unreadable)
    with pytest.raises(newagent.NewAgentError) as error:
        stackfor._digest(project / "agent.yaml")
    assert error.value.code == "AGENT_FILE_MISSING"


def test_the_text_output_names_code_from_outside_the_built_ins(tmp_path, capsys):
    project, _ = starter(tmp_path)
    assert cli.main([*STACK_FOR, str(project / "agent.yaml"), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Code that runs in agents, not shipped with AgentBreeder: " in out
    assert "eval/hello-evals@0.1.0 (project)" in out and "runtime/" not in out.split("Code")[1]


# -- golden: `render` was extracted for --from-spec (#833); --stack-for output is unchanged ----

GOLDEN = {
    "plain": (
        "4b96e4fcb87fc4ad79714e999f34193bad3525ab59210f87a9b00adeb0c9cab5",
        "a22ee06d033cbbe3fbd85f5be8071c1283983844ff3c6136130bcfa4a13e4167",
    ),
    "budget": (
        "63b596a158c545ad565fad0bfe98afeeaad2ce7b4f14134e070db991d128f00f",
        "085f9794672faf902cbb976307ce2b64e4e7e1ff089491b9992bdc1f1a6aabec",
    ),
    "openrouter": (
        "c73ddfb0fb7793e40c454452c4cf77d33e0c8565142f1a8d3df3026956e98d84",
        "c226e920f65550c1667213cf9cf296b44170a744294b239b58df137ef2cc9c86",
    ),
}


@pytest.mark.parametrize("variant", sorted(GOLDEN))
def test_stack_for_output_is_byte_identical_to_before_render(tmp_path, variant):
    """Digests taken from the pre-#833 code on 2026-11-13, for three shapes."""
    project, _ = starter(tmp_path)
    model, model_id = "gemini", None
    if variant == "budget":
        edit_agent(project, model={"alias": "support-model", "budget_usd": 25.5, "rpm": 120})
    if variant == "openrouter":
        model, model_id = "openrouter", "openrouter/google/gemini-x"
    files = stackfor.plan("hello", model, project / "agent.yaml", today=TODAY, model_id=model_id)
    digests = tuple(
        hashlib.sha256(files.files[name].encode()).hexdigest()
        for name in (stackfor.STACK_FILE, stackfor.LITELLM_CONFIG)
    )
    assert digests == GOLDEN[variant]
