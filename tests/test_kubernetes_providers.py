"""The cluster provider interface and the tagged binding (#770 §4.1, K0 = #908 G0).

`kind` is the only shipped provider and records its binding inline; a provider recorded by
reference (`<provider>|<binding id>`, a `cluster_bindings` row) is exercised here through an
injected fake, against the fake kubectl of `test_kubernetes`.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import v2_manifest_data, write_private
from test_kubernetes import UID, FakeCluster, kubeconfig_data, manifest

from agentbreeder import cli, kubernetes_providers
from agentbreeder import kubernetes as kubernetes_module
from agentbreeder.commands import forward as forward_command
from agentbreeder.commands import logs as logs_command
from agentbreeder.commands import watch as watch_command
from agentbreeder.deployment import DeploymentService
from agentbreeder.kubernetes import Kubernetes
from agentbreeder.kubernetes_providers import (
    KIND,
    Binding,
    KindProvider,
    Registry,
    Session,
    Snapshot,
)
from agentbreeder.operator_config import KubernetesTarget
from agentbreeder.store import DeploymentError, StateNeedsMigration, Store

LEGACY = "kind-x|{uid}|agents|{path}"


@pytest.fixture
def cluster(monkeypatch):
    fake = FakeCluster()
    monkeypatch.setattr(kubernetes_module.subprocess, "run", fake.run)
    monkeypatch.setattr(kubernetes_module.time, "sleep", lambda _: None)
    return fake


@pytest.fixture
def config(identity_env, tmp_path):
    path = write_private(tmp_path / "kubeconfig", kubeconfig_data())
    identity_env.config["environments"]["dev"] = {
        "targets": ["local", "kubernetes"],
        "kubernetes": {
            "kubeconfig": str(path),
            "context": "kind-x",
            "cluster_uid": UID,
            "namespace": "agents",
            "registry": "localhost:5001/agentbreeder",
            "blocked_cidrs": ["10.244.0.0/16"],
        },
    }
    return identity_env.load()


# -- a provider recorded by reference ------------------------------------------------------------


class FakeProvider:
    """A provider whose binding lives in `cluster_bindings`: its pins are a server and a CA."""

    name = "fake"
    inline = False
    built: list[FakeProvider] = []

    def __init__(self, binding: Binding):
        self.binding = binding
        self.checked: list[Session] = []
        self.opened = self.closed = 0
        FakeProvider.built.append(self)

    @classmethod
    def document(cls, target) -> dict:
        return {"context": target.context, "server": "https://192.0.2.10:6443", "ca": "a" * 64}

    @contextmanager
    def session(self):
        self.opened += 1
        try:
            yield Session(
                ("--context", self.binding.document["context"], "--server", "pinned"),
                {"PATH": os.environ.get("PATH", "")},
            )
        finally:
            self.closed += 1

    def check(self, session: Session) -> None:
        self.checked.append(session)

    def registry(self) -> Registry:
        return Registry(scheme="https", loopback_only=False, authenticated=True)

    def blocked_cidrs(self) -> list[str]:
        return ["10.0.0.0/8"]


class Kube(Kubernetes):
    """The real binding, session and authorize paths; workloads kept in memory."""

    def __init__(self, *arguments, running=False, **options):
        super().__init__(*arguments, **options)
        self.running = running  # a fresh process (logs, watch): every workload exists
        self.workloads: dict[str, dict] = {}
        self.authorized: list[str | None] = []
        self.forwarded: list[str] = []

    def authorize(self, context=None, environment=None):
        self.authorized.append(context)
        super().authorize(context, environment)

    def inspect(self, record):
        if self.running:
            return self._workload(record)
        return self.workloads.get(record["id"])

    def create(self, record, manifest, **options):
        self.workloads[record["id"]] = self._workload(record)

    @staticmethod
    def _workload(record):
        return {
            "Id": "uid-" + record["id"],
            "State": {"Running": True},
            "deployment": record["id"],
            "name": record["container"],
        }

    def start(self, info):
        return None

    def endpoint(self, info, manifest):
        return f"kubernetes://{self.namespace}/{info['name']}:8080"

    def healthy(self, endpoint, path, timeout=30):
        return True

    def remove(self, info):
        self.workloads.pop(info["deployment"], None)

    def sweep(self, record):
        self.workloads.pop(record["id"], None)

    @contextmanager
    def forward(self, endpoint, local_port=0):
        self.forwarded.append(endpoint)
        yield 4321

    def pod(self, info):
        return {"name": "pod-1", "reason": None, "readable": True}

    def logs(self, info, tail=200, since=None, previous=False, pod=None):
        return "2026-11-20T10:00:00Z hello\n", False


@pytest.fixture
def fake(monkeypatch):
    FakeProvider.built = []
    monkeypatch.setitem(kubernetes_providers.PROVIDERS, "fake", FakeProvider)
    return FakeProvider


def _use_provider(config, name="fake", **changes):
    """The operator config's `dev` cluster with another provider (the literal allows only
    `kind`, so the fake is set past validation, as a later slice's provider would be)."""
    environment = config.model.environments["dev"]
    target = environment.kubernetes.model_copy(update={"provider": name, **changes})
    object.__setattr__(environment, "kubernetes", target)
    return config


