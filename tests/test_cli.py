import json
import subprocess

import pytest

from agentbreeder import cli, doctor


def test_missing_docker(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    assert doctor.check_docker()[0].passed is False


def test_docker_command_is_bounded_read_only(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/docker")

    def run(command, **options):
        assert command == ["/usr/bin/docker", "info", "--format", "{{json .ServerVersion}}"]
        assert options["timeout"] == 10
        assert "shell" not in options
        return subprocess.CompletedProcess(command, 0, '"1.0"', "")

    monkeypatch.setattr(doctor.subprocess, "run", run)
    assert doctor.check_docker()[0].passed


@pytest.mark.parametrize(
    "failure", [OSError("private info"), subprocess.TimeoutExpired("docker", 10)]
)
def test_docker_failure_is_safe(monkeypatch, failure):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/docker")

    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(doctor.subprocess, "run", run)
    check = doctor.check_docker()[0]
    assert not check.passed
    assert "private info" not in check.message


def test_doctor_json_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "check_docker", lambda: [doctor.Check("Docker", False, "Unavailable")]
    )
    assert cli.main(["doctor", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)[0]["passed"] is False


def test_validate_missing_file(capsys):
    assert cli.main(["validate", "/does-not-exist/agent.yaml"]) == 2
    assert "Validation failed" in capsys.readouterr().err


def test_deploy_requires_explicit_local_authorization():
    with pytest.raises(SystemExit) as error:
        cli.main(["deploy", "agent.yaml"])
    assert error.value.code == 2


def test_local_cli_status_persists_and_remove_requires_consent(tmp_path, capsys):
    from agentbreeder.store import Store

    path = tmp_path / "state"
    store = Store(path)
    try:
        store.create("abc", {"name": "sample"}, "digest", "local-context")
    finally:
        store.close()
    assert cli.main(["status", "abc", "--state-dir", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "pending"
    with pytest.raises(SystemExit):
        cli.main(["remove", "abc", "--state-dir", str(path)])


def test_deploy_cli_failed_runtime_exit(monkeypatch, tmp_path, capsys):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text("schema_version: 1\nname: test\nimage: sha256:" + "a" * 64 + "\n")
    monkeypatch.setattr(
        cli.DeploymentService,
        "deploy",
        lambda *a, **kw: {
            "id": "abc",
            "state": "failed",
            "endpoint": None,
            "error": "Health check failed",
        },
    )
    assert (
        cli.main(
            ["deploy", str(manifest), "--local", "--state-dir", str(tmp_path / "state"), "--json"]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["endpoint"] is None
