"""`agentbreeder invoke NAME MESSAGE|-` (M4, guided §9.2 #7): loopback-only, verified, stripped."""

from __future__ import annotations

import io
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from conftest import v2_manifest_data

from agentbreeder import cli, guide_checks, invoke
from agentbreeder.commands import invoke as invoke_command
from agentbreeder.store import DeploymentError, Store

DIGEST = "sha256:" + "d" * 64
MARKER = "seeded-marker-" + "q" * 20


class Agent(BaseHTTPRequestHandler):
    """A stand-in for the ADK runtime's POST /invoke."""

    answer: tuple[int, object] = (
        200,
        {"reply": "Support is open 9am to 5pm.", "session_id": "s1"},
    )
    seen: list = []
    location: str | None = None

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        type(self).seen.append(
            {
                "path": self.path,
                "host": self.headers.get("Host"),
                "type": self.headers.get("Content-Type"),
                "body": json.loads(self.rfile.read(size)),
            }
        )
        status, body = type(self).answer
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        if type(self).location:
            self.send_header("Location", type(self).location)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def agent():
    Agent.answer = (200, {"reply": "Support is open 9am to 5pm.", "session_id": "s1"})
    Agent.seen = []
    Agent.location = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), Agent)
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class FakeDocker:
    def __init__(self, endpoint=None, gone=False, error=None):
        self.endpoint_value, self.gone, self.error = endpoint, gone, error
        self.calls = []

    def authorize(self, context=None):
        self.calls.append(("authorize", context))
        if self.error:
            raise self.error

    def inspect(self, record):
        self.calls.append(("inspect", record["id"]))
        return None if self.gone else {"Id": "c" * 64}

    def endpoint(self, info, manifest):
        self.calls.append(("endpoint", manifest.port))
        return self.endpoint_value


def deployment(state_dir, name, endpoint, target="local", state="ready", identifier=None):
    store = Store(state_dir)
    try:
        identifier = identifier or f"id-{name}-{target}"
        manifest = v2_manifest_data(name=name, port=8080)
        if target != "local":
            manifest["target"] = target
        store.create(identifier, manifest, DIGEST, "test-context")
        store.transition(identifier, state, endpoint=endpoint)
    finally:
        store.close()
    return identifier


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


@pytest.fixture
def docker(monkeypatch):
    holder = SimpleNamespace(fake=None)

    def install(fake):
        holder.fake = fake
        monkeypatch.setattr(invoke_command, "_runtime", lambda: fake)
        return fake

    holder.install = install
    return holder


def run(state, *argv):
    return cli.main(["invoke", *argv, "--state-dir", str(state)])


# -- the happy path -----------------------------------------------------------------------


def test_reply_is_printed_after_the_container_is_verified(state, agent, docker, capsys):
    identifier = deployment(state, "hello", agent)
    fake = docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "When is support open?") == 0
    captured = capsys.readouterr()
    assert captured.out == "Support is open 9am to 5pm.\n"
    assert "Session: s1" in captured.err
    assert fake.calls == [
        ("authorize", "test-context"),
        ("inspect", identifier),
        ("endpoint", 8080),
    ]
    assert Agent.seen == [
        {
            "path": "/invoke",
            "host": agent.removeprefix("http://"),
            "type": "application/json",
            "body": {"message": "When is support open?"},  # no customer_id by default
        }
    ]


def test_json_prints_the_whole_answer(state, agent, docker, capsys):
    identifier = deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi", "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "schema_version": 1,
        "deployment": identifier,
        "name": "hello",
        "endpoint": agent,
        "status": 200,
        "response": {"reply": "Support is open 9am to 5pm.", "session_id": "s1"},
    }


def test_candidate_calls_the_candidate(state, agent, docker, capsys):
    deployment(state, "hello", "http://127.0.0.1:1")  # production elsewhere
    deployment(state, "hello-candidate", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi", "--candidate") == 0
    assert "Support is open" in capsys.readouterr().out


def test_message_from_stdin(state, agent, docker, monkeypatch, capsys):
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"  from stdin \n")))
    assert run(state, "hello", "-") == 0
    assert Agent.seen[0]["body"] == {"message": "from stdin"}


def test_customer_and_session_are_sent_with_a_note(state, agent, docker, capsys):
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi", "--customer", "c-1", "--session", "s_2") == 0
    assert Agent.seen[0]["body"] == {"message": "hi", "customer_id": "c-1", "session_id": "s_2"}
    assert "not authenticated" in capsys.readouterr().err and "#780" in invoke.NOT_AUTHENTICATED


