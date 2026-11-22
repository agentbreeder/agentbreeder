"""`cluster up --restricted` on kind (#770 K2), with kubectl, kind and Docker faked."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml
from test_cluster import FAKE_STACK_ID, FakeEngine, Tools, fake_engine, tools  # noqa: F401

from agentbreeder import cluster
from agentbreeder import kubernetes_admission as admission
from agentbreeder.kubernetes_providers import KIND, Binding, KindProvider, Session
from agentbreeder.services.schema import ServiceError

GRANTS = admission.Grants(registry="localhost:5001/agentbreeder", blocked=("10.244.0.0/16",))
TOKEN = "eyJhbGciOiJSUzI1NiJ9.payload-of-the-token.signature_part"
ADMIN = {
    "apiVersion": "v1",
    "kind": "Config",
    "clusters": [
        {
            "name": "kind-agentbreeder",
            "cluster": {"server": "https://127.0.0.1:6443", "certificate-authority-data": "Q0E="},
        }
    ],
    "users": [
        {
            "name": "kind-agentbreeder",
            "user": {"client-certificate-data": "Y2VydA==", "client-key-data": "a2V5"},
        }
    ],
    "contexts": [
        {
            "name": cluster.CONTEXT,
            "context": {"cluster": "kind-agentbreeder", "user": "kind-agentbreeder"},
        }
    ],
    "current-context": cluster.CONTEXT,
}


class RestrictedTools(Tools):
    def __init__(self):
        super().__init__()
        self.server = {"major": "1", "minor": "37"}
        self.secrets: list[dict] = []
        self.deployments = ""
        self.applied: list[dict] = []
        # The API server's admission objects: name -> object, and a name whose apply fails.
        self.policies: dict[str, dict] = {}
        self.bindings: dict[str, dict] = {}
        self.refuse_policy: str | None = None
        self.refuse_binding: str | None = None
        self.refuse_delete = False
        self.deleted: list[str] = []
        self.configmaps: set[str] = set()  # ConfigMaps in the namespace

    def _admission(self, argv, stdin):
        """Answer a call on a ValidatingAdmissionPolicy or its binding, or return None."""
        if argv[:1] == ["kubectl"] and argv[5:7] == ["get", "configmap"]:
            name = argv[argv.index("configmap") + 1]
            return f"configmap/{name}" if name in self.configmaps else ""
        if argv[-3:] == ["validatingadmissionpolicybindings", "-o", "name"]:
            return "\n".join(
                f"validatingadmissionpolicybinding.admissionregistration.k8s.io/{name}"
                for name in self.bindings
            )
        if "get" in argv and "validatingadmissionpolicy" in argv:
            name = argv[argv.index("validatingadmissionpolicy") + 1]
            if name not in self.policies:
                raise ServiceError("CLUSTER_FAILED", "kubectl get failed (exit 1): NotFound")
            return json.dumps(self.policies[name])
        if "delete" in argv and "validatingadmissionpolicybinding" in argv:
            name = argv[argv.index("validatingadmissionpolicybinding") + 1]
            if self.refuse_delete:
                raise ServiceError("CLUSTER_FAILED", "kubectl delete failed (exit 1)")
            self.deleted.append(name)
            self.bindings.pop(name, None)
            return ""
        if " apply " in f" {' '.join(argv)} " and stdin:
            item = json.loads(stdin)
            name = (item.get("metadata") or {}).get("name")
            if item["kind"] == "ValidatingAdmissionPolicy":
                self.applied.append(item)
                if name == self.refuse_policy:  # as the API server refuses ill-typed CEL
                    raise ServiceError("CLUSTER_FAILED", "kubectl apply failed (exit 1): Invalid")
                self.policies[name] = item
                return ""
            if item["kind"] == "ConfigMap":
                self.configmaps.add(name)
                return None  # recorded in `applied` by __call__
            if item["kind"] == "ValidatingAdmissionPolicyBinding":
                self.applied.append(item)
                if name == self.refuse_binding:
                    raise ServiceError("CLUSTER_FAILED", "kubectl apply failed (exit 1)")
                self.bindings[name] = item
                return ""
        return None

    def __call__(self, argv, *, stdin=None, timeout=120, what):
        joined = " ".join(argv)
        answer = self._admission(argv, stdin)
        if answer is not None:
            self.calls.append(argv)
            return answer
        if "get kubeconfig" in joined:
            self.calls.append(argv)
            return yaml.safe_dump(ADMIN)
        if "version -o json" in joined:
            self.calls.append(argv)
            return json.dumps({"serverVersion": self.server})
        if " create token " in f" {joined} ":
            self.calls.append(argv)
            return TOKEN
        if "get secrets" in joined:
            self.calls.append(argv)
            assert argv[argv.index("-o") + 1].startswith("jsonpath=")  # values never read
            return "\n".join(
                f"{m['name']} {' '.join(o['kind'] for o in m.get('ownerReferences', []))}"
                for m in (item["metadata"] for item in self.secrets)
            )
        if "get deployments" in joined and "jsonpath" in joined:
            self.calls.append(argv)
            return self.deployments
        if " apply " in f" {joined} " and stdin:
            self.applied.append(json.loads(stdin))
        return super().__call__(argv, stdin=stdin, timeout=timeout, what=what)


@pytest.fixture
def restricted(monkeypatch):
    fake = RestrictedTools()
    monkeypatch.setattr(cluster, "_run", fake)
    monkeypatch.setattr(cluster.shutil, "which", lambda name: f"/bin/{name}")
    return fake


def _kinds(document: dict) -> set[str]:
    items = document.get("items") or [document]
    return {item["kind"] for item in items}


def test_install_restricted_writes_a_private_deployer_kubeconfig(restricted, tmp_path):
    admin = tmp_path / "kubeconfig"
    cluster.write_kubeconfig(admin)
    path = cluster.install_restricted(admin, tmp_path / cluster.DEPLOYER_KUBECONFIG, grants=GRANTS)
    assert path.stat().st_mode & 0o077 == 0
    config = yaml.safe_load(path.read_text())
    assert config["users"] == [{"name": "ab-deployer", "user": {"token": TOKEN}}]
    assert config["clusters"] == ADMIN["clusters"]  # the same server and CA, not the admin user
    assert "client-key-data" not in path.read_text()
    # The deployer's kubeconfig passes the kind provider's checks (static, loopback, private).
    binding = Binding(
        KIND, "u", cluster.NAMESPACE, {"context": cluster.CONTEXT, "kubeconfig": str(path)}
    )
    KindProvider(binding).check(Session((), {}))
    # The token never reaches argv; the accounts, Roles and policies were applied as admin.
    assert all(TOKEN not in part for argv in restricted.calls for part in argv)
    kinds = set().union(*(_kinds(document) for document in restricted.applied))
    assert {"ServiceAccount", "Role", "ClusterRole", "ValidatingAdmissionPolicy"} <= kinds
    token = next(argv for argv in restricted.calls if "token" in argv)
    assert token[-6:] == [
        "token",
        "ab-deployer",
        "--namespace",
        cluster.NAMESPACE,
        "--duration",
        "720h",
    ]


def test_install_restricted_refuses_an_old_cluster(restricted, tmp_path):
    restricted.server = {"major": "1", "minor": "29+"}
    admin = tmp_path / "kubeconfig"
    cluster.write_kubeconfig(admin)
    with pytest.raises(ServiceError, match="CLUSTER_VERSION_OLD"):
        cluster.install_restricted(admin, tmp_path / "deployer", grants=GRANTS)
    assert restricted.applied == []


def test_install_restricted_refuses_a_missing_token(restricted, monkeypatch, tmp_path):
    admin = tmp_path / "kubeconfig"
    cluster.write_kubeconfig(admin)
    original = RestrictedTools.__call__

    def no_token(self, argv, **kwargs):
        if "token" in argv:
            return ""
        return original(self, argv, **kwargs)

    monkeypatch.setattr(RestrictedTools, "__call__", no_token)
    with pytest.raises(ServiceError, match="no service account token"):
        cluster.install_restricted(admin, tmp_path / "deployer", grants=GRANTS)
    assert not (tmp_path / "deployer").exists()


# -- #1204: the policies install fail closed ------------------------------------------------------


def _admin(tmp_path):
    admin = tmp_path / "kubeconfig"
    cluster.write_kubeconfig(admin)
    return admin


POLICY, BINDING = "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding"


def _order(restricted) -> list[tuple[str, str]]:
    return [
        (item["kind"], (item.get("metadata") or {}).get("name", ""))
        for item in restricted.applied
        if item["kind"] in (POLICY, BINDING, "List")
    ]


def test_each_policy_is_applied_and_read_back_before_its_binding(restricted, tmp_path):
    cluster.install_restricted(
        _admin(tmp_path), tmp_path / "deployer", grants=GRANTS, sleep=lambda _: None
    )
    order = _order(restricted)
    expected = []
    for name in admission.POLICY_NAMES:
        expected += [(POLICY, name), (BINDING, name)]
    # The policies and bindings come first, one object per apply; then the Roles.
    assert order == [*expected, ("List", "")]
    assert set(restricted.policies) == set(restricted.bindings) == set(admission.POLICY_NAMES)
    reads = [
        argv[argv.index("get") + 2]
        for argv in restricted.calls
        if "get" in argv and "json" in argv and "validatingadmissionpolicy" in argv
    ]
    assert reads == list(admission.POLICY_NAMES)


@pytest.mark.parametrize("failing", ["ab-deployments", "ab-networkpolicies", "ab-pods"])
def test_a_refused_policy_creates_no_further_binding_and_deletes_this_runs(
    restricted, tmp_path, failing
):
    restricted.refuse_policy = failing
    with pytest.raises(ServiceError, match="installing the admission policies") as error:
        cluster.install_restricted(
            _admin(tmp_path), tmp_path / "deployer", grants=GRANTS, sleep=lambda _: None
        )
    assert error.value.code == "CLUSTER_FAILED"
    before = admission.POLICY_NAMES[: admission.POLICY_NAMES.index(failing)]
    # No binding was applied after the refusal, and every binding this run made is gone.
    applied = [n for k, n in _order(restricted) if k == "ValidatingAdmissionPolicyBinding"]
    assert applied == list(before)
    assert restricted.bindings == {}
    assert restricted.deleted == list(reversed(before))
    # Nothing that grants the restricted accounts a right was applied, and no kubeconfig.
    assert ("List", "") not in _order(restricted)
    assert not (tmp_path / "deployer").exists()


def test_a_refused_binding_is_deleted_too(restricted, tmp_path):
    restricted.refuse_binding = "ab-services"
    with pytest.raises(ServiceError):
        cluster.install_restricted(
            _admin(tmp_path), tmp_path / "deployer", grants=GRANTS, sleep=lambda _: None
        )
    assert restricted.deleted == ["ab-services", "ab-deployments"]
    assert restricted.bindings == {}


def test_bindings_from_an_earlier_run_are_kept_on_failure(restricted, tmp_path):
    # An earlier install's binding of a policy that still exists keeps enforcing it.
    restricted.policies["ab-deployments"] = {"metadata": {"name": "ab-deployments"}}
    restricted.bindings["ab-deployments"] = {"metadata": {"name": "ab-deployments"}}
    restricted.refuse_policy = "ab-secrets"
    with pytest.raises(ServiceError):
        cluster.install_restricted(
            _admin(tmp_path), tmp_path / "deployer", grants=GRANTS, sleep=lambda _: None
        )
    assert "ab-deployments" in restricted.bindings
    assert restricted.deleted == ["ab-networkpolicies", "ab-services"]


def test_a_binding_that_cannot_be_deleted_is_named(restricted, tmp_path):
    restricted.refuse_policy = "ab-services"
    restricted.refuse_delete = True
    with pytest.raises(ServiceError, match="could not delete the binding.s. ab-deployments"):
        cluster.install_restricted(
            _admin(tmp_path), tmp_path / "deployer", grants=GRANTS, sleep=lambda _: None
        )


def _drop_validations(spec):
    spec["validations"] = spec["validations"][:1]


def _drop_variables(spec):
    spec.pop("variables")


def _drop_condition(spec):
    spec.pop("matchConditions")


def _open_failure(spec):
    spec["failurePolicy"] = "Ignore"


def _other_resources(spec):
    spec["matchConstraints"]["resourceRules"][0]["resources"] = ["configmaps"]


def _no_grants_exclusion(spec):
    rules = spec["matchConstraints"].get("excludeResourceRules") or []
    spec["matchConstraints"]["excludeResourceRules"] = [
        rule for rule in rules if "resourceNames" not in rule
    ]


@pytest.mark.parametrize(
    "change",
    [
        _drop_validations,
        _drop_variables,
        _drop_condition,
        _open_failure,
        _other_resources,
        _no_grants_exclusion,
    ],
)
def test_a_policy_that_does_not_read_back_fails_closed(restricted, monkeypatch, tmp_path, change):
    monkeypatch.setattr(cluster, "POLICY_WAIT", 0)
    original = RestrictedTools._admission

    def stale(self, argv, stdin):
        answer = original(self, argv, stdin)
        target = "ab-deny-other" if change is _no_grants_exclusion else "ab-services"
        if "get" in argv and target in argv:  # another version is still served
            item = json.loads(answer)
            change(item["spec"])
            return json.dumps(item)
        return answer

    monkeypatch.setattr(RestrictedTools, "_admission", stale)
    target = "ab-deny-other" if change is _no_grants_exclusion else "ab-services"
    with pytest.raises(ServiceError, match=f"{target} did not read back: it differs"):
        cluster.install_restricted(
            _admin(tmp_path), tmp_path / "deployer", grants=GRANTS, sleep=lambda _: None
        )
    assert target not in restricted.bindings and restricted.bindings == {}


def test_server_defaults_on_a_read_back_policy_are_accepted(restricted, monkeypatch, tmp_path):
    original = RestrictedTools._admission

    def served(self, argv, stdin):
        answer = original(self, argv, stdin)
        if "get" in argv and "json" in argv and "validatingadmissionpolicy" in argv:
            item = json.loads(answer)
            item["metadata"].update(uid="u", generation=1, managedFields=[{"manager": "kubectl"}])
            for rule in item["spec"]["matchConstraints"]["resourceRules"]:
                rule["scope"] = rule.get("scope", "*")
            item["status"] = {"observedGeneration": 1, "typeChecking": {}}
            return json.dumps(item)
        return answer

    monkeypatch.setattr(RestrictedTools, "_admission", served)
    cluster.install_restricted(
        _admin(tmp_path), tmp_path / "deployer", grants=GRANTS, sleep=lambda _: None
    )
    assert set(restricted.bindings) == set(admission.POLICY_NAMES)


def test_a_failed_read_back_names_the_last_error(restricted, monkeypatch, tmp_path):
    monkeypatch.setattr(cluster, "POLICY_WAIT", 0)
    original = RestrictedTools._admission

    def forbidden(self, argv, stdin):
        if "get" in argv and "json" in argv and "validatingadmissionpolicy" in argv:
            raise ServiceError("CLUSTER_FAILED", "kubectl get failed (exit 1): Forbidden")
        return original(self, argv, stdin)

    monkeypatch.setattr(RestrictedTools, "_admission", forbidden)
    with pytest.raises(ServiceError, match="did not read back: .*Forbidden"):
        cluster.install_restricted(
            _admin(tmp_path), tmp_path / "deployer", grants=GRANTS, sleep=lambda _: None
        )
    assert restricted.bindings == {}


def test_a_read_back_that_is_not_json_fails_closed(restricted, monkeypatch, tmp_path):
    monkeypatch.setattr(cluster, "POLICY_WAIT", 0)
    original = RestrictedTools._admission

    def garbled(self, argv, stdin):
        if "get" in argv and "json" in argv and "validatingadmissionpolicy" in argv:
            return "not json"
        return original(self, argv, stdin)

    monkeypatch.setattr(RestrictedTools, "_admission", garbled)
    with pytest.raises(ServiceError, match="kubectl returned no JSON"):
        cluster.install_restricted(
            _admin(tmp_path), tmp_path / "deployer", grants=GRANTS, sleep=lambda _: None
        )


def test_a_policy_read_back_waits_and_retries(restricted, monkeypatch, tmp_path):
    misses = {"ab-deployments": 2}
    original = RestrictedTools._admission

    def late(self, argv, stdin):
        if "get" in argv and "ab-deployments" in argv and misses["ab-deployments"]:
            misses["ab-deployments"] -= 1
            raise ServiceError("CLUSTER_FAILED", "NotFound")
        return original(self, argv, stdin)

    monkeypatch.setattr(RestrictedTools, "_admission", late)
    slept = []
    cluster.install_restricted(
        _admin(tmp_path), tmp_path / "deployer", grants=GRANTS, sleep=slept.append
    )
    assert slept == [1, 1]
    assert set(restricted.bindings) == set(admission.POLICY_NAMES)


def test_the_rendered_policies_must_be_pairs(restricted, monkeypatch, tmp_path):
    monkeypatch.setattr(admission, "policies", lambda namespace: [{"kind": "List"}])
    with pytest.raises(ServiceError, match="not policy/binding pairs"):
        cluster.install_restricted(_admin(tmp_path), tmp_path / "deployer", grants=GRANTS)
    # Only ab-grants was written: no policy, binding or Role.
    assert [item["kind"] for item in restricted.applied] == ["ConfigMap"]


def test_the_policies_are_never_installed_without_ab_grants(restricted, tmp_path):
    """#1204, measured on kind 1.37: a binding whose ab-grants is missing denies every write in
    the namespace, the admin's too, so the policies need their parameters first."""
    with pytest.raises(ServiceError, match="ab-grants must exist"):
        cluster.install_restricted(_admin(tmp_path), tmp_path / "deployer")
    assert restricted.applied == [] and restricted.bindings == {}


