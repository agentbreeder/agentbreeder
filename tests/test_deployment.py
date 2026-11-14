import json
from unittest.mock import Mock

import pytest

from agentbreeder.deployment import DeploymentService
from agentbreeder.manifest import Manifest
from agentbreeder.store import DeploymentError, Store


@pytest.fixture
def manifest(v2_manifest):
    return v2_manifest


@pytest.fixture
def store(tmp_path):
    state = Store(tmp_path / "state")
    yield state
    state.close()


@pytest.fixture
def runtime():
    runtime = Mock(context="local-test")
    runtime.inspect.return_value = {"Id": "container-id", "State": {"Running": True}}
    runtime.endpoint.return_value = "http://127.0.0.1:1234"
    runtime.healthy.return_value = True
    return runtime


def test_deploy_persists_and_retry_reuses(store, runtime, manifest):
    runtime.inspect.side_effect = [
        None,
        {"Id": "id", "State": {"Running": False}},
        {"Id": "id", "State": {"Running": True}},
    ]
    service = DeploymentService(store, runtime)
    result = service.deploy(manifest, local=True)
    assert result["state"] == "ready"
    runtime.create.assert_called_once()
    runtime.start.assert_called_once()
    runtime.inspect.side_effect = None
    reopened = Store(store.directory)
    try:
        result2 = DeploymentService(reopened, runtime).deploy(manifest, local=True)
        assert result2["id"] == result["id"]
        assert result2["resource"] == "container-id"
        assert len(reopened.get(result["id"])["events"]) > 3
        runtime.create.assert_called_once()
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "local,target,secrets",
    [(False, "local", {}), (True, "kubernetes", {}), (True, "local", {"KEY": "secret://key"})],
)
def test_authorization_before_runtime(store, runtime, local, target, secrets):
    manifest = Manifest(
        schema_version=1,
        name="sample",
        image="repo@sha256:" + "a" * 64,
        target=target,
        secrets=secrets,
    )
    with pytest.raises(DeploymentError):
        DeploymentService(store, runtime).deploy(manifest, local=local)
    assert runtime.mock_calls == []
    assert store.active("sample") is None


def test_conflicting_spec_does_not_touch_runtime(store, runtime, manifest):
    service = DeploymentService(store, runtime)
    service.deploy(manifest, local=True)
    runtime.reset_mock()
    with pytest.raises(DeploymentError, match="different spec"):
        service.deploy(manifest.model_copy(update={"port": 8000}), local=True)
    assert runtime.mock_calls == []


@pytest.mark.parametrize("operation", ["create", "start", "inspect", "endpoint", "healthy"])
def test_runtime_failures_are_durable(store, runtime, manifest, operation):
    runtime.inspect.return_value = {"Id": "id", "State": {"Running": False}}
    if operation == "create":
        runtime.inspect.return_value = None
    getattr(runtime, operation).side_effect = DeploymentError("safe error")
    result = DeploymentService(store, runtime).deploy(manifest, local=True)
    assert result["state"] == "failed"
    assert result["endpoint"] is None
    assert store.get(result["id"])["events"][-1]["error"] == "safe error"


def test_health_failure_never_ready(store, runtime, manifest):
    runtime.healthy.return_value = False
    result = DeploymentService(store, runtime).deploy(manifest, local=True)
    assert result["state"] == "failed"
    assert result["endpoint"] is None


def test_crash_after_creation_reconciles_same_name(store, runtime, manifest):
    runtime.inspect.side_effect = KeyboardInterrupt()
    service = DeploymentService(store, runtime)
    with pytest.raises(KeyboardInterrupt):
        service.deploy(manifest, local=True)
    old = store.active(manifest.name)
    assert old["state"] == "starting"
    runtime.inspect.side_effect = None
    result = service.deploy(manifest, local=True)
    assert result["id"] == old["id"]
    runtime.create.assert_not_called()


def test_remove_idempotent_and_redeploy_new_id(store, runtime, manifest):
    service = DeploymentService(store, runtime)
    first = service.deploy(manifest, local=True)
    runtime.inspect.side_effect = [runtime.inspect.return_value, None]
    assert service.remove(first["id"], local=True)["state"] == "removed"
    runtime.reset_mock()
    assert service.remove(first["id"], local=True)["state"] == "removed"
    assert runtime.mock_calls == []
    runtime.inspect.side_effect = None
    assert service.deploy(manifest, local=True)["id"] != first["id"]


def test_cleanup_failure_is_persisted(store, runtime, manifest):
    service = DeploymentService(store, runtime)
    result = service.deploy(manifest, local=True)
    runtime.remove.side_effect = DeploymentError("cleanup failed")
    result = service.remove(result["id"], local=True)
    assert result["state"] == "failed"
    assert result["endpoint"] is None
    assert "cleanup failed" in result["error"]


def test_lock_prevents_other_writer(store):
    other = Store(store.directory)
    try:
        with store.lock(), pytest.raises(DeploymentError, match="running"), other.lock():
            pytest.fail("Concurrent lock acquired")
        with other.lock():
            pass
    finally:
        other.close()


def test_unknown_id_and_private_permissions(store, tmp_path):
    with pytest.raises(DeploymentError, match="not found"):
        store.get("unknown")
    assert store.directory.stat().st_mode & 0o777 == 0o700
    assert (store.directory / "deployments.sqlite3").stat().st_mode & 0o777 == 0o600
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(DeploymentError, match="0700"):
        Store(public)


def test_local_image_contract(manifest):
    with pytest.raises(ValueError):
        Manifest(**{**manifest.model_dump(), "target": "kubernetes"})
    assert json.loads(manifest.model_dump_json())["image"].startswith("sha256:")


def test_future_schema_not_downgraded(store):
    store.db.execute("PRAGMA user_version=99")
    with pytest.raises(DeploymentError, match="schema"):
        Store(store.directory)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 99


def test_existing_public_database_rejected(store):
    path = store.directory / "deployments.sqlite3"
    path.chmod(0o644)
    try:
        with pytest.raises(DeploymentError, match="private file"):
            Store(store.directory)
    finally:
        path.chmod(0o600)


def test_schema_initialization_rolls_back_on_failure(tmp_path, monkeypatch):
    import sqlite3

    from agentbreeder import store as module

    connect = sqlite3.connect

    class InterruptedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "CREATE TABLE events" in sql:
                raise sqlite3.OperationalError("injected initialization failure")
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        module.sqlite3,
        "connect",
        lambda *a, **kw: connect(*a, **kw, factory=InterruptedConnection),
    )
    with pytest.raises(sqlite3.OperationalError):
        Store(tmp_path / "state")
    monkeypatch.setattr(module.sqlite3, "connect", connect)
    recovered = Store(tmp_path / "state")
    try:
        assert recovered.db.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        recovered.close()
