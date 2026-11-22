"""Identities, RBAC and the required admission policies of a cluster namespace (#770 §7, K2).

AgentBreeder renders these objects; it never installs cluster-wide objects on a customer cluster
(the runbook does, from `agentbreeder cluster policy print` and `cluster rbac print`). On the local
`kind` cluster `cluster up --restricted` installs them itself (preview).

- `roles(namespace)`: the four service accounts and their Roles of §7.1 (the operator's own
  identity only mints and is not rendered).
- `policies(namespace)`: one `ValidatingAdmissionPolicy` per kind (§7.2) and its binding. The
  policies hold what the deployer and the canary create, change and delete to the one shape
  `kubernetes._objects` writes (after API-server defaulting) and the canary templates here.
  Their parameters come from the `ab-grants` ConfigMap (`grants_configmap`).
- `digest(objects)`: the digest of the rendered objects' specs after normalisation, so a live
  object with server defaults and `managedFields` gives the same value.
- `probes(namespace, grants)`: paired server-side dry-run probes (§7.4): each refused request
  differs from an admitted one only in the refused field.

Nothing here has run against a real API server unless a record says so; the opt-in `kind`
test (`tests/test_kind_admission_docker.py`) is the measurement.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agentbreeder.kubernetes import (
    CLI_SECURITY_CONTEXT,
    CLI_SHARED_DIR,
    CLI_SHARED_VOLUME,
    CLI_TMP_VOLUME,
    CONTAINER_SECURITY_CONTEXT,
    DEPLOYMENT_LABEL,
    DNS_PEER,
    DNS_PORTS,
    IMAGE_ANNOTATION,
    MANAGED_BY,
    POD_SECURITY_CONTEXT,
    READINESS,
    RESOURCES,
    SECRET_MODE,
    SPEC_ANNOTATION,
    TMP_VOLUME,
)
from agentbreeder.secret_files import MOUNT_ROOT

NAME_LABEL = "io.agentbreeder/name"
USES_PREFIX = "io.agentbreeder/uses-"
CANARY_LABEL = "io.agentbreeder/canary"
CHART_SERVICE_LABEL = "io.agentbreeder/chart-service"
GRANTS = "ab-grants"
CHART_LOCK = "ab-chart-lock"
PROBE_AGENT = "ab-probe"  # reserved: `connect` writes its grant for the probes (§7.2)
ACCOUNTS = ("deployer", "installer", "operator", "canary")
STORE_DRIVER = "secrets-store.csi.k8s.io"
STORE_ROOT = "/run/secret-store/"
CANARY_ROLES = ("server", "target", "client")
CANARY_ARGS = ["serve", "8080", "8081"]
MINIMUM_VERSION = (1, 30)  # ValidatingAdmissionPolicy GA (D14)
AGENT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ITEM = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
_USE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?:[0-9]{1,5}$")
_IPV4 = r"^(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])(\.(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])){3}$"
_PORT = r"^[1-9][0-9]{0,4}$"
_HEX32 = "^[0-9a-f]{32}$"
_OWN_NAME = "^agentbreeder-[0-9a-f]{32}$"
# Kubernetes defaults the API server writes before validating admission (§7.2). Each defaulted
# key is admitted only at this value. *(To measure on the run cluster's version.)*
DEPLOYMENT_DEFAULTS = {
    "strategy": {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxUnavailable": "25%", "maxSurge": "25%"},
    },
    "revisionHistoryLimit": 10,
    "progressDeadlineSeconds": 600,
}
POD_DEFAULTS = {
    "restartPolicy": "Always",
    "dnsPolicy": "ClusterFirst",
    "schedulerName": "default-scheduler",
    "terminationGracePeriodSeconds": 30,
}
CONTAINER_DEFAULTS = {
    "terminationMessagePath": "/dev/termination-log",
    "terminationMessagePolicy": "File",
}
PROBE_DEFAULTS = {"timeoutSeconds": 1, "successThreshold": 1}
SERVICE_DEFAULTS = {
    "sessionAffinity": "None",
    "ipFamilyPolicy": "SingleStack",
    "ipFamilies": ["IPv4"],
    "internalTrafficPolicy": "Cluster",
}
# What built-in mutating admission adds to a Pod the canary creates (§7.2).
CANARY_POD_DEFAULTS = {
    **POD_DEFAULTS,
    "serviceAccountName": "default",
    "serviceAccount": "default",
    "priority": 0,
    "preemptionPolicy": "PreemptLowerPriority",
    "enableServiceLinks": True,
    "tolerations": [
        {
            "key": f"node.kubernetes.io/{reason}",
            "operator": "Exists",
            "effect": "NoExecute",
            "tolerationSeconds": 300,
        }
        for reason in ("not-ready", "unreachable")
    ],
}


class AdmissionError(ValueError):
    """An input the policies or grants cannot represent; its message starts with a code."""


def account(name: str) -> str:
    return f"ab-{name}"


def username(namespace: str, name: str) -> str:
    return f"system:serviceaccount:{namespace}:{account(name)}"


def _check_namespace(namespace: str) -> None:
    if not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", namespace or ""):
        raise AdmissionError("[KUBERNETES_UNCONFIGURED] Not a valid namespace name")


# -- ab-grants ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentGrant:
    """What one agent name may use in the namespace (§7.2 `ab-grants`)."""

    bindings: tuple[str, ...] = ()  # Secret names its pods may project (K5)
    uses: tuple[str, ...] = ()  # "<service>:<port>" in-cluster services (K6)
    stores: tuple[str, ...] = ()  # SecretProviderClass names (K7)
    service_accounts: tuple[str, ...] = ()  # runbook-created pod identities (K7, D8)


@dataclass(frozen=True)
class Grants:
    """The parameters of every policy: the `data` of the `ab-grants` ConfigMap."""

    registry: str
    blocked: tuple[str, ...]
    echo: str = ""
    pull: str = ""
    nodedns: str = ""
    # The stack's agents network on `kind` (#1083 F1b): the ranges a kit deployment may reach.
    agentnet: tuple[str, ...] = ()
    agents: Mapping[str, AgentGrant] = field(default_factory=dict)

    def data(self) -> dict[str, str]:
        """The ConfigMap's flat strings (CEL cannot parse JSON). Every value is validated, so a
        list never holds a comma inside an item and an absent agent never means "empty"."""
        if not re.fullmatch(r"^[a-z0-9.:-]+(/[a-z0-9._-]+)*$", self.registry or ""):
            raise AdmissionError("[KUBERNETES_GRANTS_INVALID] registry is not a registry prefix")
        if not self.blocked:
            raise AdmissionError("[KUBERNETES_GRANTS_INVALID] blocked is empty")
        for value in (*self.blocked, *self.agentnet):
            _canonical_ipv4(value)
        if self.nodedns:
            ipaddress.IPv4Address(self.nodedns)
        if self.echo and not re.fullmatch(r"^[a-z0-9.:/_-]+@sha256:[0-9a-f]{64}$", self.echo):
            raise AdmissionError("[KUBERNETES_GRANTS_INVALID] echo must be pinned by digest")
        if self.pull and not _ITEM.fullmatch(self.pull):
            raise AdmissionError("[KUBERNETES_GRANTS_INVALID] pull is not a Secret name")
        data = {
            "registry": self.registry,
            "echo": self.echo,
            "pull": self.pull,
            "blocked": ",".join(self.blocked),
            "nodedns": self.nodedns,
            # The exact peers a kit deployment's egress rule may hold (#1083 F1b): each agents
            # range with every blocked range inside it excepted, as `kit_peer` renders them.
            "kit": ";".join(kit_peers(self.agentnet, self.blocked)),
        }
        for name, grant in sorted(self.agents.items()):
            if not AGENT_NAME.fullmatch(name):
                raise AdmissionError(f"[KUBERNETES_GRANTS_INVALID] {name!r} is not an agent name")
            for values, pattern in (
                (grant.bindings, _ITEM),
                (grant.uses, _USE),
                (grant.stores, _ITEM),
                (grant.service_accounts, _ITEM),
            ):
                if any(not pattern.fullmatch(value) for value in values):
                    raise AdmissionError(
                        f"[KUBERNETES_GRANTS_INVALID] agent {name}: an item has the wrong shape"
                    )
            data[f"{name}.bindings"] = ",".join(grant.bindings)
            data[f"{name}.uses"] = ",".join(grant.uses)
            data[f"{name}.stores"] = ",".join(grant.stores)
            data[f"{name}.sa"] = ",".join(grant.service_accounts)
        return data


def kit_peer(peer: Mapping) -> str:
    """One `ipBlock` peer as the string the policy compares: `cidr|except1,except2`."""
    block = peer["ipBlock"]
    return block["cidr"] + "|" + ",".join(block.get("except") or [])


def kit_peers(agentnet: Iterable[str], blocked: Iterable[str]) -> list[str]:
    from agentbreeder.kubernetes import kit_destinations
    from agentbreeder.store import DeploymentError

    agentnet = tuple(agentnet)
    if not agentnet:
        return []
    try:
        return [kit_peer(peer) for peer in kit_destinations(agentnet, tuple(blocked))]
    except DeploymentError:
        return []  # nothing allowed: a kit deployment is refused by `create` anyway


def _canonical_ipv4(value: str) -> None:
    try:
        network = ipaddress.ip_network(value)
    except ValueError as error:
        raise AdmissionError(f"[KUBERNETES_GRANTS_INVALID] {value!r} is not a CIDR") from error
    if network.version != 4 or str(network) != value:
        # Membership in `blocked` is string equality in CEL, so only canonical forms are kept.
        raise AdmissionError(f"[KUBERNETES_GRANTS_INVALID] {value!r} is not a canonical IPv4 CIDR")


def grants_configmap(namespace: str, grants: Grants) -> dict:
    _check_namespace(namespace)
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": GRANTS,
            "namespace": namespace,
            "labels": {MANAGED_BY: "agentbreeder"},
        },
        "data": grants.data(),
    }


# -- §7.1 identities and Roles --------------------------------------------------------------------


def _rule(groups, resources, verbs, names=None) -> dict:
    rule = {"apiGroups": list(groups), "resources": list(resources), "verbs": list(verbs)}
    if names is not None:
        if not names:
            raise AdmissionError("an empty resourceNames list would grant every name")
        rule["resourceNames"] = list(names)
    return rule


READ = ("get", "list", "watch")
WRITE = ("create", "get", "list", "watch", "patch", "update", "delete")


def role_rules(namespace: str, operator_secrets: Iterable[str] = ()) -> dict[str, list[dict]]:
    """The namespaced rules of each service account (§7.1)."""
    operator_secrets = sorted(operator_secrets)
    return {
        "deployer": [
            _rule(["apps"], ["deployments"], ("create", "delete", *READ)),
            _rule([""], ["services"], ("create", "delete", *READ)),
            _rule(["networking.k8s.io"], ["networkpolicies"], ("create", "delete", *READ)),
            # Authorization only, for `blockOwnerDeletion` (§4.3); grants no write path.
            _rule(["apps"], ["deployments/finalizers"], ["update"]),
            _rule([""], ["secrets"], ["create"]),
            _rule(["apps"], ["replicasets"], READ),
            _rule([""], ["pods"], READ),
            _rule([""], ["pods/log"], ["get"]),
            _rule([""], ["pods/portforward"], ["create"]),
            _rule([""], ["configmaps"], ["get"], [CHART_LOCK, GRANTS]),
            _rule(["secrets-store.csi.x-k8s.io"], ["secretproviderclasses"], ("get", "list")),
            _rule([""], ["resourcequotas", "limitranges"], ("get", "list")),
        ],
        "installer": [
            _rule(
                ["apps", "", "networking.k8s.io", "batch"],
                [
                    "deployments",
                    "statefulsets",
                    "services",
                    "networkpolicies",
                    "configmaps",
                    "serviceaccounts",
                    "persistentvolumeclaims",
                    "jobs",
                ],
                WRITE,
            ),
            # Helm's release storage updates the previous release record on every upgrade.
            _rule([""], ["secrets"], WRITE),
            _rule(["", "apps"], ["pods", "replicasets"], READ),
            _rule([""], ["pods/log"], ["get"]),
        ],
        "operator": [
            # No rule at all until the chart lock names operator-only Secrets (K4/K5, D3):
            # an empty resourceNames list would grant every Secret.
            *([_rule([""], ["secrets"], ["get"], operator_secrets)] if operator_secrets else []),
            _rule([""], ["pods/portforward"], ["create"]),
            _rule([""], ["services", "pods"], ("get", "list")),
        ],
        "canary": [
            _rule([""], ["pods"], ("create", "delete", *READ)),
            _rule(["networking.k8s.io"], ["networkpolicies"], ("create", "delete", *READ)),
        ],
    }


def cluster_rules(namespace: str) -> dict[str, list[dict]]:
    """The cluster-scoped rules (§7.1): only the deployer reads named objects; installer and
    deployer list nodes (the ranges of §6.4)."""
    return {
        "deployer": [
            _rule([""], ["namespaces"], ["get"], ["kube-system", namespace]),
            _rule(
                ["admissionregistration.k8s.io"],
                ["validatingadmissionpolicies", "validatingadmissionpolicybindings"],
                ["get"],
                POLICY_NAMES,
            ),
            _rule([""], ["nodes"], ["list"]),
        ],
        "installer": [_rule([""], ["nodes"], ["list"])],
    }


def _cluster_name(namespace: str, name: str) -> str:
    """Cluster-scoped RBAC is per namespace, so two namespaces never share a grant."""
    return f"agentbreeder:{namespace}:{account(name)}"


def roles(namespace: str, operator_secrets: Iterable[str] = ()) -> list[dict]:
    """Service accounts, Roles, RoleBindings, ClusterRoles and ClusterRoleBindings (§7.1)."""
    _check_namespace(namespace)
    labels = {MANAGED_BY: "agentbreeder"}
    namespaced = role_rules(namespace, operator_secrets)
    clustered = cluster_rules(namespace)
    objects: list[dict] = []
    for name in ACCOUNTS:
        subject = [{"kind": "ServiceAccount", "name": account(name), "namespace": namespace}]
        meta = {"name": account(name), "namespace": namespace, "labels": labels}
        objects += [
            {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": meta,
                "automountServiceAccountToken": False,
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": meta,
                "rules": namespaced[name],
            },
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": meta,
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": account(name),
                },
                "subjects": subject,
            },
        ]
        if name in clustered:
            cluster_meta = {"name": _cluster_name(namespace, name), "labels": labels}
            objects += [
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "ClusterRole",
                    "metadata": cluster_meta,
                    "rules": clustered[name],
                },
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "ClusterRoleBinding",
                    "metadata": cluster_meta,
                    "roleRef": {
                        "apiGroup": "rbac.authorization.k8s.io",
                        "kind": "ClusterRole",
                        "name": _cluster_name(namespace, name),
                    },
                    "subjects": subject,
                },
            ]
    return objects


# -- CEL helpers ----------------------------------------------------------------------------------


def cel(value: Any) -> str:
    """A CEL literal for a JSON value, well typed under Kubernetes' CEL (#1204).

    Kubernetes validates aggregate literals as homogeneous: every element of a list literal,
    and every value of a map literal, must have the same type as the first. When the elements
    of one literal differ in type, each of them is wrapped in `dyn(...)`, which changes the
    static type only: CEL equality still compares every key and value, so a map literal still
    refuses an extra key, a missing key or a different value."""
    return _literal(value)[0]


def _literal(value: Any) -> tuple[str, str]:
    """`(text, static type)`. An empty aggregate gets a type no sibling shares (`?`), so its
    siblings are always wrapped."""
    if value is None:
        return "null", "null"
    if value is True or value is False:
        return ("true" if value else "false"), "bool"
    if isinstance(value, int):
        return str(value), "int"
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'", "string"
    if isinstance(value, list):
        texts, element = _homogeneous([_literal(item) for item in value])
        return "[" + ", ".join(texts) + "]", f"list({element})"
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("map keys must be strings")
        texts, element = _homogeneous([_literal(item) for item in value.values()])
        entries = ", ".join(
            f"{_literal(k)[0]}: {text}" for k, text in zip(value, texts, strict=True)
        )
        return "{" + entries + "}", f"map(string, {element})"
    raise TypeError(type(value))


def _homogeneous(items: list[tuple[str, str]]) -> tuple[list[str], str]:
    """The elements' texts and their common type, wrapping each in `dyn()` unless every element
    has one and the same fully known type."""
    if not items:
        return [], "?"
    types = {kind for _, kind in items}
    if len(items) == 1 or (len(types) == 1 and "?" not in next(iter(types))):
        return [text for text, _ in items], items[0][1]
    return [f"dyn({text})" for text, _ in items], "dyn"


def _shape(expr: str, fixed: Mapping, defaults: Mapping = {}, free: Iterable[str] = ()) -> str:  # noqa: B006
    """`expr` holds exactly the keys of `fixed` at their values, `defaults` absent or at their
    default value, and `free` (checked elsewhere); nothing else."""
    keys = [*fixed, *defaults, *free]
    parts = [f"{expr}.all(k, k in {cel(keys)})"]
    parts += [f"{cel(k)} in {expr} && {expr}[{cel(k)}] == {cel(v)}" for k, v in fixed.items()]
    parts += [
        f"(!({cel(k)} in {expr}) || {expr}[{cel(k)}] == {cel(v)})" for k, v in defaults.items()
    ]
    return " && ".join(f"({part})" for part in parts)


def _absent_or_empty(expr: str, key: str) -> str:
    return f"(!has({expr}.{key}) || size({expr}.{key}) == 0)"


def _guard(operation: str, expression: str) -> str:
    return f"request.operation != '{operation}' || ({expression})"


def _list_variable(key_expr: str) -> str:
    """A comma-separated `ab-grants` value as a list (`''` is the empty list, not `['']`)."""
    return f"params.data[{key_expr}] == '' ? [] : params.data[{key_expr}].split(',')"


@dataclass(frozen=True)
class Validation:
    expression: str
    message: str

    def render(self) -> dict:
        return {"expression": self.expression, "message": self.message}


@dataclass(frozen=True)
class Policy:
    name: str
    resources: tuple[tuple[str, tuple[str, ...]], ...]  # (apiGroup, resources)
    validations: tuple[Validation, ...]
    variables: tuple[tuple[str, str], ...] = ()
    exclude: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # (apiGroup, resources, resourceNames): objects of those names this policy never matches.
    exclude_named: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = ()
    # (apiGroup, resources, resourceNames): only these named objects are matched (with
    # `resources` empty).
    named: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = ()
    # False: no `paramKind` or `paramRef`, so the policy works while `ab-grants` is missing.
    params: bool = True


def _identity_variables(namespace: str) -> list[tuple[str, str]]:
    return [
        ("isDeployer", f"request.userInfo.username == {cel(username(namespace, 'deployer'))}"),
        ("isCanary", f"request.userInfo.username == {cel(username(namespace, 'canary'))}"),
    ]


def _grant_variables(labels_expr: str) -> list[tuple[str, str]]:
    """The agent a deployer object names and its grant, from its labels."""
    return [
        ("agent", f"{labels_expr}[{cel(NAME_LABEL)}]"),
        ("id", f"{labels_expr}[{cel(DEPLOYMENT_LABEL)}]"),
        ("granted", "(variables.agent + '.bindings') in params.data"),
        ("uses", _list_variable("variables.agent + '.uses'")),
        ("bindings", _list_variable("variables.agent + '.bindings'")),
        ("stores", _list_variable("variables.agent + '.stores'")),
        ("sas", _list_variable("variables.agent + '.sa'")),
        ("services", "variables.uses.map(u, u.split(':')[0])"),
        (
            "labelKeys",
            cel([MANAGED_BY, DEPLOYMENT_LABEL, NAME_LABEL])
            + f" + variables.services.map(s, {cel(USES_PREFIX)} + s)",
        ),
        ("blocked", "params.data.blocked == '' ? [] : params.data.blocked.split(',')"),
        ("kit", "params.data.kit == '' ? [] : params.data.kit.split(';')"),
    ]


def _labels_valid(expr: str) -> str:
    return " && ".join(
        [
            f"has({expr})",
            f"{expr}.all(k, k in variables.labelKeys)",
            f"{cel(MANAGED_BY)} in {expr} && {expr}[{cel(MANAGED_BY)}] == 'agentbreeder'",
            f"{cel(DEPLOYMENT_LABEL)} in {expr}",
            f"{expr}[{cel(DEPLOYMENT_LABEL)}].matches({cel(_HEX32)})",
            f"{cel(NAME_LABEL)} in {expr}",
            f"{expr}[{cel(NAME_LABEL)}].matches({cel(AGENT_NAME.pattern)})",
            f"{expr}.all(k, !k.startsWith({cel(USES_PREFIX)}) || {expr}[k] == 'true')",
        ]
    )


def _owned_delete() -> str:
    """A delete only of an AgentBreeder deployment's object, whatever selector was given."""
    old = "oldObject.metadata"
    return " && ".join(
        [
            f"has({old}.labels)",
            f"{cel(MANAGED_BY)} in {old}.labels",
            f"{old}.labels[{cel(MANAGED_BY)}] == 'agentbreeder'",
            f"{cel(DEPLOYMENT_LABEL)} in {old}.labels",
            f"{old}.name.matches({cel(_OWN_NAME)})",
        ]
    )