# -- the reply is untrusted ---------------------------------------------------------------


def test_controls_in_the_reply_are_escaped(state, agent, docker, capsys):
    Agent.answer = (200, {"reply": "ok\x1b]52;c;cGF5bG9hZA==\x07\x1b[2J‮next", "session_id": "s"})
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "‮" not in out
    assert "\\x1b]52" in out


def test_a_hostile_session_id_is_not_echoed(state, agent, docker, capsys):
    Agent.answer = (200, {"reply": "ok", "session_id": "x\x1b[31m"})
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 0
    assert "Session" not in capsys.readouterr().err


def test_agent_error_exits_1_with_a_stripped_excerpt(state, agent, docker, capsys):
    Agent.answer = (
        503,
        {"error": "Conversation database unavailable\x1b[2J; is Postgres running?"},
    )
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 1
    err = capsys.readouterr().err
    assert err.startswith("[AGENT_ERROR] hello answered 503: Conversation database")
    assert "\x1b" not in err
    assert run(state, "hello", "hi", "--json") == 1
    assert json.loads(capsys.readouterr().out)["status"] == 503


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[1, 2]", b"x" * (invoke.MAX_RESPONSE + 2), {"no": "reply"}],
)
def test_bad_answers(state, agent, docker, capsys, body):
    Agent.answer = (200, body)
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    code = run(state, "hello", "hi")
    err = capsys.readouterr().err
    if isinstance(body, dict):
        assert code == 1 and "no reply in the answer" in err
    else:  # sent, but no usable answer: exit 1 (a retry is a second turn)
        assert code == 1 and err.startswith("[RESPONSE_INVALID]")


def test_redirects_are_refused(state, agent, docker, capsys):
    Agent.answer = (307, {"error": "moved"})
    Agent.location = "http://198.51.100.7/invoke"
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi", "--json") == 1  # the message was sent
    captured = capsys.readouterr()
    assert captured.err.startswith("[REDIRECT_REFUSED]")
    assert json.loads(captured.out)["error"] == {
        "code": "REDIRECT_REFUSED",
        "message": "the agent answered with a redirect (307)",
        "sent": True,
    }
    assert len(Agent.seen) == 1  # never followed


# -- what is refused before anything is sent ------------------------------------------------


def test_proxy_environment_is_ignored(state, agent, docker, monkeypatch, capsys):
    proxies = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
    for name in proxies:
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # a dead proxy
    monkeypatch.setenv("no_proxy", "example.com")  # an exclusion list that does not cover us
    monkeypatch.delenv("NO_PROXY", raising=False)
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 0
    assert Agent.seen  # reached directly


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:8080",
        "http://10.0.0.5:8080",
        "https://127.0.0.1:8080",
        "http://127.0.0.1:8080/x",
        "http://127.0.0.1:0",
        "http://127.0.0.1:99999",
        None,
    ],
)
def test_non_loopback_endpoints_are_refused(state, docker, capsys, endpoint):
    deployment(state, "hello", endpoint)
    fake = docker.install(FakeDocker(endpoint=endpoint))
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[ENDPOINT_REFUSED]")
    assert fake.calls == []  # Docker is not even asked


def test_kubernetes_rows_point_at_forward(state, docker, capsys):
    identifier = deployment(state, "hello", "kubernetes://ns/svc", target="kubernetes")
    fake = docker.install(FakeDocker())
    assert run(state, "hello", "hi") == 2
    err = capsys.readouterr().err
    assert err.startswith("[KUBERNETES_TARGET]") and f"forward {identifier}" in err
    assert fake.calls == []


def test_a_dead_container_is_not_called(state, agent, docker, capsys):
    deployment(state, "hello", agent)
    docker.install(FakeDocker(gone=True))
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[CONTAINER_GONE]")
    assert Agent.seen == []


def test_a_port_the_container_no_longer_owns_is_not_called(state, agent, docker, capsys):
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint="http://127.0.0.1:1"))
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[PORT_CHANGED]")
    assert Agent.seen == []


def test_a_stopped_container_is_not_called(state, agent, docker, capsys):
    deployment(state, "hello", agent)
    fake = docker.install(FakeDocker(endpoint=agent))

    def stopped(info, manifest):
        raise DeploymentError("Container is not running with one loopback HTTP binding")

    fake.endpoint = stopped
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[CONTAINER_STOPPED]")
    assert Agent.seen == []


