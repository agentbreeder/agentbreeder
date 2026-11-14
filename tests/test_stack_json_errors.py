"""Coded errors for `--stack DIR` resolution (every stateful command) and for `services
up|plan|status|down --json`: `[CODE] message` on stderr and, with `--json`, the error object
`{"schema_version": 1, "error": {"code", "message"}}` on stdout. Docker is faked."""

# Fixtures imported from the services tests are redefined as parameters on purpose.
# ruff: noqa: F811

from __future__ import annotations

import json

import pytest
from test_services_control import ControlDocker, docker, root, stack, stack_id  # noqa: F401
from test_services_engine import env, fake_docker  # noqa: F401

from agentbreeder import cli
from agentbreeder.services import engine as engine_module
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import services_lock


def error_object(out: str) -> dict:
    body = json.loads(out)
    assert set(body) == {"schema_version", "error"} and body["schema_version"] == 1
    assert set(body["error"]) == {"code", "message"}
    return body["error"]


# -- `--stack DIR` (cli._use_stack) --------------------------------------------------------------


STATEFUL = [
    ["status", "abc"],
    ["list"],
    ["remove", "abc", "--local"],
    ["logs", "abc"],
    ["traces"],
    ["cost"],
    ["migrate"],
]


@pytest.mark.parametrize("argv", STATEFUL)
def test_stack_not_up_is_coded_with_the_error_object(stack, capsys, argv):
    code = cli.main([*argv, "--stack", str(stack), "--environment", "prod", "--json"])
    assert code == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[STACK_NOT_UP] stack ")
    error = error_object(captured.out)
    assert error["code"] == "STACK_NOT_UP"
    assert not error["message"].startswith("[")
    assert "services up" in error["message"]


