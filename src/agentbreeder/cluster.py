"""`agentbreeder cluster`: a local kind cluster for one stack (spec §7.8).

Local only. It creates a kind cluster and a registry, both under fixed AgentBreeder names and
pinned image digests, prepares a `restricted` namespace, and attaches the node to the stack's
agents network, so pods reach the stack's services under the same names a local container does.
It only ever touches resources with those names. Services still run in Docker: this demonstrates
the deployer, it is not a production topology.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml

from agentbreeder.kubernetes import MANAGED_BY, POD_SECURITY
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import private_dir, write_private

NAME = "agentbreeder"
NODE = f"{NAME}-control-plane"
CONTEXT = f"kind-{NAME}"
REGISTRY = "agentbreeder-registry"
REGISTRY_PORT = 5001
NAMESPACE = "agentbreeder"
NODE_IMAGE = (
    "kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
)
REGISTRY_IMAGE = (
    "registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
)
MIN_KIND = (0, 33)  # the release whose NetworkPolicy enforcement was measured
OWNER_LABEL = "io.agentbreeder/owner"
POD_CIDR = "10.244.0.0/16"
SERVICE_CIDR = "10.96.0.0/16"
METADATA_CIDR = "169.254.169.254/32"
KIND_CONFIG = f"""kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  podSubnet: {POD_CIDR}
  serviceSubnet: {SERVICE_CIDR}
nodes:
- role: control-plane
  labels:
    {OWNER_LABEL}: "{NAME}"
containerdConfigPatches:
- |-
  [plugins."io.containerd.grpc.v1.cri".registry]
    config_path = "/etc/containerd/certs.d"