@pytest.fixture
def fake_config(config, fake):
    return _use_provider(config)


@pytest.fixture
def store(tmp_path):
    state = Store(tmp_path / "state")
    yield state
    state.close()


def _service(store, config, **options):
    docker = Mock(context="docker-context")
    kube = Kube(config, **options)
    return DeploymentService(store, docker, config, kubernetes=kube), kube


def _binding_row(store, identifier):
    return dict(
        store.db.execute("SELECT * FROM cluster_bindings WHERE id=?", (identifier,)).fetchone()
    )


def _created_event(store, deployment_id):
    row = store.db.execute(
        "SELECT context FROM audit WHERE action='deployment_created' AND deployment_id=?",
        (deployment_id,),
    ).fetchone()
    return json.loads(row[0])


def _tamper(store, identifier, *, recompute: bool):
    """Edit a binding row's server pin, as a DB owner could; optionally with a fresh digest."""
    row = _binding_row(store, identifier)
    document = {**json.loads(row["document"]), "server": "https://203.0.113.66:6443"}
    digest = row["digest"]
    if recompute:
        digest = kubernetes_providers.digest(
            row["provider"], row["environment"], row["cluster_uid"], row["namespace"], document
        )
    store.db.execute(
        "UPDATE cluster_bindings SET document=?, digest=? WHERE id=?",
        (kubernetes_providers.canonical(document), digest, identifier),
    )


# -- the store migration (v4 -> v5) ---------------------------------------------------------------


def _v4_state(tmp_path, rows: list[tuple[str, str, str]]):
    """A v4 state directory holding (id, target, context) deployments."""
    directory = tmp_path / "v4"
    store = Store(directory)
    try:
        for identifier, target, context in rows:
            data = v2_manifest_data(name=f"a{identifier[:4]}")
            data["target"] = target
            store.create(identifier, data, "d" * 64, context)
        store.db.execute("DROP TABLE cluster_bindings")
        store.db.execute(
            "UPDATE deployments SET context=? WHERE id=?", (rows[0][2], rows[0][0])
        )  # create() never rewrites; the v4 value is what was written
        store.db.execute("PRAGMA user_version=4")
    finally:
        store.close()
    return directory


def _contexts(directory) -> dict[str, str]:
    store = Store.open_readonly(directory)
    try:
        return {
            r["id"]: r["context"] for r in store.db.execute("SELECT id, context FROM deployments")
        }
    finally:
        store.close()


def test_v4_kubernetes_records_are_rewritten_as_kind(tmp_path):
    legacy = LEGACY.format(uid=UID, path="/k/config")
    directory = _v4_state(
        tmp_path,
        [
            ("1" * 32, "kubernetes", legacy),
            ("2" * 32, "local", "desktop-linux"),
            ("3" * 32, "kubernetes", "torn|record"),  # not a 4-field record: left as it is
        ],
    )
    with pytest.raises(StateNeedsMigration, match="v4"):
        Store.open_readonly(directory)
    Store(directory).close()  # the writer migrates
    assert _contexts(directory) == {
        "1" * 32: "kind|" + legacy,
        "2" * 32: "desktop-linux",
        "3" * 32: "torn|record",
    }
    store = Store(directory)
    try:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert store.db.execute("SELECT count(*) FROM cluster_bindings").fetchone()[0] == 0
    finally:
        store.close()


