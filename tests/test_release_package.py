"""`release` writes the agent package (#763, M13 slice 2; spec §5.5, §6)."""

# ruff: noqa: F811  (the project/operator fixtures are imported from test_release)

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_release import (  # noqa: F401  (fixtures)
    EXIT_OK,
    Evaluator,
    FakeRuntime,
    do_release,
    operator,
    prepare_lock,
    project,
)

from agentbreeder import package, registry
from agentbreeder import release as release_module
from agentbreeder.build import BuildError, docker_platform
from agentbreeder.deployment import AuditIncomplete, DeploymentService
from agentbreeder.release import EXIT_EVAL, EXIT_RUNTIME, ReleaseError, project_id

IMAGE = "sha256:" + "1" * 64
OTHER = "sha256:" + "2" * 64


def _rows(state, decision):
    return [
        {item["code"]: item["message"] for item in row.get("reasons") or []}
        for row in state.audit_matching("release", decision)
    ]


def _versions(root: Path) -> list[str]:
    directory = root / "registry" / "agent" / "helper"
    return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


class Count(Evaluator):
    def __init__(self, passed=True):
        super().__init__(passed)
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return super().__call__(*args, **kwargs)


# -- the happy path ---------------------------------------------------------------------------


def test_release_publishes_the_package_and_records_it(project, operator, state):
    prepare_lock(project, operator)
    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE)
    assert outcome.code == EXIT_OK, outcome.message
    path = project / "registry" / "agent" / "helper" / "0.1.0"
    record = package.load(path, "helper", "0.1.0")
    built = json.loads((project / "deploy" / "helper.build.json").read_text())
    assert record.image.id == IMAGE and record.image.platform == "linux/arm64"
    assert record.agent.runtime == "runtime/fake@1.0.0"
    assert record.inputs.agent_yaml_sha256 == built["agent_yaml_sha256"]
    assert record.inputs.lock_sha256 == built["lock_sha256"]
    assert record.evals.ref == "eval/golden@1.0.0" and record.evals.environment == "dev"
    assert (record.evals.passed, record.evals.cases, record.evals.run_id) == (2, 2, "run1")
    assert record.evals.threshold == 1.0 and record.evals.retries == 1
    assert record.deploy.aliases == ("support-model",) and record.deploy.port == 8080
    assert (path / "agent.yaml").read_bytes() == (project / "agent.yaml").read_bytes()

    digest = registry.copy_and_hash(path)
    promoted = _rows(state, "promoted")[0]
    assert promoted["RELEASE_PACKAGE"] == "agent/helper@0.1.0"
    assert promoted["RELEASE_PACKAGE_SHA256"] == digest
    assert promoted["RELEASE_PROJECT"] == project_id(project)
    assert str(project) not in json.dumps(promoted)  # only a hash of the path is recorded
    assert _rows(state, "started")[0]["RELEASE_PACKAGE"] == "agent/helper@0.1.0"
    assert outcome.details["package"] == {
        "ref": "agent/helper@0.1.0",
        "sha256": digest,
        "path": "registry/agent/helper/0.1.0",
    }


