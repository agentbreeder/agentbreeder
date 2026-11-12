"""`agentbreeder logs ID` (M5, guided §14.3): a local deployment's logs, redacted and escaped."""

from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from conftest import v2_manifest_data

from agentbreeder import cli, redaction
from agentbreeder.commands import logs as logs_command
from agentbreeder.store import DeploymentError, Store

DIGEST = "sha256:" + "d" * 64
SECRET = "seeded-marker-" + "q" * 20  # a stored secret value; must never be printed
IDENTIFIER = "a" * 32
CONTAINER = "c" * 64


def deployment(state, identifier=IDENTIFIER, target="local", record_state="ready"):
    store = Store(state)
    try:
        manifest = v2_manifest_data(name="hello", port=8080)
        if target != "local":
            manifest["target"] = target
        store.create(identifier, manifest, DIGEST, "test-context")
        store.transition(identifier, record_state, endpoint="http://127.0.0.1:5000")
    finally:
        store.close()
    return identifier


def secret(state, name="token", value=SECRET):
    directory = state / "secrets"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / name
    path.write_text(value)
    path.chmod(0o600)


class FakeDocker:
    def __init__(self, text="", truncated=False, gone=False, child=None):
        self.text, self.truncated, self.gone, self.child = text, truncated, gone, child
        self.calls = []
        self.process = None

    def authorize(self, context=None):
        self.calls.append(("authorize", context))

    def inspect(self, record):
        self.calls.append(("inspect", record["id"]))
        return None if self.gone else {"Id": CONTAINER}

    def logs(self, info, tail=500, since=None):
        self.calls.append(("logs", info["Id"], tail, since))
        return self.text, self.truncated

    def follow(self, info, tail=200, since=None):
        self.calls.append(("follow", info["Id"], tail, since))
        self.process = subprocess.Popen(
            [sys.executable, "-c", self.child],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return self.process


@pytest.fixture
def state(tmp_path):
    directory = tmp_path / "state"
    deployment(directory)
    secret(directory)
    return directory


@pytest.fixture
def docker(monkeypatch):
    holder = SimpleNamespace()

    def install(fake):
        monkeypatch.setattr(logs_command, "_runtime", lambda: fake)
        holder.fake = fake
        return fake

    holder.install = install
    return holder


def run(state, *argv):
    return cli.main(["logs", *argv, "--state-dir", str(state)])


LOG = (
    f"2026-11-12T10:00:00Z starting with token {SECRET}\n"
    f"2026-11-12T10:00:01Z basic {base64.b64encode(SECRET.encode()).decode()}\n"
    "2026-11-12T10:00:02Z db postgresql://app:hunter2-password@db:5432/app\n"
    "2026-11-12T10:00:03Z paint \x1b]52;c;cGF5bG9hZA==\x07\x1b[2J done\n"
)


# -- reading -----------------------------------------------------------------------------------


def test_lines_are_redacted_and_escaped(state, docker, capsys):
    fake = docker.install(FakeDocker(text=LOG))
    assert run(state, IDENTIFIER) == 0
    out = capsys.readouterr().out
    assert SECRET not in out and base64.b64encode(SECRET.encode()).decode() not in out
    assert "hunter2-password" not in out  # URL passwords, independent of stored values
    assert redaction.MARK in out
    assert "\x1b" not in out and "\x07" not in out and "\\x1b]52" in out
    assert fake.calls == [
        ("authorize", "test-context"),
        ("inspect", IDENTIFIER),
        ("logs", CONTAINER, 200, None),
    ]


def test_raw_keeps_controls_but_still_redacts(state, docker, capsys):
    docker.install(FakeDocker(text=LOG))
    assert run(state, IDENTIFIER, "--raw") == 0
    out = capsys.readouterr().out
    assert "\x1b]52" in out and SECRET not in out


def test_json_shape(state, docker, capsys):
    docker.install(FakeDocker(text=LOG, truncated=True))
    assert run(state, IDENTIFIER, "--json", "--tail", "50", "--since", "10m") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1 and result["source"] == "agent"
    assert (result["deployment"], result["name"], result["truncated"]) == (
        IDENTIFIER,
        "hello",
        True,
    )
    assert len(result["lines"]) == 4 and SECRET not in json.dumps(result)
    assert docker.fake.calls[-1] == ("logs", CONTAINER, 50, "10m")


def test_truncated_note_in_text(state, docker, capsys):
    docker.install(FakeDocker(text="x\n", truncated=True))
    assert run(state, IDENTIFIER) == 0
    assert "older lines were dropped" in capsys.readouterr().err


def test_the_deployments_own_copies_are_redacted(tmp_path, docker, capsys):
    state = tmp_path / "state"
    deployment(state)
    copies = state / "deployments" / IDENTIFIER / "secrets"
    copies.mkdir(parents=True)
    for directory in (state / "deployments", state / "deployments" / IDENTIFIER, copies):
        directory.chmod(0o700)
    gateway = "seeded-gateway-" + "k" * 24
    (copies / "gateway-key").write_text(gateway)
    (copies / "gateway-key").chmod(0o444)
    docker.install(FakeDocker(text=f"key={gateway}\n"))
    assert run(state, IDENTIFIER) == 0
    assert gateway not in capsys.readouterr().out


def test_the_state_is_not_written(state, docker, capsys):
    docker.install(FakeDocker(text=LOG))
    before = {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()}
    assert run(state, IDENTIFIER) == 0
    assert {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()} == before


# -- refusals ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["not-an-id"], "ID_INVALID"),
        ([IDENTIFIER, "--since", "yesterday"], "SINCE_INVALID"),
        (["b" * 32], "NOT_FOUND"),
        ([], "USAGE"),
        ([IDENTIFIER, "--failed", "hello"], "USAGE"),
        (["--failed", "hello", "--follow"], "USAGE"),
        (["--failed", "hello", "--tail", "5"], "USAGE"),
    ],
)
def test_refusals(state, docker, capsys, argv, code):
    fake = docker.install(FakeDocker(text=LOG))
    assert run(state, *argv, "--json") == 2
    captured = capsys.readouterr()
    assert captured.err.startswith(f"[{code}]")
    assert json.loads(captured.out)["error"]["code"] == code
    assert not [call for call in fake.calls if call[0] in ("logs", "follow")]


