"""Log levels (#821, M5; guided §14.4): `loglevels.py` on real fixtures, and `--level` on
`logs ID` and `services logs`."""

# Fixtures imported from the logs tests are redefined as parameters on purpose.
# ruff: noqa: F811

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_logs_id import (  # noqa: F401
    CHILD,
    IDENTIFIER,
    FakeDocker,
    _pipe_stdin,
    docker,
    run,
    state,
)
from test_services_engine import FakeDocker as ServiceDocker  # noqa: F401
from test_services_engine import env, fake_docker  # noqa: F401

from agentbreeder import cli, loglevels
from agentbreeder.commands import logs as logs_command
from agentbreeder.services.engine import Engine

FIXTURES = Path(__file__).parent / "fixtures" / "logs"
CAPTURED = {"postgres": "postgres", "litellm": "python", "phoenix": "python", "adk": "adk"}


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return logs_command._lines(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["postgres", "litellm", "phoenix"])
def test_captured_fixtures_match_their_expected_levels(name):
    """Golden: one level per line of a real Northwind log (`fixtures/logs/README.md`)."""
    lines = _lines(FIXTURES / f"{name}.txt")
    got = ["-" if level is None else level for level in loglevels.classify(CAPTURED[name], lines)]
    expected_path = FIXTURES / f"{name}.levels"
    if os.environ.get("AGENTBREEDER_UPDATE_GOLDEN"):
        expected_path.write_text("\n".join(got) + "\n", encoding="utf-8")
    assert got == expected_path.read_text(encoding="utf-8").split("\n")[:-1]


def _by_level(name: str) -> dict[str, list[str]]:
    lines = _lines(FIXTURES / f"{name}.txt")
    found: dict[str, list[str]] = {}
    for line, level in zip(lines, loglevels.classify(CAPTURED[name], lines), strict=True):
        found.setdefault(str(level), []).append(line[31:])  # without Docker's timestamp
    return found


def test_postgres_errors_keep_their_statement_and_fatal_is_an_error():
    found = _by_level("postgres")
    errors = found["error"]
    assert any('ERROR:  relation "no_such_table" does not exist' in line for line in errors)
    assert any("STATEMENT:  select no_such_column from no_such_table" in line for line in errors)
    assert any('FATAL:  role "nobody" does not exist' in line for line in errors)
    assert any("database system is ready to accept connections" in line for line in found["info"])
    assert found["warning"][0].startswith("initdb: warning:")
    assert found["warning"][1].startswith("initdb: hint:")  # continues the warning
    # The init script's plain output is not classified.
    assert any("creating subdirectories ... ok" in line for line in found["None"])


def test_litellm_tracebacks_stay_with_their_error_line():
    lines = _lines(FIXTURES / "litellm.txt")
    levels = loglevels.classify("python", lines)
    first = next(i for i, line in enumerate(lines) if "Proxy:ERROR:" in line)
    end = next(i for i in range(first + 1, len(lines)) if "Proxy:" in lines[i])
    assert levels[first:end] == ["error"] * (end - first)
    assert "Traceback (most recent call last):" in lines[first + 1]
    assert "HTTPException: 404" in lines[end - 1]
    # The ASCII banner before the first record is not classified.
    assert all(level is None for level in levels[1:9])


def test_phoenix_sql_continuations_warnings_and_the_banner():
    found = _by_level("phoenix")
    assert "FROM alembic_version" in found["info"]  # a multi-line SQL statement's second line
    assert any("PydanticJsonSchemaWarning" in line for line in found["warning"])
    assert any(line.startswith("<sys>:0: SAWarning") for line in found["warning"])
    assert any("Uvicorn running on" in line for line in found["info"])
    # After the warning, a blank line, then the banner: the banner is not part of the warning.
    assert not any("Arize Phoenix" in line for line in found["warning"])
    assert any("Arize Phoenix" in line for line in found["None"])


