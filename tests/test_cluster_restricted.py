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

    def __call__(self, argv, *, stdin=None, timeout=120, what):
        joined = " ".join(argv)
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
    path = cluster.install_restricted(admin, tmp_path / cluster.DEPLOYER_KUBECONFIG)
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
        cluster.install_restricted(admin, tmp_path / "deployer")
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
        cluster.install_restricted(admin, tmp_path / "deployer")
    assert not (tmp_path / "deployer").exists()


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
    # ab-grants waits for `services up`, which writes the operator config with this block.
    assert not any(item.get("kind") == "ConfigMap" for item in restricted.applied)


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
