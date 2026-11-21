"""runtime/adk@1.4.1: coded model errors (#1030, #1034) and gateway fallbacks (#1039).

The runtime's real model client (ADK's LiteLlm, LiteLLM's SDK and the OpenAI client under it)
talks to a fake LiteLLM gateway on loopback that answers each chat call from a script, so the
status codes, error bodies and `x-litellm-*` headers go through the same parsing as in a stack.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent as agent_module  # noqa: E402
import config as cfg  # noqa: E402
import redaction  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from test_runtime import FakeMemory, app  # noqa: E402, F401  (app is a fixture)

KEY = "sk-gatewaykey-" + "q" * 20  # the deployment's gateway key (a test value)
HASH = "ab" * 32  # LiteLLM names a key's SHA-256 token hash in its 429 text
LONG = "upstream detail " * 80  # a long provider message


def ok(content="Done.", headers=None):
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }
    return 200, {"x-litellm-response-cost": "0.001", **(headers or {})}, body


def error(status, kind, message, headers=None):
    body = {"error": {"message": message, "type": kind, "param": None, "code": str(status)}}
    return status, headers or {}, body


BUDGET = (
    "Budget has been exceeded! Key=agentbreeder-449e60ed (sk-...WXYZ) Current cost: 0.00015375,"
    " Max budget: 0.0001"
)
THROTTLED = (
    "LiteLLM Rate Limit Handler for rate limit type = key. Crossed TPM / RPM / Max Parallel "
    f"Request Limit. Limit type: requests. Current limit: 2, Remaining: 0. hashed_token={HASH}"
)


class Gateway:
    """A fake LiteLLM proxy: POST /v1/chat/completions answers from `script` (the last entry
    repeats); every other path is 404."""

    def __init__(self):
        self.script: list = []
        self.calls = 0
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if not self.path.endswith("/chat/completions"):
                    status, headers, body = 404, {}, {"error": {"message": "no"}}
                else:
                    gateway.calls += 1
                    entry = gateway.script.pop(0) if len(gateway.script) > 1 else gateway.script[0]
                    status, headers, body = entry
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def gateway(app, monkeypatch):  # noqa: F811
    fake = Gateway()
    monkeypatch.setenv("AGENTBREEDER_GATEWAY_URL", fake.url)
    monkeypatch.setattr(agent_module, "Memory", FakeMemory)
    monkeypatch.setattr(
        agent_module, "DatabaseSessionService", lambda **_: InMemorySessionService()
    )
    monkeypatch.setattr(agent_module, "ensure_sessions_schema", lambda url: None)
    monkeypatch.setattr(agent_module, "RETRY_BACKOFF", 0.01)
    FakeMemory.events = []
    runtime = agent_module.Runtime(cfg.config(), "postgresql://u:p@db/agent", KEY)
    yield runtime, fake
    fake.close()


def turn(runtime, message="hi", session="s1"):
    return asyncio.run(runtime.chat_async(session, "cust-001", message))


def failed_turn(runtime, session="s1"):
    with pytest.raises(agent_module.ModelError) as caught:
        turn(runtime, session=session)
    return caught.value


def reply_for(failure):
    import server

    return server.model_error_response(failure)


def assert_secretless(text):
    """No key, masked key fragment or token hash (log lines and stored detail)."""
    for leak in (KEY, "WXYZ", HASH):
        assert leak not in text


def assert_clean(text):
    """Nothing from upstream at all (what the caller gets)."""
    assert_secretless(text)
    for leak in ("upstream detail", "Budget has been", "hashed_token"):
        assert leak not in text


# -- #1034: budget and rate limit ------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 422])
def test_a_spent_budget_is_a_coded_error_with_the_right_next_step(gateway, status, capsys):
    runtime, fake = gateway
    fake.script = [error(status, "budget_exceeded", BUDGET)]
    failure = failed_turn(runtime)
    assert failure.code == "MODEL_BUDGET_EXCEEDED"
    assert fake.calls == 1  # never retried
    code, body, headers = reply_for(failure)
    assert code == 402 and body["code"] == "MODEL_BUDGET_EXCEEDED"
    assert "budget_usd" in body["error"] and "try again" not in body["error"].lower()
    assert body["correlation_id"] == failure.turn_id and len(body["correlation_id"]) == 32
    assert_clean(json.dumps(body))
    assert_secretless(capsys.readouterr().out)


def test_a_rate_limit_past_the_bound_is_not_waited_out(gateway, capsys):
    runtime, fake = gateway
    fake.script = [error(429, "throttling_error", THROTTLED, {"retry-after": "60"})]
    failure = failed_turn(runtime)
    assert failure.code == "MODEL_RATE_LIMITED" and failure.retry_after == 60
    assert fake.calls == 1  # 60 s is past the bound: no wait, no retry
    code, body, headers = reply_for(failure)
    assert code == 429 and headers == {"Retry-After": "60"}
    assert body["retry_after_s"] == 60 and "60" in body["error"]
    assert_clean(json.dumps(body))
    assert_secretless(capsys.readouterr().out)


def test_a_short_rate_limit_is_waited_out_and_reported(gateway):
    runtime, fake = gateway
    fake.script = [error(429, "throttling_error", THROTTLED, {"retry-after": "1"}), ok()]
    result = turn(runtime)
    assert result["reply"] == "Done." and fake.calls == 2
    assert 1.0 <= result["rate_limit_wait_s"] < 3


def test_the_wait_for_rate_limits_is_bounded_per_turn(gateway, monkeypatch):
    runtime, fake = gateway
    monkeypatch.setattr(agent_module, "RATE_LIMIT_WAIT", 1.5)
    fake.script = [error(429, "throttling_error", THROTTLED, {"retry-after": "1"})]
    failure = failed_turn(runtime)
    assert failure.code == "MODEL_RATE_LIMITED" and failure.retry_after == 1
    assert fake.calls == 2  # waited once (1 s), the second wait would pass 1.5 s


def test_a_rate_limit_without_retry_after_has_no_hint(gateway, monkeypatch):
    runtime, fake = gateway
    monkeypatch.setattr(agent_module, "RATE_LIMIT_WAIT", 0)
    fake.script = [error(429, "throttling_error", THROTTLED)]
    failure = failed_turn(runtime)
    code, body, headers = reply_for(failure)
    assert failure.retry_after is None and headers == {} and body["retry_after_s"] is None


@pytest.mark.parametrize("value", ["abc", "-5", "0", "1e3", "999999999", " 7 ", "7.5"])
def test_hostile_retry_after_values(value):
    assert agent_module.retry_after({"retry-after": value}) in (None, 7, 3600)
    assert agent_module.retry_after({"retry-after": "999999999"}) == 3600
    assert agent_module.retry_after({"retry-after": " 7 "}) == 7
    assert agent_module.retry_after({}) is None
    assert agent_module.retry_after(None) is None
    assert agent_module.retry_after({"Retry-After": "7"}) == 7  # a plain dict, any case


# -- #1030: no upstream text in the reply ----------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind", "code", "http", "calls"),
    [
        (500, "internal_server_error", "MODEL_UNAVAILABLE", 502, 3),
        (503, "service_unavailable", "MODEL_UNAVAILABLE", 502, 3),
        (408, "timeout", "MODEL_UNAVAILABLE", 502, 1),  # a timeout is not retried
        (504, "gateway_timeout", "MODEL_UNAVAILABLE", 502, 1),
        (403, "key_model_access_denied", "MODEL_ACCESS_DENIED", 502, 1),
        (401, "auth_error", "MODEL_ACCESS_DENIED", 502, 1),
        (400, "invalid_request_error", "MODEL_REQUEST_REFUSED", 502, 1),
        (404, "not_found_error", "MODEL_REQUEST_REFUSED", 502, 1),
    ],
)
def test_a_raw_provider_error_never_reaches_the_reply(
    gateway, capsys, status, kind, code, http, calls
):
    runtime, fake = gateway
    message = f"provider says no; key {KEY} (sk-...WXYZ) token {HASH} {LONG}"
    fake.script = [error(status, kind, message)]
    failure = failed_turn(runtime)
    assert failure.code == code and fake.calls == calls
    reply_status, body, _ = reply_for(failure)
    assert reply_status == http and body["code"] == code
    assert_clean(json.dumps(body))
    assert "provider says no" not in json.dumps(body)
    assert str(failure) == code  # the exception carries no upstream text either
    log = capsys.readouterr().out
    assert_secretless(log)
    assert f"turn={failure.turn_id}" in log and f"code={code}" in log  # the detail, redacted
    assert "provider says no" in log and len(log.splitlines()) == 1
    stored = [e[5]["error"] for e in FakeMemory.events if e[4] == "turn_failed"]
    assert len(stored) == 1 and "provider says no" in stored[0]
    assert_secretless(stored[0])
    assert FakeMemory.last_turn["error"] == stored[0]


def test_a_transient_failure_is_retried(gateway):
    runtime, fake = gateway
    fake.script = [error(500, "internal_server_error", "blip"), ok()]
    assert turn(runtime)["reply"] == "Done." and fake.calls == 2


def test_no_retry_starts_past_the_turn_deadline(gateway, monkeypatch):
    runtime, fake = gateway
    monkeypatch.setattr(agent_module, "RETRY_DEADLINE", 0)
    fake.script = [error(500, "internal_server_error", "blip"), ok()]
    assert failed_turn(runtime).code == "MODEL_UNAVAILABLE" and fake.calls == 1


def test_the_model_client_does_its_own_retries():
    """The OpenAI client's own retries (which honour retry-after up to 120 s) are off, so the
    runtime's bounded loop is the only one."""
    assert agent_module.MODEL_ARGS["max_retries"] == 0