def _deployer_metadata(annotations: bool = False, owners: bool = False) -> list[Validation]:
    checks = [
        Validation(
            _guard(
                "CREATE",
                "object.metadata.name.matches("
                + cel(_OWN_NAME)
                + ") && object.metadata.name == 'agentbreeder-' + variables.id",
            ),
            "the name must be agentbreeder-<the deployment label>",
        ),
        Validation(
            _guard("CREATE", _labels_valid("object.metadata.labels")),
            "labels must be exactly AgentBreeder's deployment labels",
        ),
        Validation(
            _guard("CREATE", "variables.granted"),
            "the agent named by io.agentbreeder/name has no entry in ab-grants",
        ),
        Validation(
            _guard("CREATE", _absent_or_empty("object.metadata", "finalizers")),
            "finalizers are refused",
        ),
    ]
    if not annotations:
        checks.append(
            Validation(
                _guard("CREATE", _absent_or_empty("object.metadata", "annotations")),
                "annotations are refused",
            )
        )
    if not owners:
        checks.append(
            Validation(
                _guard("CREATE", _absent_or_empty("object.metadata", "ownerReferences")),
                "ownerReferences are refused",
            )
        )
    return checks


_NO_UPDATE = Validation(
    "request.operation != 'UPDATE'", "updates are refused; AgentBreeder replaces objects"
)


