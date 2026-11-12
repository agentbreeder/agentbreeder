"""`agentbreeder cluster`, the local kind helper, with the external tools faked."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentbreeder import cli, cluster
from agentbreeder.services.schema import ServiceError


class Tools:
    """Answers the fixed commands cluster.py runs and records them."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.stdin: dict[str, bytes] = {}
        self.clusters = ""
        self.containers = set()
        self.networks = {"kind": {"IPAddress": "172.30.0.2"}}
        self.version = "kind v0.33.0 go1.24 darwin/amd64"
        self.owner = cluster.NAME
        self.nodes = "node/agentbreeder-control-plane"
        self.workloads = ""

    def __call__(self, argv, *, stdin=None, timeout=120, what):
        self.calls.append(argv)
        joined = " ".join(argv)
        if stdin:
            self.stdin[argv[0] + " " + argv[1]] = stdin
        if joined == "kind version":
            return self.version
        if joined == "kind get clusters":
            return self.clusters
        if "get kubeconfig" in joined:
            return "kubeconfig-text"
        if "get nodes" in joined:
            return self.nodes
        if "jsonpath" in joined:
            return "uid-1"
        if argv[:3] == ["docker", "container", "inspect"]:
            if argv[-1] not in self.containers:
                raise ServiceError("CLUSTER_FAILED", "missing")
            return self.owner if "--format" in argv else "{}"
        if "get deployments" in joined:
            return self.workloads
        if ".IPAM.Config" in joined:
            return json.dumps([{"Subnet": "172.30.0.0/16"}, {"Subnet": "fc00:f853::/64"}])
        if ".NetworkSettings.Networks" in joined:
            return json.dumps(self.networks)
        if argv[:3] == ["docker", "network", "connect"]:
            self.networks[argv[3]] = {"IPAddress": "172.31.0.6"}
        return ""


