"""Shared redactor, Docker.logs, the failed-candidate tail and `logs --failed` (#819; S-08, S-09).

Test values are built at run time (random tokens, a PEM-shaped block from parts) so no
credential-shaped literal sits in the source.
"""

from __future__ import annotations

import ast
import base64
import io
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from test_release import (  # noqa: F401 - fixtures
    Evaluator,
    FakeRuntime,
    do_release,
    operator,
    prepare_lock,
    project,
)

from agentbreeder import cli, redaction, runtime, secret_files
from agentbreeder.deployment import DeploymentService
from agentbreeder.release import EXIT_EVAL, EXIT_OK, candidate_tail_path
from agentbreeder.store import DeploymentError, Store

SRC = Path(__file__).resolve().parents[1] / "src"
VALUE = secrets.token_hex(8) + "/with+chars=&"
BODY = [secrets.token_urlsafe(24), secrets.token_urlsafe(24), "Ab3"]
MARKER = "-" * 5
BLOCK = "\n".join([f"{MARKER}BEGIN TEST BLOCK{MARKER}", *BODY, f"{MARKER}END TEST BLOCK{MARKER}"])


# -- redact ------------------------------------------------------------------------------------


def forms(value: str) -> list[str]:
    return [
        value,
        base64.b64encode(value.encode()).decode(),
        base64.b64encode(value.encode()).decode().rstrip("="),
        base64.urlsafe_b64encode(value.encode()).decode(),
        quote(value, safe=""),
        json.dumps(value)[1:-1],
    ]


@pytest.mark.parametrize("index", range(6))
def test_redact_every_encoding(index):
    form = forms(VALUE)[index]
    assert redaction.redact(f"before {form} after", [VALUE.encode()]) == "before [redacted] after"


def test_redact_json_escaped_value():
    value = 'q"uo\\te\n' + secrets.token_hex(6)
    text = json.dumps({"field": value})
    out = redaction.redact(text, [value])
    assert json.dumps(value)[1:-1] not in out and "[redacted]" in out


def test_redact_multiline_values_line_by_line():
    log = f"loaded {BODY[0]} from file\nshort {BODY[2]} stays"
    out = redaction.redact(log, [BLOCK])
    assert BODY[0] not in out
    assert f"{BODY[2]} stays" in out  # lines under 8 characters are not redacted on their own


def test_redact_url_passwords_and_short_values():
    hidden = secrets.token_hex(5)
    text = f"postgresql://agent:{hidden}@db:5432/x and https://user:p%40{hidden}@host/ and abc"
    out = redaction.redact(text, [b"abc"])
    assert hidden not in out
    assert "postgresql://agent:[redacted]@db" in out
    assert out.endswith("and abc")  # values under 4 characters are never replaced


def test_a_query_at_sign_is_not_userinfo():
    for text in (
        "http://svc:4000/v1?user=a@b.com",
        "http://[::1]:8080/x@y",  # IPv6 literal host: not mangled
        "https://host/x#frag@y",
        "GET http://svc:4000/v1?email=me@example.com HTTP/1.1",
    ):
        assert redaction.redact(text, []) == text


@pytest.mark.parametrize(
    "template",
    [
        "redis://:{h}@cache:6379",  # empty user
        "postgres://u:pa/{h}@host/db",  # `/` in the password
        "postgres://u:p@{h}@host/db",  # `@` in the password: no tail may leak
        '{{"url": "postgres://u:{h}@host/db", "other": "x@y"}}',
    ],
)
def test_url_password_shapes(template):
    hidden = secrets.token_hex(6)
    out = redaction.redact(template.format(h=hidden), [])
    assert hidden not in out and "[redacted]" in out
    assert "other" not in template or '"other": "x@y"' in out  # stops at the quote