# -- the policies ---------------------------------------------------------------------------------


def _deployments(namespace: str) -> Policy:
    pod = "variables.pod"
    agent = "variables.pod.containers[0]"
    mount_ok = " || ".join(
        [
            "m == {'name': 'tmp', 'mountPath': '/tmp'}",
            f"m == {cel({'name': 'secrets', 'mountPath': MOUNT_ROOT, 'readOnly': True})}",
            f"m == {cel({'name': 'cli-shared', 'mountPath': CLI_SHARED_DIR})}",
            "(m.name.startsWith('store-') && 'readOnly' in m && m.readOnly == true && "
            f"m.mountPath == {cel(STORE_ROOT)} + m.name.substring(6))",
        ]
    )
    source_ok = " || ".join(
        [
            # The Deployment's own Secret: only its own name, so never another deployment's.
            "('secret' in s && s.secret == {'name': object.metadata.name})",
            "('secret' in s && s.secret.all(k, k in ['name', 'items']) && "
            "s.secret.name in variables.bindings && "
            "(!('items' in s.secret) || s.secret.items.all(i, i.all(k, k in ['key', 'path']))))",
            "('downwardAPI' in s && s.downwardAPI.all(k, k == 'items') && "
            "s.downwardAPI.items.all(i, i.all(k, k in ['path', 'fieldRef']) && "
            "i.fieldRef.all(k, k in ['apiVersion', 'fieldPath']) && "
            "i.fieldRef.fieldPath in ['metadata.name', 'metadata.labels']))",
        ]
    )
    volume_ok = " || ".join(
        [
            f"v == {cel(TMP_VOLUME)}",
            f"v == {cel(CLI_TMP_VOLUME)}",
            f"v == {cel(CLI_SHARED_VOLUME)}",
            "(v.name == 'secrets' && v.all(k, k in ['name', 'projected']) && "
            f"{_shape('v.projected', {'defaultMode': SECRET_MODE}, free=['sources'])} && "
            f"v.projected.sources.all(s, size(s) == 1 && ({source_ok})))",
            "(v.all(k, k in ['name', 'csi']) && "
            "v.csi.all(k, k in ['driver', 'readOnly', 'volumeAttributes']) && "
            f"v.csi.driver == {cel(STORE_DRIVER)} && v.csi.readOnly == true && "
            "v.csi.volumeAttributes.all(k, k == 'secretProviderClass') && "
            "v.csi.volumeAttributes.secretProviderClass in variables.stores && "
            "v.name == 'store-' + v.csi.volumeAttributes.secretProviderClass)",
        ]
    )
    cli = "variables.pod.containers[1]"
    cli_ok = (
        _shape(
            cli,
            {
                "name": "cli",
                "imagePullPolicy": "IfNotPresent",
                "env": [{"name": "AGENTBREEDER_ROLE", "value": "cli-host"}],
                "securityContext": CLI_SECURITY_CONTEXT,
                "resources": RESOURCES,
                "volumeMounts": [
                    {"name": "cli-tmp", "mountPath": "/tmp"},
                    {"name": "cli-shared", "mountPath": CLI_SHARED_DIR, "readOnly": True},
                ],
            },
            CONTAINER_DEFAULTS,
            free=["image"],
        )
        + f" && {cli}.image == {agent}.image"
    )
    pod_keys = [
        "automountServiceAccountToken",
        "enableServiceLinks",
        "securityContext",
        "containers",
        "volumes",
        "serviceAccountName",
        "serviceAccount",
        "imagePullSecrets",
        *POD_DEFAULTS,
    ]
    agent_keys = [
        "name",
        "image",
        "imagePullPolicy",
        "ports",
        "env",
        "securityContext",
        "resources",
        "readinessProbe",
        "volumeMounts",
        *CONTAINER_DEFAULTS,
    ]
    create = [
        Validation(
            _shape(
                "object.spec", {"replicas": 1}, DEPLOYMENT_DEFAULTS, free=["selector", "template"]
            ),
            "spec may hold only replicas 1, the selector and the template",
        ),
        Validation(
            "object.spec.selector == {'matchLabels': {"
            + cel(DEPLOYMENT_LABEL)
            + ": variables.id}}",
            "the selector must be the deployment label only",
        ),
        Validation(
            "object.spec.template.all(k, k in ['metadata', 'spec']) && "
            + _shape(
                "object.spec.template.metadata",
                {},
                {"creationTimestamp": None},
                free=["labels"],
            )
            + " && object.spec.template.metadata.labels == object.metadata.labels",
            "pod labels must equal the Deployment's; pod annotations are refused",
        ),
        Validation(
            f"{pod}.all(k, k in {cel(pod_keys)})",
            "a pod field outside AgentBreeder's template is refused",
        ),
        Validation(
            f"'automountServiceAccountToken' in {pod} && "
            f"{pod}.automountServiceAccountToken == false && "
            f"'enableServiceLinks' in {pod} && {pod}.enableServiceLinks == false && "
            f"'securityContext' in {pod} && {pod}.securityContext == {cel(POD_SECURITY_CONTEXT)}",
            "the pod security context must be AgentBreeder's",
        ),
        Validation(
            " && ".join(
                f"(!({cel(k)} in {pod}) || {pod}[{cel(k)}] == {cel(v)})"
                for k, v in POD_DEFAULTS.items()
            ),
            "restartPolicy, dnsPolicy, schedulerName and the grace period must be the defaults",
        ),
        Validation(
            f"(!('serviceAccountName' in {pod}) || {pod}.serviceAccountName == 'default' || "
            f"{pod}.serviceAccountName in variables.sas) && "
            f"(!('serviceAccount' in {pod}) || ('serviceAccountName' in {pod} && "
            f"{pod}.serviceAccount == {pod}.serviceAccountName))",
            "the pod may run only as default or the agent's granted service account",
        ),
        Validation(
            f"!('imagePullSecrets' in {pod}) || (params.data.pull != '' && "
            f"{pod}.imagePullSecrets == [{{'name': params.data.pull}}])",
            "imagePullSecrets may name only the granted pull Secret",
        ),
        Validation(
            f"(size({pod}.containers) == 1 || (size({pod}.containers) == 2 && ({cli_ok}))) && "
            f"{agent}.name == 'agent'",
            "the pod holds the agent container (and, for two-uid runtimes, "
            "the cli container) only",
        ),
        Validation(
            f"{agent}.all(k, k in {cel(agent_keys)}) && "
            f"'imagePullPolicy' in {agent} && {agent}.imagePullPolicy == 'IfNotPresent' && "
            f"'securityContext' in {agent} && "
            f"{agent}.securityContext == {cel(CONTAINER_SECURITY_CONTEXT)} && "
            f"'resources' in {agent} && {agent}.resources == {cel(RESOURCES)} && "
            + " && ".join(
                f"(!({cel(k)} in {agent}) || {agent}[{cel(k)}] == {cel(v)})"
                for k, v in CONTAINER_DEFAULTS.items()
            ),
            "the agent container must be AgentBreeder's (no command, args, "
            "envFrom or probes but readiness)",
        ),
        Validation(
            f"size({agent}.ports) == 1 && "
            f"{_shape(agent + '.ports[0]', {'protocol': 'TCP'}, free=['containerPort'])}",
            "the agent container has exactly one TCP port",
        ),
        Validation(
            f"'readinessProbe' in {agent} && "
            + _shape(agent + ".readinessProbe", READINESS, PROBE_DEFAULTS, free=["httpGet"])
            + " && "
            + _shape(
                agent + ".readinessProbe.httpGet", {}, {"scheme": "HTTP"}, free=["path", "port"]
            )
            + f" && {agent}.readinessProbe.httpGet.port == {agent}.ports[0].containerPort",
            "the readiness probe is an HTTP GET of the agent's port only",
        ),
        Validation(
            f"{agent}.image.startsWith("
            "params.data.registry + '/' + variables.agent + '@sha256:') && "
            f"size({agent}.image) == size(params.data.registry + '/' + variables.agent) + 72 && "
            f"{agent}.image.matches('@sha256:[0-9a-f]{{64}}$')",
            "the image must be <registry>/<agent>@sha256:<digest>",
        ),
        Validation(
            f"!('env' in {agent}) || {agent}.env.all(e, e.all(k, k in ['name', 'value']))",
            "environment variables are literal values only (no valueFrom)",
        ),
        Validation(
            f"{agent}.volumeMounts.all(m, m.all(k, k in ['name', 'mountPath', 'readOnly']) && "
            f"({mount_ok}))",
            "a volume mount outside AgentBreeder's template is refused",
        ),
        Validation(
            f"{pod}.volumes.all(v, {volume_ok})",
            "a volume outside AgentBreeder's template is refused",
        ),
        Validation(
            "object.metadata.annotations.all(k, "
            f"k in {cel([SPEC_ANNOTATION, IMAGE_ANNOTATION])}) && "
            f"size(object.metadata.annotations) == 2 && "
            f"object.metadata.annotations[{cel(IMAGE_ANNOTATION)}] == {agent}.image",
            "annotations must be exactly io.agentbreeder/spec and io.agentbreeder/image",
        ),
    ]
    return Policy(
        name="ab-deployments",
        resources=(("apps", ("deployments", "deployments/scale")),),
        variables=(
            *_identity_variables(namespace),
            *_grant_variables("object.metadata.labels"),
            ("pod", "object.spec.template.spec"),
        ),
        validations=(
            Validation("variables.isDeployer", "only the deployer may write Deployments"),
            _NO_UPDATE,
            *_deployer_metadata(annotations=True),
            *(Validation(_guard("CREATE", v.expression), v.message) for v in create),
            Validation(
                _guard("DELETE", _owned_delete()), "only AgentBreeder's objects may be deleted"
            ),
            Validation(
                _guard(
                    "DELETE",
                    "has(request.options.propagationPolicy) && "
                    "request.options.propagationPolicy == 'Foreground' && "
                    "!has(request.options.orphanDependents)",
                ),
                "a Deployment is deleted only with foreground propagation",
            ),
        ),
    )


