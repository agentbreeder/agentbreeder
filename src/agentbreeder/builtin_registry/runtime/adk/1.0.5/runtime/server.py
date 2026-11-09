"""HTTP front end: GET /health, GET / (chat page), POST /invoke."""

from __future__ import annotations

import concurrent.futures
import contextlib
import html
import json
import os
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfg  # noqa: E402
import psycopg  # noqa: E402
import sqlalchemy.exc  # noqa: E402
from agent import ModelError, SessionBusy, chat  # noqa: E402
from memory import ANONYMOUS  # noqa: E402

MAX_BODY = 64 * 1024
MAX_MESSAGE = 4000
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# session id -> [lock, holders]; an entry is dropped when its last holder leaves, so a stream of
# new session ids can't grow this without bound.
LOCKS: dict[str, list] = {}
_LOCKS_GUARD = threading.Lock()


@contextlib.contextmanager
def session_lock(session: str, blocking: bool = True):
    with _LOCKS_GUARD:
        entry = LOCKS.setdefault(session, [threading.Lock(), 0])
        entry[1] += 1
    try:
        if not entry[0].acquire(blocking=blocking):
            raise SessionBusy(session)
        try:
            yield
        finally:
            entry[0].release()
    finally:
        with _LOCKS_GUARD:
            entry[1] -= 1
            if entry[1] == 0:
                del LOCKS[session]


def page() -> bytes:
    config = cfg.config()
    title = config["name"].replace("-", " ").title()
    text = (Path(__file__).parent / "chat.html").read_text(encoding="utf-8")
    text = text.replace("{{AGENT_TITLE}}", html.escape(title)).replace(
        "{{AGENT_DESCRIPTION}}", html.escape(config.get("description", ""))
    )
    return text.encode()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(180)

    def send(self, status: int, body: bytes, kind: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def respond(self, status: int, body: dict) -> None:
        self.send(status, json.dumps(body).encode(), "application/json")

    def local_host(self) -> bool:
        # The port is published on loopback only; rejecting other Host values blocks
        # DNS-rebinding pages from driving the agent from a browser.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost", "agent")

    def do_GET(self) -> None:
        if self.path == "/health":
            self.respond(200, {"status": "ok", "model": cfg.config()["model"]["alias"]})
        elif self.path in ("/", "/index.html") and self.local_host():
            self.send(200, PAGE, "text/html; charset=utf-8")
        else:
            self.respond(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path != "/invoke" or not self.local_host():
            self.respond(404, {"error": "Not found"})
            return
        # Requiring JSON forces a CORS preflight, which this server never approves, so other
        # websites cannot spend tokens through a visitor's browser.
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
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
            if not (ID.match(session) and ID.match(customer)):
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
        try:
            with session_lock(session):
                self.respond(200, chat(session, customer, message.strip()))
        except SessionBusy:
            self.respond(
                409,
                {"error": "This conversation is still finishing a previous turn; retry shortly"},
            )
        except concurrent.futures.TimeoutError:
            self.respond(504, {"error": "The agent took too long to answer; please try again"})
        except (
            psycopg.OperationalError,
            sqlalchemy.exc.OperationalError,
            sqlalchemy.exc.InterfaceError,
        ):
            self.respond(503, {"error": "Conversation database unavailable; is Postgres running?"})
        except cfg.ConfigError as error:
            self.respond(503, {"error": f"Agent is misconfigured: {error}"})
        except ModelError as error:
            status = 429 if error.status == 429 else 502
            self.respond(status, {"error": f"Model API error {error.status}: {error}"})

    def log_message(self, format: str, *args) -> None:
        pass  # never log customer messages


PAGE = page()


def warm() -> None:
    """Build the runtime (and sync knowledge) in the background so the first turn is fast.
    Failures here are retried on the first request, which reports them."""
    try:
        import agent

        agent.runtime()
    except Exception as error:  # noqa: BLE001 - logged, retried lazily
        print(f"runtime warm-up failed: {type(error).__name__}", flush=True)


if __name__ == "__main__":
    threading.Thread(target=warm, name="warm-up", daemon=True).start()
    server = ThreadingHTTPServer(
        (os.getenv("HOST", "0.0.0.0"), int(os.getenv("PORT", "8080"))), Handler
    )
    server.serve_forever()