def test_adk_runtime_messages_and_python_formats():
    lines = [
        "2026-11-12T10:00:00Z runtime warm-up failed: ConnectError",
        "2026-11-12T10:00:01Z turn record failed: OperationalError",
        "2026-11-12T10:00:02Z hello from a tool",
        "",
        "2026-11-12T10:00:03Z closing MCP sessions failed: TimeoutError",
        "2026-11-12T10:00:04Z /app/x.py:3: DeprecationWarning: old",
        "2026-11-12T10:00:05Z   warnings.warn(message)",
    ]
    assert loglevels.classify("adk", lines) == [
        "error", "error", "error", "error", "error", "warning", "warning",
    ]  # fmt: skip
    # The same messages under the plain Python format are not classified.
    assert loglevels.classify("python", lines[:2]) == [None, None]
    assert _lines(FIXTURES / "adk.txt") == []  # nothing printed in the captured runs


def test_python_headers_chained_tracebacks_and_blank_lines():
    lines = [
        "ERROR:root:boom",
        "Traceback (most recent call last):",
        '  File "x.py", line 1, in <module>',
        "ValueError: one",
        "",
        "During handling of the above exception, another exception occurred:",
        "",
        "Traceback (most recent call last):",
        "KeyError: two",
        "",
        "a plain line after a blank",
        "2026-11-12 10:00:00,123 - WARN something",
        "12:00:00 - LiteLLM Router:DEBUG: router.py:1 - picked",
        "CRITICAL:     the end",
    ]
    assert loglevels.classify("python", lines) == [
        "error", "error", "error", "error", "error", "error", "error", "error", "error",
        "error", None, "warning", "debug", "error",
    ]  # fmt: skip


def test_postgres_words_continuations_and_tools():
    stamp = "2026-11-12 10:00:00.000 UTC [7] "
    lines = [
        "DETAIL: before any record",
        stamp + "DEBUG2:  x",
        stamp + "NOTICE:  y",
        stamp + "WARNING:  z",
        stamp + "HINT:  continues the warning",
        "\tmore of the statement",
        stamp + "PANIC:  w",
        stamp + "SOMETHING:  unknown word",
        "pg_ctl: error: could not start",
        "pg_ctl: detail: continues",
        "",
        "plain after a blank",
        stamp + "LOG:  statement: select '",
        "\tforged " + stamp + "ERROR:  inside a statement's text",
        "waiting for server to start...." + stamp + "LOG:  starting",
        "x " + stamp + "ERROR:  not at the start",
    ]
    assert loglevels.classify("postgres", lines) == [
        None, "debug", "info", "warning", "warning", "warning", "error", "info", "error",
        "error", "error", None, "info", "info", "info", "info",
    ]  # fmt: skip


def test_shown_and_unknown_formats():
    assert loglevels.shown(None, None) and loglevels.shown("debug", None)
    assert loglevels.shown("warning", "warning") and loglevels.shown("error", "warning")
    assert not loglevels.shown("info", "warning") and not loglevels.shown(None, "warning")
    assert not loglevels.shown("warning", "error")
    assert loglevels.classify(None, ["ERROR: x"]) == [None]
    with pytest.raises(ValueError):
        loglevels.Classifier("go")


def test_format_for_needs_a_builtin_known_ref():
    assert loglevels.format_for("service/postgres-pgvector@1.1.0", True) == "postgres"
    assert loglevels.format_for("service/litellm@1.0.0", True) == "python"
    assert loglevels.format_for("runtime/adk@1.0.6", True) == "adk"
    assert loglevels.format_for("service/litellm@1.0.0", False) is None  # a project's own
    assert loglevels.format_for("service/northwind-ops@1.0.0", True) is None
    assert loglevels.format_for("not a ref", True) is None
    assert loglevels.format_for(None, True) is None


# -- services logs -----------------------------------------------------------------------------


def _engine(tmp_path, services: dict, lock: object) -> SimpleNamespace:
    (tmp_path / "stack.lock").write_text(json.dumps(lock) if not isinstance(lock, str) else lock)
    stack = SimpleNamespace(services={k: SimpleNamespace(ref=v) for k, v in services.items()})
    return SimpleNamespace(stack=stack, project=tmp_path)