"""
INSTALL = {
    "kind": "https://kind.sigs.k8s.io/docs/user/quick-start/#installation",
    "kubectl": "https://kubernetes.io/docs/tasks/tools/",
}


def _run(argv: list[str], *, stdin: bytes | None = None, timeout: int = 120, what: str) -> str:
    """Run a tool. Its output is not echoed on failure: it can carry local details."""
    try:
        result = subprocess.run(argv, capture_output=True, input=stdin, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise ServiceError("CLUSTER_FAILED", f"{what} failed or timed out") from None
    if result.returncode:
        raise ServiceError("CLUSTER_FAILED", f"{what} failed (exit {result.returncode})")
    return result.stdout.decode("utf-8", "replace").strip()


def tools() -> dict[str, dict]:
    """Which external tools are present, and how to get the missing ones."""
    found = {}
    for tool, url in INSTALL.items():
        found[tool] = {"present": shutil.which(tool) is not None, "install": url}
    if found["kind"]["present"]:
        found["kind"]["supported"] = _kind_supported()
    return found


def _kind_supported() -> bool:
    try:
        output = _run(["kind", "version"], what="kind version")
    except ServiceError:
        return False
    match = re.search(r"v([0-9]+)\.([0-9]+)", output)
    return match is not None and (int(match[1]), int(match[2])) >= MIN_KIND


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        raise ServiceError("CLUSTER_TOOL_MISSING", f"{tool} is not on PATH; {INSTALL[tool]}")


def require_tools() -> None:
    state = tools()
    for tool, info in state.items():
        if not info["present"]:
            raise ServiceError("CLUSTER_TOOL_MISSING", f"{tool} is not on PATH; {info['install']}")
    if not state["kind"].get("supported"):
        raise ServiceError(
            "CLUSTER_TOOL_OLD",
            f"kind {MIN_KIND[0]}.{MIN_KIND[1]} or later is required: network policy was "
            "measured on that release",
        )


def _exists(*argv: str, what: str) -> bool:
    try:
        _run(list(argv), what=what)
    except ServiceError:
        return False
    return True


def _clusters() -> list[str]:
    return _run(["kind", "get", "clusters"], what="kind get clusters").splitlines()


def _registry_owned() -> bool | None:
    """None if no such container; False if something else holds the name."""
    if not _exists("docker", "container", "inspect", REGISTRY, what="inspect registry"):
        return None
    label = _run(
        [
            "docker",
            "container",
            "inspect",
            "--format",
            '{{index .Config.Labels "' + OWNER_LABEL + '"}}',
            REGISTRY,
        ],
        what="inspect registry",
    )
    return label == NAME


def ensure_registry() -> None:
    owned = _registry_owned()
    if owned is False:
        raise ServiceError("CLUSTER_NAME_TAKEN", f"A container named {REGISTRY} already exists")
    if owned is None:
        _run(
            [
                "docker",
                "run",
                "--detach",
                "--restart=unless-stopped",
                "--label",
                f"{OWNER_LABEL}={NAME}",
                "--name",
                REGISTRY,
                "--publish",
                f"127.0.0.1:{REGISTRY_PORT}:5000",
                REGISTRY_IMAGE,
            ],
            timeout=300,
            what="starting the local registry",
        )


def _require_owned(path: Path) -> None:
    """A cluster with our name that AgentBreeder did not create is never changed or deleted."""
    write_kubeconfig(path)
    selector = f"{OWNER_LABEL}={NAME}"
    if not kubectl(path, "get", "nodes", "--selector", selector, "-o", "name"):
        raise ServiceError("CLUSTER_NAME_TAKEN", f"A different kind cluster is named {NAME}")


def ensure_cluster(path: Path) -> None:
    if NAME in _clusters():
        _require_owned(path)
    else:
        _run(
            [
                "kind",
                "create",
                "cluster",
                "--name",
                NAME,
                "--image",
                NODE_IMAGE,
                "--wait",
                "180s",
                "--config",
                "-",
            ],
            stdin=KIND_CONFIG.encode(),
            timeout=600,
            what="creating the kind cluster",
        )
    hosts = f'[host."http://{REGISTRY}:5000"]\n'.encode()
    directory = f"/etc/containerd/certs.d/localhost:{REGISTRY_PORT}"
    _run(
        [
            "docker",
            "exec",
            "--interactive",
            NODE,
            "sh",
            "-c",
            f"mkdir -p {directory} && cat > {directory}/hosts.toml",
        ],
        stdin=hosts,
        what="configuring the registry mirror",
    )
    connect(REGISTRY, "kind")


def connect(container: str, network: str) -> None:
    """Attach a container to a network; already attached is fine."""
    attached = json.loads(_networks(container))
    if network not in attached:
        _run(
            ["docker", "network", "connect", network, container],
            what=f"connecting {container} to its network",
        )


def _networks(container: str) -> str:
    return _run(
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", container],
        what=f"inspecting {container}",
    )


def write_kubeconfig(path: Path) -> None:
    config = _run(["kind", "get", "kubeconfig", "--name", NAME], what="reading the kubeconfig")
    write_private(path, config.encode())


def kubectl(path: Path, *arguments: str, stdin: bytes | None = None) -> str:
    return _run(
        ["kubectl", "--kubeconfig", str(path), "--context", CONTEXT, *arguments],
        stdin=stdin,
        what=f"kubectl {arguments[0]}",
    )


def ensure_namespace(path: Path) -> None:
    document = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": NAMESPACE,
            "labels": {MANAGED_BY: "agentbreeder", POD_SECURITY: "restricted"},
        },
    }
    kubectl(path, "apply", "-f", "-", stdin=json.dumps(document).encode())


def blocked_cidrs(agents_network: str) -> list[str]:
    """Egress the agent's NetworkPolicy denies: pods, services, metadata, the kind network (node
    and registry), and the node's address on the agents network."""
    ipam = json.loads(
        _run(
            ["docker", "network", "inspect", "kind", "--format", "{{json .IPAM.Config}}"],
            what="inspecting the kind network",
        )
    )
    blocked = [POD_CIDR, SERVICE_CIDR, METADATA_CIDR]
    blocked += [item["Subnet"] for item in ipam if ":" not in item.get("Subnet", ":")]
    address = (json.loads(_networks(NODE)).get(agents_network) or {}).get("IPAddress")
    if address:
        blocked.append(f"{address}/32")
    return blocked


def block_for(state, agents_network: str) -> dict:
    """The `kubernetes` block for this stack: cluster prepared, node attached to its network."""
    path = state.directory / "kubeconfig"
    write_kubeconfig(path)
    ensure_namespace(path)
    uid = kubectl(path, "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}")
    connect(NODE, agents_network)
    return {
        "kubeconfig": str(path),
        "context": CONTEXT,
        "cluster_uid": uid,
        "namespace": NAMESPACE,
        "registry": f"localhost:{REGISTRY_PORT}/{NAME}",
        "blocked_cidrs": blocked_cidrs(agents_network),
    }


LOCK_NAME = "cluster.lock"


def lock_path() -> Path:
    """`~/.agentbreeder/locks/cluster.lock`: per user and fixed (it does not follow
    AGENTBREEDER_STACKS_DIR), because the kind cluster and registry are one per machine."""
    return private_dir(Path.home() / ".agentbreeder" / "locks") / LOCK_NAME


