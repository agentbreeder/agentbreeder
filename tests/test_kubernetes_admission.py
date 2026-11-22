"""The §7.1 Roles and the §7.2 admission policies (#770 K2), evaluated with cel-python.

The CEL runs in-process against objects defaulted as the API server is documented to default
them (`admission_cel.default`). That checks the policies' logic; whether a real API server
admits the same objects is measured by the opt-in kind test
(`tests/test_kind_admission_docker.py`)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

celpy = pytest.importorskip("celpy")

import admission_cel as ac  # noqa: E402

from agentbreeder import cli, kubernetes  # noqa: E402
from agentbreeder import kubernetes_admission as admission  # noqa: E402

NS = "agents"
REGISTRY = "localhost:5001/agentbreeder"
BLOCKED = ("10.244.0.0/16", "172.30.0.0/16", "169.254.169.254/32")
ECHO = f"{REGISTRY}/mirror/echo@sha256:" + "e" * 64
GOLDEN = Path(__file__).parent / "fixtures" / "kubernetes_admission"


@pytest.fixture
def grants() -> admission.Grants:
    return admission.probe_grants(REGISTRY, BLOCKED, ECHO, agentnet=("172.31.0.0/16",))


@pytest.fixture
def params(grants) -> dict:
    return grants.data()


@pytest.fixture
def own(grants) -> dict:
    return admission.probe_objects(NS, grants)


# -- own objects are admitted (§7.2: "K2's first test") -------------------------------------------


@pytest.mark.parametrize("kind", ["Deployment", "Service", "NetworkPolicy", "Secret"])
def test_agentbreeders_own_objects_are_admitted(own, params, kind):
    result = ac.create(NS, own[kind], params)
    assert result.allowed, result.messages


def _objects(manifest, allowed=None, mounts=()) -> dict:
    record = {"id": "c" * 32, "container": "agentbreeder-" + "c" * 32, "digest": "d" * 64}
    items = kubernetes._objects(record, manifest, NS, {"A": "1"}, list(mounts), BLOCKED, allowed)
    return {item["kind"]: item for item in items}


def _manifest(**overrides):
    values = {
        "name": admission.PROBE_AGENT,
        "image": f"{REGISTRY}/{admission.PROBE_AGENT}@sha256:" + "a" * 64,
        "port": 8080,
        "health_path": "/health",
        "runtime": None,
    }
    return SimpleNamespace(**{**values, **overrides})


def test_a_deployment_without_secrets_is_admitted(params):
    result = ac.create(NS, _objects(_manifest())["Deployment"], params)
    assert result.allowed, result.messages


def test_a_kit_deployments_policy_is_admitted_only_to_the_agents_network(params):
    allowed = kubernetes.kit_destinations(("172.31.0.0/16",), BLOCKED)
    policy = _objects(_manifest(), allowed=allowed)["NetworkPolicy"]
    assert ac.create(NS, policy, params).allowed
    wider = kubernetes.kit_destinations(("172.40.0.0/16",), BLOCKED)
    refused = ac.create(NS, _objects(_manifest(), allowed=wider)["NetworkPolicy"], params)
    assert refused.messages == ["an egress rule outside AgentBreeder's fixed shapes is refused"]


def test_a_kit_rule_must_except_every_blocked_range_inside_the_agents_network():
    blocked = (*BLOCKED, "172.31.0.5/32")
    params = admission.probe_grants(REGISTRY, blocked, ECHO, agentnet=("172.31.0.0/16",)).data()
    assert params["kit"] == "172.31.0.0/16|172.31.0.5/32"
    allowed = kubernetes.kit_destinations(("172.31.0.0/16",), blocked)
    assert ac.create(NS, _objects(_manifest(), allowed=allowed)["NetworkPolicy"], params).allowed
    for peer in ({"cidr": "172.31.0.0/16"}, {"cidr": "172.31.0.0/16", "except": [BLOCKED[0]]}):
        policy = _objects(_manifest(), allowed=[{"ipBlock": peer}])["NetworkPolicy"]
        assert not ac.create(NS, policy, params).allowed, peer


def test_a_two_uid_deployment_is_admitted_with_its_fixed_cli_container(params):
    runtime = SimpleNamespace(isolation="two-uid", profile="kit")
    deployment = _objects(_manifest(runtime=runtime))["Deployment"]
    assert ac.create(NS, deployment, params).allowed
    changed = copy.deepcopy(deployment)
    changed["spec"]["template"]["spec"]["containers"][1]["env"].append({"name": "X", "value": "1"})
    assert not ac.create(NS, changed, params).allowed
    other = copy.deepcopy(deployment)
    other["spec"]["template"]["spec"]["containers"][1]["image"] = ECHO
    assert not ac.create(NS, other, params).allowed


@pytest.mark.parametrize("role", admission.CANARY_ROLES)
def test_the_canary_pods_are_admitted(params, role):
    pod = admission.canary_pod(NS, role, ECHO, [("10.244.0.9", 8080), ("10.244.0.9", 8081)])
    result = ac.create(NS, pod, params, identity="canary")
    assert result.allowed, result.messages


def test_a_canary_policy_is_admitted(params):
    policy = admission.canary_policy(
        NS,
        2,
        "server",
        ingress=[{"from": [admission.canary_peer("client")], "ports": [{"port": 8080}]}],
        egress=[{"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": list(BLOCKED)}}]}],
    )
    result = ac.create(NS, policy, params, identity="canary")
    assert result.allowed, result.messages


def test_a_client_probe_outside_the_template_is_refused(params):
    for targets in ([("10.0.0.1", "x")], [("host.example", 80)], [("10.0.0.1", 80)] * 7):
        pod = admission.canary_pod(NS, "client", ECHO, targets)
        assert not ac.create(NS, pod, params, identity="canary").allowed
    server = admission.canary_pod(NS, "server", ECHO)
    server["spec"]["restartPolicy"] = "Never"
    assert not ac.create(NS, server, params, identity="canary").allowed


# -- paired probes (§7.4) -------------------------------------------------------------------------


def _create_probes(grants):
    return [probe for probe in admission.probes(NS, grants) if probe.operation == "CREATE"]


PAIRS = _create_probes(
    admission.probe_grants(REGISTRY, BLOCKED, ECHO, agentnet=("172.31.0.0/16",))
)


@pytest.mark.parametrize("probe", PAIRS, ids=lambda probe: probe.name)
def test_each_probe_pair_is_admitted_then_refused_by_its_policy(probe, params):
    admitted = ac.create(NS, probe.admitted, params, probe.identity)
    assert admitted.allowed, admitted.messages
    assert ac.POLICY_OF[probe.admitted["kind"]] == probe.policy
    if probe.refused is not None:
        refused = ac.create(NS, probe.refused, params, probe.identity)
        assert not refused.allowed and refused.matched
        assert refused.messages  # each names the field; the server prefixes the policy name


def test_probes_cover_every_refusal_section_7_4_names(grants):
    names = {probe.name for probe in admission.probes(NS, grants)}
    for required in (
        "other deployment's Secret",
        "operator-only Secret",
        "Helm release Secret",
        "serviceAccountName ab-installer",
        "envFrom",
        "env valueFrom",
        "initContainer",
        "image from mirror",
        "ungranted uses-postgres",
        "chart-service label",
        "LoadBalancer Service",
        "externalIPs",
        "egress to everything",
        "namespaceSelector peer",
        "service-account-token Secret",
        "delete chart Service",
        "delete ab-baseline",
        "delete with orphan",
        "delete with background",
        "canary free ipBlock",
    ):
        assert required in names


def test_probe_pairs_differ_from_their_admitted_request(grants):
    for probe in admission.probes(NS, grants):
        if probe.refused is not None:
            assert probe.refused != probe.admitted, probe.name


# -- deletes, updates, other identities -----------------------------------------------------------


def _delete(name, old, options, params, identity="deployer"):
    return ac.review(
        NS, name, "DELETE", old=old, options=options, identity=identity, params=params
    )


def test_a_deployment_is_deleted_only_in_the_foreground(own, params):
    old = ac.default(own["Deployment"])
    assert _delete("ab-deployments", old, {"propagationPolicy": "Foreground"}, params).allowed
    for options in (
        {"propagationPolicy": "Orphan"},
        {"propagationPolicy": "Background"},
        {},  # a body-less DELETE defaults to Background for apps/v1
        {"gracePeriodSeconds": 0},
        {"propagationPolicy": "Foreground", "orphanDependents": False},
    ):
        assert not _delete("ab-deployments", old, options, params).allowed, options


def test_only_agentbreeders_objects_may_be_deleted(own, params):
    chart = {
        "metadata": {"name": "litellm", "labels": {"io.agentbreeder/chart-service": "litellm"}}
    }
    baseline = {
        "metadata": {"name": "ab-baseline", "labels": {kubernetes.MANAGED_BY: "agentbreeder"}}
    }
    assert _delete("ab-services", ac.default(own["Service"]), {}, params).allowed
    assert not _delete("ab-services", chart, {}, params).allowed
    assert _delete("ab-networkpolicies", ac.default(own["NetworkPolicy"]), {}, params).allowed
    assert not _delete("ab-networkpolicies", baseline, {}, params).allowed
    canary = admission.canary_policy(NS, 1, "client")
    assert not _delete("ab-networkpolicies", canary, {}, params).allowed
    assert _delete("ab-networkpolicies", canary, {}, params, identity="canary").allowed
    assert not _delete(
        "ab-networkpolicies", ac.default(own["NetworkPolicy"]), {}, params, "canary"
    ).allowed
    pod = admission.canary_pod(NS, "client", ECHO)
    assert _delete("ab-pods", pod, {}, params, identity="canary").allowed


def test_the_deployer_never_deletes_or_changes_a_secret(own, params):
    secret = ac.default(own["Secret"])
    assert not _delete("ab-secrets", secret, {}, params).allowed
    update = ac.review(NS, "ab-secrets", "UPDATE", obj=secret, old=secret, params=params)
    assert not update.allowed


@pytest.mark.parametrize(
    "policy, kind",
    [
        ("ab-deployments", "Deployment"),
        ("ab-services", "Service"),
        ("ab-networkpolicies", "NetworkPolicy"),
    ],
)
def test_updates_are_refused(own, params, policy, kind):
    item = ac.default(own[kind])
    result = ac.review(NS, policy, "UPDATE", obj=item, old=item, params=params)
    assert "updates are refused; AgentBreeder replaces objects" in result.messages


def test_the_scale_subresource_is_matched_and_refused(own, params):
    (rules,) = [p for p in admission.policies(NS) if p["metadata"]["name"] == "ab-deployments"][:1]
    assert (
        "deployments/scale" in rules["spec"]["matchConstraints"]["resourceRules"][0]["resources"]
    )
    scale = {"kind": "Scale", "metadata": {"name": "x"}, "spec": {"replicas": 5}}
    assert not ac.review(
        NS, "ab-deployments", "UPDATE", obj=scale, old=scale, params=params
    ).allowed


def test_each_identity_writes_only_its_own_kinds(own, params):
    pod = admission.canary_pod(NS, "server", ECHO)
    assert not ac.create(NS, pod, params, identity="deployer").allowed
    for kind in ("Deployment", "Service", "Secret"):
        assert not ac.create(NS, own[kind], params, identity="canary").allowed, kind
    # The deployer's NetworkPolicy shapes are not the canary's, and the reverse.
    assert not ac.create(NS, own["NetworkPolicy"], params, identity="canary").allowed
    canary = admission.canary_policy(NS, 1, "client")
    assert not ac.create(NS, canary, params, identity="deployer").allowed


def test_other_users_are_not_matched(own, params):
    for user in (
        "system:admin",
        "system:serviceaccount:kube-system:generic-garbage-collector",
        f"system:serviceaccount:{NS}:ab-installer",
        "system:serviceaccount:other:ab-deployer",
    ):
        result = ac.review(
            NS, "ab-deployments", "DELETE", old=own["Deployment"], user=user, params=params
        )
        assert result.allowed and not result.matched


def test_missing_grants_deny_everything(own):
    assert not ac.create(NS, own["Deployment"], None).allowed


def test_every_other_namespaced_write_is_refused(params):
    spec = ac.policy(NS, "ab-deny-other")["spec"]
    rules = spec["matchConstraints"]
    assert rules["resourceRules"][0]["resources"] == ["*/*"]
    assert rules["resourceRules"][0]["scope"] == "Namespaced"  # never SelfSubjectAccessReviews
    unnamed = [rule for rule in rules["excludeResourceRules"] if "resourceNames" not in rule]
    excluded = {r for rule in unnamed for r in rule["resources"]}
    assert excluded == {
        "deployments", "deployments/scale", "services", "networkpolicies", "secrets", "pods",
        "pods/ephemeralcontainers",
    }  # fmt: skip
    # #1204: the one named exclusion is ab-grants, so the admin can write the parameters before
    # (and while) the policies exist; nothing else of any kind is excluded by name.
    named = [rule for rule in rules["excludeResourceRules"] if "resourceNames" in rule]
    assert named == [
        {
            "apiGroups": [""],
            "apiVersions": ["*"],
            "resources": ["configmaps"],
            "operations": ["CREATE", "UPDATE", "DELETE"],
            "scope": "Namespaced",
            "resourceNames": [admission.GRANTS],
        }
    ]
    other = admission.grants_configmap(NS, admission.probe_grants(REGISTRY, BLOCKED, ECHO))
    other["metadata"]["name"] = "other"
    result = ac.review(NS, "ab-deny-other", "UPDATE", obj=other, old=other, params=params)
    assert not result.allowed


def test_the_restricted_accounts_cannot_write_the_excluded_grants():
    """Excluding ab-grants from ab-deny-other gives the deployer and canary nothing: no RBAC
    rule of theirs writes a ConfigMap (the deployer only reads ab-grants by name)."""
    write = {"create", "update", "patch", "delete", "deletecollection", "*"}
    for name, rules in admission.role_rules(NS).items():
        if name not in ("deployer", "canary"):
            continue
        for rule in rules:
            if {"configmaps", "*"} & set(rule["resources"]):
                assert not write & set(rule["verbs"]), (name, rule)
    for name, rules in admission.cluster_rules(NS).items():
        for rule in rules:
            assert not {"configmaps", "*"} & set(rule["resources"]), (name, rule)


# -- grants ---------------------------------------------------------------------------------------


def test_an_ungranted_agent_is_refused_even_with_its_own_image(own, params):
    del params[f"{admission.PROBE_AGENT}.bindings"]
    assert (
        "the agent named by io.agentbreeder/name has no entry in ab-grants"
        in ac.create(NS, own["Deployment"], params).messages
    )


def test_a_grant_missing_one_list_is_refused(own, params):
    del params[f"{admission.PROBE_AGENT}.uses"]
    assert not ac.create(NS, own["NetworkPolicy"], params).allowed


def test_a_policy_must_except_every_blocked_range(own, params):
    policy = copy.deepcopy(own["NetworkPolicy"])
    policy["spec"]["egress"][1]["to"][0]["ipBlock"]["except"] = list(BLOCKED[:-1])
    assert not ac.create(NS, policy, params).allowed
    params["blocked"] = ""  # an empty list never admits the open rule
    assert not ac.create(NS, own["NetworkPolicy"], params).allowed


def test_the_node_local_dns_rule_needs_a_granted_address(own, params):
    policy = copy.deepcopy(own["NetworkPolicy"])
    rule = {"to": [{"ipBlock": {"cidr": "169.254.20.10/32"}}], "ports": kubernetes.DNS_PORTS}
    policy["spec"]["egress"].append(rule)
    assert not ac.create(NS, policy, params).allowed
    params["nodedns"] = "169.254.20.10"
    assert ac.create(NS, policy, params).allowed


def test_a_pull_secret_and_service_account_need_a_grant(own, params):
    deployment = copy.deepcopy(own["Deployment"])
    pod = deployment["spec"]["template"]["spec"]
    pod["imagePullSecrets"] = [{"name": "ab-pull"}]
    assert not ac.create(NS, deployment, params).allowed
    params["pull"] = "ab-pull"
    assert ac.create(NS, deployment, params).allowed
    pod["serviceAccountName"] = "store-reader"
    assert not ac.create(NS, deployment, params).allowed
    params[f"{admission.PROBE_AGENT}.sa"] = "store-reader"
    assert ac.create(NS, deployment, params).allowed


def test_a_granted_store_volume_is_admitted_and_another_is_not(own, params):
    deployment = copy.deepcopy(own["Deployment"])
    pod = deployment["spec"]["template"]["spec"]
    pod["volumes"].append(
        {
            "name": "store-vault",
            "csi": {
                "driver": admission.STORE_DRIVER,
                "readOnly": True,
                "volumeAttributes": {"secretProviderClass": "vault"},
            },
        }
    )
    pod["containers"][0]["volumeMounts"].append(
        {"name": "store-vault", "mountPath": admission.STORE_ROOT + "vault", "readOnly": True}
    )
    assert not ac.create(NS, deployment, params).allowed
    params[f"{admission.PROBE_AGENT}.stores"] = "vault"
    assert ac.create(NS, deployment, params).allowed
    pod["volumes"][-1]["csi"]["nodePublishSecretRef"] = {"name": "x"}
    assert not ac.create(NS, deployment, params).allowed


@pytest.mark.parametrize(
    "change, code",
    [
        ({"registry": "Not A Registry"}, "registry"),
        ({"blocked": ()}, "blocked is empty"),
        ({"blocked": ("10.0.0.1/8",)}, "CIDR"),
        ({"blocked": ("fd00::/8",)}, "canonical"),
        ({"blocked": ("not-a-cidr",)}, "not a CIDR"),
        ({"echo": "echo:latest"}, "digest"),
        ({"pull": "Bad Name"}, "pull"),
        ({"agents": {"Bad_Name": admission.AgentGrant()}}, "agent name"),
        ({"agents": {"a": admission.AgentGrant(uses=("postgres",))}}, "wrong shape"),
        ({"agents": {"a": admission.AgentGrant(bindings=("a,b",))}}, "wrong shape"),
    ],
)
def test_grants_refuse_values_the_policy_cannot_read(change, code):
    values = {"registry": REGISTRY, "blocked": BLOCKED, **change}
    with pytest.raises(admission.AdmissionError, match=code):
        admission.Grants(**values).data()


def test_every_granted_agent_has_all_four_keys():
    data = admission.Grants(
        registry=REGISTRY,
        blocked=BLOCKED,
        agents={"support": admission.AgentGrant(uses=("postgres:5432", "litellm:4000"))},
    ).data()
    assert data["support.uses"] == "postgres:5432,litellm:4000"
    assert {data[f"support.{k}"] for k in ("bindings", "stores", "sa")} == {""}


# -- RBAC (§7.1) ----------------------------------------------------------------------------------


def _rules(objects, kind, name):
    return next(o for o in objects if o["kind"] == kind and o["metadata"]["name"] == name)["rules"]


def test_the_deployer_cannot_read_secrets_or_create_pods():
    objects = admission.roles(NS)
    rules = _rules(objects, "Role", "ab-deployer")
    secrets = [rule for rule in rules if "secrets" in rule["resources"]]
    assert secrets == [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]}]
    pods = [rule for rule in rules if "pods" in rule["resources"]]
    assert all("create" not in rule["verbs"] for rule in pods)
    assert not any("replicasets" in r["resources"] and "create" in r["verbs"] for r in rules)
    assert not any(
        r["verbs"] == ["*"] or r["resources"] == ["*"] for o in objects for r in o.get("rules", [])
    )


def test_no_rule_has_an_empty_resource_name_list():
    for item in admission.roles(NS):
        for rule in item.get("rules", []):
            assert rule.get("resourceNames", ["x"]), rule
    assert not any(
        "secrets" in r["resources"] for r in _rules(admission.roles(NS), "Role", "ab-operator")
    )
    named = _rules(admission.roles(NS, ["ab-svc-litellm-master"]), "Role", "ab-operator")
    assert named[0] == {
        "apiGroups": [""],
        "resources": ["secrets"],
        "verbs": ["get"],
        "resourceNames": ["ab-svc-litellm-master"],
    }
    with pytest.raises(admission.AdmissionError):
        admission._rule([""], ["secrets"], ["get"], [])


def test_cluster_scoped_rbac_is_per_namespace():
    first = {
        o["metadata"]["name"] for o in admission.roles("a") if o["kind"].startswith("Cluster")
    }
    second = {
        o["metadata"]["name"] for o in admission.roles("b") if o["kind"].startswith("Cluster")
    }
    assert first and not first & second
    deployer = _rules(admission.roles(NS), "ClusterRole", f"agentbreeder:{NS}:ab-deployer")
    assert deployer[0]["resourceNames"] == ["kube-system", NS]
    assert set(deployer[1]["resourceNames"]) == set(admission.POLICY_NAMES)


def test_service_accounts_do_not_automount_tokens():
    accounts = [o for o in admission.roles(NS) if o["kind"] == "ServiceAccount"]
    assert {a["metadata"]["name"] for a in accounts} == {
        "ab-deployer",
        "ab-installer",
        "ab-operator",
        "ab-canary",
    }
    assert all(a["automountServiceAccountToken"] is False for a in accounts)


# -- the rendered policies, their digest and the print commands -----------------------------------


def test_every_policy_fails_closed():
    rendered = admission.policies(NS)
    policies = [o for o in rendered if o["kind"] == "ValidatingAdmissionPolicy"]
    bindings = [o for o in rendered if o["kind"] == "ValidatingAdmissionPolicyBinding"]
    assert [p["metadata"]["name"] for p in policies] == list(admission.POLICY_NAMES)
    for policy in policies:
        spec = policy["spec"]
        assert spec["failurePolicy"] == "Fail"
        assert spec["matchConstraints"]["matchPolicy"] == "Equivalent"
        assert spec["matchConstraints"]["namespaceSelector"] == {
            "matchLabels": {"kubernetes.io/metadata.name": NS}
        }
        assert all(r["scope"] == "Namespaced" for r in spec["matchConstraints"]["resourceRules"])
        assert all(
            set(r["operations"]) == {"CREATE", "UPDATE", "DELETE"}
            for r in spec["matchConstraints"]["resourceRules"]
        )
    for binding in bindings:
        if binding["metadata"]["name"] == "ab-grants-guard":
            continue  # no parameters (#1204), checked below
        assert binding["spec"]["paramRef"] == {
            "name": "ab-grants",
            "namespace": NS,
            "parameterNotFoundAction": "Deny",
        }
        assert binding["spec"]["validationActions"] == ["Deny"]


def test_the_grants_guard_refuses_every_write_to_ab_grants_and_needs_no_parameters(params):
    """#1204: ab-deny-other excludes ab-grants so the admin can write it first; the guard keeps
    the deployer and the canary from writing it, and has no parameters, so it applies while
    ab-grants is missing and can never lock the admin out."""
    rendered = {(o["kind"], o["metadata"]["name"]): o for o in admission.policies(NS)}
    policy = rendered[("ValidatingAdmissionPolicy", "ab-grants-guard")]["spec"]
    binding = rendered[("ValidatingAdmissionPolicyBinding", "ab-grants-guard")]["spec"]
    assert "paramKind" not in policy and "paramRef" not in binding
    assert binding["validationActions"] == ["Deny"]
    assert policy["matchConstraints"]["resourceRules"] == [
        {
            "apiGroups": [""],
            "apiVersions": ["*"],
            "resources": ["configmaps"],
            "operations": ["CREATE", "UPDATE", "DELETE"],
            "scope": "Namespaced",
            "resourceNames": [admission.GRANTS],
        }
    ]
    configmap = admission.grants_configmap(NS, admission.probe_grants(REGISTRY, BLOCKED, ECHO))
    for identity in ("deployer", "canary"):
        for operation in ("CREATE", "UPDATE", "DELETE"):
            result = ac.review(
                NS, "ab-grants-guard", operation, obj=configmap, old=configmap,
                identity=identity, params=None if operation == "CREATE" else params,
            )  # fmt: skip
            assert not result.allowed and result.matched
    admin = ac.review(
        NS, "ab-grants-guard", "UPDATE", obj=configmap, old=configmap, params=params,
        user="kubernetes-admin",
    )  # fmt: skip
    assert admin.allowed and not admin.matched


def _as_served(item: dict) -> dict:
    """What the API server returns for a stored policy: defaults, metadata and status added."""
    served = copy.deepcopy(item)
    served["metadata"].update(uid="u", resourceVersion="1", generation=1, managedFields=[{}])
    served["status"] = {"observedGeneration": 1, "typeChecking": {}}
    spec = served["spec"]
    for rule in (spec.get("matchConstraints") or {}).get("resourceRules") or []:
        rule.pop("scope", None) if rule.get("scope") == "*" else None
    if served["kind"] == "ValidatingAdmissionPolicyBinding":
        spec["matchResources"].pop("objectSelector")
    else:
        spec["matchConstraints"].pop("objectSelector")
    return served


def test_the_digest_is_stable_across_server_defaults_and_differs_per_namespace():
    rendered = admission.policies(NS)
    assert admission.digest(rendered) == admission.digest([_as_served(o) for o in rendered])
    assert admission.digest(rendered) != admission.digest(admission.policies("other"))
    changed = copy.deepcopy(rendered)
    changed[0]["spec"]["validations"][0]["expression"] = "true"
    assert admission.digest(changed) != admission.digest(rendered)


def test_invalid_namespaces_are_refused():
    for bad in ("", "Agents", "-x", "a" * 64):
        with pytest.raises(admission.AdmissionError):
            admission.policies(bad)


def _printed(capsys, *args) -> str:
    assert cli.main(["cluster", *args]) == 0
    return capsys.readouterr().out


@pytest.mark.parametrize("what", ["policy", "rbac"])
def test_print_matches_the_golden(capsys, what):
    text = _printed(capsys, what, "print", "--namespace", "agentbreeder")
    path = GOLDEN / f"{what}-agentbreeder.yaml"
    assert text == path.read_text(encoding="utf-8")
    documents = list(yaml.safe_load_all(text))
    expected = (
        admission.policies("agentbreeder") if what == "policy" else admission.roles("agentbreeder")
    )
    assert documents == json.loads(json.dumps(expected))
    if what == "policy":
        assert f"# Digest: {admission.digest(expected)}" in text


def test_print_refuses_an_invalid_namespace(capsys):
    assert cli.main(["cluster", "policy", "print", "--namespace", "Bad"]) == 2
    assert "namespace" in capsys.readouterr().err


def test_cel_literals():
    # Mixed elements are wrapped in dyn() (#1204); a single or uniform one is not.
    assert admission.cel({"a": [1, True, None, "it's"]}) == (
        "{'a': [dyn(1), dyn(true), dyn(null), dyn('it\\'s')]}"
    )
    assert admission.cel(["a", "b"]) == "['a', 'b']"
    assert admission.cel({"a": 1, "b": 2}) == "{'a': 1, 'b': 2}"
    assert admission.cel({"a": {"x": 1}, "b": {"x": "y"}}) == (
        "{'a': dyn({'x': 1}), 'b': dyn({'x': 'y'})}"
    )
    assert admission.cel([[], []]) == "[dyn([]), dyn([])]"
    assert admission.cel([[]]) == "[[]]"
    with pytest.raises(TypeError):
        admission.cel(1.5)
    with pytest.raises(TypeError):
        admission.cel({1: "a"})


# -- #1204: every expression is well typed under Kubernetes' CEL ---------------------------------

import cel_literals  # noqa: E402

EXPRESSIONS = [
    (where, expression)
    for item in admission.policies(NS)
    if item["kind"] == "ValidatingAdmissionPolicy"
    for where, expression in cel_literals.policy_expressions(item)
]


def test_every_policy_expression_is_checked():
    assert {where.split()[0] for where, _ in EXPRESSIONS} == set(admission.POLICY_NAMES)
    assert len(EXPRESSIONS) > 100


@pytest.mark.parametrize(("where", "expression"), EXPRESSIONS, ids=[w for w, _ in EXPRESSIONS])
def test_no_policy_expression_holds_a_heterogeneous_literal(where, expression):
    """Kubernetes refuses a list or map literal whose elements differ in type; cel-python does
    not, so this checks the generated source itself (strictly; see `cel_literals`)."""
    assert cel_literals.heterogeneous_literals(expression) == [], where


@pytest.mark.parametrize(
    "expression",
    [
        "x == {'runAsNonRoot': true, 'runAsUser': 65532}",  # bool, int: ab-deployments [11]
        "x == [{'port': 53}, {'protocol': 'UDP'}]",  # map(string,int) vs map(string,string)
        "x == {'a': 'agentbreeder', 'b': object.metadata.labels['c']}",  # string vs dyn: ab-pods
        "x == [[], []]",
        "x == {'a': dyn(1), 'b': 2}",
        "f(['a', 1])",
        "x.all(k, k in ['a', 1])",
    ],
)
def test_the_literal_check_refuses_what_kubernetes_refuses(expression):
    assert cel_literals.heterogeneous_literals(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "x == {'a': dyn(true), 'b': dyn(1)}",
        "['a', 'b'] + [1]",
        "[[1], [2]]",
        "{'a': [1], 'b': [2]}",
        "x in [y]",
        "[]",
    ],
)
def test_the_literal_check_accepts_homogeneous_literals(expression):
    assert cel_literals.heterogeneous_literals(expression) == []


@pytest.mark.parametrize(
    "value",
    [
        admission.POD_SECURITY_CONTEXT,
        admission.CONTAINER_SECURITY_CONTEXT,
        admission.RESOURCES,
        admission.CANARY_POD_DEFAULTS,
        {"to": [admission.DNS_PEER], "ports": admission.DNS_PORTS},
    ],
)
def test_a_dyn_literal_is_as_strict_as_before(value):
    """dyn() changes only the static type: equality still refuses an extra key, a missing key
    or a different value, at every depth."""
    literal = admission.cel(value)
    assert cel_literals.heterogeneous_literals(literal) == []

    def equal(other) -> bool:
        return bool(ac.evaluate(f"x == {literal}", {"x": celpy.json_to_cel(other)}))

    def changed(item):
        """A different value of the same type, so the comparison is a real inequality."""
        if isinstance(item, bool):
            return not item
        if isinstance(item, int):
            return item + 1
        if isinstance(item, str):
            return item + "-different"
        if isinstance(item, list):
            return [*item, copy.deepcopy(item[0])] if item else ["extra"]
        return {**item, "extra": "x"}

    assert equal(value)
    variants = [{**value, "extra": "x"}]
    for key, item in value.items():
        variants.append({k: v for k, v in value.items() if k != key})
        variants.append({**value, key: changed(item)})
        if isinstance(item, dict):  # one level down: a nested key changed
            for inner, nested in item.items():
                variants.append({**value, key: {**item, inner: changed(nested)}})
    for variant in variants:
        assert not equal(variant), variant


def test_main_s_refused_validations_are_flagged_by_the_check():
    """The expressions the API server refused on 1.37 (#1204, 0-indexed: ab-deployments 7, 11,
    15, 16, 21, 22; ab-networkpolicies 8; ab-pods 2, 3, 4) are flagged when rendered the old way,
    i.e. without dyn(); so the check finds what the server found."""

    def old_cel(value):
        if isinstance(value, list):
            return "[" + ", ".join(old_cel(item) for item in value) + "]"
        if isinstance(value, dict):
            return "{" + ", ".join(f"{old_cel(k)}: {old_cel(v)}" for k, v in value.items()) + "}"
        return admission.cel(value)

    assert cel_literals.heterogeneous_literals(f"x == {old_cel(admission.POD_SECURITY_CONTEXT)}")
    assert cel_literals.heterogeneous_literals(
        f"x == {old_cel({'to': [admission.DNS_PEER], 'ports': admission.DNS_PORTS})}"
    )


# -- #1204: each refused probe reaches its policy (core validation accepts it) --------------------


def _probe(grants, name):
    return next(probe for probe in admission.probes(NS, grants) if probe.name == name)


def test_the_init_container_probe_is_valid_for_core_validation(grants, params):
    refused = _probe(grants, "initContainer").refused
    spec = refused["spec"]["template"]["spec"]
    (init,) = spec["initContainers"]
    names = [c["name"] for c in spec["containers"]] + [init["name"]]
    assert len(names) == len(set(names))  # no Duplicate value
    assert not {"readinessProbe", "livenessProbe", "startupProbe", "lifecycle"} & set(init)
    result = ac.create(NS, refused, params)
    assert "a pod field outside AgentBreeder's template is refused" in result.messages


def test_the_service_account_token_probe_is_valid_for_core_validation(grants, params):
    refused = _probe(grants, "service-account-token Secret").refused
    assert refused["type"] == "kubernetes.io/service-account-token"
    assert refused["metadata"]["annotations"]["kubernetes.io/service-account.name"]
    result = ac.create(NS, refused, params)
    assert "only an Opaque Secret of data is accepted" in result.messages


def test_canary_roles_and_policy_names_are_checked():
    with pytest.raises(admission.AdmissionError):
        admission.canary_pod(NS, "other", ECHO)
