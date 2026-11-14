"""The Kubernetes target, against a fake kubectl: real argv, stdin and object contents."""

from __future__ import annotations

import base64
import json
from itertools import chain, repeat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from conftest import v2_manifest_data, write_private

from agentbreeder import cli, kubernetes
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.kubernetes import DEPLOYMENT_LABEL, IMAGE_ANNOTATION, SPEC_ANNOTATION, Kubernetes
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import load_operator_config
from agentbreeder.secret_files import SecretMount
from agentbreeder.store import DeploymentError, Store

UID = "11111111-2222-3333-4444-555555555555"
IMAGE = "localhost:5001/agentbreeder/sample@sha256:" + "b" * 64
RECORD = {"id": "c" * 32, "digest": "d" * 64, "container": "agentbreeder-" + "c" * 32}
SECRET_VALUE = "super-secret-value"


def kubeconfig_data(server="https://127.0.0.1:6443", user=None) -> dict:
    return {
        "contexts": [{"name": "kind-x", "context": {"cluster": "c", "user": "u"}}],
        "clusters": [{"name": "c", "cluster": {"server": server}}],
        "users": [{"name": "u", "user": user or {"client-key-data": "x"}}],
    }


class FakeCluster:
    def __init__(self):
        self.uid = UID
        self.labels = {
            kubernetes.MANAGED_BY: "agentbreeder",
            kubernetes.POD_SECURITY: "restricted",
        }
        self.calls: list[list[str]] = []
        self.stdin: list[bytes] = []
        self.deployment: dict | None = None
        self.fail: str | None = None
        self.env: dict = {}
        self.waiting: str | None = None
        self.left = ""

    def run(self, argv, capture_output, input, timeout, check, env):
        self.calls.append(argv)
        self.env = env
        if input:
            self.stdin.append(input)
        verb = argv[5:]
        code, output = 0, ""
        if self.fail and self.fail in verb:
            code = 1
        elif verb[:2] == ["get", "namespace"] and verb[2] == "kube-system":
            output = self.uid
        elif verb[:2] == ["get", "namespace"]:
            output = json.dumps({"metadata": {"labels": self.labels}})
        elif "deployment" in verb and "get" in verb:
            output = json.dumps(self.deployment) if self.deployment else ""
        elif "pods" in verb and "get" in verb:
            status = {"state": {"waiting": {"reason": self.waiting}}}
            output = json.dumps({"items": [{"status": {"containerStatuses": [status]}}]})
        elif any(part.startswith("pods,") for part in verb):
            output = self.left
        return SimpleNamespace(returncode=code, stdout=output.encode())


@pytest.fixture
def cluster(monkeypatch):
    fake = FakeCluster()
    monkeypatch.setattr(kubernetes.subprocess, "run", fake.run)
    monkeypatch.setattr(kubernetes.time, "sleep", lambda _: None)
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


@pytest.fixture
def kube(config, cluster):
    runtime = Kubernetes(config)
    runtime.select("dev")
    return runtime


def manifest(**overrides) -> Manifest:
    return Manifest.model_validate(v2_manifest_data(target="kubernetes", image=IMAGE, **overrides))


def owned(**annotations) -> dict:
    return {
        "metadata": {
            "uid": "dep-uid",
            "labels": {DEPLOYMENT_LABEL: RECORD["id"]},
            "annotations": {
                SPEC_ANNOTATION: RECORD["digest"],
                IMAGE_ANNOTATION: IMAGE,
                **annotations,
            },
        },
        "spec": {"template": {"spec": {"containers": [{"image": IMAGE}]}}},
        "status": {"availableReplicas": 1},
    }


def test_select_needs_a_kubernetes_block(identity_env, cluster):
    with pytest.raises(DeploymentError, match="KUBERNETES_UNCONFIGURED"):
        Kubernetes(identity_env.load()).select("dev")


