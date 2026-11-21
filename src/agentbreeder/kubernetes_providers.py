"""Cluster providers behind the Kubernetes target (#770 §4.1, slice K0 = #908 G0).

What differs per kind of cluster sits behind a provider: how kubectl reaches the API server
(`session()`), what is checked before kubectl runs (`check(session)`), how images reach the cluster
(`registry()`) and which ranges agents may never reach (`blocked_cidrs()`, at connect). `kind` is
the only provider; its methods are the code `kubernetes.py` and `cluster.py` ran before, moved.

A deployment records where it runs in its `context` column, tagged with the provider:

- `kind|<kube context>|<cluster uid>|<namespace>|<kubeconfig>`: inline, as before with a tag. A
  record written before the tag (`<kube context>|<uid>|<namespace>|<kubeconfig>`) reads as `kind`,
  and the v5 store migration rewrites it.
- `<provider>|<binding id>`: a row of the store's `cluster_bindings` table (unique on environment,
  cluster UID and namespace) holding the provider's non-secret pins. The row's digest is written
  into the `deployment_created` audit event; `Kubernetes.authorize` recomputes it and compares it
  with the row and with every such event, so an edited row (a new server or CA pin) is refused
  with `KUBERNETES_BINDING_CHANGED` instead of being used (#770 T12). This is a consistency check
  between two tables of the same state file, not tamper-evidence: whoever can write that file
  can edit the event too (the audit chain shows such an edit only when it is verified, and its
  owner can recompute the chain). No shipped provider uses this form yet; it is exercised
  through injected providers in tests.

A binding is always rebuilt from the record, never from the live operator config, so `remove`,
`disable`, `reconcile`, `logs` and `forward` act on the cluster the deployment was created in.
Provider names appear only in `PROVIDERS` and the operator config's `provider` literal.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol
from urllib.parse import urlsplit

from agentbreeder.operator_config import OperatorConfigError, parse_yaml, read_private
from agentbreeder.store import DeploymentError

KIND = "kind"
_CLUSTER_KEYS = {"server", "certificate-authority-data"}  # no proxy-url, no insecure-skip
_USER_KEYS = {"client-certificate-data", "client-key-data", "token"}  # no exec, no auth-provider
_PROVIDER = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
# The operator config's formats (KubernetesTarget): a recorded value never reaches kubectl's argv
# unless it could have come from a valid config, so none can start with `-`.
_CONTEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_UID = re.compile(r"^[0-9a-f-]{36}$")
_NAMESPACE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_KUBECONFIG = re.compile(r"^/[^|\s]{0,1023}$")
_BINDING_ID = re.compile(r"^[0-9a-f]{32}$")
_UNREADABLE = "Cannot read this deployment's cluster binding"
CHANGED = (
    "[KUBERNETES_BINDING_CHANGED] This deployment's recorded cluster binding was changed; "
    "nothing was changed"
)


@dataclass(frozen=True)
class Session:
    """What kubectl runs with: an argv prefix after `kubectl` and its whole environment."""

    argv: tuple[str, ...]
    env: Mapping[str, str]


@dataclass(frozen=True)
class Registry:
    """Where the cluster pulls agent images from, and how they are pushed (#770 §8)."""

    scheme: str  # "http" (loopback only) or "https"
    loopback_only: bool
    authenticated: bool


@dataclass(frozen=True)
class Binding:
    """The cluster a deployment is bound to. `document` holds the provider's non-secret pins;
    for `kind` it is `{"context", "kubeconfig"}` and the binding is recorded inline."""

    provider: str
    cluster_uid: str
    namespace: str
    document: Mapping[str, Any]
    # Not part of equality: a binding parsed from an inline record carries no environment, and
    # it is the same cluster the selection names (a by-reference binding's `id` keys its row).
    environment: str | None = field(default=None, compare=False)
    id: str | None = None  # the cluster_bindings row, for a provider recorded by reference
    digest: str | None = field(default=None, compare=False)

    def record(self) -> str:
        """The deployment record's `context` value."""
        if self.provider == KIND:
            return "|".join(
                (
                    KIND,
                    self.document["context"],
                    self.cluster_uid,
                    self.namespace,
                    self.document["kubeconfig"],
                )
            )
        if not self.id:
            raise DeploymentError("[KUBERNETES_UNCONFIGURED] The cluster binding is not recorded")
        return f"{self.provider}|{self.id}"

    def audit(self) -> dict | None:
        """The `cluster_binding` entry of the `deployment_created` audit event, or None for an
        inline binding (whose audit events are unchanged)."""
        if self.id is None:
            return None
        return {"id": self.id, "digest": self.digest}


class Provider(Protocol):
    """One kind of cluster, built from a `Binding`: at deploy and connect time the binding
    `from_target` makes from the operator config, afterwards the one rebuilt from the record
    (never the live operator config).

    Session ownership: `Kubernetes` enters `session()` on first use and keeps it for every kubectl
    call against the same binding; `Kubernetes.close()` (called by `DeploymentService.close()`
    in each command handler's `finally`) or a rebind to another binding ends it. A provider whose
    session holds a temporary credential removes it when the context manager exits."""

    name: ClassVar[str]
    inline: ClassVar[bool]  # recorded in the context string (True) or a cluster_bindings row

    @classmethod
    def document(cls, target) -> dict:
        """The non-secret pins of an operator `KubernetesTarget`."""

    def session(self) -> AbstractContextManager[Session]:
        """kubectl's argv prefix and environment for one operation."""

    def check(self, session: Session) -> None:
        """Refuse before kubectl runs when the session does not reach the recorded cluster."""

    def registry(self) -> Registry:
        """How images reach this cluster. K0 defines it; nothing consumes it until the generic
        registry path (#770 K8): `promote` still checks its loopback registry itself."""

    def blocked_cidrs(self, session: Session | None, **context: Any) -> list[str]:
        """The ranges an agent's NetworkPolicy denies, measured when the cluster is connected
        (`cluster up` for `kind`, with `agents_network`; a later provider reads the cluster's
        ranges through `session`)."""


def kubectl_environment() -> dict[str, str]:
    """The caller's environment without `KUBECONFIG`: kubectl reads only the recorded file."""
    return {k: v for k, v in os.environ.items() if k != "KUBECONFIG"}


class KindProvider:
    """A local `kind` cluster: a private static kubeconfig for a loopback API server, and the
    loopback registry `cluster up` starts. This is the code the Kubernetes target always ran."""

    name: ClassVar[str] = KIND
    inline: ClassVar[bool] = True

    def __init__(self, binding: Binding):
        self.kubeconfig = str(binding.document["kubeconfig"])
        self.context = str(binding.document["context"])

    @classmethod
    def document(cls, target) -> dict:
        return {"context": target.context, "kubeconfig": target.kubeconfig}

    @contextmanager
    def session(self) -> Iterator[Session]:
        yield Session(
            ("--kubeconfig", self.kubeconfig, "--context", self.context), kubectl_environment()
        )

    def check(self, session: Session) -> None:
        """Only a private, static-credential kubeconfig for a loopback cluster is accepted."""
        try:
            config = parse_yaml(read_private(Path(self.kubeconfig), "Kubeconfig"), "Kubeconfig")
            context = next(c["context"] for c in config["contexts"] if c["name"] == self.context)
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

    def registry(self) -> Registry:
        return Registry(scheme="http", loopback_only=True, authenticated=False)

    def blocked_cidrs(self, session: Session | None, *, agents_network: str) -> list[str]:
        """The Docker networks of the node and registry, pod, service and metadata ranges."""
        from agentbreeder import cluster

        return cluster.blocked_cidrs(agents_network)


PROVIDERS: dict[str, type] = {KIND: KindProvider}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(
    provider: str, environment: str, cluster_uid: str, namespace: str, document: Mapping
) -> str:
    """The digest of a cluster_bindings row: everything it binds, canonical JSON."""
    body = {
        "provider": provider,
        "environment": environment,
        "cluster_uid": cluster_uid,
        "namespace": namespace,
        "document": dict(document),
    }
    return "sha256:" + hashlib.sha256(canonical(body).encode()).hexdigest()


def from_target(environment: str, target, providers: Mapping[str, type]) -> Binding:
    """The binding an operator `KubernetesTarget` describes (deploy time only)."""
    name = getattr(target, "provider", KIND) or KIND
    kind = providers.get(name)
    if kind is None:
        raise DeploymentError(f"[KUBERNETES_UNCONFIGURED] Unknown cluster provider {name}")
    document = kind.document(target)
    return Binding(
        provider=name,
        cluster_uid=target.cluster_uid,
        namespace=target.namespace,
        document=document,
        environment=environment,
        digest=None
        if kind.inline
        else digest(name, environment, target.cluster_uid, target.namespace, document),
    )


def parse_inline(context: str) -> Binding | None:
    """A `kind` binding from its record (tagged, or the 4-field form written before K0), or
    None when `context` is not an inline record."""
    parts = context.split("|")
    if len(parts) == 5 and parts[0] == KIND:
        parts = parts[1:]
    if len(parts) != 4 or not all(parts):
        return None
    kube_context, cluster_uid, namespace, kubeconfig = parts
    if not (
        _CONTEXT.fullmatch(kube_context)
        and _UID.fullmatch(cluster_uid)
        and _NAMESPACE.fullmatch(namespace)
        and _KUBECONFIG.fullmatch(kubeconfig)
    ):
        raise DeploymentError(_UNREADABLE)
    return Binding(
        provider=KIND,
        cluster_uid=cluster_uid,
        namespace=namespace,
        document={"context": kube_context, "kubeconfig": kubeconfig},
    )


def parse_inline_or_none(context: str) -> Binding | None:
    """`parse_inline`, with an invalid inline record read as None."""
    try:
        return parse_inline(context)
    except DeploymentError:
        return None


def resolve(context: str, bindings, providers: Mapping[str, type]) -> Binding:
    """The binding a deployment record names. A by-reference binding is read from the store
    (`bindings`: `cluster_binding(id)` and `cluster_binding_audit(id)`) and refused with
    KUBERNETES_BINDING_CHANGED when its row, its digest and the audit disagree."""
    inline = parse_inline(context)
    if inline is not None:
        return inline
    provider, _, identifier = context.partition("|")
    kind = providers.get(provider)
    if (
        kind is None
        or kind.inline
        or not _PROVIDER.fullmatch(provider)
        or not _BINDING_ID.fullmatch(identifier)
    ):
        raise DeploymentError(_UNREADABLE)
    if bindings is None:
        raise DeploymentError(_UNREADABLE)
    try:
        row = bindings.cluster_binding(identifier)
        recorded = bindings.cluster_binding_audit(identifier)
    except sqlite3.Error as error:
        raise DeploymentError(_UNREADABLE) from error
    if row is None or row["provider"] != provider:
        raise DeploymentError(CHANGED)
    if not (
        _UID.fullmatch(str(row["cluster_uid"])) and _NAMESPACE.fullmatch(str(row["namespace"]))
    ):
        raise DeploymentError(CHANGED)
    try:
        document = json.loads(row["document"])
    except (ValueError, TypeError, RecursionError) as error:
        raise DeploymentError(CHANGED) from error
    if not isinstance(document, dict):
        raise DeploymentError(CHANGED)
    found = digest(provider, row["environment"], row["cluster_uid"], row["namespace"], document)
    if found != row["digest"] or not recorded or any(value != found for value in recorded):
        raise DeploymentError(CHANGED)
    return Binding(
        provider=provider,
        cluster_uid=row["cluster_uid"],
        namespace=row["namespace"],
        document=document,
        environment=row["environment"],
        id=identifier,
        digest=found,
    )


def record(binding: Binding, bindings) -> Binding:
    """Record a by-reference binding before a deploy (an inline one is returned unchanged).
    Reuses the environment's row when its pins are the same; replaces it when they changed and
    no live deployment uses it; else KUBERNETES_BINDING_CHANGED."""
    if binding.id is not None or binding.digest is None:
        return binding
    if bindings is None:
        raise DeploymentError("[KUBERNETES_UNCONFIGURED] No state to record the cluster binding")
    identifier = bindings.ensure_cluster_binding(
        provider=binding.provider,
        environment=binding.environment or "",
        cluster_uid=binding.cluster_uid,
        namespace=binding.namespace,
        document=canonical(dict(binding.document)),
        digest=binding.digest,
    )
    return Binding(
        provider=binding.provider,
        cluster_uid=binding.cluster_uid,
        namespace=binding.namespace,
        document=binding.document,
        environment=binding.environment,
        id=identifier,
        digest=binding.digest,
    )


def audit_entry(runtime) -> dict | None:
    """The `cluster_binding` audit entry of a runtime's selected binding, if it has one."""
    binding = getattr(runtime, "binding", None)
    return binding.audit() if isinstance(binding, Binding) else None


@dataclass(frozen=True)
class Snapshot:
    """A by-reference binding and its audit digests read from a store before it is closed
    (`logs` and `watch` close their read-only store before kubectl runs)."""

    rows: Mapping[str, Mapping]
    audit: Mapping[str, list[str]]

    @classmethod
    def read(cls, store, context: str) -> Snapshot:
        _, _, identifier = context.partition("|")
        if parse_inline_or_none(context) is not None or not _BINDING_ID.fullmatch(identifier):
            return cls({}, {})
        row = store.cluster_binding(identifier)
        return cls(
            {identifier: row} if row else {},
            {identifier: store.cluster_binding_audit(identifier)},
        )

    def cluster_binding(self, identifier: str) -> Mapping | None:
        return self.rows.get(identifier)

    def cluster_binding_audit(self, identifier: str) -> list[str]:
        return list(self.audit.get(identifier, []))
