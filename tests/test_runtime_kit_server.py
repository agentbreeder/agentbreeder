"""The runtime kit's protocol v1 server and its kit-profile additions (#1083 F2b, spec §10.2):
/health readiness, 409 at once for a busy session, the SIGTERM drain, coded errors."""

from __future__ import annotations

import asyncio
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import psycopg
import pytest

from agentbreeder.runtime_kit import config as cfg
from agentbreeder.runtime_kit import server as kit_server
from agentbreeder.runtime_kit.errors import ModelError


class FakeKit:
    alias = "support-model"

    def __init__(self, behaviour=None):
        self.behaviour = behaviour or (lambda session, customer, message: {"reply": message})
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def chat_async(self, session, customer, message):
        result = self.behaviour(session, customer, message)
        if asyncio.iscoroutine(result):
            result = await result
        return result


@pytest.fixture
def serve():
    servers = []

    def start(kit=None, build=None, status="ready", turn_timeout=5.0):
        app = kit_server.App(build or (lambda: kit), turn_timeout=turn_timeout)
        if status == "ready":
            app.start()
        else:
            app.status = status
        server = ThreadingHTTPServer(("127.0.0.1", 0), kit_server.make_handler(app))
        threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True).start()
        servers.append((server, app))
        return app, server.server_address[1]

    yield start
    for server, app in servers:
        server.shutdown()
        server.server_close()
        app.loop.call_soon_threadsafe(app.loop.stop)


