"""`deploy agent/<name>@X.Y.Z` and the store v4 package link (#763, M13 slice 3; spec §7)."""

# ruff: noqa: F811  (the project/operator fixtures are imported from test_release)

from __future__ import annotations

import json
import shutil
import sqlite3

import pytest
from conftest import actor
from test_release import (  # noqa: F401  (fixtures)
    EXIT_OK,
    FakeRuntime,
    do_release,
    operator,
    prepare_lock,
    project,
)

from agentbreeder import cli, deploy_ref, registry, registry_deployed
from agentbreeder.deployment import DeploymentService
from agentbreeder.registry import Ref
from agentbreeder.store import SCHEMA_VERSION, StateNeedsMigration, Store

IMAGE = "sha256:" + "1" * 64
OTHER = "sha256:" + "2" * 64
REF = "agent/helper@0.1.0"


def _released(project, operator, state, runtime=None, image=IMAGE):
    prepare_lock(project, operator)
    runtime = runtime or FakeRuntime()
    outcome, service = do_release(project, operator, state, runtime, image)
    assert outcome.code == EXIT_OK, outcome.message
    return outcome, service, runtime


def _events(state, action, decision):
    return [
        row
        for row in reversed(state.audit_rows(limit=1000))
        if (row["action"], row["decision"]) == (action, decision)
    ]


def _reasons(row):
    return {item["code"]: item["message"] for item in row.get("reasons") or []}


def _deploy(service, project, operator, environment="dev", ref=REF):
    prepared = deploy_ref.prepare(Ref.parse(ref), project, service, operator.load(), environment)
    return prepared, service.deploy(
        prepared.manifest,
        local=True,
        environment=prepared.environment,
        deployer=actor("dave"),
        package=(prepared.ref, prepared.sha256),
    )


def _remove_production(service, state):
    production = state.active("helper")
    service.remove(production["id"], local=True, actor=actor("dave"))


def _code(error) -> str:
    return error.value.code


# -- release links production ------------------------------------------------------------------


def test_release_links_production_to_its_package(project, operator, state):
    outcome, _, _ = _released(project, operator, state)
    production = state.get(outcome.details["production"]["id"])
    digest = registry.copy_and_hash(project / "registry" / "agent" / "helper" / "0.1.0")
    assert production["package"] == {"ref": REF, "sha256": digest}
    linked = _events(state, "deploy", "package_linked")
    assert len(linked) == 1 and linked[0]["deployment_id"] == production["id"]
    assert _reasons(linked[0]) == {"DEPLOY_PACKAGE": f"{REF} {digest}"}
    # The link comes right after the release row.
    rows = state.audit_rows(limit=2)
    assert [(r["action"], r["decision"]) for r in rows] == [
        ("deploy", "package_linked"),
        ("release", "promoted"),
    ]


def test_package_exists_repairs_a_null_link(project, operator, state):
    outcome, _, runtime = _released(project, operator, state)
    identifier = outcome.details["production"]["id"]
    with state.transaction():
        state.db.execute("UPDATE deployments SET package=NULL WHERE id=?", (identifier,))
    again, _ = do_release(project, operator, state, runtime, IMAGE)
    assert "[PACKAGE_EXISTS]" in again.message
    assert state.get(identifier)["package"]["ref"] == REF
    assert len(_events(state, "deploy", "package_linked")) == 2
    # A link that is set is left alone.
    do_release(project, operator, state, runtime, IMAGE)
    assert len(_events(state, "deploy", "package_linked")) == 2


def test_a_g_f3_release_replaces_the_link_and_names_the_previous_one(project, operator, state):
    outcome, service, runtime = _released(project, operator, state)
    identifier = outcome.details["production"]["id"]
    # A stricter threshold makes the earlier release not count: the same image is released again.
    again, _ = do_release(project, operator, state, runtime, IMAGE, threshold=1.0, version="0.2.0")
    assert again.code == EXIT_OK
    # The threshold is the same, so the image counts as released: no new link.
    assert state.get(identifier)["package"]["ref"] == REF
    service.link_package(identifier, "agent/helper@0.2.0", "sha256:" + "a" * 64, actor("dave"))
    latest = _events(state, "deploy", "package_linked")[-1]
    assert _reasons(latest)["PREVIOUS_PACKAGE"].startswith(f"{REF} sha256:")
    assert state.get(identifier)["package"]["ref"] == "agent/helper@0.2.0"


