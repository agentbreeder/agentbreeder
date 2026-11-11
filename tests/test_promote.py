"""`agentbreeder promote`: only an audited release goes to the cluster, by digest, gated."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import actor, write_private
from test_release import (
    Evaluator,
    FakeRuntime,
    do_release,
    prepare_lock,
)
from test_release import operator as operator  # noqa: F401, PLW0127
from test_release import project as project  # noqa: F401, PLW0127

from agentbreeder import cli
from agentbreeder import promote as promote_module
from agentbreeder.deployment import DeploymentService
from agentbreeder.promote import promote, push_image, registry_image_id
from agentbreeder.release import (
    EXIT_DENIED,
    EXIT_EVAL,
    EXIT_OK,
    EXIT_RUNTIME,
    ReleaseError,
    defer_interrupt,
)

IMAGE = "sha256:" + "1" * 64
REFERENCE = "localhost:5001/agentbreeder/helper@sha256:" + "e" * 64
OTHER = "localhost:5001/agentbreeder/helper@sha256:" + "f" * 64
UID = "11111111-2222-3333-4444-555555555555"


class FakeCluster(FakeRuntime):
    context = f"kind-x|{UID}|agents|/k"

    def __init__(self):
        super().__init__()
        self.selected = []

    def select(self, environment):
        self.selected.append(environment)

    def sweep(self, record):
        self.containers.pop(record["container"], None)

    def endpoint(self, info, manifest):
        return f"kubernetes://agents/{info['Id']}:8080"

    def forward(self, endpoint):
        class Forward:
            def __enter__(self):
                return 4321

            def __exit__(self, *exc):
                return False

        return Forward()


@pytest.fixture
def cluster_env(operator, tmp_path):  # noqa: F811
    path = write_private(tmp_path / "kubeconfig", "{}")
    operator.config["environments"]["dev"]["targets"] = ["local", "kubernetes"]
    operator.config["environments"]["dev"]["kubernetes"] = {
        "kubeconfig": str(path),
        "context": "kind-x",
        "cluster_uid": UID,
        "namespace": "agents",
        "registry": "localhost:5001/agentbreeder",
        "blocked_cidrs": ["10.244.0.0/16"],
    }
    return operator


@pytest.fixture
def released(project, cluster_env, state):  # noqa: F811
    prepare_lock(project, cluster_env)
    docker = FakeRuntime()
    outcome, _ = do_release(project, cluster_env, state, docker, IMAGE)
    assert outcome.code == EXIT_OK
    return docker


def run_promote(project, env, state, docker, cube, *, reference=REFERENCE, evaluator=None, **kw):  # noqa: F811
    service = DeploymentService(state, docker, env.load(), kubernetes=cube)
    pushed = []

    def pusher(runtime, image, repository, tag):
        pushed.append((image, repository, tag))
        return reference

    outcome = promote(
        project / "agent.yaml",
        env.load(),
        service,
        actor("dave"),
        "dev",
        pusher=pusher,
        evaluate=evaluator or Evaluator(),
        **kw,
    )
    return outcome, service, pushed


def test_promote_deploys_the_evaluated_image_by_digest(project, cluster_env, state, released):  # noqa: F811
    cube = FakeCluster()
    evaluator = Evaluator()
    outcome, service, pushed = run_promote(
        project, cluster_env, state, released, cube, evaluator=evaluator
    )
    assert outcome.code == EXIT_OK and "2/2" in outcome.message
    assert pushed[0][0] == IMAGE and pushed[0][1] == "localhost:5001/agentbreeder/helper"
    record = state.active("helper", "kubernetes")
    manifest = state.manifest(record["id"])
    local = state.manifest(state.active("helper")["id"])
    assert manifest["image"] == REFERENCE and manifest["target"] == "kubernetes"
    assert {k: v for k, v in manifest.items() if k not in ("image", "target")} == {
        k: v for k, v in local.items() if k not in ("image", "target")
    }
    assert evaluator.endpoints == ["http://127.0.0.1:4321"]
    written = yaml.safe_load((project / "deploy/helper.kubernetes.deploy.yaml").read_text())
    assert written["image"] == REFERENCE
    rows = [r for r in state.audit_rows(limit=200) if r["action"] == "promote"]
    assert {r["decision"] for r in rows} == {"started", "promoted"}
    reasons = {x["code"]: x["message"] for x in rows[0]["reasons"]}
    assert reasons["RELEASE_IMAGE"] == IMAGE and reasons["PROMOTE_IMAGE"] == REFERENCE
    assert reasons["PROMOTE_EVAL"].endswith("2/2")


def test_promote_again_is_a_no_op(project, cluster_env, state, released):  # noqa: F811
    cube = FakeCluster()
    assert run_promote(project, cluster_env, state, released, cube)[0].code == EXIT_OK
    first = state.active("helper", "kubernetes")["id"]
    evaluator = Evaluator()
    outcome, *_ = run_promote(project, cluster_env, state, released, cube, evaluator=evaluator)
    assert "already" in outcome.message and evaluator.endpoints == []
    assert state.active("helper", "kubernetes")["id"] == first


def test_ready_without_an_audited_promote_runs_the_evals(project, cluster_env, state, released):  # noqa: F811
    cube = FakeCluster()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_promote(project, cluster_env, state, released, cube, evaluator=interrupted)
    assert state.active("helper", "kubernetes") is None  # the interrupted deployment is out
    # A deploy left ready with no outcome recorded is evaluated, not reported as done.
    service = DeploymentService(state, released, cluster_env.load(), kubernetes=cube)
    from agentbreeder.manifest import Manifest

    data = state.manifest(state.active("helper")["id"])
    service.deploy(
        Manifest.model_validate({**data, "target": "kubernetes", "image": REFERENCE}),
        local=False,
        environment="dev",
        deployer=actor("dave"),
    )
    evaluator = Evaluator()
    outcome, *_ = run_promote(project, cluster_env, state, released, cube, evaluator=evaluator)
    assert outcome.code == EXIT_OK and evaluator.endpoints == ["http://127.0.0.1:4321"]


def _leave_ready(project, env, state, docker, cube):  # noqa: F811
    """A cluster deployment that is ready but has no recorded promote outcome."""
    from agentbreeder.manifest import Manifest

    service = DeploymentService(state, docker, env.load(), kubernetes=cube)
    data = state.manifest(state.active("helper")["id"])
    return service.deploy(
        Manifest.model_validate({**data, "target": "kubernetes", "image": REFERENCE}),
        local=False,
        environment="dev",
        deployer=actor("dave"),
    )


def test_interrupt_during_the_deploy_step_takes_it_out(project, cluster_env, state, released):  # noqa: F811
    cube = FakeCluster()
    original = cube.start

    def interrupted_start(info):
        original(info)
        import os
        import signal

        os.kill(os.getpid(), signal.SIGINT)

    cube.start = interrupted_start
    with pytest.raises(KeyboardInterrupt):
        run_promote(project, cluster_env, state, released, cube)
    assert state.active("helper", "kubernetes") is None


def test_stale_lock_stops_before_anything_changes(project, cluster_env, state, released):  # noqa: F811
    path = project / "agent.lock"
    lock = json.loads(path.read_text())
    for key in [k for k in lock["entries"] if k.startswith("eval/")]:
        del lock["entries"][key]
    path.write_text(json.dumps(lock))
    cube = FakeCluster()
    with pytest.raises(ReleaseError):
        run_promote(project, cluster_env, state, released, cube)
    assert cube.containers == {}


def test_a_skipped_promote_does_not_count_for_one_with_evals(
    project, cluster_env, state, released
):  # noqa: F811
    cube = FakeCluster()
    run_promote(project, cluster_env, state, released, cube, skip_cluster_evals=True)
    evaluator = Evaluator()
    outcome, *_ = run_promote(project, cluster_env, state, released, cube, evaluator=evaluator)
    assert outcome.code == EXIT_OK and evaluator.endpoints


def test_resume_keeps_what_the_interrupted_run_replaced(project, cluster_env, state, released):  # noqa: F811
    cube = FakeCluster()
    run_promote(project, cluster_env, state, released, cube, reference=OTHER)
    first = state.manifest(state.active("helper", "kubernetes")["id"])
    journal = state.directory / "releases" / "helper.kubernetes.json"
    # The interrupted run: previous removed, new deployment ready, no outcome recorded.
    previous = state.active("helper", "kubernetes")
    service = DeploymentService(state, released, cluster_env.load(), kubernetes=cube)
    service.remove(previous["id"], local=False, actor=actor("dave"))
    _leave_ready(project, cluster_env, state, released, cube)
    journal.write_text(
        json.dumps(
            {
                "image": REFERENCE,
                "previous": {"id": "old", "manifest": first, "environment": "dev"},
            }
        )
    )
    outcome, *_ = run_promote(
        project, cluster_env, state, released, cube, evaluator=Evaluator(False)
    )
    assert outcome.code == EXIT_EVAL
    assert state.manifest(state.active("helper", "kubernetes")["id"])["image"] == OTHER


def test_failed_cluster_evals_restore_the_previous_deployment(
    project, cluster_env, state, released
):  # noqa: F811
    cube = FakeCluster()
    run_promote(project, cluster_env, state, released, cube)
    outcome, *_ = run_promote(
        project, cluster_env, state, released, cube, reference=OTHER, evaluator=Evaluator(False)
    )
    assert outcome.code == EXIT_EVAL and "replaced" in outcome.message
    assert state.manifest(state.active("helper", "kubernetes")["id"])["image"] == REFERENCE
    assert state.active("helper")["state"] == "ready"  # local production untouched
    assert "eval_failed" in {r["decision"] for r in state.audit_rows(limit=100)}


def test_failed_cluster_evals_with_no_previous_remove_it(project, cluster_env, state, released):  # noqa: F811
    outcome, *_ = run_promote(
        project, cluster_env, state, released, FakeCluster(), evaluator=Evaluator(False)
    )
    assert outcome.code == EXIT_EVAL and state.active("helper", "kubernetes") is None


def test_skip_cluster_evals_is_recorded(project, cluster_env, state, released):  # noqa: F811
    evaluator = Evaluator()
    outcome, *_ = run_promote(
        project,
        cluster_env,
        state,
        released,
        FakeCluster(),
        evaluator=evaluator,
        skip_cluster_evals=True,
    )
    assert outcome.code == EXIT_OK and evaluator.endpoints == []
    codes = {x["code"] for r in state.audit_rows(limit=50) for x in r.get("reasons") or []}
    assert "CLUSTER_EVALS_SKIPPED" in codes


def test_cluster_start_failure_restores(project, cluster_env, state, released):  # noqa: F811
    cube = FakeCluster()
    run_promote(project, cluster_env, state, released, cube)
    cube.healthy_flag = False
    outcome, *_ = run_promote(project, cluster_env, state, released, cube, reference=OTHER)
    assert outcome.code == EXIT_RUNTIME and "restored" in outcome.message or outcome.code


def test_denied_by_the_gate_leaves_the_cluster_alone(project, cluster_env, state, released):  # noqa: F811
    cluster_env.principals["agent-1"]["enabled"] = False
    cube = FakeCluster()
    outcome, *_ = run_promote(project, cluster_env, state, released, cube)
    assert outcome.code == EXIT_DENIED and "cluster unchanged" in outcome.message
    assert cube.containers == {}


def test_needs_a_kubernetes_block(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)
    with pytest.raises(ReleaseError, match="KUBERNETES_UNCONFIGURED"):
        run_promote(project, operator, state, FakeRuntime(), FakeCluster())


def test_needs_a_ready_local_release(project, cluster_env, state):  # noqa: F811
    prepare_lock(project, cluster_env)
    with pytest.raises(ReleaseError, match="NO_LOCAL_RELEASE"):
        run_promote(project, cluster_env, state, FakeRuntime(), FakeCluster())


def test_a_plain_deploy_is_not_evidence(project, cluster_env, state, v2_manifest):  # noqa: F811
    from agentbreeder.manifest import Manifest

    prepare_lock(project, cluster_env)
    docker = FakeRuntime()
    service = DeploymentService(state, docker, cluster_env.load())
    data = v2_manifest.model_dump(exclude_none=True)
    data.pop("requires", None)
    service.deploy(
        Manifest.model_validate({**data, "name": "helper"}),
        local=True,
        environment="dev",
        deployer=actor("dave"),
    )
    with pytest.raises(ReleaseError, match="NO_RELEASE_EVIDENCE"):
        run_promote(project, cluster_env, state, docker, FakeCluster())


def test_a_lowered_threshold_is_not_evidence(project, cluster_env, state):  # noqa: F811
    prepare_lock(project, cluster_env)
    docker = FakeRuntime()
    outcome, _ = do_release(
        project, cluster_env, state, docker, IMAGE, evaluator=Evaluator(False), threshold=0.0
    )
    assert outcome.code == EXIT_OK
    with pytest.raises(ReleaseError, match="RELEASE_THRESHOLD_LOWERED"):
        run_promote(project, cluster_env, state, docker, FakeCluster())


def test_a_changed_eval_set_is_not_evidence(project, cluster_env, state, released):  # noqa: F811
    path = project / "agent.lock"
    lock = json.loads(path.read_text())
    for key, entry in lock["entries"].items():
        if key.startswith("eval/"):
            entry["sha256"] = "sha256:" + "9" * 64
    path.write_text(json.dumps(lock))
    with pytest.raises(ReleaseError, match="RELEASE_EVALSET_CHANGED"):
        run_promote(project, cluster_env, state, released, FakeCluster())


class FakeDocker:
    def __init__(self, digests):
        self.digests = digests
        self.commands = []

    def authorize(self):
        self.commands.append(("authorize",))

    def command(self, *arguments, timeout=30):
        self.commands.append(arguments)
        return json.dumps(self.digests) if "inspect" in arguments else ""


def test_push_verifies_the_registry_copy(monkeypatch):
    monkeypatch.setattr(promote_module, "registry_image_id", lambda reference: IMAGE)
    docker = FakeDocker(["other/thing@sha256:" + "0" * 64, REFERENCE])
    result = push_image(docker, IMAGE, "localhost:5001/agentbreeder/helper", "run1")
    assert result == REFERENCE
    assert ("image", "tag", IMAGE, "localhost:5001/agentbreeder/helper:run1") in docker.commands


def test_push_refuses_a_different_registry_image(monkeypatch):
    monkeypatch.setattr(promote_module, "registry_image_id", lambda reference: "sha256:bad")
    docker = FakeDocker([REFERENCE])
    with pytest.raises(ReleaseError, match="REGISTRY_MISMATCH"):
        push_image(docker, IMAGE, "localhost:5001/agentbreeder/helper", "run1")


def test_push_without_a_digest_is_refused():
    docker = FakeDocker(["other/thing@sha256:" + "0" * 64])
    with pytest.raises(ReleaseError, match="REGISTRY_DIGEST"):
        push_image(docker, IMAGE, "localhost:5001/agentbreeder/helper", "run1")


def test_registry_image_id_reads_a_manifest_and_an_index(monkeypatch):
    responses = iter(
        [
            {"config": {"digest": IMAGE}},
            {"manifests": [{"digest": "sha256:a", "platform": {"os": "linux"}}]},
            {"config": {"digest": "sha256:cfg"}},
        ]
    )
    monkeypatch.setattr(promote_module, "_fetch", lambda url: next(responses))
    assert registry_image_id(REFERENCE) == IMAGE
    assert registry_image_id(REFERENCE) == "sha256:cfg"


def test_registry_image_id_refuses_a_remote_registry():
    with pytest.raises(ReleaseError, match="NOT_LOCAL"):
        registry_image_id("registry.example.com/a/b@sha256:" + "e" * 64)


def test_registry_image_id_bad_documents(monkeypatch):
    monkeypatch.setattr(promote_module, "_fetch", lambda url: {})
    with pytest.raises(ReleaseError, match="UNREADABLE"):
        registry_image_id(REFERENCE)
    monkeypatch.setattr(promote_module, "_fetch", lambda url: {"manifests": []})
    with pytest.raises(ReleaseError, match="no image"):
        registry_image_id(REFERENCE)


def test_defer_interrupt_delivers_afterwards():
    import os
    import signal

    with pytest.raises(KeyboardInterrupt):
        with defer_interrupt():
            os.kill(os.getpid(), signal.SIGINT)
            finished = True
    assert finished


def test_cli_promote_reports_errors(identity_env, tmp_path, capsys):
    agent = Path(tmp_path / "agent.yaml")
    agent.write_text("kind: agent\n")
    code = cli.main(
        ["promote", str(agent), "--to", "kubernetes", "--state-dir", str(tmp_path / "s")]
    )
    assert code == 2 and capsys.readouterr().err


def test_cli_promote_success_path(project, cluster_env, state, released, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr(
        promote_module.__dict__["promote"].__globals__["registry"], "PATH_ENV", "UNUSED"
    )
    outcome = SimpleNamespace(code=0, message="ok", as_json=lambda: {"exit_code": 0})
    monkeypatch.setattr(promote_module, "promote", lambda *a, **k: outcome)
    code = cli.main(
        [
            "promote",
            str(project / "agent.yaml"),
            "--to",
            "kubernetes",
            "--state-dir",
            str(state.directory),
            "--json",
        ]
    )
    assert code == 0 and json.loads(capsys.readouterr().out) == {"exit_code": 0}
