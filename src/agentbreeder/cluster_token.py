"""`agentbreeder cluster connect token`: record an existing conformant cluster (#770 §4.2, K1).

`connect` reads the operator's dedicated mint kubeconfig, cross-checks its CA with the chain the
API server presents (and refuses a server whose certificate chains to a public CA), records the
tools by absolute path and hash, then, with tokens it mints for the named service accounts:

1. reads as the **deployer** the `kube-system` UID, the namespace's labels, the Kubernetes
   version (1.30 or later) and the namespace's `ResourceQuota` (it must cap Secrets and
   Deployments, #770 T14);
2. computes `blocked_cidrs` as the **installer** (§6.4): the operator's pod, service and node
   ranges, cross-checked against what the cluster shows, every address the API server's name
   resolves to, the metadata address and the private ranges;
3. checks the §7.2 admission policies (`kubernetes_token.admission_policy`). That check is not
   wired in yet (K2 built the policies, not this check): it refuses, so this command runs every
   other check and then records nothing.

What it does not do yet (later slices of #770): mirror the `echo` image, write `ab-baseline` and
`ab-grants`, the paired policy probes, the RBAC probes (K2), the canary (K6), registry
credentials (K8). It writes nothing to the cluster or to disk except its session directories.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import ssl
import subprocess
import textwrap
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agentbreeder import kubernetes_token
from agentbreeder.kubernetes import MANAGED_BY, POD_SECURITY
from agentbreeder.kubernetes_providers import Binding
from agentbreeder.kubernetes_token import TOKEN, TokenProvider, file_sha256, parse_mint
from agentbreeder.operator_config import (
    TOKEN_CONTEXT,
    KubernetesTarget,
    OperatorConfigError,
    api_server_refusal,
    read_private,
)
from agentbreeder.store import DeploymentError

MIN_VERSION = (1, 30)
METADATA = "169.254.169.254/32"
# Always blocked: a customer network is reachable through them (#908 §6).
PRIVATE = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16")
MAX_RANGES = 32
TLS_TIMEOUT = 10
_QUOTA_KEYS = ("count/secrets", "count/deployments.apps")


def _error(code: str, message: str) -> DeploymentError:
    return DeploymentError(f"[{code}] {message}")


def kubectl(session, *arguments: str, run: Callable = subprocess.run, timeout: int = 30) -> str:
    """One kubectl call in a provider session. stderr is never copied into an error."""
    if session.verify is not None:
        session.verify()
    try:
        result = run(
            [session.binary, *session.argv, *arguments],
            capture_output=True,
            timeout=timeout,
            check=False,
            env=dict(session.env),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise _error("KUBERNETES_UNREACHABLE", "kubectl failed or timed out") from None
    if result.returncode:
        raise DeploymentError("The cluster rejected the operation; check the cluster and RBAC")
    output = result.stdout
    return (output.decode("utf-8", "replace") if isinstance(output, bytes) else output).strip()


def _json(session, *arguments: str, run: Callable) -> Any:
    try:
        return json.loads(kubectl(session, *arguments, "-o", "json", run=run))
    except ValueError:
        raise _error("KUBERNETES_UNREADABLE", "The cluster returned unreadable output") from None


# -- the endpoint ---------------------------------------------------------------------------------


def _host_port(server: str) -> tuple[str, int]:
    parts = urlsplit(server)
    return parts.hostname or "", parts.port or 443


def tls_check(server: str, ca_pem: str, tls_server_name: str | None) -> None:
    """The server's chain must verify against the pinned CA (KUBERNETES_CA_MISMATCH), and must
    not verify against the system trust store (KUBERNETES_CA_PUBLIC): a pin on a public CA would
    accept any host it ever signed. Direct connections only, never a proxy."""
    host, port = _host_port(server)

    def handshake(context: ssl.SSLContext, name: str | None) -> None:
        with socket.create_connection((host, port), timeout=TLS_TIMEOUT) as raw:
            with context.wrap_socket(raw, server_hostname=name):
                pass

    pinned = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    pinned.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        pinned.load_verify_locations(cadata=ca_pem)
    except ssl.SSLError:
        raise _error(
            "KUBERNETES_CA_INVALID", "The mint kubeconfig's CA cannot be loaded"
        ) from None
    try:
        handshake(pinned, tls_server_name or host)
    except ssl.SSLCertVerificationError:
        raise _error(
            "KUBERNETES_CA_MISMATCH",
            "The API server's certificate does not verify against the mint kubeconfig's CA "
            "for this server name",
        ) from None
    except (OSError, ssl.SSLError):
        raise _error("KUBERNETES_UNREACHABLE", "Cannot reach the API server over TLS") from None
    public = ssl.create_default_context()
    public.check_hostname = False  # only asks whether the chain is public; never trusted
    if not _has_trust_store():
        # An empty store would read every server as "not public" and fail open.
        raise _error(
            "KUBERNETES_TRUST_STORE_EMPTY",
            "Python has no system CA store to check the API server against public CAs; install "
            "one (for python.org Python on macOS: Install Certificates.command)",
        )
    try:
        # The same server name as the pinned handshake, so a server that picks its
        # certificate by SNI is checked on the certificate it serves for that name.
        handshake(public, tls_server_name or host)
    except ssl.SSLCertVerificationError:
        return
    except (OSError, ssl.SSLError):
        raise _error("KUBERNETES_UNREACHABLE", "Cannot reach the API server over TLS") from None
    raise _error(
        "KUBERNETES_CA_PUBLIC",
        "The API server's certificate chains to a public CA; pin a cluster with its own CA",
    )


def _has_trust_store() -> bool:
    """Whether the default context has any CA to load: a CA file, or a CA directory that has
    files (OpenSSL loads a directory lazily, so `cert_store_stats` cannot tell)."""
    paths = ssl.get_default_verify_paths()
    if paths.cafile and os.path.isfile(paths.cafile):
        return True
    try:
        return bool(paths.capath) and any(os.scandir(paths.capath))
    except OSError:
        return False


def resolve_ipv4(host: str) -> list[str]:
    """Every IPv4 address `host` resolves to (the address itself when it is one)."""
    try:
        return [str(ipaddress.IPv4Address(host))]
    except ValueError:
        pass
    try:
        found = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        raise _error("KUBERNETES_UNREACHABLE", "Cannot resolve the API server's name") from None
    return sorted({item[4][0] for item in found})


def ca_fingerprints(ca_pem: str) -> list[str]:
    """Each CA certificate's SHA-256 fingerprint as `openssl x509 -fingerprint -sha256` prints
    it, for the review's second read."""
    from agentbreeder.operator_config import pem_certificates

    return [
        ":".join(f"{byte:02X}" for byte in hashlib.sha256(der).digest())
        for der in pem_certificates(ca_pem)
    ]