@pytest.mark.parametrize("tail", ["0", "10001", "x"])
def test_tail_range(state, tail, capsys):
    with pytest.raises(SystemExit) as error:
        run(state, IDENTIFIER, "--tail", tail)
    assert error.value.code == 2


def test_kubernetes_deployments_are_not_read_yet(tmp_path, docker, capsys):
    state = tmp_path / "state"
    deployment(state, target="kubernetes")
    fake = docker.install(FakeDocker())
    assert run(state, IDENTIFIER) == 2
    assert capsys.readouterr().err.startswith("[NOT_LOCAL]") and fake.calls == []


def test_removed_deployments(tmp_path, docker, capsys):
    state = tmp_path / "state"
    deployment(state, record_state="removed")
    docker.install(FakeDocker())
    assert run(state, IDENTIFIER) == 2
    assert capsys.readouterr().err.startswith("[REMOVED]")


def test_missing_state_is_not_created(tmp_path, docker, capsys):
    docker.install(FakeDocker())
    assert run(tmp_path / "none", IDENTIFIER) == 2
    assert capsys.readouterr().err.startswith("[NO_STATE]")
    assert not (tmp_path / "none").exists()


def test_redaction_incomplete_prints_nothing(state, docker, capsys):
    (state / "secrets" / "unreadable").write_text("x" * 20)
    (state / "secrets" / "unreadable").chmod(0o666)  # read_copy refuses group/other write
    fake = docker.install(FakeDocker(text=LOG))
    assert run(state, IDENTIFIER) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[REDACTION_INCOMPLETE]") and captured.out == ""
    assert fake.calls == []  # Docker is not even asked


def test_container_checks(state, docker, capsys):
    docker.install(FakeDocker(gone=True))
    assert run(state, IDENTIFIER) == 2
    assert capsys.readouterr().err.startswith("[CONTAINER_GONE]")
    fake = docker.install(FakeDocker())

    def mismatch(record):
        raise DeploymentError("Container ownership does not match; no changes made")

    fake.inspect = mismatch
    assert run(state, IDENTIFIER) == 2
    assert capsys.readouterr().err.startswith("[CONTAINER_MISMATCH]")

    def down(context=None):
        raise DeploymentError("Cannot reach the Docker engine for this context")

    fake = docker.install(FakeDocker())
    fake.authorize = down
    assert run(state, IDENTIFIER) == 2
    assert capsys.readouterr().err.startswith("[DOCKER_UNAVAILABLE]")

    def failing(info, tail=500, since=None):
        raise DeploymentError("Docker could not read the container's logs")

    fake = docker.install(FakeDocker())
    fake.logs = failing
    assert run(state, IDENTIFIER) == 2
    assert capsys.readouterr().err.startswith("[DOCKER_LOGS_FAILED]")