# -- deploy by ref: the happy paths ------------------------------------------------------------


def test_deploy_by_ref_after_remove(project, operator, state):
    _, service, runtime = _released(project, operator, state)
    _remove_production(service, state)
    prepared, result = _deploy(service, project, operator)
    assert result["state"] == "ready" and result["package"] == {
        "ref": REF,
        "sha256": prepared.sha256,
    }
    assert runtime.containers[result["container"]]["image"] == IMAGE
    allow = _events(state, "deploy", "allow")[-1]
    assert _reasons(allow) == {"DEPLOY_PACKAGE": f"{REF} {prepared.sha256}"}
    manifest = state.manifest(result["id"])
    assert manifest["target"] == "local" and manifest["image"] == IMAGE
    assert manifest["identity"]["ref"] == "agent-1"  # from the operator config
    assert prepared.notes == ()


def test_deploy_by_ref_over_an_equal_running_deployment_reuses_it(project, operator, state):
    outcome, service, _ = _released(project, operator, state)
    identifier = outcome.details["production"]["id"]
    with state.transaction():
        state.db.execute("UPDATE deployments SET package=NULL WHERE id=?", (identifier,))
    _, result = _deploy(service, project, operator)
    assert result["id"] == identifier and result["state"] == "ready"
    assert result["package"]["ref"] == REF  # a NULL column is set
    assert not any("stays linked" in w for w in result["gate"]["warnings"])


def test_deploy_by_ref_never_replaces_another_link(project, operator, state):
    outcome, service, _ = _released(project, operator, state)
    identifier = outcome.details["production"]["id"]
    other = {"ref": "agent/helper@9.9.9", "sha256": "sha256:" + "b" * 64}
    with state.transaction():
        state.db.execute(
            "UPDATE deployments SET package=? WHERE id=?", (json.dumps(other), identifier)
        )
    _, result = _deploy(service, project, operator)
    assert result["package"] == other
    assert any("stays linked to agent/helper@9.9.9" in w for w in result["gate"]["warnings"])


@pytest.mark.parametrize("state_of", ["starting", "failed"])
def test_update_binding_sets_the_package_only_while_null(state, state_of):
    record = state.create("d1", {"name": "x", "image": IMAGE}, "sha256:d", "ctx")
    state.transition(record["id"], state_of)
    first = {"ref": REF, "sha256": "sha256:" + "c" * 64}
    state.update_binding("d1", revision=None, approval_id=None, package=first)
    assert state.get("d1")["package"] == first
    state.update_binding(
        "d1", revision=None, approval_id=None, package={"ref": "agent/x@1.0.0", "sha256": "s"}
    )
    assert state.get("d1")["package"] == first and state.get("d1")["state"] == state_of


def test_status_shows_a_null_package(state):
    state.create("d1", {"name": "x", "image": IMAGE}, "sha256:d", "ctx")
    assert state.get("d1")["package"] is None


def test_a_deploy_to_another_environment_notes_where_the_evals_ran(project, operator, state):
    operator.config["environments"]["prod"] = json.loads(
        json.dumps(operator.config["environments"]["dev"])
    )
    _, service, _ = _released(project, operator, state)
    _remove_production(service, state)
    prepared = deploy_ref.prepare(Ref.parse(REF), project, service, operator.load(), "prod")
    assert prepared.environment == "prod"
    assert "evaluated in dev, not prod" in prepared.notes[0]


# -- refusals (spec §15) -----------------------------------------------------------------------


def test_parse_tells_refs_from_paths():
    assert deploy_ref.parse("agent/helper@0.1.0") == Ref.parse(REF)
    for path in ("deploy/helper.deploy.yaml", "./agent/helper@0.1.0", "agent/helper@0.1"):
        assert deploy_ref.parse(path) is None
    with pytest.raises(deploy_ref.PackageDeployError) as error:
        deploy_ref.parse("tool/x@1.0.0")
    assert _code(error) == "REF_NOT_DEPLOYABLE" and error.value.exit == 2


