"""The conformance kit's fakes: a scripted gateway, a span sink and an ingress forwarder.

Standard library only. `agentbreeder conformance` copies this file into a small container that
sits on the internal test network beside the agent (`python -m agentbreeder.conformance.fakes`);
the unit tests start the same `Harness` in process.

Three listeners:

- **the fakes** (what the agent reaches): an OpenAI-shaped gateway (`/chat/completions` and
  `/v1/chat/completions`, `/v1/embeddings`), an Anthropic-shaped one (`/v1/messages`), an OTLP
  span sink (`/v1/traces`) and an MCP relay address that is always down (`/mcp/…`, 503). Every
  request is recorded, the `Authorization` value as a hash only.
- **the control API** (what the check runner reaches, with a bearer token): script the next
  model answers, read back what the agent sent, read the spans, reset (`{"spans": false}`
  keeps the spans).
- **the ingress forwarder**: a plain TCP relay to the agent, because an agent on an internal
  network publishes no port. The bytes are not changed, so the `Host` checks still reach the
  agent as sent.

A scripted answer is a JSON object. Every key is optional:
`status` (200), `reply` (the assistant text), `cost` (the `x-litellm-response-cost` header; null
for none), `headers` (extra response headers), `usage` (`{prompt_tokens, completion_tokens}`),
`delay_s` (wait before answering, at most 30), `error` (`{type, message}`, the body of a failed
call), `tool_calls` (OpenAI `tool_calls` for the assistant message) and `repeat` (keep the entry
for every later call instead of using it once). With nothing scripted the gateway answers 200
with `DEFAULT_REPLY`.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import os
import socket
import socketserver
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_REPLY = "Conformance reply."
DEFAULT_COST = "0.001"
DEFAULT_USAGE = {"prompt_tokens": 10, "completion_tokens": 2}
MAX_BODY = 8 * 1024 * 1024  # one request to a fake
MAX_REQUESTS = 2000  # recorded requests kept
MAX_SPAN_BYTES = 16 * 1024 * 1024  # span payloads kept, in total
MAX_SPAN_PAYLOADS = 2000
MAX_RECORDED_BODY = 256 * 1024  # a larger request body is recorded by its size only
MAX_SERVER_CONNECTIONS = (
    48  # per listener: a peer holding connections open cannot use every thread
)
MAX_SCRIPT = 200
MAX_DELAY = 30.0
MAX_DIMENSIONS = 4096
MAX_CONNECTIONS = 64  # ingress connections relayed at once
SCRIPT_KEYS = frozenset(
    {"status", "reply", "cost", "headers", "usage", "delay_s", "error", "tool_calls", "repeat"}
)
TOKEN_ENV = "AGENTBREEDER_CONFORMANCE_TOKEN"
CONFIG_ENV = "AGENTBREEDER_CONFORMANCE_CONFIG"


class ScriptError(ValueError):
    """A script the control API refuses."""


def check_script(entries: object) -> list[dict]:
    """The script as a list of answer objects, or ScriptError saying what is wrong."""
    if not isinstance(entries, list) or len(entries) > MAX_SCRIPT:
        raise ScriptError(f"entries must be a list of at most {MAX_SCRIPT} objects")
    for entry in entries:
        if not isinstance(entry, dict) or not set(entry) <= SCRIPT_KEYS:
            raise ScriptError("an entry must be an object with only the documented keys")
        status = entry.get("status", 200)
        if isinstance(status, bool) or not isinstance(status, int) or not 200 <= status <= 599:
            raise ScriptError("status must be an integer from 200 to 599")
        delay = entry.get("delay_s", 0)
        if isinstance(delay, bool) or not isinstance(delay, (int, float)):
            raise ScriptError("delay_s must be a number")
        if not 0 <= delay <= MAX_DELAY:
            raise ScriptError(f"delay_s must be from 0 to {MAX_DELAY:g}")
        headers = entry.get("headers", {})
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and "\n" not in k + v and "\r" not in k + v
            for k, v in headers.items()
        ):
            raise ScriptError("headers must be an object of single-line strings")
        cost = entry.get("cost", DEFAULT_COST)
        if cost is not None and (not isinstance(cost, str) or "\n" in cost or "\r" in cost):
            raise ScriptError("cost must be a string or null")
    return entries


def vector(text: str, dimensions: int) -> list[float]:
    """A deterministic embedding: the same text always gets the same vector."""
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        block = hashlib.sha256(f"{counter}\0{text}".encode()).digest()
        values.extend((byte - 127.5) / 127.5 for byte in block)
        counter += 1
    return [round(value, 6) for value in values[:dimensions]]


def _authorization(value: str | None) -> dict | None:
    """What a check may know of a credential: its scheme and the hash of its value."""
    if not value:
        return None
    scheme, _, secret = value.partition(" ")
    return {"scheme": scheme, "sha256": hashlib.sha256(secret.strip().encode()).hexdigest()}


class Fakes:
    """The fakes' state: the script, and what the agent sent."""

    def __init__(self):
        self.lock = threading.Lock()
        self.script: list[dict] = []
        self.requests: list[dict] = []
        self.spans: list[bytes] = []
        self.span_bytes = 0
        self.dropped = 0  # requests or span payloads not kept: a limit was reached

    def reset(self, spans: bool = True) -> None:
        """Forget the script and the recorded requests and, unless `spans` is false, the span
        payloads (the end-of-run checks read every turn's spans)."""
        with self.lock:
            self.script, self.requests = [], []
            if spans:
                self.spans, self.span_bytes, self.dropped = [], 0, 0

    def set_script(self, entries: list[dict]) -> None:
        with self.lock:
            self.script = [dict(entry) for entry in entries]

    def next_answer(self) -> dict:
        with self.lock:
            if not self.script:
                return {}
            return self.script[0] if self.script[0].get("repeat") else self.script.pop(0)

    def record(self, kind: str, method: str, path: str, headers, body: object) -> None:
        kept = {name.lower(): value for name, value in headers.items()}
        for name in ("authorization", "x-api-key"):
            if name in kept:
                value = kept[name]
                kept[name] = _authorization(value if name == "authorization" else "key " + value)
        entry = {"kind": kind, "method": method, "path": path, "headers": kept, "body": body}
        with self.lock:
            if len(self.requests) >= MAX_REQUESTS:
                self.dropped += 1
                return
            entry["n"] = len(self.requests) + 1
            self.requests.append(entry)

    def add_spans(self, payload: bytes) -> None:
        with self.lock:
            over = self.span_bytes + len(payload) > MAX_SPAN_BYTES
            if over or len(self.spans) >= MAX_SPAN_PAYLOADS:
                self.dropped += 1
                return
            self.spans.append(payload)
            self.span_bytes += len(payload)

    def view(self) -> dict:
        with self.lock:
            return {"requests": list(self.requests), "dropped": self.dropped}

    def span_view(self) -> dict:
        with self.lock:
            return {
                "spans": [base64.b64encode(payload).decode() for payload in self.spans],
                "dropped": self.dropped,
            }