def request(port, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    head = {"Host": "localhost", "Content-Type": "application/json"}
    head.update(headers or {})
    connection.request(method, path, body=data, headers=head)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    try:
        parsed = json.loads(payload)
    except ValueError:
        parsed = None
    return response.status, parsed, dict(response.getheaders())


def test_health_is_200_only_when_ready(serve):
    app, port = serve(FakeKit())
    assert request(port, "GET", "/health")[:2] == (200, {"status": "ok", "model": "support-model"})
    app.draining = True
    assert request(port, "GET", "/health")[:2] == (503, {"status": "stopping"})


def test_health_while_starting_and_after_a_failed_start(serve, capsys):
    _, port = serve(status="starting")
    assert request(port, "GET", "/health")[:2] == (503, {"status": "starting"})
    assert request(port, "POST", "/invoke", {"message": "hi"})[0] == 503

    def broken():
        raise cfg.ConfigError("secret at /run/secrets/x unreadable")

    app, port = serve(build=broken)
    status, body, _ = request(port, "GET", "/health")
    assert status == 503 and body["status"] == "failed"
    assert "secrets" not in json.dumps(body)
    assert "agent start failed: ConfigError" in capsys.readouterr().out
    assert app.status == "failed"


def test_invoke_runs_a_turn(serve):
    kit = FakeKit(lambda s, c, m: {"reply": m, "session_id": s, "customer_id": c})
    _, port = serve(kit)
    status, body, headers = request(
        port, "POST", "/invoke", {"message": "  hi  ", "session_id": "s1"}
    )
    assert status == 200 and body == {"reply": "hi", "session_id": "s1", "customer_id": "guest"}
    assert headers["Cache-Control"] == "no-store"
    status, body, _ = request(port, "POST", "/invoke", {"message": "hi"})
    assert len(body["session_id"]) == 32


@pytest.mark.parametrize(
    "body,headers,status",
    [
        ({"message": "hi"}, {"Host": "evil.example"}, 404),
        ({"message": "hi"}, {"Content-Type": "text/plain"}, 415),
        ({"message": ""}, {}, 400),
        ({"message": 5}, {}, 400),
        ({"message": "hi", "session_id": "bad id"}, {}, 400),
        ({"message": "hi", "customer_id": ["x"]}, {}, 400),
        ({"nothing": 1}, {}, 400),
        (b"not json", {}, 400),
        ({"message": "x" * 4001}, {}, 413),
        (b"x" * (64 * 1024 + 1), {}, 400),
    ],
)
def test_v1_request_rules(serve, body, headers, status):
    _, port = serve(FakeKit())
    assert request(port, "POST", "/invoke", body, headers)[0] == status


def test_other_paths_are_404(serve):
    _, port = serve(FakeKit())
    assert request(port, "GET", "/")[0] == 404
    assert request(port, "POST", "/other", {"message": "hi"})[0] == 404


def test_a_busy_session_is_409_at_once(serve):
    release = threading.Event()

    async def slow(session, customer, message):
        await asyncio.get_running_loop().run_in_executor(None, release.wait, 5)
        return {"reply": "done"}

    app, port = serve(FakeKit(slow))
    first: list = []
    worker = threading.Thread(
        target=lambda: first.append(
            request(port, "POST", "/invoke", {"message": "a", "session_id": "s"})
        )
    )
    worker.start()
    while ("guest", "s") not in app.locks:
        threading.Event().wait(0.01)
    assert request(port, "POST", "/invoke", {"message": "b", "session_id": "s"})[0] == 409
    # Another customer with the same session id is a different conversation.
    other = threading.Thread(
        target=lambda: first.append(
            request(
                port, "POST", "/invoke", {"message": "c", "session_id": "s", "customer_id": "x"}
            )
        )
    )
    other.start()
    threading.Event().wait(0.1)
    release.set()
    worker.join(5)
    other.join(5)
    assert sorted(r[0] for r in first) == [200, 200]


@pytest.mark.parametrize(
    "raised,status,code",
    [
        (ModelError("MODEL_RATE_LIMITED", 30), 429, "MODEL_RATE_LIMITED"),
        (ModelError("MODEL_STEP_LIMIT"), 502, "MODEL_STEP_LIMIT"),
        (ModelError("MODEL_BUDGET_EXCEEDED"), 402, "MODEL_BUDGET_EXCEEDED"),
        (ModelError("SOMETHING_ELSE"), 502, "MODEL_UNAVAILABLE"),
    ],
)
def test_model_errors_are_coded(serve, raised, status, code):
    def fail(s, c, m):
        raised.turn_id, raised.trace_id = "turn-1", "a" * 32
        raise raised

    _, port = serve(FakeKit(fail))
    got, body, headers = request(port, "POST", "/invoke", {"message": "hi"})
    assert got == status and body["code"] == code and body["correlation_id"] == "turn-1"
    if code == "MODEL_RATE_LIMITED":
        assert headers["Retry-After"] == "30" and body["retry_after_s"] == 30
    else:
        assert "Retry-After" not in headers and body["retry_after_s"] is None


def test_other_failures(serve, capsys):
    cases = [
        (psycopg.OperationalError("db at 10.0.0.1 down"), 503),
        (cfg.ConfigError("bad"), 503),
        (ValueError("customer said my SSN is 123"), 500),
    ]
    for error, status in cases:

        def fail(s, c, m, error=error):
            raise error

        _, port = serve(FakeKit(fail))
        got, body, _ = request(port, "POST", "/invoke", {"message": "hi"})
        assert got == status and "10.0.0.1" not in body["error"] and "SSN" not in body["error"]
    log = capsys.readouterr().out
    assert "turn failed: ValueError" in log and "SSN" not in log


def test_a_turn_over_the_deadline_is_504(serve):
    async def slow(s, c, m):
        await asyncio.sleep(2)
        return {"reply": "late"}

    app, port = serve(FakeKit(slow), turn_timeout=0.2)
    assert request(port, "POST", "/invoke", {"message": "hi", "session_id": "s"})[0] == 504
    assert app.locks == set()  # released once the cancelled turn unwound


def test_drain_refuses_new_turns_waits_for_the_running_one_and_closes(serve):
    release = threading.Event()

    async def slow(s, c, m):
        await asyncio.get_running_loop().run_in_executor(None, release.wait, 5)
        return {"reply": "finished"}

    kit = FakeKit(slow)
    app, port = serve(kit)
    results: list = []
    worker = threading.Thread(
        target=lambda: results.append(
            request(port, "POST", "/invoke", {"message": "a", "session_id": "s"})
        )
    )
    worker.start()
    while app.inflight == 0:
        threading.Event().wait(0.01)
    drainer = threading.Thread(target=app.drain, kwargs={"grace": 5})
    drainer.start()
    while not app.draining:
        threading.Event().wait(0.01)
    assert request(port, "POST", "/invoke", {"message": "b", "session_id": "t"})[0] == 503
    release.set()
    worker.join(5)
    drainer.join(5)
    assert results[0][:2] == (200, {"reply": "finished"})
    assert kit.closed


def test_drain_gives_up_after_the_grace_and_survives_a_failing_close(serve, capsys):
    class Unclosable(FakeKit):
        async def close(self):
            raise RuntimeError("stuck")

    app, _ = serve(Unclosable())
    app.inflight = 1  # a turn that never finishes
    app.drain(grace=0.05)
    assert "closing the agent failed: RuntimeError" in capsys.readouterr().out


def test_the_session_is_released_when_scheduling_fails(serve, monkeypatch):
    app, _ = serve(FakeKit())

    def refuse(coroutine, loop):
        coroutine.close()
        raise RuntimeError("loop gone")

    monkeypatch.setattr(kit_server.asyncio, "run_coroutine_threadsafe", refuse)
    with app.session("c", "s"), pytest.raises(RuntimeError):
        app.chat("s", "c", "hi")
    assert app.locks == set()


def test_a_turn_cannot_start_once_draining(serve):
    app, _ = serve(FakeKit())
    app.drain(grace=0.01)
    with pytest.raises(kit_server.Draining), app.session("c", "s"):
        pass
    assert app.inflight == 0