def test_docker_unreachable_is_reported(state, agent, docker, capsys):
    deployment(state, "hello", agent)
    docker.install(FakeDocker(error=DeploymentError("Cannot reach the Docker engine")))
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith(
        "[DOCKER_UNAVAILABLE] Cannot reach the Docker engine"
    )


def test_a_container_with_other_labels_is_not_called(state, agent, docker, capsys):
    deployment(state, "hello", agent)
    fake = docker.install(FakeDocker(endpoint=agent))

    def mismatch(record):
        raise DeploymentError("Container ownership does not match; no changes made")

    fake.inspect = mismatch
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[CONTAINER_MISMATCH]")
    assert Agent.seen == []


@pytest.mark.parametrize("state_name", ["pending", "failed"])
def test_not_ready_is_refused(state, agent, docker, capsys, state_name):
    deployment(state, "hello", agent, state=state_name)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[NOT_READY]")


def test_removed_or_missing_deployments(state, agent, docker, capsys):
    deployment(state, "other", agent)
    deployment(state, "hello", agent, state="removed")
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[NOT_DEPLOYED]")
    assert run(state, "hello", "hi", "--candidate") == 2
    assert "candidate exists only" in capsys.readouterr().err


def test_missing_state_is_not_created(tmp_path, docker, capsys):
    docker.install(FakeDocker())
    missing = tmp_path / "nowhere"
    assert run(missing, "hello", "hi", "--json") == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[NO_STATE]")
    assert json.loads(captured.out)["error"]["code"] == "NO_STATE"
    assert not missing.exists()


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root"
)
def test_an_unsearchable_state_dir_is_coded_not_a_traceback(tmp_path, docker, capsys):
    docker.install(FakeDocker())
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        assert run(locked / "state", "hello", "hi", "--json") == 2
    finally:
        locked.chmod(0o700)
    captured = capsys.readouterr()
    assert captured.err.startswith("[STATE_UNREADABLE] cannot read the AgentBreeder state at ")
    assert "(permission denied)" in captured.err
    assert "Traceback" not in captured.err
    error = json.loads(captured.out)["error"]
    assert (error["code"], error["sent"]) == ("STATE_UNREADABLE", False)
    assert not (locked / "state").exists()


def test_an_io_error_on_the_state_file_is_coded(state, docker, capsys, monkeypatch):
    import errno

    docker.install(FakeDocker())
    real = os.stat

    def failing(path, *args, **kwargs):
        if str(path).endswith("deployments.sqlite3"):
            raise OSError(errno.EIO, "Input/output error")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", failing)
    assert run(state, "hello", "hi") == 2
    err = capsys.readouterr().err
    assert err.startswith("[STATE_UNREADABLE]")
    assert err.rstrip().endswith("(EIO)")
    assert "Input/output" not in err  # the errno name, never the OS message


def test_what_is_file_called_missing_stays_no_state(tmp_path, docker, capsys, monkeypatch):
    docker.install(FakeDocker())
    loop = tmp_path / "deployments.sqlite3"
    loop.symlink_to(loop.name)  # ELOOP, which pathlib's is_file ignores
    assert run(tmp_path, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[NO_STATE]")
    real = os.stat

    def nul(path, *args, **kwargs):  # what os.stat does with a NUL (e.g. from a stack config)
        if str(path).endswith("deployments.sqlite3"):
            raise ValueError("embedded null byte")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", nul)
    assert run(tmp_path / "x", "hello", "hi", "--json") == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "NO_STATE"


def test_a_state_path_that_is_not_a_file_is_no_state(tmp_path, docker, capsys):
    docker.install(FakeDocker())
    (tmp_path / "deployments.sqlite3").mkdir()
    assert run(tmp_path, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[NO_STATE]")
    blocked = tmp_path / "file"
    blocked.write_text("")
    assert run(blocked, "hello", "hi") == 2  # a file where the directory should be: ENOTDIR
    assert capsys.readouterr().err.startswith("[NO_STATE]")


def test_a_state_read_error_while_verifying_is_coded_not_sent(
    state, agent, docker, capsys, monkeypatch
):
    import sqlite3

    from agentbreeder.store import Store

    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))

    def locked(self, identifier):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Store, "manifest", locked)
    assert run(state, "hello", "hi", "--json") == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert (error["code"], error["sent"]) == ("STATE_UNREADABLE", False)
    assert Agent.seen == []