def _openai_reply(model: str, answer: dict) -> dict:
    message: dict = {"role": "assistant", "content": answer.get("reply", DEFAULT_REPLY)}
    calls = answer.get("tool_calls")
    if calls:
        message["tool_calls"] = calls
    usage = {**DEFAULT_USAGE, **(answer.get("usage") or {})}
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return {
        "id": "chatcmpl-conformance",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}
        ],
        "usage": usage,
    }


def _anthropic_reply(model: str, answer: dict) -> dict:
    usage = {**DEFAULT_USAGE, **(answer.get("usage") or {})}
    return {
        "id": "msg_conformance",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": answer.get("reply", DEFAULT_REPLY)}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage["prompt_tokens"],
            "output_tokens": usage["completion_tokens"],
        },
    }


def _error_body(answer: dict, status: int, anthropic: bool) -> dict:
    error = answer.get("error") if isinstance(answer.get("error"), dict) else {}
    kind = str(error.get("type", "api_error"))
    message = str(error.get("message", "scripted failure"))
    if anthropic:
        return {"type": "error", "error": {"type": kind, "message": message}}
    return {"error": {"message": message, "type": kind, "param": None, "code": str(status)}}


class _Quiet(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, format: str, *args) -> None:
        pass  # request bodies hold test messages; the record is the control API's

    def reply(self, status: int, body: dict, headers: dict | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> bytes | None:
        """The request body, or None after answering 400 or 413 for one that is not usable."""
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = -1
        if size < 0 or self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self.reply(400, {"error": {"message": "a Content-Length body is required"}})
            return None
        if size > MAX_BODY:
            self.close_connection = True
            self.reply(413, {"error": {"message": "body too large"}})
            return None
        return self.rfile.read(size)


def _fakes_handler(fakes: Fakes):
    class Handler(_Quiet):
        def do_GET(self) -> None:
            fakes.record("other", "GET", self.path, self.headers, None)
            self.reply(404, {"error": {"message": "not found"}})

        def do_DELETE(self) -> None:
            kind = "mcp" if self.path.startswith("/mcp") else "other"
            fakes.record(kind, "DELETE", self.path, self.headers, None)
            self.reply(503 if kind == "mcp" else 404, {"error": {"message": "unavailable"}})

        def do_POST(self) -> None:
            raw = self.body()
            if raw is None:
                return
            path = self.path
            if path == "/v1/traces":
                payload = raw
                if self.headers.get("Content-Encoding", "").lower() == "gzip":
                    try:
                        payload = gzip.GzipFile(fileobj=_Limited(raw)).read(MAX_BODY)
                    except (OSError, EOFError, zlib.error):
                        payload = b""
                fakes.record("traces", "POST", path, self.headers, {"bytes": len(payload)})
                fakes.add_spans(payload)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if len(raw) > MAX_RECORDED_BODY:
                data = {"oversize_bytes": len(raw)}  # kept out of memory and of the record
            else:
                try:
                    data = json.loads(raw) if raw else None
                except (ValueError, RecursionError):
                    data = None
            if path.startswith("/mcp"):
                fakes.record("mcp", "POST", path, self.headers, data)
                self.reply(503, {"error": {"message": "the conformance relay is down"}})
            elif path in ("/chat/completions", "/v1/chat/completions"):
                fakes.record("chat", "POST", path, self.headers, data)
                self.answer(data, anthropic=False)
            elif path == "/v1/messages":
                fakes.record("messages", "POST", path, self.headers, data)
                self.answer(data, anthropic=True)
            elif path in ("/embeddings", "/v1/embeddings"):
                fakes.record("embeddings", "POST", path, self.headers, data)
                self.embed(data)
            else:
                fakes.record("other", "POST", path, self.headers, data)
                self.reply(404, {"error": {"message": "not found"}})

        def answer(self, data: object, anthropic: bool) -> None:
            answer = fakes.next_answer()
            delay = answer.get("delay_s", 0)
            if delay:
                time.sleep(min(float(delay), MAX_DELAY))
            status = answer.get("status", 200)
            headers = dict(answer.get("headers") or {})
            model = data.get("model") if isinstance(data, dict) else None
            model = model if isinstance(model, str) else "conformance-model"
            if status >= 400:
                self.reply(status, _error_body(answer, status, anthropic), headers)
                return
            cost = answer.get("cost", DEFAULT_COST)
            if cost is not None:
                headers.setdefault("x-litellm-response-cost", cost)
            shape = _anthropic_reply if anthropic else _openai_reply
            self.reply(status, shape(model, answer), headers)

        def embed(self, data: object) -> None:
            body = data if isinstance(data, dict) else {}
            texts = body.get("input")
            texts = [texts] if isinstance(texts, str) else texts
            dimensions = body.get("dimensions", 768)
            if (
                not isinstance(texts, list)
                or not all(isinstance(text, str) for text in texts)
                or isinstance(dimensions, bool)
                or not isinstance(dimensions, int)
                or not 1 <= dimensions <= MAX_DIMENSIONS
            ):
                self.reply(400, {"error": {"message": "input and dimensions are required"}})
                return
            self.reply(
                200,
                {
                    "object": "list",
                    "model": str(body.get("model", "conformance-embedding")),
                    "data": [
                        {"object": "embedding", "index": i, "embedding": vector(t, dimensions)}
                        for i, t in enumerate(texts)
                    ],
                    "usage": {"prompt_tokens": len(texts), "total_tokens": len(texts)},
                },
                {"x-litellm-response-cost": "0"},
            )

    return Handler


class _Limited:
    """A file object over bytes, for GzipFile."""

    def __init__(self, data: bytes):
        self.data, self.at = data, 0

    def read(self, size: int = -1) -> bytes:
        end = len(self.data) if size < 0 else min(len(self.data), self.at + size)
        chunk = self.data[self.at : end]
        self.at = end
        return chunk


def _control_handler(fakes: Fakes, token: str):
    expected = ("Bearer " + token).encode()

    class Handler(_Quiet):
        def allowed(self) -> bool:
            given = (self.headers.get("Authorization") or "").encode()
            if hmac.compare_digest(given, expected):
                return True
            self.close_connection = True
            self.reply(401, {"error": "a bearer token is required"})
            return False

        def do_GET(self) -> None:
            if not self.allowed():
                return
            if self.path == "/control/health":
                self.reply(200, {"ok": True})
            elif self.path == "/control/requests":
                self.reply(200, fakes.view())
            elif self.path == "/control/spans":
                self.reply(200, fakes.span_view())
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self.allowed():
                return
            raw = self.body()
            if raw is None:
                return
            if self.path == "/control/reset":
                try:
                    options = json.loads(raw) if raw else {}
                except (ValueError, RecursionError):
                    options = {}
                keep = isinstance(options, dict) and options.get("spans") is False
                fakes.reset(spans=not keep)
                self.reply(200, {"ok": True})
            elif self.path == "/control/script":
                try:
                    data = json.loads(raw)
                    entries = check_script(data.get("entries") if isinstance(data, dict) else None)
                except (ValueError, RecursionError) as error:
                    message = str(error) if isinstance(error, ScriptError) else "not JSON"
                    self.reply(400, {"error": message})
                    return
                fakes.set_script(entries)
                self.reply(200, {"ok": True, "entries": len(entries)})
            else:
                self.reply(404, {"error": "not found"})

    return Handler


def _pipe(source: socket.socket, target: socket.socket) -> None:
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                break
            target.sendall(chunk)
    except OSError:
        pass
    finally:
        for end, how in ((target, socket.SHUT_WR), (source, socket.SHUT_RD)):
            try:
                end.shutdown(how)
            except OSError:
                pass


class Forwarder:
    """A TCP relay from a listening port to the agent. Bytes pass unchanged."""

    def __init__(self, host: str, port: int, agent: tuple[str, int]):
        self.agent = agent
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.listener = socket.create_server((host, port))
        self.port = self.listener.getsockname()[1]
        self.closed = False
        threading.Thread(target=self._accept, name="ingress", daemon=True).start()

    def _accept(self) -> None:
        while not self.closed:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            if not self.slots.acquire(blocking=False):
                client.close()
                continue
            threading.Thread(target=self._relay, args=(client,), daemon=True).start()

    def _relay(self, client: socket.socket) -> None:
        upstream = None
        try:
            upstream = socket.create_connection(self.agent, timeout=10)
            upstream.settimeout(300)
            client.settimeout(300)
            back = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
            back.start()
            _pipe(client, upstream)
            back.join(300)
        except OSError:
            pass
        finally:
            for end in (client, upstream):
                if end is not None:
                    try:
                        end.close()
                    except OSError:
                        pass
            self.slots.release()

    def close(self) -> None:
        self.closed = True
        try:
            self.listener.close()
        except OSError:
            pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(MAX_SERVER_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address) -> None:
        # The agent under test can reach every listener of this container. A connection over
        # the cap is closed at once, so held-open connections cannot use up the threads.
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_bind(self) -> None:
        # HTTPServer looks up the host's full name here, a reverse DNS query that can take
        # seconds on a network with no resolver (the internal test network).
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class Harness:
    """The three listeners. `agent` is where the ingress forwarder sends connections; without
    it there is no forwarder (a test that reaches its stub agent directly)."""

    def __init__(
        self,
        token: str,
        host: str = "127.0.0.1",
        agent: tuple[str, int] | None = None,
        ports: tuple[int, int, int] = (0, 0, 0),
    ):
        if len(token) < 16:
            raise ValueError("the control token must be at least 16 characters")
        self.fakes = Fakes()
        self.host = host
        self.gateway = _Server((host, ports[0]), _fakes_handler(self.fakes))
        self.control = _Server((host, ports[1]), _control_handler(self.fakes, token))
        for server in (self.gateway, self.control):
            threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
        self.forwarder = Forwarder(host, ports[2], agent) if agent else None

    @property
    def gateway_port(self) -> int:
        return self.gateway.server_address[1]

    @property
    def control_port(self) -> int:
        return self.control.server_address[1]

    @property
    def ingress_port(self) -> int | None:
        return self.forwarder.port if self.forwarder else None

    def close(self) -> None:
        for server in (self.gateway, self.control):
            server.shutdown()
            server.server_close()
        if self.forwarder:
            self.forwarder.close()


def main() -> int:
    """The container entry point: listen on every interface of the test networks."""
    token = os.environ.get(TOKEN_ENV, "")
    try:
        config = json.loads(os.environ.get(CONFIG_ENV, ""))
        ports = (
            int(config["gateway_port"]),
            int(config["control_port"]),
            int(config["ingress_port"]),
        )
        agent = (str(config["agent_host"]), int(config["agent_port"]))
        harness = Harness(token, host="0.0.0.0", agent=agent, ports=ports)  # noqa: S104
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"conformance fakes: cannot start: {type(error).__name__}", file=sys.stderr)
        return 2
    print(f"conformance fakes: listening (gateway {harness.gateway_port})", flush=True)
    _serve_forever()
    return 0


def _serve_forever() -> None:
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