def test_ab_grants_written_earlier_is_enough(restricted, tmp_path):
    restricted.configmaps.add(admission.GRANTS)
    cluster.install_restricted(_admin(tmp_path), tmp_path / "deployer", sleep=lambda _: None)
    assert set(restricted.bindings) == set(admission.POLICY_NAMES)


def test_server_version_must_be_readable(restricted, tmp_path):
    restricted.server = {"major": "one"}
    with pytest.raises(ServiceError, match="version"):
        cluster.server_version(tmp_path / "kubeconfig")


def test_sweep_deletes_only_unowned_secrets_of_removed_deployments(restricted, tmp_path):
    gone, live, owned = ("agentbreeder-" + c * 32 for c in "abc")
    restricted.secrets = [
        {"metadata": {"name": gone}},
        {"metadata": {"name": live}},
        {"metadata": {"name": owned, "ownerReferences": [{"kind": "Deployment"}]}},
        {"metadata": {"name": "ab-svc-postgres"}},
    ]
    restricted.deployments = live
    assert cluster.sweep_legacy_secrets(tmp_path / "kubeconfig") == [gone]
    deletes = [argv for argv in restricted.calls if "delete" in argv]
    assert len(deletes) == 1 and gone in deletes[0]


def _state(tmp_path, agents=("support", "triage")) -> FakeEngine:
    engine = FakeEngine(tmp_path)
    operator = {"environments": {"dev": {"agents": {name: {} for name in agents}}}}
    (tmp_path / "operator.yaml").write_text(yaml.safe_dump(operator))
    block = {
        "registry": "localhost:5001/agentbreeder",
        "blocked_cidrs": ["10.244.0.0/16", "172.30.0.0/16"],
        "agents_cidrs": ["172.31.0.0/16"],
    }
    engine.state.put("kubernetes", json.dumps(block))
    return engine


