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
import queue
import re
import subprocess
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path

from agentbreeder import kubernetes_providers as providers
from agentbreeder.kubernetes_providers import PROVIDERS, Binding, Session
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import OperatorConfig
from agentbreeder.runtime import probe_http
from agentbreeder.secret_files import MOUNT_ROOT
from agentbreeder.store import DeploymentError

MANAGED_BY = "app.kubernetes.io/managed-by"
DEPLOYMENT_LABEL = "io.agentbreeder/deployment"
SPEC_ANNOTATION = "io.agentbreeder/spec"
IMAGE_ANNOTATION = "io.agentbreeder/image"
POD_SECURITY = "pod-security.kubernetes.io/enforce"
# The two-uid contract (#1089): the CLI container's uid and the directory it shares with the agent.
CLI_UID = 65533
CLI_SHARED_DIR = "/run/agentbreeder-cli"
SCHEME = "kubernetes://"
ROLLOUT_TIMEOUT = 180
# A deployment's own Secret is owned by its Deployment and goes with it through the garbage
# collector (#770 §4.3, K2): the deployer never lists, reads or deletes Secrets.
_KINDS = "deployment,service,networkpolicy"
# The fixed shapes `_objects` writes. The admission policies (`kubernetes_admission`, #770 §7.2)
# admit exactly these, so both read them from here.
POD_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "runAsUser": 65532,
    "runAsGroup": 65532,
    "fsGroup": 65532,
    "seccompProfile": {"type": "RuntimeDefault"},
}
CONTAINER_SECURITY_CONTEXT = {
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]},
}
CLI_SECURITY_CONTEXT = {
    "runAsUser": CLI_UID,
    "runAsGroup": CLI_UID,
    "allowPrivilegeEscalation": False,
    "readOnlyRootFilesystem": True,
    "capabilities": {"drop": ["ALL"]},
}
RESOURCES = {
    "requests": {"cpu": "100m", "memory": "128Mi"},
    "limits": {"cpu": "1", "memory": "512Mi"},
}
READINESS = {"periodSeconds": 2, "failureThreshold": 30}
SECRET_MODE = 0o440
TMP_VOLUME = {"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}
CLI_TMP_VOLUME = {"name": "cli-tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}
CLI_SHARED_VOLUME = {"name": "cli-shared", "emptyDir": {"medium": "Memory", "sizeLimit": "1Mi"}}
DNS_PEER = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
}
DNS_PORTS = [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]
_FAILED_REASONS = {
    "ErrImagePull",
    "ImagePullBackOff",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
    "CrashLoopBackOff",
}


class Kubernetes:
    def __init__(
        self,
        operator: OperatorConfig | None = None,
        *,
        providers: Mapping[str, type] | None = None,
        bindings=None,
    ):
        self.operator = operator
        # The cluster providers (#770 §4.1); injectable so tests and later slices can add one.
        self.providers = dict(providers if providers is not None else PROVIDERS)
        # Where by-reference bindings live: the store (`DeploymentService` sets it).
        self.bindings = bindings
        self.binding: Binding | None = None
        self.provider = None
        self._session: Session | None = None
        self._sessions = ExitStack()
        # Recorded binding: "kind|<kube context>|<uid>|<namespace>|<kubeconfig>" or
        # "<provider>|<binding id>" (kubernetes_providers).
        self.context = ""
        self.kubeconfig = ""
        self.kube_context = ""
        self.cluster_uid = ""
        self.namespace = ""
        self.blocked: tuple[str, ...] = ()
        self.allowed: tuple[str, ...] = ()  # the only egress of a kit deployment (F1b)

    def select(self, environment: str | None) -> None:
        """Bind to the cluster the operator configured for this environment."""
        model = self.operator.model if self.operator else None
        config = model.environments.get(environment or "") if model else None
        target = config.kubernetes if config else None
        if target is None:
            raise DeploymentError(
                f"[KUBERNETES_UNCONFIGURED] Environment {environment} has no kubernetes block"
            )
        binding = providers.from_target(environment or "", target, self.providers)
        self._bind(providers.record(binding, self.bindings))
        self.blocked = tuple(target.blocked_cidrs)
        self.allowed = tuple(target.agents_cidrs)

    def _bind(self, binding: Binding) -> None:
        """Use `binding`. The same binding keeps its provider and open session, so a loop of
        operations (reconcile, the kill switch) does not start a session per deployment."""
        if self.provider is None or binding != self.binding:
            self.close()
            self.provider = self.providers[binding.provider](binding)
        self.binding = binding
        self.cluster_uid = binding.cluster_uid
        self.namespace = binding.namespace
        self.kube_context = str(binding.document.get("context") or "")
        self.kubeconfig = str(binding.document.get("kubeconfig") or "")
        self.context = binding.record()

    def close(self) -> None:
        """End the provider session (a `kind` session holds nothing)."""
        self._session = None
        self._sessions.close()

    def __enter__(self) -> Kubernetes:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def same_binding(recorded: str, selected: str) -> bool:
        """Whether a deployment's recorded binding is the one selected now: compared as parsed
        inline bindings when both are (a record from before the tag reads as `kind`), else as
        strings."""
        first, second = (
            providers.parse_inline_or_none(recorded),
            providers.parse_inline_or_none(selected),
        )
        if first is not None and second is not None:
            return first == second
        return recorded == selected

    def _current(self) -> Session:
        if self.provider is None:
            # Attributes set by hand (older callers and tests): the `kind` session for them.
            # Never an empty kubeconfig or context, which kubectl would read as ~/.kube/config.
            if not (self.kubeconfig and self.kube_context):
                raise DeploymentError("[KUBERNETES_UNCONFIGURED] No cluster selected")
            return Session(
                ("--kubeconfig", self.kubeconfig, "--context", self.kube_context),
                providers.kubectl_environment(),
            )
        if self._session is None:
            self._session = self._sessions.enter_context(self.provider.session())
        return self._session

    def _binary(self, *arguments: str) -> list[str]:
        return ["kubectl", *self._current().argv, *arguments]

    def _environment(self) -> dict[str, str]:
        return dict(self._current().env)

    def _check_kubeconfig(self) -> None:
        """The provider's checks, before kubectl runs (for `kind`: a private, static-credential
        kubeconfig for a loopback cluster)."""
        if self.provider is None:
            self._bind(
                Binding(
                    provider=providers.KIND,
                    cluster_uid=self.cluster_uid,
                    namespace=self.namespace,
                    document={"context": self.kube_context, "kubeconfig": self.kubeconfig},
                )
            )
        self.provider.check(self._current())

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
            self._bind(providers.resolve(context, self.bindings, self.providers))
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
        # A kit runtime (manifest `runtime.profile: kit`) gets default-deny egress: only the
        # stack's agents network and cluster DNS (#1083 F1b). Every other manifest gets the
        # objects it always did.
        kit = manifest.runtime is not None and manifest.runtime.profile == "kit"
        document = {
            "apiVersion": "v1",
            "kind": "List",
            "items": _objects(
                record,
                manifest,
                self.namespace,
                environment,
                mounts,
                self.blocked,
                allowed=kit_destinations(self.allowed, self.blocked) if kit else None,
            ),
        }
        # Read the secret files before anything exists on the cluster, so an unreadable file
        # cannot leave a workload behind.
        data = _secret_data(mounts)
        self._delete(record["id"])  # a previous attempt may have left objects behind
        try:
            self._namespaced(
                "create", "-f", "-", timeout=60, stdin=json.dumps(document).encode("utf-8")
            )
            if mounts:
                # The Secret comes second, owned by the Deployment it is mounted into, so the
                # garbage collector deletes it with the Deployment (#770 §4.3). The pod waits
                # on its volume until then.
                owner = self._namespaced(
                    "get", "deployment", record["container"], "-o", "jsonpath={.metadata.uid}"
                )
                if not _UID.fullmatch(owner):
                    raise DeploymentError("Cannot read the new Deployment's UID")
                secret = _secret(record, manifest, self.namespace, data, owner)
                self._namespaced(
                    "create", "-f", "-", timeout=60, stdin=json.dumps(secret).encode("utf-8")
                )
        except DeploymentError as error:
            self._delete(record["id"])  # a partial create must not leave a workload behind
            raise DeploymentError(
                f"{error}. Under AgentBreeder's admission policies (`cluster up --restricted`) "
                "the agent must also be named in the stack's environment (ab-grants)"
            ) from error

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

    def pod(self, info: dict) -> dict:
        """The pod whose `agent` container `logs` reads, chosen among the pods owned by the
        verified Deployment (`info["Id"]`, through its ReplicaSets' ownerReferences), so a stray
        pod that merely carries the label is never read or reported. Prefers a pod whose agent
        container is running, then the newest. Returns `{"name", "reason", "readable"}`:
        `reason` is a fixed Kubernetes word (ImagePullBackOff, CrashLoopBackOff, Unschedulable,
        NoPod, ...) or None, never a status message; `readable` is whether the container has
        produced output to read (running, terminated, or restarted at least once)."""
        identifier = info["deployment"]
        selector = f"{DEPLOYMENT_LABEL}={identifier}"
        try:
            sets = json.loads(
                self._namespaced("get", "replicasets", "-l", selector, "-o", "json") or "{}"
            )
            owned_sets = {
                item["metadata"]["uid"]
                for item in sets.get("items") or []
                if any(
                    ref.get("uid") == info["Id"] and ref.get("controller")
                    for ref in item["metadata"].get("ownerReferences") or []
                )
            }
            pods = json.loads(
                self._namespaced("get", "pods", "-l", selector, "-o", "json") or "{}"
            )
            owned = [
                pod
                for pod in pods.get("items") or []
                if any(
                    ref.get("uid") in owned_sets and ref.get("controller")
                    for ref in pod["metadata"].get("ownerReferences") or []
                )
                and not pod["metadata"].get("deletionTimestamp")
            ]
            candidates = [_pod_view(pod) for pod in owned]
        except (ValueError, KeyError, AttributeError, TypeError) as error:
            raise DeploymentError("Cannot read the pod's status") from error
        if not candidates:
            return {"name": None, "reason": "NoPod", "readable": False}
        candidates.sort(key=lambda view: (view["running"], view["created"]), reverse=True)
        chosen = candidates[0]
        return {"name": chosen["name"], "reason": chosen["reason"], "readable": chosen["readable"]}

    def _log_argv(
        self, pod: str, tail: int, since: str | None, previous: bool, follow: bool
    ) -> list[str]:
        """`kubectl logs pod/NAME -c agent --timestamps` for a pod `pod()` chose. `since` was
        validated as runtime.SINCE."""
        from agentbreeder.runtime import MAX_TAIL, SINCE

        if type(tail) is not int or not 1 <= tail <= MAX_TAIL:
            raise DeploymentError(f"--tail must be 1..{MAX_TAIL}")
        if since is not None and not SINCE.fullmatch(since):
            raise DeploymentError("--since must look like 10m, 2h or 2026-11-11T09:00:00Z")
        if not isinstance(pod, str) or not _POD_NAME.fullmatch(pod):
            raise DeploymentError("Cannot read logs: no verified pod")
        argv = [
            "--namespace", self.namespace, "logs", f"pod/{pod}",
            "-c", "agent", "--timestamps", "--tail", str(tail),
        ]  # fmt: skip
        if since is not None:
            argv += ["--since-time", since] if "T" in since else ["--since", since]
        argv += ["--previous"] if previous else []
        argv += ["--follow"] if follow else []
        return self._binary(*argv)

    def logs(
        self,
        info: dict,
        tail: int = 200,
        since: str | None = None,
        previous: bool = False,
        pod: str | None = None,
    ) -> tuple[str, bool]:
        """The agent container's recent output (as `Docker.logs`: 4 MiB cap, whole lines, long
        lines' partial records joined) for `pod` (from `pod()`), after `authorize` and
        `inspect`. `previous`: the last terminated container (a crash loop). kubectl's stderr
        is never read into a message."""
        from agentbreeder.runtime import LOG_LIMIT, _read_tail, join_partials

        argv = self._log_argv(pod, tail, since, previous, follow=False)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._environment(),
            )
        except OSError as error:
            raise DeploymentError("Cannot run kubectl; is it installed?") from error
        try:
            result = _read_tail(process, LOG_LIMIT, timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        if result is None:
            raise DeploymentError("Reading the pod's logs timed out")
        if process.returncode:
            raise DeploymentError(
                "kubectl could not read the pod's logs"
                + (" (has the container restarted?)" if previous else "")
            )
        data, truncated = result
        if not truncated and _log_file_gone(data):
            raise LogUnavailable(
                "the kubelet no longer has the "
                + ("previous container's" if previous else "container's")
                + " log (the container or its log file is gone); nothing to show"
            )
        if truncated:
            data = data[data.find(b"\n") + 1 :] if b"\n" in data else b""
        data = b"\n".join(join_partials(line) for line in data.split(b"\n"))
        return data.decode("utf-8", "replace"), truncated

    def follow(
        self, info: dict, tail: int = 200, since: str | None = None, pod: str | None = None
    ) -> subprocess.Popen:
        """`kubectl logs --follow` of `pod`'s agent container, as `Docker.follow`
        (`runtime.start_follow`: in the caller's process group, stdin closed, kubectl's
        stderr discarded; stopped with `runtime.stop_follow`). It ends when the pod goes away; a
        replacement pod needs a new follow."""
        from agentbreeder.runtime import start_follow

        argv = self._log_argv(pod, tail, since, previous=False, follow=True)
        try:
            return start_follow(argv, self._environment())
        except OSError as error:
            raise DeploymentError("Cannot run kubectl; is it installed?") from error

    def remove(self, info: dict) -> None:
        self._delete(info["deployment"])

    def sweep(self, record: dict) -> None:
        """Delete by label when the Deployment is gone but other objects may remain."""
        self._delete(record["id"])

    def _delete(self, identifier: str) -> None:
        selector = f"{DEPLOYMENT_LABEL}={identifier}"
        # Foreground: the Deployment stays until the garbage collector has deleted what it owns
        # (its pods' ReplicaSet and its Secret), so an empty poll means the Secret is gone too.
        # The admission policy refuses any other propagation from the deployer (#770 §7.2).
        self._namespaced(
            "delete", _KINDS, "-l", selector, "--ignore-not-found", "--wait=true",
            "--cascade=foreground", timeout=120,
        )  # fmt: skip
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


_UID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_POD_NAME = re.compile(r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?")
_WORD = re.compile(r"[A-Za-z]{1,64}")
# What the kubelet sends instead of log lines, with kubectl exiting 0 (kind, node v1.37,
# 2026-11-13): `--previous` on a crash-looping pod whose last container the runtime no longer
# has, and a container whose log file is gone.
_GONE = re.compile(
    rb"unable to retrieve container logs for [a-z][a-z0-9-]{0,31}://[0-9a-f]{64}"
    rb'|failed to try resolving symlinks in path "[^"\n]{1,1024}": '
    rb"[^\n]{0,1024}no such file or directory"
)


class LogUnavailable(DeploymentError):
    """kubectl exited 0, but its output is the kubelet's message for a missing container or log
    file, not log lines."""


def _log_file_gone(data: bytes) -> bool:
    """Whether kubectl's whole output is one kubelet message for a missing container or log
    file. It never matches a container line: `--timestamps` starts each with a time."""
    if len(data) > 4096:
        return False
    lines = [line for line in data.split(b"\n") if line.strip()]
    return len(lines) == 1 and _GONE.fullmatch(lines[0].strip()) is not None


def _pod_view(pod: dict) -> dict:
    """What `Kubernetes.pod` needs from one pod, from its `agent` container only (a sidecar
    added later must not change the answer). `reason` is a fixed word or None when running."""
    metadata = pod["metadata"]
    status = pod.get("status") or {}
    agent = next(
        (c for c in status.get("containerStatuses") or [] if c.get("name") == "agent"), None
    )
    state = (agent or {}).get("state") or {}
    running = "running" in state
    restarted = int((agent or {}).get("restartCount") or 0) > 0
    reason = None
    if not running:
        words = [(state.get("waiting") or {}).get("reason")]
        words.append((state.get("terminated") or {}).get("reason"))
        words += [
            condition.get("reason")
            for condition in status.get("conditions") or []
            if isinstance(condition, dict) and condition.get("status") == "False"
        ]
        words.append(status.get("phase"))
        reason = next(
            (w for w in words if isinstance(w, str) and _WORD.fullmatch(w) and w != "Running"),
            "Pending",
        )
    return {
        "name": metadata["name"],
        "created": str(metadata.get("creationTimestamp") or ""),
        "running": running,
        # A first crash can wait in CrashLoopBackOff with restartCount still 0 and the exit only
        # under lastState: its output is readable too.
        "readable": running
        or "terminated" in state
        or restarted
        or "terminated" in ((agent or {}).get("lastState") or {}),
        "reason": reason,
    }


def kit_destinations(allowed, blocked) -> list[dict]:
    """The `ipBlock`s a kit deployment may reach: each allowed range, minus the blocked ranges
    inside it. A range that a blocked range covers is dropped. With nothing left the deploy is
    refused: default deny never falls back to the open rule."""
    result = []
    try:
        denied = [(item, ipaddress.ip_network(item, strict=False)) for item in blocked]
        for cidr in allowed:
            network = ipaddress.ip_network(cidr)
            if any(network.subnet_of(other) for _, other in denied):
                continue
            inside = [item for item, other in denied if other.subnet_of(network)]
            result.append({"ipBlock": {"cidr": cidr, **({"except": inside} if inside else {})}})
    except (ValueError, TypeError) as error:
        raise DeploymentError(
            "[KUBERNETES_KIT_EGRESS_UNCONFIGURED] agents_cidrs or blocked_cidrs is not a "
            "valid IPv4 range"
        ) from error
    if not result:
        raise DeploymentError(
            "[KUBERNETES_KIT_EGRESS_UNCONFIGURED] A kit runtime needs kubernetes.agents_cidrs "
            "(the stack's agents network); run `agentbreeder cluster up` again"
        )
    return result


def _labels(record, manifest) -> dict:
    return {
        MANAGED_BY: "agentbreeder",
        DEPLOYMENT_LABEL: record["id"],
        "io.agentbreeder/name": manifest.name,
    }


def _secret_data(mounts) -> dict[str, str]:
    try:
        return {
            mount.name: base64.b64encode(Path(mount.source).read_bytes()).decode("ascii")
            for mount in mounts
        }
    except OSError as error:
        raise DeploymentError("Cannot read a secret file for the deployment") from error


def _secret(record, manifest, namespace, data: dict[str, str], owner_uid: str) -> dict:
    """The deployment's own Secret, owned by its Deployment (`blockOwnerDeletion`, so the name
    is not free again until the garbage collector has deleted it; #770 §4.3)."""
    name = record["container"]
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": _labels(record, manifest),
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": name,
                    "uid": owner_uid,
                    "blockOwnerDeletion": True,
                }
            ],
        },
        "type": "Opaque",
        "immutable": True,
        "data": dict(data),
    }