def _services(namespace: str) -> Policy:
    spec = "object.spec"
    return Policy(
        name="ab-services",
        resources=(("", ("services",)),),
        variables=(*_identity_variables(namespace), *_grant_variables("object.metadata.labels")),
        validations=(
            Validation("variables.isDeployer", "only the deployer may write Services"),
            _NO_UPDATE,
            *_deployer_metadata(),
            Validation(
                _guard(
                    "CREATE",
                    _shape(
                        spec,
                        {"type": "ClusterIP"},
                        SERVICE_DEFAULTS,
                        free=["selector", "ports", "clusterIP", "clusterIPs"],
                    )
                    + f" && {spec}.selector == {{{cel(DEPLOYMENT_LABEL)}: variables.id}}"
                    + f" && (!('clusterIP' in {spec}) || {spec}.clusterIP.matches({cel(_IPV4)}))"
                    + f" && (!('clusterIPs' in {spec}) || ('clusterIP' in {spec} && "
                    f"{spec}.clusterIPs == [{spec}.clusterIP]))",
                ),
                "a Service is a ClusterIP Service selecting its own deployment only",
            ),
            Validation(
                _guard(
                    "CREATE",
                    f"size({spec}.ports) == 1 && "
                    + _shape(spec + ".ports[0]", {"protocol": "TCP"}, free=["port", "targetPort"])
                    + f" && {spec}.ports[0].targetPort == {spec}.ports[0].port",
                ),
                "a Service has exactly one TCP port, equal to its target port",
            ),
            Validation(
                _guard("DELETE", _owned_delete()), "only AgentBreeder's objects may be deleted"
            ),
        ),
    )