def test_library_logs_are_one_redacted_line(capsys):
    import logging

    agent_module.quiet_library_logs()
    try:
        raise ValueError(f"BadRequestError: {BUDGET} {KEY}\nsecond line")
    except ValueError:
        runners = logging.getLogger("google_adk.google.adk.runners")
        runners.error("Root node x failed.", exc_info=True)
    logging.getLogger("LiteLLM").error(f"budget: {BUDGET} {HASH}")
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 2
    assert_secretless(err)


@pytest.mark.parametrize(
    "text",
    [
        "Key=x (sk-...WXYZ)",
        "key sk-....wxyz1234 here",
        "Key=x (sk-\u2026WXYZ)",
        "Key=x (sk-***WXYZ)",
        f"hashed_token={HASH}",
        f"Bearer {KEY}",
    ],
)
def test_upstream_redaction_covers_key_fragments_and_hashes(text):
    cleaned = redaction.upstream(text)
    for leak in ("WXYZ", "wxyz1234", HASH, KEY):
        assert leak not in cleaned


def test_key_fragments_are_redacted_everywhere():
    assert "WXYZ" not in redaction.redact("Key=x (sk-...WXYZ)")


# -- #1039: a fallback served the turn -------------------------------------------------------


def test_a_turn_served_by_a_fallback_says_so(gateway, monkeypatch):
    runtime, fake = gateway
    seen = {}
    real = agent_module.Runtime.mark_root

    def recording(self, root, tally, trace):
        real(self, root, tally, trace)
        seen.update(agent_module.root_attributes(tally))

    monkeypatch.setattr(agent_module.Runtime, "mark_root", recording)
    fallback = {"x-litellm-attempted-fallbacks": "1", "x-litellm-model-group": "chat-or"}
    fake.script = [ok(headers=fallback)]
    result = turn(runtime)
    assert result["fallbacks"] == 1 and result["served_by"] == ["chat-or"]
    assert result["model"] == "m"  # the alias the agent asked for, as before
    called = [e[5] for e in FakeMemory.events if e[4] == "model_called"]
    assert called[0]["served_by"] == "chat-or" and called[0]["fallbacks"] == 1
    assert seen == {"agentbreeder.fallbacks": 1, "agentbreeder.served_by": ("chat-or",)}


