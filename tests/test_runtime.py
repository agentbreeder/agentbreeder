import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from agentbreeder.manifest import Manifest
from agentbreeder.runtime import Docker
from agentbreeder.store import DeploymentError


def test_command_bounded_safe_and_context_pinned(monkeypatch):
    runtime = Docker()
    runtime.context = "desktop-local"
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote")
    assert runtime.command("info") == "ok"
    args, kwargs = run.call_args
    assert args[0] == ["docker", "--context", "desktop-local", "info"]
    assert kwargs["timeout"] == 30
    assert "DOCKER_HOST" not in kwargs["env"]
    assert not kwargs.get("shell")


def test_command_secrets_travel_in_environment_not_argv(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
    monkeypatch.setattr(subprocess, "run", run)
    Docker().command("container", "create", "--env", "SECRET", env={"SECRET": "s3cr3t"})
    args, kwargs = run.call_args
    assert "s3cr3t" not in " ".join(args[0]) and kwargs["env"]["SECRET"] == "s3cr3t"


@pytest.mark.parametrize("host", ["tcp://remote:2375", "ssh://remote"])
def test_remote_context_denied(host, monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    runtime = Docker()
    runtime.command = Mock(return_value=json.dumps([{"Endpoints": {"docker": {"Host": host}}}]))
    with pytest.raises(DeploymentError):
        runtime.authorize("remote")
    assert runtime.command.call_count == 1


def test_remote_override_denied(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote")
    runtime = Docker()
    runtime.command = Mock()
    with pytest.raises(DeploymentError):
        runtime.authorize()
    runtime.command.assert_not_called()


def test_verified_absence_not_an_inspect_failure():
    runtime = Docker()
    record = {"container": "owned", "id": "id", "digest": "hash"}
    runtime.command = Mock(return_value="")
    assert runtime.inspect(record) is None
    runtime.command.side_effect = DeploymentError("Docker unavailable")
    with pytest.raises(DeploymentError):
        runtime.inspect(record)


def test_ownership_mismatch_denied():
    runtime = Docker()
    runtime.command = Mock(side_effect=["id", json.dumps([{"Config": {"Labels": {}}}])])
    with pytest.raises(DeploymentError):
        runtime.inspect({"container": "owned", "id": "id", "digest": "hash"})


def test_create_has_restricted_runtime_flags():
    runtime = Docker()
    runtime.command = Mock(return_value=json.dumps([{"Config": {}}]))
    manifest = Manifest(schema_version=1, name="test", image="sha256:" + "a" * 64)
    runtime.create({"container": "owned", "id": "id", "digest": "hash"}, manifest)
    args, kwargs = runtime.command.call_args
    for flag in ["--read-only", "--cap-drop", "--security-opt", "--pids-limit", "--memory"]:
        assert flag in args
    assert args[args.index("--publish") + 1] == "127.0.0.1::8080"
    assert "--privileged" not in args
    assert kwargs["timeout"] == 120


@pytest.mark.parametrize("ip,running", [("0.0.0.0", True), ("127.0.0.1", False)])
def test_endpoint_must_be_running_and_local(ip, running):
    manifest = Manifest(schema_version=1, name="test", image="sha256:" + "a" * 64)
    info = {
        "State": {"Running": running},
        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostIp": ip, "HostPort": "1234"}]}},
    }
    with pytest.raises(DeploymentError):
        Docker().endpoint(info, manifest)


def test_image_present_checks_local_ids_only():
    runtime = Docker()
    runtime.command = Mock(return_value="sha256:" + "a" * 64)
    assert runtime.image_present("sha256:" + "a" * 64)
    runtime.command = Mock(side_effect=DeploymentError("no such image"))
    assert not runtime.image_present("sha256:" + "a" * 64)
    assert runtime.image_present("registry.example.com/a@sha256:" + "b" * 64)  # pullable
    runtime.command.assert_called_once()


def test_relative_state_dir_is_made_absolute(tmp_path, monkeypatch):
    from agentbreeder.store import Store

    monkeypatch.chdir(tmp_path)
    store = Store(Path("state"))
    assert store.directory.is_absolute() and store.directory == tmp_path / "state"


def test_state_dir_with_comma_refused(tmp_path):
    from agentbreeder.store import Store

    with pytest.raises(DeploymentError, match="comma"):
        Store(tmp_path / "a,b")
    assert not (tmp_path / "a,b").exists()


def test_image_declared_volumes_denied():
    runtime = Docker()
    runtime.command = Mock(return_value=json.dumps([{"Config": {"Volumes": {"/data": {}}}}]))
    manifest = Manifest(schema_version=1, name="test", image="sha256:" + "a" * 64)
    with pytest.raises(DeploymentError, match="VOLUME are unsupported"):
        runtime.create({"container": "owned", "id": "id", "digest": "hash"}, manifest)
    assert runtime.command.call_count == 1


@pytest.mark.parametrize("platform,expected", [("linux", True), ("darwin", False)])
def test_host_gateway_only_on_linux(monkeypatch, platform, expected):
    from agentbreeder import runtime as runtime_module
    from agentbreeder.secret_files import SecretMount

    monkeypatch.setattr(runtime_module.sys, "platform", platform)
    runtime = Docker()
    runtime.command = Mock(return_value=json.dumps([{"Config": {}}]))
    manifest = Manifest(schema_version=1, name="test", image="sha256:" + "a" * 64)
    mount = SecretMount(("A", "B"), "k", Path("/state/k"), "/run/secrets/k")
    runtime.create(
        {"container": "owned", "id": "id", "digest": "hash"},
        manifest,
        environment={"X": "1"},
        mounts=[mount],
        host_gateway=True,
    )
    args = runtime.command.call_args.args
    assert ("--add-host" in args) is expected
    assert "--env=X=1" in args and "--env=A_FILE=/run/secrets/k" in args
    assert "--env=B_FILE=/run/secrets/k" in args
    assert args[args.index("--mount") + 1] == "type=bind,src=/state/k,dst=/run/secrets/k,readonly"