def _egress_rule_ok() -> str:
    """One deployer egress rule: DNS, node-local DNS, a granted chart service, the open
    `0.0.0.0/0 except ⊇ blocked` rule, or (kit, #1083 F1b) the stack's agents network."""
    dns = f"r == {cel({'to': [DNS_PEER], 'ports': DNS_PORTS})}"
    # Each value is wrapped in dyn(): Kubernetes refuses a map literal whose values differ in
    # type (#1204), and the equality still compares every key and value.
    nodedns = (
        "(params.data.nodedns != '' && r == {'to': dyn([{'ipBlock': {'cidr': "
        f"params.data.nodedns + '/32'}}}}]), 'ports': dyn({cel(DNS_PORTS)})}})"
    )
    service = (
        "(r.all(k, k in ['to', 'ports']) && size(r.to) == 1 && r.to[0].all(k, "
        "k == 'podSelector') && "
        "r.to[0].podSelector.all(k, k == 'matchLabels') && "
        "size(r.to[0].podSelector.matchLabels) == 1 && "
        f"{cel(CHART_SERVICE_LABEL)} in r.to[0].podSelector.matchLabels && "
        "size(r.ports) == 1 && r.ports[0].all(k, k in ['protocol', 'port']) && "
        "r.ports[0].protocol == 'TCP' && type(r.ports[0].port) == int && "
        f"(r.to[0].podSelector.matchLabels[{cel(CHART_SERVICE_LABEL)}] + ':' + "
        "string(r.ports[0].port)) in variables.uses)"
    )
    open_rule = (
        "(r.all(k, k == 'to') && size(r.to) == 1 && r.to[0].all(k, k == 'ipBlock') && "
        "r.to[0].ipBlock.all(k, k in ['cidr', 'except']) && "
        "r.to[0].ipBlock.cidr == '0.0.0.0/0' && "
        "size(variables.blocked) > 0 && 'except' in r.to[0].ipBlock && "
        "variables.blocked.all(b, b in r.to[0].ipBlock.except))"
    )
    # Each peer must be exactly one `kit_destinations` computed (the agents range minus every
    # blocked range inside it), so a deployer cannot drop an `except` entry.
    kit = (
        "(r.all(k, k == 'to') && size(r.to) >= 1 && size(variables.kit) > 0 && "
        "r.to.all(p, p.all(k, k == 'ipBlock') && p.ipBlock.all(k, k in ['cidr', 'except']) && "
        "(p.ipBlock.cidr + '|' + ('except' in p.ipBlock ? p.ipBlock.except.join(',') : '')) "
        "in variables.kit))"
    )
    return " || ".join([dns, nodedns, service, open_rule, kit])


def _canary_selector(expr: str) -> str:
    """`expr` is a label selector on exactly one canary role."""
    return (
        f"({expr}.all(k, k == 'matchLabels') && size({expr}.matchLabels) == 1 && "
        f"{cel(CANARY_LABEL)} in {expr}.matchLabels && "
        f"{expr}.matchLabels[{cel(CANARY_LABEL)}] in {cel(list(CANARY_ROLES))})"
    )


def _canary_peer(expr: str) -> str:
    return f"({expr}.all(k, k == 'podSelector') && {_canary_selector(expr + '.podSelector')})"


def _canary_ports(expr: str) -> str:
    return (
        f"(!('ports' in {expr}) || {expr}.ports.all(q, q.all(k, k in ['protocol', 'port']) && "
        "q.protocol == 'TCP' && type(q.port) == int))"
    )


def _networkpolicies(namespace: str) -> Policy:
    spec = "object.spec"
    deployer = [
        Validation(
            _shape(
                spec,
                {"policyTypes": ["Ingress", "Egress"]},
                free=["podSelector", "ingress", "egress"],
            )
            + " && "
            f"{spec}.podSelector == {{'matchLabels': {{{cel(DEPLOYMENT_LABEL)}: variables.id}}}}"
            + f" && (!('ingress' in {spec}) || size({spec}.ingress) == 0)",
            "the policy selects its own deployment, both directions, with no ingress",
        ),
        Validation(
            f"'egress' in {spec} && {spec}.egress.all(r, {_egress_rule_ok()})",
            "an egress rule outside AgentBreeder's fixed shapes is refused",
        ),
    ]
    canary_name = "object.metadata.name.matches('^ab-canary-[0-9]{1,2}$')"
    canary = [
        Validation(
            f"{canary_name} && object.metadata.labels == "
            + cel({MANAGED_BY: "agentbreeder", CANARY_LABEL: "policy"})
            + " && "
            + " && ".join(
                _absent_or_empty("object.metadata", key)
                for key in ("annotations", "finalizers", "ownerReferences")
            ),
            "a canary policy is named ab-canary-<n> and carries the canary labels only",
        ),
        Validation(
            _shape(
                spec,
                {"policyTypes": ["Ingress", "Egress"]},
                free=["podSelector", "ingress", "egress"],
            )
            + f" && {_canary_selector(spec + '.podSelector')}",
            "a canary policy selects one canary role",
        ),
        Validation(
            f"(!('ingress' in {spec}) || {spec}.ingress.all(r, r.all(k, k in ['from', "
            "'ports']) && "
            f"'from' in r && size(r.from) >= 1 && r.from.all(p, {_canary_peer('p')}) && "
            f"{_canary_ports('r')})) && "
            f"(!('egress' in {spec}) || {spec}.egress.all(r, r.all(k, k in ['to', 'ports']) && "
            "'to' in r && size(r.to) >= 1 && "
            f"(r.to.all(p, {_canary_peer('p')}) && {_canary_ports('r')} || "
            "(size(r.to) == 1 && !('ports' in r) && r.to[0].all(k, k == 'ipBlock') && "
            "r.to[0].ipBlock.cidr == '0.0.0.0/0' && 'except' in r.to[0].ipBlock && "
            "variables.blocked.all(b, b in r.to[0].ipBlock.except)))))",
            "canary rules peer only with canary pods or the fixed public rule",
        ),
    ]
    canary_delete = (
        "has(oldObject.metadata.labels) && "
        f"{cel(CANARY_LABEL)} in oldObject.metadata.labels && "
        "oldObject.metadata.name.startsWith('ab-canary-')"
    )
    return Policy(
        name="ab-networkpolicies",
        resources=(("networking.k8s.io", ("networkpolicies",)),),
        variables=(
            *_identity_variables(namespace),
            *_grant_variables("object.metadata.labels"),
        ),
        validations=(
            _NO_UPDATE,
            *(
                Validation(f"!variables.isDeployer || ({v.expression})", v.message)
                for v in _deployer_metadata()
            ),
            *(
                Validation(f"!variables.isDeployer || {_guard('CREATE', v.expression)}", v.message)
                for v in deployer
            ),
            Validation(
                f"!variables.isDeployer || {_guard('DELETE', _owned_delete())}",
                "only AgentBreeder's objects may be deleted",
            ),
            *(
                Validation(f"!variables.isCanary || {_guard('CREATE', v.expression)}", v.message)
                for v in canary
            ),
            Validation(
                f"!variables.isCanary || {_guard('DELETE', canary_delete)}",
                "the canary deletes only canary objects",
            ),
        ),
    )


def _secrets(namespace: str) -> Policy:
    owner = "object.metadata.ownerReferences"
    return Policy(
        name="ab-secrets",
        resources=(("", ("secrets",)),),
        variables=(*_identity_variables(namespace), *_grant_variables("object.metadata.labels")),
        validations=(
            Validation("variables.isDeployer", "only the deployer may write Secrets"),
            Validation(
                "request.operation == 'CREATE'",
                "Secrets are created only, never changed or deleted",
            ),
            *_deployer_metadata(owners=True),
            Validation(
                "object.all(k, k in ['apiVersion', 'kind', 'metadata', 'type', 'immutable', "
                "'data']) && "
                "'type' in object && object.type == 'Opaque' && "
                "(!('immutable' in object) || object.immutable == true)",
                "only an Opaque Secret of data is accepted",
            ),
            Validation(
                f"has({owner}) && size({owner}) == 1 && "
                f"{owner}[0].all(k, k in ['apiVersion', 'kind', 'name', 'uid', "
                "'blockOwnerDeletion']) && "
                f"{owner}[0].apiVersion == 'apps/v1' && {owner}[0].kind == 'Deployment' && "
                f"{owner}[0].name == object.metadata.name && "
                f"'blockOwnerDeletion' in {owner}[0] && {owner}[0].blockOwnerDeletion == true",
                "a Secret is owned by its own Deployment, with blockOwnerDeletion",
            ),
        ),
    )