@pytest.mark.parametrize("argv", STATEFUL)
def test_without_json_only_the_coded_line(stack, capsys, argv):
    assert cli.main([*argv, "--stack", str(stack), "--environment", "prod"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("[STACK_NOT_UP] ")


def test_follow_prints_the_error_object_on_one_line(stack, capsys):
    argv = ["logs", "abc", "--follow", "--stack", str(stack), "--environment", "prod", "--json"]
    assert cli.main(argv) == 2
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert error_object(out)["code"] == "STACK_NOT_UP"


def test_missing_stack_file(tmp_path, capsys):
    assert cli.main(["status", "abc", "--stack", str(tmp_path), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[STACK_MISSING] no stack.yaml in ")
    assert error_object(captured.out)["code"] == "STACK_MISSING"


def test_an_unreadable_stack_is_coded(tmp_path, capsys, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(engine_module, "context", denied)
    assert cli.main(["list", "--stack", str(tmp_path), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err == f"[STACK_UNREADABLE] cannot read the stack at {tmp_path}: " + (
        "Permission denied\n"
    )
    assert error_object(captured.out) == {
        "code": "STACK_UNREADABLE",
        "message": f"cannot read the stack at {tmp_path}: Permission denied",
    }


def test_an_oserror_without_strerror_names_its_type(tmp_path, capsys, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError()

    monkeypatch.setattr(engine_module, "context", broken)
    assert cli.main(["list", "--stack", str(tmp_path)]) == 2
    assert capsys.readouterr().err.endswith(": OSError\n")


def test_control_characters_are_escaped(tmp_path, capsys, monkeypatch):
    def hostile(*args, **kwargs):
        raise ServiceError("STACK_INVALID", "bad name \x1b[31mred\x07")

    monkeypatch.setattr(engine_module, "context", hostile)
    assert cli.main(["status", "abc", "--stack", str(tmp_path), "--json"]) == 2
    captured = capsys.readouterr()
    assert "\x1b" not in captured.err and "\x07" not in captured.err
    message = error_object(captured.out)["message"]
    assert "\x1b" not in message and "\x07" not in message
    assert message.startswith("bad name ")


def test_a_multi_line_message_stays_one_stderr_line(tmp_path, capsys, monkeypatch):
    def forged(*args, **kwargs):
        raise ServiceError("STACK_INVALID", "bad name\n[SERVICES_BUSY] forged")

    monkeypatch.setattr(engine_module, "context", forged)
    assert cli.main(["status", "abc", "--stack", str(tmp_path), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err == "[STACK_INVALID] bad name\\n[SERVICES_BUSY] forged\n"
    assert error_object(captured.out)["message"] == "bad name\n[SERVICES_BUSY] forged"


def test_a_resolved_stack_still_runs_the_command(stack, capsys, monkeypatch):
    monkeypatch.setattr("agentbreeder.commands.status.run", lambda args: 0)
    assert cli.main(["status", "abc", "--stack", str(stack), "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Using stack " in captured.err


# -- `services up|plan|status|down --json` -------------------------------------------------------


WRITES = [
    ["up", "{dir}", "--environment", "dev", "--yes"],
    ["plan", "{dir}", "--environment", "dev"],
    ["down", "{dir}"],
    ["down", "{dir}", "--purge", "--yes"],
]


def _argv(argv, directory):
    return ["services", *[str(directory) if a == "{dir}" else a for a in argv]]


@pytest.mark.parametrize("argv", WRITES)
def test_busy_under_json_is_the_error_object(stack, docker, capsys, argv):
    with services_lock(root(), stack_id(stack)):
        assert cli.main([*_argv(argv, stack), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[SERVICES_BUSY] another services operation is running")
    error = error_object(captured.out)
    assert error["code"] == "SERVICES_BUSY"
    assert error["message"].startswith("another services operation is running")
    assert docker.events == []


@pytest.mark.parametrize("argv", WRITES)
def test_busy_without_json_prints_no_object(stack, docker, capsys, argv):
    with services_lock(root(), stack_id(stack)):
        assert cli.main(_argv(argv, stack)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("[SERVICES_BUSY] ")


@pytest.mark.parametrize("command", ["up", "plan", "status", "down"])
def test_a_missing_stack_is_the_error_object(tmp_path, fake_docker, capsys, command):
    extra = ["--environment", "dev"] if command in ("up", "plan") else []
    assert cli.main(["services", command, str(tmp_path), *extra, "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[STACK_MISSING] ")
    assert error_object(captured.out)["code"] == "STACK_MISSING"


@pytest.mark.parametrize("flag", ["--purge", "--volumes"])
def test_confirm_required_under_json(tmp_path, capsys, flag):
    assert cli.main(["services", "down", str(tmp_path), flag, "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err == f"[CONFIRM_REQUIRED] down {flag} deletes data; add --yes\n"
    assert error_object(captured.out) == {
        "code": "CONFIRM_REQUIRED",
        "message": f"down {flag} deletes data; add --yes",
    }


def test_confirm_required_without_json_is_unchanged(tmp_path, capsys):
    assert cli.main(["services", "down", str(tmp_path), "--purge"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "[CONFIRM_REQUIRED] down --purge deletes data; add --yes\n"


def test_state_errors_under_json(stack, capsys, monkeypatch):
    def denied(*args, **kwargs):
        raise OSError("denied")

    monkeypatch.setattr(engine_module.Engine, "status", denied)
    assert cli.main(["services", "status", str(stack), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[STATE_UNREADABLE] cannot access the stack state")
    assert error_object(captured.out)["code"] == "STATE_UNREADABLE"
    assert cli.main(["services", "status", str(stack)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Cannot access the stack state" in captured.err


def test_an_uncoded_prefix_error_gets_its_code(stack, capsys, monkeypatch):
    """SecretError and DeploymentError carry a code but no `[CODE] ` prefix in str()."""
    from agentbreeder.store import DeploymentError

    def refused(*args, **kwargs):
        raise DeploymentError("state needs attention")

    monkeypatch.setattr(engine_module.Engine, "status", refused)
    assert cli.main(["services", "status", str(stack), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err == "[DEPLOYMENT_ERROR] state needs attention\n"
    assert error_object(captured.out) == {
        "code": "DEPLOYMENT_ERROR",
        "message": "state needs attention",
    }
