"""Error and result messages that name their fix (#819: G-F1, G-F2, G-F4, G-F5, G-F6, G-F9)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unicodedata
from argparse import Namespace
from pathlib import Path

import pytest
from test_release import (  # noqa: F401 - fixtures
    Evaluator,
    FakeRuntime,
    do_release,
    operator,
    prepare_lock,
    project,
)

from agentbreeder import cli, evals, lock, registry
from agentbreeder.build import BuildError
from agentbreeder.release import EXIT_EVAL, Outcome
from agentbreeder.safe_text import excerpt, visible

SRC = str(Path(__file__).resolve().parents[1] / "src")


def args(command="build", stack=None):
    return Namespace(command=command, stack=stack)


# -- G-F1, G-F2: --stack hint ------------------------------------------------------------------


@pytest.mark.parametrize("code", ["CONTEXT_ENVIRONMENT_UNKNOWN", "MODEL_ACCESS_UNCONFIGURED"])
def test_stack_hint_when_stack_yaml_is_beside_the_agent(tmp_path, code):
    (tmp_path / "stack.yaml").write_text("kind: stack\n")
    message = cli._message(BuildError(code, "x"), args=args(), near=tmp_path / "agent.yaml")
    assert message.startswith(f"[{code}] x")
    assert f"add `--stack {tmp_path}`" in message


def test_stack_hint_one_level_above_a_manifest(tmp_path):
    (tmp_path / "stack.yaml").write_text("kind: stack\n")
    (tmp_path / "deploy").mkdir()
    error = BuildError("CONTEXT_ENVIRONMENT_UNKNOWN", "Unknown or missing --environment")
    message = cli._message(error, args=args("deploy"), near=tmp_path / "deploy" / "x.yaml")
    assert f"--stack {tmp_path}" in message


def test_an_agent_file_gets_no_hint_from_a_stack_two_levels_up(tmp_path):
    """Only a manifest (in deploy/) looks one level up; an agent file looks beside itself."""
    (tmp_path / "stack.yaml").write_text("kind: stack\n")
    (tmp_path / "sub").mkdir()
    error = BuildError("CONTEXT_ENVIRONMENT_UNKNOWN", "x")
    assert "--stack" not in cli._message(
        error, args=args("build"), near=tmp_path / "sub" / "a.yaml"
    )


def test_no_stack_hint_without_stack_yaml_with_stack_or_for_other_codes(tmp_path):
    error = BuildError("CONTEXT_ENVIRONMENT_UNKNOWN", "x")
    assert "--stack" not in cli._message(error, args=args(), near=tmp_path / "agent.yaml")
    (tmp_path / "stack.yaml").write_text("kind: stack\n")
    used = args(stack=tmp_path)
    assert "--stack" not in cli._message(error, args=used, near=tmp_path / "agent.yaml")
    other = BuildError("IDENTITY_UNBOUND", "x")
    assert "--stack" not in cli._message(other, args=args(), near=tmp_path / "agent.yaml")
    assert cli._message(error) == "[CONTEXT_ENVIRONMENT_UNKNOWN] x"


def test_symlinked_stack_yaml_is_not_suggested(tmp_path):
    real = tmp_path / "elsewhere.yaml"
    real.write_text("kind: stack\n")
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "stack.yaml").symlink_to(real)
    error = BuildError("CONTEXT_ENVIRONMENT_UNKNOWN", "x")
    assert "--stack" not in cli._message(error, args=args(), near=folder / "agent.yaml")


def test_build_end_to_end_names_the_stack_fix(project, operator):  # noqa: F811
    operator.load()
    (project / "stack.yaml").write_text("kind: stack\n")
    result = subprocess.run(
        [sys.executable, "-m", "agentbreeder", "build", "agent.yaml", "--environment", "nope"],
        cwd=project,
        env={**os.environ, "PYTHONPATH": SRC},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "[CONTEXT_ENVIRONMENT_UNKNOWN]" in result.stderr
    assert f"stack.yaml is in {project}" in result.stderr
    assert "--stack" in result.stderr


# -- G-F4: REF_UNPINNED on release ------------------------------------------------------------


def test_ref_unpinned_on_release_says_how_to_publish():
    error = registry.RegistryError("REF_UNPINNED", "--locked refuses path refs: path:./p.md")
    message = cli._message(error, args=args("release"), near=Path("agent.yaml"))
    assert "registry new KIND NAME" in message and "registry publish DIR" in message
    assert "agentbreeder build agent.yaml" in message
    # Only release gets the publish advice (build --locked has its own fix).
    assert "publish" not in cli._message(error, args=args("build"), near=Path("agent.yaml"))


def test_release_with_a_path_prompt_prints_the_publish_hint(project, operator, tmp_path, capsys):  # noqa: F811
    (project / "prompt.md").write_text("Be helpful.")
    text = (project / "agent.yaml").read_text()
    (project / "agent.yaml").write_text(
        text.replace("prompt: {ref: prompt/helper@1.0.0}", "prompt: {path: ./prompt.md}")
    )
    operator.load()
    code = cli.main(["release", str(project / "agent.yaml"), "--state-dir", str(tmp_path / "s")])
    err = capsys.readouterr().err
    assert code == 2 and "[REF_UNPINNED]" in err and "registry publish DIR" in err


# -- G-F5: LOCK_STALE --------------------------------------------------------------------------


def test_lock_stale_says_run_build(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")
    root = tmp_path / "registry" / "prompt" / "p" / "1.0.0"
    root.mkdir(parents=True)
    (root / "manifest.yaml").write_text("kind: prompt\nname: p\nversion: 1.0.0\n")
    (root / "prompt.md").write_text("hi")
    with pytest.raises(registry.RegistryError) as raised:
        lock.materialise(
            refs=["prompt/p@1.0.0"],
            paths=[],
            project_dir=tmp_path,
            search=[tmp_path / "registry"],
            lock=lock.Lock(),
            context=tmp_path / "context",
            mode="locked",
        )
    assert raised.value.code == "LOCK_STALE"
    assert "run `agentbreeder build` (without --locked)" in str(raised.value)
    assert "run without --locked" not in str(raised.value)


def test_unused_lock_entries_also_say_run_build(tmp_path):
    stale = lock.Lock({"prompt/gone@1.0.0": {"sha256": "x", "source": "registry"}})
    with pytest.raises(registry.RegistryError, match="run `agentbreeder build`"):
        lock.materialise(
            refs=[], paths=[], project_dir=tmp_path, search=[], lock=stale,
            context=tmp_path / "c", mode="locked",
        )  # fmt: skip


def test_stack_lock_stale_names_services_up(tmp_path):
    stale = lock.Lock({"service/gone@1.0.0": {"sha256": "x", "source": "registry"}})
    with pytest.raises(registry.RegistryError, match="run `agentbreeder services up`"):
        lock.materialise(
            refs=[], paths=[], project_dir=tmp_path, search=[], lock=stale,
            context=tmp_path / "c", mode="locked", lock_file="stack.lock",
            update_command="services up --update",
        )  # fmt: skip


def test_release_lock_stale_says_build_first(project, operator):  # noqa: F811
    from agentbreeder.agentfile import load_agent_file
    from agentbreeder.release import ReleaseError, _eval_set

    with pytest.raises(ReleaseError, match=r"run `agentbreeder build \S+agent\.yaml` first"):
        _eval_set(load_agent_file(project / "agent.yaml"), project)


def test_lock_stale_names_the_stack_when_stack_yaml_is_beside(tmp_path):
    """#854: `eval` before the first build: the `build` it suggests needs --stack too."""
    from agentbreeder.release import ReleaseError

    stale = "eval/e@1.0.0 is not in agent.lock; run `agentbreeder build` first"
    error = ReleaseError("LOCK_STALE", stale)
    near = tmp_path / "agent.yaml"
    assert "--stack" not in cli._message(error, args=args("eval"), near=near)
    (tmp_path / "stack.yaml").write_text("kind: stack\n")
    message = cli._message(error, args=args("eval"), near=near)
    assert message.startswith("[LOCK_STALE] eval/e@1.0.0 is not in agent.lock")
    assert f"stack.yaml is in {tmp_path}: run that build with `--stack {tmp_path}`" in message
    used = args("eval", stack=tmp_path)
    assert "stack.yaml is in" not in cli._message(error, args=used, near=near)
    services = registry.RegistryError("LOCK_STALE", "run `agentbreeder services up` first")
    assert "stack.yaml is in" not in cli._message(services, args=args("eval"), near=near)
    other = registry.RegistryError("LOCK_STALE", "mcp.rebuild is gone; see /work/build/x")
    assert "stack.yaml is in" not in cli._message(other, args=args("eval"), near=near)
    npx = registry.RegistryError("LOCK_STALE", "run `npx agentbreeder@3.0.0 build a.yaml` first")
    assert "stack.yaml is in" in cli._message(npx, args=args("eval"), near=near)


