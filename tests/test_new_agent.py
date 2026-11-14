"""`agentbreeder new agent NAME --model gemini` (#769, M4): the starter project."""

from __future__ import annotations

import json
import os
import shlex
import stat
from datetime import date
from pathlib import Path

import pytest
import yaml

from agentbreeder import cli, guide_checks, newagent, registry
from agentbreeder.build import build
from agentbreeder.lock import Lock

TODAY = date(2026, 11, 12)
IMAGE = "sha256:" + "b" * 64


@pytest.fixture(autouse=True)
def clean_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


def snapshot(root: Path) -> dict:
    return {
        str(path.relative_to(root)): (
            path.lstat().st_mode,
            path.read_bytes() if path.is_file() else b"",
        )
        for path in sorted(root.rglob("*"))
    }


def make(tmp_path, name="hello", **kwargs) -> newagent.Plan:
    return newagent.create(newagent.plan(name, "gemini", tmp_path / name, today=TODAY, **kwargs))


# -- plan ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Hello",
        "1hello",
        "hello-",
        "-hello",
        "he_llo",
        "../x",
        "a/b",
        "",
        "h" * 32,
        "hé",
        "a b",
        "true",
        "false",
        "null",
        "no",
        "yes",
        "on",
        "off",
    ],  # fmt: skip
)
def test_names_outside_both_patterns_are_refused(name):
    with pytest.raises(newagent.NewAgentError) as error:
        newagent.plan(name, "gemini")
    assert error.value.code == "NAME_INVALID"
    with pytest.raises(newagent.NewAgentError) as fixed:
        newagent.plan("Hello", "gemini")
    assert str(error.value) == str(fixed.value)  # one fixed message: the name is never echoed


@pytest.mark.parametrize("name", ["a", "y", "n", "none", "nan", "hello", "my-agent-2", "h" * 31])
def test_valid_names_fit_the_agent_and_stack_patterns(name):
    import re

    from agentbreeder.agentfile import NAME_PATTERN

    plan = newagent.plan(name, "gemini", today=TODAY)
    assert re.fullmatch(NAME_PATTERN, name)
    assert yaml.safe_load(plan.files["stack.yaml"])["name"] == name


def test_an_unknown_provider_is_refused():
    with pytest.raises(newagent.NewAgentError) as error:
        newagent.plan("hello", "mistral")
    assert error.value.code == "MODEL_UNSUPPORTED"
    assert sorted(newagent.PROVIDERS) == ["anthropic", "gemini", "openai", "openrouter"]


def test_plan_fills_the_stated_fields():
    plan = newagent.plan("hello", "gemini", Path("x"), today=TODAY)
    agent = yaml.safe_load(plan.files["agent.yaml"])
    assert agent["runtime"] == "runtime/adk@1.2.0"
    assert agent["traces"] == "none" and agent["agent_events"] == "none"
    assert agent["prompt"] == {"ref": "prompt/hello-prompt@0.1.0"}
    assert agent["evals"] == {"ref": "eval/hello-evals@0.1.0", "threshold": 1.0, "retries": 1}
    assert "registry publish src/prompt" in plan.files["agent.yaml"]  # the edit loop
    stack = yaml.safe_load(plan.files["stack.yaml"])
    assert set(stack["services"]) == {"postgres", "litellm"}
    environment = stack["environment"]
    assert "judge" in environment["models"] and "chat" in environment["models"]
    assert environment["bindings"] == {"DATABASE_URL": {"ref": "postgres.databases.app"}}
    entry = environment["agents"]["hello"]
    assert entry["candidate"] is True
    assert entry["review_by"] == "2027-05-11" == plan.review_by  # today + 180 days
    evals = yaml.safe_load(plan.files["src/evals/manifest.yaml"])
    assert evals["judge"] == {"alias": "judge"}
    config = yaml.safe_load(plan.files["stack/litellm/config.yaml"])
    names = {model["model_name"] for model in config["model_list"]}
    assert names == {"chat", "judge"}
    for model in config["model_list"]:
        assert model["litellm_params"]["api_key"] == "os.environ/GEMINI_API_KEY"
    cases = [json.loads(line) for line in plan.files["src/evals/golden.jsonl"].splitlines()]
    assert [case["id"] for case in cases] == ["hours", "unknown"]
    assert plan.directory == Path(os.path.abspath("x"))


