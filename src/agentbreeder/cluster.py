"""`agentbreeder cluster`: a local kind cluster for one stack (spec §7.8).

Local only. It creates a kind cluster and a registry, both under fixed AgentBreeder names and
pinned image digests, prepares a `restricted` namespace, and attaches the node to the stack's
agents network, so pods reach the stack's services under the same names a local container does.
It only ever touches resources with those names. Services still run in Docker: this demonstrates
the deployer, it is not a production topology.

With `--restricted` (#770 K2, preview, not yet run on kind) it also installs the service accounts
and Roles of #770 §7.1, the admission policies of §7.2 and their `ab-grants` parameters, and
records a kubeconfig for the `ab-deployer` service account instead of the admin one, so deploys on
the laptop meet the same RBAC and policies as on a cluster.
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

from agentbreeder import kubernetes_admission as admission
from agentbreeder.kubernetes import DEPLOYMENT_LABEL, MANAGED_BY, POD_SECURITY
from agentbreeder.kubernetes_providers import KIND, Binding, KindProvider
from agentbreeder.operator_config import agents_range
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
RESTRICTED = "kubernetes_restricted"  # state key: `cluster up --restricted` ran
DEPLOYER_KUBECONFIG = "kubeconfig-deployer"
# The deployer token's lifetime; `cluster up --restricted` mints a new one on every run.
TOKEN_DURATION = "720h"
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


def agents_cidrs(agents_network: str) -> list[str]:
    """The IPv4 ranges of the stack's agents network: the only egress, besides cluster DNS,
    of a deployment whose manifest says `runtime.profile: kit` (#1083 F1b)."""
    ipam = json.loads(
        _run(
            ["docker", "network", "inspect", agents_network, "--format", "{{json .IPAM.Config}}"],
            what="inspecting the agents network",
        )
    )
    # A range the operator config would refuse is left out: a kit deploy is then refused
    # (KUBERNETES_KIT_EGRESS_UNCONFIGURED) and every other deployment is unaffected.
    return [item["Subnet"] for item in ipam or [] if agents_range(item.get("Subnet", ""))]


def block_for(state, agents_network: str, *, restricted: bool = False) -> dict:
    """The `kubernetes` block for this stack: cluster prepared, node attached to its network.
    With `restricted`, deploys use the `ab-deployer` service account's kubeconfig."""
    path = state.directory / "kubeconfig"
    write_kubeconfig(path)
    ensure_namespace(path)
    sweep_legacy_secrets(path)
    uid = kubectl(path, "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}")
    connect(NODE, agents_network)
    # Measured at connect through the provider (#770 §4.1), from the binding being written.
    provider = KindProvider(
        Binding(KIND, uid, NAMESPACE, {"context": CONTEXT, "kubeconfig": str(path)})
    )
    block = {
        "kubeconfig": str(path),
        "context": CONTEXT,
        "cluster_uid": uid,
        "namespace": NAMESPACE,
        "registry": f"localhost:{REGISTRY_PORT}/{NAME}",
        "blocked_cidrs": provider.blocked_cidrs(None, agents_network=agents_network),
        "agents_cidrs": agents_cidrs(agents_network),
    }
    if restricted:
        block["kubeconfig"] = str(install_restricted(path, state.directory / DEPLOYER_KUBECONFIG))
    return block


def sweep_legacy_secrets(path: Path) -> list[str]:
    """Delete the Secrets deployments made before #770 K2 left behind: created beside their
    Deployment without an owner, so removing the Deployment no longer deletes them. Only a
    Secret with AgentBreeder's labels and name, no owner, and no Deployment of that name."""
    selector = f"{MANAGED_BY}=agentbreeder,{DEPLOYMENT_LABEL}"
    # Names and owner kinds only: the Secrets' values never reach this process.
    listed = kubectl(
        path, "get", "secrets", "--namespace", NAMESPACE, "--selector", selector, "-o",
        'jsonpath={range .items[*]}{.metadata.name}{" "}{.metadata.ownerReferences[*].kind}'
        '{"\\n"}{end}',
    )  # fmt: skip
    deployments = set(
        kubectl(
            path, "get", "deployments", "--namespace", NAMESPACE, "--selector", selector,
            "-o", "jsonpath={.items[*].metadata.name}",
        ).split()
    )  # fmt: skip
    removed = []
    for line in listed.splitlines():
        name, _, owners = line.strip().partition(" ")
        if (
            re.fullmatch(r"agentbreeder-[0-9a-f]{32}", name)
            and not owners.strip()
            and name not in deployments
        ):
            kubectl(path, "delete", "secret", name, "--namespace", NAMESPACE, "--ignore-not-found")
            removed.append(name)
    return removed


def server_version(path: Path) -> tuple[int, int]:
    try:
        info = json.loads(kubectl(path, "version", "-o", "json"))["serverVersion"]
        return int(info["major"]), int(re.match(r"[0-9]+", info["minor"])[0])
    except (ValueError, KeyError, TypeError) as error:
        raise ServiceError("CLUSTER_FAILED", "Cannot read the cluster's version") from error


def install_restricted(admin: Path, deployer: Path) -> Path:
    """Install the §7.1 accounts and Roles and the §7.2 policies with the admin kubeconfig, then
    write `deployer`: a private kubeconfig for `ab-deployer` with a bound token. The token goes
    from kubectl's stdout into the file, never to argv or the log."""
    version = server_version(admin)
    if version < admission.MINIMUM_VERSION:
        raise ServiceError(
            "CLUSTER_VERSION_OLD",
            "the restricted accounts need Kubernetes 1.30 or later (ValidatingAdmissionPolicy)",
        )
    for objects in (admission.roles(NAMESPACE), admission.policies(NAMESPACE)):
        document = {"apiVersion": "v1", "kind": "List", "items": objects}
        kubectl(admin, "apply", "-f", "-", stdin=json.dumps(document).encode())
    token = kubectl(
        admin, "create", "token", admission.account("deployer"), "--namespace", NAMESPACE,
        "--duration", TOKEN_DURATION,
    )  # fmt: skip
    if not re.fullmatch(r"[A-Za-z0-9._-]{20,8192}", token):
        raise ServiceError("CLUSTER_FAILED", "kubectl returned no service account token")
    config = yaml.safe_load(admin.read_text())
    context = next(c["context"] for c in config["contexts"] if c["name"] == CONTEXT)
    cluster = next(c for c in config["clusters"] if c["name"] == context["cluster"])
    account = admission.account("deployer")
    document = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [cluster],
        "users": [{"name": account, "user": {"token": token}}],
        "contexts": [
            {
                "name": CONTEXT,
                "context": {"cluster": cluster["name"], "user": account, "namespace": NAMESPACE},
            }
        ],
        "current-context": CONTEXT,
    }
    write_private(deployer, yaml.safe_dump(document).encode())
    return deployer