def test_authorize_pins_kubeconfig_context_and_strips_kubeconfig_env(kube, cluster, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", "/elsewhere")
    kube.authorize()
    assert cluster.calls[0][:5] == [
        "kubectl",
        "--kubeconfig",
        kube.kubeconfig,
        "--context",
        "kind-x",
    ]
    assert "KUBECONFIG" not in cluster.env


def test_authorize_refuses_a_different_cluster(kube, cluster):
    cluster.uid = "99999999-2222-3333-4444-555555555555"
    with pytest.raises(DeploymentError, match="CLUSTER_MISMATCH"):
        kube.authorize()


@pytest.mark.parametrize(
    "removed,code",
    [(kubernetes.MANAGED_BY, "UNMANAGED"), (kubernetes.POD_SECURITY, "UNRESTRICTED")],
)
def test_authorize_refuses_an_unprepared_namespace(kube, cluster, removed, code):
    del cluster.labels[removed]
    with pytest.raises(DeploymentError, match=code):
        kube.authorize()


@pytest.mark.parametrize(
    "data,code",
    [
        (kubeconfig_data(server="https://10.0.0.5:6443"), "NOT_LOCAL"),
        (kubeconfig_data(user={"exec": {"command": "x"}}), "KUBECONFIG_INVALID"),
        (kubeconfig_data(user={"auth-provider": {"name": "x"}}), "KUBECONFIG_INVALID"),
        (
            {
                **kubeconfig_data(),
                "clusters": [
                    {
                        "name": "c",
                        "cluster": {"server": "https://127.0.0.1:1", "proxy-url": "http://p:1"},
                    }
                ],
            },
            "KUBECONFIG_INVALID",
        ),
        ({"contexts": []}, "KUBECONFIG_INVALID"),
    ],
)
def test_authorize_refuses_unsafe_kubeconfigs(kube, cluster, data, code):
    Path(kube.kubeconfig).write_text(yaml.safe_dump(data))
    with pytest.raises(DeploymentError, match=code):
        kube.authorize()
    assert cluster.calls == []  # refused before kubectl runs


def test_authorize_refuses_a_world_readable_kubeconfig(kube, cluster):
    Path(kube.kubeconfig).chmod(0o644)
    with pytest.raises(DeploymentError, match="KUBECONFIG_INVALID"):
        kube.authorize()


def test_authorize_uses_the_recorded_binding(kube, cluster, tmp_path):
    other = write_private(tmp_path / "other", kubeconfig_data())
    fresh = Kubernetes(None)
    fresh.authorize(f"kind-x|{UID}|agents|{other}")
    assert fresh.kubeconfig == str(other) and fresh.namespace == "agents"
    with pytest.raises(DeploymentError, match="binding"):
        fresh.authorize("broken")


def _created(cluster) -> dict:
    return {item["kind"]: item for item in json.loads(cluster.stdin[-1])["items"]}


def _mount(tmp_path) -> SecretMount:
    source = tmp_path / "database-url"
    source.write_text(SECRET_VALUE)
    return SecretMount(("DATABASE_URL",), "database-url", source, "/run/secrets/database-url")


def test_create_passes_the_deployment_id_variable(kube, cluster):
    # Deploy gives every model-access container its own ID on both targets (#907).
    kube.create(RECORD, manifest(), environment={"AGENTBREEDER_DEPLOYMENT_ID": "a" * 32})
    env = _created(cluster)["Deployment"]["spec"]["template"]["spec"]["containers"][0]["env"]
    assert {"name": "AGENTBREEDER_DEPLOYMENT_ID", "value": "a" * 32} in env


def test_create_sends_hardened_objects_over_stdin(kube, cluster, tmp_path):
    kube.create(RECORD, manifest(), environment={"A": "1"}, mounts=[_mount(tmp_path)])
    assert all(SECRET_VALUE not in " ".join(argv) for argv in cluster.calls)
    assert "create" in cluster.calls[-1] and "apply" not in cluster.calls[-1]
    objects = _created(cluster)
    assert set(objects) == {"Secret", "Service", "NetworkPolicy", "Deployment"}
    secret = objects["Secret"]
    assert base64.b64decode(secret["data"]["database-url"]).decode() == SECRET_VALUE
    assert secret["immutable"] is True
    deployment = objects["Deployment"]
    pod = deployment["spec"]["template"]["spec"]
    assert SECRET_VALUE not in json.dumps(deployment)
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsUser"] == 65532
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    container = pod["containers"][0]
    assert container["image"] == IMAGE
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert {"name": "DATABASE_URL_FILE", "value": "/run/secrets/database-url"} in container["env"]
    assert objects["Service"]["spec"]["type"] == "ClusterIP"
    policy = objects["NetworkPolicy"]["spec"]
    assert policy["ingress"] == []
    assert policy["egress"][1]["to"][0]["ipBlock"]["except"] == ["10.244.0.0/16"]
    assert deployment["metadata"]["annotations"][IMAGE_ANNOTATION] == IMAGE


def test_create_without_secrets_has_no_secret_object(kube, cluster):
    kube.create(RECORD, manifest())
    assert "Secret" not in _created(cluster)


def test_create_refuses_a_runtime_boundary_and_missing_cidrs(kube, cluster):
    with pytest.raises(DeploymentError, match="enforcement"):
        kube.create(RECORD, manifest(), network="boundary")
    kube.blocked = ()
    with pytest.raises(DeploymentError, match="blocked_cidrs"):
        kube.create(RECORD, manifest())
    assert cluster.stdin == []


def test_failed_create_deletes_everything_labelled(kube, cluster):
    cluster.fail = "create"
    with pytest.raises(DeploymentError, match="rejected"):
        kube.create(RECORD, manifest())
    assert any("delete" in argv for argv in cluster.calls)


def test_inspect_checks_ownership(kube, cluster):
    assert kube.inspect(RECORD) is None
    cluster.deployment = owned()
    assert kube.inspect(RECORD)["State"]["Running"] is True
    cluster.deployment = owned(**{SPEC_ANNOTATION: "other"})
    with pytest.raises(DeploymentError, match="ownership"):
        kube.inspect(RECORD)
    cluster.deployment = owned(**{IMAGE_ANNOTATION: "other"})
    with pytest.raises(DeploymentError, match="ownership"):
        kube.inspect(RECORD)
    cluster.deployment = {"metadata": {}}
    with pytest.raises(DeploymentError, match="Cannot verify"):
        kube.inspect(RECORD)


def test_start_returns_when_available_and_names_the_failure(kube, cluster):
    info = {"name": RECORD["container"], "deployment": RECORD["id"]}
    cluster.deployment = owned()
    kube.start(info)
    cluster.deployment = {"status": {}}
    cluster.waiting = "ImagePullBackOff"
    with pytest.raises(DeploymentError, match="ImagePullBackOff"):
        kube.start(info)


def test_start_times_out(kube, cluster, monkeypatch):
    ticks = iter([0, 1000, 2000])
    monkeypatch.setattr(kubernetes.time, "monotonic", lambda: next(ticks))
    with pytest.raises(DeploymentError, match="ROLLOUT_TIMEOUT"):
        kube.start({"name": "n", "deployment": RECORD["id"]})


def test_remove_verifies_nothing_is_left(kube, cluster, monkeypatch):
    kube.remove({"deployment": RECORD["id"]})
    delete = next(argv for argv in cluster.calls if "delete" in argv)
    assert f"{DEPLOYMENT_LABEL}={RECORD['id']}" in delete
    cluster.left = "pod/x"
    ticks = iter([0, 1, 1000])
    monkeypatch.setattr(kubernetes.time, "monotonic", lambda: next(ticks))
    with pytest.raises(DeploymentError, match="could not be verified"):
        kube.remove({"deployment": RECORD["id"]})


def test_endpoint_and_parse(kube):
    info = {"State": {"Running": True}, "name": "svc"}
    endpoint = kube.endpoint(info, manifest())
    assert endpoint == "kubernetes://agents/svc:8080"
    assert Kubernetes.parse_endpoint(endpoint) == ("agents", "svc", 8080)
    with pytest.raises(DeploymentError):
        kube.endpoint({"State": {"Running": False}, "name": "svc"}, manifest())
    with pytest.raises(DeploymentError):
        Kubernetes.parse_endpoint("http://127.0.0.1:1")


class FakeProcess:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.terminated = False

    def poll(self):
        return 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout):
        return 0


