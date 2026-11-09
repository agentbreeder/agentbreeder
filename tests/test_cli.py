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
        assert command == ["/usr/bin/docker", "version", "--format", "{{.Server.Version}}"]
        assert options["timeout"] == 10
        assert "shell" not in options
        return subprocess.CompletedProcess(command, 0, "1.0\n", "")

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


def test_docker_unreachable_engine_with_zero_exit_is_not_a_pass(monkeypatch):
    # Regression: `docker info --format` exits 0 with empty output when the engine is down.
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda command, **options: subprocess.CompletedProcess(command, 0, "\n", "error"),
    )
    assert doctor.check_docker()[0].passed is False


@pytest.mark.parametrize(
    "which,result,passed,text",
    [
        (None, None, False, "Install Docker Compose"),
        ("/usr/bin/docker", OSError("x"), False, "timed out"),
        ("/usr/bin/docker", (1, ""), False, "not installed"),
        ("/usr/bin/docker", (0, "Usage: up [--detach]"), False, "Upgrade"),
        ("/usr/bin/docker", (0, "  --wait   Wait for services"), True, "available"),
    ],
)
def test_doctor_compose_check(monkeypatch, which, result, passed, text):
    from types import SimpleNamespace

    monkeypatch.setattr(doctor.shutil, "which", lambda _: which)

    def run(*args, **kwargs):
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(returncode=result[0], stdout=result[1])

    monkeypatch.setattr(doctor.subprocess, "run", run)
    check = doctor.check_compose()[0]
    assert check.passed is passed and text in check.message and check.required is False