def _objects(
    record, manifest, namespace, environment, mounts, blocked, allowed=None
) -> list[dict]:
    """The Service, NetworkPolicy and Deployment of a deployment, in the one shape the admission
    policies admit (#770 §7.2). Its Secret (`_secret`) is created after the Deployment."""
    identifier, name = record["id"], record["container"]
    labels = _labels(record, manifest)
    selector = {DEPLOYMENT_LABEL: identifier}
    metadata = {"name": name, "namespace": namespace, "labels": labels}
    variables = dict(environment or {})
    for mount in mounts:
        for env in mount.envs:
            variables[f"{env}_FILE"] = mount.target
    volumes: list[dict] = [_copy(TMP_VOLUME)]
    volume_mounts: list[dict] = [{"name": "tmp", "mountPath": "/tmp"}]
    objects: list[dict] = []
    if mounts:
        # One projected volume at MOUNT_ROOT, so environment bindings by reference (K5) join
        # the own Secret's files as further sources instead of nesting mounts.
        volumes.append(
            {
                "name": "secrets",
                "projected": {"defaultMode": SECRET_MODE, "sources": [{"secret": {"name": name}}]},
            }
        )
        volume_mounts.append({"name": "secrets", "mountPath": MOUNT_ROOT, "readOnly": True})
    cli: list[dict] = []
    if manifest.runtime is not None and manifest.runtime.isolation == "two-uid":
        # #1089, owner decision D15: the framework's subprocess (the Claude Code CLI) runs in a
        # second container from the same image, as its own uid, with no secret volume and no
        # environment but its role. The two share only a small memory-backed directory; the
        # runtime checks every peer's kernel credentials over it. In a pod both containers
        # share the network namespace (on local Docker the CLI container has none).
        volumes += [_copy(CLI_TMP_VOLUME), _copy(CLI_SHARED_VOLUME)]
        volume_mounts.append({"name": "cli-shared", "mountPath": CLI_SHARED_DIR})
        cli.append(
            {
                "name": "cli",
                "image": manifest.image,
                "imagePullPolicy": "IfNotPresent",
                "env": [{"name": "AGENTBREEDER_ROLE", "value": "cli-host"}],
                "securityContext": _copy(CLI_SECURITY_CONTEXT),
                "resources": _copy(RESOURCES),
                # Read-only: the CLI only connects to the kit's sockets. The emptyDir itself
                # is world-writable, so a writable mount would let the CLI's uid replace them.
                "volumeMounts": [
                    {"name": "cli-tmp", "mountPath": "/tmp"},
                    {"name": "cli-shared", "mountPath": CLI_SHARED_DIR, "readOnly": True},
                ],
            }
        )
    objects.append(
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": metadata,
            "spec": {
                "type": "ClusterIP",
                "selector": selector,
                "ports": [{"port": manifest.port, "targetPort": manifest.port, "protocol": "TCP"}],
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
                    {"to": [_copy(DNS_PEER)], "ports": _copy(DNS_PORTS)},
                    {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": list(blocked)}}]}
                    if allowed is None
                    else {"to": list(allowed)},
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
                        "securityContext": _copy(POD_SECURITY_CONTEXT),
                        "containers": [
                            {
                                "name": "agent",
                                "image": manifest.image,
                                "imagePullPolicy": "IfNotPresent",
                                "ports": [{"containerPort": manifest.port, "protocol": "TCP"}],
                                "env": [
                                    {"name": key, "value": value}
                                    for key, value in sorted(variables.items())
                                ],
                                "securityContext": _copy(CONTAINER_SECURITY_CONTEXT),
                                "resources": _copy(RESOURCES),
                                "readinessProbe": {
                                    "httpGet": {
                                        "path": manifest.health_path,
                                        "port": manifest.port,
                                    },
                                    **READINESS,
                                },
                                "volumeMounts": volume_mounts,
                            },
                            *cli,
                        ],
                        "volumes": volumes,
                    },
                },
            },
        }
    )
    return objects


def _copy(value):
    return json.loads(json.dumps(value))