def test_the_migration_is_idempotent(tmp_path):
    legacy = LEGACY.format(uid=UID, path="/k/config")
    directory = _v4_state(tmp_path, [("1" * 32, "kubernetes", legacy)])
    Store(directory).close()
    Store(directory).close()  # v5: nothing to do
    store = Store(directory)
    try:  # an interrupted upgrade that left the table and the tag: run again, no double tag
        store.db.execute("PRAGMA user_version=4")
    finally:
        store.close()
    Store(directory).close()
    assert _contexts(directory) == {"1" * 32: "kind|" + legacy}


def test_a_migrated_record_binds_as_kind_and_matches_the_selection(
    tmp_path,
    config,
    cluster,
):
    selected = Kubernetes(config)
    selected.select("dev")
    legacy = selected.context.removeprefix("kind|")
    directory = _v4_state(tmp_path, [("1" * 32, "kubernetes", legacy)])
    Store(directory).close()
    # The deploy-time check `record["context"] != runtime.context` holds after the migration.
    assert _contexts(directory)["1" * 32] == selected.context
    fresh = Kubernetes(None)
    fresh.authorize(selected.context)
    assert (fresh.kube_context, fresh.cluster_uid, fresh.namespace) == ("kind-x", UID, "agents")
    assert fresh.binding.provider == KIND


def test_a_legacy_context_named_kind_is_tagged_too(tmp_path):
    legacy = f"kind|{UID}|agents|/k/config"  # a kube context literally named `kind`
    directory = _v4_state(tmp_path, [("1" * 32, "kubernetes", legacy)])
    Store(directory).close()
    assert _contexts(directory) == {"1" * 32: "kind|" + legacy}
    assert kubernetes_providers.parse_inline("kind|" + legacy).document["context"] == "kind"


def test_a_malformed_manifest_does_not_block_the_migration(tmp_path):
    legacy = LEGACY.format(uid=UID, path="/k/config")
    directory = _v4_state(tmp_path, [("1" * 32, "kubernetes", legacy), ("2" * 32, "local", "c")])
    db = sqlite3.connect(directory / "deployments.sqlite3")
    db.execute("UPDATE deployments SET manifest='{not json' WHERE id=?", ("2" * 32,))
    db.commit()
    db.close()
    Store(directory).close()
    assert _contexts(directory) == {"1" * 32: "kind|" + legacy, "2" * 32: "c"}


def test_registry_readers_accept_v5_state(tmp_path):
    from agentbreeder import registry_deployed, store

    assert store.SCHEMA_VERSION in registry_deployed._STATE_SCHEMAS


def test_an_unknown_newer_schema_is_still_refused(tmp_path):
    directory = tmp_path / "state"
    Store(directory).close()
    db = sqlite3.connect(directory / "deployments.sqlite3")
    db.execute("PRAGMA user_version=6")
    db.commit()
    db.close()
    with pytest.raises(DeploymentError, match="Unsupported state schema"):
        Store(directory)


# -- the kind binding -----------------------------------------------------------------------------


def test_kind_records_its_binding_inline_and_tagged(config, cluster):
    kube = Kubernetes(config)
    kube.select("dev")
    assert kube.context == f"kind|kind-x|{UID}|agents|{kube.kubeconfig}"
    assert kube.binding.audit() is None
    assert kubernetes_providers.parse_inline(kube.context) == kubernetes_providers.parse_inline(
        kube.context.removeprefix("kind|")
    )
    kube.authorize()
    assert cluster.calls[0][:5] == [
        "kubectl",
        "--kubeconfig",
        kube.kubeconfig,
        "--context",
        "kind-x",
    ]


