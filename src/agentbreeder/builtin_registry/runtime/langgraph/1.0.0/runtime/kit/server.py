"""Agent protocol v1 over HTTP, with the kit profile's additions (spec §10.1, §10.2):
GET /health and POST /invoke (`docs/agent-protocol.md`).

As `runtime/adk@1.3.0`'s `server.py` for every v1 rule (body and id checks, the Host check,
JSON only, the fixed error bodies, the coded model errors), plus the kit profile below. Its
log lines are stricter than 1.3.0's: a failed turn is logged by exception type only (spec
§13.8), never its message. The kit profile:
- `/health` is 503 while the agent is being built, 503 with a fixed body after a failed start,
  and 200 only when the agent is ready (1.3.0 answers 200 as soon as it listens);
- a turn on a session that is still running one is 409 at once (1.3.0 waits for it);
- on SIGTERM, new turns get 503 while the turns in flight finish or time out within DRAIN_GRACE
  (Docker's stop grace is 10 s), then the proxy closes;
- `MODEL_STEP_LIMIT` (502) when the turn used its model calls (`proxy.decide`).

A runtime's entry point calls `main(adapter_factory, runtime_ref=..., framework=...)`.
The chat page 1.3.0 serves at `GET /` is not part of the protocol and is not served here.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import os
import re
import signal
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

from . import config as cfg
from . import errors, logs, redaction, telemetry
from .errors import ModelError
from .memory import ANONYMOUS

MAX_BODY = 64 * 1024
MAX_MESSAGE = 4000
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CANCEL_GRACE = 120  # seconds to wait for a cancelled turn (and any in-flight tool) to stop
DRAIN_GRACE = 8.0  # seconds a SIGTERM waits for the turns in flight
CLOSE_TIMEOUT = 1.5
BUSY = "This conversation is still finishing a previous turn; retry shortly"


class SessionBusy(Exception):
    """A turn for this (customer, session) is still running."""


class Draining(Exception):
    """SIGTERM arrived: no new turn starts."""


class App:
    """The process's agent: its readiness, its event loop and its turns."""

    def __init__(self, build: Callable[[], object], turn_timeout: float = errors.TURN_TIMEOUT):
        self.build = build  # () -> Kit, called on the start thread
        self.turn_timeout = turn_timeout
        self.kit = None
        self.status = "starting"  # starting | ready | failed
        self.draining = False
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="kit-loop", daemon=True)
        self.thread.start()
        self.locks: set[tuple[str, str]] = set()  # (customer, session) with a turn running
        self.guard = threading.Lock()
        self.inflight = 0
        self.idle = threading.Condition(self.guard)

    def start(self) -> None:
        """Build the agent: config, tools, knowledge, the proxy and the framework adapter. The
        reason for a failure goes to the log as its type only; /health says it failed."""
        try:
            kit = self.build()
            asyncio.run_coroutine_threadsafe(kit.start(), self.loop).result(60)
        except Exception as error:  # noqa: BLE001 - reported by /health, logged as a type
            self.status = "failed"
            print(f"agent start failed: {type(error).__name__}", flush=True)
            return
        self.kit = kit
        self.status = "ready"

    @contextlib.contextmanager
    def session(self, customer: str, session: str):
        key = (customer, session)
        with self.guard:
            # Under the guard: a turn cannot start after `drain` has counted none in flight.
            if self.draining:
                raise Draining()
            if key in self.locks:
                raise SessionBusy(session)
            self.locks.add(key)
            self.inflight += 1
        try:
            yield
        finally:
            with self.guard:
                self.inflight -= 1
                self.idle.notify_all()

    def release(self, customer: str, session: str) -> None:
        with self.guard:
            self.locks.discard((customer, session))

    def chat(self, session: str, customer: str, message: str) -> dict:
        """Run one turn (from an HTTP handler thread). On a timeout the turn is cancelled and
        the session stays busy until its coroutine has really unwound."""
        finished = threading.Event()

        async def turn():
            try:
                return await self.kit.chat_async(session, customer, message)
            finally:
                self.release(customer, session)
                finished.set()

        try:
            future = asyncio.run_coroutine_threadsafe(turn(), self.loop)
        except BaseException:
            self.release(customer, session)
            raise
        try:
            return future.result(timeout=self.turn_timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            finished.wait(CANCEL_GRACE)
            raise

    def drain(self, grace: float = DRAIN_GRACE) -> None:
        """Refuse new turns, wait for the ones in flight (bounded), close the agent."""
        with self.guard:
            self.draining = True
        deadline = time.monotonic() + grace
        with self.guard:
            while self.inflight and time.monotonic() < deadline:
                self.idle.wait(max(0.0, deadline - time.monotonic()))
        if self.kit is not None:
            try:
                asyncio.run_coroutine_threadsafe(self.kit.close(), self.loop).result(CLOSE_TIMEOUT)
            except Exception as error:  # noqa: BLE001 - shutting down regardless
                print(f"closing the agent failed: {type(error).__name__}", flush=True)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(180)

        def send(self, status: int, body: bytes, kind: str, headers: dict | None = None) -> None:
            self.send_response(status)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def respond(self, status: int, body: dict, headers: dict | None = None) -> None:
            self.send(status, json.dumps(body).encode(), "application/json", headers)

        def local_host(self) -> bool:
            # The port is published on loopback only; rejecting other Host values blocks
            # DNS-rebinding pages from driving the agent from a browser.
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            return host in ("127.0.0.1", "localhost", "agent")

        def do_GET(self) -> None:
            if self.path != "/health":
                self.respond(404, {"error": "Not found"})
            elif app.status == "ready" and not app.draining:
                self.respond(200, {"status": "ok", "model": app.kit.alias})
            elif app.status == "failed":
                self.respond(
                    503, {"status": "failed", "error": "The agent failed to start; see its log."}
                )
            else:
                self.respond(503, {"status": "stopping" if app.draining else "starting"})

        def do_POST(self) -> None:
            if self.path != "/invoke" or not self.local_host():
                self.respond(404, {"error": "Not found"})
                return
            # Requiring JSON forces a CORS preflight, which this server never approves, so other
            # websites cannot spend tokens through a visitor's browser.
            if (self.headers.get("Content-Type") or "").split(";")[
                0
            ].strip() != "application/json":
                self.respond(415, {"error": "Content-Type must be application/json"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = -1
            if not 0 < size <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                self.respond(400, {"error": "A JSON body of at most 64 KiB is required"})
                return
            try:
                payload = json.loads(self.rfile.read(size))
                message = payload["message"]
                if not isinstance(message, str) or not message.strip():
                    raise ValueError
                session = payload.get("session_id") or uuid.uuid4().hex
                customer = payload.get("customer_id") or ANONYMOUS
                if not (
                    isinstance(session, str)
                    and isinstance(customer, str)
                    and ID.match(session)
                    and ID.match(customer)
                ):
                    raise ValueError
            except (ValueError, KeyError, TypeError, UnicodeError):
                self.respond(
                    400,
                    {"error": 'Send {"message": str, "session_id"?: id, "customer_id"?: id}'},
                )
                return
            if len(message) > MAX_MESSAGE:
                self.respond(413, {"error": f"Messages are limited to {MAX_MESSAGE} characters"})
                return
            if app.draining:
                self.respond(503, {"error": "The agent is stopping; please try again shortly"})
                return
            if app.status != "ready":
                self.respond(503, {"error": "The agent is not ready; see /health"})
                return
            try:
                with app.session(customer, session):
                    self.respond(200, app.chat(session, customer, message.strip()))
            except Draining:
                self.respond(503, {"error": "The agent is stopping; please try again shortly"})
            except SessionBusy:
                self.respond(409, {"error": BUSY})
            except concurrent.futures.TimeoutError:
                self.respond(504, {"error": "The agent took too long to answer; please try again"})
            except psycopg.OperationalError:
                self.respond(
                    503, {"error": "Conversation database unavailable; is Postgres running?"}
                )
            except cfg.ConfigError:
                self.respond(503, {"error": "Agent is misconfigured; see its log."})
            except ModelError as error:
                self.respond(*errors.model_error_response(error))
            except Exception as error:  # noqa: BLE001 - one redacted line, never a traceback
                # Its type only: the text of a framework's exception may hold message content.
                print(redaction.one_line(f"turn failed: {type(error).__name__}"), flush=True)
                self.respond(500, {"error": "The agent failed to answer this turn; see its log."})

        def log_message(self, format: str, *args) -> None:
            pass  # never log customer messages

    return Handler


def main(
    adapter_factory: Callable[[], object],
    *,
    runtime_ref: str,
    framework: str,
    loggers: tuple[str, ...] = (),
) -> None:
    """Serve the agent: build it in the background, answer /health and /invoke, drain on
    SIGTERM. `loggers` are the framework's logger names (`logs.quiet`)."""
    from .turn import Kit

    logs.quiet(loggers)

    def build():
        config = cfg.config()
        provider = telemetry.setup(
            config["name"],
            cfg.service("traces_url"),
            enabled=config.get("traces", "otlp") == "otlp",
        )
        adapter = adapter_factory()
        instrument = getattr(adapter, "instrument", None)
        if instrument is not None:
            instrument(provider)
        return Kit(
            config,
            cfg.secret("DATABASE_URL"),
            cfg.secret("LLM_GATEWAY_KEY"),
            adapter,
            runtime_ref=runtime_ref,
            framework=framework,
        )

    app = App(build)
    server = ThreadingHTTPServer(
        (os.getenv("HOST", "0.0.0.0"), int(os.getenv("PORT", "8080"))), make_handler(app)
    )
    threading.Thread(target=app.start, name="start", daemon=True).start()

    def stop():
        try:
            app.drain()
        finally:
            server.shutdown()

    def on_sigterm(signum, frame):
        # On its own thread: the signal arrives on the main thread, inside serve_forever(), and
        # shutdown() from that thread would deadlock.
        threading.Thread(target=stop, name="shutdown", daemon=True).start()

    signal.signal(signal.SIGTERM, on_sigterm)
    server.serve_forever()
    server.server_close()
