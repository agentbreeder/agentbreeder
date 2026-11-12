"""`agentbreeder next` (#820, M4): the checks behind the guided-path contract, read-only."""

# Fixtures imported from test_release are redefined as parameters on purpose.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
import shlex
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_release import (  # noqa: F401 - fixtures
    Evaluator,
    FakeRuntime,
    do_release,
    operator,
    prepare_lock,
    project,
    version,
)

from agentbreeder import cli, guide, guide_checks
from agentbreeder.services.state import StackState, stack_id
from agentbreeder.store import StateBusy, StateNeedsMigration, Store

IMAGE = "sha256:" + "c" * 64
MARKER = "seeded-marker-" + "q" * 20  # stands in for a key value; must never be printed

STACK = """kind: stack
schema_version: 1
name: helper
services:
  postgres:
    ref: service/postgres-pgvector@1.0.0
  litellm:
    ref: service/litellm@1.0.0
    with: {config: stack/litellm/config.yaml}
environment:
  gateway: litellm
  models: [support-model, support-judge]
  ceilings: {budget_usd: 5, rpm: 60}
  agents:
    helper:
      sponsor: you@example.com
      review_by: "2099-01-01"
      candidate: true
"""

CONFIG = """model_list:
  - model_name: support-model
    litellm_params: {model: gemini/gemini-3.8-flash, api_key: os.environ/GEMINI_API_KEY}
  - model_name: support-judge
    litellm_params: {model: gemini/gemini-3.8-flash, api_key: os.environ/GEMINI_API_KEY}
  - model_name: fallback
    litellm_params:
      {model: openrouter/google/gemini-3.8-flash, api_key: os.environ/OPENROUTER_API_KEY}
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
"""


def check(name, passed, required=True):
    return SimpleNamespace(name=name, passed=passed, message=f"{name} message", required=required)


def healthy_doctor():
    return [check("Docker", True), check("agentbreeder on PATH", False, required=False)]


def running(*names):
    return {name: {"id": name, "state": "running", "health": "healthy"} for name in names}


class Calls:
    def __init__(self, containers=None, clusters=None):
        self.counts = {"doctor": 0, "containers": 0, "clusters": 0}
        self._containers = containers if containers is not None else running("postgres", "litellm")
        self._clusters = clusters if clusters is not None else []

    def doctor(self):
        self.counts["doctor"] += 1
        return healthy_doctor()

    def containers(self, state):
        self.counts["containers"] += 1
        if isinstance(self._containers, Exception):
            raise self._containers
        return self._containers

    def clusters(self):
        self.counts["clusters"] += 1
        return self._clusters


@pytest.fixture
def stacked(project):  # noqa: F811
    """The release test project, plus a stack.yaml, its LiteLLM config and service versions."""
    (project / "stack.yaml").write_text(STACK)
    (project / "stack" / "litellm").mkdir(parents=True)
    (project / "stack" / "litellm" / "config.yaml").write_text(CONFIG)
    root = project / "registry"
    version(root, "service", "postgres-pgvector", {})
    version(root, "service", "litellm", {})
    return project


@pytest.fixture
def stacks(tmp_path):
    return tmp_path / "stacks"


def bring_up(project, stacks, environment="dev", grant=True):  # noqa: F811
    """What `services up` leaves: stack.db, a grant, the environment's operator config."""
    state = StackState("helper", project.resolve(), stacks)
    if grant:
        state.put("grant", "sha256:" + "a" * 64)
    env = state.env_dir(environment)
    env.mkdir(parents=True, exist_ok=True, mode=0o700)
    (env / "operator.yaml").write_text("environments: {}\n")
    store = Store(env / "state")
    return state, store


def probe(stacks, calls=None, **kwargs):
    calls = calls or Calls()
    values = {
        "doctor": calls.doctor,
        "containers": calls.containers,
        "clusters": calls.clusters,
        "environ": {"GEMINI_API_KEY": MARKER},
        "stacks_root": stacks,
        "now": lambda: datetime(2026, 11, 12, tzinfo=UTC),
    }
    values.update(kwargs)
    return guide_checks.Probe(**values)


@pytest.fixture
def later_commands(monkeypatch):
    """As if `new agent` and `invoke` (later M4 PRs) were installed."""
    monkeypatch.setattr(guide_checks, "_available", lambda name: True)


def steps(result) -> dict:
    return {step.id: step for step in result.steps}


def released(stacked, operator, stacks, evaluator=None):  # noqa: F811
    prepare_lock(stacked, operator)
    state, store = bring_up(stacked, stacks)
    outcome, _ = do_release(stacked, operator, store, FakeRuntime(), IMAGE, evaluator)
    return state, store, outcome