def test_a_turn_on_the_primary_has_no_fallback(gateway):
    runtime, fake = gateway
    fake.script = [
        ok(headers={"x-litellm-attempted-fallbacks": "0", "x-litellm-model-group": "m"})
    ]
    result = turn(runtime)
    assert result["fallbacks"] == 0 and result["served_by"] == ["m"]


def test_fallback_fields_without_gateway_headers(gateway):
    runtime, fake = gateway
    fake.script = [ok()]
    result = turn(runtime)
    assert result["fallbacks"] == 0 and result["served_by"] == []


class Response:
    def __init__(self, headers):
        self._hidden_params = {"additional_headers": headers}


F = agent_module.FALLBACKS_HEADER
G = agent_module.GROUP_HEADER


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({F: "1", G: "chat-or"}, (1, "chat-or")),
        ({F: 2, G: "support-model-openrouter"}, (2, "support-model-openrouter")),
        ({}, (0, None)),
        ({F: "abc", G: "chat"}, (0, "chat")),
        ({F: "-1"}, (0, None)),
        ({F: "1.5"}, (0, None)),
        ({F: True}, (0, None)),
        ({F: "1000000"}, (0, None)),
        ({G: "https://api.example.com/v1"}, (0, None)),
        ({G: "openrouter/google/gemini"}, (0, None)),
        ({G: "chat or"}, (0, None)),
        ({G: "chat\nX-Injected: 1"}, (0, None)),
        ({G: "x" * 65}, (0, None)),
        ({G: "chаt"}, (0, None)),  # Cyrillic a
        ({G: ["chat"]}, (0, None)),
    ],
)
def test_fallback_header_parsing(headers, expected):
    assert agent_module.response_fallback(Response(headers)) == expected