def test_write_grants_is_a_no_op_unless_restricted(restricted, tmp_path):
    state = _state(tmp_path).state
    cluster.write_grants(state)
    assert restricted.applied == []


def test_write_grants_names_every_agent_with_an_empty_grant(restricted, tmp_path):
    state = _state(tmp_path).state
    state.put(cluster.RESTRICTED, "1")
    cluster.write_grants(state)
    (configmap,) = restricted.applied
    assert configmap["metadata"]["name"] == admission.GRANTS
    data = configmap["data"]
    assert data["registry"] == "localhost:5001/agentbreeder"
    assert data["blocked"] == "10.244.0.0/16,172.30.0.0/16"
    assert data["kit"] == "172.31.0.0/16|"
    for name in ("support", "triage"):
        assert {data[f"{name}.{key}"] for key in ("bindings", "uses", "stores", "sa")} == {""}


def test_write_grants_refuses_an_agent_name_the_policy_cannot_hold(restricted, tmp_path):
    state = _state(tmp_path, agents=("Bad_Name",)).state
    state.put(cluster.RESTRICTED, "1")
    with pytest.raises(ServiceError, match="KUBERNETES_GRANTS_INVALID"):
        cluster.write_grants(state)


def test_up_restricted_records_the_deployer_kubeconfig(restricted, fake_engine, tmp_path):  # noqa: F811
    restricted.networks["agentbreeder-x-agents"] = {"IPAddress": "172.31.0.6"}
    cluster.up(tmp_path, "dev", restricted=True)
    instance = fake_engine.instances[0]
    block = json.loads(instance.state.values["kubernetes"])
    assert block["kubeconfig"] == str(tmp_path / cluster.DEPLOYER_KUBECONFIG)
    assert instance.state.values[cluster.RESTRICTED] == "1"
    # #1204: ab-grants is written before the policies (no agent is granted until `services up`
    # writes the operator config and rewrites it), then the policies, then the Roles.
    kinds = [item.get("kind") for item in restricted.applied]
    assert (
        kinds.index("ConfigMap") < kinds.index("ValidatingAdmissionPolicy") < kinds.index("List")
    )
    configmap = next(item for item in restricted.applied if item.get("kind") == "ConfigMap")
    assert configmap["metadata"]["name"] == admission.GRANTS