def _pods(namespace: str) -> Policy:
    role = "object.metadata.labels[" + cel(CANARY_LABEL) + "]"
    pod = "object.spec"
    container = "object.spec.containers[0]"
    pairs = "[1, 3, 5, 7, 9, 11]"
    args_ok = (
        f"(({role} == 'server' || {role} == 'target') && "
        f"{container}.args == {cel(CANARY_ARGS)}) || "
        f"({role} == 'client' && size({container}.args) >= 3 && size({container}.args) <= 13 && "
        f"size({container}.args) % 2 == 1 && {container}.args[0] == 'probe' && "
        f"{pairs}.all(i, i >= size({container}.args) || "
        f"({container}.args[i].matches({cel(_IPV4)}) && "
        f"{container}.args[i + 1].matches({cel(_PORT)}))))"
    )
    defaults = {k: v for k, v in CANARY_POD_DEFAULTS.items() if k != "restartPolicy"}
    create = [
        Validation(
            f"has(object.metadata.labels) && {cel(CANARY_LABEL)} in object.metadata.labels && "
            f"{role} in {cel(list(CANARY_ROLES))} && "
            f"object.metadata.name == 'ab-canary-' + {role} && "
            "object.metadata.labels == {"
            + f"{cel(MANAGED_BY)}: dyn('agentbreeder'), {cel(CANARY_LABEL)}: dyn({role})"
            + "} && "
            + " && ".join(
                _absent_or_empty("object.metadata", key)
                for key in ("annotations", "finalizers", "ownerReferences")
            ),
            "a canary pod is ab-canary-<role> with the canary labels only",
        ),
        Validation(
            _shape(
                pod,
                {
                    "automountServiceAccountToken": False,
                    "securityContext": POD_SECURITY_CONTEXT,
                    "volumes": [TMP_VOLUME],
                },
                defaults,
                free=["containers", "restartPolicy"],
            )
            + f" && 'restartPolicy' in {pod} && "
            f"{pod}.restartPolicy == ({role} == 'client' ? 'Never' : 'Always')",
            "a canary pod field outside the canary template is refused",
        ),
        Validation(
            f"size({pod}.containers) == 1 && "
            + _shape(
                container,
                {
                    "name": "echo",
                    "imagePullPolicy": "IfNotPresent",
                    "securityContext": CONTAINER_SECURITY_CONTEXT,
                    "resources": RESOURCES,
                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                },
                CONTAINER_DEFAULTS,
                free=["image", "args"],
            )
            + f" && params.data.echo != '' && {container}.image == params.data.echo && "
            f"({args_ok})",
            "the canary container is the pinned echo image with the canary arguments",
        ),
    ]
    return Policy(
        name="ab-pods",
        resources=(("", ("pods", "pods/ephemeralcontainers")),),
        variables=tuple(_identity_variables(namespace))
        + (("blocked", "params.data.blocked == '' ? [] : params.data.blocked.split(',')"),),
        validations=(
            Validation("variables.isCanary", "only the canary may write Pods"),
            _NO_UPDATE,
            *(Validation(_guard("CREATE", v.expression), v.message) for v in create),
            Validation(
                _guard(
                    "DELETE",
                    "has(oldObject.metadata.labels) && "
                    f"{cel(CANARY_LABEL)} in oldObject.metadata.labels && "
                    "oldObject.metadata.name.startsWith('ab-canary-')",
                ),
                "the canary deletes only canary pods",
            ),
        ),
    )


TYPED = (
    ("apps", ("deployments", "deployments/scale")),
    ("", ("services",)),
    ("networking.k8s.io", ("networkpolicies",)),
    ("", ("secrets",)),
    ("", ("pods", "pods/ephemeralcontainers")),
)


def _deny_other(namespace: str) -> Policy:
    return Policy(
        name="ab-deny-other",
        resources=(("*", ("*/*",)),),
        exclude=TYPED,
        # The admin writes `ab-grants` (#1204). Kubernetes resolves a binding's parameters
        # before its matchConditions, so while `ab-grants` is missing every binding denies every
        # write in the namespace, the admin's included, and `ab-grants` could never be created.
        # Excluding that one name by `resourceNames` lets it be written first. The deployer and
        # the canary have no RBAC verb that writes a ConfigMap, and `ab-grants-guard` (which has
        # no parameters, so it cannot lock anyone out) still refuses their writes to it.
        exclude_named=(("", ("configmaps",), (GRANTS,)),),
        validations=(
            Validation("false", "the deployer and the canary may write no other kind of object"),
        ),
    )


def _grants_guard(namespace: str) -> Policy:
    """`ab-grants` is the input of every other policy; `ab-deny-other` excludes it so the admin
    can write it first (#1204). This policy keeps a second layer behind RBAC: the deployer and
    the canary never create, change or delete it. It reads no parameters."""
    return Policy(
        name="ab-grants-guard",
        resources=(),
        named=(("", ("configmaps",), (GRANTS,)),),
        params=False,
        validations=(Validation("false", "the deployer and the canary may not write ab-grants"),),
    )


BUILDERS = (
    _deployments,
    _services,
    _networkpolicies,
    _secrets,
    _pods,
    _deny_other,
    _grants_guard,
)
POLICY_NAMES = (
    "ab-deployments",
    "ab-services",
    "ab-networkpolicies",
    "ab-secrets",
    "ab-pods",
    "ab-deny-other",
    "ab-grants-guard",
)


def _named_rules(named) -> list[dict]:
    return [
        {**rule, "resourceNames": list(names)}
        for group, resources, names in named
        for rule in _rules(((group, resources),))
    ]


def _rules(resources) -> list[dict]:
    return [
        {
            "apiGroups": [group],
            "apiVersions": ["*"],
            "resources": list(names),
            "operations": ["CREATE", "UPDATE", "DELETE"],
            "scope": "Namespaced",
        }
        for group, names in resources
    ]


def _namespace_selector(namespace: str) -> dict:
    return {"matchLabels": {"kubernetes.io/metadata.name": namespace}}


def policy_objects(namespace: str) -> list[Policy]:
    _check_namespace(namespace)
    return [build(namespace) for build in BUILDERS]


def policies(namespace: str) -> list[dict]:
    """The seven `ValidatingAdmissionPolicy` objects and their bindings for `namespace` (§7.2,
    #1204)."""
    rendered: list[dict] = []
    users = [username(namespace, "deployer"), username(namespace, "canary")]
    for policy in policy_objects(namespace):
        constraints = {
            "matchPolicy": "Equivalent",
            "namespaceSelector": _namespace_selector(namespace),
            "objectSelector": {},
            "resourceRules": _rules(policy.resources) + _named_rules(policy.named),
        }
        if policy.exclude or policy.exclude_named:
            constraints["excludeResourceRules"] = _rules(policy.exclude) + _named_rules(
                policy.exclude_named
            )
        spec = {
            "failurePolicy": "Fail",
            "paramKind": {"apiVersion": "v1", "kind": "ConfigMap"},
            "matchConstraints": constraints,
            # Bindings have no user selector: the policy applies only to the two identities.
            "matchConditions": [
                {
                    "name": "agentbreeder-identities",
                    "expression": f"request.userInfo.username in {cel(users)}",
                }
            ],
            "variables": [{"name": k, "expression": v} for k, v in policy.variables],
            "validations": [v.render() for v in policy.validations],
        }
        if not spec["variables"]:
            del spec["variables"]
        if not policy.params:
            del spec["paramKind"]
        binding = {
            "policyName": policy.name,
            "paramRef": {
                "name": GRANTS,
                "namespace": namespace,
                "parameterNotFoundAction": "Deny",
            },
            "validationActions": ["Deny"],
            "matchResources": {
                "matchPolicy": "Equivalent",
                "namespaceSelector": _namespace_selector(namespace),
                "objectSelector": {},
            },
        }
        if not policy.params:
            del binding["paramRef"]
        rendered.append(
            {
                "apiVersion": "admissionregistration.k8s.io/v1",
                "kind": "ValidatingAdmissionPolicy",
                "metadata": {"name": policy.name, "labels": {MANAGED_BY: "agentbreeder"}},
                "spec": spec,
            }
        )
        rendered.append(
            {
                "apiVersion": "admissionregistration.k8s.io/v1",
                "kind": "ValidatingAdmissionPolicyBinding",
                "metadata": {"name": policy.name, "labels": {MANAGED_BY: "agentbreeder"}},
                "spec": binding,
            }
        )
    return rendered


# -- digest ---------------------------------------------------------------------------------------


