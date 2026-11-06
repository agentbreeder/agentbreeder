"""Phase-4 PEP: JSON-RPC conformance, forwarding rules and bypass attempts (no Docker)."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from unittest.mock import Mock

import pytest
from egress_support import TOOL_TOKEN, Upstream

from agentbreeder.egress import probe as probe_module
from agentbreeder.egress.protocol import (
    MAC_HEADER,
    NONCE_HEADER,
    HostClient,
    PepError,
    arguments_digest,
    parse_request,
    response_mac,
    rpc_error,
    token_matches,
)
from agentbreeder.egress.sidecar import (
    RateLimiter,
    Sidecar,
    gateway_handler,
    ingress_handler,
    resolve_destination,
)


def _body(method="tools/call", ident=1, **params):
    message = {"jsonrpc": "2.0", "method": method}
    if ident is not None:
        message["id"] = ident
    if method == "tools/call":
        message["params"] = {"name": params.get("name", "lookup"), "arguments": {"q": 1}}
    return json.dumps(message).encode()


HEADERS = {"Mcp-Method": "tools/call", "Mcp-Name": "lookup"}


# -- protocol --------------------------------------------------------------------------------


def test_parse_valid_tool_call_digests_arguments():
    request = parse_request(_body(), HEADERS)
    assert request.method == "tools/call" and request.tool == "lookup"
    assert request.arguments_digest == arguments_digest({"q": 1})
    assert not request.notification


@pytest.mark.parametrize(
    ("body", "headers", "code", "status"),
    [
        (b"{not json", HEADERS, "MALFORMED_JSON", 400),
        (b"\xff\xfe", HEADERS, "MALFORMED_JSON", 400),
        (b"[" + _body() + b"]", HEADERS, "BATCH_UNSUPPORTED", 400),
        (
            b'{"jsonrpc":"1.0","method":"ping","id":1}',
            {"Mcp-Method": "ping"},
            "MALFORMED_RPC",
            400,
        ),
        (b'{"jsonrpc":"2.0","id":1,"result":{}}', {}, "MALFORMED_RPC", 400),
        (_body("resources/read"), {"Mcp-Method": "resources/read"}, "METHOD_UNSUPPORTED", 403),
        (_body("ping", ident=True), {"Mcp-Method": "ping"}, "MALFORMED_RPC", 400),
        (_body("notifications/initialized", ident=1), {}, "MALFORMED_RPC", 400),
        (_body("ping", ident=None), {"Mcp-Method": "ping"}, "MALFORMED_RPC", 400),
        (_body(), {"Mcp-Name": "lookup"}, "HEADER_MISMATCH", 400),
        (_body(), {"Mcp-Method": "tools/call"}, "HEADER_MISMATCH", 400),
        (_body(), {"Mcp-Method": "tools/call", "Mcp-Name": "delete"}, "HEADER_MISMATCH", 400),
        (
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}',
            {"Mcp-Method": "tools/call"},
            "MALFORMED_RPC",
            400,
        ),
        (
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":""}}',
            {"Mcp-Method": "tools/call", "Mcp-Name": ""},
            "MALFORMED_RPC",
            400,
        ),
        (
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"a","arguments":[]}}',
            {"Mcp-Method": "tools/call", "Mcp-Name": "a"},
            "MALFORMED_RPC",
            400,
        ),
    ],
)
def test_parse_rejects(body, headers, code, status):
    with pytest.raises(PepError) as caught:
        parse_request(body, headers)
    assert caught.value.code == code and caught.value.status == status


def test_parse_rejects_oversize_and_accepts_notification():
    with pytest.raises(PepError, match="limit"):
        parse_request(b"x" * 2048, HEADERS, max_body=1024)
    request = parse_request(
        _body("notifications/initialized", ident=None), {"Mcp-Method": "notifications/initialized"}
    )
    assert request.notification and request.tool is None


def test_rpc_error_and_token_match():
    error = json.loads(rpc_error(7, -32003, "X", "no", held_call_id="h"))
    assert error["id"] == 7 and error["error"]["data"] == {"reason": "X", "held_call_id": "h"}
    import hashlib

    digest = hashlib.sha256(b"t").hexdigest()
    assert token_matches("t", digest) and not token_matches("u", digest)
    assert not token_matches(None, digest)


class _Echo(BaseHTTPRequestHandler):
    key: str | None = hashlib.sha256(b"tok").hexdigest()  # None: an unauthenticated squatter

    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        status = 200 if self.headers["Authorization"] == "Bearer tok" else 401
        data = json.dumps({"path": self.path, "body": json.loads(body)}).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        if self.key:
            nonce = self.headers[NONCE_HEADER]
            self.send_header(MAC_HEADER, response_mac(self.key, nonce, status, data))
        self.end_headers()
        self.wfile.write(data)


def test_host_client_tcp_and_unix(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=server.handle_request, daemon=True).start()
    reply = HostClient(f"http://127.0.0.1:{server.server_address[1]}", "tok").post("/x", {"a": 1})
    assert reply == {"path": "/x", "body": {"a": 1}}
    server.server_close()

    import socketserver

    class Unix(socketserver.UnixStreamServer):
        def get_request(self):
            return super().get_request()[0], ("unix", 0)

    import shutil
    import tempfile

    short = tempfile.mkdtemp(dir="/tmp")  # AF_UNIX paths are limited to ~104 bytes
    path = f"{short}/s.sock"
    unix = Unix(path, _Echo)
    threading.Thread(target=unix.handle_request, daemon=True).start()
    try:
        assert HostClient(f"unix:{path}", "tok").post("/y", {})["path"] == "/y"
    finally:
        unix.server_close()
        shutil.rmtree(short)


def test_host_client_fails_closed(tmp_path):
    with pytest.raises(PepError) as caught:
        HostClient("http://127.0.0.1:1", "t", timeout=1).post("/decide", {})
    assert caught.value.status == 503
    with pytest.raises(PepError):
        HostClient("ftp://x", "t").post("/decide", {})
    server = HTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=server.handle_request, daemon=True).start()
    with pytest.raises(PepError, match="refused"):
        HostClient(f"http://127.0.0.1:{server.server_address[1]}", "wrong").post("/x", {})
    server.server_close()


# -- sidecar ---------------------------------------------------------------------------------


class FakeHost:
    def __init__(self, decision=None, error=None):
        self.decision = decision or {"decision": "allow", "authorization": f"Bearer {TOOL_TOKEN}"}
        self.error = error
        self.calls = []

    def post(self, path, body):
        self.calls.append((path, body))
        if self.error:
            raise self.error
        if path == "/health":
            return {"revision": "r1"}
        return self.decision


@pytest.fixture
def upstream(tmp_path):
    server = Upstream(tmp_path)
    yield server
    server.close()


def _gateway(sidecar):
    server = ThreadingHTTPServer(("127.0.0.1", 0), gateway_handler(sidecar))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _sidecar(upstream, host, **overrides):
    destination = {
        "url": f"https://localhost:{upstream.port}/mcp",
        "allow_private": True,
        "ca_pem": upstream.pem,
        "tools": ["lookup"],
    }
    destination.update(overrides)
    config = {
        "deployment": "d1",
        "code_digest": "sha256:c",
        "host_address": "http://127.0.0.1:1",
        "destinations": {"tools": destination},
        "limits": {"max_body_bytes": 4096, "calls_per_minute": 3},
    }
    return Sidecar(config, "token", host_client=host)


def _post(server, path="/mcp/tools", body=None, headers=None, method="POST"):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    connection.request(
        method,
        path,
        body=_body() if body is None else body,
        headers={"Content-Type": "application/json", **(HEADERS if headers is None else headers)},
    )
    response = connection.getresponse()
    data = response.read()
    connection.close()
    try:
        return response.status, json.loads(data), response
    except ValueError:
        return response.status, data, response


def test_allowed_call_forwarded_with_broker_token_not_agent_token(upstream):
    host = FakeHost()
    server = _gateway(_sidecar(upstream, host))
    try:
        status, body, response = _post(
            server, headers={**HEADERS, "Authorization": "Bearer evil", "X-Leak": "1"}
        )
        assert status == 200 and body["result"] == {"authorized": True, "tool": "lookup"}
        assert response.getheader("Mcp-Session-Id") == "session-1"
        sent = upstream.requests[0]["headers"]
        assert sent["Authorization"] == f"Bearer {TOOL_TOKEN}" and "X-Leak" not in sent
        assert host.calls[0][1]["arguments_digest"] == arguments_digest({"q": 1})
    finally:
        server.shutdown()


@pytest.mark.parametrize(
    ("decision", "status", "rpc"),
    [
        ({"decision": "deny", "code": "POLICY_DENIED", "message": "no"}, 403, -32003),
        ({"decision": "held", "held_call_id": "h1", "retry_after": 30}, 200, -32002),
    ],
)
def test_denied_and_held_calls_are_not_forwarded(upstream, decision, status, rpc):
    server = _gateway(_sidecar(upstream, FakeHost(decision)))
    try:
        got_status, body, _ = _post(server)
        assert got_status == status and body["error"]["code"] == rpc and body["id"] == 1
        assert upstream.requests == []
    finally:
        server.shutdown()


def test_decision_service_down_means_503_and_no_forward(upstream):
    error = PepError("HOST_UNAVAILABLE", "down", 503, -32005)
    server = _gateway(_sidecar(upstream, FakeHost(error=error)))
    try:
        assert _post(server)[0] == 503 and upstream.requests == []
        assert _post(server, path="/healthz", method="GET", body=b"")[0] == 503
    finally:
        server.shutdown()


def test_gateway_rejections(upstream):
    sidecar = _sidecar(upstream, FakeHost())
    server = _gateway(sidecar)
    try:
        assert _post(server, path="/mcp/unknown")[1]["error"]["data"]["reason"] == (
            "DESTINATION_UNAPPROVED"
        )
        assert _post(server, path="/other")[0] == 404
        assert _post(server, headers={"Mcp-Method": "tools/call", "Mcp-Name": "x"})[0] == 400
        assert _post(server, body=b"x" * 5000)[0] == 413
        assert _post(server, method="DELETE")[0] == 405
        assert _post(server, path="/healthz", method="GET", body=b"")[1]["host"] == {
            "revision": "r1"
        }
        assert _post(server, path="/nope", method="GET", body=b"")[0] == 404
        # CONNECT tunnelling through the gateway is not a supported method.
        assert _post(server, path="evil.example.com:443", method="CONNECT", body=b"")[0] == 501
        statuses = [_post(server)[0] for _ in range(4)]
        assert statuses[-1] == 429  # 3 calls per minute in this config
        assert len(upstream.requests) == 3
    finally:
        server.shutdown()


def test_chunked_body_and_bad_length_rejected(upstream):
    server = _gateway(_sidecar(upstream, FakeHost()))
    try:
        assert _post(server, headers={**HEADERS, "Transfer-Encoding": "chunked"})[0] == 411
        connection = socket.create_connection(("127.0.0.1", server.server_address[1]))
        connection.sendall(b"POST /mcp/tools HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n\r\n")
        assert b" 400 " in connection.recv(1024)
        connection.close()
    finally:
        server.shutdown()


def test_redirect_is_refused_not_followed(tmp_path):
    redirecting = Upstream(tmp_path, redirect=True)
    server = _gateway(_sidecar(redirecting, FakeHost()))
    try:
        status, body, _ = _post(server)
        assert status == 502 and body["error"]["data"]["reason"] == "REDIRECT_REFUSED"
        assert len(redirecting.requests) == 1
    finally:
        server.shutdown()
        redirecting.close()


def test_untrusted_certificate_and_unreachable_upstream_fail(upstream):
    server = _gateway(_sidecar(upstream, FakeHost(), ca_pem=None))
    try:
        assert _post(server)[1]["error"]["data"]["reason"] == "UPSTREAM_ERROR"
    finally:
        server.shutdown()
    server = _gateway(_sidecar(upstream, FakeHost(), url="http://localhost/mcp"))
    try:
        assert _post(server)[1]["error"]["data"]["reason"] == "DESTINATION_INVALID"
    finally:
        server.shutdown()


def test_private_destination_refused_unless_allow_listed(upstream):
    server = _gateway(_sidecar(upstream, FakeHost(), allow_private=False))
    try:
        assert _post(server)[1]["error"]["data"]["reason"] == "DESTINATION_PRIVATE"
        assert upstream.requests == []
    finally:
        server.shutdown()


def test_dns_rebinding_any_private_answer_refused():
    def rebinding(host, port, type=None):  # noqa: A002
        return [
            (socket.AF_INET, type, 0, "", ("93.184.216.34", port)),
            (socket.AF_INET, type, 0, "", ("10.0.0.5", port)),
        ]

    with pytest.raises(PepError, match="non-public"):
        resolve_destination("mcp.example.com", 443, False, rebinding)
    assert resolve_destination("mcp.example.com", 443, True, rebinding) == [
        "93.184.216.34",
        "10.0.0.5",
    ]

    def failing(*args, **kwargs):
        raise OSError

    with pytest.raises(PepError, match="resolve"):
        resolve_destination("x", 443, False, failing)
    with pytest.raises(PepError, match="resolve"):
        resolve_destination("x", 443, False, lambda *a, **k: [])


def test_rate_limiter_window():
    now = [0.0]
    limiter = RateLimiter(2, clock=lambda: now[0])
    assert limiter.allow(("a",)) and limiter.allow(("a",)) and not limiter.allow(("a",))
    assert limiter.allow(("b",))
    now[0] = 61
    assert limiter.allow(("a",))


def test_ingress_proxies_to_agent_only():
    class Agent(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            data = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_POST = do_GET  # noqa: N815

    agent = ThreadingHTTPServer(("127.0.0.1", 0), Agent)
    threading.Thread(target=agent.serve_forever, daemon=True).start()
    ingress = ThreadingHTTPServer(
        ("127.0.0.1", 0), ingress_handler("127.0.0.1", agent.server_address[1], 1024)
    )
    threading.Thread(target=ingress.serve_forever, daemon=True).start()
    try:
        assert _post(ingress, path="/health", method="GET", body=b"", headers={})[1] == {
            "ok": True
        }
        assert _post(ingress, path="/invoke", body=b"{}", headers={})[0] == 200
        assert _post(ingress, path="/invoke", body=b"x" * 2048, headers={})[0] == 413
    finally:
        agent.shutdown()
        ingress.shutdown()
    dead = ThreadingHTTPServer(("127.0.0.1", 0), ingress_handler("127.0.0.1", 1, 1024))
    threading.Thread(target=dead.serve_forever, daemon=True).start()
    try:
        assert _post(dead, path="/", method="GET", body=b"", headers={})[0] == 502
    finally:
        dead.shutdown()


# -- probe -----------------------------------------------------------------------------------


def test_probe_reports_reachability_dns_and_gateway():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    class Health(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            data = b'{"deployment":"d"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    health = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=health.serve_forever, daemon=True).start()
    try:
        report = probe_module.probe(
            {
                "tcp": [["127.0.0.1", port], ["127.0.0.1", 1]],
                "dns_must_fail": ["localhost", "does-not-exist.invalid"],
                "gateway": f"http://127.0.0.1:{health.server_address[1]}/healthz",
            },
            timeout=1,
        )
        assert report["tcp_open"] == [["127.0.0.1", port]]
        assert report["dns_resolved"] == ["localhost"]
        assert report["gateway"] == {"status": 200, "body": {"deployment": "d"}}
        missing = probe_module.probe(
            {"gateway": "http://127.0.0.1:1/healthz", "gateway_wait": 0}, timeout=1
        )
        assert missing["gateway"]["status"] is None
    finally:
        listener.close()
        health.shutdown()


# -- Codex review regressions ----------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","method":"ping"}',
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lookup",'
        b'"arguments":{"q":1},"arguments":{"q":2}}}',
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lookup",'
        b'"arguments":{"q":NaN}}}',
    ],
)
def test_ambiguous_json_is_rejected(body):
    with pytest.raises(PepError) as caught:
        parse_request(body, {"Mcp-Method": "ping", **HEADERS})
    assert caught.value.code == "MALFORMED_JSON"


def test_forwarded_body_is_the_validated_message(upstream):
    server = _gateway(_sidecar(upstream, FakeHost()))
    padded = b'{ "jsonrpc" : "2.0", "id": 1, "method": "tools/call",\n "params": ' + (
        b'{"name": "lookup", "arguments": {"q": 1}} }'
    )
    try:
        assert _post(server, body=padded)[0] == 200
        assert upstream.requests[0]["body"] == json.loads(padded)
        assert upstream.requests[0]["headers"]["Content-Length"] == str(
            len(parse_request(padded, HEADERS).body)
        )
    finally:
        server.shutdown()


def test_oversized_stream_ends_with_explicit_error():
    from agentbreeder.egress import sidecar as sidecar_module

    class Response:
        status = 200
        chunks = [b"data: a\n\n", b"data: b\n\n", b""]

        def getheader(self, name):
            return "text/event-stream" if name == "Content-Type" else None

        def read1(self, size):
            return self.chunks.pop(0)

    handler = Mock()
    original = sidecar_module.MAX_RESPONSE
    sidecar_module.MAX_RESPONSE = 12
    try:
        sidecar_module._relay(handler, Response(), ("Content-Type",))
    finally:
        sidecar_module.MAX_RESPONSE = original
    written = b"".join(c.args[0] for c in handler.wfile.write.call_args_list)
    assert written.startswith(b"data: a") and b"RESPONSE_TOO_LARGE" in written
    assert b"data: b" not in written


def test_overflowing_numbers_are_rejected():
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        b'"params":{"name":"lookup","arguments":{"q":1e400}}}'
    )
    with pytest.raises(PepError) as caught:
        parse_request(body, HEADERS)
    assert caught.value.code == "MALFORMED_JSON"


def test_undeclared_tools_refused_before_rate_limit_and_host(upstream):
    host = FakeHost()
    sidecar = _sidecar(upstream, host)
    server = _gateway(sidecar)
    try:
        for index in range(10):
            name = f"x{index}"
            body = _body(name=name)
            status, reply, _ = _post(server, body=body, headers={**HEADERS, "Mcp-Name": name})
            assert status == 403 and reply["error"]["data"]["reason"] == "CAPABILITY_UNDECLARED"
        assert host.calls == [] and sidecar.limiter.calls == {}
        assert _post(server, headers={**HEADERS, "Mcp-Session-Id": "s-1"})[0] == 200
        assert host.calls[0][1]["session"] == "s-1"
    finally:
        server.shutdown()


def test_overlong_session_id_rejected_not_truncated(upstream):
    host = FakeHost()
    server = _gateway(_sidecar(upstream, host))
    try:
        status, reply, _ = _post(server, headers={**HEADERS, "Mcp-Session-Id": "s" * 257})
        assert status == 400 and reply["error"]["data"]["reason"] == "MALFORMED_RPC"
        assert host.calls == [] and upstream.requests == []
    finally:
        server.shutdown()


# -- independent review (2026-11-06) ------------------------------------------------------------


@pytest.mark.parametrize(
    "key", [None, hashlib.sha256(b"guess").hexdigest()], ids=["no-mac", "wrong-key"]
)
def test_host_client_rejects_unauthenticated_decision_service(key, monkeypatch):
    """A local process holding the decision port (Docker Desktop TCP mode) must not be able
    to issue decisions: replies need a MAC keyed by the token hash over the request nonce."""
    monkeypatch.setattr(_Echo, "key", key)
    server = HTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=server.handle_request, daemon=True).start()
    try:
        with pytest.raises(PepError, match="failed authentication") as caught:
            HostClient(f"http://127.0.0.1:{server.server_address[1]}", "tok").post("/decide", {})
        assert caught.value.status == 503
    finally:
        server.server_close()


def test_response_mac_binds_nonce_status_and_body():
    key = hashlib.sha256(b"tok").hexdigest()
    mac = response_mac(key, "a" * 32, 200, b"{}")
    assert mac != response_mac(key, "b" * 32, 200, b"{}")
    assert mac != response_mac(key, "a" * 32, 409, b"{}")
    assert mac != response_mac(key, "a" * 32, 200, b'{"decision":"allow"}')


@pytest.mark.parametrize(
    "body",
    [
        b'{"jsonrpc":"2.0","id":1,"method":"ping","params":'
        + b"[" * 100000
        + b"]" * 100000
        + b"}",
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lookup",'
        b'"arguments":{"q":"\\ud800"}}}',
    ],
    ids=["deep-nesting", "lone-surrogate"],
)
def test_unforwardable_json_is_malformed_not_a_crash(body):
    with pytest.raises(PepError) as caught:
        parse_request(body, {"Mcp-Method": "tools/call", **HEADERS}, max_body=1 << 21)
    assert caught.value.code == "MALFORMED_JSON" and caught.value.status == 400


def test_gateway_answers_lone_surrogate_with_jsonrpc_error(upstream):
    host = FakeHost()
    server = _gateway(_sidecar(upstream, host))
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lookup",'
        b'"arguments":{"q":"\\ud800"}}}'
    )
    try:
        status, reply, _ = _post(server, body=body)
        assert status == 400 and reply["error"]["data"]["reason"] == "MALFORMED_JSON"
        assert host.calls == [] and upstream.requests == []
    finally:
        server.shutdown()


def test_get_stream_is_405_and_routing_headers_rebuilt_from_body(upstream):
    server = _gateway(_sidecar(upstream, FakeHost()))
    try:
        assert _post(server, method="GET", body=b"")[0] == 405
        listing = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode()
        status, _, _ = _post(
            server,
            body=listing,
            headers={"Mcp-Method": "tools/list", "Mcp-Name": "delete_repo"},
        )
        assert status == 200
        sent = upstream.requests[-1]["headers"]
        assert sent["Mcp-Method"] == "tools/list" and "Mcp-Name" not in sent
        assert _post(server)[0] == 200
        sent = upstream.requests[-1]["headers"]
        assert sent["Mcp-Method"] == "tools/call" and sent["Mcp-Name"] == "lookup"
    finally:
        server.shutdown()


@pytest.mark.parametrize(
    "address",
    ["64:ff9b::a00:1", "64:ff9b:1::c0a8:101", "2002:a00:1::1", "::ffff:10.0.0.1"],
    ids=["nat64", "nat64-local", "6to4", "mapped"],
)
def test_ipv6_forms_embedding_private_ipv4_are_private(address):
    def resolver(host, port, type=None):  # noqa: A002
        return [(socket.AF_INET6, type, 0, "", (address, port, 0, 0))]

    with pytest.raises(PepError, match="non-public"):
        resolve_destination("mcp.example.com", 443, False, resolver)
    resolved = resolve_destination("mcp.example.com", 443, True, resolver)
    assert [ipaddress.ip_address(a) for a in resolved] == [ipaddress.ip_address(address)]


def test_nat64_of_public_ipv4_stays_public():
    def resolver(host, port, type=None):  # noqa: A002
        return [(socket.AF_INET6, type, 0, "", ("64:ff9b::808:808", port, 0, 0))]

    assert resolve_destination("mcp.example.com", 443, False, resolver) == ["64:ff9b::808:808"]