def test_service_format_comes_from_a_builtin_lock_entry(tmp_path):
    services = {"postgres": "service/postgres-pgvector@1.1.0", "ops": "service/litellm@9.0.0"}
    lock = {
        "lock_version": 1,
        "entries": {
            "service/postgres-pgvector@1.1.0": {
                "sha256": "sha256:" + "a" * 64,
                "source": "builtin",
            },
            "service/litellm@9.0.0": {"sha256": "sha256:" + "b" * 64, "source": "registry"},
        },
    }
    engine = _engine(tmp_path, services, lock)
    assert Engine.log_format(engine, "postgres") == "postgres"
    assert Engine.log_format(engine, "ops") is None  # a project version named like a built-in
    assert Engine.log_format(engine, "relay-x") is None  # not a stack service
    assert Engine.log_format(_engine(tmp_path, services, "{broken"), "postgres") is None
    assert Engine.log_format(_engine(tmp_path, services, "[" * 100000), "postgres") is None
    assert Engine.log_format(_engine(tmp_path, services, '{"entries": []}'), "postgres") is None
    (tmp_path / "stack.lock").unlink()
    os.mkfifo(tmp_path / "stack.lock")  # never blocks on a FIFO
    assert Engine.log_format(engine, "postgres") is None


@pytest.fixture
def stack(env, fake_docker, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["services", "up", str(env), "--environment", "dev", "--yes"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(logs_command, "_stdin_descriptor", lambda: None)
    return env


PG = (
    "2026-11-12T10:00:00Z 2026-11-12 10:00:00.000 UTC [1] LOG:  ready\n"
    '2026-11-12T10:00:01Z 2026-11-12 10:00:01.000 UTC [9] ERROR:  relation "x" does not exist\n'
    "2026-11-12T10:00:01Z 2026-11-12 10:00:01.000 UTC [9] STATEMENT:  select * from x\n"
    "2026-11-12T10:00:02Z 2026-11-12 10:00:02.000 UTC [1] LOG:  checkpoint\n"
)


def test_services_logs_level_filters_a_builtin_service(stack, fake_docker, capsys, monkeypatch):
    monkeypatch.setattr(fake_docker, "read_logs", lambda *a, **k: (PG, False))
    monkeypatch.setattr(Engine, "log_format", lambda self, name: "postgres")
    assert cli.main(["services", "logs", str(stack), "echo", "--level", "error"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and "ERROR:" in out[0] and "STATEMENT:" in out[1]
    assert cli.main(["services", "logs", str(stack), "echo", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["format"] == "postgres" and body["level"] is None
    assert body["levels"] == ["info", "error", "error", "info"]


def test_services_logs_level_on_an_unclassified_service(stack, fake_docker, capsys, monkeypatch):
    read = []
    monkeypatch.setattr(fake_docker, "read_logs", lambda *a, **k: read.append(a) or (PG, False))
    assert cli.main(["services", "logs", str(stack), "echo", "--level", "warning", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[LEVELS_UNAVAILABLE]")
    assert json.loads(captured.out)["error"]["code"] == "LEVELS_UNAVAILABLE"
    assert read == []  # refused before reading
    assert cli.main(["services", "logs", str(stack), "nope", "--level", "error"]) == 2
    assert capsys.readouterr().err.startswith("[SERVICE_UNKNOWN]")


def test_services_logs_follow_level(stack, fake_docker, capsys, monkeypatch):
    import subprocess
    import sys

    script = "print(" + repr(PG) + ", end='', flush=True)"

    def follow(state, service, tail, since=None):
        return subprocess.Popen(
            [sys.executable, "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE
        )

    monkeypatch.setattr(fake_docker, "follow_logs", follow, raising=False)
    monkeypatch.setattr(Engine, "log_format", lambda self, name: "postgres")
    argv = ["services", "logs", str(stack), "echo", "--follow", "--json", "--level", "error"]
    assert cli.main(argv) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [entry.get("level") for entry in lines[:-1]] == ["error", "error"]
    assert lines[-1]["event"] == "end"
    monkeypatch.setattr(Engine, "log_format", lambda self, name: None)
    assert cli.main(argv) == 2
    assert capsys.readouterr().err.startswith("[LEVELS_UNAVAILABLE]")


# -- logs ID -----------------------------------------------------------------------------------


def _build_record(
    project: Path, agent="hello", runtime="runtime/adk@1.0.6", name=None, source="builtin"
):
    """As `build` writes it (`build.py`): the runtime is also one of the locked refs."""
    (project / "deploy").mkdir(parents=True, exist_ok=True)
    record = {
        "name": name or agent,
        "runtime": runtime,
        "image": "sha256:" + "d" * 64,
        "refs": [{"ref": runtime, "sha256": "sha256:" + "e" * 64, "source": source}],
    }
    (project / "deploy" / f"{agent}.build.json").write_text(json.dumps(record))


def test_agent_format_reads_the_build_record(tmp_path):
    assert logs_command.agent_format(None, "hello") is None
    assert logs_command.agent_format(tmp_path, "hello") is None  # no record
    _build_record(tmp_path)
    assert logs_command.agent_format(tmp_path, "hello") == "adk"
    assert logs_command.agent_format(tmp_path, "hello-candidate") == "adk"
    assert logs_command.agent_format(tmp_path, "Bad Name") is None
    _build_record(tmp_path, name="other")  # a record for another agent
    assert logs_command.agent_format(tmp_path, "hello") is None
    _build_record(tmp_path, runtime="runtime/other@1.0.0")
    assert logs_command.agent_format(tmp_path, "hello") is None
    # A project's own `runtime/adk` (its registry shadows the built-in one) is not classified.
    _build_record(tmp_path, source="registry")
    assert logs_command.agent_format(tmp_path, "hello") is None
    (tmp_path / "deploy" / "hello.build.json").write_text(
        json.dumps({"name": "hello", "runtime": "runtime/adk@1.0.6", "refs": "nope"})
    )
    assert logs_command.agent_format(tmp_path, "hello") is None
    (tmp_path / "deploy" / "hello.build.json").unlink()
    (tmp_path / "deploy" / "hello.build.json").symlink_to(tmp_path / "elsewhere.json")
    _build_record(tmp_path / "x")
    (tmp_path / "elsewhere.json").write_text((tmp_path / "x/deploy/hello.build.json").read_text())
    assert logs_command.agent_format(tmp_path, "hello") is None  # never through a symlink


AGENT_LOG = (
    "2026-11-12T10:00:00Z hello from a tool\n"
    "2026-11-12T10:00:01Z turn record failed: OperationalError\n"
    "2026-11-12T10:00:02Z   detail of the failure\n"
)


def test_logs_id_level(state, docker, capsys, monkeypatch):
    docker.install(FakeDocker(text=AGENT_LOG))
    assert run(state, IDENTIFIER, "--level", "error") == 2
    assert capsys.readouterr().err.startswith("[LEVELS_UNAVAILABLE]")
    monkeypatch.setattr(logs_command, "agent_format", lambda stack, name: "adk")
    assert run(state, IDENTIFIER, "--level", "error") == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and "turn record failed" in out[0]
    assert run(state, IDENTIFIER, "--json") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["format"] == "adk" and body["levels"] == [None, "error", "error"]
    assert body["level"] is None and len(body["lines"]) == 3


def test_logs_id_follow_level(state, docker, capsys, monkeypatch):
    docker.install(FakeDocker(child=CHILD))
    monkeypatch.setattr(logs_command, "agent_format", lambda stack, name: "adk")
    timer = _pipe_stdin(monkeypatch, close_after=1.0)
    try:
        assert run(state, IDENTIFIER, "--follow", "--json") == 0
    finally:
        timer.cancel()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all("level" in entry for entry in lines[:-1]) and lines[-1]["event"] == "end"


def test_level_is_refused_with_failed(state, capsys):
    assert run(state, "--failed", "hello", "--level", "error") == 2
    assert "--level go with a deployment ID" in capsys.readouterr().err