def normalise(item: Mapping) -> dict:
    """What the digest covers: kind, name and `spec`, with the defaults the API server fills in,
    so a live object (with `managedFields`, status and defaults) digests as the rendered one."""
    spec = copy.deepcopy(dict(item.get("spec") or {}))
    kind = item.get("kind")
    if kind == "ValidatingAdmissionPolicy":
        spec.setdefault("failurePolicy", "Fail")
        constraints = spec.setdefault("matchConstraints", {})
        constraints.setdefault("matchPolicy", "Equivalent")
        constraints.setdefault("namespaceSelector", {})
        constraints.setdefault("objectSelector", {})
        for key in ("resourceRules", "excludeResourceRules"):
            for rule in constraints.get(key) or []:
                rule.setdefault("scope", "*")
    elif kind == "ValidatingAdmissionPolicyBinding":
        match = spec.setdefault("matchResources", {})
        match.setdefault("matchPolicy", "Equivalent")
        match.setdefault("namespaceSelector", {})
        match.setdefault("objectSelector", {})
    return {"kind": kind, "name": (item.get("metadata") or {}).get("name"), "spec": spec}


def digest(objects: Iterable[Mapping]) -> str:
    body = sorted(
        (normalise(item) for item in objects), key=lambda entry: (entry["kind"], entry["name"])
    )
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


# -- canary templates (K6 runs them; K2's policy admits them) -------------------------------------


def canary_pod(
    namespace: str, role: str, echo: str, targets: Iterable[tuple[str, int]] = ()
) -> dict:
    if role not in CANARY_ROLES:
        raise AdmissionError(f"unknown canary role {role}")
    args = list(CANARY_ARGS)
    if role == "client":
        args = ["probe", *(str(part) for pair in targets for part in pair)]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"ab-canary-{role}",
            "namespace": namespace,
            "labels": {MANAGED_BY: "agentbreeder", CANARY_LABEL: role},
        },
        "spec": {
            "automountServiceAccountToken": False,
            "restartPolicy": "Never" if role == "client" else "Always",
            "securityContext": copy.deepcopy(POD_SECURITY_CONTEXT),
            "containers": [
                {
                    "name": "echo",
                    "image": echo,
                    "imagePullPolicy": "IfNotPresent",
                    "args": args,
                    "securityContext": copy.deepcopy(CONTAINER_SECURITY_CONTEXT),
                    "resources": copy.deepcopy(RESOURCES),
                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                }
            ],
            "volumes": [copy.deepcopy(TMP_VOLUME)],
        },
    }


def canary_policy(namespace: str, number: int, role: str, *, ingress=(), egress=()) -> dict:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": f"ab-canary-{number}",
            "namespace": namespace,
            "labels": {MANAGED_BY: "agentbreeder", CANARY_LABEL: "policy"},
        },
        "spec": {
            "podSelector": {"matchLabels": {CANARY_LABEL: role}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": list(ingress),
            "egress": list(egress),
        },
    }


def canary_peer(role: str) -> dict:
    return {"podSelector": {"matchLabels": {CANARY_LABEL: role}}}


