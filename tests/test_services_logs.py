"""`services logs [DIR] NAME [--tail N] [--since D] [--follow] [--json] [--raw]` (#821, M5):
the same shape, redaction and escaping as `logs ID`."""

# Fixtures imported from test_services_engine are redefined as parameters on purpose.
# ruff: noqa: F811

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from test_services_engine import CANARY, FakeDocker, env, fake_docker  # noqa: F401

from agentbreeder import cli, redaction
from agentbreeder.commands import logs as logs_command


@pytest.fixture
def stack(env, fake_docker, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["services", "up", str(env), "--environment", "dev", "--yes"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(logs_command, "_stdin_descriptor", lambda: None)
    return env


def logs(stack, *argv):
    return cli.main(["services", "logs", str(stack), "echo", *argv])


def test_lines_are_redacted_and_escaped(stack, fake_docker, capsys):  # noqa: F811
    fake_docker.secret_seen = f"{CANARY} \x1b]52;c;eA==\x07"
    assert logs(stack) == 0
    out = capsys.readouterr().out
    assert CANARY not in out and redaction.MARK in out
    assert "\x1b" not in out and "\\x1b]52" in out


def test_raw_keeps_controls_and_still_redacts(stack, fake_docker, capsys):  # noqa: F811
    fake_docker.secret_seen = f"{CANARY} \x1b[2J"
    assert logs(stack, "--raw") == 0
    out = capsys.readouterr().out
    assert "\x1b[2J" in out and CANARY not in out


def test_json_shape_matches_logs_id(stack, fake_docker, capsys, monkeypatch):  # noqa: F811
    seen = []

    def read(state, service, tail, since=None):
        seen.append((service, tail, since))
        return f"2026-11-12T10:00:00Z a {CANARY}\n2026-11-12T10:00:01Z b\n", True

    monkeypatch.setattr(fake_docker, "read_logs", read)
    assert logs(stack, "--json", "--tail", "50", "--since", "2h") == 0
    body = json.loads(capsys.readouterr().out)
    assert body == {
        "schema_version": 1,
        "source": "service",
        "target": "local",
        "name": "echo",
        "lines": [f"2026-11-12T10:00:00Z a {redaction.MARK}", "2026-11-12T10:00:01Z b"],
        "truncated": True,
    }
    assert seen == [("echo", 50, "2h")]


def test_truncated_note(stack, fake_docker, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fake_docker, "read_logs", lambda *a, **k: ("x\n", True))
    assert logs(stack) == 0
    assert "older lines were dropped" in capsys.readouterr().err


@pytest.mark.parametrize("tail", ["0", "10001", "x"])
def test_tail_is_bounded(stack, capsys, tail):
    with pytest.raises(SystemExit):
        logs(stack, "--tail", tail)


def test_errors_are_coded_with_a_json_object(stack, capsys):
    assert cli.main(["services", "logs", str(stack), "nope", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[SERVICE_UNKNOWN]")
    assert json.loads(captured.out)["error"]["code"] == "SERVICE_UNKNOWN"
    assert logs(stack, "--since", "yesterday", "--json") == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "SINCE_INVALID"


def test_redaction_incomplete_prints_nothing(stack, fake_docker, capsys, monkeypatch):  # noqa: F811
    real = redaction.collect

    def partial(*a, **k):
        found = real(*a, **k)
        found.problems.append("cannot read the stack's stored secret litellm/master-key")
        return found

    monkeypatch.setattr(redaction, "collect", partial)
    fake_docker.secret_seen = CANARY
    assert logs(stack) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[REDACTION_INCOMPLETE]") and captured.out == ""


def test_a_recorded_secret_whose_file_is_gone_refuses(stack, fake_docker, capsys):  # noqa: F811
    from agentbreeder.services.engine import Engine

    engine = Engine(stack, docker=fake_docker)
    try:
        service, name = engine.state.db.execute("SELECT service, name FROM secrets").fetchone()
        engine.state._secret_path(service, name).unlink()
    finally:
        engine.close()
    assert logs(stack) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[REDACTION_INCOMPLETE]") and "is missing" in captured.err
    assert captured.out == ""


# -- follow ------------------------------------------------------------------------------------


def _child(fake_docker, monkeypatch, script):  # noqa: F811
    started = []

    def follow(state, service, tail, since=None):
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        started.append((service, tail, since, process))
        return process

    monkeypatch.setattr(fake_docker, "follow_logs", follow, raising=False)
    return started


def test_follow_streams_ndjson_and_ends_with_the_end_record(
    stack, fake_docker, capsys, monkeypatch
):  # noqa: F811
    started = _child(
        fake_docker, monkeypatch, f"print('2026-11-12T10:00:00Z up {CANARY}', flush=True)"
    )
    assert logs(stack, "--follow", "--json", "--tail", "5", "--since", "10m") == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0] == {
        "schema_version": 1,
        "source": "service",
        "target": "local",
        "name": "echo",
        "line": f"2026-11-12T10:00:00Z up {redaction.MARK}",
    }
    assert lines[-1] == {
        "schema_version": 1,
        "event": "end",
        "reason": "container",
        "dropped": False,
    }
    service, tail, since, process = started[0]
    assert (service, tail, since) == ("echo", 5, "10m") and process.poll() is not None


def test_follow_text_escapes(stack, fake_docker, capsys, monkeypatch):  # noqa: F811
    _child(fake_docker, monkeypatch, "print('x \\x1b[2J y', flush=True)")
    assert logs(stack, "--follow") == 0
    captured = capsys.readouterr()
    assert captured.out == "x \\x1b[2J y\n" and "Follow ended" in captured.err


def test_a_failing_follow_is_coded(stack, fake_docker, capsys, monkeypatch):  # noqa: F811
    _child(fake_docker, monkeypatch, "raise SystemExit(1)")
    assert logs(stack, "--follow", "--json") == 2
    last = capsys.readouterr().out.splitlines()[-1]
    assert json.loads(last)["error"]["code"] == "DOCKER_LOGS_FAILED"


def test_a_follow_that_cannot_start_is_coded(stack, fake_docker, capsys, monkeypatch):  # noqa: F811
    from agentbreeder.services.schema import ServiceError

    def broken(state, service, tail, since=None):
        raise ServiceError("DOCKER_FAILED", "docker compose logs failed to start")

    monkeypatch.setattr(fake_docker, "follow_logs", broken, raising=False)
    assert logs(stack, "--follow") == 2
    assert capsys.readouterr().err.startswith("[DOCKER_LOGS_FAILED]")


def test_follow_refuses_before_starting_when_redaction_is_incomplete(
    stack, fake_docker, capsys, monkeypatch
):  # noqa: F811
    started = _child(fake_docker, monkeypatch, "print('never')")
    real = redaction.collect

    def partial(*a, **k):
        found = real(*a, **k)
        found.problems.append("cannot read local secrets")
        return found

    monkeypatch.setattr(redaction, "collect", partial)
    assert logs(stack, "--follow") == 2
    assert capsys.readouterr().err.startswith("[REDACTION_INCOMPLETE]") and started == []