def grants_for(state, block: dict) -> admission.Grants:
    """`ab-grants` on kind: the block's registry and ranges, and an empty grant per agent the
    stack's environments name (services stay in Docker, so no agent uses an in-cluster
    service, binding by reference, store or pod identity there)."""
    agents: set[str] = set()
    for environment in state.environments():
        path = state.env_dir(environment) / "operator.yaml"
        if path.exists():
            config = (yaml.safe_load(path.read_text()) or {}).get("environments", {})
            agents |= set(((config.get(environment) or {}).get("agents") or {}).keys())
    return admission.Grants(
        registry=block["registry"],
        blocked=tuple(block["blocked_cidrs"]),
        agentnet=tuple(block.get("agents_cidrs") or ()),
        agents={name: admission.AgentGrant() for name in sorted(agents)},
    )


def write_grants(state) -> None:
    """Write `ab-grants` (admin kubeconfig) when `cluster up --restricted` ran for this stack."""
    block = json.loads(state.get("kubernetes") or "null")
    if not block or state.get(RESTRICTED) != "1":
        return
    try:
        configmap = admission.grants_configmap(NAMESPACE, grants_for(state, block))
    except admission.AdmissionError as error:
        raise ServiceError("CLUSTER_FAILED", str(error)) from None
    kubectl(
        state.directory / "kubeconfig", "apply", "-f", "-", stdin=json.dumps(configmap).encode()
    )


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
    project: Path,
    environment: str,
    *,
    confirm=None,
    expect_grant=None,
    restricted: bool = False,
    log=lambda _: None,
) -> dict:
    """Prepare the cluster and record it. With `confirm`, also accept the changed grant; without
    it the caller reviews the grant (`services plan`) and confirms with `services up`."""
    require_tools()
    with cluster_lock():
        return _up(
            project,
            environment,
            confirm=confirm,
            expect_grant=expect_grant,
            restricted=restricted,
            log=log,
        )


def _up(project: Path, environment: str, *, confirm, expect_grant, restricted, log) -> dict:
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
            if restricted != (engine.state.get(RESTRICTED) == "1"):
                # A deployment records its kubeconfig path; switching accounts under it would
                # make its next redeploy read as another cluster (KUBERNETES_BINDING_CHANGED).
                active = engine.active_cluster_deployments()
                if active:
                    raise ServiceError(
                        "CLUSTER_IN_USE",
                        "remove these cluster deployments before switching --restricted "
                        "(remove ID --cluster): " + ", ".join(active),
                    )
            if restricted:
                log("Installing the restricted accounts and admission policies ...")
            block = block_for(engine.state, network, restricted=restricted)
            engine.state.put("kubernetes", json.dumps(block))
            engine.state.put(RESTRICTED, "1" if restricted else "")
            if confirm is None:
                # ab-grants is written by `services up` once the operator config holds this
                # block, so the two never disagree (until then the policies deny).
                return {"cluster": CONTEXT, "grant": "review the change with services plan"}
            log("Recording the cluster in the stack's grant ...")
            # `engine.up` writes ab-grants once the operator config names the agents.
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
            (engine.state.directory / DEPLOYER_KUBECONFIG).unlink(missing_ok=True)
            engine.state.put("kubernetes", "")
            engine.state.put(RESTRICTED, "")
            return {"removed": [NAME, REGISTRY]}
        finally:
            engine.close()