def add_approval(store, revision, expires, agent="helper", identifier="a1"):
    store.db.execute(
        "INSERT INTO approvals (id, revision, evidence, principal_key, environment, agent, "
        "deployer, deployer_verified, sponsors, obligations, state, created, expires) "
        "VALUES (?, ?, '{}', 'k', 'dev', ?, 'dave', 1, '[]', '[]', 'pending', ?, ?)",
        (identifier, revision, agent, expires, expires),
    )


# -- whole paths ---------------------------------------------------------------------------


def test_empty_directory_points_at_new_agent(tmp_path, stacks, later_commands):
    empty = tmp_path / "hello"
    empty.mkdir()
    result = guide_checks.compute(empty, probe=probe(stacks))
    found = steps(result)
    assert result.next == "project" and not result.complete
    assert found["project"].reason.code == "PROJECT_MISSING"
    assert found["project"].fix.action == "new-agent"
    assert shlex.split(found["project"].fix.command)[1:] == [
        "new", "agent", "hello", "--model", "gemini", "--dir", str(empty.resolve()),
    ]  # fmt: skip
    for later in ("keys", "services", "build", "release", "try"):
        assert found[later].reason.code == guide.NOT_CHECKED


def test_released_project_is_complete_and_reads_the_stack_state(
    stacked,
    operator,
    stacks,
    later_commands,  # noqa: F811
):
    state, store, outcome = released(stacked, operator, stacks)
    assert outcome.code == 0, outcome.message
    calls = Calls()
    result = guide_checks.compute(stacked, probe=probe(stacks, calls))
    found = steps(result)
    assert result.complete and result.next is None, [
        (s.id, s.state, s.reason) for s in result.steps
    ]
    assert found["release"].data.released and found["release"].data.production.state == "ready"
    assert found["try"].fix.action == "invoke"
    assert "kubernetes" not in found
    assert result.conditions == []
    # One compose ps, no cluster call without a cluster.
    assert calls.counts == {"doctor": 1, "containers": 1, "clusters": 0}


def test_a_release_in_another_state_dir_is_not_this_stacks(
    stacked,
    operator,  # noqa: F811
    stacks,
    tmp_path,
):
    """`next DIR` reads the state `deploy --stack DIR` writes, not the default state."""
    prepare_lock(stacked, operator)
    bring_up(stacked, stacks)
    elsewhere = Store(tmp_path / "default-state")
    assert do_release(stacked, operator, elsewhere, FakeRuntime(), IMAGE)[0].code == 0
    result = guide_checks.compute(stacked, probe=probe(stacks))
    assert steps(result)["release"].reason.code == "NOT_RELEASED"
    assert result.next == "release"


def test_docker_down_blocks_and_skips_services(stacked, stacks):
    calls = Calls()
    result = guide_checks.compute(
        stacked, probe=probe(stacks, calls, doctor=lambda: [check("Docker", False)])
    )
    found = steps(result)
    assert found["prerequisites"].state == "blocked" and result.next is None
    assert found["prerequisites"].fix.action is None
    assert found["services"].reason.code == guide.NOT_CHECKED
    assert [c.code for c in result.conditions] == ["STEP_BLOCKED"]
    assert calls.counts["containers"] == 0


# -- keys ----------------------------------------------------------------------------------


def test_keys_missing_names_only_listed_aliases(stacked, stacks):
    result = guide_checks.compute(stacked, probe=probe(stacks, environ={}))
    keys = steps(result)["keys"]
    # OPENROUTER_API_KEY serves only a fallback alias outside environment.models.
    assert keys.data.required == ["GEMINI_API_KEY"] and keys.data.missing == ["GEMINI_API_KEY"]
    assert keys.reason.code == "KEYS_MISSING" and keys.fix.action == "set-keys"
    assert keys.fix.url == "https://aistudio.google.com/apikey"
    assert "<your key>" in keys.fix.note


def test_keys_stored_by_services_up_count(stacked, stacks):
    state, _ = bring_up(stacked, stacks)
    state.db.execute(
        "INSERT INTO secrets (service, name, origin) "
        "VALUES ('litellm', 'gemini-api-key', 'import')"
    )
    keys = steps(guide_checks.compute(stacked, probe=probe(stacks, environ={})))["keys"]
    assert keys.state == "done" and keys.data.stored == ["GEMINI_API_KEY"]