def test_default_directory_is_the_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert newagent.plan("hello", "gemini").directory == tmp_path.resolve() / "hello"


# -- create --------------------------------------------------------------------------------


def test_create_writes_publishes_and_locks(tmp_path):
    plan = make(tmp_path)
    project = tmp_path / "hello"
    for relative, content in plan.files.items():
        assert (project / relative).read_text() == content
    for path in [project, *project.rglob("*")]:
        mode = stat.S_IMODE(path.lstat().st_mode)
        assert not path.is_symlink()
        assert mode == (0o755 if path.is_dir() else 0o644), (path, oct(mode))
    assert not [p for p in project.iterdir() if p.name.startswith(".")]  # no staging left
    lock = Lock.load(project / "agent.lock")
    assert set(lock.entries) == {
        "runtime/adk@1.2.0",
        "prompt/hello-prompt@0.1.0",
        "eval/hello-evals@0.1.0",
    }
    assert lock.entries["runtime/adk@1.2.0"]["source"] == "builtin"
    assert lock.entries["prompt/hello-prompt@0.1.0"]["source"] == "registry"
    for ref, digest in plan.published.items():
        assert lock.entries[ref]["sha256"] == digest
    published = project / "registry" / "prompt" / "hello-prompt" / "0.1.0" / "prompt.md"
    assert published.read_text() == plan.files["src/prompt/prompt.md"]
    assert registry.verify(registry.roots(project)) == []


def test_output_validates_through_the_cli(tmp_path, capsys):
    make(tmp_path)
    assert cli.main(["validate", str(tmp_path / "hello" / "agent.yaml")]) == 0
    assert cli.main(["validate", str(tmp_path / "hello" / "stack.yaml")]) == 0
    assert "3 registry refs" in capsys.readouterr().out


def test_locked_build_passes_and_keeps_the_lock(tmp_path, identity_env):
    make(tmp_path)
    project = tmp_path / "hello"
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
            "bindings": {"DATABASE_URL": "secret://hello-db"},
            "agents": {"hello": {"identity": identity, "candidate_identity": dict(identity)}},
        }
    )
    before = (project / "agent.lock").read_bytes()
    contexts = []

    def builder(context, iidfile):
        contexts.append(context)
        assert (context / "artifacts" / "prompt" / "hello-prompt" / "prompt.md").is_file()
        return IMAGE

    result = build(
        project / "agent.yaml", identity_env.load(), "dev", mode="locked", builder=builder
    )
    assert contexts and result.image == IMAGE and result.candidate_manifest_path is not None
    assert (project / "agent.lock").read_bytes() == before


def test_existing_empty_directory_keeps_its_inode_and_mode(tmp_path):
    target = tmp_path / "hello"
    target.mkdir(mode=0o700)
    inode = target.stat().st_ino
    make(tmp_path)
    assert target.stat().st_ino == inode
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert (target / "agent.yaml").is_file()


def test_missing_parents_are_created(tmp_path):
    plan = newagent.create(newagent.plan("hello", "gemini", tmp_path / "a" / "b", today=TODAY))
    assert (plan.directory / "agent.yaml").is_file()


# -- refusals ------------------------------------------------------------------------------