def _pem(certificates: Iterable[bytes]) -> str:
    blocks = []
    for der in certificates:
        body = "\n".join(textwrap.wrap(base64.b64encode(der).decode(), 64))
        blocks.append(f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n")
    return "".join(blocks)


# -- blocked_cidrs (§6.4) -------------------------------------------------------------------------


def _network(value: str, what: str) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError:
        raise _error("KUBERNETES_RANGES_INVALID", f"{what} {value!r} is not a network") from None
    if network.version != 4:
        raise _error(
            "KUBERNETES_DUAL_STACK_UNSUPPORTED", "IPv6 and dual-stack clusters are not supported"
        )
    return network


def _address(value: Any) -> ipaddress.IPv4Address:
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        raise _error(
            "KUBERNETES_UNREADABLE", "The cluster returned an unreadable address"
        ) from None
    if address.version != 4:
        raise _error(
            "KUBERNETES_DUAL_STACK_UNSUPPORTED", "IPv6 and dual-stack clusters are not supported"
        )
    return address


def _within(address, networks) -> bool:
    return any(address in network for network in networks)


def blocked_cidrs(
    session,
    *,
    pod_cidrs: list[str],
    service_cidr: str,
    server: str,
    node_cidrs: list[str] | None = None,
    run: Callable = subprocess.run,
    resolve: Callable[[str], list[str]] = resolve_ipv4,
) -> list[str]:
    """§6.4, read with the installer's session. Operator ranges are cross-checked, not trusted:
    a node's pod range, a pod IP or a ClusterIP outside them is KUBERNETES_RANGES_INCOMPLETE."""
    if not pod_cidrs:
        raise _error("KUBERNETES_RANGES_INVALID", "At least one --pod-cidr is required")
    pods = [_network(value, "--pod-cidr") for value in pod_cidrs]
    services = _network(service_cidr, "--service-cidr")
    nodes_given = [_network(value, "--node-cidr") for value in node_cidrs or ()]
    try:
        node_items = _json(session, "get", "nodes", run=run)["items"]
        namespace_pods = _json(session, "get", "pods", run=run)["items"]
        namespace_services = _json(session, "get", "services", run=run)["items"]
        node_addresses: list[ipaddress.IPv4Address] = []
        for node in node_items:
            spec = node.get("spec") or {}
            for value in spec.get("podCIDRs") or (
                [spec["podCIDR"]] if spec.get("podCIDR") else []
            ):
                found = ipaddress.ip_network(value, strict=False)
                if found.version != 4:
                    raise _error(
                        "KUBERNETES_DUAL_STACK_UNSUPPORTED",
                        "IPv6 and dual-stack clusters are not supported",
                    )
                if not any(found.subnet_of(network) for network in pods):
                    raise _error(
                        "KUBERNETES_RANGES_INCOMPLETE",
                        "A node's pod range is outside every --pod-cidr",
                    )
            for entry in (node.get("status") or {}).get("addresses") or []:
                if entry.get("type") in ("InternalIP", "ExternalIP"):
                    address = _address(entry["address"])
                    if nodes_given and not _within(address, nodes_given):
                        raise _error(
                            "KUBERNETES_RANGES_INCOMPLETE",
                            "A node's address is outside every --node-cidr",
                        )
                    node_addresses.append(address)
        for pod in namespace_pods:
            status = pod.get("status") or {}
            values = [item.get("ip") for item in status.get("podIPs") or []]
            values += [status["podIP"]] if status.get("podIP") else []
            for value in filter(None, values):
                if not _within(_address(value), pods):
                    raise _error(
                        "KUBERNETES_RANGES_INCOMPLETE",
                        "A pod in the namespace has an address outside every --pod-cidr",
                    )
        for service in namespace_services:
            spec = service.get("spec") or {}
            for value in spec.get("clusterIPs") or [spec.get("clusterIP")]:
                if value in (None, "", "None"):
                    continue
                if _address(value) not in services:
                    raise _error(
                        "KUBERNETES_RANGES_INCOMPLETE",
                        "A Service in the namespace has a ClusterIP outside --service-cidr",
                    )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        if isinstance(error, DeploymentError):  # a ValueError subclass: our own refusals
            raise
        raise _error("KUBERNETES_UNREADABLE", "The cluster returned unreadable objects") from None
    host, _ = _host_port(server)
    api = [f"{_address(value)}/32" for value in resolve(host)]
    if not api:
        raise _error("KUBERNETES_UNREACHABLE", "The API server's name has no IPv4 address")
    entries = [str(network) for network in pods] + [str(services)] + api
    if nodes_given:
        entries += [str(network) for network in nodes_given]
    else:
        entries += [f"{address}/32" for address in node_addresses]
    entries += [METADATA, *PRIVATE]
    result = list(dict.fromkeys(entries))
    if len(result) > MAX_RANGES:
        raise _error(
            "KUBERNETES_RANGES_TOO_MANY",
            f"{len(result)} blocked ranges exceed {MAX_RANGES}; give the node ranges with "
            "--node-cidr",
        )
    return result


# -- connect --------------------------------------------------------------------------------------


def _version(session, run: Callable) -> tuple[int, int]:
    try:
        found = json.loads(kubectl(session, "version", "-o", "json", run=run))["serverVersion"]
        major = int("".join(ch for ch in str(found["major"]) if ch.isdigit()))
        minor = int("".join(ch for ch in str(found["minor"]) if ch.isdigit()))
    except (ValueError, KeyError, TypeError):
        raise _error("KUBERNETES_UNREADABLE", "Cannot read the Kubernetes version") from None
    return major, minor


def _namespace_checks(session, namespace: str, run: Callable) -> None:
    try:
        labels = _json(session, "get", "namespace", namespace, run=run)["metadata"].get("labels")
    except (KeyError, TypeError, AttributeError):
        raise _error("KUBERNETES_UNREADABLE", "Cannot verify the namespace") from None
    labels = labels or {}
    if labels.get(MANAGED_BY) != "agentbreeder":
        raise _error(
            "KUBERNETES_NAMESPACE_UNMANAGED",
            f"Namespace {namespace} lacks the label {MANAGED_BY}=agentbreeder",
        )
    if labels.get(POD_SECURITY) != "restricted":
        raise _error(
            "KUBERNETES_NAMESPACE_UNRESTRICTED",
            f"Namespace {namespace} must set {POD_SECURITY}=restricted",
        )


def _quota(session, run: Callable) -> None:
    try:
        items = _json(session, "get", "resourcequotas", run=run)["items"]
        capped = set()
        for item in items:
            capped.update(((item.get("spec") or {}).get("hard") or {}).keys())
    except (KeyError, TypeError, AttributeError):
        raise _error("KUBERNETES_UNREADABLE", "Cannot read the namespace's quotas") from None
    missing = [key for key in _QUOTA_KEYS if key not in capped]
    if missing:
        raise _error(
            "KUBERNETES_QUOTA_MISSING",
            "The namespace needs a ResourceQuota capping " + " and ".join(missing),
        )


def resolve_kubectl(path: str | None) -> str:
    found = path or shutil.which("kubectl")
    if not found:
        raise _error("CLUSTER_TOOL_MISSING", "kubectl is not on PATH; give --kubectl PATH")
    real = os.path.realpath(found)
    if not os.path.isfile(real) or not os.access(real, os.X_OK):
        raise _error("CLUSTER_TOOL_MISSING", "kubectl is not an executable file")
    return real


def connect(
    *,
    server: str,
    mint_kubeconfig: Path,
    registry: str,
    pod_cidrs: list[str],
    service_cidr: str,
    node_cidrs: list[str] | None = None,
    namespace: str = "agentbreeder",
    tls_server_name: str | None = None,
    token_minutes: int = 30,
    kubectl_path: str | None = None,
    run: Callable = subprocess.run,
    tls: Callable[[str, str, str | None], None] = tls_check,
    resolve: Callable[[str], list[str]] = resolve_ipv4,
    policy: Callable | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> dict:
    """Check a cluster and return its `kubernetes` operator config block and the review."""
    refusal = api_server_refusal(server)
    if refusal:
        raise _error("KUBERNETES_SERVER_REFUSED", refusal)
    mint_path = Path(os.path.abspath(mint_kubeconfig))
    try:
        data = read_private(mint_path, "The mint kubeconfig")
    except OperatorConfigError as error:
        raise _error("MINT_KUBECONFIG_INVALID", str(error)) from None
    mint = parse_mint(data)
    if mint.server != server:
        raise _error("KUBERNETES_MINT_MISMATCH", "The mint kubeconfig's server is not --server")
    ca_pem = _pem(mint.ca)
    exec_sha256 = None
    if mint.exec_command is not None:
        if kubernetes_token.writable_by_user(mint.exec_command):
            raise _error(
                "MINT_EXEC_WRITABLE",
                "The mint exec plugin, or a directory above it, is writable by your user; "
                "install it where only an administrator can write",
            )
        try:
            exec_sha256 = file_sha256(mint.exec_command)
        except OSError:
            raise _error("MINT_EXEC_MISSING", "Cannot read the mint exec plugin") from None
    tool = resolve_kubectl(kubectl_path)
    log("Checking the API server's certificate against the mint kubeconfig's CA ...")
    tls(server, ca_pem, tls_server_name)
    token_block = {
        "server": server,
        "tls_server_name": tls_server_name,
        "ca_pem": ca_pem,
        "ca_sha256": hashlib.sha256(ca_pem.encode()).hexdigest(),
        "mint_kubeconfig": str(mint_path),
        "mint_kubeconfig_sha256": hashlib.sha256(data).hexdigest(),
        "mint_exec_sha256": exec_sha256,
        "token_minutes": token_minutes,
    }
    tools = {"kubectl": tool, "kubectl_sha256": file_sha256(tool)}
    try:
        draft = KubernetesTarget.model_validate(
            {
                "provider": TOKEN,
                "context": TOKEN_CONTEXT,
                "cluster_uid": "0" * 36,
                "namespace": namespace,
                "registry": registry,
                "blocked_cidrs": [METADATA],
                "token": token_block,
                "tools": tools,
            }
        )
    except ValueError as error:
        raise _error("KUBERNETES_TOKEN_INVALID", _first_message(error)) from None
    document = TokenProvider.document(draft)
    provider = TokenProvider(Binding(TOKEN, draft.cluster_uid, namespace, document), run=run)
    log("Minting a deployer token and reading the cluster ...")
    with provider.session_as("deployer") as deployer:
        uid = kubectl(
            deployer, "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}", run=run
        )
        observed = provider.observed_minutes
        version = _version(deployer, run)
        if version < MIN_VERSION:
            raise _error(
                "KUBERNETES_VERSION_UNSUPPORTED",
                f"Kubernetes {MIN_VERSION[0]}.{MIN_VERSION[1]} or later is required",
            )
        _namespace_checks(deployer, namespace, run)
        _quota(deployer, run)
        log("Minting an installer token and computing the blocked ranges ...")
        with provider.session_as("installer") as installer:
            blocked = blocked_cidrs(
                installer,
                pod_cidrs=pod_cidrs,
                service_cidr=service_cidr,
                server=server,
                node_cidrs=node_cidrs,
                run=run,
                resolve=resolve,
            )
        log("Checking the admission policies ...")
        policy_digest = (policy or kubernetes_token.admission_policy)(deployer, namespace, run=run)
    block = draft.model_dump(exclude_none=True) | {"cluster_uid": uid, "blocked_cidrs": blocked}
    try:
        target = KubernetesTarget.model_validate(block)
    except ValueError as error:
        raise _error("KUBERNETES_TOKEN_INVALID", _first_message(error)) from None
    host, port = _host_port(server)
    return {
        "kubernetes": target.model_dump(exclude_none=True),
        "review": {
            "server": server,
            "tls_server_name": tls_server_name,
            "ca_sha256": token_block["ca_sha256"],
            "ca_fingerprints_sha256": ca_fingerprints(ca_pem),
            "service_accounts": target.token.service_accounts.model_dump(),
            "token_minutes": token_minutes,
            "token_observed_minutes": observed,
            "kubernetes_version": f"{version[0]}.{version[1]}",
            "policy_digest": policy_digest,
            "compare": [
                "Pin check, a second way: the CA in the mint kubeconfig",
                f"  kubectl config view --raw --kubeconfig {mint_path} -o "
                "jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d | "
                "openssl x509 -noout -fingerprint -sha256",
                "and the chain the server presents (its last certificate should be issued by it)",
                f"  openssl s_client -connect {host}:{port} -showcerts </dev/null",
            ],
        },
    }


def _first_message(error: Exception) -> str:
    errors = getattr(error, "errors", None)
    if callable(errors):
        for item in errors():
            message = str(item.get("msg", ""))
            if "[" in message:
                return message.split("[", 1)[1].split("] ", 1)[-1]
    return "the cluster block is invalid"
