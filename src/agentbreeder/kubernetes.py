"""Bounded kubectl operations for `target: kubernetes` (spec §7).

The methods mirror `runtime.Docker`, which the lifecycle already calls. kubectl always gets a
dedicated kubeconfig, context and namespace from the operator config, never `KUBECONFIG` or
`current-context`. That kubeconfig must be private, use static credentials (no exec plugin) and
point at a loopback API server: only a local cluster is supported. The cluster must also match the
recorded `kube-system` UID. Objects, including Secrets, go over stdin with `create`, so nothing
sensitive reaches argv or a last-applied annotation. kubectl's stderr is never copied into an
error, since it can carry local details; messages come from status fields.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    parse_yaml,
    read_private,
)
from agentbreeder.runtime import probe_http
from agentbreeder.secret_files import MOUNT_ROOT
from agentbreeder.store import DeploymentError

MANAGED_BY = "app.kubernetes.io/managed-by"
DEPLOYMENT_LABEL = "io.agentbreeder/deployment"
SPEC_ANNOTATION = "io.agentbreeder/spec"
IMAGE_ANNOTATION = "io.agentbreeder/image"
POD_SECURITY = "pod-security.kubernetes.io/enforce"
SCHEME = "kubernetes://"
ROLLOUT_TIMEOUT = 180
_KINDS = "deployment,service,networkpolicy,secret"
_CLUSTER_KEYS = {"server", "certificate-authority-data"}  # no proxy-url, no insecure-skip
_USER_KEYS = {"client-certificate-data", "client-key-data", "token"}  # no exec, no auth-provider
_FAILED_REASONS = {
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
    "CrashLoopBackOff",
}


class Kubernetes:
    def __init__(self, operator: OperatorConfig | None = None):
        self.operator = operator
        self.context = ""  # recorded binding: "<kube context>|<uid>|<namespace>|<kubeconfig>"
        self.kubeconfig = ""
        self.kube_context = ""
        self.cluster_uid = ""
        self.namespace = ""
        self.blocked: tuple[str, ...] = ()

    def select(self, environment: str | None) -> None:
        """Bind to the cluster the operator configured for this environment."""
        model = self.operator.model if self.operator else None
        config = model.environments.get(environment or "") if model else None
        target = config.kubernetes if config else None
        if target is None:
            raise DeploymentError(
                f"[KUBERNETES_UNCONFIGURED] Environment {environment} has no kubernetes block"
            )
        self.kube_context = target.context
        self.cluster_uid = target.cluster_uid
        self.namespace = target.namespace
        self.kubeconfig = target.kubeconfig
        self.blocked = tuple(target.blocked_cidrs)
        self.context = "|".join(
            (target.context, target.cluster_uid, target.namespace, target.kubeconfig)
        )

    def _binary(self, *arguments: str) -> list[str]:
        return [
            "kubectl",
            "--kubeconfig",
            self.kubeconfig,
            "--context",
            self.kube_context,
            *arguments,
        ]

    @staticmethod
    def _environment() -> dict[str, str]:
        return {k: v for k, v in os.environ.items() if k != "KUBECONFIG"}

    def _check_kubeconfig(self) -> None:
        """Only a private, static-credential kubeconfig for a loopback cluster is accepted."""
        try:
            config = parse_yaml(read_private(Path(self.kubeconfig), "Kubeconfig"), "Kubeconfig")
            context = next(
                c["context"] for c in config["contexts"] if c["name"] == self.kube_context
            )
            cluster = next(
                c["cluster"] for c in config["clusters"] if c["name"] == context["cluster"]
            )
            user = next(u["user"] for u in config["users"] if u["name"] == context["user"])
            host = urlsplit(cluster["server"]).hostname or ""
            loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
            unsafe = set(cluster) - _CLUSTER_KEYS or set(user) - _USER_KEYS
        except OperatorConfigError as error:
            raise DeploymentError(f"[KUBECONFIG_INVALID] {error}") from error
        except (ValueError, KeyError, TypeError, StopIteration) as error:
            raise DeploymentError("[KUBECONFIG_INVALID] Cannot read the kubeconfig") from error
        if unsafe:
            raise DeploymentError(
                "[KUBECONFIG_INVALID] Only static credentials and a direct connection are accepted"
            )
        if not loopback:
            raise DeploymentError(
                "[KUBERNETES_NOT_LOCAL] Only a local (loopback) cluster is supported in this "
                "preview"
            )

    def command(self, *arguments: str, timeout: int = 30, stdin: bytes | None = None) -> str:
        try:
            result = subprocess.run(
                self._binary(*arguments),
                capture_output=True,
                input=stdin,
                timeout=timeout,
                check=False,
                env=self._environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DeploymentError(
                "kubectl failed or timed out; is it installed and is the cluster reachable?"
            ) from error
        if result.returncode:
            raise DeploymentError("The cluster rejected the operation; check the cluster and RBAC")
        output = result.stdout
        return (output.decode("utf-8", "replace") if isinstance(output, bytes) else output).strip()

    def _namespaced(self, *arguments: str, **options) -> str:
        return self.command("--namespace", self.namespace, *arguments, **options)

    def authorize(self, context: str | None = None) -> None:
        """Verify the cluster and namespace. A recorded `context` wins over the selection, so
        later operations act on the cluster the deployment was created in."""
        if context:
            parts = context.split("|")
            if len(parts) != 4:
                raise DeploymentError("Cannot read this deployment's cluster binding")
            self.kube_context, self.cluster_uid, self.namespace, self.kubeconfig = parts
            self.context = context
        if not self.context:
            raise DeploymentError("[KUBERNETES_UNCONFIGURED] No cluster selected")
        self._check_kubeconfig()
        found = self.command("get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}")
        if found != self.cluster_uid:
            raise DeploymentError(
                "[KUBERNETES_CLUSTER_MISMATCH] This context does not point at the configured "
                "cluster; nothing was changed"
            )
        try:
            namespace = json.loads(self.command("get", "namespace", self.namespace, "-o", "json"))
            labels = namespace["metadata"].get("labels") or {}
        except (ValueError, KeyError, TypeError) as error:
            raise DeploymentError("Cannot verify the namespace") from error
        if labels.get(MANAGED_BY) != "agentbreeder":
            raise DeploymentError(
                f"[KUBERNETES_NAMESPACE_UNMANAGED] Namespace {self.namespace} lacks the label "
                f"{MANAGED_BY}=agentbreeder; AgentBreeder only deploys into namespaces marked "
                "for it"
            )
        if labels.get(POD_SECURITY) != "restricted":
            raise DeploymentError(
                f"[KUBERNETES_NAMESPACE_UNRESTRICTED] Namespace {self.namespace} must set "
                f"{POD_SECURITY}=restricted"
            )

    def inspect(self, record: dict) -> dict | None:
        output = self._namespaced(
            "get", "deployment", record["container"], "--ignore-not-found", "-o", "json"
        )
        if not output:
            return None
        try:
            item = json.loads(output)
            metadata = item["metadata"]
            labels = metadata.get("labels") or {}
            annotations = metadata.get("annotations") or {}
            container = item["spec"]["template"]["spec"]["containers"][0]
            if (
                labels.get(DEPLOYMENT_LABEL) != record["id"]
                or annotations.get(SPEC_ANNOTATION) != record["digest"]
                or container["image"] != annotations.get(IMAGE_ANNOTATION)
            ):
                raise DeploymentError("Workload ownership does not match; no changes made")
            status = item.get("status") or {}
            return {
                "Id": metadata["uid"],
                "State": {"Running": int(status.get("availableReplicas") or 0) >= 1},
                "deployment": record["id"],
                "name": record["container"],
            }
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise DeploymentError("Cannot verify workload ownership") from error

    def image_present(self, image: str) -> bool:
        return True  # images are registry digests here; the cluster pulls them

    def create(
        self,
        record: dict,
        manifest: Manifest,
        *,
        network: str | None = None,
        environment: dict[str, str] | None = None,
        mounts=(),
        host_gateway: bool = False,
        agents_network: str | None = None,
    ) -> None:
        if network:
            raise DeploymentError("Runtime enforcement is not supported on Kubernetes")
        if not self.blocked:
            raise DeploymentError("[KUBERNETES_UNCONFIGURED] blocked_cidrs is required")
        document = {
            "apiVersion": "v1",
            "kind": "List",
            "items": _objects(record, manifest, self.namespace, environment, mounts, self.blocked),
        }
        self._delete(record["id"])  # a previous attempt may have left objects behind
        try:
            self._namespaced(
                "create", "-f", "-", timeout=60, stdin=json.dumps(document).encode("utf-8")
            )
        except DeploymentError:
            self._delete(record["id"])  # a partial apply must not leave a Secret behind
            raise

    def start(self, info: dict) -> None:
        """Pods start on apply; this waits for the rollout and reports why it failed."""
        deadline = time.monotonic() + ROLLOUT_TIMEOUT
        while time.monotonic() < deadline:
            current = json.loads(
                self._namespaced("get", "deployment", info["name"], "-o", "json") or "{}"
            )
            if int((current.get("status") or {}).get("availableReplicas") or 0) >= 1:
                return
            reason = self._pod_failure(info["deployment"])
            if reason in _FAILED_REASONS:
                raise DeploymentError(f"[KUBERNETES_POD_FAILED] The pod cannot start: {reason}")
            time.sleep(2)
        raise DeploymentError(
            f"[KUBERNETES_ROLLOUT_TIMEOUT] The pod was not ready in {ROLLOUT_TIMEOUT}s; last "
            f"reason: {self._pod_failure(info['deployment']) or 'unknown'}"
        )

    def _pod_failure(self, identifier: str) -> str | None:
        """A status reason (a fixed Kubernetes word), never a message, which can hold details."""
        pods = json.loads(
            self._namespaced("get", "pods", "-l", f"{DEPLOYMENT_LABEL}={identifier}", "-o", "json")
        )
        for pod in pods.get("items", []):
            for status in (pod.get("status") or {}).get("containerStatuses") or []:
                waiting = (status.get("state") or {}).get("waiting") or {}
                reason = waiting.get("reason")
                if reason and re.fullmatch(r"[A-Za-z]{1,64}", reason):
                    return reason
        return None

    def remove(self, info: dict) -> None:
        self._delete(info["deployment"])

    def sweep(self, record: dict) -> None:
        """Delete by label when the Deployment is gone but other objects may remain."""
        self._delete(record["id"])

    def _delete(self, identifier: str) -> None:
        selector = f"{DEPLOYMENT_LABEL}={identifier}"
        self._namespaced(
            "delete", _KINDS, "-l", selector, "--ignore-not-found", "--wait=true", timeout=120
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            left = self._namespaced("get", f"pods,{_KINDS}", "-l", selector, "-o", "name")
            if not left:
                return
            time.sleep(2)
        raise DeploymentError("Workload removal could not be verified; retry")

    def endpoint(self, info: dict, manifest: Manifest) -> str:
        if not info["State"]["Running"]:
            raise DeploymentError("The workload has no available pod")
        return f"{SCHEME}{self.namespace}/{info['name']}:{manifest.port}"

    @staticmethod
    def parse_endpoint(endpoint: str) -> tuple[str, str, int]:
        match = re.fullmatch(
            re.escape(SCHEME) + r"([a-z0-9-]+)/([a-z0-9-]+):([0-9]{1,5})", endpoint
        )
        if match is None:
            raise DeploymentError("Not a Kubernetes endpoint")
        return match[1], match[2], int(match[3])

    @contextmanager
    def forward(self, endpoint: str, local_port: int = 0) -> Iterator[int]:
        """A loopback `kubectl port-forward` to the agent's Service; yields the local port."""
        namespace, service, port = self.parse_endpoint(endpoint)
        lines: queue.Queue[str] = queue.Queue()
        try:
            process = subprocess.Popen(
                [
                    *self._binary("--namespace", namespace, "port-forward"),
                    "--address",
                    "127.0.0.1",
                    f"service/{service}",
                    f"{local_port or ''}:{port}",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                env=self._environment(),
            )
        except OSError as error:
            raise DeploymentError("Cannot run kubectl port-forward") from error

        def read() -> None:
            for line in process.stdout or ():
                lines.put(line)

        threading.Thread(target=read, daemon=True).start()
        try:
            deadline = time.monotonic() + 20
            bound = None
            while bound is None and time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=0.2)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                match = re.search(r"Forwarding from 127\.0\.0\.1:([0-9]{1,5})", line)
                bound = int(match[1]) if match else None
            if bound is None:
                raise DeploymentError("kubectl port-forward did not start")
            yield bound
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def healthy(self, endpoint: str, path: str, timeout: float = 30) -> bool:
        try:
            with self.forward(endpoint) as port:
                return probe_http(port, path, timeout)
        except DeploymentError:
            return False


