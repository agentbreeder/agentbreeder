"""Evaluate AgentBreeder's ValidatingAdmissionPolicies in-process with cel-python (#770 K2).

This is an approximation of the API server, used to test the policies' logic before (and
beside) the opt-in `kind` run, which is the measurement:

- `default()` fills the defaults the API server writes before validating admission, as the
  policy module documents them (`kubernetes_admission.*_DEFAULTS`);
- `review()` applies a policy as the API server does: `matchConditions`, then `variables` in
  order (a variable whose expression errors is left out, so a validation that reads it fails,
  as a lazily evaluated error does on the server), then every validation; a validation that is
  false or errors denies with its message.

Kubernetes' CEL adds `split`, `substring` and `join` (the strings extension); they are
registered here.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

import celpy
from celpy import celtypes

from agentbreeder import kubernetes_admission as admission

ENV = celpy.Environment()


def _split(value, separator):
    return celtypes.ListType([celtypes.StringType(p) for p in str(value).split(str(separator))])


def _substring(value, start):
    return celtypes.StringType(str(value)[int(start) :])


def _join(values, separator):
    return celtypes.StringType(str(separator).join(str(v) for v in values))


FUNCTIONS = {"split": _split, "substring": _substring, "join": _join}
_CACHE: dict[str, object] = {}


def evaluate(expression: str, activation: dict):
    program = _CACHE.get(expression)
    if program is None:
        program = ENV.program(ENV.compile(expression), functions=FUNCTIONS)
        _CACHE[expression] = program
    return program.evaluate(activation)


@dataclass
class Result:
    allowed: bool
    matched: bool
    messages: list[str]


def policy(namespace: str, name: str) -> dict:
    return next(
        item
        for item in admission.policies(namespace)
        if item["kind"] == "ValidatingAdmissionPolicy" and item["metadata"]["name"] == name
    )


def review(
    namespace: str,
    name: str,
    operation: str,
    *,
    obj: dict | None = None,
    old: dict | None = None,
    identity: str = "deployer",
    params: dict | None,
    options: dict | None = None,
    user: str | None = None,
) -> Result:
    spec = policy(namespace, name)["spec"]
    request = {
        "operation": operation,
        "userInfo": {"username": user or admission.username(namespace, identity)},
        "namespace": namespace,
        "options": options if options is not None else {},
    }
    base = {
        "object": celpy.json_to_cel(obj),
        "oldObject": celpy.json_to_cel(old),
        "request": celpy.json_to_cel(request),
    }
    for condition in spec.get("matchConditions") or []:
        if not bool(evaluate(condition["expression"], base)):
            return Result(True, False, [])
    if params is None:  # parameterNotFoundAction: Deny
        return Result(False, True, ["ab-grants is missing"])
    base["params"] = celpy.json_to_cel({"data": params})
    variables: dict = {}
    for item in spec.get("variables") or []:
        activation = {**base, "variables": celtypes.MapType(variables)}
        try:
            variables[celtypes.StringType(item["name"])] = evaluate(item["expression"], activation)
        except Exception:  # noqa: BLE001 -- an error is left unset, as on the server
            continue
    activation = {**base, "variables": celtypes.MapType(variables)}
    messages = []
    for validation in spec["validations"]:
        try:
            outcome = evaluate(validation["expression"], activation)
            passed = isinstance(outcome, celtypes.BoolType) and bool(outcome)
        except Exception:  # noqa: BLE001 -- an evaluation error denies (failurePolicy Fail)
            passed = False
        if not passed:
            messages.append(validation["message"])
    return Result(not messages, True, messages)


# -- the defaults the API server writes before validation ----------------------------------------


def _container(container: dict, pod_defaults: bool) -> None:
    for key, value in admission.CONTAINER_DEFAULTS.items():
        container.setdefault(key, value)
    for port in container.get("ports") or []:
        port.setdefault("protocol", "TCP")
    probe = container.get("readinessProbe")
    if probe:
        for key, value in admission.PROBE_DEFAULTS.items():
            probe.setdefault(key, value)
        probe.get("httpGet", {}).setdefault("scheme", "HTTP")
    if pod_defaults and "@sha256:" in container.get("image", ""):
        container.setdefault("imagePullPolicy", "IfNotPresent")


def _metadata(item: dict) -> None:
    metadata = item.setdefault("metadata", {})
    metadata.setdefault("uid", "11111111-1111-1111-1111-111111111111")
    metadata.setdefault("creationTimestamp", "2026-11-20T00:00:00Z")
    metadata.setdefault("managedFields", [{"manager": "kubectl-create", "operation": "Update"}])


def default(item: dict) -> dict:
    """A copy of `item` as validating admission sees it on CREATE (to measure on kind)."""
    item = copy.deepcopy(item)
    _metadata(item)
    kind = item["kind"]
    if kind == "Deployment":
        spec = item["spec"]
        for key, value in admission.DEPLOYMENT_DEFAULTS.items():
            spec.setdefault(key, copy.deepcopy(value))
        template = spec["template"]
        template.setdefault("metadata", {})["creationTimestamp"] = None
        pod = template["spec"]
        for key, value in admission.POD_DEFAULTS.items():
            pod.setdefault(key, value)
        if "serviceAccountName" in pod:
            pod["serviceAccount"] = pod["serviceAccountName"]
        for container in pod.get("containers") or []:
            _container(container, pod_defaults=False)
    elif kind == "Service":
        spec = item["spec"]
        for key, value in admission.SERVICE_DEFAULTS.items():
            spec.setdefault(key, copy.deepcopy(value))
        for port in spec.get("ports") or []:
            port.setdefault("protocol", "TCP")
            port.setdefault("targetPort", port.get("port"))
    elif kind == "Pod":
        pod = item["spec"]
        for key, value in admission.CANARY_POD_DEFAULTS.items():
            pod.setdefault(key, copy.deepcopy(value))
        for container in pod.get("containers") or []:
            _container(container, pod_defaults=True)
    elif kind == "NetworkPolicy":
        for direction in ("ingress", "egress"):
            for rule in item["spec"].get(direction) or []:
                for port in rule.get("ports") or []:
                    port.setdefault("protocol", "TCP")
    elif kind == "Secret":
        item.setdefault("type", "Opaque")
    return item


POLICY_OF = {
    "Deployment": "ab-deployments",
    "Service": "ab-services",
    "NetworkPolicy": "ab-networkpolicies",
    "Secret": "ab-secrets",
    "Pod": "ab-pods",
}


def create(namespace, item, params, identity="deployer") -> Result:
    return review(
        namespace,
        POLICY_OF[item["kind"]],
        "CREATE",
        obj=default(item),
        identity=identity,
        params=params,
    )