def test_state_problems_are_coded_and_json(state, agent, docker, capsys, monkeypatch):
    from agentbreeder import store as store_module

    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    (state / "deployments.sqlite3").chmod(0o644)  # not private
    assert run(state, "hello", "hi", "--json") == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert (error["code"], error["sent"]) == ("STATE_UNREADABLE", False)
    (state / "deployments.sqlite3").chmod(0o600)

    def old(directory):
        raise store_module.StateNeedsMigration("The local state uses schema v2; run migrate")

    monkeypatch.setattr(store_module.Store, "open_readonly", staticmethod(old))
    assert run(state, "hello", "hi", "--json") == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "STATE_NEEDS_MIGRATION"

    def busy(directory):
        raise store_module.StateBusy("Another AgentBreeder operation is writing")

    monkeypatch.setattr(store_module.Store, "open_readonly", staticmethod(busy))
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[STATE_BUSY]")


def test_the_state_is_opened_read_only(state, agent, docker, capsys):
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    before = {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()}
    assert run(state, "hello", "hi") == 0
    after = {p.name: p.read_bytes() for p in state.iterdir() if p.is_file()}
    assert after == before


def test_a_dead_port_is_unreachable(state, docker, capsys):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{probe.getsockname()[1]}"
    deployment(state, "hello", dead)
    docker.install(FakeDocker(endpoint=dead))
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[UNREACHABLE]")


def test_timeout_is_reported(monkeypatch):
    import socket

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()  # accepts, never answers
        endpoint = f"http://127.0.0.1:{server.getsockname()[1]}"
        with pytest.raises(invoke.InvokeError) as error:
            invoke.call(endpoint, "hi", timeout=0.5)
    assert error.value.code == "TIMEOUT" and error.value.sent
    assert invoke.TIMEOUT == 180


# -- input checks ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["Bad_Name", "hi"], "AGENT_NAME"),
        (["hello", "   "], "MESSAGE_INVALID"),
        (["hello", "x" * (invoke.MAX_MESSAGE + 1)], "MESSAGE_TOO_LONG"),
        (["hello", "hi", "--customer", "a b"], "ID_INVALID"),
        (["hello", "hi", "--session", "x" * 65], "ID_INVALID"),
    ],
)
def test_input_is_checked_before_state_or_docker(state, docker, capsys, argv, code):
    fake = docker.install(FakeDocker())
    assert run(state, *argv) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith(f"[{code}]") and MARKER not in captured.err
    assert fake.calls == [] and not state.exists()


def test_stdin_limits(monkeypatch):
    big = io.TextIOWrapper(io.BytesIO(b"x" * (invoke.MAX_STDIN + 1)))
    with pytest.raises(invoke.InvokeError) as error:
        invoke.message_from("-", big)
    assert error.value.code == "MESSAGE_TOO_LONG"
    bad = io.TextIOWrapper(io.BytesIO(b"\xff\xfe"))
    with pytest.raises(invoke.InvokeError) as error:
        invoke.message_from("-", bad)
    assert error.value.code == "MESSAGE_INVALID"
    assert invoke.message_from("-", io.StringIO(" text ")) == "text"


def test_call_refuses_a_non_loopback_endpoint_itself():
    with pytest.raises(invoke.InvokeError) as error:
        invoke.call("http://example.com:80", "hi")
    assert error.value.code == "ENDPOINT_REFUSED"


def test_environment_needs_stack(state, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["invoke", "hello", "hi", "--environment", "dev"])
    assert error.value.code == 2
    assert "--environment needs --stack" in capsys.readouterr().err
    with pytest.raises(SystemExit) as error:
        cli.main(["invoke", "hello", "hi", "--stack", str(state), "--state-dir", str(state)])
    assert error.value.code == 2
    assert "not both" in capsys.readouterr().err


# -- next ---------------------------------------------------------------------------------


def test_next_suggests_invoke_now_that_it_ships():
    assert guide_checks._available("invoke")
    args = cli._parser().parse_args(
        ["invoke", "hello", "Hello", "--stack", "/p", "--environment", "dev"]
    )
    assert (args.command, args.name, args.message, args.environment) == (
        "invoke",
        "hello",
        "Hello",
        "dev",
    )


# -- review round 1 --------------------------------------------------------------------------