def _objects(record, manifest, namespace, environment, mounts, blocked) -> list[dict]:
    identifier, name = record["id"], record["container"]
    labels = {
        MANAGED_BY: "agentbreeder",
        DEPLOYMENT_LABEL: identifier,
        "io.agentbreeder/name": manifest.name,
    }
    selector = {DEPLOYMENT_LABEL: identifier}
    metadata = {"name": name, "namespace": namespace, "labels": labels}
    variables = dict(environment or {})
    for mount in mounts:
        for env in mount.envs:
            variables[f"{env}_FILE"] = mount.target
    volumes: list[dict] = [{"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}]
    volume_mounts: list[dict] = [{"name": "tmp", "mountPath": "/tmp"}]
    objects: list[dict] = []
    if mounts:
        objects.append(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": metadata,
                "type": "Opaque",
                "immutable": True,
                "data": {
                    mount.name: base64.b64encode(Path(mount.source).read_bytes()).decode("ascii")
                    for mount in mounts
                },
            }
        )
        volumes.append({"name": "secrets", "secret": {"secretName": name, "defaultMode": 0o440}})
        volume_mounts.append({"name": "secrets", "mountPath": MOUNT_ROOT, "readOnly": True})
    objects.append(
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": metadata,
            "spec": {
                "type": "ClusterIP",
                "selector": selector,
                "ports": [{"port": manifest.port, "targetPort": manifest.port}],
            },
        }
    )
    objects.append(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": metadata,
            "spec": {
                "podSelector": {"matchLabels": selector},
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [],
                "egress": [
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                                },
                                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                        ],
                    },
                    {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": list(blocked)}}]},
                ],
            },
        }
    )
    objects.append(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                **metadata,
                "annotations": {
                    SPEC_ANNOTATION: record["digest"],
                    IMAGE_ANNOTATION: manifest.image,
                },
            },
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": selector},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 65532,
                            "runAsGroup": 65532,
                            "fsGroup": 65532,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": "agent",
                                "image": manifest.image,
                                "imagePullPolicy": "IfNotPresent",
                                "ports": [{"containerPort": manifest.port}],
                                "env": [
                                    {"name": key, "value": value}
                                    for key, value in sorted(variables.items())
                                ],
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "1", "memory": "512Mi"},
                                },
                                "readinessProbe": {
                                    "httpGet": {
                                        "path": manifest.health_path,
                                        "port": manifest.port,
                                    },
                                    "periodSeconds": 2,
                                    "failureThreshold": 30,
                                },
                                "volumeMounts": volume_mounts,
                            }
                        ],
                        "volumes": volumes,
                    },
                },
            },
        }
    )
    return objects