@pytest.mark.parametrize("before, restricted_now", [("", True), ("1", False)])
def test_switching_accounts_is_refused_while_deployments_are_active(
    restricted,
    fake_engine,  # noqa: F811
    tmp_path,
    before,
    restricted_now,
):
    class Busy(FakeEngine):
        def __init__(self, project, log=None):
            super().__init__(project, log)
            self.active = ["dev/support"]
            self.state.values[cluster.RESTRICTED] = before

    from agentbreeder.services import engine as engine_module

    engine_module.Engine = Busy
    restricted.networks["agentbreeder-x-agents"] = {"IPAddress": "172.31.0.6"}
    with pytest.raises(ServiceError, match="CLUSTER_IN_USE"):
        cluster.up(tmp_path, "dev", restricted=restricted_now)
    assert restricted.applied == []
    # The same mode again is fine: nothing a deployment recorded changes.
    cluster.up(tmp_path, "dev", restricted=before == "1")


def test_up_unrestricted_keeps_the_admin_kubeconfig(restricted, fake_engine, tmp_path):  # noqa: F811
    restricted.networks["agentbreeder-x-agents"] = {"IPAddress": "172.31.0.6"}
    cluster.up(tmp_path, "dev")
    instance = fake_engine.instances[0]
    assert json.loads(instance.state.values["kubernetes"])["kubeconfig"] == str(
        tmp_path / "kubeconfig"
    )
    assert instance.state.values[cluster.RESTRICTED] == ""
    assert {item["kind"] for item in restricted.applied} == {"Namespace"}


def test_down_removes_the_deployer_kubeconfig(restricted, fake_engine, tmp_path):  # noqa: F811
    (tmp_path / cluster.DEPLOYER_KUBECONFIG).write_text("x")
    restricted.clusters = cluster.NAME
    cluster.down(tmp_path)
    assert not (tmp_path / cluster.DEPLOYER_KUBECONFIG).exists()
    assert fake_engine.instances[0].state.values[cluster.RESTRICTED] == ""


def test_block_for_sweeps_legacy_secrets(restricted, tmp_path):
    restricted.secrets = [{"metadata": {"name": "agentbreeder-" + "d" * 32}}]
    cluster.block_for(SimpleNamespace(directory=tmp_path), "stack-agents")
    assert any("delete" in argv and "secret" in argv for argv in restricted.calls)
