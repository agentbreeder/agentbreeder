"""`agentbreeder logs ID` for a Kubernetes (local kind) deployment (M5, guided §14.3 #1)."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from conftest import v2_manifest_data

from agentbreeder import cli, redaction
from agentbreeder.commands import logs as logs_command
from agentbreeder.kubernetes import DEPLOYMENT_LABEL, Kubernetes
from agentbreeder.store import DeploymentError, Store

DIGEST = "sha256:" + "d" * 64
SECRET = "seeded-marker-" + "q" * 20
IDENTIFIER = "b" * 32
BINDING = "kind-agentbreeder|uid-1|agentbreeder-dev|/k/config"
DEPLOYMENT_UID = "dep-uid"
POD = "agentbreeder-bbbb-7d9f-x2"


def deployment(state, record_state="ready"):
    store = Store(state)
    try:
        manifest = v2_manifest_data(name="hello", port=8080)
        manifest["target"] = "kubernetes"
        store.create(IDENTIFIER, manifest, DIGEST, BINDING)
        store.transition(IDENTIFIER, record_state, endpoint="kubernetes://agentbreeder-dev/x:8080")
    finally:
        store.close()


@pytest.fixture
def state(tmp_path):
    directory = tmp_path / "state"
    deployment(directory)
    secrets = directory / "secrets"
    secrets.mkdir(mode=0o700)
    (secrets / "token").write_text(SECRET)
    (secrets / "token").chmod(0o600)
    return directory


RUNNING_POD = {"name": POD, "reason": None, "readable": True}


class FakeKube:
    def __init__(self, text="", pod=None, child=None, error=None, fail_logs=False):
        self.text, self.chosen, self.child = text, pod or RUNNING_POD, child
        self.error, self.fail_logs = error, fail_logs  # fail_logs: True or an exception
        self.calls = []
        self.process = None

    def authorize(self, context=None):
        self.calls.append(("authorize", context))
        if self.error:
            raise self.error

    def inspect(self, record):
        self.calls.append(("inspect", record["id"]))
        return {"Id": DEPLOYMENT_UID, "deployment": record["id"], "name": record["container"]}

    def pod(self, info):
        self.calls.append(("pod", info["Id"]))
        return self.chosen

    def logs(self, info, tail=200, since=None, previous=False, pod=None):
        self.calls.append(("logs", pod, tail, since, previous))
        if isinstance(self.fail_logs, Exception):
            raise self.fail_logs
        if self.fail_logs:
            raise DeploymentError("kubectl could not read the pod's logs")
        return self.text, False

    def follow(self, info, tail=200, since=None, pod=None):
        self.calls.append(("follow", pod, tail, since))
        self.process = subprocess.Popen(
            [sys.executable, "-c", self.child],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return self.process


@pytest.fixture
def kube(monkeypatch):
    holder = SimpleNamespace()

    def install(fake):
        monkeypatch.setattr(logs_command, "_kubernetes", lambda: fake)
        monkeypatch.setattr(logs_command, "_runtime", lambda: pytest.fail("Docker was asked"))
        monkeypatch.setattr(logs_command, "_stdin_descriptor", lambda: None)
        holder.fake = fake
        return fake

    holder.install = install
    return holder


def run(state, *argv):
    return cli.main(["logs", IDENTIFIER, *argv, "--state-dir", str(state)])


# -- the command ---------------------------------------------------------------------------------


def test_the_chosen_pod_is_read_redacted_through_the_recorded_binding(state, kube, capsys):
    fake = kube.install(FakeKube(text=f"2026-11-12T10:00:00Z token {SECRET}\x1b[2J\n"))
    assert run(state, "--tail", "30", "--since", "2h") == 0
    out = capsys.readouterr().out
    assert SECRET not in out and redaction.MARK in out and "\\x1b[2J" in out
    assert fake.calls == [
        ("authorize", BINDING),  # the recorded cluster binding, not the operator's selection
        ("inspect", IDENTIFIER),
        ("pod", DEPLOYMENT_UID),  # pods owned by the verified Deployment only
        ("logs", POD, 30, "2h", False),
    ]


def test_json_shape(state, kube, capsys):
    kube.install(FakeKube(text="a\n"))
    assert run(state, "--json") == 0
    body = json.loads(capsys.readouterr().out)
    assert (body["target"], body["previous"], body["pod_reason"]) == ("kubernetes", False, None)
    assert body["lines"] == ["a"] and body["source"] == "agent"


def test_a_pod_without_output_returns_no_lines_and_its_reason(state, kube, capsys):
    pending = {"name": POD, "reason": "ImagePullBackOff", "readable": False}
    fake = kube.install(FakeKube(pod=pending))
    assert run(state) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "no output yet (ImagePullBackOff)" in captured.err
    assert not [call for call in fake.calls if call[0] == "logs"]
    assert run(state, "--json") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["lines"] == [] and body["pod_reason"] == "ImagePullBackOff"


def test_a_crash_loop_is_read_with_its_reason(state, kube, capsys):
    looping = {"name": POD, "reason": "CrashLoopBackOff", "readable": True}
    kube.install(FakeKube(text="last crash\n", pod=looping))
    assert run(state, "--json") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["lines"] == ["last crash"] and body["pod_reason"] == "CrashLoopBackOff"
    assert run(state) == 0
    assert "not running (CrashLoopBackOff)" in capsys.readouterr().err


def test_previous_reads_the_restarted_container(state, kube, capsys):
    looping = {"name": POD, "reason": "CrashLoopBackOff", "readable": True}
    fake = kube.install(FakeKube(text="crash\n", pod=looping))
    assert run(state, "--previous", "--json") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["lines"] == ["crash"] and body["previous"] is True
    assert ("logs", POD, 200, None, True) in fake.calls


def test_previous_with_no_pod_reads_nothing(state, kube, capsys):
    fake = kube.install(FakeKube(pod={"name": None, "reason": "NoPod", "readable": False}))
    assert run(state, "--previous", "--json") == 0
    assert json.loads(capsys.readouterr().out)["pod_reason"] == "NoPod"
    assert not [call for call in fake.calls if call[0] == "logs"]


def test_previous_does_not_follow(state, kube, capsys):
    fake = kube.install(FakeKube())
    assert run(state, "--previous", "--follow") == 2
    assert capsys.readouterr().err.startswith("[USAGE]") and fake.calls == []


def test_cluster_and_kubectl_errors_are_coded(state, kube, capsys):
    kube.install(FakeKube(error=DeploymentError("[KUBERNETES_CLUSTER_MISMATCH] other cluster")))
    assert run(state) == 2
    assert capsys.readouterr().err.startswith("[CLUSTER_UNAVAILABLE]")
    kube.install(FakeKube(fail_logs=True))
    assert run(state, "--json") == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "KUBECTL_LOGS_FAILED"


def test_a_missing_previous_log_is_coded_not_printed(state, kube, capsys):
    from agentbreeder.kubernetes import LogUnavailable

    looping = {"name": POD, "reason": "CrashLoopBackOff", "readable": True}
    gone = LogUnavailable("the kubelet no longer has the previous container's log")
    kube.install(FakeKube(pod=looping, fail_logs=gone))
    assert run(state, "--previous", "--json") == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[LOG_UNAVAILABLE] the kubelet no longer has")
    error = json.loads(captured.out)["error"]
    assert error["code"] == "LOG_UNAVAILABLE" and "previous container" in error["message"]


def test_cluster_error_text_is_excerpted(state, kube, capsys):
    kube.install(FakeKube(error=DeploymentError("Namespace evil\x1b[2Jns lacks the label")))
    assert run(state) == 2
    err = capsys.readouterr().err
    assert err.startswith("[CLUSTER_UNAVAILABLE]") and "\x1b" not in err


def test_follow_streams_from_the_chosen_pod(state, kube, capsys):
    child = f"print('2026-11-12T10:00:00Z hi {SECRET}', flush=True)"
    fake = kube.install(FakeKube(child=child))
    assert run(state, "--follow", "--json") == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["line"].endswith(f"hi {redaction.MARK}") and lines[0]["target"] == "kubernetes"
    assert lines[-1]["reason"] == "container"
    assert fake.calls[-1] == ("follow", POD, 200, None)


def test_follow_refuses_a_pod_without_output(state, kube, capsys):
    fake = kube.install(FakeKube(pod={"name": POD, "reason": "Unschedulable", "readable": False}))
    assert run(state, "--follow") == 2
    assert capsys.readouterr().err.startswith("[POD_NOT_RUNNING]") and fake.process is None


def test_a_failing_kubectl_follow_is_coded(state, kube, capsys):
    kube.install(FakeKube(child="raise SystemExit(1)"))
    assert run(state, "--follow") == 2
    assert capsys.readouterr().err.startswith("[KUBECTL_LOGS_FAILED]")


def test_a_pod_status_error_is_coded(state, kube, capsys):
    fake = kube.install(FakeKube())

    def broken(info):
        raise DeploymentError("Cannot read the pod's status")

    fake.pod = broken
    assert run(state) == 2
    assert capsys.readouterr().err.startswith("[CLUSTER_UNAVAILABLE]")


# -- Kubernetes.pod: ownership and choice ---------------------------------------------------------


@pytest.fixture
def bound():
    kubernetes = Kubernetes()
    kubernetes.kube_context, kubernetes.namespace = "kind-agentbreeder", "agentbreeder-dev"
    kubernetes.kubeconfig = "/k/config"
    return kubernetes


INFO = {"Id": DEPLOYMENT_UID, "deployment": IDENTIFIER}


def _rs(uid, owner=DEPLOYMENT_UID):
    return {"metadata": {"uid": uid, "ownerReferences": [{"uid": owner, "controller": True}]}}


def _pod(name, rs="rs-1", created="2026-11-12T10:00:00Z", agent=None, deleting=False, **status):
    metadata = {
        "name": name,
        "creationTimestamp": created,
        "ownerReferences": [{"uid": rs, "controller": True}] if rs else [],
    }
    if deleting:
        metadata["deletionTimestamp"] = created
    statuses = [{"name": "agent", **agent}] if agent is not None else []
    return {"metadata": metadata, "status": {"containerStatuses": statuses, **status}}


RUN = {"state": {"running": {}}}


def _cluster(monkeypatch, bound, sets, pods):
    def namespaced(*arguments, **options):
        assert arguments[:3] in (("get", "replicasets", "-l"), ("get", "pods", "-l"))
        assert arguments[3] == f"{DEPLOYMENT_LABEL}={IDENTIFIER}"  # always scoped by the label
        items = sets if arguments[1] == "replicasets" else pods
        return json.dumps({"items": items})

    monkeypatch.setattr(bound, "_namespaced", namespaced)


def test_a_running_owned_pod_is_chosen(bound, monkeypatch):
    _cluster(monkeypatch, bound, [_rs("rs-1")], [_pod("p1", agent=RUN)])
    assert bound.pod(INFO) == {"name": "p1", "reason": None, "readable": True}


def test_a_stray_pod_with_the_label_is_ignored(bound, monkeypatch):
    stray = _pod("forged", rs=None, agent=RUN, created="2026-11-12T11:00:00Z")
    other = _pod("other-owner", rs="rs-x", agent=RUN, created="2026-11-12T11:00:00Z")
    pods = [stray, other, _pod("p1", agent=RUN)]
    _cluster(monkeypatch, bound, [_rs("rs-1"), _rs("rs-x", owner="someone-else")], pods)
    assert bound.pod(INFO)["name"] == "p1"


def test_a_stale_pod_does_not_hide_a_running_one(bound, monkeypatch):
    stale = _pod(
        "stale", agent={"state": {"waiting": {"reason": "ImagePullBackOff"}}},
        created="2026-11-12T11:00:00Z",
    )  # fmt: skip
    _cluster(monkeypatch, bound, [_rs("rs-1")], [stale, _pod("p1", agent=RUN)])
    assert bound.pod(INFO) == {"name": "p1", "reason": None, "readable": True}


def test_a_rollout_prefers_the_running_old_pod_then_the_newest(bound, monkeypatch):
    new = _pod("new", rs="rs-2", agent={"state": {"waiting": {"reason": "ContainerCreating"}}},
               created="2026-11-12T11:00:00Z")  # fmt: skip
    old = _pod("old", agent=RUN)
    _cluster(monkeypatch, bound, [_rs("rs-1"), _rs("rs-2")], [new, old])
    assert bound.pod(INFO)["name"] == "old"
    _cluster(monkeypatch, bound, [_rs("rs-1"), _rs("rs-2")], [new])
    assert bound.pod(INFO) == {"name": "new", "reason": "ContainerCreating", "readable": False}


def test_a_terminating_pod_is_skipped(bound, monkeypatch):
    _cluster(monkeypatch, bound, [_rs("rs-1")], [_pod("gone", agent=RUN, deleting=True)])
    assert bound.pod(INFO) == {"name": None, "reason": "NoPod", "readable": False}


def test_a_crash_loop_is_readable(bound, monkeypatch):
    looping = {"state": {"waiting": {"reason": "CrashLoopBackOff"}}, "restartCount": 3}
    _cluster(monkeypatch, bound, [_rs("rs-1")], [_pod("p1", agent=looping)])
    assert bound.pod(INFO) == {"name": "p1", "reason": "CrashLoopBackOff", "readable": True}


def test_a_first_crash_before_restart_count_is_readable(bound, monkeypatch):
    first = {
        "state": {"waiting": {"reason": "CrashLoopBackOff"}},
        "lastState": {"terminated": {"reason": "Error"}},
        "restartCount": 0,
    }
    _cluster(monkeypatch, bound, [_rs("rs-1")], [_pod("p1", agent=first)])
    assert bound.pod(INFO) == {"name": "p1", "reason": "CrashLoopBackOff", "readable": True}


def test_only_the_agent_container_counts(bound, monkeypatch):
    pod = _pod("p1", agent={"state": {"waiting": {"reason": "ImagePullBackOff"}}})
    pod["status"]["containerStatuses"].insert(0, {"name": "sidecar", **RUN})
    _cluster(monkeypatch, bound, [_rs("rs-1")], [pod])
    assert bound.pod(INFO) == {"name": "p1", "reason": "ImagePullBackOff", "readable": False}


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ({"phase": "Pending", "conditions": [{"status": "False", "reason": "Unschedulable"}]},
         "Unschedulable"),
        ({"phase": "Pending"}, "Pending"),
        ({"phase": "weird; phase"}, "Pending"),  # never echoed
    ],
)  # fmt: skip
def test_scheduling_reasons(bound, monkeypatch, status, reason):
    _cluster(monkeypatch, bound, [_rs("rs-1")], [_pod("p1", **status)])
    assert bound.pod(INFO)["reason"] == reason


@pytest.mark.parametrize("reply", ["[1, 2]", "not json", '{"items": [1]}'])
def test_a_malformed_reply_is_a_cluster_error(bound, monkeypatch, reply):
    monkeypatch.setattr(bound, "_namespaced", lambda *a, **k: reply)
    with pytest.raises(DeploymentError):
        bound.pod(INFO)


# -- Kubernetes.logs / follow --------------------------------------------------------------------


def test_log_argv_reads_one_pod_and_the_agent_container(bound):
    argv = bound._log_argv(POD, 50, "10m", previous=False, follow=False)
    assert argv[:5] == ["kubectl", "--kubeconfig", "/k/config", "--context", "kind-agentbreeder"]
    rest = argv[5:]
    assert rest[:4] == ["--namespace", "agentbreeder-dev", "logs", f"pod/{POD}"]
    assert ["-c", "agent"] == rest[4:6] and "--timestamps" in rest
    assert rest[rest.index("--tail") + 1] == "50" and rest[rest.index("--since") + 1] == "10m"
    stamped = bound._log_argv(POD, 5, "2026-11-12T09:00:00Z", previous=True, follow=False)
    assert "--since-time" in stamped and "--previous" in stamped and "--follow" not in stamped
    assert "--follow" in bound._log_argv(POD, 5, None, previous=False, follow=True)


@pytest.mark.parametrize(
    ("pod", "tail", "since"),
    [("x; rm", 5, None), (None, 5, None), ("-x", 5, None), (POD, 0, None), (POD, True, None),
     (POD, 5, "1d")],
)  # fmt: skip
def test_log_argv_validates(bound, pod, tail, since):
    with pytest.raises(DeploymentError):
        bound._log_argv(pod, tail, since, previous=False, follow=False)


def _fake_kubectl(monkeypatch, bound, script):
    monkeypatch.setattr(bound, "_binary", lambda *arguments: [sys.executable, "-c", script])


def test_logs_joins_partials(bound, monkeypatch):
    from agentbreeder.runtime import PARTIAL

    payload = "x" * (PARTIAL - 4) + SECRET
    chunked = (
        "2026-11-12T10:00:00.1Z "
        + payload[:PARTIAL]
        + "2026-11-12T10:00:00.2Z "
        + payload[PARTIAL:]
    )
    script = (
        f"import sys; sys.stdout.write({chunked!r} + '\\n'); sys.stderr.write('kubectl noise')"
    )
    _fake_kubectl(monkeypatch, bound, script)
    text, truncated = bound.logs(INFO, tail=5, pod=POD)
    assert text == "2026-11-12T10:00:00.1Z " + payload + "\n" and not truncated
    assert "kubectl noise" not in text  # kubectl's stderr is never read


def test_logs_failure_never_carries_kubectl_stderr(bound, monkeypatch):
    _fake_kubectl(monkeypatch, bound, "import sys; sys.stderr.write('secret path'); sys.exit(1)")
    with pytest.raises(DeploymentError) as error:
        bound.logs(INFO, tail=5, previous=True, pod=POD)
    assert "secret path" not in str(error.value) and "restarted" in str(error.value)


# Bodies captured from `kubectl logs --timestamps` (exit 0, no trailing newline) on kind, node
# v1.37, 2026-11-13: `--previous` on a crash-looping pod, and a pod whose log file was removed.
UNRETRIEVABLE = "unable to retrieve container logs for containerd://" + "4c35e140" * 8
NO_FILE = (
    'failed to try resolving symlinks in path "/var/log/pods/ns_pod_uid/agent/2.log": '
    "lstat /var/log/pods/ns_pod_uid/agent/2.log: no such file or directory"
)


@pytest.mark.parametrize(
    "body", [UNRETRIEVABLE, NO_FILE, NO_FILE + "\n", "\n" + UNRETRIEVABLE, UNRETRIEVABLE + "\r\n"]
)
@pytest.mark.parametrize("previous", [True, False])
def test_logs_reports_a_kubelet_missing_log_body(bound, monkeypatch, body, previous):
    from agentbreeder.kubernetes import LogUnavailable

    _fake_kubectl(monkeypatch, bound, f"import sys; sys.stdout.write({body!r})")
    with pytest.raises(LogUnavailable) as error:
        bound.logs(INFO, tail=5, previous=previous, pod=POD)
    message = str(error.value)
    assert ("previous container's" in message) is previous and "/var/log" not in message


@pytest.mark.parametrize(
    "body",
    [
        "2026-11-13T19:23:28.584735824Z " + UNRETRIEVABLE + "\n",  # an app line saying it
        "2026-11-13T19:23:28Z " + NO_FILE + "\n",
        UNRETRIEVABLE + " (retrying)",  # not the whole line
        "app: " + NO_FILE,
        UNRETRIEVABLE + "\n" + UNRETRIEVABLE + "\n",  # two lines is output, not one message
        "unable to retrieve container logs for containerd://" + "4c35e140" * 7,  # short ID
        NO_FILE.replace("no such file or directory", "permission denied"),
        NO_FILE.replace("2.log", "a" * 1000 + "/2.log") * 4,  # long (past the cap)
        NO_FILE.replace("try resolving symlinks in path", "open log file"),  # not measured
    ],
)
def test_logs_keeps_lines_that_only_resemble_it(bound, monkeypatch, body):
    _fake_kubectl(monkeypatch, bound, f"import sys; sys.stdout.write({body!r})")
    text, truncated = bound.logs(INFO, tail=5, previous=True, pod=POD)
    assert text == body and not truncated


def test_follow_runs_kubectl_follow(bound, monkeypatch):
    from agentbreeder.runtime import stop_follow

    _fake_kubectl(monkeypatch, bound, "print('line', flush=True)")
    process = bound.follow(INFO, tail=5, pod=POD)
    try:
        assert process.stdout.readline() == b"line\n"
    finally:
        stop_follow(process)
