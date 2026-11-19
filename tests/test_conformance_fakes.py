"""The conformance kit's fakes (#759): the scripted gateway, the span sink, the control API and
the ingress forwarder, in process."""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentbreeder.conformance import checks, fakes

TOKEN = "t" * 32
KEY = "sk-conf-" + "9" * 48


@pytest.fixture
def harness():
    running = fakes.Harness(TOKEN)
    yield running
    running.close()


@pytest.fixture
def control(harness):
    return checks.Control(f"http://127.0.0.1:{harness.control_port}", TOKEN)


def call(harness, path, body=None, headers=None, method=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    request = urllib.request.Request(
        f"http://127.0.0.1:{harness.gateway_port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            return answer.status, dict(answer.getheaders()), answer.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def chat(harness, **options):
    body = {"model": "support-model", "messages": [{"role": "user", "content": "hi"}]}
    return call(harness, "/v1/chat/completions", body, **options)


def test_an_unscripted_call_gets_the_default_answer(harness):
    status, headers, raw = chat(harness)
    body = json.loads(raw)
    assert status == 200
    assert body["choices"][0]["message"] == {"role": "assistant", "content": fakes.DEFAULT_REPLY}
    assert body["model"] == "support-model"
    assert body["usage"] == {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    assert headers["x-litellm-response-cost"] == fakes.DEFAULT_COST


def test_scripted_answers_are_used_once_in_order(harness, control):
    control.script(
        {"reply": "one", "cost": None, "usage": {"prompt_tokens": 3}},
        {"reply": "two", "headers": {"x-litellm-model-group": "other"}},
    )
    status, headers, raw = chat(harness)
    assert json.loads(raw)["choices"][0]["message"]["content"] == "one"
    assert json.loads(raw)["usage"]["prompt_tokens"] == 3
    assert "x-litellm-response-cost" not in headers
    _, headers, raw = call(harness, "/chat/completions", {"model": "m", "messages": []})
    assert json.loads(raw)["choices"][0]["message"]["content"] == "two"
    assert headers["x-litellm-model-group"] == "other"
    assert json.loads(chat(harness)[2])["choices"][0]["message"]["content"] == fakes.DEFAULT_REPLY


def test_a_repeating_failure_keeps_answering(harness, control):
    control.script(
        {
            "status": 429,
            "error": {"type": "throttling_error", "message": "slow down"},
            "headers": {"Retry-After": "30"},
            "repeat": True,
        }
    )
    for _ in range(3):
        status, headers, raw = chat(harness)
        assert status == 429 and headers["Retry-After"] == "30"
        assert json.loads(raw) == {
            "error": {
                "message": "slow down",
                "type": "throttling_error",
                "param": None,
                "code": "429",
            }
        }


def test_a_delay_holds_the_answer(harness, control):
    control.script({"delay_s": 0.3})
    started = time.monotonic()
    assert chat(harness)[0] == 200
    assert time.monotonic() - started >= 0.3


def test_tool_calls_are_passed_on(harness, control):
    calls = [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    control.script({"tool_calls": calls, "reply": ""})
    choice = json.loads(chat(harness)[2])["choices"][0]
    assert choice["message"]["tool_calls"] == calls and choice["finish_reason"] == "tool_calls"


def test_the_anthropic_shape(harness, control):
    control.script({"reply": "bonjour"}, {"status": 529, "error": {"type": "overloaded_error"}})
    body = {"model": "claude", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
    status, headers, raw = call(harness, "/v1/messages", body, {"x-api-key": KEY})
    assert status == 200 and headers["x-litellm-response-cost"] == fakes.DEFAULT_COST
    answer = json.loads(raw)
    assert answer["content"] == [{"type": "text", "text": "bonjour"}]
    assert answer["usage"] == {"input_tokens": 10, "output_tokens": 2}
    status, _, raw = call(harness, "/v1/messages", body)
    assert status == 529
    assert json.loads(raw) == {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "scripted failure"},
    }


def test_embeddings_are_deterministic_and_sized(harness):
    body = {"model": "embed", "input": ["refund policy", "shipping"], "dimensions": 5}
    status, _, raw = call(harness, "/v1/embeddings", body)
    data = json.loads(raw)["data"]
    assert status == 200 and [len(item["embedding"]) for item in data] == [5, 5]
    assert data[0]["embedding"] == fakes.vector("refund policy", 5)
    assert data[0]["embedding"] != data[1]["embedding"]
    assert all(-1 <= value <= 1 for value in fakes.vector("x", 768))
    single = json.loads(call(harness, "/embeddings", {"input": "one"})[2])
    assert len(single["data"][0]["embedding"]) == 768
    for bad in (
        {"input": 5},
        {"input": ["a"], "dimensions": 0},
        {"input": ["a"], "dimensions": True},
    ):
        assert call(harness, "/v1/embeddings", bad)[0] == 400


def test_requests_are_recorded_without_the_credential(harness, control):
    chat(harness, headers={"Authorization": "Bearer " + KEY})
    call(harness, "/nowhere", {"a": 1})
    call(harness, "/health", method="GET")
    recorded = control.requests()
    assert [entry["kind"] for entry in recorded] == ["chat", "other", "other"]
    assert [entry["n"] for entry in recorded] == [1, 2, 3]
    assert recorded[0]["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert recorded[0]["headers"]["authorization"] == {
        "scheme": "Bearer",
        "sha256": hashlib.sha256(KEY.encode()).hexdigest(),
    }
    assert KEY not in json.dumps(recorded)
    assert control.model_calls() == control.requests("chat")
    control.reset()
    assert control.requests() == []


def test_the_span_sink_keeps_payloads_and_unpacks_gzip(harness, control):
    plain = b"\x0a\x02hi"
    assert call(harness, "/v1/traces", raw=plain)[0] == 200
    packed = gzip.compress(b"packed spans")
    assert call(harness, "/v1/traces", raw=packed, headers={"Content-Encoding": "gzip"})[0] == 200
    call(harness, "/v1/traces", raw=b"not gzip", headers={"Content-Encoding": "gzip"})
    assert control.span_payloads() == [plain, b"packed spans", b""]
    assert [entry["kind"] for entry in control.requests()] == ["traces"] * 3


def test_the_relay_address_is_always_down(harness, control):
    assert call(harness, "/mcp/github", {"jsonrpc": "2.0", "method": "initialize"})[0] == 503
    assert call(harness, "/mcp/github", method="DELETE")[0] == 503
    assert call(harness, "/other", method="DELETE")[0] == 404
    assert [entry["kind"] for entry in control.requests()] == ["mcp", "mcp", "other"]


def test_bodies_must_have_a_length_within_the_limit(harness, monkeypatch):
    assert call(harness, "/v1/chat/completions", raw=b"{not json")[0] == 200  # recorded as null
    monkeypatch.setattr(fakes, "MAX_BODY", 10)
    assert chat(harness)[0] == 413


def test_a_large_request_is_recorded_by_size_only(harness, control, monkeypatch):
    monkeypatch.setattr(fakes, "MAX_RECORDED_BODY", 20)
    assert chat(harness)[0] == 200
    (entry,) = control.requests()
    assert set(entry["body"]) == {"oversize_bytes"}


def test_connections_over_the_cap_are_closed(monkeypatch):
    monkeypatch.setattr(fakes, "MAX_SERVER_CONNECTIONS", 1)
    running = fakes.Harness(TOKEN)
    held = __import__("socket").create_connection(("127.0.0.1", running.gateway_port))
    try:
        held.sendall(b"POST /v1/chat/completions HTTP/1.1\r\n")  # holds the only slot
        time.sleep(0.2)
        with pytest.raises(OSError):
            chat(running)
        held.close()
        time.sleep(0.3)
        assert chat(running)[0] == 200
    finally:
        running.close()


def test_reset_can_keep_the_spans(harness, control):
    call(harness, "/v1/traces", raw=b"one")
    chat(harness)
    control.reset(spans=False)
    assert control.requests() == [] and control.span_payloads() == [b"one"]
    control.reset()
    assert control.span_payloads() == []


def test_the_control_api_needs_its_token(harness):
    for token in ("", "wrong" * 8, TOKEN + "x"):
        assert not checks.Control(f"http://127.0.0.1:{harness.control_port}", token).healthy()
    wrong = checks.Control(f"http://127.0.0.1:{harness.control_port}", "wrong" * 8)
    with pytest.raises(OSError, match="401"):
        wrong.requests()
    with pytest.raises(OSError, match="401"):
        wrong.script({})
    assert checks.Control(f"http://127.0.0.1:{harness.control_port}", TOKEN).healthy()
    assert not checks.Control("http://127.0.0.1:1", TOKEN).healthy()
    with pytest.raises(ValueError, match="16"):
        fakes.Harness("short")


def test_the_control_api_refuses_what_it_does_not_know(harness, control):
    with pytest.raises(OSError, match="404"):
        control._call("GET", "/control/nothing")
    with pytest.raises(OSError, match="404"):
        control._call("POST", "/control/nothing", {})
    with pytest.raises(OSError, match="400"):
        control._call("POST", "/control/script", {"entries": "x"})


@pytest.mark.parametrize(
    "entries",
    [
        "x",
        [1],
        [{"unknown": 1}],
        [{"status": 99}],
        [{"status": True}],
        [{"delay_s": 31}],
        [{"delay_s": "1"}],
        [{"headers": {"a": "b\r\nc: d"}}],
        [{"headers": []}],
        [{"cost": 1}],
        [{}] * (fakes.MAX_SCRIPT + 1),
    ],
)
def test_a_bad_script_is_refused(entries):
    with pytest.raises(fakes.ScriptError):
        fakes.check_script(entries)


def test_limits_drop_instead_of_growing(monkeypatch):
    state = fakes.Fakes()
    monkeypatch.setattr(fakes, "MAX_REQUESTS", 2)
    monkeypatch.setattr(fakes, "MAX_SPAN_BYTES", 5)
    for _ in range(3):
        state.record("chat", "POST", "/v1/chat/completions", {}, None)
    state.add_spans(b"1234")
    state.add_spans(b"5678")
    assert len(state.view()["requests"]) == 2 and state.span_view()["dropped"] == 2
    assert len(state.span_view()["spans"]) == 1


class _Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps({"host": self.headers.get("Host"), "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_the_forwarder_passes_bytes_unchanged():
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    running = fakes.Harness(TOKEN, agent=("127.0.0.1", upstream.server_address[1]))
    try:
        agent = checks.Agent("127.0.0.1", running.ingress_port)
        answer = agent.request("GET", "/health", host="evil.example")
        assert answer.status == 200
        assert answer.json == {"host": "evil.example", "path": "/health"}
    finally:
        running.close()
        upstream.shutdown()
        upstream.server_close()


def test_the_forwarder_drops_a_connection_it_cannot_relay():
    running = fakes.Harness(TOKEN, agent=("127.0.0.1", 1))
    try:
        with pytest.raises(OSError):
            checks.Agent("127.0.0.1", running.ingress_port).request("GET", "/health", timeout=5)
    finally:
        running.close()
    assert fakes.Harness(TOKEN).ingress_port is None


def test_the_container_entry_point_needs_its_configuration(monkeypatch, capsys):
    monkeypatch.setenv(fakes.TOKEN_ENV, TOKEN)
    monkeypatch.setenv(fakes.CONFIG_ENV, "{}")
    assert fakes.main() == 2
    monkeypatch.setenv(fakes.CONFIG_ENV, "not json")
    assert fakes.main() == 2
    assert "cannot start" in capsys.readouterr().err


def test_the_container_entry_point_listens(monkeypatch, capsys):
    config = {
        "gateway_port": 0,
        "control_port": 0,
        "ingress_port": 0,
        "agent_host": "127.0.0.1",
        "agent_port": 9,
    }
    monkeypatch.setenv(fakes.TOKEN_ENV, TOKEN)
    monkeypatch.setenv(fakes.CONFIG_ENV, json.dumps(config))

    monkeypatch.setattr(fakes, "_serve_forever", lambda: None)
    assert fakes.main() == 0
    assert "listening" in capsys.readouterr().out