@pytest.fixture(autouse=True)
def _private_stacks_root(tmp_path, monkeypatch):
    """`cluster up/down` take a per-user lock under ~/.agentbreeder; never the developer's."""
    monkeypatch.setenv("AGENTBREEDER_STACKS_DIR", str(tmp_path / "stacks-root"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def tools(monkeypatch):
    fake = Tools()
    monkeypatch.setattr(cluster, "_run", fake)
    monkeypatch.setattr(cluster.shutil, "which", lambda name: f"/bin/{name}")
    return fake


def test_tools_report_missing_with_install_help(tools, monkeypatch):
    monkeypatch.setattr(cluster.shutil, "which", lambda name: None)
    state = cluster.tools()
    assert state["kind"]["present"] is False and "kind.sigs" in state["kind"]["install"]
    with pytest.raises(ServiceError, match="CLUSTER_TOOL_MISSING"):
        cluster.require_tools()


def test_old_kind_is_refused(tools):
    tools.version = "kind v0.24.0"
    with pytest.raises(ServiceError, match="measured"):
        cluster.require_tools()


def test_kind_version_failure_is_unsupported(tools, monkeypatch):
    def fail(*args, **kwargs):
        raise ServiceError("CLUSTER_FAILED", "x")

    monkeypatch.setattr(cluster, "_run", fail)
    assert cluster._kind_supported() is False


def test_registry_is_pinned_loopback_and_idempotent(tools):
    cluster.ensure_registry()
    run = tools.calls[-1]
    assert cluster.REGISTRY_IMAGE in run and "127.0.0.1:5001:5000" in run
    assert "@sha256:" in cluster.REGISTRY_IMAGE
    tools.containers.add(cluster.REGISTRY)
    calls = len(tools.calls)
    cluster.ensure_registry()
    assert not any(c[:2] == ["docker", "run"] for c in tools.calls[calls:])


def test_a_foreign_registry_container_is_refused(tools):
    tools.containers.add(cluster.REGISTRY)
    tools.owner = ""
    with pytest.raises(ServiceError, match="NAME_TAKEN"):
        cluster.ensure_registry()


def test_a_foreign_cluster_is_never_changed_or_deleted(tools, fake_engine, tmp_path):
    tools.clusters = cluster.NAME
    tools.nodes = ""
    with pytest.raises(ServiceError, match="NAME_TAKEN"):
        cluster.ensure_cluster(tmp_path / "kubeconfig")
    assert not any(c[:2] == ["docker", "exec"] for c in tools.calls)
    with pytest.raises(ServiceError, match="NAME_TAKEN"):
        cluster.down(tmp_path)
    assert not any(c[:2] == ["kind", "delete"] for c in tools.calls)


class FakeEngine:
    instances: list = []

    def __init__(self, project, log=None):
        self.state = FakeState(project)
        self.active: list[str] = []
        self.confirmed = None
        FakeEngine.instances.append(self)

    def up(self, environment, *, confirm, expect_grant):
        self.confirmed = (environment, expect_grant)
        return {"services": {}}

    def active_cluster_deployments(self):
        return self.active

    def close(self):
        pass


class FakeState:
    project = "agentbreeder-x"

    def __init__(self, directory):
        self.directory = directory
        self.values = {}

    def put(self, key, value):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def environments(self):
        return ["dev"]

    def env_dir(self, environment):
        return self.directory


@pytest.fixture
def fake_engine(monkeypatch):
    from agentbreeder.services import engine

    FakeEngine.instances = []
    monkeypatch.setattr(engine, "Engine", FakeEngine)
    return FakeEngine


def test_up_without_confirmation_only_records_the_cluster(tools, fake_engine, tmp_path):
    tools.networks["agentbreeder-x-agents"] = {"IPAddress": "172.31.0.6"}
    result = cluster.up(tmp_path, "dev")
    instance = fake_engine.instances[0]
    assert result["cluster"] == cluster.CONTEXT and instance.confirmed is None
    assert json.loads(instance.state.values["kubernetes"])["namespace"] == cluster.NAMESPACE


def test_up_with_confirmation_accepts_the_grant(tools, fake_engine, tmp_path):
    cluster.up(tmp_path, "dev", confirm=lambda grant: True, expect_grant="d")
    assert fake_engine.instances[0].confirmed == ("dev", "d")


def test_status_reports_the_accepted_grant(tools, fake_engine, tmp_path):
    from agentbreeder.services import engine as engine_module

    assert cluster.status(tmp_path)["configured"] is False
    states = {}

    class Stateful(FakeEngine):
        def __init__(self, project, log=None):
            super().__init__(project, log)
            self.state.values = states

    engine_module.Engine = Stateful
    states["kubernetes"] = json.dumps({"cluster_uid": "uid-1"})
    assert cluster.status(tmp_path)["configured"] is False  # recorded, not yet accepted
    config = {"environments": {"dev": {"kubernetes": {"cluster_uid": "uid-1"}}}}
    (tmp_path / "operator.yaml").write_text(json.dumps(config))
    assert cluster.status(tmp_path)["configured"] is True
    states["kubernetes"] = json.dumps({"cluster_uid": "new-cluster"})
    assert cluster.status(tmp_path)["configured"] is False  # recreated since it was accepted
    states["kubernetes"] = ""
    assert cluster.status(tmp_path)["configured"] is False


def test_down_removes_only_what_it_owns(tools, fake_engine, tmp_path):
    tools.clusters = cluster.NAME
    tools.containers.add(cluster.REGISTRY)
    assert cluster.down(tmp_path)["removed"]
    assert ["kind", "delete", "cluster", "--name", cluster.NAME] in [c[:5] for c in tools.calls]
    assert ["docker", "rm", "--force", cluster.REGISTRY] in tools.calls


def test_down_refuses_while_agents_are_on_the_machine_wide_cluster(tools, fake_engine, tmp_path):
    tools.clusters = cluster.NAME
    tools.workloads = "deployment.apps/other-stack"
    with pytest.raises(ServiceError, match="CLUSTER_IN_USE"):
        cluster.down(tmp_path)
    assert not any(c[:2] == ["kind", "delete"] for c in tools.calls)


def test_down_refuses_while_this_stack_has_deployments(tools, fake_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(FakeEngine, "active_cluster_deployments", lambda self: ["dev/agent"])
    with pytest.raises(ServiceError, match="CLUSTER_IN_USE"):
        cluster.down(tmp_path)


def test_cluster_uses_a_pinned_node_and_mirror(tools, tmp_path):
    tools.networks = {}
    cluster.ensure_cluster(tmp_path / "kubeconfig")
    create = next(c for c in tools.calls if c[:3] == ["kind", "create", "cluster"])
    assert cluster.NODE_IMAGE in create and "@sha256:" in cluster.NODE_IMAGE
    assert b"certs.d" in tools.stdin["kind create"]
    assert cluster.OWNER_LABEL.encode() in tools.stdin["kind create"]
    assert ["docker", "network", "connect", "kind", cluster.REGISTRY] in tools.calls


def test_existing_cluster_is_kept(tools, tmp_path):
    tools.clusters = cluster.NAME
    cluster.ensure_cluster(tmp_path / "kubeconfig")
    assert not any(c[:2] == ["kind", "create"] for c in tools.calls)


def test_connect_is_idempotent(tools):
    cluster.connect(cluster.NODE, "kind")
    assert not any(c[:3] == ["docker", "network", "connect"] for c in tools.calls)


def test_block_for_a_stack(tools, tmp_path):
    state = SimpleNamespace(directory=tmp_path)
    block = cluster.block_for(state, "stack-agents")
    assert block["cluster_uid"] == "uid-1" and block["context"] == cluster.CONTEXT
    assert block["kubeconfig"] == str(tmp_path / "kubeconfig")
    assert (tmp_path / "kubeconfig").stat().st_mode & 0o077 == 0
    assert set(block["blocked_cidrs"]) == {
        cluster.POD_CIDR,
        cluster.SERVICE_CIDR,
        cluster.METADATA_CIDR,
        "172.30.0.0/16",
        "172.31.0.6/32",
    }
    namespace = json.loads(tools.stdin["kubectl --kubeconfig"])
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"


def test_blocked_cidrs_without_an_agents_address(tools):
    assert "172.31.0.6/32" not in cluster.blocked_cidrs("not-attached")


def test_real_run_never_echoes_output(monkeypatch):
    monkeypatch.undo()
    result = SimpleNamespace(returncode=3, stdout=b"", stderr=b"token=abc")
    monkeypatch.setattr(cluster.subprocess, "run", lambda *a, **k: result)
    with pytest.raises(ServiceError) as error:
        cluster._run(["x"], what="doing x")
    assert "abc" not in str(error.value)


def test_real_run_handles_a_missing_binary(monkeypatch):
    def missing(*args, **kwargs):
        raise OSError

    monkeypatch.setattr(cluster.subprocess, "run", missing)
    with pytest.raises(ServiceError, match="failed or timed out"):
        cluster._run(["x"], what="x")


def test_cli_status_and_errors(monkeypatch, capsys):
    monkeypatch.setattr(cluster, "status", lambda project: {"cluster": True})
    assert cli.main(["cluster", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"cluster": True}

    def refused(project):
        raise ServiceError("CLUSTER_IN_USE", "remove first")

    monkeypatch.setattr(cluster, "down", refused)
    assert cli.main(["cluster", "down"]) == 2
    assert "CLUSTER_IN_USE" in capsys.readouterr().err


def test_cli_up_passes_confirmation(monkeypatch, capsys):
    seen = {}

    def fake_up(project, environment, *, confirm, expect_grant, log):
        seen.update(environment=environment, expect_grant=expect_grant)
        return {"ok": True}

    monkeypatch.setattr(cluster, "up", fake_up)
    assert cli.main(["cluster", "up", "--environment", "dev", "--yes"]) == 0
    assert seen == {"environment": "dev", "expect_grant": None}


# -- S-11: one cluster up/down at a time on this machine ---------------------------------------


def test_cluster_up_and_down_hold_a_machine_wide_lock(tools, fake_engine, tmp_path, monkeypatch):
    import fcntl
    import os

    lock = tmp_path / "home" / ".agentbreeder" / "locks" / "cluster.lock"
    with cluster.cluster_lock():
        assert lock.exists() and (lock.stat().st_mode & 0o777) == 0o600
        # A second run (another terminal, or a console job) fails fast instead of interleaving.
        with pytest.raises(ServiceError, match="CLUSTER_BUSY"):
            cluster.up(tmp_path, "dev")
        with pytest.raises(ServiceError, match="CLUSTER_BUSY"):
            cluster.down(tmp_path)
    seen = []

    def probe(*args, **kwargs):
        descriptor = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append("free")
        except BlockingIOError:
            seen.append("held")
        finally:
            os.close(descriptor)
        return {}

    monkeypatch.setattr(cluster, "_down", probe)
    monkeypatch.setattr(cluster, "_up", probe)
    cluster.down(tmp_path)
    cluster.up(tmp_path, "dev")
    assert seen == ["held", "held"]  # held for the whole run, released between runs


def test_cluster_busy_is_reported_by_the_cli(tools, fake_engine, tmp_path, capsys):
    with cluster.cluster_lock():
        assert cli.main(["cluster", "down", "--stack", str(tmp_path)]) == 2
    assert "[CLUSTER_BUSY]" in capsys.readouterr().err


def test_the_cluster_lock_ignores_the_stacks_override(tmp_path, monkeypatch):
    """Two stacks roots (AGENTBREEDER_STACKS_DIR) still share the one machine-wide lock."""
    with cluster.cluster_lock():
        monkeypatch.setenv("AGENTBREEDER_STACKS_DIR", str(tmp_path / "another-root"))
        with pytest.raises(ServiceError, match="CLUSTER_BUSY"):
            with cluster.cluster_lock():
                pass
    path = cluster.lock_path()
    assert path.parent.stat().st_mode & 0o777 == 0o700
    path.unlink()
    path.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ServiceError, match="STATE_UNSAFE"):
        with cluster.cluster_lock():
            pass
    assert not (tmp_path / "elsewhere").exists()