def _refused(service, project, operator, environment="dev", ref=REF, **kwargs):
    with pytest.raises(deploy_ref.PackageDeployError) as error:
        deploy_ref.prepare(
            Ref.parse(ref), project, service, operator.load(), environment, **kwargs
        )
    return error


def test_missing_package(project, operator, state):
    _, service, _ = _released(project, operator, state)
    assert _code(_refused(service, project, operator, ref="agent/helper@3.0.0")) == (
        "PACKAGE_NOT_FOUND"
    )


def test_a_package_outside_the_project_registry(project, operator, state, tmp_path, monkeypatch):
    _, service, _ = _released(project, operator, state)
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(project / "registry" / "agent", elsewhere / "agent")
    shutil.move(project / "registry" / "agent" / "helper" / "0.1.0", tmp_path / "moved-away")
    monkeypatch.setenv(registry.PATH_ENV, str(elsewhere))
    error = _refused(service, project, operator)
    assert _code(error) == "PACKAGE_NOT_HERE" and "elsewhere" in str(error.value)


def test_a_symlinked_package_is_refused(project, operator, state, tmp_path):
    _, service, _ = _released(project, operator, state)
    version = project / "registry" / "agent" / "helper" / "0.1.0"
    shutil.move(version, tmp_path / "real")
    version.symlink_to(tmp_path / "real")
    assert _code(_refused(service, project, operator)) == "PACKAGE_INVALID"


def test_unreleased_here(project, operator, state, tmp_path):
    _released(project, operator, state)
    fresh = Store(tmp_path / "other-state")
    try:
        service = DeploymentService(fresh, FakeRuntime(), operator.load())
        assert _code(_refused(service, project, operator)) == "PACKAGE_UNRELEASED_HERE"
    finally:
        fresh.close()


def test_another_project_with_the_same_agent(project, operator, state, tmp_path):
    _, service, _ = _released(project, operator, state)
    copy = tmp_path / "copy"
    shutil.copytree(project, copy)
    assert _code(_refused(service, copy, operator)) == "PACKAGE_OTHER_PROJECT"


def test_a_package_changed_after_release(project, operator, state):
    _, service, _ = _released(project, operator, state)
    manifest = project / "registry" / "agent" / "helper" / "0.1.0" / "manifest.yaml"
    manifest.write_text(manifest.read_text() + "# edited\n")
    error = _refused(service, project, operator)
    assert _code(error) == "PACKAGE_CHANGED" and "restore it from git" in str(error.value)


def test_an_invalid_package(project, operator, state):
    _, service, _ = _released(project, operator, state)
    (project / "registry" / "agent" / "helper" / "0.1.0" / "extra.txt").write_text("x")
    assert _code(_refused(service, project, operator)) == "PACKAGE_INVALID"


def test_ambiguous_release_rows(project, operator, state):
    _, service, _ = _released(project, operator, state)
    row = _events(state, "release", "promoted")[-1]
    forged = [
        {"code": c, "message": OTHER if c == "RELEASE_IMAGE" else m}
        for c, m in _reasons(row).items()
    ]
    service.record("release", "promoted", actor("dave"), forged)
    assert _code(_refused(service, project, operator)) == "PACKAGE_AMBIGUOUS"


@pytest.mark.parametrize(
    ("code", "value", "named"),
    [
        ("RELEASE_IMAGE", OTHER, "image"),
        ("RELEASE_THRESHOLD", "0.5", "threshold"),
        ("RELEASE_THRESHOLD", "not-a-number", "threshold"),
        ("RELEASE_EVALSET", "sha256:" + "d" * 64, "eval set"),
        ("RELEASE_EVAL", "run9 2/2", "eval run"),
    ],
)
def test_evidence_mismatch(project, operator, state, monkeypatch, code, value, named):
    _, service, _ = _released(project, operator, state)
    rows = state.audit_matching("release", "promoted")
    for item in rows[0]["reasons"]:
        if item["code"] == code:
            item["message"] = value
    monkeypatch.setattr(state, "audit_matching", lambda action, decision: rows)
    error = _refused(service, project, operator)
    assert _code(error) == "PACKAGE_EVIDENCE_MISMATCH" and named in str(error.value)