# -- paired probes (§7.4) -------------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    """A server-side dry-run pair. `admitted` must be admitted and `refused` refused by
    `policy`. For a delete, `delete` names the object and the propagation of each side."""

    name: str
    identity: str  # "deployer" or "canary"
    policy: str
    operation: str  # "CREATE" or "DELETE"
    admitted: Mapping | None = None
    refused: Mapping | None = None
    delete: Mapping | None = None


def probe_grants(registry: str, blocked: Iterable[str], echo: str, **extra) -> Grants:
    """The `ab-grants` fixture the probes run against: the reserved `ab-probe` agent, granted a
    binding, a service and nothing else."""
    return Grants(
        registry=registry,
        blocked=tuple(blocked),
        echo=echo,
        agents={
            PROBE_AGENT: AgentGrant(bindings=("ab-svc-probe-binding",), uses=("probe-svc:8080",)),
            "other": AgentGrant(),
        },
        **extra,
    )


PROBE_ID = "a" * 32


def probe_objects(namespace: str, grants: Grants, identifier: str = PROBE_ID) -> dict[str, dict]:
    """AgentBreeder's own objects for the probe agent (Deployment with a mounted Secret, the
    Service, the NetworkPolicy, the Secret), as `_objects` writes them."""
    from types import SimpleNamespace

    from agentbreeder import kubernetes
    from agentbreeder.secret_files import SecretMount

    record = {"id": identifier, "container": f"agentbreeder-{identifier}", "digest": "f" * 64}
    image = f"{grants.registry}/{PROBE_AGENT}@sha256:" + "b" * 64
    manifest = SimpleNamespace(
        name=PROBE_AGENT, image=image, port=8080, health_path="/health", runtime=None
    )
    mount = SecretMount(("TOKEN",), "token", None, f"{MOUNT_ROOT}/token")  # type: ignore[arg-type]
    items = kubernetes._objects(record, manifest, namespace, {"A": "1"}, [mount], grants.blocked)
    objects = {item["kind"]: item for item in items}
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": record["container"],
            "namespace": namespace,
            "labels": kubernetes._labels(record, manifest),
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": record["container"],
                    "uid": "00000000-0000-0000-0000-000000000000",
                    "blockOwnerDeletion": True,
                }
            ],
        },
        "type": "Opaque",
        "immutable": True,
        "data": {"token": "eA=="},
    }
    objects["Secret"] = secret
    return objects


def _init_container(deployment: Mapping) -> dict:
    """The agent container as an init container that core validation accepts."""
    container = copy.deepcopy(deployment["spec"]["template"]["spec"]["containers"][0])
    container["name"] = "init"
    for key in ("readinessProbe", "ports"):
        container.pop(key, None)
    return container


def _variant(base: Mapping, change) -> dict:
    item = copy.deepcopy(dict(base))
    change(item)
    return item


def probes(namespace: str, grants: Grants) -> list[Probe]:
    """Every pair of §7.4 that a dry-run can show; each refused request differs from its
    admitted one only in the refused field."""
    own = probe_objects(namespace, grants)
    deployment, service, policy, secret = (
        own["Deployment"],
        own["Service"],
        own["NetworkPolicy"],
        own["Secret"],
    )

    def pod(item):
        return item["spec"]["template"]["spec"]

    def agent(item):
        return pod(item)["containers"][0]

    def projected(item):
        return next(v for v in pod(item)["volumes"] if v["name"] == "secrets")["projected"]

    def label(item, key, value):
        item["metadata"]["labels"][key] = value
        item["spec"]["template"]["metadata"]["labels"][key] = value

    def granted_uses(item):
        label(item, USES_PREFIX + "probe-svc", "true")

    image = agent(deployment)["image"]
    deployments: list[tuple[str, Any, Any]] = [
        (
            "other deployment's Secret",
            None,
            lambda d: projected(d)["sources"].append(
                {"secret": {"name": "agentbreeder-" + "b" * 32}}
            ),
        ),
        (
            "operator-only Secret",
            None,
            lambda d: projected(d)["sources"].append(
                {"secret": {"name": "ab-svc-litellm-master"}}
            ),
        ),
        (
            "Helm release Secret",
            None,
            lambda d: projected(d)["sources"].append(
                {"secret": {"name": "sh.helm.release.v1.ab.v1"}}
            ),
        ),
        (
            "granted binding",
            lambda d: projected(d)["sources"].append(
                {
                    "secret": {
                        "name": "ab-svc-probe-binding",
                        "items": [{"key": "value", "path": "database_url"}],
                    }
                }
            ),
            None,
        ),
        (
            "serviceAccountName ab-installer",
            None,
            lambda d: pod(d).update(serviceAccountName="ab-installer"),
        ),
        (
            "persistentVolumeClaim volume",
            None,
            lambda d: pod(d)["volumes"].append(
                {"name": "data", "persistentVolumeClaim": {"claimName": "x"}}
            ),
        ),
        ("envFrom", None, lambda d: agent(d).update(envFrom=[{"secretRef": {"name": "x"}}])),
        (
            "env valueFrom",
            None,
            lambda d: agent(d)["env"].append(
                {"name": "B", "valueFrom": {"secretKeyRef": {"name": "x", "key": "y"}}}
            ),
        ),
        # Valid for core validation (a name of its own; no probe or port on an init
        # container), so the refusal is ab-deployments' (#1204).
        ("initContainer", None, lambda d: pod(d).update(initContainers=[_init_container(d)])),
        ("command", None, lambda d: agent(d).update(command=["/bin/sh"])),
        (
            "livenessProbe",
            None,
            lambda d: agent(d).update(livenessProbe=copy.deepcopy(agent(d)["readinessProbe"])),
        ),
        ("nodeName", None, lambda d: pod(d).update(nodeName="node")),
        (
            "priorityClassName",
            None,
            lambda d: pod(d).update(priorityClassName="system-node-critical"),
        ),
        ("tolerations", None, lambda d: pod(d).update(tolerations=[{"operator": "Exists"}])),
        (
            "pod annotation",
            None,
            lambda d: d["spec"]["template"]["metadata"].update(
                annotations={"k8s.v1.cni.cncf.io/networks": "x"}
            ),
        ),
        (
            "Deployment annotation",
            None,
            lambda d: d["metadata"]["annotations"].update({"other": "x"}),
        ),
        ("hostPort", None, lambda d: agent(d)["ports"][0].update(hostPort=8080)),
        (
            "readiness probe host",
            None,
            lambda d: agent(d)["readinessProbe"]["httpGet"].update(host="169.254.169.254"),
        ),
        ("volumeMount subPath", None, lambda d: agent(d)["volumeMounts"][1].update(subPath="x")),
        (
            "volumeMount elsewhere",
            None,
            lambda d: agent(d)["volumeMounts"][1].update(mountPath="/etc"),
        ),
        ("resources", None, lambda d: agent(d)["resources"]["limits"].update(cpu="8")),
        (
            "image by tag",
            None,
            lambda d: agent(d).update(image=f"{grants.registry}/{PROBE_AGENT}:latest"),
        ),
        (
            "image from another agent",
            None,
            lambda d: agent(d).update(image=image.replace(f"/{PROBE_AGENT}@", "/other@")),
        ),
        (
            "image from mirror",
            None,
            lambda d: agent(d).update(image=image.replace(f"/{PROBE_AGENT}@", "/mirror/echo@")),
        ),
        ("ungranted agent", None, lambda d: label(d, NAME_LABEL, "ungranted")),
        (
            "foreign pull Secret",
            None,
            lambda d: pod(d).update(imagePullSecrets=[{"name": "foreign"}]),
        ),
        ("granted uses label", granted_uses, None),
        ("ungranted uses-postgres", None, lambda d: label(d, USES_PREFIX + "postgres", "true")),
        ("chart-service label", None, lambda d: label(d, CHART_SERVICE_LABEL, "litellm")),
        ("paused", None, lambda d: d["spec"].update(paused=True)),
        ("two replicas", None, lambda d: d["spec"].update(replicas=2)),
        (
            "generateName",
            None,
            lambda d: (
                d["metadata"].pop("name"),
                d["metadata"].update(generateName="agentbreeder-"),
            ),
        ),
    ]
    result: list[Probe] = []
    for name, admit, refuse in deployments:
        admitted = _variant(deployment, admit) if admit else deployment
        refused = _variant(admitted, refuse) if refuse else None
        result.append(Probe(name, "deployer", "ab-deployments", "CREATE", admitted, refused))
    for name, change in (
        ("LoadBalancer Service", lambda s: s["spec"].update(type="LoadBalancer")),
        ("externalIPs", lambda s: s["spec"].update(externalIPs=["203.0.113.9"])),
        (
            "Service selecting a chart pod",
            lambda s: s["spec"].update(selector={CHART_SERVICE_LABEL: "postgres"}),
        ),
    ):
        result.append(
            Probe(name, "deployer", "ab-services", "CREATE", service, _variant(service, change))
        )

    def egress(item):
        return item["spec"]["egress"]

    service_rule = {
        "to": [{"podSelector": {"matchLabels": {CHART_SERVICE_LABEL: "probe-svc"}}}],
        "ports": [{"protocol": "TCP", "port": 8080}],
    }
    granted_policy = _variant(policy, lambda p: egress(p).append(copy.deepcopy(service_rule)))
    for name, base, change in (
        ("egress to everything", policy, lambda p: egress(p).append({})),
        ("port-less service rule", granted_policy, lambda p: egress(p)[-1].pop("ports")),
        ("endPort", granted_policy, lambda p: egress(p)[-1]["ports"][0].update(endPort=9000)),
        (
            "namespaceSelector peer",
            granted_policy,
            lambda p: egress(p)[-1]["to"][0].update(namespaceSelector={}),
        ),
        (
            "ungranted service port",
            granted_policy,
            lambda p: egress(p)[-1]["ports"][0].update(port=5432),
        ),
        (
            "free ipBlock",
            policy,
            lambda p: egress(p)[1]["to"][0]["ipBlock"].update({"except": []}),
        ),
        (
            "policy selecting chart-service",
            policy,
            lambda p: p["spec"].update(
                podSelector={"matchLabels": {CHART_SERVICE_LABEL: "postgres"}}
            ),
        ),
        ("ingress allowed", policy, lambda p: p["spec"].update(ingress=[{}])),
    ):
        result.append(
            Probe(name, "deployer", "ab-networkpolicies", "CREATE", base, _variant(base, change))
        )
    for name, change in (
        (
            # Core validation requires the account annotation on this type (#1204), so the
            # refusal is ab-secrets' (its type and its annotation).
            "service-account-token Secret",
            lambda s: (
                s.update(type="kubernetes.io/service-account-token"),
                s["metadata"].update(
                    annotations={"kubernetes.io/service-account.name": "ab-installer"}
                ),
            ),
        ),
        ("Secret named like ab-svc-*", lambda s: s["metadata"].update(name="ab-svc-postgres")),
        ("Secret without its owner", lambda s: s["metadata"].pop("ownerReferences")),
        (
            "Secret owned by another Deployment",
            lambda s: s["metadata"]["ownerReferences"][0].update(name="agentbreeder-" + "b" * 32),
        ),
        (
            "Secret annotation",
            lambda s: s["metadata"].update(
                annotations={"kubernetes.io/service-account.name": "ab-installer"}
            ),
        ),
    ):
        result.append(
            Probe(name, "deployer", "ab-secrets", "CREATE", secret, _variant(secret, change))
        )
    result += [
        Probe(
            "delete chart Service",
            "deployer",
            "ab-services",
            "DELETE",
            delete={
                "kind": "service",
                "admitted": service["metadata"]["name"],
                "refused": "litellm",
            },
        ),
        Probe(
            "delete ab-baseline",
            "deployer",
            "ab-networkpolicies",
            "DELETE",
            delete={
                "kind": "networkpolicy",
                "admitted": policy["metadata"]["name"],
                "refused": "ab-baseline",
            },
        ),
        Probe(
            "delete with orphan",
            "deployer",
            "ab-deployments",
            "DELETE",
            delete={"kind": "deployment", "admitted": "foreground", "refused": "orphan"},
        ),
        Probe(
            "delete with background",
            "deployer",
            "ab-deployments",
            "DELETE",
            delete={"kind": "deployment", "admitted": "foreground", "refused": "background"},
        ),
    ]
    server = canary_pod(namespace, "server", grants.echo)
    client = canary_pod(namespace, "client", grants.echo, [("10.0.0.1", 8080)])
    for name, base, change in (
        (
            "canary Secret volume",
            server,
            lambda p: p["spec"]["volumes"].append({"name": "s", "secret": {"secretName": "x"}}),
        ),
        (
            "canary serviceAccountName",
            server,
            lambda p: p["spec"].update(serviceAccountName="ab-installer"),
        ),
        (
            "canary env valueFrom",
            server,
            lambda p: p["spec"]["containers"][0].update(
                env=[{"name": "A", "valueFrom": {"secretKeyRef": {"name": "x", "key": "y"}}}]
            ),
        ),
        (
            "canary uses-postgres label",
            server,
            lambda p: p["metadata"]["labels"].update({USES_PREFIX + "postgres": "true"}),
        ),
        ("canary other image", server, lambda p: p["spec"]["containers"][0].update(image=image)),
        (
            "canary args",
            client,
            lambda p: p["spec"]["containers"][0].update(args=["sh", "-c", "x"]),
        ),
    ):
        result.append(Probe(name, "canary", "ab-pods", "CREATE", base, _variant(base, change)))
    blocked = list(grants.blocked)
    open_rule = {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": blocked}}]}
    canary_np = canary_policy(
        namespace,
        1,
        "client",
        egress=[
            {"to": [canary_peer("server")], "ports": [{"protocol": "TCP", "port": 8080}]},
            open_rule,
        ],
    )
    for name, change in (
        (
            "canary free ipBlock",
            lambda p: p["spec"]["egress"][1]["to"][0]["ipBlock"].update({"except": []}),
        ),
        (
            "canary non-canary peer",
            lambda p: p["spec"]["egress"][0]["to"][0]["podSelector"].update(
                matchLabels={CHART_SERVICE_LABEL: "postgres"}
            ),
        ),
    ):
        result.append(
            Probe(
                name,
                "canary",
                "ab-networkpolicies",
                "CREATE",
                canary_np,
                _variant(canary_np, change),
            )
        )
    return result