def test_keys_config_invalid_is_blocked(stacked, stacks):
    (stacked / "stack" / "litellm" / "config.yaml").write_text(
        CONFIG + "litellm_settings: {callbacks: [langfuse]}\n"
    )
    keys = steps(guide_checks.compute(stacked, probe=probe(stacks)))["keys"]
    assert keys.reason.code == "KEYS_CONFIG_INVALID" and keys.state == "blocked"


def test_no_litellm_config_means_no_keys(stacked, stacks):
    text = (stacked / "stack.yaml").read_text()
    (stacked / "stack.yaml").write_text(
        text.replace("    with: {config: stack/litellm/config.yaml}\n", "")
    )
    keys = steps(guide_checks.compute(stacked, probe=probe(stacks)))["keys"]
    assert keys.state == "done" and keys.data.required == []


# -- services ------------------------------------------------------------------------------


def test_services_never_up(stacked, stacks):
    calls = Calls()
    result = guide_checks.compute(stacked, probe=probe(stacks, calls))
    services = steps(result)["services"]
    assert services.reason.code == "SERVICES_DOWN" and result.next == "services"
    assert {s.state for s in services.data.services} == {"absent"}
    assert calls.counts["containers"] == 0  # no stack state: nothing to ask Compose


def test_services_unhealthy_raises_a_condition_per_service(stacked, stacks):
    bring_up(stacked, stacks)
    containers = running("postgres") | {"litellm": {"state": "exited", "health": ""}}
    result = guide_checks.compute(stacked, probe=probe(stacks, Calls(containers)))
    assert steps(result)["services"].reason.code == "SERVICES_UNHEALTHY"
    unhealthy = [c for c in result.conditions if c.code == "SERVICES_UNHEALTHY"]
    assert [c.service for c in unhealthy] == ["litellm"]


def test_services_unreadable_is_blocked(stacked, stacks):
    bring_up(stacked, stacks)
    result = guide_checks.compute(stacked, probe=probe(stacks, Calls(RuntimeError("boom"))))
    services = steps(result)["services"]
    assert services.state == "blocked" and services.reason.code == "SERVICES_UNREADABLE"


def test_environment_missing(stacked, stacks):
    bring_up(stacked, stacks, environment="staging")
    result = guide_checks.compute(stacked, probe=probe(stacks))
    assert steps(result)["services"].reason.code == "ENVIRONMENT_MISSING"


def test_all_down_after_services_down_is_not_a_condition(stacked, stacks):
    bring_up(stacked, stacks)
    result = guide_checks.compute(stacked, probe=probe(stacks, Calls({})))
    assert steps(result)["services"].reason.code == "SERVICES_DOWN"
    assert not [c for c in result.conditions if c.code == "SERVICES_UNHEALTHY"]


# -- build -----------------------------------------------------------------------------------


def test_lock_stale_is_next_without_console_action(stacked, stacks):
    bring_up(stacked, stacks)
    build = steps(guide_checks.compute(stacked, probe=probe(stacks)))["build"]
    assert build.reason.code == "LOCK_STALE" and build.state == "next"
    assert build.fix.action is None and shlex.split(build.fix.command)[1] == "build"


def test_not_built_then_stale(stacked, operator, stacks):  # noqa: F811
    bring_up(stacked, stacks)
    prepare_lock(stacked, operator)
    (stacked / "deploy" / "helper.build.json").unlink()
    build = steps(guide_checks.compute(stacked, probe=probe(stacks)))["build"]
    assert build.reason.code == "NOT_BUILT" and build.fix.action == "build"
    prepare_lock(stacked, operator)
    with (stacked / "agent.yaml").open("a") as handle:
        handle.write("# edited\n")
    build = steps(guide_checks.compute(stacked, probe=probe(stacks)))["build"]
    assert build.reason.code == "BUILD_STALE" and not build.data.agent_yaml_current


def test_path_ref_is_unpinned(stacked, stacks):
    (stacked / "prompt.md").write_text("Be helpful.")
    text = (stacked / "agent.yaml").read_text()
    (stacked / "agent.yaml").write_text(
        text.replace("prompt: {ref: prompt/helper@1.0.0}", "prompt: {path: prompt.md}")
    )
    build = steps(guide_checks.compute(stacked, probe=probe(stacks)))["build"]
    assert build.reason.code == "REF_UNPINNED" and build.fix.action is None


# -- release -------------------------------------------------------------------------------


def test_gate_failure_is_reported(stacked, operator, stacks):  # noqa: F811
    _, _, outcome = released(stacked, operator, stacks, Evaluator(passed=False))
    assert outcome.code == 5
    release = steps(guide_checks.compute(stacked, probe=probe(stacks)))["release"]
    assert release.reason.code == "RELEASE_GATE_FAILED" and release.data.gate_failed
    assert release.fix.action == "release"