def test_eval_repeats_its_stack_in_the_build_hint(monkeypatch, tmp_path, capsys):
    """#854: with --stack, eval's LOCK_STALE hint is `build ... --stack DIR --environment ENV`."""
    from agentbreeder import agentfile, release
    from agentbreeder.commands import eval as eval_command

    seen = {}

    def fake_eval_set(agent, directory, where=None):
        seen["where"] = where
        raise release.ReleaseError("LOCK_STALE", "stale")

    monkeypatch.setattr(release, "_eval_set", fake_eval_set)
    monkeypatch.setattr(eval_command, "load_operator_config", lambda: Namespace())
    monkeypatch.setattr(agentfile, "load_agent_file", lambda path: Namespace())
    ran = Namespace(agent=str(tmp_path / "agent.yaml"), stack=tmp_path, environment="dev")
    assert eval_command.run(ran) == 2
    assert seen["where"].stack == tmp_path and seen["where"].environment == "dev"
    ran = Namespace(agent=str(tmp_path / "agent.yaml"), stack=None, environment="staging")
    eval_command.run(ran)
    assert seen["where"].stack is None and seen["where"].environment == "staging"
    capsys.readouterr()


# -- G-F6: failed cases ------------------------------------------------------------------------


def test_failed_cases_are_stripped_and_capped():
    results = [
        {"case_id": "ok", "passed": True, "checks": {"a": True}},
        {
            "case_id": "x" * 100 + "\x1b]52;c;ZXZpbA==\x07",
            "passed": False,
            "checks": {"agent_responded": True, "judge_correct": False, "matches:\x1b[2J": False},
            "explanation": "bad\nreply \x1b[31m" + "y" * 300,
        },
    ]
    [case] = evals.failed_cases(results)
    assert len(case["id"]) == 80 and case["id"].endswith("…")
    assert case["failed_checks"] == ["judge_correct", "matches: [2J"]
    assert len(case["reason"]) == 200 and "\x1b" not in case["reason"]
    assert "\n" not in case["reason"] and case["reason"].startswith("bad reply [31m")
    assert all("\x1b" not in value for value in case["failed_checks"])