def test_forward_yields_the_port_and_terminates(kube, monkeypatch):
    process = FakeProcess(["Forwarding from 127.0.0.1:4321 -> 8080\n"])
    popen = Mock(return_value=process)
    monkeypatch.setattr(kubernetes.subprocess, "Popen", popen)
    with kube.forward("kubernetes://agents/svc:8080") as port:
        assert port == 4321
    assert process.terminated
    argv = popen.call_args.args[0]
    assert argv[argv.index("--address") + 1] == "127.0.0.1"
    assert "service/svc" in argv


def test_forward_that_never_binds_is_an_error(kube, monkeypatch):
    process = FakeProcess([])
    monkeypatch.setattr(kubernetes.subprocess, "Popen", Mock(return_value=process))
    assert kube.healthy("kubernetes://agents/svc:8080", "/health") is False
    assert process.terminated


def test_healthy_probes_through_the_forward(kube, monkeypatch):
    process = FakeProcess(["Forwarding from 127.0.0.1:4321 -> 8080\n"])
    monkeypatch.setattr(kubernetes.subprocess, "Popen", Mock(return_value=process))
    probe = Mock(return_value=True)
    monkeypatch.setattr(kubernetes, "probe_http", probe)
    assert kube.healthy("kubernetes://agents/svc:8080", "/health") is True
    probe.assert_called_once_with(4321, "/health", 30)


