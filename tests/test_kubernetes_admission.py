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
    excluded = {r for rule in rules["excludeResourceRules"] for r in rule["resources"]}
    assert excluded == {
        "deployments", "deployments/scale", "services", "networkpolicies", "secrets", "pods",
        "pods/ephemeralcontainers",
    }  # fmt: skip
    configmap = admission.grants_configmap(NS, admission.probe_grants(REGISTRY, BLOCKED, ECHO))
    result = ac.review(NS, "ab-deny-other", "UPDATE", obj=configmap, old=configmap, params=params)
    assert not result.allowed


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
        assert binding["spec"]["paramRef"] == {
            "name": "ab-grants",
            "namespace": NS,
            "parameterNotFoundAction": "Deny",
        }
        assert binding["spec"]["validationActions"] == ["Deny"]


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
    assert admission.cel({"a": [1, True, None, "it's"]}) == "{'a': [1, true, null, 'it\\'s']}"
    with pytest.raises(TypeError):
        admission.cel(1.5)


def test_canary_roles_and_policy_names_are_checked():
    with pytest.raises(admission.AdmissionError):
        admission.canary_pod(NS, "other", ECHO)