def test_releasing_the_same_image_again_gives_its_package(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    evaluator = Count()
    again, _ = do_release(project, operator, state, runtime, IMAGE, evaluator, version="2.0.0")
    assert again.code == EXIT_OK and evaluator.calls == 0
    assert "already in production" in again.message and "[PACKAGE_EXISTS]" in again.message
    assert (
        "agent/helper@0.1.0" in again.message and "--version 2.0.0 was not used" in again.message
    )
    assert again.details["package"]["ref"] == "agent/helper@0.1.0"
    assert _versions(project) == ["0.1.0"]


def test_a_new_image_gets_the_next_patch(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    outcome, _ = do_release(project, operator, state, runtime, OTHER)
    assert outcome.code == EXIT_OK and outcome.details["package"]["ref"] == "agent/helper@0.1.1"
    assert _versions(project) == ["0.1.0", "0.1.1"]


# -- versions (spec §5.5) ---------------------------------------------------------------------


def test_a_lower_free_version_is_accepted(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE, version="2.0.0")
    outcome, _ = do_release(project, operator, state, runtime, OTHER, version="1.5.0")
    assert outcome.code == EXIT_OK and _versions(project) == ["1.5.0", "2.0.0"]


def test_a_taken_version_is_refused_before_any_eval(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    evaluator = Count()
    with pytest.raises(ReleaseError, match="VERSION_EXISTS") as error:
        do_release(project, operator, state, runtime, OTHER, evaluator, version="0.1.0")
    assert evaluator.calls == 0
    assert "never released" not in str(error.value)
    assert "--version 0.1.1" in str(error.value)  # the Next: line names the next free version


def test_a_deleted_directory_with_a_started_row_stays_taken(project, operator, state):
    import shutil

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    shutil.rmtree(project / "registry" / "agent")
    with pytest.raises(ReleaseError, match="VERSION_EXISTS"):
        do_release(project, operator, state, runtime, OTHER, version="0.1.0")
    outcome, _ = do_release(project, operator, state, runtime, OTHER)
    assert outcome.details["package"]["ref"] == "agent/helper@0.1.1"


def test_a_failed_release_reserves_its_version(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    failed, _ = do_release(project, operator, state, runtime, IMAGE, Count(passed=False))
    assert (
        failed.code == EXIT_EVAL and _versions(project) == [] and "package" not in failed.details
    )
    assert all("RELEASE_PACKAGE" not in row for row in _rows(state, "eval_failed"))
    outcome, _ = do_release(project, operator, state, runtime, OTHER)
    assert outcome.details["package"]["ref"] == "agent/helper@0.1.1"


def test_an_interrupted_release_directory_is_named_as_never_released(project, operator, state):
    """A crash between publish and the release row leaves a directory nothing released."""
    prepare_lock(project, operator)
    stray = project / "registry" / "agent" / "helper" / "0.3.0"
    stray.mkdir(parents=True)
    with pytest.raises(ReleaseError, match="never released") as error:
        do_release(project, operator, state, FakeRuntime(), IMAGE, version="0.3.0")
    assert "registry/agent/helper/0.3.0" in str(error.value)
    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE)
    assert outcome.details["package"]["ref"] == "agent/helper@0.3.1"  # never adopted


@pytest.mark.parametrize("value", ["1.0", "v1.0.0", "01.0.0", "1.0.0-rc1", "../1.0.0"])
def test_an_invalid_version_is_refused(project, operator, state, value):
    prepare_lock(project, operator)
    with pytest.raises(ReleaseError, match="VERSION_INVALID"):
        do_release(project, operator, state, FakeRuntime(), IMAGE, version=value)


def test_retries_outside_the_package_range_are_refused(project, operator, state):
    prepare_lock(project, operator)
    with pytest.raises(ReleaseError, match="RETRIES_INVALID"):
        do_release(project, operator, state, FakeRuntime(), IMAGE, retries=4)


# -- failures and cleanup (spec §6 steps 2.1, 2.5, 4) ----------------------------------------


def test_a_failed_production_deploy_removes_the_package(project, operator, state, monkeypatch):
    from agentbreeder.store import DeploymentError

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    original = DeploymentService.deploy

    def deploy(self, manifest, **kwargs):
        if manifest.name == "helper" and manifest.image == OTHER:
            raise DeploymentError("gateway key not minted")
        return original(self, manifest, **kwargs)

    monkeypatch.setattr(DeploymentService, "deploy", deploy)
    outcome, _ = do_release(project, operator, state, runtime, OTHER)
    assert outcome.code == EXIT_RUNTIME and "package" not in outcome.details
    assert _versions(project) == ["0.1.0"]
    assert all("RELEASE_PACKAGE" not in row for row in _rows(state, "failed"))
    monkeypatch.setattr(DeploymentService, "deploy", original)
    outcome, _ = do_release(project, operator, state, runtime, OTHER)
    assert outcome.details["package"]["ref"] == "agent/helper@0.1.2"  # 0.1.1 stays reserved


def test_an_interrupt_before_the_release_row_removes_the_package(
    project, operator, state, monkeypatch
):
    prepare_lock(project, operator)
    original = DeploymentService.deploy

    def deploy(self, manifest, **kwargs):
        if manifest.name == "helper":
            raise KeyboardInterrupt
        return original(self, manifest, **kwargs)

    monkeypatch.setattr(DeploymentService, "deploy", deploy)
    with pytest.raises(KeyboardInterrupt):
        do_release(project, operator, state, FakeRuntime(), IMAGE)
    assert _versions(project) == []


def test_cleanup_keeps_a_directory_that_changed_after_publish(
    project, operator, state, monkeypatch
):
    from agentbreeder.store import DeploymentError

    prepare_lock(project, operator)
    original = DeploymentService.deploy

    def deploy(self, manifest, **kwargs):
        if manifest.name == "helper":
            target = project / "registry" / "agent" / "helper" / "0.1.0" / "agent.yaml"
            target.write_text(target.read_text() + "# edited\n")
            raise DeploymentError("boom")
        return original(self, manifest, **kwargs)

    monkeypatch.setattr(DeploymentService, "deploy", deploy)
    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE)
    assert outcome.code == EXIT_RUNTIME and _versions(project) == ["0.1.0"]


def test_an_unconfirmed_audit_keeps_the_package(project, operator, state, monkeypatch):
    prepare_lock(project, operator)
    original = DeploymentService.deploy

    def deploy(self, manifest, **kwargs):
        if manifest.name == "helper":
            raise AuditIncomplete()
        return original(self, manifest, **kwargs)

    monkeypatch.setattr(DeploymentService, "deploy", deploy)
    with pytest.raises(AuditIncomplete) as error:
        do_release(project, operator, state, FakeRuntime(), IMAGE)
    text = str(error.value)
    assert text.startswith("[AUDIT_WRITE_FAILED]") and "[PACKAGE_KEPT_UNVERIFIED]" in text
    assert "agent/helper@0.1.0" in text and "audit verify" in text
    assert _versions(project) == ["0.1.0"]


@pytest.mark.parametrize("edited", ["agent.yaml", "agent.lock"])
def test_inputs_edited_during_the_evals_publish_nothing(project, operator, state, edited):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)

    class Edit(Evaluator):
        def __call__(self, *args, **kwargs):
            target = project / edited
            target.write_bytes(target.read_bytes() + b"\n")
            return super().__call__(*args, **kwargs)

    outcome, _ = do_release(project, operator, state, runtime, OTHER, Edit())
    assert outcome.code == EXIT_RUNTIME and "[PACKAGE_INPUTS_CHANGED]" in outcome.message
    assert f"{edited} changed while the evals ran" in outcome.message
    assert _versions(project) == ["0.1.0"]
    assert state.manifest(state.active("helper")["id"])["image"] == IMAGE  # production unchanged


def test_a_publish_digest_mismatch_removes_the_copy(project, operator, state, monkeypatch):
    prepare_lock(project, operator)
    original = registry.publish

    def publish(source, root, *, package=False):
        ref, digest, path = original(source, root, package=package)
        return ref, "sha256:" + "0" * 64, path

    monkeypatch.setattr(registry, "publish", publish)
    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE)
    assert outcome.code == EXIT_RUNTIME and "[PACKAGE_PUBLISH_MISMATCH]" in outcome.message
    assert _versions(project) == [] and state.active("helper") is None


