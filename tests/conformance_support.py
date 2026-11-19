"""A stub agent that serves agent protocol v1, for the conformance kit's tests.

It is a small HTTP server that calls the kit's fake gateway the way a runtime would, keeps
history per customer and session, and posts OTLP spans. `flaws` switches off one behaviour at a
time, so a test can show that the matching check fails. Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import struct
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RUNTIME = "runtime/stub@1.0.0"
FRAMEWORK = "stub-framework"
CODES = {
    "MODEL_BUDGET_EXCEEDED": 402,
    "MODEL_RATE_LIMITED": 429,
    "MODEL_ACCESS_DENIED": 502,
    "MODEL_REQUEST_REFUSED": 502,
    "MODEL_UNAVAILABLE": 502,
}


# -- a minimal OTLP protobuf writer ----------------------------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _len(number: int, payload: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def _any(value) -> bytes:
    if isinstance(value, bool):
        return _varint(2 << 3) + _varint(int(value))
    if isinstance(value, int):
        return _varint(3 << 3) + _varint(value & (1 << 64) - 1)
    if isinstance(value, float):
        return _varint(4 << 3 | 1) + struct.pack("<d", value)
    if isinstance(value, (list, tuple)):
        return _len(5, b"".join(_len(1, _any(item)) for item in value))
    return _len(1, str(value).encode())


def _pairs(number: int, attributes: dict) -> bytes:
    return b"".join(
        _len(number, _len(1, key.encode()) + _len(2, _any(value)))
        for key, value in attributes.items()
    )


def otlp_span(trace: str, span: str, parent: str, name: str, attributes: dict) -> bytes:
    body = _len(1, bytes.fromhex(trace)) + _len(2, bytes.fromhex(span))
    if parent:
        body += _len(4, bytes.fromhex(parent))
    return body + _len(5, name.encode()) + _pairs(9, attributes)


def otlp_request(resource: dict, spans: list[bytes]) -> bytes:
    scope = b"".join(_len(2, span) for span in spans)
    return _len(1, _len(1, _pairs(1, resource)) + _len(2, scope))


# -- the stub --------------------------------------------------------------------------------


class _Coded(Exception):
    def __init__(self, code: str, retry_after: int | None = None):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after


class StubAgent:
    """`flaws` is a set of names; each breaks one thing a check looks for."""

    def __init__(
        self,
        gateway_url: str,
        key: str,
        *,
        flaws=frozenset(),
        name: str = "stub-agent",
        deployment: str | None = None,
        runtime: str = RUNTIME,
        framework: str = FRAMEWORK,
        port: int = 0,
    ):
        self.runtime, self.framework = runtime, framework
        self.gateway_url, self.key = gateway_url.rstrip("/"), key
        self.flaws = frozenset(flaws)
        self.name, self.deployment = name, deployment
        self.history: dict[tuple, list] = {}
        self.busy: set[str] = set()
        self.serial = threading.Lock()
        self.lock = threading.Lock()
        self.log: list[str] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def respond(self, status: int, body: dict, headers: dict | None = None) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                for key_, value in (headers or {}).items():
                    self.send_header(key_, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def local(self) -> bool:
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
                return "any_host" in stub.flaws or host in ("127.0.0.1", "localhost", "agent")

            def do_GET(self) -> None:
                if self.path == "/health":
                    self.respond(500 if "unhealthy" in stub.flaws else 200, {"status": "ok"})
                else:
                    self.respond(404, {"error": "Not found"})

            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(size)
                if self.path != "/invoke" or not self.local():
                    self.respond(404, {"error": "Not found"})
                    return
                kind = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                if kind != "application/json" and "any_type" not in stub.flaws:
                    self.respond(415, {"error": "Content-Type must be application/json"})
                    return
                if not 0 < size <= 64 * 1024 and "any_size" not in stub.flaws:
                    self.respond(400, {"error": "A JSON body of at most 64 KiB is required"})
                    return
                try:
                    payload = json.loads(raw)
                    message = payload["message"]
                    if not isinstance(message, str) or not message.strip():
                        raise ValueError
                    session = payload.get("session_id") or uuid.uuid4().hex
                    customer = payload.get("customer_id") or "guest"
                    if not (ID.match(session) and ID.match(customer)):
                        if "any_id" not in stub.flaws:
                            raise ValueError
                except (ValueError, KeyError, TypeError):
                    self.respond(400, {"error": "Send a message"})
                    return
                if len(message) > 4000 and "any_length" not in stub.flaws:
                    self.respond(413, {"error": "Messages are limited to 4000 characters"})
                    return
                self.respond(*stub.turn(session, customer, message))

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def logs(self) -> str:
        return "\n".join(self.log)

    # -- one turn ----------------------------------------------------------------------------

    def turn(self, session: str, customer: str, message: str) -> tuple[int, dict, dict]:
        if "serial_sessions" in self.flaws:
            with self.serial:
                return self._turn(session, customer, message)
        with self.lock:
            if session in self.busy:
                return 409, {"error": "This conversation is still finishing a turn"}, {}
            self.busy.add(session)
        try:
            return self._turn(session, customer, message)
        finally:
            with self.lock:
                self.busy.discard(session)

    def _turn(self, session: str, customer: str, message: str) -> tuple[int, dict, dict]:
        turn, trace = uuid.uuid4().hex, os.urandom(16).hex()
        key = (session,) if "shared_history" in self.flaws else (customer, session)
        history = [] if "no_history" in self.flaws else self.history.setdefault(key, [])
        messages = [*history, {"role": "user", "content": message}]
        try:
            answer, headers, calls = self.model(messages)
        except _Coded as failure:
            self.spans(trace, turn, session, customer, llm=False)
            wait = failure.retry_after
            body = {
                "error": "The model call failed." + (failure.detail if failure.detail else ""),
                "code": failure.code,
                "correlation_id": turn,
                "trace_id": trace,
                "retry_after_s": wait,
            }
            if "no_correlation" in self.flaws:
                del body["correlation_id"]
            status = 500 if "uncoded" in self.flaws else CODES[failure.code]
            extra = (
                {"Retry-After": str(wait)} if wait and "no_retry_after" not in self.flaws else {}
            )
            return status, body, extra
        text = answer["choices"][0]["message"]["content"]
        history += [{"role": "user", "content": message}, {"role": "assistant", "content": text}]
        self.spans(trace, turn, session, customer, llm=True)
        cost = headers.get("x-litellm-response-cost")
        group = headers.get("x-litellm-model-group")
        reply = {
            "reply": text + (f" key={self.key}" if "leak_key" in self.flaws else ""),
            "session_id": session,
            "customer_id": customer,
            "model": "stub-model",
            "framework": "another" if "wrong_framework" in self.flaws else self.framework,
            "runtime": self.runtime,
            "turn_id": turn,
            "trace_id": None if "no_trace_id" in self.flaws else trace,
            "cost_usd": 0.5 if "wrong_cost" in self.flaws else (float(cost) if cost else None),
            "fallbacks": 0
            if "no_fallbacks" in self.flaws
            else int(headers.get("x-litellm-attempted-fallbacks", 0)),
            "served_by": [group] if group else [],
            "rate_limit_wait_s": 0.0,
            "sources": [],
            "tools": [],
            "web": [],
            "tickets": [],
            "memory_saved": [],
            "kb_searches": 0,
            "model_calls": calls,
            "retrieval": "none",
            "prompt_tokens": answer["usage"]["prompt_tokens"],
            "output_tokens": answer["usage"]["completion_tokens"],
            "mcp_unavailable": [],
        }
        if "missing_field" in self.flaws:
            del reply["mcp_unavailable"]
        if "wrong_type" in self.flaws:
            reply["model_calls"] = "1"
        return 200, reply, {}

    def model(self, messages: list) -> tuple[dict, dict, int]:
        attempts = 6 if "many_retries" in self.flaws else 3
        calls = 0
        while True:
            calls += 1
            request = urllib.request.Request(  # noqa: S310 - the loopback fake gateway
                self.gateway_url + "/v1/chat/completions",
                data=json.dumps({"model": "stub-model", "messages": messages}).encode(),
                headers={
                    "Authorization": "Bearer " + self.key,
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310
                    found = {name.lower(): value for name, value in answer.getheaders()}
                    return json.loads(answer.read()), found, calls
            except urllib.error.HTTPError as error:
                detail = json.loads(error.read()).get("error", {})
                kind, text = detail.get("type"), str(detail.get("message"))
                self.log.append(
                    f"model call failed: status={error.code}"
                    + (f" detail={text}" if "log_upstream" in self.flaws else "")
                    + (f" key={self.key[-8:]}" if "log_key" in self.flaws else "")
                )
                status = error.code
                retriable = status in (500, 502, 503) or (
                    "retry_budget" in self.flaws and kind == "budget_exceeded"
                )
                if retriable and calls < attempts:
                    continue
                if kind == "budget_exceeded":
                    failure = _Coded("MODEL_BUDGET_EXCEEDED")
                elif status == 429:
                    after = error.headers.get("Retry-After")
                    failure = _Coded("MODEL_RATE_LIMITED", int(after) if after else None)
                elif status in (401, 403):
                    failure = _Coded("MODEL_ACCESS_DENIED")
                elif 400 <= status < 500:
                    failure = _Coded("MODEL_REQUEST_REFUSED")
                else:
                    failure = _Coded("MODEL_UNAVAILABLE")
                if "wrong_code" in self.flaws:
                    failure = _Coded("MODEL_UNAVAILABLE", failure.retry_after)
                failure.detail = f" {text}" if "leak_upstream" in self.flaws else ""
                self.upstream = text
                raise failure from None

    def spans(self, trace: str, turn: str, session: str, customer: str, llm: bool) -> None:
        if "no_spans" in self.flaws:
            return
        root = os.urandom(8).hex()
        attributes = {
            "openinference.span.kind": "AGENT",
            "session.id": session,
            "user.id": "someone" if "wrong_user" in self.flaws else customer,
            "agent.turn_id": turn,
            "agent.framework": self.framework,
            "agent.name": self.name,
            "agentbreeder.fallbacks": 0,
            "agentbreeder.served_by": ["stub-model"],
        }
        if self.deployment:
            attributes["agentbreeder.deployment_id"] = self.deployment
        if "span_key" in self.flaws:
            attributes["debug"] = self.key
        if "span_upstream" in self.flaws and getattr(self, "upstream", None):
            attributes["error.detail"] = self.upstream
        found = [otlp_span(trace, root, "", "agent_turn", attributes)]
        if "two_roots" in self.flaws:
            found.append(otlp_span(trace, os.urandom(8).hex(), "", "agent_turn", attributes))
        if llm and "no_llm_span" not in self.flaws:
            child = {"openinference.span.kind": "LLM", "llm.token_count.prompt": 10}
            found.append(otlp_span(trace, os.urandom(8).hex(), root, "call_llm", child))
        payload = otlp_request({"openinference.project.name": self.name}, found)
        request = urllib.request.Request(  # noqa: S310 - the loopback fake gateway
            self.gateway_url + "/v1/traces",
            data=payload,
            headers={"Content-Type": "application/x-protobuf"},
        )
        urllib.request.urlopen(request, timeout=10).close()  # noqa: S310