def test_non_empty_directory_is_refused_untouched(tmp_path):
    target = tmp_path / "hello"
    target.mkdir()
    (target / "notes.txt").write_text("mine")
    before = snapshot(tmp_path)
    with pytest.raises(newagent.NewAgentError) as error:
        make(tmp_path)
    assert error.value.code == "DESTINATION_NOT_EMPTY"
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("kind", ["dir", "file", "dangling"])
def test_symlinked_destination_is_refused(tmp_path, kind):
    real = tmp_path / "real"
    if kind == "dir":
        real.mkdir()
    elif kind == "file":
        real.write_text("x")
    (tmp_path / "hello").symlink_to(real)
    with pytest.raises(newagent.NewAgentError) as error:
        make(tmp_path)
    assert error.value.code == "DESTINATION_UNSAFE"
    if kind == "dir":
        assert list(real.iterdir()) == []
    assert not (tmp_path / "real").exists() or kind != "dangling"


def test_file_destination_and_file_parent_are_refused(tmp_path):
    (tmp_path / "hello").write_text("x")
    with pytest.raises(newagent.NewAgentError) as error:
        make(tmp_path)
    assert error.value.code == "DESTINATION_UNSAFE"
    with pytest.raises(newagent.NewAgentError) as error:
        newagent.create(newagent.plan("hello", "gemini", tmp_path / "hello" / "sub"))
    assert error.value.code == "DESTINATION_UNSAFE"


def test_a_failure_removes_what_it_created(tmp_path, monkeypatch):
    def broken(source, root):
        raise registry.RegistryError("ENTRY_UNSAFE", "simulated")

    monkeypatch.setattr(registry, "publish", broken)
    with pytest.raises(newagent.NewAgentError) as error:
        newagent.create(newagent.plan("hello", "gemini", tmp_path / "a" / "hello"))
    assert error.value.code == "ENTRY_UNSAFE" and "simulated" in str(error.value)
    assert not (tmp_path / "a").exists()


