"""The `token` provider against a real `kind` cluster (#770 §13 K1's check). Opt-in:

    AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_KIND_TEST=1 python -m pytest \
        tests/test_kubernetes_token_kind.py -v

It creates its own `kind` cluster (`ab-token-k1`, never AgentBreeder's `agentbreeder` cluster)
whose API server listens on a non-loopback address of this host, which `kind` adds to the API
server certificate's SANs (`networking.apiServerAddress`). As the cluster admin it prepares what
the runbook would: the labelled namespace, a quota, the four service accounts with the reads
`connect` needs, and a minting account whose static token goes into a dedicated mint
kubeconfig. Then `cluster connect token` runs with a fake admission policy check (K2 builds the
real one), its block is put in an operator config, and the runtime authorizes through a token
session. The cluster is deleted in `finally`; no image is removed.

Needs `docker`, `kind` (0.33 or later) and `kubectl` on PATH, and a host IPv4 address that is
not loopback (a LAN address). The API server is reachable on that LAN address while the test
runs.

Python needs a system CA store: `connect` refuses a server it cannot check against public CAs
(`KUBERNETES_TRUST_STORE_EMPTY`). A python.org Python on macOS has none until its
`Install Certificates.command` is run; without changing that Python, point it at a bundle for
the run instead, for example `SSL_CERT_FILE="$(python -m certifi)"` with certifi installed.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import write_private

from agentbreeder import cluster, cluster_token, kubernetes_token
from agentbreeder.kubernetes import Kubernetes
from agentbreeder.store import DeploymentError, Store

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("AGENTBREEDER_DOCKER_TEST") == "1"
        and os.environ.get("AGENTBREEDER_KIND_TEST") == "1"
    ),
    reason="needs Docker and kind: AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_KIND_TEST=1",
)

NAME = "ab-token-k1"
CONTEXT = f"kind-{NAME}"
NAMESPACE = "agentbreeder"
POD_CIDR = "10.244.0.0/16"
SERVICE_CIDR = "10.96.0.0/16"

RBAC = f"""
apiVersion: v1
kind: Namespace
metadata:
  name: {NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: agentbreeder
    pod-security.kubernetes.io/enforce: restricted
---
apiVersion: v1
kind: ResourceQuota
metadata: {{name: ab-quota, namespace: {NAMESPACE}}}
spec:
  hard: {{"count/secrets": "50", "count/deployments.apps": "20"}}
---
apiVersion: v1
kind: ServiceAccount
metadata: {{name: ab-deployer, namespace: {NAMESPACE}}}
---
apiVersion: v1
kind: ServiceAccount
metadata: {{name: ab-installer, namespace: {NAMESPACE}}}
---
apiVersion: v1
kind: ServiceAccount
metadata: {{name: ab-operator, namespace: {NAMESPACE}}}
---
apiVersion: v1
kind: ServiceAccount
metadata: {{name: ab-canary, namespace: {NAMESPACE}}}
---
apiVersion: v1
kind: ServiceAccount
metadata: {{name: ab-minter, namespace: {NAMESPACE}}}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {{name: ab-minter, namespace: {NAMESPACE}}}
rules:
- apiGroups: [""]
  resources: [serviceaccounts/token]
  resourceNames: [ab-deployer, ab-installer, ab-operator, ab-canary]
  verbs: [create]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {{name: ab-minter, namespace: {NAMESPACE}}}
roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: Role, name: ab-minter}}
subjects: [{{kind: ServiceAccount, name: ab-minter, namespace: {NAMESPACE}}}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {{name: ab-test-deployer-ns}}
rules:
- apiGroups: [""]
  resources: [namespaces]
  resourceNames: [kube-system, {NAMESPACE}]
  verbs: [get]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: {{name: ab-test-deployer-ns}}
roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: ab-test-deployer-ns}}
subjects: [{{kind: ServiceAccount, name: ab-deployer, namespace: {NAMESPACE}}}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {{name: ab-deployer, namespace: {NAMESPACE}}}
rules:
- apiGroups: [""]
  resources: [resourcequotas]
  verbs: [get, list]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {{name: ab-deployer, namespace: {NAMESPACE}}}
roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: Role, name: ab-deployer}}
subjects: [{{kind: ServiceAccount, name: ab-deployer, namespace: {NAMESPACE}}}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {{name: ab-test-installer}}
rules:
- apiGroups: [""]
  resources: [nodes]
  verbs: [list]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: {{name: ab-test-installer}}
roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: ab-test-installer}}
subjects: [{{kind: ServiceAccount, name: ab-installer, namespace: {NAMESPACE}}}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {{name: ab-installer, namespace: {NAMESPACE}}}
rules:
- apiGroups: [""]
  resources: [pods, services]
  verbs: [list]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {{name: ab-installer, namespace: {NAMESPACE}}}
roleRef: {{apiGroup: rbac.authorization.k8s.io, kind: Role, name: ab-installer}}
subjects: [{{kind: ServiceAccount, name: ab-installer, namespace: {NAMESPACE}}}]
"""


def _host_address() -> str:
    """This host's outbound IPv4 address (no packet is sent by a UDP connect)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))
        address = probe.getsockname()[0]
    if address.startswith("127.") or address.startswith("169.254."):
        pytest.skip("no non-loopback host IPv4 address")
    return address


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 0))
        return listener.getsockname()[1]


