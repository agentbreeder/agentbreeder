"""The §7.1 Roles and the §7.2 admission policies on a real API server (#770 K2). **Written, not
run**: it needs Docker, `kind` (≥ 0.33) and `kubectl`, so it is opt-in:

    AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_KIND_TEST=1 \\
      python -m pytest -q tests/test_kind_admission_docker.py

It creates its own kind cluster, `ab-k2-admission`, from `cluster.NODE_IMAGE` (Kubernetes 1.37,
≥ 1.30), never the machine-wide `agentbreeder` cluster, and deletes it at the end even when a
test fails. Steps, in file order:

1. `cluster.install_restricted` installs the accounts, Roles and policies and writes the
   deployer kubeconfig; `ab-grants` is the probe fixture (`kubernetes_admission.probe_grants`).
2. Every CREATE probe pair as a server-side dry-run under its identity: the admitted request is
   admitted, the refused one is refused by the named policy (the message names it).
3. AgentBreeder's own create path (`Kubernetes.create` with a secret mount) under the deployer
   kubeconfig: Deployment, Service and NetworkPolicy, then the Secret owned by the Deployment.
4. The delete pairs: foreground admitted; orphan, background and a body-less DELETE refused; the
   chart Service and `ab-baseline` refused.
5. `Kubernetes.remove`: the foreground delete removes the owned Secret through the garbage
   collector (checked as admin), with the deployer holding no Secret read verb.
6. RBAC probes (`kubectl auth can-i`) for the deployer and the canary (§7.3).

Record what ran in the PR or `docs/kubernetes-admission.md`; until then nothing here is
verified.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from agentbreeder import cluster
from agentbreeder import kubernetes_admission as admission
from agentbreeder.kubernetes import Kubernetes
from agentbreeder.secret_files import MOUNT_ROOT, SecretMount

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1" or os.getenv("AGENTBREEDER_KIND_TEST") != "1",
    reason="Opt-in: needs Docker, kind and kubectl",
)

NAME = "ab-k2-admission"
CONTEXT = f"kind-{NAME}"
NS = "agentbreeder"
REGISTRY = "localhost:5001/agentbreeder"
BLOCKED = ("10.244.0.0/16", "10.96.0.0/16", "169.254.169.254/32")
ECHO = f"{REGISTRY}/mirror/echo@sha256:" + "e" * 64
STATE: dict = {}


def run(*argv: str, stdin: bytes | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), input=stdin, capture_output=True, timeout=timeout)


def kubectl(config: Path, *argv: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return run("kubectl", "--kubeconfig", str(config), "--context", CONTEXT, *argv, stdin=stdin)


def ok(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 0, result.stderr.decode()[-2000:]
    return result.stdout.decode()


@pytest.fixture(scope="module", autouse=True)
def kind_cluster(tmp_path_factory):
    directory = tmp_path_factory.mktemp("k2")
    admin = directory / "kubeconfig"
    ok(run("kind", "create", "cluster", "--name", NAME, "--image", cluster.NODE_IMAGE,
           "--wait", "180s", "--kubeconfig", str(admin), timeout=600))  # fmt: skip
    admin.chmod(0o600)
    try:
        namespace = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": NS,
                "labels": {"app.kubernetes.io/managed-by": "agentbreeder",
                           "pod-security.kubernetes.io/enforce": "restricted"},
            },
        }  # fmt: skip
        ok(kubectl(admin, "apply", "-f", "-", stdin=json.dumps(namespace).encode()))
        grants = admission.probe_grants(REGISTRY, BLOCKED, ECHO)
        configmap = admission.grants_configmap(NS, grants)
        ok(kubectl(admin, "apply", "-f", "-", stdin=json.dumps(configmap).encode()))
        original = (cluster.CONTEXT, cluster.NAMESPACE)
        cluster.CONTEXT, cluster.NAMESPACE = CONTEXT, NS
        try:
            deployer = cluster.install_restricted(admin, directory / "deployer")
        finally:
            cluster.CONTEXT, cluster.NAMESPACE = original
        STATE.update(admin=admin, deployer=deployer, grants=grants, directory=directory)
        STATE["canary"] = _account_kubeconfig(admin, directory, "canary")
        _wait_for_policies(deployer, grants)
        yield
    finally:
        run("kind", "delete", "cluster", "--name", NAME, timeout=300)


def _account_kubeconfig(admin: Path, directory: Path, name: str) -> Path:
    token = ok(kubectl(admin, "create", "token", admission.account(name), "--namespace", NS,
                       "--duration", "1h")).strip()  # fmt: skip
    config = yaml.safe_load(admin.read_text())
    config["users"] = [{"name": name, "user": {"token": token}}]
    entry = {"cluster": config["clusters"][0]["name"], "user": name, "namespace": NS}
    config["contexts"] = [{"name": CONTEXT, "context": entry}]
    path = directory / name
    path.write_text(yaml.safe_dump(config))
    path.chmod(0o600)
    return path


def _dry_run(config: Path, item: dict) -> subprocess.CompletedProcess:
    return kubectl(config, "--namespace", NS, "create", "--dry-run=server", "-f", "-",
                   stdin=json.dumps(item).encode())  # fmt: skip


def _wait_for_policies(deployer: Path, grants) -> None:
    """Policies take a few seconds to be enforced: wait until an ungranted name is refused."""
    own = admission.probe_objects(NS, grants)["Deployment"]
    ungranted = json.loads(json.dumps(own))
    ungranted["metadata"]["labels"][admission.NAME_LABEL] = "ungranted"
    ungranted["spec"]["template"]["metadata"]["labels"][admission.NAME_LABEL] = "ungranted"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if "ValidatingAdmissionPolicy" in _dry_run(deployer, ungranted).stderr.decode():
            return
        time.sleep(3)
    pytest.fail("the admission policies were not enforced within 120 s")


def _identity(name: str) -> Path:
    return STATE["deployer"] if name == "deployer" else STATE["canary"]


@pytest.mark.parametrize(
    "probe",
    [
        p
        for p in admission.probes(NS, admission.probe_grants(REGISTRY, BLOCKED, ECHO))
        if p.operation == "CREATE"
    ],
    ids=lambda probe: probe.name,
)
def test_probe_pairs_on_the_api_server(probe):
    admitted = _dry_run(_identity(probe.identity), probe.admitted)
    assert admitted.returncode == 0, admitted.stderr.decode()[-2000:]
    if probe.refused is not None:
        refused = _dry_run(_identity(probe.identity), probe.refused)
        assert refused.returncode != 0
        assert f"ValidatingAdmissionPolicy '{probe.policy}'" in refused.stderr.decode()


def _runtime() -> Kubernetes:
    runtime = Kubernetes()
    runtime.kubeconfig, runtime.kube_context = str(STATE["deployer"]), CONTEXT
    runtime.namespace, runtime.blocked = NS, BLOCKED
    runtime.cluster_uid = ok(kubectl(STATE["admin"], "get", "namespace", "kube-system", "-o",
                                     "jsonpath={.metadata.uid}"))  # fmt: skip
    runtime.context = f"kind|{CONTEXT}|{runtime.cluster_uid}|{NS}|{STATE['deployer']}"
    return runtime


def test_agentbreeders_own_create_path_is_admitted():
    from types import SimpleNamespace

    source = STATE["directory"] / "token"
    source.write_text("value")
    mount = SecretMount(("TOKEN",), "token", source, f"{MOUNT_ROOT}/token")
    manifest = SimpleNamespace(
        name=admission.PROBE_AGENT,
        image=f"{REGISTRY}/{admission.PROBE_AGENT}@sha256:" + "b" * 64,
        port=8080, health_path="/health", runtime=None,
    )  # fmt: skip
    record = {"id": "d" * 32, "container": "agentbreeder-" + "d" * 32, "digest": "f" * 64}
    runtime = _runtime()
    runtime.authorize()
    runtime.create(record, manifest, environment={"A": "1"}, mounts=[mount])
    secret = json.loads(ok(kubectl(STATE["admin"], "--namespace", NS, "get", "secret",
                                   record["container"], "-o", "json")))  # fmt: skip
    (owner,) = secret["metadata"]["ownerReferences"]
    assert owner["kind"] == "Deployment" and owner["blockOwnerDeletion"] is True
    STATE["record"] = record


@pytest.mark.parametrize(
    "cascade, admitted", [("foreground", True), ("orphan", False), ("background", False)]
)
def test_a_deployment_delete_needs_foreground_propagation(cascade, admitted):
    name = STATE["record"]["container"]
    result = kubectl(STATE["deployer"], "--namespace", NS, "delete", "deployment", name,
                     "--dry-run=server", f"--cascade={cascade}")  # fmt: skip
    assert (result.returncode == 0) is admitted, result.stderr.decode()[-2000:]


def test_a_body_less_delete_is_refused():
    path = f"/apis/apps/v1/namespaces/{NS}/deployments/{STATE['record']['container']}?dryRun=All"
    result = kubectl(STATE["deployer"], "delete", "--raw", path)
    assert result.returncode != 0
    assert "ab-deployments" in result.stderr.decode()


def test_chart_objects_and_the_baseline_cannot_be_deleted():
    chart = {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": "litellm", "namespace": NS,
                     "labels": {"io.agentbreeder/chart-service": "litellm"}},
        "spec": {"selector": {"io.agentbreeder/chart-service": "litellm"},
                 "ports": [{"port": 4000}]},
    }  # fmt: skip
    baseline = {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": {"name": "ab-baseline", "namespace": NS},
        "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
    }  # fmt: skip
    for item in (chart, baseline):
        ok(kubectl(STATE["admin"], "apply", "-f", "-", stdin=json.dumps(item).encode()))
    for kind, name in (("service", "litellm"), ("networkpolicy", "ab-baseline")):
        result = kubectl(
            STATE["deployer"], "--namespace", NS, "delete", kind, name, "--dry-run=server"
        )
        assert result.returncode != 0 and "ValidatingAdmissionPolicy" in result.stderr.decode()


def test_remove_deletes_the_owned_secret_through_the_garbage_collector():
    runtime = _runtime()
    runtime.remove({"deployment": STATE["record"]["id"]})
    name = STATE["record"]["container"]
    left = kubectl(STATE["admin"], "--namespace", NS, "get", "secret", name,
                   "--ignore-not-found", "-o", "name")  # fmt: skip
    assert ok(left).strip() == ""


@pytest.mark.parametrize(
    "identity, verb, resource, allowed",
    [
        ("deployer", "get", "secrets", False),
        ("deployer", "list", "secrets", False),
        ("deployer", "delete", "secrets", False),
        ("deployer", "create", "secrets", True),
        ("deployer", "create", "pods", False),
        ("deployer", "create", "replicasets", False),
        ("deployer", "patch", "configmaps", False),
        ("deployer", "create", "serviceaccounts/token", False),
        ("deployer", "create", "rolebindings", False),
        ("deployer", "impersonate", "serviceaccounts", False),
        ("deployer", "*", "*", False),
        ("canary", "create", "deployments", False),
        ("canary", "get", "pods/log", False),
        ("canary", "create", "pods", True),
    ],
)
def test_rbac_probes(identity, verb, resource, allowed):
    result = kubectl(_identity(identity), "--namespace", NS, "auth", "can-i", verb, resource)
    assert (result.stdout.decode().strip() == "yes") is allowed