def raw_server(chunks, delay=0.0, reset=False):
    """A loopback server that answers one request with raw bytes (optionally slowly), then
    closes, or with `reset` aborts the connection (RST) after sending."""
    import socket
    import struct
    import time

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()

    def serve():
        try:
            connection, _ = server.accept()
        except OSError:
            return
        with connection:
            try:
                # Read the whole request first: closing with unread bytes sends a reset, which
                # the client would see as CONNECTION_LOST instead of the answer under test.
                data = b""
                while b"\r\n\r\n" not in data:
                    data += connection.recv(65536)
                head, _, body = data.partition(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                while len(body) < length:
                    body += connection.recv(65536)
                for chunk in chunks:
                    connection.sendall(chunk)
                    time.sleep(delay)
                if reset:
                    time.sleep(0.05)  # let the client read what was sent first
                    linger = struct.pack("ii", 1, 0)
                    connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    return server, f"http://127.0.0.1:{server.getsockname()[1]}"


@pytest.mark.parametrize(
    "chunks",
    [
        [b"garbage\r\n\r\n"],  # BadStatusLine
        [
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"zz\r\nnot a chunk\r\n",
        ],  # IncompleteRead / bad chunk
        [b'HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n{"reply":'],  # truncated
        [b"HTTP/1.1 200 OK\r\n" + b"X: y\r\n" * 120 + b"\r\n{}"],  # too many headers
    ],
)
def test_malformed_http_is_response_invalid_not_a_traceback(chunks):
    server, endpoint = raw_server(chunks)
    with server, pytest.raises(invoke.InvokeError) as error:
        invoke.call(endpoint, "hi", timeout=5)
    assert error.value.code == "RESPONSE_INVALID" and error.value.sent


@pytest.mark.parametrize(
    ("chunks", "reset", "code"),
    [
        (
            [b"HTTP/1.1 500 Oops\r\nTransfer-Encoding: chunked\r\n\r\n", b"zz\r\nbad\r\n"],
            False,
            "RESPONSE_INVALID",
        ),
        (
            [b"HTTP/1.1 500 Oops\r\nContent-Length: 100\r\n\r\n{"],
            True,
            "CONNECTION_LOST",
        ),
    ],
)
def test_a_broken_error_body_is_sent_not_a_state_error(state, docker, capsys, chunks, reset, code):
    """Reading a 4xx/5xx body is guarded like a 200's: the agent got the message (exit 1),
    never `run_stateful`'s "Cannot access local state" (exit 2)."""
    server, endpoint = raw_server(chunks, reset=reset)
    with server:
        deployment(state, "hello", endpoint)
        docker.install(FakeDocker(endpoint=endpoint))
        assert run(state, "hello", "hi", "--json") == 1
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert error["sent"] is True and error["code"] in ("RESPONSE_INVALID", "CONNECTION_LOST")
    assert error["code"] == code or reset  # a reset may surface as either read error
    assert "local state" not in captured.err


def test_deeply_nested_json_is_response_invalid(state, agent, docker, capsys):
    Agent.answer = (200, b'{"a":' * 100_000)
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 1
    assert capsys.readouterr().err.startswith("[RESPONSE_INVALID]")


def test_lone_surrogates_are_escaped_not_a_crash(state, agent, docker, capsys):
    Agent.answer = (200, b'{"reply": "ok \\ud800 done", "session_id": "s"}')
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 0
    assert capsys.readouterr().out == "ok \\ud800 done\n"
    Agent.answer = (500, b'{"error": "bad \\udfff"}')
    assert run(state, "hello", "hi") == 1
    assert "\udfff" not in capsys.readouterr().err


def test_the_deadline_covers_the_whole_exchange():
    """urllib's timeout is per socket read; a slow-drip answer must not extend it."""
    import time

    head = b"HTTP/1.1 200 OK\r\nContent-Length: 200\r\n\r\n"
    server, endpoint = raw_server([head] + [b" "] * 200, delay=0.1)
    started = time.monotonic()
    with server, pytest.raises(invoke.InvokeError) as error:
        invoke.call(endpoint, "hi", timeout=1)
    assert error.value.code == "TIMEOUT" and error.value.sent
    assert time.monotonic() - started < 5  # 200 × 0.1 s would be 20 s


def test_the_reply_is_trimmed(state, agent, docker, capsys):
    Agent.answer = (200, {"reply": "\n  hello there \n\n", "session_id": "s"})
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 0
    assert capsys.readouterr().out == "hello there\n"


def test_tampered_state_values_are_not_echoed_raw(state, agent, docker, capsys):
    deployment(state, "hello", agent, state="pending\x1b[2J", identifier="id\x1b]52;c;x\x07")
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 2
    err = capsys.readouterr().err
    assert err.startswith("[NOT_READY]") and "\x1b" not in err and "\x07" not in err


def test_an_invalid_recorded_manifest_is_state_invalid(state, agent, docker, capsys):
    import sqlite3

    identifier = deployment(state, "hello", agent)
    with sqlite3.connect(state / "deployments.sqlite3") as db:
        db.execute("UPDATE deployments SET manifest=? WHERE id=?", ('{"name": 1}', identifier))
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 2
    assert capsys.readouterr().err.startswith("[STATE_INVALID]")
    assert Agent.seen == []


def test_local_wins_over_kubernetes_for_the_same_name(state, agent, docker, capsys):
    deployment(state, "hello", "kubernetes://ns/svc", target="kubernetes")
    deployment(state, "hello", agent)
    docker.install(FakeDocker(endpoint=agent))
    assert run(state, "hello", "hi") == 0
    assert Agent.seen


def _hint(err: str) -> list[str]:
    import shlex

    command = err.split("`")[1]
    words = shlex.split(command)
    return words[next(i for i, w in enumerate(words) if w in ("status", "forward", "next")) :]


def test_hints_carry_a_custom_state_dir(state, agent, docker, capsys):
    identifier = deployment(state, "hello", agent)
    fake = docker.install(FakeDocker(endpoint=agent))

    def stopped(info, manifest):
        raise DeploymentError("Container is not running")

    fake.endpoint = stopped
    assert run(state, "hello", "hi") == 2
    args = cli._parser().parse_args(_hint(capsys.readouterr().err))
    assert (args.command, args.id, args.state_dir) == ("status", identifier, state)


def test_forward_hint_carries_the_state_dir(state, docker, capsys):
    identifier = deployment(state, "hello", "kubernetes://ns/svc", target="kubernetes")
    docker.install(FakeDocker())
    assert run(state, "hello", "hi") == 2
    args = cli._parser().parse_args(_hint(capsys.readouterr().err))
    assert (args.command, args.id, args.state_dir) == ("forward", identifier, state)


def test_with_stack_hints_carry_the_stack_and_the_state_resolves(
    tmp_path, agent, docker, capsys, monkeypatch
):
    from agentbreeder.services import engine

    project = tmp_path / "project"
    project.mkdir()
    resolved_state = tmp_path / "stack-state"
    identifier = deployment(resolved_state, "hello", agent)
    operator = tmp_path / "operator.yaml"
    operator.write_text("environments: {}\n")
    seen = []

    def context(directory, environment, root=None):
        seen.append((directory, environment))
        return SimpleNamespace(
            operator_config=operator,
            state_dir=resolved_state,
            environment=environment or "dev",
            stack_id="hello-1234",
        )

    monkeypatch.setattr(engine, "context", context)
    fake = docker.install(FakeDocker(endpoint=agent))
    argv = ["invoke", "hello", "hi", "--stack", str(project), "--environment", "dev"]
    assert cli.main(argv) == 0
    assert seen == [(project, "dev")] and Agent.seen
    assert "Support is open" in capsys.readouterr().out

    def stopped(info, manifest):
        raise DeploymentError("Container is not running")

    fake.endpoint = stopped
    assert cli.main(argv) == 2
    args = cli._parser().parse_args(_hint(capsys.readouterr().err))
    assert (args.command, args.id, args.stack, args.environment) == (
        "status",
        identifier,
        project,
        "dev",
    )
    assert (
        cli.main(["invoke", "nothere", "hi", "--stack", str(project), "--environment", "dev"]) == 2
    )
    args = cli._parser().parse_args(_hint(capsys.readouterr().err))
    assert (args.command, args.project, args.environment) == ("next", project, "dev")


def test_limits_match_the_runtime():
    import re
    from pathlib import Path

    from agentbreeder import registry

    server = (
        Path(registry.BUILTIN_ROOT) / "runtime" / "adk" / "1.0.6" / "runtime" / "server.py"
    ).read_text()
    assert f"MAX_MESSAGE = {invoke.MAX_MESSAGE}\n" in server
    assert re.search(r'ID = re\.compile\(r"\^\[A-Za-z0-9_-\]\{1,64\}\$"\)', server)
    assert invoke.ID.pattern == "[A-Za-z0-9_-]{1,64}"