def test_state_problems_are_coded(state, docker, capsys, monkeypatch):
    from agentbreeder import store as store_module

    docker.install(FakeDocker())

    def old(directory):
        raise store_module.StateNeedsMigration("schema v2; run migrate")

    monkeypatch.setattr(store_module.Store, "open_readonly", staticmethod(old))
    assert run(state, IDENTIFIER) == 2
    assert capsys.readouterr().err.startswith("[STATE_NEEDS_MIGRATION]")


def test_failed_still_works(tmp_path, capsys):
    from agentbreeder.release import candidate_tail_path

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = candidate_tail_path(state.absolute(), "hello")
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text("line one\n")
    path.chmod(0o600)
    assert run(state, "--failed", "hello") == 0
    assert capsys.readouterr().out == "line one\n"


# -- follow ------------------------------------------------------------------------------------

CHILD = f"""
import sys, time
print("2026-11-12T10:00:00Z first {SECRET}", flush=True)
print("2026-11-12T10:00:01Z second \\x1b[2J", flush=True)
time.sleep(60)
"""


def _pipe_stdin(monkeypatch, close_after):
    read, write = os.pipe()
    monkeypatch.setattr(sys, "stdin", os.fdopen(read, "r"))
    timer = threading.Timer(close_after, os.close, args=(write,))
    timer.start()
    return timer


def _no_stdin_watch(monkeypatch):
    monkeypatch.setattr(logs_command, "_stdin_descriptor", lambda: None)


def test_follow_streams_redacted_lines_and_stops_at_stdin_eof(state, docker, capsys, monkeypatch):
    fake = docker.install(FakeDocker(child=CHILD))
    timer = _pipe_stdin(monkeypatch, close_after=1.0)
    started = time.monotonic()
    try:
        assert run(state, IDENTIFIER, "--follow", "--tail", "20", "--since", "5m") == 0
    finally:
        timer.cancel()
    assert time.monotonic() - started < 10  # not the child's 60 s
    captured = capsys.readouterr()
    out = captured.out.splitlines()
    assert out[0].endswith(f"first {redaction.MARK}") and "\\x1b[2J" in out[1]
    assert "Follow ended: stdin ended" in captured.err
    assert fake.calls[-1] == ("follow", CONTAINER, 20, "5m")
    assert fake.process.poll() is not None  # S-10: stopped and reaped


def test_follow_json_lines_and_end_record(state, docker, capsys, monkeypatch):
    docker.install(FakeDocker(child=CHILD))
    timer = _pipe_stdin(monkeypatch, close_after=1.0)
    try:
        assert run(state, IDENTIFIER, "--follow", "--json") == 0
    finally:
        timer.cancel()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0] == {
        "schema_version": 1,
        "source": "agent",
        "name": "hello",
        "deployment": IDENTIFIER,
        "line": f"2026-11-12T10:00:00Z first {redaction.MARK}",
    }
    assert lines[1]["line"].endswith("second \x1b[2J")  # JSON escapes it on output
    assert lines[-1] == {"schema_version": 1, "event": "end", "reason": "stdin", "dropped": False}


def test_follow_stops_on_sigterm_and_restores_the_handler(state, docker, capsys, monkeypatch):
    fake = docker.install(FakeDocker(child=CHILD))
    _no_stdin_watch(monkeypatch)
    before = signal.getsignal(signal.SIGTERM)
    timer = threading.Timer(1.0, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        assert run(state, IDENTIFIER, "--follow") == 0
    finally:
        timer.cancel()
    assert fake.process.poll() is not None
    assert "stopped by a signal" in capsys.readouterr().err
    assert signal.getsignal(signal.SIGTERM) is before


def test_follow_ends_when_the_container_stops(state, docker, capsys, monkeypatch):
    docker.install(FakeDocker(child="print('last line', flush=True)"))  # docker exits 0
    _no_stdin_watch(monkeypatch)
    assert run(state, IDENTIFIER, "--follow", "--json") == 0
    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines()]
    assert lines[0]["line"] == "last line" and lines[-1]["reason"] == "container"
    assert "the container stopped or was removed" in captured.err