@contextmanager
def cluster_lock() -> Iterator[None]:
    """One `cluster up` or `cluster down` at a time on this machine (security finding S-11):
    the kind cluster and registry are machine-wide, so the console's in-process locks cannot
    order a terminal run against a console job. A second run fails fast instead of waiting."""
    path = lock_path()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        raise ServiceError("STATE_UNSAFE", f"cannot open {path}") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ServiceError("STATE_UNSAFE", f"{path} must be a 0600 file you own")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ServiceError(
                "CLUSTER_BUSY",
                "another `agentbreeder cluster up` or `cluster down` is running; retry when it "
                "finishes",
            ) from None
        yield
    finally:
        os.close(descriptor)


def up(
    project: Path, environment: str, *, confirm=None, expect_grant=None, log=lambda _: None
) -> dict:
    """Prepare the cluster and record it. With `confirm`, also accept the changed grant; without
    it the caller reviews the grant (`services plan`) and confirms with `services up`."""
    require_tools()
    with cluster_lock():
        return _up(project, environment, confirm=confirm, expect_grant=expect_grant, log=log)


def _up(project: Path, environment: str, *, confirm, expect_grant, log) -> dict:
    from agentbreeder.services.engine import Engine, check_locked, write_lock

    with write_lock(project) as locked:  # S-11: cluster lock, then stack lock, before Engine()
        engine = Engine(project, log=log)
        try:
            check_locked(engine, locked)
            network = engine.state.project + "-agents"
            if not _exists(
                "docker", "network", "inspect", network, what="inspect the agents network"
            ):
                raise ServiceError(
                    "STACK_NOT_UP", "the stack's agents network is missing; run services up first"
                )
            log("Starting the local registry ...")
            ensure_registry()
            log("Creating the kind cluster (the first run pulls its image) ...")
            ensure_cluster(engine.state.directory / "kubeconfig")
            engine.state.put("kubernetes", json.dumps(block_for(engine.state, network)))
            if confirm is None:
                return {"cluster": CONTEXT, "grant": "review the change with services plan"}
            log("Recording the cluster in the stack's grant ...")
            return engine.up(environment, confirm=confirm, expect_grant=expect_grant)
        finally:
            engine.close()


def _in_operator_config(state) -> bool:
    """Whether a confirmed grant has put this exact cluster in the operator config."""
    block = json.loads(state.get("kubernetes") or "null")
    if not block:
        return False
    for environment in state.environments():
        path = state.env_dir(environment) / "operator.yaml"
        if not path.exists():
            continue
        config = (yaml.safe_load(path.read_text()) or {}).get("environments", {})
        target = (config.get(environment) or {}).get("kubernetes") or {}
        if target.get("cluster_uid") == block["cluster_uid"]:
            return True
    return False


def status(project: Path) -> dict:
    from agentbreeder.services.engine import Engine

    engine = Engine(project)
    try:
        state = {"tools": tools(), "configured": _in_operator_config(engine.state)}
        state["cluster"] = bool(state["tools"]["kind"]["present"]) and NAME in _clusters()
        state["registry"] = _exists("docker", "container", "inspect", REGISTRY, what="registry")
        return state
    finally:
        engine.close()


def down(project: Path) -> dict:
    with cluster_lock():
        return _down(project)


def _down(project: Path) -> dict:
    from agentbreeder.services.engine import Engine, check_locked, write_lock

    _require("kind")  # as `cluster up`: name the missing tool, not a failed `kind get clusters`
    with write_lock(project) as locked:  # S-11: cluster lock, then stack lock, before Engine()
        engine = Engine(project)
        try:
            check_locked(engine, locked)
            active = engine.active_cluster_deployments()
            if active:
                raise ServiceError(
                    "CLUSTER_IN_USE",
                    "remove these cluster deployments first (remove ID --cluster): "
                    + ", ".join(active),
                )
            if NAME in _clusters():
                _require("kubectl")
                path = engine.state.directory / "kubeconfig"
                _require_owned(path)  # the cluster is machine-wide: other stacks may use it
                workloads = kubectl(
                    path,
                    "get",
                    "deployments",
                    "--namespace",
                    NAMESPACE,
                    "--selector",
                    f"{MANAGED_BY}=agentbreeder",
                    "--ignore-not-found",
                    "-o",
                    "name",
                )
                if workloads:
                    raise ServiceError(
                        "CLUSTER_IN_USE", "Agents are still deployed on the cluster"
                    )
                _run(
                    ["kind", "delete", "cluster", "--name", NAME], timeout=180, what="kind delete"
                )
            if _registry_owned():
                # registry:2 declares VOLUME /var/lib/registry; without --volumes it is orphaned
                _run(
                    ["docker", "rm", "--force", "--volumes", REGISTRY],
                    what="removing the registry",
                )
            (engine.state.directory / "kubeconfig").unlink(missing_ok=True)
            engine.state.put("kubernetes", "")
            return {"removed": [NAME, REGISTRY]}
        finally:
            engine.close()