def test_hand_deployed_production_is_ungated(stacked, operator, stacks):  # noqa: F811
    from conftest import actor
    from test_release import builder_for

    from agentbreeder.build import build
    from agentbreeder.deployment import DeploymentService
    from agentbreeder.manifest import load_manifest

    prepare_lock(stacked, operator)
    _, store = bring_up(stacked, stacks)
    built = build(stacked / "agent.yaml", operator.load(), "dev", builder=builder_for(IMAGE))
    service = DeploymentService(store, FakeRuntime(), operator.load())
    deployed = service.deploy(
        load_manifest(built.manifest_path), local=True, environment="dev", deployer=actor("dave")
    )
    result = guide_checks.compute(stacked, probe=probe(stacks))
    release = steps(result)["release"]
    assert release.reason.code == "NO_RELEASE_EVIDENCE" and not release.data.released
    ungated = [c for c in result.conditions if c.code == "PRODUCTION_UNGATED"]
    assert [c.deployment for c in ungated] == [deployed["id"]]


def test_state_needing_migration_blocks_release(stacked, operator, stacks, monkeypatch):  # noqa: F811
    released(stacked, operator, stacks)

    def needs(directory):
        raise StateNeedsMigration("old")

    monkeypatch.setattr(Store, "open_readonly", staticmethod(needs))
    found = steps(guide_checks.compute(stacked, probe=probe(stacks)))
    assert found["release"].state == "blocked" and found["release"].data is None
    assert shlex.split(found["release"].fix.command)[1] == "migrate"
    assert found["try"].reason.code == guide.NOT_CHECKED


def test_busy_state_is_an_error(stacked, operator, stacks, monkeypatch):  # noqa: F811
    released(stacked, operator, stacks)

    def busy(directory):
        raise StateBusy("busy")

    monkeypatch.setattr(Store, "open_readonly", staticmethod(busy))
    with pytest.raises(guide_checks.GuideError, match="STATE_BUSY"):
        guide_checks.compute(stacked, probe=probe(stacks))


def test_pending_approval_then_blocker(stacked, operator, stacks):  # noqa: F811
    from agentbreeder.manifest import load_manifest

    _, store, _ = released(stacked, operator, stacks)
    production = store.active("helper")
    revision = load_manifest(stacked / "deploy" / "helper.deploy.yaml").digest()
    # A real row carries the gate's revision digest, never the manifest digest.
    assert revision
    add_approval(
        store,
        "sha256:" + "9" * 64,
        (datetime(2026, 11, 12, tzinfo=UTC) + timedelta(hours=1)).isoformat(),
    )
    release = steps(guide_checks.compute(stacked, probe=probe(stacks)))["release"]
    assert release.reason.code == "APPROVAL_PENDING" and release.state == "blocked"
    store.db.execute("DELETE FROM approvals")
    store.db.execute(
        "INSERT INTO blockers (principal_key, environment, actor, actor_verified, reason, created)"
        " VALUES (?, 'dev', 'ops', 1, 'test', '2026-11-12')",
        (production["principal_key"],),
    )
    release = steps(guide_checks.compute(stacked, probe=probe(stacks)))["release"]
    assert release.reason.code == "PRODUCTION_DISABLED" and release.data.blocker
    assert shlex.split(release.fix.command)[1:3] == ["enable", production["id"]]


def test_expired_approval_is_ignored(stacked, operator, stacks):  # noqa: F811
    from agentbreeder.manifest import load_manifest

    _, store, _ = released(stacked, operator, stacks)
    revision = load_manifest(stacked / "deploy" / "helper.deploy.yaml").digest()
    add_approval(store, revision, "2020-01-02")
    assert steps(guide_checks.compute(stacked, probe=probe(stacks)))["release"].state == "done"


def test_newer_published_version_is_advisory(stacked, operator, stacks):  # noqa: F811
    released(stacked, operator, stacks)
    path = stacked / "registry" / "prompt" / "helper" / "1.0.1"
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text("kind: prompt\nname: helper\nversion: 1.0.1\n")
    (path / "prompt.md").write_text("Be very helpful.")
    result = guide_checks.compute(stacked, probe=probe(stacks))
    project_step = steps(result)["project"]
    assert project_step.state == "done" and result.complete
    assert [(n.ref, n.latest, n.line) for n in project_step.data.newer] == [
        ("prompt/helper@1.0.0", "1.0.1", 6)
    ]


# -- kubernetes ------------------------------------------------------------------------------


