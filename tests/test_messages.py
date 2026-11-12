"""Error and result messages that name their fix (#819: G-F1, G-F2, G-F4, G-F5, G-F6, G-F9)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
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

    with pytest.raises(ReleaseError, match="run `agentbreeder build` first"):
        _eval_set(load_agent_file(project / "agent.yaml"), project)


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


def test_release_cli_prints_failed_cases(monkeypatch, capsys, tmp_path):
    from agentbreeder import release as release_module

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