def test_a_failing_docker_logs_is_an_error(state, docker, capsys, monkeypatch):
    child = "print('Error: no such container', flush=True); raise SystemExit(1)"
    docker.install(FakeDocker(child=child))
    _no_stdin_watch(monkeypatch)
    assert run(state, IDENTIFIER, "--follow", "--json") == 2
    captured = capsys.readouterr()
    last = captured.out.splitlines()[-1]
    assert json.loads(last)["error"]["code"] == "DOCKER_LOGS_FAILED"  # one line: NDJSON-safe
    assert captured.err.startswith("[DOCKER_LOGS_FAILED]")


def test_a_child_stopped_by_the_terminals_ctrl_c_is_an_interrupt(
    state, docker, capsys, monkeypatch
):
    child = (
        "import os, signal; print('x', flush=True); "
        "signal.signal(signal.SIGINT, signal.SIG_DFL); os.kill(os.getpid(), signal.SIGINT)"
    )
    docker.install(FakeDocker(child=child))
    _no_stdin_watch(monkeypatch)
    assert run(state, IDENTIFIER, "--follow", "--json") == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[-1]["reason"] == "interrupt"


def test_follow_drops_an_over_long_line_whole(state, docker, capsys, monkeypatch):
    monkeypatch.setattr(logs_command, "LINE_LIMIT", 64)
    child = f"print('x' * 200 + '{SECRET}', flush=True); print('after', flush=True)"
    docker.install(FakeDocker(child=child))
    _no_stdin_watch(monkeypatch)
    assert run(state, IDENTIFIER, "--follow") == 0
    captured = capsys.readouterr()
    assert captured.out == "after\n" and "dropped" in captured.err
    assert "x" * 64 not in captured.out


def test_a_signal_before_the_child_starts_starts_nothing(state, docker, capsys, monkeypatch):
    fake = docker.install(FakeDocker(child=CHILD))
    real = logs_command._handlers

    def early(follow):
        installed = real(follow)
        follow.stop("signal")
        return installed

    monkeypatch.setattr(logs_command, "_handlers", early)
    assert run(state, IDENTIFIER, "--follow") == 0
    assert fake.process is None and "stopped by a signal" in capsys.readouterr().err


# -- the CLI as a real process (interpreter shutdown, signals from outside) ---------------------

WRAPPER = """
import os, subprocess, sys
from agentbreeder import cli
from agentbreeder.commands import logs

class Fake:
    def authorize(self, context=None):
        pass
    def inspect(self, record):
        return {"Id": "c" * 64}
    def follow(self, info, tail=200, since=None):
        return subprocess.Popen(
            [sys.executable, "-c", os.environ["FAKE_CHILD"]],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )

logs._runtime = Fake
sys.exit(cli.main(sys.argv[1:]))
"""

PID_CHILD = """
import os, sys, time
print("PID", os.getpid(), flush=True)
print("line two", flush=True)
time.sleep({linger})
"""


def _spawn(tmp_path, state, linger=60):
    script = tmp_path / "wrapper.py"
    script.write_text(WRAPPER)
    env = {**os.environ, "FAKE_CHILD": PID_CHILD.format(linger=linger)}
    argv = [sys.executable, str(script), "logs", IDENTIFIER, "--follow"]
    process = subprocess.Popen(
        [*argv, "--state-dir", str(state)],
        stdin=subprocess.PIPE,  # held open, as the console will
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    first = process.stdout.readline().decode()
    assert first.startswith("PID "), first
    return process, int(first.split()[1])


def _finish(process) -> bytes:
    """Wait for the CLI to exit on its own, with stdin still open (unlike `communicate`, which
    closes stdin at once and would stop the follow itself); then return stderr."""
    process.wait(timeout=20)
    err = process.stderr.read()
    for stream in (process.stdin, process.stdout):
        if stream is not None and not stream.closed:
            stream.close()
    return err


def _dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGHUP])
def test_a_real_process_stops_cleanly_on_a_signal(tmp_path, state, number):
    process, child = _spawn(tmp_path, state)
    process.send_signal(number)
    err = _finish(process)
    assert process.returncode == 0, err
    assert b"Fatal" not in err and b"Traceback" not in err
    assert b"stopped by a signal" in err
    assert _dead(child)  # S-10: the grandchild is gone


def test_a_real_process_ends_cleanly_when_the_container_stops(tmp_path, state):
    process, child = _spawn(tmp_path, state, linger=0)  # the child exits on its own
    time.sleep(1.0)  # it ends while stdin is still an open pipe
    err = _finish(process)
    assert process.returncode == 0, err
    assert b"Fatal" not in err and b"Traceback" not in err
    assert b"the container stopped" in err