def test_kubernetes_step_only_with_a_cluster(stacked, operator, stacks):  # noqa: F811
    state, _, _ = released(stacked, operator, stacks)
    state.put("kubernetes", json.dumps({"cluster_uid": "u"}))
    result = guide_checks.compute(stacked, probe=probe(stacks, Calls(clusters=[])))
    kube = steps(result)["kubernetes"]
    assert kube.reason.code == "CLUSTER_DOWN" and kube.fix.action == "cluster-up"
    assert result.complete and result.next == "kubernetes"
    result = guide_checks.compute(stacked, probe=probe(stacks, Calls(clusters=["agentbreeder"])))
    kube = steps(result)["kubernetes"]
    assert kube.reason.code == "NOT_PROMOTED" and kube.fix.action == "promote"


# -- safety ----------------------------------------------------------------------------------


def _snapshot(*roots: Path) -> dict:
    found = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                path = Path(dirpath) / name
                info = path.lstat()
                found[str(path)] = (info.st_mode, info.st_size, info.st_mtime_ns)
    return found


def test_next_writes_nothing(stacked, operator, stacks):  # noqa: F811
    state, store, _ = released(stacked, operator, stacks)
    store.close()
    state.close()
    before = _snapshot(stacked, stacks)
    guide_checks.compute(stacked, probe=probe(stacks))
    assert _snapshot(stacked, stacks) == before
    # A stack root that does not exist is not created.
    other = stacks.parent / "never"
    guide_checks.compute(stacked, probe=probe(other))
    assert not other.exists()


def test_hostile_directory_name_is_quoted(tmp_path, stacks, later_commands):
    hostile = tmp_path / "x; touch pwned $(id)"
    hostile.mkdir()
    (hostile / "notes.txt").write_text("not empty")
    result = guide_checks.compute(hostile, probe=probe(stacks))
    assert MARKER not in result.model_dump_json()
    command = steps(result)["project"].fix.command
    # Not empty, so no --dir; the name is not a valid agent name, so the default is used.
    assert shlex.split(command)[1:] == ["new", "agent", "hello", "--model", "gemini"]
    empty = tmp_path / "y; touch pwned"
    empty.mkdir()
    command = steps(guide_checks.compute(empty, probe=probe(stacks)))["project"].fix.command
    assert shlex.split(command)[-1] == str(empty.resolve())


def test_no_key_value_printed_when_present_and_stored(stacked, operator, stacks):  # noqa: F811
    state, _, _ = released(stacked, operator, stacks)
    secrets = state.directory / "service-secrets" / "litellm"
    secrets.mkdir(parents=True)
    (secrets / "gemini-api-key").write_text(MARKER)
    result = guide_checks.compute(stacked, probe=probe(stacks))
    assert MARKER not in result.model_dump_json()


def test_unprintable_project_path(tmp_path, stacks):
    odd = tmp_path / "a​b"
    odd.mkdir()
    with pytest.raises(guide_checks.GuideError, match="PROJECT_PATH_UNSUPPORTED"):
        guide_checks.compute(odd, probe=probe(stacks))


def test_output_must_be_inside_project(stacked, stacks, tmp_path):
    with pytest.raises(guide_checks.GuideError, match="OUTPUT_OUTSIDE_PROJECT"):
        guide_checks.compute(stacked, output=tmp_path / "elsewhere", probe=probe(stacks))


def test_invalid_agent_file_is_blocked(stacked, stacks):
    (stacked / "agent.yaml").write_text("kind: agent\nschema_version: 1\nname: Bad Name\n")
    found = steps(guide_checks.compute(stacked, probe=probe(stacks)))["project"]
    assert found.state == "blocked" and found.reason.code == "PROJECT_INVALID"
    assert found.data.error_code


def test_unresolved_ref_is_blocked(stacked, stacks):
    text = (
        (stacked / "agent.yaml").read_text().replace("prompt/helper@1.0.0", "prompt/helper@9.9.9")
    )
    (stacked / "agent.yaml").write_text(text)
    found = steps(guide_checks.compute(stacked, probe=probe(stacks)))["project"]
    assert found.reason.code == "REF_UNRESOLVED" and found.state == "blocked"


def test_contract_violation_names_fields_not_values(tmp_path, stacks, monkeypatch):
    empty = tmp_path / "hello"
    empty.mkdir()

    def broken(ctx):
        step = guide_checks._step("prerequisites", "done", data={"checks": []})
        step["title"] = "‮forged"
        return step

    monkeypatch.setattr(guide_checks, "_prerequisites", broken)
    with pytest.raises(guide_checks.GuideError) as caught:
        guide_checks.compute(empty, probe=probe(stacks))
    assert caught.value.code == "GUIDE_OUTPUT_INVALID"
    assert "forged" not in str(caught.value)