def test_redaction_is_linear_on_a_huge_single_token():
    """Review r1: the URL-password pattern was quadratic (a 4 MiB token took hours)."""
    blob = "ab12cd34" * (4 * 1024 * 1024 // 8)
    values = [secrets.token_hex(16).encode() for _ in range(5)]
    started = time.monotonic()
    assert redaction.redact(blob, values) == blob
    worst = ("x://" + "y" * 50) * 50_000 + "@"  # many `://`, one `@` far away
    redaction.redact(worst, values)
    assert time.monotonic() - started < 2


def test_lowercase_percent_trailing_newline_base64_and_overlaps():
    value = "s3cr/t+" + secrets.token_hex(6)
    lower = quote(value, safe="").replace("%2F", "%2f").replace("%2B", "%2b")
    assert redaction.redact(lower, [value]) == "[redacted]"
    with_newline = base64.b64encode(value.encode() + b"\n").decode()
    assert redaction.redact(with_newline, [value]) == "[redacted]"
    # Overlapping values: one span, no suffix left behind.
    first = "abcdef" + secrets.token_hex(4)
    second = first[3:] + "XYZW1234"
    assert redaction.redact(f"[{first}XYZW1234]", [first, second]) == "[[redacted]]"


@pytest.mark.parametrize("prefix", [b"", b"u", b"us", b"user:"])
def test_base64_inside_a_larger_string(prefix):
    value = secrets.token_hex(12)
    header = "Basic " + base64.b64encode(prefix + value.encode() + b"!").decode()
    out = redaction.redact(header, [value])
    assert "[redacted]" in out
    # No 8-character run of the value's own base64 survives at any alignment.
    for offset in range(3):
        encoded = base64.b64encode(b"\0" * offset + value.encode()).decode()
        core = encoded[(8 * offset + 5) // 6 : (8 * (offset + len(value))) // 6]
        assert all(core[i : i + 8] not in out for i in range(len(core) - 8))


# -- collecting values -------------------------------------------------------------------------


def private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def test_read_copy_checks(tmp_path):
    directory = private_dir(tmp_path / "copies")
    copy = directory / "db"
    copy.write_bytes(b"value-1234")
    copy.chmod(0o444)
    assert redaction.read_copy(copy) == b"value-1234"
    link = directory / "link"
    link.symlink_to(copy)
    assert redaction.read_copy(link) is None
    copy.chmod(0o666)
    assert redaction.read_copy(copy) is None  # group/world writable
    copy.chmod(0o444)
    directory.chmod(0o755)
    assert redaction.read_copy(copy) is None  # parent must be 0700
    directory.chmod(0o700)


def test_redaction_values_cover_stored_values_and_live_copies(tmp_path):
    state = private_dir(tmp_path / "state")
    secret_files.set_secret(state, "db", b"stored-value-1")
    store = Store(state)
    try:
        live = "a" * 32
        store.create(live, {"name": "x", "image": "i"}, "d", "ctx")
        removed = "b" * 32
        store.create(removed, {"name": "y", "image": "i"}, "d", "ctx")
        store.transition(removed, "removed")
    finally:
        store.close()
    secret_files.write_generated(state, live, "gateway-key", "KEY", b"live-copy-value")
    secret_files.write_generated(state, removed, "gateway-key", "KEY", b"removed-copy-value")
    values = redaction.redaction_values(state)
    assert b"stored-value-1" in values and b"live-copy-value" in values
    assert b"removed-copy-value" not in values


def test_redaction_values_cover_every_environment_of_a_stack(tmp_path):
    from agentbreeder.services.state import StackState

    stack = StackState("demo", tmp_path, tmp_path / "stacks")
    stack.write_secret("postgres", "pw", b"pg-value-12345", "generate")
    for env in ("dev", "prod"):
        directory = stack.env_dir(env)
        directory.mkdir(parents=True, mode=0o700)
        (directory / "operator.yaml").write_text("{}")
        secret_files.set_secret(stack.deploy_state(env), "x", f"{env}-value-12345".encode())
    stack.close()
    values = redaction.redaction_values(stack.deploy_state("dev"))
    assert {b"pg-value-12345", b"dev-value-12345", b"prod-value-12345"} <= set(values)


# -- Docker.logs -------------------------------------------------------------------------------


class FakePopen:
    calls: list = []
    output = b""
    exit_code = 0

    def __init__(self, argv, **kwargs):
        FakePopen.calls.append((argv, kwargs))
        self.stdout = io.BytesIO(FakePopen.output)
        self.returncode = FakePopen.exit_code

    def poll(self):
        return 0

    def kill(self):
        pass

    def wait(self):
        return 0


CONTAINER = "0123456789ab" * 4


def test_docker_logs_by_id_merged_and_timestamped(monkeypatch):
    FakePopen.calls, FakePopen.output = [], b"2026-11-11T00:00:00Z hello\n"
    monkeypatch.setattr(runtime.subprocess, "Popen", FakePopen)
    docker = runtime.Docker()
    docker.context = "desktop-linux"
    text, truncated = docker.logs({"Id": CONTAINER}, tail=500, since="10m")
    assert text == "2026-11-11T00:00:00Z hello\n" and not truncated
    argv, kwargs = FakePopen.calls[0]
    assert argv == [
        "docker", "--context", "desktop-linux", "container", "logs", "--timestamps",
        "--tail", "500", "--since", "10m", CONTAINER,
    ]  # fmt: skip
    assert kwargs["stderr"] is subprocess.STDOUT and kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stdin"] is subprocess.DEVNULL and "DOCKER_HOST" not in kwargs["env"]


def test_docker_logs_cap_and_validation(monkeypatch):
    monkeypatch.setattr(runtime, "LOG_LIMIT", 16)
    # The newest bytes are kept, and the line cut at the front is dropped whole.
    FakePopen.output = b"old-1 " + b"x" * 200 + b"\nmid line here\nnewest\n"
    monkeypatch.setattr(runtime.subprocess, "Popen", FakePopen)
    text, truncated = runtime.Docker().logs({"Id": CONTAINER}, tail=5)
    assert text == "newest\n" and truncated
    FakePopen.output, FakePopen.exit_code = b"Error: No such container: abc\n", 1
    with pytest.raises(DeploymentError, match="could not read"):
        runtime.Docker().logs({"Id": CONTAINER}, tail=5)
    FakePopen.exit_code = 0
    docker = runtime.Docker()
    for bad in ("1d; rm", "10", "yesterday", "-1m"):
        with pytest.raises(DeploymentError, match="since"):
            docker.logs({"Id": CONTAINER}, since=bad)
    with pytest.raises(DeploymentError, match="tail"):
        docker.logs({"Id": CONTAINER}, tail=0)
    with pytest.raises(DeploymentError, match="container ID"):
        docker.logs({"Id": "agentbreeder-name"})
    with pytest.raises(DeploymentError, match="container ID"):
        docker.logs({})


# -- failed-candidate tail ---------------------------------------------------------------------


class LoggingRuntime(FakeRuntime):
    def __init__(self, text):
        super().__init__()
        self.text = text
        self.logged = []

    def logs(self, info, tail=500, since=None):
        self.logged.append((info["Id"], tail))
        return self.text, False


def test_failed_release_saves_a_redacted_tail_and_a_pass_discards_it(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)
    secret_files.set_secret(state.directory, "db", VALUE.encode())
    lines = [f"line {i}" for i in range(600)] + [f"using {VALUE}", "\x1b]52;c;ZXZpbA==\x07"]
    logging = LoggingRuntime("\n".join(lines) + "\n")
    outcome, _ = do_release(
        project, operator, state, logging, "sha256:" + "d" * 64, Evaluator(passed=False)
    )
    assert outcome.code == EXIT_EVAL
    path = candidate_tail_path(state.directory, "helper")
    assert outcome.details["candidate_log"] == str(path)
    assert len(logging.logged) == 1 and logging.logged[0][1] == 500  # the candidate, once
    saved = path.read_text().splitlines()
    assert len(saved) == 500 and saved[-2] == "using [redacted]"  # redacted, then truncated
    assert VALUE not in path.read_text()
    assert (path.stat().st_mode & 0o777) == 0o600
    passed, _ = do_release(project, operator, state, logging, "sha256:" + "e" * 64, Evaluator())
    assert passed.code == EXIT_OK and not path.exists()
    assert "candidate_log" not in passed.details


class MintingRuntime(LoggingRuntime):
    """Writes a per-deployment generated secret (as the gateway key mint does) when the
    container is created, and logs it."""

    def __init__(self, state_dir):
        super().__init__("")
        self.state_dir = state_dir
        self.key = "gw-key-" + secrets.token_hex(12)

    def create(self, record, manifest, **kwargs):
        secret_files.write_generated(
            self.state_dir, record["id"], "gateway-key", "K", self.key.encode()
        )
        super().create(record, manifest, **kwargs)
        self.text = f"startup with key {self.key}\nTraceback: boom\n"


def test_a_candidate_that_failed_to_start_keeps_a_redacted_tail(project, operator, state):  # noqa: F811
    """Guided §14.3 #3: a failed start is the tail users want most. Its own secret copies are
    deleted by the failed deploy, so the service keeps what it read just before that cleanup."""
    prepare_lock(project, operator)
    minting = MintingRuntime(state.directory)
    minting.healthy_flag = False
    outcome, _ = do_release(project, operator, state, minting, "sha256:" + "f" * 64)
    assert "Candidate failed" in outcome.message
    assert "candidate_log_skipped" not in outcome.details
    saved = Path(outcome.details["candidate_log"]).read_text()
    assert saved == "startup with key [redacted]\nTraceback: boom\n"
    assert minting.key not in saved
    # The copies really were deleted from disk by the failed start.
    candidate_dirs = list((state.directory / "deployments").glob("*/secrets"))
    assert all(not any(d.iterdir()) for d in candidate_dirs)


def test_an_interrupt_during_capture_still_removes_the_candidate(project, operator, state):  # noqa: F811
    import signal

    prepare_lock(project, operator)

    class Interrupting(LoggingRuntime):
        def logs(self, info, tail=500, since=None):
            signal.raise_signal(signal.SIGINT)  # Ctrl-C while reading the log
            return "x\n", False

    runtime_ = Interrupting("")
    with pytest.raises(KeyboardInterrupt):
        do_release(
            project, operator, state, runtime_, "sha256:" + "c" * 64, Evaluator(passed=False)
        )
    assert state.active("helper-candidate") is None and runtime_.containers == {}


def test_failed_case_reasons_are_redacted_before_they_are_cut():
    from agentbreeder.release import Capture, _redacted_cases

    key = "gw-key-" + secrets.token_hex(16)
    results = [
        {
            "case_id": "c1",
            "passed": False,
            "checks": {"judge_correct": False},
            "explanation": "x" * 190 + key,  # straddles the 200-character cap
        }
    ]
    [case] = _redacted_cases(results, Capture(values=[key.encode()]))
    assert key[:5] not in case["reason"] and "[redacted]" in case["reason"]
    [hidden] = _redacted_cases(results, Capture(values=[], problems=["x"]))
    assert hidden["reason"] == "(not shown: could not be redacted)" and hidden["id"] == "c1"


@pytest.mark.parametrize(
    "template",
    [
        "s://user:pa?{h}@host",
        "s://user:pa#{h}@host",
        "s://user:pa/s?{h}@host",
        "s://user:1234/{h}@host",
        "s://user:99999/{h}@host/db",
        "http://u:{h}@[::1]:8080/",
        "s://[user:{h}@host",
        "s://[user:pa/{h}@host",
        "s://[::1]:{h}@host",
        "s://[:{h}@host",
    ],
)
def test_malformed_passwords_with_delimiters_are_redacted(template):
    hidden = secrets.token_hex(6)
    out = redaction.redact(template.format(h=hidden), [])
    assert hidden not in out and "@" in out and "[redacted]" in out


def test_a_non_ascii_digit_port_is_not_a_port():
    # "٤٠٠٠" is Arabic-Indic 4000: str.isdigit() would call it a port and skip the userinfo.
    text = "http://svc:\u0664\u0660\u0660\u0660/v1?x@host"
    assert redaction.redact(text, []) == "http://svc:[redacted]@host"


def test_capture_failure_withholds_the_judges_reasons(project, operator, state, monkeypatch):  # noqa: F811
    from agentbreeder.release import Capture, _redacted_cases, capture_candidate

    class Service:
        store = state
        runtime = None

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(state, "get", broken)
    capture = capture_candidate(Service(), "a" * 32)
    assert capture.problems and capture.values == [] and capture.text is None
    results = [{"case_id": "c1", "passed": False, "checks": {}, "explanation": "secret-ish"}]
    [case] = _redacted_cases(results, capture)
    assert case["reason"] == "(not shown: could not be redacted)"
    # The values and the raw log never show up in a repr (tracebacks, debug logs).
    shown = repr(Capture(text="raw log", values=[b"value-1"], problems=[]))
    assert "raw log" not in shown and "value-1" not in shown


def test_retained_secrets_are_used_once_and_disk_wins_when_ready(project, operator, state):  # noqa: F811
    from agentbreeder.release import capture_candidate

    prepare_lock(project, operator)
    service = DeploymentService(state, LoggingRuntime("x\n"), operator.load())
    identifier = "b" * 32
    state.create(identifier, {"name": "x", "image": "i"}, "d", "ctx")
    secret_files.write_generated(state.directory, identifier, "k", "K", b"on-disk-value")
    found = redaction.Collected()
    found.values.append(b"retained-value")
    service.retained_secrets[identifier] = found
    state.transition(identifier, "ready")
    capture = capture_candidate(service, identifier)
    assert identifier not in service.retained_secrets  # popped after use
    assert b"on-disk-value" in capture.values and b"retained-value" not in capture.values
    state.transition(identifier, "failed")
    service.retained_secrets[identifier] = found
    capture = capture_candidate(service, identifier)
    assert b"retained-value" in capture.values and identifier not in service.retained_secrets


def test_an_old_schema_elsewhere_names_migrate(tmp_path):
    from test_state_readonly import downgrade_to_v2

    state = private_dir(tmp_path / "state")
    Store(state).close()
    downgrade_to_v2(state)
    found = redaction.collect(state)
    assert not found.complete
    assert f"run `agentbreeder migrate --state-dir {state}`" in found.problems[0]


def test_the_candidate_is_removed_when_capture_breaks(project, operator, state, monkeypatch):  # noqa: F811
    from agentbreeder import redaction as redaction_module

    prepare_lock(project, operator)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(redaction_module, "collect", broken)
    monkeypatch.setattr(redaction_module, "redact", broken)
    logging = LoggingRuntime("x\n")
    outcome, _ = do_release(
        project, operator, state, logging, "sha256:" + "a" * 64, Evaluator(passed=False)
    )
    assert outcome.code == EXIT_EVAL
    assert state.active("helper-candidate") is None and logging.containers == {}
    assert "candidate_log" not in outcome.details


def test_an_unreadable_secret_source_means_no_tail(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)
    secret_files.set_secret(state.directory, "db", VALUE.encode())
    (state.directory / "secrets" / "db").chmod(0o666)  # refused by the redactor reader
    logging = LoggingRuntime(f"using {VALUE}\n")
    outcome, _ = do_release(
        project, operator, state, logging, "sha256:" + "b" * 64, Evaluator(passed=False)
    )
    assert "candidate_log" not in outcome.details
    assert "could not be fully redacted" in outcome.details["candidate_log_skipped"]
    assert not candidate_tail_path(state.directory, "helper").exists()


def test_a_runtime_without_logs_never_breaks_a_release(project, operator, state):  # noqa: F811
    prepare_lock(project, operator)
    outcome, _ = do_release(
        project, operator, state, FakeRuntime(), "sha256:" + "9" * 64, Evaluator(passed=False)
    )
    assert outcome.code == EXIT_EVAL and "candidate_log" not in outcome.details


# -- logs --failed (S-08) ----------------------------------------------------------------------


def saved_tail(state_dir: Path, text: str) -> Path:
    private_dir(state_dir / "releases")
    path = state_dir / "releases" / "hello.candidate.log"
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_logs_failed_escapes_controls(tmp_path, capsys):
    state = private_dir(tmp_path / "state")
    saved_tail(state, "ok\tfine\n\x1b]52;c;ZXZpbA==\x07evil \x1b[2J\x9b\n")
    assert cli.main(["logs", "--failed", "hello", "--state-dir", str(state)]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "\x9b" not in out
    assert "\\x1b]52;c;ZXZpbA==\\x07evil \\x1b[2J\\x9b" in out and "ok\tfine" in out
    assert cli.main(["logs", "--failed", "hello", "--state-dir", str(state), "--raw"]) == 0
    assert "\x1b]52" in capsys.readouterr().out
    assert cli.main(["logs", "--failed", "hello", "--state-dir", str(state), "--json"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and json.loads(out)["lines"][1].startswith("\x1b]52")


def test_logs_failed_missing_bad_name_and_unsafe_file(tmp_path, capsys):
    state = private_dir(tmp_path / "state")
    assert cli.main(["logs", "--failed", "hello", "--state-dir", str(state)]) == 1
    assert "No failed-candidate log for hello" in capsys.readouterr().err
    assert cli.main(["logs", "--failed", "../etc", "--state-dir", str(state)]) == 2
    path = saved_tail(state, "x\n")
    path.chmod(0o644)
    assert cli.main(["logs", "--failed", "hello", "--state-dir", str(state)]) == 2
    path.unlink()
    target = tmp_path / "elsewhere"
    target.write_text("outside\n")
    path.symlink_to(target)
    assert cli.main(["logs", "--failed", "hello", "--state-dir", str(state)]) == 2
    assert "outside" not in capsys.readouterr().out
    missing = tmp_path / "missing"
    assert cli.main(["logs", "--failed", "hello", "--state-dir", str(missing)]) == 1
    assert not missing.exists()  # read-only: nothing created


def test_logs_failed_end_to_end(tmp_path):
    state = private_dir(tmp_path / "state")
    saved_tail(state, "candidate said \x1b[31mhi\n")
    argv = ["logs", "--failed", "hello", "--state-dir", str(state)]
    result = subprocess.run(
        [sys.executable, "-m", "agentbreeder", *argv],
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0 and result.stdout == "candidate said \\x1b[31mhi\n"


# -- S-09: no child inherits the CLI's stdio ---------------------------------------------------

# Code that runs inside images and the console (lane B redacts its own job pump) are out of
# scope; every CLI child must capture or discard its output, so nothing reaches a job's output
# unredacted.
EXCLUDED = ("builtin_registry", "examples", "center")
# Injectable process starters stored on `self` (subprocess.run / Popen by default).
WRAPPERS = {("compose.py", "_run"), ("compose.py", "_popen"), ("boundary.py", "spawn")}
CHECKED = {"run", "Popen", "call", "check_call", "check_output"}
FORBIDDEN = {
    ("subprocess", "getoutput"),
    ("subprocess", "getstatusoutput"),
    ("os", "system"),
    ("os", "popen"),
    ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
}
FORBIDDEN_PREFIXES = (("os", "exec"), ("os", "spawn"), ("os", "posix_spawn"))
STREAMS = {"PIPE", "DEVNULL", "STDOUT"}


def _aliases(tree) -> tuple[dict, dict]:
    """Module aliases (`import subprocess as sp`) and imported names (`from os import system`)."""
    modules, names = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = (node.module, alias.name)
    return modules, names


def _target(func, modules, names, injected):
    """(module, function) a call goes to, when it is a process API."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module = modules.get(func.value.id)
        if module:
            return module, func.attr
    if isinstance(func, ast.Name):
        if func.id in names:
            return names[func.id]
        if func.id in injected:
            return "subprocess", injected[func.id]
    return None


def _stream_ok(node, modules, names) -> bool:
    if isinstance(node, ast.IfExp):
        return _stream_ok(node.body, modules, names) and _stream_ok(node.orelse, modules, names)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return modules.get(node.value.id) == "subprocess" and node.attr in STREAMS
    if isinstance(node, ast.Name):
        if names.get(node.id, ("", ""))[0] == "subprocess":
            return names[node.id][1] in STREAMS
        return node.id not in ("stdout", "stderr")  # a file descriptor variable, e.g. `log`
    return False  # None, 1, sys.stdout, a call...


def _injected(tree, modules, names) -> dict:
    """Parameters defaulting to a subprocess function: `def f(run=subprocess.run)`."""
    injected = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        positional = node.args.args
        defaults = [None] * (len(positional) - len(node.args.defaults)) + node.args.defaults
        pairs = list(zip(positional, defaults, strict=True))
        pairs += list(zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True))
        for argument, default in pairs:
            target = _target(default, modules, names, {}) if default is not None else None
            if target and target[0] == "subprocess":
                injected[argument.arg] = target[1]
    return injected


def _process_calls():
    """(where, kind, call, modules, names) for every process start in CLI code."""
    for path in sorted((SRC / "agentbreeder").rglob("*.py")):
        if set(EXCLUDED) & set(path.relative_to(SRC / "agentbreeder").parts):
            continue
        tree = ast.parse(path.read_text())
        modules, names = _aliases(tree)
        injected = _injected(tree, modules, names)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            where = f"{path.relative_to(SRC)}:{node.lineno}"
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "self"
                and (path.name, func.attr) in WRAPPERS
            ):
                yield where, "Popen", node, modules, names
                continue
            target = _target(func, modules, names, injected)
            if target is None:
                continue
            if target in FORBIDDEN or any(
                target[0] == m and target[1].startswith(p) for m, p in FORBIDDEN_PREFIXES
            ):
                yield where, "forbidden", node, modules, names
            elif target[0] == "subprocess" and target[1] in CHECKED:
                yield where, target[1], node, modules, names


def _violation(kind, node, modules, names) -> str | None:
    if kind == "forbidden":
        return "uses a process API that inherits stdio"
    keywords = {k.arg: k.value for k in node.keywords}
    capture = keywords.get("capture_output")
    if isinstance(capture, ast.Constant) and capture.value is True:
        return None  # Python refuses stdout=/stderr= beside it, even from **kwargs
    if None in keywords:
        return "passes **kwargs (streams cannot be checked)"
    needed = ("stderr",) if kind == "check_output" else ("stdout", "stderr")
    for stream in needed:
        if stream not in keywords or not _stream_ok(keywords[stream], modules, names):
            return f"{stream} is not PIPE, DEVNULL, STDOUT or a file"
    return None


def test_cli_children_never_inherit_stdio():
    found = list(_process_calls())
    assert len(found) >= 10
    assert any("doctor.py" in where for where, *_ in found)  # injected `run=subprocess.run`
    problems = [
        f"{where}: {problem}"
        for where, kind, node, modules, names in found
        if (problem := _violation(kind, node, modules, names))
    ]
    assert problems == []


@pytest.mark.parametrize(
    "source, bad",
    [
        ("import subprocess\nsubprocess.run(['x'])", True),
        ("import subprocess as sp\nsp.Popen(['x'], stdout=sp.PIPE)", True),
        ("from subprocess import check_output\ncheck_output(['x'])", True),
        ("import os\nos.system('x')", True),
        ("from os import execvp\nexecvp('x', ['x'])", True),
        ("import asyncio\nasyncio.create_subprocess_exec('x')", True),
        ("import subprocess, sys\nsubprocess.run(['x'], stdout=sys.stdout, stderr=1)", True),
        ("import subprocess\nsubprocess.run(['x'], **options)", True),
        ("import subprocess\ndef f(run=subprocess.run):\n    run(['x'])", True),
        ("import subprocess\nsubprocess.run(['x'], capture_output=True)", False),
        (
            "import subprocess as sp\n"
            "sp.Popen(['x'], stdout=sp.PIPE if a else sp.DEVNULL, stderr=sp.STDOUT)",
            False,
        ),
    ],
)
def test_the_stdio_checker_itself(tmp_path, monkeypatch, source, bad):
    package = tmp_path / "agentbreeder"
    package.mkdir()
    (package / "probe.py").write_text(source)
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_path)
    problems = [
        p for _, kind, node, m, n in _process_calls() if (p := _violation(kind, node, m, n))
    ]
    assert bool(problems) is bad