# -- the lifecycle, with the runtime mocked ------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    state = Store(tmp_path / "state")
    yield state
    state.close()


def fake_runtime(context):
    runtime = Mock(context=context)
    runtime.inspect.side_effect = chain([None], repeat({"Id": "x", "State": {"Running": True}}))
    runtime.endpoint.return_value = "kubernetes://agents/svc:8080"
    runtime.healthy.return_value = True
    return runtime


@pytest.fixture
def docker():
    return fake_runtime("docker-context")


@pytest.fixture
def cube():
    return fake_runtime(f"kind-x|{UID}|agents|/k")


def test_deploy_routes_to_the_cluster_and_binds_it(store, docker, cube, config):
    service = DeploymentService(store, docker, config, kubernetes=cube)
    result = service.deploy(manifest(), local=False, environment="dev")
    assert result["state"] == "ready"
    cube.select.assert_called_once_with("dev")
    cube.create.assert_called_once()
    docker.create.assert_not_called()
    assert store.get(result["id"])["context"] == cube.context


def test_one_name_is_active_once_per_target(store, docker, cube, config, v2_manifest):
    service = DeploymentService(store, docker, config, kubernetes=cube)
    local = service.deploy(v2_manifest, local=True, environment="dev")
    cluster = service.deploy(manifest(), local=False, environment="dev")
    assert local["state"] == cluster["state"] == "ready" and local["id"] != cluster["id"]
    assert store.active("sample")["id"] == local["id"]
    assert store.active("sample", "kubernetes")["id"] == cluster["id"]


@pytest.mark.parametrize("local,target", [(True, "kubernetes"), (False, "local")])
def test_confirmation_flag_must_match_the_target(store, docker, cube, config, local, target):
    data = v2_manifest_data(target=target, image=IMAGE)
    with pytest.raises(DeploymentError, match="--cluster"):
        DeploymentService(store, docker, config, kubernetes=cube).deploy(
            Manifest.model_validate(data), local=local, environment="dev"
        )
    cube.create.assert_not_called()


def test_cluster_deploy_without_a_block_is_denied(store, docker, cube, identity_env):
    identity_env.config["environments"]["dev"] = {"targets": ["kubernetes"]}
    service = DeploymentService(store, docker, identity_env.load(), kubernetes=cube)
    with pytest.raises(GateDenied, match="no kubernetes block"):
        service.deploy(manifest(), local=False, environment="dev")
    cube.create.assert_not_called()


