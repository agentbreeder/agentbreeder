"""G-F3 (#819): a hand-deployed production is evaluated by `release`, never called released."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import actor
from test_release import (  # noqa: F401 - fixtures
    Evaluator,
    FakeRuntime,
    builder_for,
    do_release,
    operator,
    prepare_lock,
    project,
)

from agentbreeder import evidence
from agentbreeder.agentfile import load_agent_file
from agentbreeder.build import build
from agentbreeder.deployment import DeploymentService
from agentbreeder.manifest import load_manifest
from agentbreeder.release import EXIT_EVAL, EXIT_OK, ReleaseError

IMAGE = "sha256:" + "c" * 64


def hand_deploy(project, operator, state, runtime, image=IMAGE):  # noqa: F811
    built = build(project / "agent.yaml", operator.load(), "dev", builder=builder_for(image))
    service = DeploymentService(state, runtime, operator.load())
    result = service.deploy(
        load_manifest(built.manifest_path), local=True, environment="dev", deployer=actor("dave")
    )
    assert result["state"] == "ready"
    return result


def promoted_rows(state):
    rows = state.audit_rows(limit=500)
    return [r for r in rows if (r["action"], r["decision"]) == ("release", "promoted")]


def test_hand_deployed_production_is_evaluated_and_recorded(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    production = hand_deploy(project, operator, state, runtime)
    evaluator = Evaluator()
    outcome, _ = do_release(project, operator, state, runtime, IMAGE, evaluator)
    assert outcome.code == EXIT_OK, outcome.message
    assert "already in production" not in outcome.message
    assert "was not replaced" in outcome.message
    assert "NO_RELEASE_EVIDENCE" in outcome.details["warnings"][0]
    # The gate ran once, against the candidate, not against production.
    assert len(evaluator.endpoints) == 1 and evaluator.endpoints[0] != production["endpoint"]
    # Production is the same deployment, untouched; the candidate is gone.
    assert state.active("helper")["id"] == production["id"]
    assert state.active("helper-candidate") is None
    assert len(promoted_rows(state)) == 1
    # Now it is released: the next run is a no-op, and promote accepts the evidence.
    again, _ = do_release(project, operator, state, runtime, IMAGE, Evaluator())
    assert again.code == EXIT_OK and "already in production" in again.message
    agent = load_agent_file(project / "agent.yaml")
    current = state.active("helper")
    assert evidence.released(state, agent, project, current, IMAGE)["RELEASE_IMAGE"] == IMAGE


def test_hand_deployed_production_failing_the_gate(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    production = hand_deploy(project, operator, state, runtime)
    outcome, _ = do_release(project, operator, state, runtime, IMAGE, Evaluator(passed=False))
    assert outcome.code == EXIT_EVAL
    assert "production is running an image that failed the gate" in outcome.message
    # No journal: rollback could not work, so it is not offered.
    assert outcome.details["unreleased_production"] == {"id": production["id"], "rollback": False}
    assert state.active("helper")["id"] == production["id"]  # never removed automatically
    assert promoted_rows(state) == []


def test_rollback_is_offered_only_when_a_journal_has_a_previous(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, "sha256:" + "1" * 64)
    do_release(project, operator, state, runtime, "sha256:" + "2" * 64)  # journal: previous=1
    # Someone deploys a third image by hand.
    production = hand_deploy_replacing(project, operator, state, runtime, "sha256:" + "3" * 64)
    outcome, _ = do_release(
        project, operator, state, runtime, "sha256:" + "3" * 64, Evaluator(passed=False)
    )
    assert outcome.code == EXIT_EVAL
    assert outcome.details["unreleased_production"] == {"id": production["id"], "rollback": True}


def hand_deploy_replacing(project, operator, state, runtime, image):  # noqa: F811
    service = DeploymentService(state, runtime, operator.load())
    current = state.active("helper")
    service.remove(current["id"], local=True, actor=actor("dave"))
    return hand_deploy(project, operator, state, runtime, image)


def test_a_lowered_threshold_release_is_evaluated_again(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    first, _ = do_release(project, operator, state, runtime, IMAGE, threshold=0.5)
    assert first.code == EXIT_OK
    evaluator = Evaluator()
    again, _ = do_release(project, operator, state, runtime, IMAGE, evaluator)
    assert again.code == EXIT_OK and "was not replaced" in again.message
    assert "RELEASE_THRESHOLD_LOWERED" in again.details["warnings"][0]
    assert evaluator.endpoints  # it ran the gate


def test_a_changed_eval_set_is_evaluated_again(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    assert do_release(project, operator, state, runtime, IMAGE)[0].code == EXIT_OK
    lock_path = project / "agent.lock"
    data = json.loads(lock_path.read_text())
    original = data["entries"]["eval/golden@1.0.0"]["sha256"]
    rows = promoted_rows(state)
    agent = load_agent_file(project / "agent.yaml")
    data["entries"]["eval/golden@1.0.0"]["sha256"] = "0" * 64
    lock_path.write_text(json.dumps(data))
    with pytest.raises(ReleaseError, match="RELEASE_EVALSET_CHANGED"):
        evidence.released(state, agent, project, state.active("helper"), IMAGE)
    data["entries"]["eval/golden@1.0.0"]["sha256"] = original
    lock_path.write_text(json.dumps(data))
    assert evidence.released(state, agent, project, state.active("helper"), IMAGE)
    assert len(rows) == 1


def test_release_cli_prints_the_warning(project, operator, state, monkeypatch, capsys, tmp_path):  # noqa: F811
    from agentbreeder import cli
    from agentbreeder import release as release_module
    from agentbreeder.release import Outcome

    outcome = Outcome(EXIT_OK, "Released x", {"warnings": ["production (abc) runs this image"]})

    def fake(*args, log, **kwargs):
        log("WARNING: production (abc) runs this image")
        return outcome

    monkeypatch.setattr(release_module, "release", fake)
    monkeypatch.setattr("agentbreeder.commands.release.resolve_deployer", lambda o: None)
    assert cli.main(["release", "agent.yaml", "--state-dir", str(tmp_path / "s")]) == 0
    assert "WARNING: production (abc)" in capsys.readouterr().err
    assert cli.main(["release", "agent.yaml", "--state-dir", str(tmp_path / "s"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["warnings"] == ["production (abc) runs this image"]


def test_advice_carries_the_stack_or_state_dir(monkeypatch, capsys, tmp_path):
    """Printed commands work as printed: same --stack/--environment or --state-dir (review r1)."""
    from agentbreeder import cli
    from agentbreeder import release as release_module
    from agentbreeder.release import Outcome

    details = {
        "agent": "hello",
        "candidate_log": str(tmp_path / "s" / "releases" / "hello.candidate.log"),
        "failed_cases": [{"id": "a", "failed_checks": ["judge_correct"], "reason": "x"}],
        "unreleased_production": {"id": "abc123", "rollback": True},
    }
    outcome = Outcome(EXIT_EVAL, "Eval gate failed", details)
    monkeypatch.setattr(release_module, "release", lambda *a, **k: outcome)
    monkeypatch.setattr("agentbreeder.commands.release.resolve_deployer", lambda o: None)
    state = tmp_path / "s"
    assert cli.main(["release", "agent.yaml", "--state-dir", str(state)]) == EXIT_EVAL
    out = capsys.readouterr().out
    assert f"agentbreeder logs --failed hello --state-dir {state}" in out
    assert f"agentbreeder remove abc123 --local --state-dir {state}" in out
    assert f"agentbreeder release agent.yaml --rollback --state-dir {state}" in out
    assert "agentbreeder build agent.yaml`" in out

    # --stack: what `services up` wrote, with its environment.
    from agentbreeder.services import engine

    stack_state = tmp_path / "stack-state"
    monkeypatch.setattr(
        engine,
        "context",
        lambda directory, env: engine.StackContext(
            "demo-1", "dev", tmp_path / "operator" / "operator.yaml", stack_state
        ),
    )
    folder = tmp_path / "proj"
    assert cli.main(["release", "agent.yaml", "--stack", str(folder), "--json"]) == EXIT_EVAL
    advice = json.loads(capsys.readouterr().out)["advice"]
    where = f"--stack {folder} --environment dev"
    assert advice == {
        "show_log": f"agentbreeder logs --failed hello {where}",
        "remove": f"agentbreeder remove abc123 --local {where}",
        "rollback": f"agentbreeder release agent.yaml --rollback {where}",
        "build": f"agentbreeder build agent.yaml {where}",
    }


def test_the_printed_logs_command_finds_the_tail(monkeypatch, capsys, tmp_path):
    """The reviewer's repro: `logs --failed` as printed after a --state-dir release reads that
    state, not the default."""
    from agentbreeder import cli

    state = tmp_path / "s"
    (state / "releases").mkdir(parents=True, mode=0o700)
    state.chmod(0o700)
    tail = state / "releases" / "hello.candidate.log"
    tail.write_text("boom\n")
    tail.chmod(0o600)
    argv = ["logs", "--failed", "hello", "--state-dir", str(state)]
    assert cli.main(argv) == 0 and capsys.readouterr().out == "boom\n"


def test_evidence_threshold_and_malformed_rows(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    first, _ = do_release(project, operator, state, runtime, IMAGE, threshold=0.5)
    assert first.code == EXIT_OK
    again, _ = do_release(project, operator, state, runtime, IMAGE, threshold=0.5)
    assert "already in production" in again.message  # same --threshold: no second promoted row
    assert len(promoted_rows(state)) == 1
    agent = load_agent_file(project / "agent.yaml")
    current = state.active("helper")

    class Rows:
        def audit_matching(self, action, decision):
            reasons = [
                {"code": "RELEASE_IMAGE", "message": IMAGE},
                {"code": "RELEASE_MANIFEST", "message": current["digest"]},
                {"code": "RELEASE_THRESHOLD", "message": "not-a-number"},
            ]
            return [{"reasons": reasons}]

    with pytest.raises(ReleaseError, match="RELEASE_EVIDENCE_INVALID"):
        evidence.released(Rows(), agent, project, current, IMAGE)


def test_production_replaced_during_evals_is_not_recorded(project, operator, state):  # noqa: F811
    """The unreleased-production pass path re-checks production before recording."""
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    production = hand_deploy(project, operator, state, runtime)

    class Swapping(Evaluator):
        def __call__(self, *args, **kwargs):
            state.transition(production["id"], "failed", error="crashed")
            return super().__call__(*args, **kwargs)

    with pytest.raises(ReleaseError, match="PRODUCTION_CHANGED"):
        do_release(project, operator, state, runtime, IMAGE, Swapping())
    assert promoted_rows(state) == []


def test_messages_raised_inside_release_carry_the_stack(project, operator, state):  # noqa: F811
    from agentbreeder import hints
    from agentbreeder.release import _eval_set, _removed_or_raise

    where = hints.Where(stack=Path("/p"), environment="dev")
    agent = load_agent_file(project / "agent.yaml")
    with pytest.raises(ReleaseError) as raised:
        _eval_set(agent, project, where)
    assert f"build {project / 'agent.yaml'} --stack /p --environment dev" in str(raised.value)

    class Stuck:
        def remove(self, identifier, local, actor):
            return {"state": "failed", "error": "busy"}

    with pytest.raises(ReleaseError, match="REMOVE_FAILED") as raised:
        _removed_or_raise(Stuck(), "abc", None, "the candidate", where=where)
    assert "remove abc --local --stack /p --environment dev" in str(raised.value)
    custom = hints.Where(environment="dev", state_dir=Path("/s"))
    assert custom.command("remove", "abc", "--local") == (
        "agentbreeder remove abc --local --state-dir /s"
    )
    assert custom.command("deploy", "m.yaml", "--local") == (
        "agentbreeder deploy m.yaml --local --environment dev --state-dir /s"
    )
    assert (
        custom.command("build", "agent.yaml") == "agentbreeder build agent.yaml --environment dev"
    )


def test_approval_message_carries_the_stack(project, operator, state, monkeypatch):  # noqa: F811
    from agentbreeder import hints

    prepare_lock(project, operator)
    service_class = DeploymentService

    class Approval:
        allowed = True
        obligations = ["require_approval"]

    monkeypatch.setattr(service_class, "preflight", lambda self, *a, **k: Approval())
    where = hints.Where(stack=Path("/p"), environment="dev")
    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE, where=where)
    assert "--local --stack /p --environment dev` after approval" in outcome.message