def _run(argv, *, stdin: bytes | None = None, timeout: int = 600) -> str:
    result = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout, check=True)
    return result.stdout.decode().strip()


def _admin(kubeconfig: Path, *arguments: str, stdin: bytes | None = None) -> str:
    return _run(
        ["kubectl", "--kubeconfig", str(kubeconfig), "--context", CONTEXT, *arguments],
        stdin=stdin,
    )


@pytest.fixture
def token_cluster(tmp_path, monkeypatch):
    for tool in ("docker", "kind", "kubectl"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not on PATH")
    monkeypatch.setattr(kubernetes_token, "sessions_root", lambda: tmp_path / "kube-sessions")
    address, port = _host_address(), _free_port()
    config = {
        "kind": "Cluster",
        "apiVersion": "kind.x-k8s.io/v1alpha4",
        "networking": {
            "apiServerAddress": address,
            "apiServerPort": port,
            "podSubnet": POD_CIDR,
            "serviceSubnet": SERVICE_CIDR,
        },
    }
    admin = tmp_path / "admin.kubeconfig"
    try:
        _run(
            ["kind", "create", "cluster", "--name", NAME, "--image", cluster.NODE_IMAGE,
             "--kubeconfig", str(admin), "--wait", "180s", "--config", "-"],
            stdin=yaml.safe_dump(config).encode(),
        )  # fmt: skip
        admin.chmod(0o600)
        _admin(admin, "apply", "-f", "-", stdin=RBAC.encode())
        minter = _admin(
            admin, "--namespace", NAMESPACE, "create", "token", "ab-minter", "--duration", "2h"
        )
        server = f"https://{address}:{port}"
        view = json.loads(_admin(admin, "config", "view", "--raw", "--minify", "-o", "json"))
        ca_data = view["clusters"][0]["cluster"]["certificate-authority-data"]
        mint = write_private(
            tmp_path / "mint.kubeconfig",
            {
                "apiVersion": "v1",
                "kind": "Config",
                "clusters": [
                    {
                        "name": "c",
                        "cluster": {"server": server, "certificate-authority-data": ca_data},
                    }
                ],
                "contexts": [{"name": "minter", "context": {"cluster": "c", "user": "m"}}],
                "users": [{"name": "m", "user": {"token": minter}}],
                "current-context": "minter",
            },
        )
        uid = _admin(admin, "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}")
        yield {"server": server, "address": address, "mint": mint, "uid": uid}
    finally:
        subprocess.run(["kind", "delete", "cluster", "--name", NAME], capture_output=True)


def test_connect_token_and_a_token_session_on_kind(token_cluster, identity_env, tmp_path):
    options = {
        "server": token_cluster["server"],
        "mint_kubeconfig": token_cluster["mint"],
        "registry": "registry.example.com/agentbreeder",
        "pod_cidrs": [POD_CIDR],
        "service_cidr": SERVICE_CIDR,
        "namespace": NAMESPACE,
    }
    with pytest.raises(DeploymentError, match="KUBERNETES_POLICY_UNCHECKED"):
        cluster_token.connect(**options)  # the shipped check: K2's, refuses
    result = cluster_token.connect(
        **options, policy=lambda session, namespace, run: "sha256:" + "0" * 64
    )
    block = result["kubernetes"]
    assert block["cluster_uid"] == token_cluster["uid"]
    assert f"{token_cluster['address']}/32" in block["blocked_cidrs"]
    assert POD_CIDR in block["blocked_cidrs"] and SERVICE_CIDR in block["blocked_cidrs"]
    assert result["review"]["token_observed_minutes"] in range(25, 31)
    assert list((tmp_path / "kube-sessions").iterdir()) == []

    identity_env.config["environments"]["dev"] = {
        "targets": ["local", "kubernetes"],
        "kubernetes": block,
    }
    store = Store(tmp_path / "state")
    kube = Kubernetes(identity_env.load(), bindings=store)
    try:
        kube.select("dev")
        kube.authorize()  # kube-system UID and namespace labels, through a deployer token
        assert kube.command("--namespace", NAMESPACE, "get", "resourcequotas", "-o", "name")
        with pytest.raises(DeploymentError):  # the deployer cannot read Secrets here
            kube.command("--namespace", NAMESPACE, "get", "secrets")
    finally:
        kube.close()
        store.close()
    assert list((tmp_path / "kube-sessions").iterdir()) == []