def test_a_real_process_stops_at_stdin_eof(tmp_path, state):
    process, child = _spawn(tmp_path, state)
    process.stdin.close()
    err = _finish(process)
    assert process.returncode == 0 and b"stdin ended" in err, err
    assert _dead(child)


def test_a_real_process_survives_a_closed_stdout(tmp_path, state):
    process, child = _spawn(tmp_path, state, linger=1)
    process.stdout.close()  # `| head -1`: the next line hits a closed pipe
    process.wait(timeout=20)
    err = process.stderr.read()
    process.stdin.close()
    assert process.returncode == 0, err
    assert b"Traceback" not in err and b"Fatal" not in err
    assert _dead(child)


# -- review round 1 ----------------------------------------------------------------------------


def test_join_partials_rejoins_long_lines():
    from agentbreeder.runtime import PARTIAL, join_partials

    payload = b"x" * (PARTIAL - 4) + SECRET.encode()
    first, rest = payload[:PARTIAL], payload[PARTIAL:]
    chunked = b"2026-11-12T10:00:00.1Z " + first + b"2026-11-12T10:00:00.2Z " + rest
    assert join_partials(chunked) == b"2026-11-12T10:00:00.1Z " + payload
    assert join_partials(b"no stamp " + b"y" * 20000) == b"no stamp " + b"y" * 20000
    short = b"2026-11-12T10:00:00Z short"
    assert join_partials(short) == short


def test_lines_split_on_newline_only(state, docker, capsys):
    text = "2026-11-12T10:00:00Z real\x85FORGED ALSO\x1cX\rY\n2026-11-12T10:00:01Z next\n"
    docker.install(FakeDocker(text=text))
    assert run(state, IDENTIFIER, "--json") == 0
    assert len(json.loads(capsys.readouterr().out)["lines"]) == 2
    assert run(state, IDENTIFIER) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 2
    assert "\\u2028" in out and "\\x85" in out and "\\x1c" in out and "\\x0d" in out


def test_missing_copies_refuse_for_a_deployment_that_had_them(tmp_path, docker, capsys):
    state = tmp_path / "state"
    store = Store(state)
    try:
        manifest = v2_manifest_data(name="hello", port=8080)
        manifest["secrets"] = {"API_TOKEN": "secret://token"}
        store.create(IDENTIFIER, manifest, DIGEST, "test-context")
        store.transition(IDENTIFIER, "failed", error="health")
    finally:
        store.close()
    fake = docker.install(FakeDocker(text="x\n"))
    assert run(state, IDENTIFIER) == 2
    assert capsys.readouterr().err.startswith("[REDACTION_INCOMPLETE]")
    assert fake.calls == []


def test_an_unreadable_stack_secret_is_a_problem():
    from agentbreeder.services import state as state_module

    problems = []

    class Broken:
        db = SimpleNamespace(execute=lambda query: [("litellm", "master-key")])

        def read_secret(self, service, name):
            raise state_module.ServiceError("SECRET_UNREADABLE", "no")

    values = state_module.StackState.known_secrets(Broken(), problems)
    assert values == []
    assert problems == ["cannot read the stack's stored secret litellm/master-key"]


def test_an_oversized_copy_is_a_problem(tmp_path):
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o700)
    big = directory / "big"
    big.write_bytes(b"line\n" * (redaction.READ_LIMIT // 4))
    big.chmod(0o600)
    found = redaction.Collected()
    redaction._files(directory, found, "local secrets")
    assert any("larger than" in problem for problem in found.problems)


def test_failed_json_has_a_schema_version(tmp_path, capsys):
    from agentbreeder.release import candidate_tail_path

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    path = candidate_tail_path(state.absolute(), "hello")
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text("one\x85two\n")
    path.chmod(0o600)
    assert run(state, "--failed", "hello", "--json") == 0
    body = json.loads(capsys.readouterr().out)
    assert body["schema_version"] == 1 and body["lines"] == ["one\x85two"]


def test_redactor_matches_redact():
    values = [SECRET.encode(), b"-----BEGIN KEY-----\nline-one-of-key-material\n-----END KEY-----"]
    text = f"a {SECRET} b line-one-of-key-material c http://u:pw-secret@h/x"
    assert redaction.Redactor(values)(text) == redaction.redact(text, values)