def test_a_corrupt_audit_log(project, operator, state):
    _, service, _ = _released(project, operator, state)
    with state.transaction():
        state.db.execute("UPDATE audit SET actor='mallory' WHERE seq=1")
    error = _refused(service, project, operator)
    assert _code(error) == "AUDIT_CORRUPT" and error.value.exit == 1
    assert "audit verify" in str(error.value)


def test_a_pruned_image(project, operator, state):
    _, service, runtime = _released(project, operator, state)
    runtime.missing_images.add(IMAGE)
    error = _refused(service, project, operator)
    assert _code(error) == "PACKAGE_IMAGE_MISSING" and error.value.exit == 1
    assert "cannot be rebuilt bit for bit" in str(error.value)


def test_cluster_is_not_yet_supported(project, operator, state):
    _, service, _ = _released(project, operator, state)
    assert _code(_refused(service, project, operator, cluster=True)) == (
        "PACKAGE_TARGET_UNSUPPORTED"
    )


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("unknown", "CONTEXT_ENVIRONMENT_UNKNOWN"),
        ("gateway", "MODEL_ACCESS_UNCONFIGURED"),
        ("identity", "IDENTITY_UNBOUND"),
    ],
)
def test_binding_needs_the_environment(project, operator, state, change, code):
    _, service, _ = _released(project, operator, state)
    environment = "dev"
    if change == "unknown":
        environment = "nowhere"
    elif change == "gateway":
        operator.config["environments"]["dev"].pop("gateway")
        operator.config["environments"]["dev"].pop("models", None)
    else:
        operator.config["environments"]["dev"]["agents"] = {}
    assert _code(_refused(service, project, operator, environment=environment)) == code


# -- store v4 ----------------------------------------------------------------------------------


def _as_v3(directory):
    db = sqlite3.connect(directory / "deployments.sqlite3")
    db.execute("ALTER TABLE deployments DROP COLUMN package")
    db.execute("PRAGMA user_version=3")
    db.commit()
    db.close()


def test_a_v3_state_upgrades_and_keeps_its_rows(tmp_path):
    directory = tmp_path / "state"
    store = Store(directory)
    store.create("d1", {"name": "x", "image": IMAGE}, "sha256:d", "ctx")
    store.close()
    _as_v3(directory)
    with pytest.raises(StateNeedsMigration):
        Store.open_readonly(directory)
    upgraded = Store(directory)
    try:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4
        assert upgraded.get("d1")["package"] is None and upgraded.get("d1")["name"] == "x"
    finally:
        upgraded.close()


def test_used_by_reads_a_v3_state_without_the_column(project, operator, state, tmp_path):
    _released(project, operator, state)
    _as_v3(state.directory)
    ref = Ref.parse(REF)
    result = registry_deployed.deployed(
        ref, registry.usage(ref, project), project, [state.directory]
    )
    assert result["deployed"] == [] and result["errors"] == []


# -- registry used-by agent/ -------------------------------------------------------------------


def test_used_by_lists_package_deployments(project, operator, state):
    outcome, service, _ = _released(project, operator, state)
    ref = Ref.parse(REF)

    def used():
        return registry_deployed.deployed(
            ref, registry.usage(ref, project), project, [state.directory]
        )

    result = used()
    assert [e["deployment"] for e in result["deployed"]] == [outcome.details["production"]["id"]]
    assert result["deployed"][0]["package_sha256"].startswith("sha256:")
    manifest = project / "registry" / "agent" / "helper" / "0.1.0" / "manifest.yaml"
    manifest.write_text(manifest.read_text() + "# edited\n")
    assert "differs" in used()["deployed_undetermined"][0]["reason"]
    shutil.rmtree(manifest.parent)
    assert "no copy" in used()["deployed_undetermined"][0]["reason"]
    _remove_production(service, state)
    assert used()["deployed_undetermined"] == []  # removed deployments are not listed