# -- releases from before M13, and a package gone from git (spec §5.5 step 1, §6) -------------


def _strip_package_reasons(state):
    """Rewrite this state's release rows as a pre-M13 CLI wrote them (test-only, via SQL; the
    hash chain no longer verifies, which release does not check)."""
    for row in state.db.execute("SELECT seq, reasons FROM audit WHERE action='release'"):
        reasons = [
            r for r in json.loads(row["reasons"]) if not r["code"].startswith("RELEASE_PAC")
        ]
        reasons = [r for r in reasons if r["code"] != "RELEASE_PROJECT"]
        state.db.execute(
            "UPDATE audit SET reasons=? WHERE seq=?", (json.dumps(reasons), row["seq"])
        )
    state.db.commit()


def test_a_pre_m13_release_row_is_released_again_without_replacing(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    _strip_package_reasons(state)
    production = state.active("helper")["id"]
    outcome, _ = do_release(project, operator, state, runtime, IMAGE)
    assert outcome.code == EXIT_OK and "was not replaced" in outcome.message
    assert "NO_RELEASE_EVIDENCE" in outcome.details["warnings"][0]
    assert state.active("helper")["id"] == production
    assert outcome.details["package"]["ref"] == "agent/helper@0.1.1"
    assert _rows(state, "promoted")[0]["RELEASE_PACKAGE"] == "agent/helper@0.1.1"


def test_a_released_package_removed_from_the_project_is_released_again(project, operator, state):
    import shutil

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    shutil.rmtree(project / "registry" / "agent")  # a branch switch or `git clean`
    outcome, _ = do_release(project, operator, state, runtime, IMAGE)
    assert outcome.code == EXIT_OK and "PACKAGE_MISSING" in outcome.details["warnings"][0]
    assert outcome.details["package"]["ref"] == "agent/helper@0.1.1"


def test_a_release_from_another_project_is_not_reused(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    row = state.db.execute(
        "SELECT seq, reasons FROM audit WHERE action='release' AND decision='promoted'"
    ).fetchone()
    reasons = [
        {**r, "message": "sha256:" + "9" * 64} if r["code"] == "RELEASE_PROJECT" else r
        for r in json.loads(row["reasons"])
    ]
    state.db.execute("UPDATE audit SET reasons=? WHERE seq=?", (json.dumps(reasons), row["seq"]))
    state.db.commit()
    outcome, _ = do_release(project, operator, state, runtime, IMAGE)
    assert "PACKAGE_MISSING" in outcome.details["warnings"][0]


# -- the platform and the CLI -----------------------------------------------------------------


def test_docker_platform_reads_and_checks_the_image(monkeypatch):
    from agentbreeder import runtime

    answers = iter(["linux/arm64/v8\n", "Linux/../x\n"])
    monkeypatch.setattr(runtime.Docker, "authorize", lambda self, context=None: None)
    calls = []

    def command(self, *arguments, **kwargs):
        calls.append(arguments)
        return next(answers)

    monkeypatch.setattr(runtime.Docker, "command", command)
    assert docker_platform(IMAGE) == "linux/arm64/v8"
    assert calls[0][:2] == ("image", "inspect") and calls[0][-1] == IMAGE
    with pytest.raises(BuildError, match="BUILD_FAILED"):
        docker_platform(IMAGE)


def test_release_uses_the_platform_it_is_given(project, operator, state):
    prepare_lock(project, operator)
    outcome, _ = do_release(
        project, operator, state, FakeRuntime(), IMAGE, platform=lambda image: "linux/amd64"
    )
    record = package.load(project / "registry/agent/helper/0.1.0", "helper", "0.1.0")
    assert outcome.code == EXIT_OK and record.image.platform == "linux/amd64"


def test_cli_release_version_and_package_output(project, operator, state, monkeypatch, capsys):
    from agentbreeder import cli

    prepare_lock(project, operator)
    seen = {}

    def fake_release(*args, **kwargs):
        seen.update(kwargs)
        return release_module.Outcome(
            EXIT_OK,
            "Released x",
            {
                "agent": "helper",
                "package": {"ref": "agent/helper@1.2.3", "sha256": "sha256:ab", "path": "p"},
            },
        )

    monkeypatch.setattr(release_module, "release", fake_release)
    monkeypatch.setattr("agentbreeder.commands.release.Docker", lambda: object())
    code = cli.main(
        [
            "release",
            str(project / "agent.yaml"),
            "--version",
            "1.2.3",
            "--state-dir",
            str(state.directory),
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK and seen["version"] == "1.2.3"
    assert "Package: agent/helper@1.2.3 (p, sha256:ab)" in out


# -- review round 1 additions -----------------------------------------------------------------


class SwapProduction(Evaluator):
    """A plain `deploy` replaces production while the evals run."""

    def __init__(self, project, operator, state, runtime):
        super().__init__()
        self.args = (project, operator, state, runtime)

    def __call__(self, *args, **kwargs):
        from conftest import actor

        from agentbreeder.manifest import load_manifest

        project, operator, state, runtime = self.args
        service = DeploymentService(state, runtime, operator.load())
        current = state.active("helper")
        if current:
            service.remove(current["id"], local=True, actor=actor("dave"))
        manifest = load_manifest(project / "deploy" / "helper.deploy.yaml")
        service.deploy(manifest.model_copy(update={"image": "sha256:" + "5" * 64}), local=True,
                       environment="dev", deployer=actor("dave"))  # fmt: skip
        return super().__call__(*args, **kwargs)


def test_production_changed_publishes_nothing_and_writes_no_journal(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    with pytest.raises(ReleaseError, match="PRODUCTION_CHANGED"):
        do_release(
            project,
            operator,
            state,
            runtime,
            IMAGE,
            SwapProduction(project, operator, state, runtime),
        )
    assert _versions(project) == []
    assert not (state.directory / "releases" / "helper.json").exists()


def test_production_changed_on_the_unreleased_path_publishes_nothing(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    _strip_package_reasons(state)  # production now runs IMAGE without packaged evidence
    with pytest.raises(ReleaseError, match="PRODUCTION_CHANGED"):
        do_release(
            project,
            operator,
            state,
            runtime,
            IMAGE,
            SwapProduction(project, operator, state, runtime),
        )
    assert _versions(project) == ["0.1.0"]  # only the first release's package


def test_a_publish_failure_leaves_no_journal(project, operator, state):
    prepare_lock(project, operator)

    class Edit(Evaluator):
        def __call__(self, *args, **kwargs):
            (project / "agent.yaml").write_text((project / "agent.yaml").read_text() + "\n")
            return super().__call__(*args, **kwargs)

    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE, Edit())
    assert outcome.code == EXIT_RUNTIME and outcome.details["code"] == "PACKAGE_INPUTS_CHANGED"
    assert "Next: `agentbreeder release" in outcome.message
    assert not (state.directory / "releases" / "helper.json").exists()
    assert _rows(state, "failed")[0]["PACKAGE_INPUTS_CHANGED"].startswith("[PACKAGE_INPUTS")


def test_unreleased_path_inputs_changed_publishes_nothing(project, operator, state):
    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    _strip_package_reasons(state)

    class Edit(Evaluator):
        def __call__(self, *args, **kwargs):
            (project / "agent.lock").write_bytes((project / "agent.lock").read_bytes() + b"\n")
            return super().__call__(*args, **kwargs)

    outcome, _ = do_release(project, operator, state, runtime, IMAGE, Edit())
    assert outcome.code == EXIT_RUNTIME and "[PACKAGE_INPUTS_CHANGED]" in outcome.message
    assert _versions(project) == ["0.1.0"]


def test_unreleased_path_unconfirmed_audit_keeps_the_package(
    project, operator, state, monkeypatch
):
    from agentbreeder.deployment import AuditUnavailable

    prepare_lock(project, operator)
    runtime = FakeRuntime()
    do_release(project, operator, state, runtime, IMAGE)
    _strip_package_reasons(state)
    original = DeploymentService.record

    def record(self, action, decision, actor, reasons):
        if (action, decision) == ("release", "promoted"):
            raise AuditUnavailable("the release was not recorded")
        return original(self, action, decision, actor, reasons)

    monkeypatch.setattr(DeploymentService, "record", record)
    with pytest.raises(AuditUnavailable, match="PACKAGE_KEPT_UNVERIFIED") as error:
        do_release(project, operator, state, runtime, IMAGE)
    assert error.value.package_kept is True
    assert _versions(project) == ["0.1.0", "0.1.1"]


def test_evidence_require_package_skips_pre_m13_rows_that_promote_accepts(
    project, operator, state
):
    from agentbreeder import evidence
    from agentbreeder.agentfile import load_agent_file

    prepare_lock(project, operator)
    do_release(project, operator, state, FakeRuntime(), IMAGE)
    _strip_package_reasons(state)
    agent = load_agent_file(project / "agent.yaml")
    current = state.active("helper")
    # promote's call: no flag, the old row still counts
    assert evidence.released(state, agent, project, current, IMAGE)["RELEASE_IMAGE"] == IMAGE
    with pytest.raises(ReleaseError, match="NO_RELEASE_EVIDENCE"):
        evidence.released(state, agent, project, current, IMAGE, require_package=True)


def test_malformed_started_rows_reserve_nothing(project, operator, state):
    from conftest import actor

    prepare_lock(project, operator)
    service = DeploymentService(state, FakeRuntime(), operator.load())
    for message in ("agent/helper@../1.0.0", "agent/other@0.1.0", "agent/helper@01.0.0", 7):
        service.record("release", "started", actor("dave"),
                       [{"code": "RELEASE_PACKAGE", "message": message}])  # fmt: skip
    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE)
    assert outcome.details["package"]["ref"] == "agent/helper@0.1.0"


def test_a_symlinked_package_directory_is_refused_before_any_eval(
    project, operator, state, tmp_path
):
    prepare_lock(project, operator)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (project / "registry" / "agent").symlink_to(elsewhere)
    evaluator = Count()
    with pytest.raises(ReleaseError, match="ENTRY_UNSAFE"):
        do_release(project, operator, state, FakeRuntime(), IMAGE, evaluator)
    assert evaluator.calls == 0 and list(elsewhere.iterdir()) == []


def test_an_invalid_version_is_refused_before_the_build(project, operator, state, monkeypatch):
    calls = []
    monkeypatch.setattr(release_module, "build", lambda *a, **k: calls.append(1))
    with pytest.raises(ReleaseError, match="VERSION_INVALID"):
        do_release(project, operator, state, FakeRuntime(), IMAGE, version="x")
    assert calls == []


def test_version_with_rollback_is_refused(project, state, capsys):
    assert _cli(project, state, "--rollback", "--version", "1.0.0") == 2
    assert "[VERSION_INVALID]" in capsys.readouterr().err


def _cli(project, state, *extra):
    from agentbreeder import cli

    return cli.main(
        ["release", str(project / "agent.yaml"), "--state-dir", str(state.directory), *extra]
    )


def test_cli_exit_codes_for_package_errors(project, operator, state, monkeypatch, capsys):
    from agentbreeder.deployment import AuditUnavailable

    def raising(error):
        def fake(*args, **kwargs):
            raise error

        return fake

    monkeypatch.setattr("agentbreeder.commands.release.Docker", lambda: object())
    monkeypatch.setattr(
        release_module, "release", raising(ReleaseError("VERSION_EXISTS", "taken"))
    )
    assert _cli(project, state) == 2
    kept = AuditUnavailable()
    kept.package_kept = True
    monkeypatch.setattr(release_module, "release", raising(kept))
    assert _cli(project, state) == 1
    monkeypatch.setattr(
        release_module,
        "release",
        lambda *a, **k: release_module.Outcome(
            EXIT_RUNTIME, "[PACKAGE_INPUTS_CHANGED] x", {"code": "PACKAGE_INPUTS_CHANGED"}
        ),
    )
    capsys.readouterr()
    assert _cli(project, state, "--json") == 1
    assert json.loads(capsys.readouterr().out)["code"] == "PACKAGE_INPUTS_CHANGED"


def test_cli_json_carries_the_package(project, operator, state, monkeypatch, capsys):
    package_info = {"ref": "agent/helper@0.1.0", "sha256": "sha256:ab", "path": "p"}
    monkeypatch.setattr("agentbreeder.commands.release.Docker", lambda: object())
    monkeypatch.setattr(
        release_module,
        "release",
        lambda *a, **k: release_module.Outcome(
            EXIT_OK, "Released x", {"agent": "helper", "package": package_info}
        ),
    )
    assert _cli(project, state, "--json") == EXIT_OK
    assert json.loads(capsys.readouterr().out)["package"] == package_info