def test_release_outcome_lists_failed_cases(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)

    class Failing(Evaluator):
        def __call__(self, eval_set, endpoint, *rest, **kwargs):
            report = super().__call__(eval_set, endpoint, *rest, **kwargs)
            for result in report.results:
                result["checks"] = {"agent_responded": True, "judge_correct": False}
                result["explanation"] = "The reply did not say hi."
            return report

    outcome, _ = do_release(
        project, operator, state, FakeRuntime(), "sha256:" + "8" * 64, Failing(passed=False)
    )
    assert outcome.code == EXIT_EVAL
    assert outcome.details["failed_cases"] == [
        {"id": c, "failed_checks": ["judge_correct"], "reason": "The reply did not say hi."}
        for c in ("a", "b")
    ]
    assert "failed_cases" in outcome.as_json()


def test_release_cli_prints_failed_cases(monkeypatch, capsys, tmp_path, identity_env):
    """Uses the test's own operator config (never ~/.agentbreeder/operator.yaml)."""
    from agentbreeder import release as release_module
    from agentbreeder.operator_config import CONFIG_ENV

    monkeypatch.setenv(CONFIG_ENV, str(identity_env.path))
    monkeypatch.setattr(
        "agentbreeder.commands.release.load_operator_config", lambda: identity_env.load()
    )

    failed = [{"id": "greets", "failed_checks": ["judge_correct"], "reason": "said bye"}]
    outcome = Outcome(EXIT_EVAL, "Eval gate failed: 1/2 passed", {"failed_cases": failed})
    monkeypatch.setattr(release_module, "release", lambda *a, **k: outcome)
    monkeypatch.setattr("agentbreeder.commands.release.resolve_deployer", lambda o: None)
    code = cli.main(["release", "agent.yaml", "--state-dir", str(tmp_path / "s")])
    out = capsys.readouterr().out
    assert code == EXIT_EVAL
    assert "FAIL greets: failed judge_correct; judge: said bye" in out
    assert "Next: edit the prompt or eval set" in out and "agentbreeder build agent.yaml" in out
    code = cli.main(["release", "agent.yaml", "--state-dir", str(tmp_path / "s"), "--json"])
    out = capsys.readouterr().out
    assert json.loads(out)["failed_cases"] == failed and "Next:" not in out