def test_mcp_access_is_refused_on_the_cluster(store, docker, cube, config):
    service = DeploymentService(store, docker, config, kubernetes=cube)
    problem = service._kubernetes_requirements(SimpleNamespace(mcp_access=[1]), "dev")
    assert problem[0] == "KUBERNETES_MCP_UNSUPPORTED"
    service.enforcing = True
    problem = service._kubernetes_requirements(SimpleNamespace(mcp_access=None), "dev")
    assert problem[0] == "KUBERNETES_ENFORCEMENT_UNSUPPORTED"


def test_remove_routes_and_checks_the_flag(store, docker, cube, config):
    service = DeploymentService(store, docker, config, kubernetes=cube)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    with pytest.raises(DeploymentError, match="--cluster"):
        service.remove(deployed["id"], local=True)
    cube.inspect.side_effect = [{"Id": "x", "State": {"Running": True}}, None]
    assert service.remove(deployed["id"], local=False)["state"] == "removed"
    cube.authorize.assert_called_with(cube.context)
    cube.remove.assert_called_once()
    docker.remove.assert_not_called()


def test_a_changed_cluster_binding_is_refused(store, docker, cube, config):
    service = DeploymentService(store, docker, config, kubernetes=cube)
    service.deploy(manifest(), local=False, environment="dev")
    cube.context = f"other|{UID}|agents|/k"
    with pytest.raises(DeploymentError, match="BINDING_CHANGED"):
        service.deploy(manifest(), local=False, environment="dev")


def test_remove_sweeps_when_the_deployment_is_gone(store, docker, cube, config):
    service = DeploymentService(store, docker, config, kubernetes=cube)
    deployed = service.deploy(manifest(), local=False, environment="dev")
    cube.inspect.side_effect = [None, None]
    assert service.remove(deployed["id"], local=False)["state"] == "removed"
    cube.sweep.assert_called_once()
    cube.remove.assert_not_called()


def test_sweep_deletes_by_label(kube, cluster):
    kube.sweep(RECORD)
    assert any("delete" in argv for argv in cluster.calls)


def test_kill_switch_stops_the_cluster_workload_too(store, docker, cube, config, v2_manifest):
    service = DeploymentService(store, docker, config, kubernetes=cube)
    actor = Mock(label="admin", verified=False)
    local = service.deploy(v2_manifest, local=True, environment="dev")
    cluster = service.deploy(manifest(), local=False, environment="dev")
    docker.inspect.side_effect = [{"Id": "l", "State": {"Running": True}}, None]
    cube.inspect.side_effect = [{"Id": "k", "State": {"Running": True}}, None]
    result = service.disable("sample", actor)
    assert store.get(local["id"])["state"] == "disabled"
    assert store.get(cluster["id"])["state"] == "disabled"
    cube.remove.assert_called_once()
    docker.remove.assert_called_once()
    assert result["state"] != "failed"


# -- CLI -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("flags", [[], ["--local", "--cluster"]], ids=["neither", "both"])
def test_cli_needs_exactly_one_target_flag(flags, capsys):
    with pytest.raises(SystemExit):
        cli.main(["deploy", "agent.yaml", *flags])


def test_forward_refuses_a_local_deployment(store, docker, cube, config, v2_manifest, capsys):
    deployed = DeploymentService(store, docker, config, kubernetes=cube).deploy(
        v2_manifest, local=True, environment="dev"
    )
    code = cli.main(["forward", deployed["id"], "--state-dir", str(store.directory)])
    assert code == 2 and "ready Kubernetes" in capsys.readouterr().err


def test_load_operator_config_rejects_bad_cidrs(identity_env, config):
    entry = identity_env.config["environments"]["dev"]["kubernetes"]
    entry["blocked_cidrs"] = ["not-a-cidr"]
    identity_env.write()
    with pytest.raises(ValueError):
        load_operator_config(identity_env.path)