def test_a_kind_deploy_leaves_the_audit_event_unchanged(store, config, cluster):
    service, kube = _service(store, config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    record = store.get(deployed["id"])
    assert record["context"].startswith("kind|kind-x|")
    assert "cluster_binding" not in _created_event(store, deployed["id"])
    assert store.db.execute("SELECT count(*) FROM cluster_bindings").fetchone()[0] == 0
    assert service.remove(deployed["id"], local=False)["state"] == "removed"
    assert kube.authorized[-1] == record["context"]


@pytest.mark.parametrize(
    "context",
    ["broken", "kind|a|b|c|d|e", "a|b|c", "fake|not-hex", "nobody|" + "a" * 32, "|" * 4],
)
def test_unreadable_records_are_refused(context, fake):
    with pytest.raises(DeploymentError, match="binding"):
        Kubernetes(None).authorize(context)


def test_the_kind_provider_contract(monkeypatch, tmp_path):
    binding = Binding(KIND, UID, "agents", {"context": "kind-x", "kubeconfig": "/k/config"})
    provider = KindProvider(binding)
    monkeypatch.setenv("KUBECONFIG", "/elsewhere")
    with provider.session() as session:
        assert session.argv == ("--kubeconfig", "/k/config", "--context", "kind-x")
        assert "KUBECONFIG" not in session.env
    assert provider.registry() == Registry(scheme="http", loopback_only=True, authenticated=False)
    monkeypatch.setattr("agentbreeder.cluster.blocked_cidrs", lambda network: [f"{network}/x"])
    assert provider.blocked_cidrs(None, agents_network="agents") == ["agents/x"]
    target = KubernetesTarget.model_validate(
        {
            "kubeconfig": "/k/config",
            "context": "kind-x",
            "cluster_uid": UID,
            "namespace": "agents",
            "registry": "localhost:5001/agentbreeder",
            "blocked_cidrs": ["10.244.0.0/16"],
        }
    )
    assert target.provider == KIND  # omitted means kind
    assert KindProvider.document(target) == {"context": "kind-x", "kubeconfig": "/k/config"}


def test_the_operator_config_accepts_only_kind():
    base = {
        "kubeconfig": "/k/config",
        "context": "kind-x",
        "cluster_uid": UID,
        "namespace": "agents",
        "registry": "localhost:5001/agentbreeder",
        "blocked_cidrs": ["10.244.0.0/16"],
    }
    assert KubernetesTarget.model_validate({**base, "provider": "kind"}).provider == KIND
    with pytest.raises(ValueError):
        KubernetesTarget.model_validate({**base, "provider": "gke"})


# -- a provider recorded by reference: the interface contract ------------------------------------


def test_select_records_a_binding_row_and_the_audit_carries_its_digest(
    store, fake_config, cluster
):
    service, kube = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    record = store.get(deployed["id"])
    provider, _, identifier = record["context"].partition("|")
    assert provider == "fake" and len(identifier) == 32
    row = _binding_row(store, identifier)
    assert (row["environment"], row["cluster_uid"], row["namespace"]) == ("dev", UID, "agents")
    assert json.loads(row["document"])["server"] == "https://192.0.2.10:6443"
    assert _created_event(store, deployed["id"])["cluster_binding"] == {
        "id": identifier,
        "digest": row["digest"],
    }
    # kubectl ran with the provider's session, after the provider's check.
    assert cluster.calls and all(
        argv[:5] == ["kubectl", "--context", "kind-x", "--server", "pinned"]
        for argv in cluster.calls
    )
    assert FakeProvider.built[-1].checked


def test_a_second_deploy_reuses_the_row(store, fake_config, cluster):
    service, _ = _service(store, fake_config)
    first = service.deploy(manifest(), local=False, environment="dev")
    again = service.deploy(manifest(), local=False, environment="dev")
    assert first["id"] == again["id"]
    assert store.db.execute("SELECT count(*) FROM cluster_bindings").fetchone()[0] == 1


def test_changed_pins_under_a_live_deployment_are_refused(
    store, fake_config, cluster, monkeypatch
):
    service, _ = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    monkeypatch.setattr(
        FakeProvider,
        "document",
        classmethod(lambda cls, target: {"context": target.context, "server": "x", "ca": "b"}),
    )
    with pytest.raises(DeploymentError, match="KUBERNETES_BINDING_CHANGED"):
        service.deploy(manifest(), local=False, environment="dev")
    service.remove(deployed["id"], local=False)
    # Nothing live on it now: the changed pins replace the row, under a new ID.
    replaced = service.deploy(manifest(), local=False, environment="dev")
    old = store.get(deployed["id"])["context"].partition("|")[2]
    new = store.get(replaced["id"])["context"].partition("|")[2]
    assert old != new and store.cluster_binding(old) is None
    assert json.loads(_binding_row(store, new)["document"])["server"] == "x"


def test_remove_rebinds_from_the_record_not_the_live_config(store, fake_config, cluster):
    service, kube = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    recorded = store.get(deployed["id"])["context"]
    _use_provider(fake_config, context="elsewhere")  # the operator config moved on
    cluster.calls.clear()
    assert service.remove(deployed["id"], local=False)["state"] == "removed"
    assert kube.authorized[-1] == recorded
    assert cluster.calls and all(argv[2] == "kind-x" for argv in cluster.calls)


@pytest.mark.parametrize("recompute", [True, False], ids=["digest-recomputed", "digest-stale"])
def test_an_edited_binding_row_is_refused_before_kubectl(store, fake_config, cluster, recompute):
    service, kube = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    identifier = store.get(deployed["id"])["context"].partition("|")[2]
    _tamper(store, identifier, recompute=recompute)
    cluster.calls.clear()
    with pytest.raises(DeploymentError, match="KUBERNETES_BINDING_CHANGED"):
        service.remove(deployed["id"], local=False)
    assert cluster.calls == []  # no session was handed the edited pins
    assert store.get(deployed["id"])["state"] == "ready"
    assert deployed["id"] in kube.workloads


@pytest.mark.parametrize("how", ["row-deleted", "no-audit", "provider-swapped", "bad-document"])
def test_a_binding_without_matching_evidence_is_refused(store, fake_config, cluster, how):
    service, _ = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    identifier = store.get(deployed["id"])["context"].partition("|")[2]
    if how == "row-deleted":
        store.db.execute("DELETE FROM cluster_bindings WHERE id=?", (identifier,))
    elif how == "no-audit":
        bindings = Mock()
        bindings.cluster_binding.return_value = store.cluster_binding(identifier)
        bindings.cluster_binding_audit.return_value = []
        with pytest.raises(DeploymentError, match="KUBERNETES_BINDING_CHANGED"):
            Kubernetes(None, bindings=bindings).authorize(f"fake|{identifier}")
        return
    elif how == "provider-swapped":
        store.db.execute("UPDATE cluster_bindings SET provider='kind' WHERE id=?", (identifier,))
    else:
        store.db.execute("UPDATE cluster_bindings SET document='[1]' WHERE id=?", (identifier,))
    with pytest.raises(DeploymentError, match="KUBERNETES_BINDING_CHANGED"):
        service.remove(deployed["id"], local=False)


def test_a_by_reference_binding_needs_the_store(fake):
    with pytest.raises(DeploymentError, match="binding"):
        Kubernetes(None).authorize("fake|" + "a" * 32)


def test_the_kill_switch_and_reconcile_rebind_through_the_record(store, fake_config, cluster):
    service, kube = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    identifier = store.get(deployed["id"])["context"].partition("|")[2]
    original = _binding_row(store, identifier)
    _tamper(store, identifier, recompute=True)
    actor = Mock(label="admin", verified=False)
    service.disable("sample", actor)
    failed = store.get(deployed["id"])
    assert failed["state"] == "failed" and "KUBERNETES_BINDING_CHANGED" in failed["error"]
    assert deployed["id"] in kube.workloads  # nothing was stopped on the edited binding
    store.db.execute(
        "UPDATE cluster_bindings SET document=?, digest=? WHERE id=?",
        (original["document"], original["digest"], identifier),
    )
    service.reconcile(actor)
    assert store.get(deployed["id"])["state"] == "disabled"
    assert deployed["id"] not in kube.workloads
    assert kube.authorized[-1] == f"fake|{identifier}"


def test_forward_rebinds_through_the_record(store, fake_config, cluster, monkeypatch, capsys):
    service, kube = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(forward_command.time, "sleep", interrupt)
    args = SimpleNamespace(id=deployed["id"], port=0)
    assert forward_command._forward(service, args) == 0
    assert kube.authorized[-1] == store.get(deployed["id"])["context"]
    assert "127.0.0.1:4321" in capsys.readouterr().out


def test_forward_refuses_an_edited_binding(store, fake_config, cluster):
    service, kube = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    _tamper(store, store.get(deployed["id"])["context"].partition("|")[2], recompute=True)
    with pytest.raises(DeploymentError, match="KUBERNETES_BINDING_CHANGED"):
        forward_command._forward(service, SimpleNamespace(id=deployed["id"], port=0))
    assert kube.forwarded == []


def _logs_kube(monkeypatch, config):
    holder = {}

    def build():
        holder["kube"] = Kube(config, running=True)
        return holder["kube"]

    monkeypatch.setattr(logs_command, "_kubernetes", build)
    monkeypatch.setattr(logs_command, "_runtime", lambda: pytest.fail("Docker was asked"))
    monkeypatch.setattr(logs_command, "_stdin_descriptor", lambda: None)
    return holder


def test_logs_rebind_through_a_snapshot_of_the_record(
    store, fake_config, cluster, monkeypatch, capsys
):
    service, _ = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    holder = _logs_kube(monkeypatch, fake_config)
    code = cli.main(["logs", deployed["id"], "--state-dir", str(store.directory)])
    assert code == 0, capsys.readouterr().err
    kube = holder["kube"]
    assert kube.authorized == [store.get(deployed["id"])["context"]]
    assert isinstance(kube.bindings, Snapshot)
    assert "hello" in capsys.readouterr().out


def test_logs_refuse_an_edited_binding(store, fake_config, cluster, monkeypatch, capsys):
    service, _ = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    _tamper(store, store.get(deployed["id"])["context"].partition("|")[2], recompute=True)
    _logs_kube(monkeypatch, fake_config)
    code = cli.main(["logs", deployed["id"], "--state-dir", str(store.directory)])
    assert code != 0
    err = capsys.readouterr().err
    assert "CLUSTER_UNAVAILABLE" in err and "KUBERNETES_BINDING_CHANGED" in err


def test_watch_reads_a_pod_through_the_recorded_binding(store, fake_config, cluster, monkeypatch):
    service, _ = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    built = []
    monkeypatch.setattr(
        logs_command,
        "_kubernetes",
        lambda: built.append(Kube(fake_config, running=True)) or built[-1],
    )
    row = {**store.get(deployed["id"]), "environment": "dev"}
    state = SimpleNamespace(deploy_state=lambda environment: store.directory)
    assert watch_command._pod(row, state) == {"running": True, "reason": None}
    assert isinstance(built[-1].bindings, Snapshot)
    _tamper(store, row["context"].partition("|")[2], recompute=True)
    assert watch_command._pod(row, state) is None  # unknown, never read through edited pins


def test_watch_needs_no_store_for_a_kind_binding(config, cluster, monkeypatch):
    kube = Kube(config)
    kube.select("dev")
    monkeypatch.setattr(logs_command, "_kubernetes", lambda: Kube(config, running=True))
    row = {"id": "c" * 32, "context": kube.context, "environment": "dev", "container": "x"}
    state = SimpleNamespace(deploy_state=lambda environment: pytest.fail("store opened"))
    assert watch_command._pod(row, state) == {"running": True, "reason": None}


def test_the_session_is_entered_once_and_closed(store, fake_config, cluster):
    service, kube = _service(store, fake_config)
    service.deploy(manifest(), local=False, environment="dev")
    kube.select("dev")
    kube.authorize()
    provider = FakeProvider.built[-1]
    kube.authorize(kube.context, "dev")  # the same binding: same provider, same session
    kube.command("get", "pods")
    assert FakeProvider.built[-1] is provider and (provider.opened, provider.closed) == (1, 0)
    service.close()
    assert provider.closed == 1
    with kube:
        kube.authorize(kube.context, "dev")
        assert provider.opened == 2
    assert provider.closed == 2


def test_a_handler_closes_the_session_when_it_fails(store, fake_config, cluster, monkeypatch):
    service, kube = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    service.close()
    built = []

    class Service(DeploymentService):
        def __init__(self, store, runtime, operator, **options):
            super().__init__(store, runtime, operator, kubernetes=Kube(fake_config))
            built.append(self)

    monkeypatch.setattr(cli, "DeploymentService", Service)
    monkeypatch.setattr(cli, "load_operator_config", lambda: fake_config)

    def action(service, operator):
        service.kubernetes.authorize(store.get(deployed["id"])["context"])
        raise DeploymentError("boom")

    args = SimpleNamespace(state_dir=store.directory)
    assert cli.run_stateful(args, action) == 2
    provider = FakeProvider.built[-1]
    assert provider.opened == provider.closed == 1


def test_logs_close_the_session(store, fake_config, cluster, monkeypatch, capsys):
    service, _ = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    holder = _logs_kube(monkeypatch, fake_config)
    assert cli.main(["logs", deployed["id"], "--state-dir", str(store.directory)]) == 0
    provider = holder["kube"].provider
    assert provider.opened == provider.closed == 1


def test_an_unselected_runtime_never_runs_kubectl_on_the_default_config(cluster):
    with pytest.raises(DeploymentError, match="KUBERNETES_UNCONFIGURED"):
        Kubernetes(None).command("get", "pods")
    assert cluster.calls == []


@pytest.mark.parametrize(
    "context",
    [
        f"kind|kind-x|{UID}|--raw=/x|/k",
        f"kind|-ctx|{UID}|agents|/k",
        "kind|kind-x|not-a-uid|agents|/k",
        f"kind|kind-x|{UID}|agents|relative",
    ],
)
def test_an_inline_record_must_hold_config_shaped_values(context, cluster):
    with pytest.raises(DeploymentError, match="binding"):
        Kubernetes(None).authorize(context)
    assert cluster.calls == []


def test_a_legacy_and_a_tagged_record_are_the_same_binding():
    legacy = LEGACY.format(uid=UID, path="/k/config")
    assert Kubernetes.same_binding(legacy, "kind|" + legacy)
    assert not Kubernetes.same_binding(legacy, "kind|" + legacy.replace("agents", "other"))
    assert Kubernetes.same_binding("fake|" + "a" * 32, "fake|" + "a" * 32)
    assert not Kubernetes.same_binding("broken", "kind|" + legacy)


def test_an_unreadable_binding_store_is_a_coded_refusal(fake):
    bindings = Mock()
    bindings.cluster_binding.side_effect = sqlite3.OperationalError("malformed JSON")
    with pytest.raises(DeploymentError, match="binding"):
        Kubernetes(None, bindings=bindings).authorize("fake|" + "a" * 32)


def test_an_edited_row_is_not_reused_by_a_deploy(store, fake_config, cluster):
    service, _ = _service(store, fake_config)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    identifier = store.get(deployed["id"])["context"].partition("|")[2]
    _tamper(store, identifier, recompute=False)  # the document only: the digest column is stale
    with pytest.raises(DeploymentError, match="KUBERNETES_BINDING_CHANGED"):
        service.deploy(manifest(), local=False, environment="dev")


def test_injected_providers_reach_the_service_runtime(store, config, fake):
    service = DeploymentService(store, Mock(), config, providers={"fake": FakeProvider})
    assert service.kubernetes.providers == {"fake": FakeProvider}
    assert service.kubernetes.bindings is store


def test_a_provider_audit_entry_is_only_for_a_real_binding():
    assert kubernetes_providers.audit_entry(Mock()) is None
    assert kubernetes_providers.audit_entry(SimpleNamespace(binding=None)) is None


def test_a_snapshot_of_an_inline_record_reads_nothing():
    store = Mock()
    snapshot = Snapshot.read(store, LEGACY.format(uid=UID, path="/k"))
    assert snapshot.cluster_binding("a" * 32) is None and snapshot.cluster_binding_audit("x") == []
    store.cluster_binding.assert_not_called()


def test_an_unrecorded_binding_has_no_record():
    binding = Binding("fake", UID, "agents", {"context": "c"}, environment="dev", digest="d")
    with pytest.raises(DeploymentError, match="not recorded"):
        binding.record()
    with pytest.raises(DeploymentError, match="No state"):
        kubernetes_providers.record(binding, None)


def test_an_unknown_provider_in_the_config_is_refused(config, cluster):
    _use_provider(config, name="nobody")
    with pytest.raises(DeploymentError, match="Unknown cluster provider"):
        Kubernetes(config).select("dev")


def test_kubeconfig_fixture_is_loopback():
    # Guards the fixture this file shares with test_kubernetes.
    assert kubeconfig_data()["clusters"][0]["cluster"]["server"].startswith("https://127.0.0.1")


def test_logs_refuse_a_crafted_inline_record_with_a_code(
    store, config, cluster, monkeypatch, capsys
):
    data = v2_manifest_data(name="hello", port=8080)
    data["target"] = "kubernetes"
    store.create("e" * 32, data, "d" * 64, f"kind|-ctx|{UID}|agents|/k")
    store.transition("e" * 32, "ready", endpoint="kubernetes://agents/x:8080")
    _logs_kube(monkeypatch, config)
    assert cli.main(["logs", "e" * 32, "--state-dir", str(store.directory)]) != 0
    assert "CLUSTER_UNAVAILABLE" in capsys.readouterr().err


def test_a_selected_and_a_recorded_kind_binding_share_one_provider(config, cluster):
    kube = Kubernetes(config)
    kube.select("dev")
    provider = kube.provider
    kube.authorize(kube.context)
    assert kube.provider is provider