def test_fallback_parsing_without_hidden_params():
    assert agent_module.response_fallback(object()) == (0, None)


def test_fallback_fields_are_null_without_a_gateway_call():
    tally = agent_module.new_tally()
    assert agent_module.turn_fallbacks(tally) == (None, [])


def test_exported_spans_drop_token_hashes_and_key_fragments():
    """ADK's own spans record a failed model call's exception text; what reaches Phoenix is
    redacted as the log line is (`traces` prints span errors)."""
    import telemetry
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import Status, StatusCode

    inner = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(telemetry.RedactingExporter(inner)))
    tracer = provider.get_tracer("adk-like")
    with tracer.start_as_current_span("call_llm") as current:
        current.set_status(Status(StatusCode.ERROR, f"RateLimitError: {THROTTLED} (sk-...WXYZ)"))
        current.add_event("exception", {"exception.message": f"{THROTTLED} {KEY}"})
    (span,) = inner.get_finished_spans()
    text = span.status.description + json.dumps(dict(span.events[0].attributes))
    assert_secretless(text)
    assert "[hash]" in text and "sk-[redacted]" in text


def test_an_unexpected_failure_is_a_fixed_500_and_one_log_line(app, monkeypatch, capsys):  # noqa: F811
    import http.client

    import server

    def broken(session, customer, message):
        raise ValueError(f"unexpected with {KEY} and {HASH}\nsecond line")

    monkeypatch.setattr(server, "chat", broken)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request("POST", "/invoke", json.dumps({"message": "hi"}),
                     {"Content-Type": "application/json", "Host": "127.0.0.1"})  # fmt: skip
        answer = conn.getresponse()
        body = json.loads(answer.read())
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert answer.status == 500 and body == {
        "error": "The agent failed to answer this turn; see its log."
    }
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    lines = [line for line in captured.out.splitlines() if line.startswith("turn failed:")]
    assert len(lines) == 1 and "ValueError: unexpected" in lines[0]
    assert_secretless(captured.out)


def test_concurrent_turns_keep_their_own_tally(app, monkeypatch):  # noqa: F811
    """Each turn's cost, fallbacks and rate-limit wait (a contextvar tally) stay its own while
    two turns run at once on the runtime's loop."""
    fake = RoutedGateway()
    monkeypatch.setenv("AGENTBREEDER_GATEWAY_URL", fake.url)
    monkeypatch.setattr(agent_module, "Memory", FakeMemory)
    monkeypatch.setattr(
        agent_module, "DatabaseSessionService", lambda **_: InMemorySessionService()
    )
    monkeypatch.setattr(agent_module, "ensure_sessions_schema", lambda url: None)
    fallback = {"x-litellm-attempted-fallbacks": "1", "x-litellm-model-group": "chat-or"}
    fake.routes = {
        "alpha": [error(429, "throttling_error", THROTTLED, {"retry-after": "1"}),
                  ok(headers=fallback)],
        "beta": [ok(headers={"x-litellm-model-group": "m"})],
    }  # fmt: skip
    runtime = agent_module.Runtime(cfg.config(), "postgresql://u:p@db/agent", KEY)

    async def both():
        return await asyncio.gather(
            runtime.chat_async("sa", "cust-001", "alpha"),
            runtime.chat_async("sb", "cust-002", "beta"),
        )

    try:
        first, second = asyncio.run(both())
    finally:
        fake.close()
    assert (first["fallbacks"], first["served_by"]) == (1, ["chat-or"])
    assert first["rate_limit_wait_s"] >= 1
    assert (second["fallbacks"], second["served_by"]) == (0, ["m"])
    assert second["rate_limit_wait_s"] == 0


class RoutedGateway(Gateway):
    """Answers each chat call from the script of the first route word in its request body."""

    def __init__(self):
        self.routes: dict = {}
        super().__init__()
        routes = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
                status, headers, body = 404, {}, {"error": {"message": "no"}}
                if self.path.endswith("/chat/completions"):
                    word = next((w for w in routes.routes if w in raw), None)
                    if word is not None:
                        script = routes.routes[word]
                        status, headers, body = script.pop(0) if len(script) > 1 else script[0]
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server.shutdown()
        self.server.server_close()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