# -- G-F9: demo identity explanation -----------------------------------------------------------


def _deployment(warnings):
    return {
        "id": "abc",
        "state": "ready",
        "endpoint": "http://127.0.0.1:1",
        "gate": {
            "warnings": warnings,
            "attempt_id": "g1",
            "enforcement": "enforce",
            "checks_unavailable": ["policy"],
        },
    }


def test_demo_explanation_only_when_the_stack_config_was_selected(capsys):
    plain = Namespace(json=False)
    cli._print_deployment(_deployment(["Deployer is the unverified OS user"]), plain, "deploy")
    assert "demo identities" not in capsys.readouterr().err
    stacked = Namespace(json=False, demo_stack=True)
    cli._print_deployment(_deployment(["Deployer is the unverified OS user"]), stacked, "deploy")
    err = capsys.readouterr().err
    assert err.count(cli.DEMO_EXPLANATION) == 1 and "WARNING: Deployer" in err
    cli._print_deployment(_deployment([]), stacked, "deploy")
    assert cli.DEMO_EXPLANATION not in capsys.readouterr().err


# -- safe_text ---------------------------------------------------------------------------------


def test_visible_escapes_controls_but_keeps_tab_and_newline():
    text = "a\tb\nc\x1b]52;c;ZXZpbA==\x07d\x9b31m\re‮"
    assert visible(text) == "a\tb\nc\\x1b]52;c;ZXZpbA==\\x07d\\x9b31m\\x0de\\u202e"
    assert excerpt("a\x00b", 10) == "a b"


def _spans(*spans: tuple[int, int]) -> set[int]:
    return {cp for low, high in spans for cp in range(low, high + 1)}


# The full set of code points safe_text treats as unsafe, written out once here so a change to the
# pattern source (for example, writing `_BIDI` as escapes rather than literal characters) is
# checked over every code point and cannot widen or narrow it.
_BIDI_CPS = _spans((0x061C, 0x061C), (0x200E, 0x200F), (0x202A, 0x202E), (0x2066, 0x2069))
_UNSAFE_CPS = _spans((0x00, 0x1F), (0x7F, 0x9F), (0x2028, 0x2029), (0xD800, 0xDFFF)) | _BIDI_CPS


def test_safe_text_patterns_match_exactly_the_documented_union():
    from agentbreeder import safe_text

    any_control = {cp for cp in range(0x110000) if safe_text._ANY_CONTROL.fullmatch(chr(cp))}
    shown = {cp for cp in range(0x110000) if safe_text._SHOWN.fullmatch(chr(cp))}
    assert any_control == _UNSAFE_CPS
    assert shown == _UNSAFE_CPS - {0x09, 0x0A}
    assert {ord(c) for c in safe_text._BIDI if c != "-"} <= _BIDI_CPS
    # Escapes in the source, not literal invisible characters a reviewer cannot see.
    source = Path(safe_text.__file__).read_text(encoding="utf-8")
    hidden = [
        c for c in source if c != "\n" and unicodedata.category(c) in {"Cc", "Cf", "Zl", "Zp"}
    ]
    assert hidden == []


def test_safe_text_renders_each_unsafe_code_point():
    for cp in sorted(_UNSAFE_CPS):
        char = chr(cp)
        if cp in (0x09, 0x0A):
            assert visible(f"a{char}b") == f"a{char}b"
        else:
            escape = f"\\x{cp:02x}" if cp < 0x100 else f"\\u{cp:04x}"
            assert visible(f"a{char}b") == f"a{escape}b", hex(cp)
        assert excerpt(f"a{char}b", 10) == "a b", hex(cp)