def test_a_failure_in_an_existing_directory_leaves_it_empty(tmp_path, monkeypatch):
    target = tmp_path / "hello"
    target.mkdir()

    def broken(project, refs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(newagent, "_lock", broken)
    with pytest.raises(newagent.NewAgentError) as error:
        make(tmp_path)
    assert error.value.code == "WRITE_FAILED" and "No space" in str(error.value)
    assert target.is_dir() and list(target.iterdir()) == []


def test_an_entry_appearing_while_writing_is_refused(tmp_path, monkeypatch):
    target = tmp_path / "hello"
    real_modes = newagent._modes

    def racing(root):
        real_modes(root)
        (target / "agent.yaml").write_text("someone else")

    monkeypatch.setattr(newagent, "_modes", racing)
    with pytest.raises(newagent.NewAgentError) as error:
        make(tmp_path)
    assert error.value.code == "DESTINATION_NOT_EMPTY"
    assert (target / "agent.yaml").read_text() == "someone else"  # theirs is kept
    assert [p.name for p in target.iterdir()] == ["agent.yaml"]


def test_a_conflicting_registry_on_the_search_path_is_refused(tmp_path, monkeypatch):
    other = tmp_path / "other"
    version = other / "prompt" / "hello-prompt" / "0.1.0"
    version.mkdir(parents=True)
    (version / "manifest.yaml").write_text("kind: prompt\nname: hello-prompt\nversion: 0.1.0\n")
    (version / "prompt.md").write_text("different\n")
    monkeypatch.setenv(registry.PATH_ENV, str(other))
    with pytest.raises(newagent.NewAgentError) as error:
        make(tmp_path)
    assert error.value.code == "REF_CONFLICT"
    assert not (tmp_path / "hello").exists()


def test_a_template_that_does_not_validate_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(newagent, "STACK_YAML", "kind: stack\nschema_version: 1\nname: {name}\n")
    with pytest.raises(newagent.NewAgentError) as error:
        make(tmp_path)
    assert error.value.code == "TEMPLATE_INVALID"
    assert not (tmp_path / "hello").exists()


def test_a_template_publishing_the_wrong_ref_is_refused(tmp_path):
    plan = newagent.plan("hello", "gemini", tmp_path / "hello", today=TODAY)
    wrong = dict(plan.files)
    wrong["src/prompt/manifest.yaml"] = wrong["src/prompt/manifest.yaml"].replace("0.1.0", "0.3.0")
    plan = newagent.Plan(
        plan.name, plan.model, plan.directory, plan.review_by, wrong, plan.publishes
    )
    with pytest.raises(newagent.NewAgentError) as error:
        newagent.create(plan)
    assert error.value.code == "TEMPLATE_INVALID"
    assert not (tmp_path / "hello").exists()


# -- the command ---------------------------------------------------------------------------


def test_cli_writes_and_points_at_next(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("agentbreeder.hints._prefix", lambda: "agentbreeder")
    target = tmp_path / "hello"
    assert cli.main(["new", "agent", "hello", "--model", "gemini", "--dir", str(target)]) == 0
    out = capsys.readouterr().out
    assert "Published prompt/hello-prompt@0.1.0 and eval/hello-evals@0.1.0" in out
    assert "GEMINI_API_KEY=<your key>" in out
    assert out.strip().splitlines()[-1] == f"Next: agentbreeder next {shlex.quote(str(target))}"
    assert (target / "agent.lock").is_file()


def test_cli_dry_run_json_writes_nothing(tmp_path, capsys):
    target = tmp_path / "hello"
    before = snapshot(tmp_path)
    argv = ["new", "agent", "hello", "--model", "gemini", "--dir", str(target), "--dry-run"]
    assert cli.main([*argv, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert snapshot(tmp_path) == before
    assert result["dry_run"] is True and result["name"] == "hello"
    assert result["directory"] == str(target)
    assert result["model"] == "gemini" and result["key"] == "GEMINI_API_KEY"
    assert result["runtime"] == "runtime/adk@1.2.0"
    assert [entry["path"] for entry in result["files"]] == list(
        newagent.plan("hello", "gemini").files
    )
    assert {entry["mode"] for entry in result["files"]} == {"0644"}
    assert [entry["ref"] for entry in result["published"]] == [
        "prompt/hello-prompt@0.1.0",
        "eval/hello-evals@0.1.0",
    ]
    assert all(entry["sha256"] is None for entry in result["published"])
    assert result["cases"] == ["hours", "unknown"]
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "Dry run: nothing was written." in out and "Next:" not in out
    assert snapshot(tmp_path) == before


def test_cli_json_after_writing_has_the_hashes(tmp_path, capsys):
    target = tmp_path / "hello"
    argv = ["new", "agent", "hello", "--model", "gemini", "--dir", str(target), "--json"]
    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)  # nothing but JSON on stdout
    assert result["dry_run"] is False
    lock = Lock.load(target / "agent.lock")
    for entry in result["published"]:
        assert entry["sha256"] == lock.entries[entry["ref"]]["sha256"]


def test_cli_dry_run_still_refuses_a_non_empty_directory(tmp_path, capsys):
    target = tmp_path / "hello"
    target.mkdir()
    (target / "x").write_text("x")
    argv = ["new", "agent", "hello", "--model", "gemini", "--dir", str(target), "--dry-run"]
    assert cli.main([*argv, "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "DESTINATION_NOT_EMPTY"
    assert captured.err.startswith("[DESTINATION_NOT_EMPTY]")


def test_cli_refuses_a_bad_name_with_exit_2(tmp_path, capsys):
    assert (
        cli.main(["new", "agent", "Bad", "--model", "gemini", "--dir", str(tmp_path / "x")]) == 2
    )
    assert capsys.readouterr().err.startswith("[NAME_INVALID]")
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["new", "agent", "hello", "--model", "mistral"],
        ["new", "hello", "--model", "gemini"],
    ],
)
def test_parser_refuses(argv, capsys):
    # `new agent hello` without --model is refused by `run` (USAGE), since --from-spec needs
    # no --model: tests/test_agent_spec.py
    with pytest.raises(SystemExit) as error:
        cli.main(argv)
    assert error.value.code == 2


# -- next ----------------------------------------------------------------------------------


def test_next_offers_new_agent_now_that_it_ships(tmp_path):
    assert guide_checks._available("new")
    empty = tmp_path / "hello"
    empty.mkdir()
    step = next(
        s for s in guide_checks.compute(empty, probe=_probe(tmp_path)).steps if s.id == "project"
    )
    assert step.fix.action == "new-agent"
    words = shlex.split(step.fix.command)
    start = words.index("new")
    args = cli._parser().parse_args(words[start:])
    assert (args.command, args.new_command, args.name, args.model) == (
        "new",
        "agent",
        "hello",
        "gemini",
    )


def test_next_on_a_new_project_moves_to_keys(tmp_path):
    make(tmp_path)
    steps = {
        s.id: s for s in guide_checks.compute(tmp_path / "hello", probe=_probe(tmp_path)).steps
    }
    assert steps["project"].state == "done", steps["project"].reason
    assert steps["keys"].state == "next"
    assert steps["keys"].reason.code == "KEYS_MISSING"


def _probe(tmp_path):
    from types import SimpleNamespace

    def doctor():
        return [SimpleNamespace(name="Docker", passed=True, message="ok", required=True)]

    return guide_checks.Probe(
        doctor=doctor,
        containers=lambda state: {},
        clusters=list,
        environ={},
        stacks_root=tmp_path / "stacks",
    )


# -- review round 1 ------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["y", "n", "none", "nan", "e1"])
def test_yaml_lookalike_names_that_stay_strings_validate(tmp_path, name):
    plan = make(tmp_path, name)
    assert cli.main(["validate", str(plan.directory / "agent.yaml")]) == 0
    assert cli.main(["validate", str(plan.directory / "stack.yaml")]) == 0


@pytest.mark.parametrize("bad", ["e\x1b[31mX", "a\nNext: rm -rf", "x‮y"])
def test_control_characters_in_the_destination_are_refused_unechoed(tmp_path, capsys, bad):
    target = tmp_path / bad
    argv = ["new", "agent", "hello", "--model", "gemini", "--dir", str(target)]
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[DESTINATION_UNSAFE]")
    assert bad not in captured.err and "\x1b" not in captured.err and captured.out == ""
    assert not target.exists()


def test_an_existing_directory_writable_by_others_is_refused(tmp_path):
    target = tmp_path / "hello"
    target.mkdir()
    target.chmod(0o777)
    with pytest.raises(newagent.NewAgentError) as error:
        make(tmp_path)
    assert error.value.code == "DESTINATION_UNSAFE"
    assert list(target.iterdir()) == []


def test_an_existing_directory_owned_by_someone_else_is_refused(tmp_path, monkeypatch):
    (tmp_path / "hello").mkdir()
    monkeypatch.setattr(newagent.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(newagent.NewAgentError) as error:
        make(tmp_path)
    assert error.value.code == "DESTINATION_UNSAFE" and "owned by you" in str(error.value)


def test_group_write_is_accepted_only_for_a_private_group_off_macos(tmp_path, monkeypatch):
    """User private groups (umask 002) are common on Linux; on macOS the default group `staff`
    holds every local user, and a directory of another group is shared too."""
    target = tmp_path / "hello"
    target.mkdir()
    target.chmod(0o775)
    gid = target.stat().st_gid
    monkeypatch.setattr(newagent.sys, "platform", "darwin")
    monkeypatch.setattr(newagent.os, "getegid", lambda: gid)
    with pytest.raises(newagent.NewAgentError) as error:
        make(tmp_path)
    assert error.value.code == "DESTINATION_UNSAFE"
    monkeypatch.setattr(newagent.sys, "platform", "linux")
    monkeypatch.setattr(newagent.os, "getegid", lambda: gid + 1)
    with pytest.raises(newagent.NewAgentError):
        make(tmp_path)  # someone else's group
    monkeypatch.setattr(newagent.os, "getegid", lambda: gid)
    make(tmp_path)  # the user's own group, off macOS
    assert (target / "agent.yaml").is_file()


@pytest.mark.parametrize("umask", [0o000, 0o077])
def test_created_parents_and_destination_are_0755_whatever_the_umask(tmp_path, umask):
    old = os.umask(umask)
    try:
        newagent.create(newagent.plan("hello", "gemini", tmp_path / "a" / "b" / "hello"))
    finally:
        os.umask(old)
    for path in (tmp_path / "a", tmp_path / "a" / "b", tmp_path / "a" / "b" / "hello"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o755, path


def test_destination_stays_private_while_it_is_written(tmp_path, monkeypatch):
    seen = []
    real_write = newagent._write

    def spying(path, content):
        seen.append(stat.S_IMODE((tmp_path / "hello").stat().st_mode))
        real_write(path, content)

    monkeypatch.setattr(newagent, "_write", spying)
    make(tmp_path)
    assert seen and set(seen) == {0o700}
    assert stat.S_IMODE((tmp_path / "hello").stat().st_mode) == 0o755


def test_parents_made_before_a_failure_are_removed(tmp_path, monkeypatch):
    real_mkdir = Path.mkdir

    def failing(self, *args, **kwargs):
        if self.name == "hello":
            raise OSError(13, "Permission denied")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing)
    with pytest.raises(newagent.NewAgentError) as error:
        newagent.create(newagent.plan("hello", "gemini", tmp_path / "a" / "b" / "hello"))
    assert error.value.code == "WRITE_FAILED"
    assert not (tmp_path / "a").exists()


def test_an_identical_mirror_on_the_search_path_does_not_change_the_lock(tmp_path, monkeypatch):
    import shutil

    from agentbreeder.agentfile import load_agent_file
    from agentbreeder.lock import materialise

    mirror = tmp_path / "mirror"
    builtin = registry.BUILTIN_ROOT / "runtime" / "adk" / "1.2.0"
    shutil.copytree(builtin, mirror / "runtime" / "adk" / "1.2.0")
    monkeypatch.setenv(registry.PATH_ENV, str(mirror))
    make(tmp_path)
    project = tmp_path / "hello"
    lock = Lock.load(project / "agent.lock")
    assert lock.entries["runtime/adk@1.2.0"]["source"] == "builtin"
    assert {entry["source"] for entry in lock.entries.values()} == {"builtin", "registry"}
    _, changed = materialise(  # what `build --locked` does, with the mirror still set
        refs=load_agent_file(project / "agent.yaml").refs(),
        paths=[],
        project_dir=project,
        search=registry.roots(project),
        lock=lock,
        context=tmp_path / "context",
        mode="locked",
    )
    assert not changed


def test_json_errors_are_json_on_stdout(tmp_path, capsys):
    target = tmp_path / "hello"
    target.mkdir()
    (target / "x").write_text("x")
    argv = ["new", "agent", "hello", "--model", "gemini", "--dir", str(target), "--json"]
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["schema_version"] == newagent.SUMMARY_VERSION
    assert body["error"]["code"] == "DESTINATION_NOT_EMPTY"
    assert not body["error"]["message"].startswith("[")
    assert captured.err.startswith("[DESTINATION_NOT_EMPTY]")


@pytest.mark.parametrize("directory", ["2fast", "true", "a" * 40, "ok-name"])
def test_next_only_suggests_names_new_agent_accepts(tmp_path, directory):
    empty = tmp_path / directory
    empty.mkdir()
    step = next(
        s for s in guide_checks.compute(empty, probe=_probe(tmp_path)).steps if s.id == "project"
    )
    words = shlex.split(step.fix.command)
    args = cli._parser().parse_args(words[words.index("new") :])
    assert args.name == (directory if directory == "ok-name" else "hello")
    newagent.plan(args.name, args.model, args.dir)  # the suggestion is accepted