def test_sqlite_error_is_unreadable(stacked, operator, stacks, monkeypatch):  # noqa: F811
    released(stacked, operator, stacks)

    def explode(*args, **kwargs):
        raise sqlite3.DatabaseError("x")

    monkeypatch.setattr(guide_checks, "_release", explode)
    with pytest.raises(guide_checks.GuideError, match="STATE_UNREADABLE"):
        guide_checks.compute(stacked, probe=probe(stacks))


def test_stack_id_is_the_one_services_up_uses(stacked, stacks):
    state, _ = bring_up(stacked, stacks)
    assert state.directory.name == stack_id("helper", stacked.resolve())


# -- CLI -----------------------------------------------------------------------------------


@pytest.fixture
def fake_reads(monkeypatch, stacks):
    calls = Calls()
    monkeypatch.setattr(guide_checks, "_doctor_checks", calls.doctor)
    monkeypatch.setattr(guide_checks, "_containers", calls.containers)
    monkeypatch.setattr(guide_checks, "_clusters", calls.clusters)
    monkeypatch.setattr("agentbreeder.services.state.stacks_root", lambda: stacks)
    return calls


def test_cli_text_and_json(tmp_path, fake_reads, capsys):
    empty = tmp_path / "hello"
    empty.mkdir()
    assert cli.main(["next", str(empty)]) == 1
    out = capsys.readouterr().out
    assert "→ Project: No agent.yaml" in out and "Next: " in out
    assert cli.main(["next", str(empty), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    guide.Guide.model_validate(data)
    assert data["next"] == "project"


def test_cli_complete_exits_zero(
    stacked,
    operator,
    stacks,
    fake_reads,
    capsys,
    monkeypatch,
    later_commands,  # noqa: F811
):
    released(stacked, operator, stacks)
    monkeypatch.setenv("GEMINI_API_KEY", MARKER)
    assert cli.main(["next", str(stacked)]) == 0
    out = capsys.readouterr().out
    assert "Done. Try it:" in out and MARKER not in out


def test_cli_blocked_prints_note(stacked, stacks, fake_reads, monkeypatch, capsys):
    monkeypatch.setattr(guide_checks, "_doctor_checks", lambda: [check("Docker", False)])
    assert cli.main(["next", str(stacked)]) == 1
    out = capsys.readouterr().out
    assert "✗ Prerequisites" in out and "Docker message" in out


def test_cli_errors(tmp_path, fake_reads, capsys):
    assert cli.main(["next", str(tmp_path / "missing")]) == 2
    assert "[PROJECT_DIR_MISSING]" in capsys.readouterr().err
    assert cli.main(["next", str(tmp_path), "--environment", "Bad Env"]) == 2
    assert "[ENVIRONMENT_INVALID]" in capsys.readouterr().err


def test_failed_and_noncompliant_production_are_conditions(stacked, operator, stacks):  # noqa: F811
    _, store, _ = released(stacked, operator, stacks)
    production = store.active("helper")
    store.db.execute(
        "UPDATE deployments SET state='failed', compliance='noncompliant:PRINCIPAL_BLOCKED' "
        "WHERE id=?",
        (production["id"],),
    )
    result = guide_checks.compute(stacked, probe=probe(stacks))
    release = steps(result)["release"]
    assert release.reason.code == "PRODUCTION_NOT_READY" and release.fix.action == "release"
    codes = {(c.code, c.deployment) for c in result.conditions}
    assert ("DEPLOYMENT_FAILED", production["id"]) in codes
    assert ("DEPLOYMENT_NONCOMPLIANT", production["id"]) in codes


def test_malformed_release_evidence_fails_closed(stacked, operator, stacks):  # noqa: F811
    _, store, _ = released(stacked, operator, stacks)
    rows = store.db.execute(
        "SELECT seq, reasons FROM audit WHERE action='release' AND decision='promoted'"
    ).fetchall()
    assert rows

    def forge(row):
        reasons = json.loads(row[1])
        for item in reasons:
            if item["code"] == "RELEASE_THRESHOLD":
                item["message"] = "not-a-number"
        return json.dumps(reasons)

    # Rewrite the audit row as a corrupt database would hold it (bypassing the chain on purpose).
    store.db.execute("DROP TRIGGER IF EXISTS audit_no_update")
    for row in rows:
        store.db.execute("UPDATE audit SET reasons=? WHERE seq=?", (forge(row), row[0]))
    result = guide_checks.compute(stacked, probe=probe(stacks))
    release = steps(result)["release"]
    assert release.reason.code == "NO_RELEASE_EVIDENCE" and not release.data.released
    assert not result.complete
    assert [c.code for c in result.conditions] == ["PRODUCTION_UNGATED"]


def test_symlinked_stacks_root_is_exit_two(stacked, stacks, tmp_path, fake_reads, capsys):
    real = tmp_path / "real-stacks"
    real.mkdir(mode=0o700)
    stacks.symlink_to(real)
    assert cli.main(["next", str(stacked), "--json"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("[STATE_UNREADABLE]") and "Traceback" not in err


def test_unreadable_project_is_exit_two(tmp_path, fake_reads, capsys):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        assert cli.main(["next", str(locked)]) == 2
        assert capsys.readouterr().err.startswith("[PROJECT_UNREADABLE]")
    finally:
        locked.chmod(0o700)


def test_unexpected_failure_is_one_coded_line(tmp_path, fake_reads, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise KeyError("/secret/path")

    monkeypatch.setattr(guide_checks, "compute", boom)
    assert cli.main(["next", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert err == "[NEXT_FAILED] next could not compute the checklist (KeyError)\n"
    assert "/secret/path" not in err


def test_locked_database_is_busy(stacked, operator, stacks, monkeypatch):  # noqa: F811
    released(stacked, operator, stacks)

    def locked(ctx):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(guide_checks, "_release", locked)
    with pytest.raises(guide_checks.GuideError, match="STATE_BUSY"):
        guide_checks.compute(stacked, probe=probe(stacks))


def test_fifo_lock_does_not_hang(stacked, stacks):
    os.mkfifo(stacked / "agent.lock")
    build = steps(guide_checks.compute(stacked, probe=probe(stacks)))["build"]
    assert build.reason.code == "LOCK_STALE"


def test_output_symlinked_outside_is_refused(stacked, stacks, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (stacked / "deploy-link").symlink_to(outside)
    with pytest.raises(guide_checks.GuideError, match="OUTPUT_OUTSIDE_PROJECT"):
        guide_checks.compute(stacked, output=stacked / "deploy-link", probe=probe(stacks))


def test_text_form_never_prints_a_key_value(stacked, operator, stacks):  # noqa: F811
    from agentbreeder.commands import next as next_command

    state, _, _ = released(stacked, operator, stacks)
    result = guide_checks.compute(stacked, probe=probe(stacks))
    assert MARKER not in next_command.text(result)
    assert MARKER not in result.model_dump_json()


LATER = (datetime(2026, 11, 12, tzinfo=UTC) + timedelta(hours=1)).isoformat()


def test_without_new_and_invoke_the_example_is_the_way_in(tmp_path, stacks):
    empty = tmp_path / "hello"
    empty.mkdir()
    project_step = steps(guide_checks.compute(empty, probe=probe(stacks)))["project"]
    assert project_step.fix.action == "example"
    assert shlex.split(project_step.fix.command)[1:] == ["example", "northwind", str(empty)]


def test_without_invoke_a_done_try_has_no_fix(stacked, operator, stacks):  # noqa: F811
    released(stacked, operator, stacks)
    result = guide_checks.compute(stacked, probe=probe(stacks))
    assert result.complete and steps(result)["try"].fix is None


def test_every_printed_command_parses(stacked, operator, stacks, tmp_path):  # noqa: F811
    """Each `fix.command` names a subcommand and flags this install's parser accepts."""
    parser = cli._parser()
    seen = []

    def commands(result):
        for step in result.steps:
            if step.fix and step.fix.command and "<" not in step.fix.command:
                words = shlex.split(step.fix.command)
                start = words.index("agentbreeder") + 1 if "agentbreeder" in words else 3
                seen.append(words[start])
                parser.parse_args(words[start:])

    empty = tmp_path / "empty"
    empty.mkdir()
    commands(guide_checks.compute(empty, probe=probe(stacks)))
    commands(guide_checks.compute(stacked, probe=probe(stacks)))  # lock stale: build
    released(stacked, operator, stacks, Evaluator(passed=False))
    commands(guide_checks.compute(stacked, probe=probe(stacks)))  # gate failed
    assert {"example", "build"} <= set(seen)


def test_candidate_approval_blocks_release(stacked, operator, stacks):  # noqa: F811
    _, store, _ = released(stacked, operator, stacks)
    add_approval(store, "sha256:" + "8" * 64, LATER, agent="helper-candidate")
    release = steps(guide_checks.compute(stacked, probe=probe(stacks)))["release"]
    assert release.reason.code == "APPROVAL_PENDING" and "approve" in release.fix.note


def test_name_approval_goes_to_kubernetes_when_released(stacked, operator, stacks):  # noqa: F811
    state, store, _ = released(stacked, operator, stacks)
    state.put("kubernetes", json.dumps({"cluster_uid": "u"}))
    add_approval(store, "sha256:" + "7" * 64, LATER)
    found = steps(
        guide_checks.compute(stacked, probe=probe(stacks, Calls(clusters=["agentbreeder"])))
    )
    assert found["release"].state == "done"
    assert found["kubernetes"].reason.code == "APPROVAL_PENDING"
    assert found["kubernetes"].state == "blocked"


def test_unbound_compliance_is_not_a_condition(stacked, operator, stacks):  # noqa: F811
    _, store, _ = released(stacked, operator, stacks)
    store.db.execute("UPDATE deployments SET compliance='unbound'")
    result = guide_checks.compute(stacked, probe=probe(stacks))
    assert not [c for c in result.conditions if c.code == "DEPLOYMENT_NONCOMPLIANT"]


def test_agent_without_evals_is_invalid(stacked, stacks):
    text = (stacked / "agent.yaml").read_text()
    lines = [line for line in text.splitlines() if not line.startswith("evals:")]
    (stacked / "agent.yaml").write_text("\n".join(lines) + "\n")
    project_step = steps(guide_checks.compute(stacked, probe=probe(stacks)))["project"]
    assert project_step.reason.code == "PROJECT_INVALID"
    assert project_step.data.error_code == "EVALS_MISSING" and project_step.fix.note


def test_unresolved_stack_ref_names_stack_yaml(stacked, stacks):
    text = (stacked / "stack.yaml").read_text().replace("litellm@1.0.0", "litellm@9.0.0")
    (stacked / "stack.yaml").write_text(text)
    project_step = steps(guide_checks.compute(stacked, probe=probe(stacks)))["project"]
    assert project_step.reason.code == "REF_UNRESOLVED"
    assert "stack.yaml" in project_step.reason.message and project_step.fix.command is None


def test_kubernetes_not_checked_without_docker(stacked, operator, stacks):  # noqa: F811
    state, _, _ = released(stacked, operator, stacks)
    state.put("kubernetes", json.dumps({"cluster_uid": "u"}))
    calls = Calls()
    found = steps(
        guide_checks.compute(
            stacked, probe=probe(stacks, calls, doctor=lambda: [check("Docker", False)])
        )
    )
    assert found["kubernetes"].reason.code == guide.NOT_CHECKED
    assert calls.counts["clusters"] == 0


def test_stale_production_after_a_rebuild(stacked, operator, stacks):  # noqa: F811
    from test_release import builder_for

    from agentbreeder.build import build

    released(stacked, operator, stacks)
    build(
        stacked / "agent.yaml", operator.load(), "dev", builder=builder_for("sha256:" + "d" * 64)
    )
    result = guide_checks.compute(stacked, probe=probe(stacks))
    release = steps(result)["release"]
    assert release.reason.code == "PRODUCTION_STALE" and release.data.released
    assert not [c for c in result.conditions if c.code == "PRODUCTION_UNGATED"]


def test_threshold_raised_after_release(stacked, operator, stacks):  # noqa: F811
    _, store, _ = released(stacked, operator, stacks)
    store.db.execute(
        "UPDATE audit SET reasons=replace(reasons, '\"1.0\"', '\"0.5\"') "
        "WHERE action='release' AND decision='promoted'"
    )
    release = steps(guide_checks.compute(stacked, probe=probe(stacks)))["release"]
    assert release.reason.code == "RELEASE_THRESHOLD_LOWERED" and release.data.released


def test_eval_set_changed_after_release(stacked, operator, stacks):  # noqa: F811
    released(stacked, operator, stacks)
    lock = json.loads((stacked / "agent.lock").read_text())
    lock["entries"]["eval/golden@1.0.0"]["sha256"] = "sha256:" + "e" * 64
    (stacked / "agent.lock").write_text(json.dumps(lock))
    release = steps(guide_checks.compute(stacked, probe=probe(stacks)))["release"]
    # A changed lock also makes the build stale; the release step still reports its own reason.
    assert release.reason.code in ("RELEASE_EVALSET_CHANGED", "PRODUCTION_STALE")


def test_disabled_without_blocker_is_not_ready(stacked, operator, stacks):  # noqa: F811
    _, store, _ = released(stacked, operator, stacks)
    store.db.execute("UPDATE deployments SET state='disabled'")
    release = steps(guide_checks.compute(stacked, probe=probe(stacks)))["release"]
    assert release.reason.code == "PRODUCTION_NOT_READY" and release.fix.action == "release"