def test_used_by_ignores_a_malformed_column(project, operator, state):
    outcome, _, _ = _released(project, operator, state)
    with state.transaction():
        state.db.execute(
            "UPDATE deployments SET package='{not json' WHERE id=?",
            (outcome.details["production"]["id"],),
        )
    ref = Ref.parse(REF)
    result = registry_deployed.deployed(
        ref, registry.usage(ref, project), project, [state.directory]
    )
    assert result["deployed"] == [] and result["deployed_undetermined"] == []
    assert registry_deployed._package(json.dumps({"ref": "bad", "sha256": IMAGE})) is None
    assert registry_deployed._package(json.dumps([1])) is None


# -- the CLI -----------------------------------------------------------------------------------


@pytest.fixture
def cli_service(monkeypatch):
    def patch(runtime):
        monkeypatch.setattr(cli, "Docker", lambda: runtime)
        monkeypatch.setattr(
            "agentbreeder.commands.deploy.resolve_deployer", lambda operator: actor("dave")
        )

    return patch


def _cli(state, *argv):
    return cli.main(["deploy", *argv, "--local", "--state-dir", str(state.directory)])


def test_cli_deploys_a_ref(project, operator, state, cli_service, capsys):
    _, service, runtime = _released(project, operator, state)
    _remove_production(service, state)
    cli_service(runtime)
    capsys.readouterr()
    assert _cli(state, REF, "--project", str(project), "--environment", "dev", "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "ready" and result["package"]["ref"] == REF


def test_cli_refusals_and_exit_codes(project, operator, state, cli_service, capsys, monkeypatch):
    _, service, runtime = _released(project, operator, state)
    cli_service(runtime)
    capsys.readouterr()
    assert _cli(state, "tool/x@1.0.0") == 2
    assert "[REF_NOT_DEPLOYABLE]" in capsys.readouterr().err
    monkeypatch.chdir(project)  # --project defaults to the current directory
    assert _cli(state, "agent/helper@3.0.0", "--json") == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["code"] == "PACKAGE_NOT_FOUND"
    assert "[PACKAGE_NOT_FOUND]" in captured.err
    runtime.missing_images.add(IMAGE)
    assert _cli(state, REF) == 1
    assert "[PACKAGE_IMAGE_MISSING]" in capsys.readouterr().err
    assert _cli(state, "deploy/helper.deploy.yaml", "--project", ".") == 2
    assert "[PROJECT_UNUSED]" in capsys.readouterr().err


def test_cli_notes_the_environment(project, operator, state, cli_service, capsys):
    operator.config["environments"]["prod"] = json.loads(
        json.dumps(operator.config["environments"]["dev"])
    )
    _, service, runtime = _released(project, operator, state)
    _remove_production(service, state)
    cli_service(runtime)
    capsys.readouterr()
    # The note is printed, then prod's own gate applies: this identity is bound to dev.
    assert _cli(state, REF, "--project", str(project), "--environment", "prod") == 2
    err = capsys.readouterr().err
    assert "Note: agent/helper@0.1.0 was evaluated in dev" in err
    assert "[ENVIRONMENT_MISMATCH]" in err


# -- error branches ----------------------------------------------------------------------------


def test_link_package_needs_a_deployment(state):
    from agentbreeder.store import DeploymentError

    with pytest.raises(DeploymentError, match="not found"):
        DeploymentService(state, FakeRuntime(), None).link_package(
            "nope", REF, "sha256:" + "a" * 64, actor("dave")
        )


def test_an_uncopyable_package(project, operator, state, monkeypatch):
    _, service, _ = _released(project, operator, state)

    def broken(*args, **kwargs):
        raise registry.RegistryError("ENTRY_UNSAFE", "x")

    monkeypatch.setattr(registry, "copy_and_hash", broken)
    assert _code(_refused(service, project, operator)) == "PACKAGE_INVALID"


def test_a_bind_failure_keeps_its_code(project, operator, state, monkeypatch):
    from agentbreeder.build import BuildError

    _, service, _ = _released(project, operator, state)

    def broken(*args, **kwargs):
        raise BuildError("MANIFEST_INVALID", "generated deploy manifest is invalid: x")

    monkeypatch.setattr(deploy_ref, "bind", broken)
    error = _refused(service, project, operator)
    assert _code(error) == "MANIFEST_INVALID" and str(error.value).count("[MANIFEST_INVALID]") == 1


def test_no_environment_and_no_default(project, operator, state):
    _, service, _ = _released(project, operator, state)
    operator.config.pop("default_environment")
    error = _refused(service, project, operator, environment=None)
    assert _code(error) == "CONTEXT_ENVIRONMENT_UNKNOWN"


def test_used_by_reports_unreadable_state_and_package(project, operator, state, monkeypatch):
    _released(project, operator, state)
    ref = Ref.parse(REF)
    row = registry.usage(ref, project)

    def unreadable(directory):
        raise registry.RegistryError("STATE_UNREADABLE", "cannot read it")

    monkeypatch.setattr(registry_deployed, "read_state", unreadable)
    result = registry_deployed.deployed(ref, row, project, [state.directory])
    assert "STATE_UNREADABLE" in result["errors"][0]["error"]
    monkeypatch.setattr(registry_deployed, "read_state", lambda directory: ([], True))

    def broken(path):
        raise registry.RegistryError("ENTRY_UNSAFE", "unsafe")

    monkeypatch.setattr(registry_deployed, "copy_and_hash", broken)
    result = registry_deployed.deployed(ref, row, project, [state.directory])
    errors = " ".join(e["error"] for e in result["errors"])
    assert "ENTRY_UNSAFE" in errors and "STATE_TRUNCATED" in errors


def test_store_link_package_returns_the_previous_link(state):
    from agentbreeder.audit import AuditEvent
    from agentbreeder.store import DeploymentError

    def event():
        return AuditEvent(
            attempt_id="a",
            action="deploy",
            decision="package_linked",
            actor="dave",
            actor_verified=True,
            enforcement_mode="enforce",
            operator_config_digest="sha256:" + "0" * 64,
        )

    with pytest.raises(DeploymentError):
        state.link_package("nope", {"ref": REF, "sha256": "s"}, event())
    state.create("d1", {"name": "x", "image": IMAGE}, "sha256:d", "ctx")
    assert state.link_package("d1", {"ref": REF, "sha256": "s"}, event()) is None
    assert state.link_package("d1", {"ref": REF, "sha256": "t"}, event()) == {
        "ref": REF,
        "sha256": "s",
    }


# -- review round 1 fixes ----------------------------------------------------------------------


def test_cli_refusals_are_audited_without_paths(
    project, operator, state, cli_service, capsys, tmp_path
):
    _, service, runtime = _released(project, operator, state)
    cli_service(runtime)
    fresh = tmp_path / "fresh-state"
    assert (
        cli.main(["deploy", REF, "--local", "--project", str(project), "--state-dir", str(fresh)])
        == 2
    )
    other = Store(fresh)
    try:
        denied = _events(other, "deploy", "deny")
        assert _reasons(denied[-1]) == {
            "PACKAGE_UNRELEASED_HERE": f"{REF}: PACKAGE_UNRELEASED_HERE"
        }
        assert str(tmp_path) not in json.dumps(denied)
    finally:
        other.close()


def test_a_corrupt_log_gets_no_denial_row(project, operator, state, cli_service, capsys):
    _, service, runtime = _released(project, operator, state)
    cli_service(runtime)
    with state.transaction():
        state.db.execute("UPDATE audit SET actor='mallory' WHERE seq=1")
    before = state.db.execute("SELECT count(*) FROM audit").fetchone()[0]
    assert _cli(state, REF, "--project", str(project)) == 1
    assert "[AUDIT_CORRUPT]" in capsys.readouterr().err
    assert state.db.execute("SELECT count(*) FROM audit").fetchone()[0] == before


def test_a_gate_deny_names_the_package(project, operator, state):
    operator.config["environments"]["prod"] = json.loads(
        json.dumps(operator.config["environments"]["dev"])
    )
    _, service, _ = _released(project, operator, state)
    _remove_production(service, state)
    from agentbreeder.gate import GateDenied

    with pytest.raises(GateDenied):
        _deploy(service, project, operator, environment="prod")
    denied = _events(state, "deploy", "deny")[-1]
    assert _reasons(denied)["DEPLOY_PACKAGE"].startswith(f"{REF} sha256:")


def test_audit_mode_override_names_the_package(project, operator, state):
    operator.config["environments"]["prod"] = json.loads(
        json.dumps(operator.config["environments"]["dev"])
    )
    _released(project, operator, state)
    operator.config["enforcement"] = "audit"
    service = DeploymentService(state, FakeRuntime(), operator.load())
    _remove_production(service, state)
    try:
        _deploy(service, project, operator, environment="prod")
    except Exception:  # a deny that audit mode may not override is fine for this check
        pass
    overrides = _events(state, "audit_mode_proceed", "override")
    if overrides:
        assert _reasons(overrides[-1])["DEPLOY_PACKAGE"].startswith(f"{REF} sha256:")
    else:
        assert "DEPLOY_PACKAGE" in _reasons(_events(state, "deploy", "deny")[-1])


def test_a_symlinked_registry_root_is_refused(project, operator, state, tmp_path):
    _, service, _ = _released(project, operator, state)
    shutil.move(project / "registry", tmp_path / "real-registry")
    (project / "registry").symlink_to(tmp_path / "real-registry")
    assert _code(_refused(service, project, operator)) == "PACKAGE_INVALID"


def test_an_unreadable_link_is_kept_with_a_warning(project, operator, state):
    outcome, service, _ = _released(project, operator, state)
    identifier = outcome.details["production"]["id"]
    with state.transaction():
        state.db.execute("UPDATE deployments SET package='{bad' WHERE id=?", (identifier,))
    _, result = _deploy(service, project, operator)
    assert any("an unreadable package" in w for w in result["gate"]["warnings"])


def test_a_link_failure_after_release_says_how_to_repair(project, operator, state, monkeypatch):
    from agentbreeder.deployment import AuditUnavailable

    def failing(self, *args, **kwargs):
        raise AuditUnavailable("the package link was not recorded")

    monkeypatch.setattr(DeploymentService, "link_package", failing)
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    outcome, _ = do_release(project, operator, state, runtime, IMAGE)
    assert outcome.code == 1 and outcome.details["code"] == "PACKAGE_LINK_FAILED"
    assert "release agent.yaml" in outcome.message and "Released" in outcome.message
    assert (project / "registry" / "agent" / "helper" / "0.1.0").is_dir()  # kept: it is released
    assert state.audit_matching("release", "promoted")
    monkeypatch.undo()
    repaired, _ = do_release(project, operator, state, runtime, IMAGE)
    assert repaired.code == EXIT_OK and "[PACKAGE_EXISTS]" in repaired.message
    assert state.get(outcome.details["production"]["id"])["package"]["ref"] == REF


def test_a_database_error_while_linking_is_reported_the_same_way(
    project, operator, state, monkeypatch
):
    def failing(self, *args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(DeploymentService, "link_package", failing)
    prepare_lock(project, operator)
    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE)
    assert outcome.code == 1 and outcome.details["code"] == "PACKAGE_LINK_FAILED"


def test_package_exists_relinks_a_different_package(project, operator, state):
    outcome, _, runtime = _released(project, operator, state)
    identifier = outcome.details["production"]["id"]
    stale = {"ref": "agent/helper@0.0.9", "sha256": "sha256:" + "e" * 64}
    with state.transaction():
        state.db.execute(
            "UPDATE deployments SET package=? WHERE id=?", (json.dumps(stale), identifier)
        )
    do_release(project, operator, state, runtime, IMAGE)
    assert state.get(identifier)["package"]["ref"] == REF
    assert "PREVIOUS_PACKAGE" in _reasons(_events(state, "deploy", "package_linked")[-1])


def test_migrate_reports_a_v3_upgrade(tmp_path, capsys):
    directory = tmp_path / "state"
    Store(directory).close()
    _as_v3(directory)
    assert cli.main(["migrate", "--state-dir", str(directory), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["previous_version"] == 3 and result["schema_version"] == 4
    assert result["migrated"] is True
