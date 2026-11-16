"""Decision protocol v2: derived keys, request authentication, replay, skew, versioning."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import Mock

import pytest

from agentbreeder.egress import host as host_module
from agentbreeder.egress.boundary import Boundary, BoundaryUnverified
from agentbreeder.egress.host import egress_dir, make_server, write_private_json
from agentbreeder.egress.protocol import (
    MAC_HEADER,
    MAX_FUTURE_SKEW,
    NONCE_HEADER,
    PROTOCOL_VERSION,
    REPLAY_CAPACITY,
    REQUEST_MAC_HEADER,
    TIMESTAMP_HEADER,
    VERSION_HEADER,
    HostClient,
    PepError,
    PepKeys,
    ProtocolMismatch,
    RequestRejected,
    RequestVerifier,
    hkdf_sha256,
    request_mac,
    response_authentic,
    response_mac,
    signed_headers,
)

TOKEN = "per-deployment-token-value"
NOW = 1_800_000_000


def test_hkdf_matches_rfc5869_test_case_1():
    okm = hkdf_sha256(
        bytes([0x0B] * 22), bytes(range(0xF0, 0xFA)), salt=bytes(range(0x0D)), length=42
    )
    assert okm.hex() == (
        "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
    )


def test_keys_are_separate_and_not_the_token_hash():
    keys = PepKeys.from_token(TOKEN)
    assert keys.request != keys.reply and len(keys.request) == len(keys.reply) == 32
    legacy = hashlib.sha256(TOKEN.encode()).hexdigest()
    assert legacy.encode() not in (keys.request, keys.reply)
    assert PepKeys.from_token("other").reply != keys.reply
    assert TOKEN not in repr(keys) and keys.reply.hex() not in repr(keys)
    with pytest.raises(ValueError):
        PepKeys.from_token("")


def test_pin_holds_derived_keys_only():
    fields = PepKeys.from_token(TOKEN).pin_fields()
    assert fields["pep_protocol"] == PROTOCOL_VERSION
    text = json.dumps(fields)
    assert TOKEN not in text and hashlib.sha256(TOKEN.encode()).hexdigest() not in text
    assert PepKeys.from_pin(fields) == PepKeys.from_token(TOKEN)


@pytest.mark.parametrize(
    "pin",
    [
        {"token_sha256": "ab" * 32},  # written before v2
        {"pep_protocol": 1, "request_key": "aa" * 32, "reply_key": "bb" * 32},
        {"pep_protocol": PROTOCOL_VERSION},
        {"pep_protocol": PROTOCOL_VERSION, "request_key": "zz", "reply_key": "bb" * 32},
        {"pep_protocol": PROTOCOL_VERSION, "request_key": "aa" * 16, "reply_key": "bb" * 32},
        {"pep_protocol": PROTOCOL_VERSION, "request_key": "aa" * 32, "reply_key": "aa" * 32},
    ],
    ids=["v1", "wrong-version", "no-keys", "not-hex", "short", "same-key"],
)
def test_old_or_broken_pins_are_refused(pin):
    with pytest.raises(ProtocolMismatch) as caught:
        PepKeys.from_pin(pin)
    assert caught.value.code == "PEP_PROTOCOL_MISMATCH"


# -- request verification ---------------------------------------------------------------------


def _verifier(keys, **options):
    return RequestVerifier(keys, clock=lambda: NOW, started=0, **options)


def _signed(keys, path="/decide", body=b"{}", now=NOW, method="POST"):
    return signed_headers(keys, method, path, body, now)


def test_verifier_accepts_once_then_refuses_replay():
    keys = PepKeys.from_token(TOKEN)
    verifier = RequestVerifier(keys, clock=lambda: NOW, started=0)
    nonce, headers = _signed(keys)
    assert verifier.verify("POST", "/decide", headers, b"{}") == nonce
    with pytest.raises(RequestRejected) as caught:
        verifier.verify("POST", "/decide", headers, b"{}")
    assert caught.value.code == "PEP_REPLAYED"


@pytest.mark.parametrize("offset", [-31, 6, 30, -10_000, 10_000])
def test_verifier_refuses_clock_skew(offset):
    keys = PepKeys.from_token(TOKEN)
    _, headers = _signed(keys, now=NOW + offset)
    with pytest.raises(RequestRejected) as caught:
        RequestVerifier(keys, clock=lambda: NOW, started=0).verify(
            "POST", "/decide", headers, b"{}"
        )
    assert caught.value.code == "PEP_CLOCK_SKEW"


@pytest.mark.parametrize("offset", [-30, 0, 5])
def test_verifier_accepts_inside_the_window(offset):
    keys = PepKeys.from_token(TOKEN)
    _, headers = _signed(keys, now=NOW + offset)
    _verifier(keys).verify("POST", "/decide", headers, b"{}")


def test_replay_after_the_window_is_refused_by_the_skew_check():
    keys = PepKeys.from_token(TOKEN)
    clock = {"now": NOW}
    verifier = RequestVerifier(keys, clock=lambda: clock["now"], started=0)
    _, headers = _signed(keys)
    verifier.verify("POST", "/decide", headers, b"{}")
    clock["now"] = NOW + 31
    with pytest.raises(RequestRejected) as caught:
        verifier.verify("POST", "/decide", headers, b"{}")
    assert caught.value.code == "PEP_CLOCK_SKEW"
    # The next authenticated request prunes the expired entry: the cache stays bounded.
    fresh, headers = _signed(keys, now=NOW + 31)
    verifier.verify("POST", "/decide", headers, b"{}")
    assert list(verifier.seen) == [fresh]


@pytest.mark.parametrize(
    "change",
    [
        {"path": "/health"},
        {"method": "PUT"},
        {"body": b'{"tool":"other"}'},
    ],
    ids=["path", "method", "body"],
)
def test_request_mac_binds_method_path_and_body(change):
    keys = PepKeys.from_token(TOKEN)
    _, headers = _signed(keys)
    call = {"method": "POST", "path": "/decide", "body": b"{}", **change}
    with pytest.raises(RequestRejected) as caught:
        RequestVerifier(keys, clock=lambda: NOW, started=0).verify(
            call["method"], call["path"], headers, call["body"]
        )
    assert caught.value.code == "PEP_AUTH_FAILED"


@pytest.mark.parametrize(
    "tamper",
    [
        {NONCE_HEADER: "cd" * 16},
        {NONCE_HEADER: "short"},
        {NONCE_HEADER: None},
        {TIMESTAMP_HEADER: str(NOW + 1)},
        {TIMESTAMP_HEADER: "-1"},
        {TIMESTAMP_HEADER: "١٨"},  # non-ASCII digits
        {TIMESTAMP_HEADER: "9" * 13},
        {REQUEST_MAC_HEADER: None},
        {REQUEST_MAC_HEADER: "00" * 32},
    ],
)
def test_verifier_refuses_tampered_or_missing_fields(tamper):
    keys = PepKeys.from_token(TOKEN)
    _, headers = _signed(keys)
    headers = {k: v for k, v in {**headers, **tamper}.items() if v is not None}
    with pytest.raises(RequestRejected) as caught:
        RequestVerifier(keys, clock=lambda: NOW, started=0).verify(
            "POST", "/decide", headers, b"{}"
        )
    assert caught.value.code in {"PEP_AUTH_FAILED", "PEP_CLOCK_SKEW"}


def test_reply_key_and_other_tokens_never_authenticate_a_request():
    keys = PepKeys.from_token(TOKEN)
    for wrong in (PepKeys(keys.reply, keys.request), PepKeys.from_token("guess")):
        _, headers = _signed(wrong)
        with pytest.raises(RequestRejected):
            RequestVerifier(keys, clock=lambda: NOW, started=0).verify(
                "POST", "/decide", headers, b"{}"
            )


@pytest.mark.parametrize("version", [None, "1", "3"])
def test_verifier_refuses_other_versions(version):
    keys = PepKeys.from_token(TOKEN)
    _, headers = _signed(keys)
    headers = (
        {**headers, VERSION_HEADER: version}
        if version
        else {k: v for k, v in headers.items() if k != VERSION_HEADER}
    )
    with pytest.raises(RequestRejected) as caught:
        RequestVerifier(keys, clock=lambda: NOW, started=0).verify(
            "POST", "/decide", headers, b"{}"
        )
    assert caught.value.code == "PEP_PROTOCOL_MISMATCH"


def test_a_restarted_service_refuses_requests_signed_before_it_started():
    """The nonce cache is per process: each request is accepted at most once, also by a new
    service process."""
    keys = PepKeys.from_token(TOKEN)
    _, received = _signed(keys, now=NOW - 5)
    RequestVerifier(keys, clock=lambda: NOW, started=NOW - 10).verify(
        "POST", "/decide", received, b"{}"
    )
    restarted = RequestVerifier(keys, clock=lambda: NOW)  # started now
    with pytest.raises(RequestRejected) as caught:
        restarted.verify("POST", "/decide", received, b"{}")
    assert caught.value.code == "PEP_REPLAYED"
    _, fresh = _signed(keys, now=NOW)
    restarted.verify("POST", "/decide", fresh, b"{}")


def test_after_a_restart_only_the_future_skew_bound_can_look_fresh():
    """Documents the bound: a request dated at most MAX_FUTURE_SKEW ahead of the new process's
    start is fresh to it once; anything dated further ahead is refused outright."""
    keys = PepKeys.from_token(TOKEN)
    restarted = RequestVerifier(keys, clock=lambda: NOW)
    _, ahead = _signed(keys, now=NOW + MAX_FUTURE_SKEW)
    restarted.verify("POST", "/decide", ahead, b"{}")
    with pytest.raises(RequestRejected):
        restarted.verify("POST", "/decide", ahead, b"{}")
    _, beyond = _signed(keys, now=NOW + MAX_FUTURE_SKEW + 1)
    with pytest.raises(RequestRejected) as caught:
        restarted.verify("POST", "/decide", beyond, b"{}")
    assert caught.value.code == "PEP_CLOCK_SKEW"


def test_replay_cache_is_bounded_and_fails_closed():
    keys = PepKeys.from_token(TOKEN)
    verifier = RequestVerifier(keys, clock=lambda: NOW, started=0, capacity=2)
    assert REPLAY_CAPACITY >= 60_000  # far above what the sidecar's rate limits allow
    for _ in range(2):
        _, headers = _signed(keys)
        verifier.verify("POST", "/decide", headers, b"{}")
    _, headers = _signed(keys)
    with pytest.raises(RequestRejected) as caught:
        verifier.verify("POST", "/decide", headers, b"{}")
    assert caught.value.code == "PEP_REPLAY_CACHE_FULL"


def test_unauthenticated_requests_never_enter_the_replay_cache():
    keys = PepKeys.from_token(TOKEN)
    verifier = RequestVerifier(keys, clock=lambda: NOW, started=0, capacity=1)
    _, wrong = _signed(PepKeys.from_token("guess"))
    for _ in range(5):
        with pytest.raises(RequestRejected):
            verifier.verify("POST", "/decide", wrong, b"{}")
    _, headers = _signed(keys)
    verifier.verify("POST", "/decide", headers, b"{}")  # still room for the real sidecar


# -- replies are accepted only from the holder of the token ---------------------------------------


class _NoToken(BaseHTTPRequestHandler):
    """A decision service without the token: it MACs its replies with keys built only from
    what the request carries (headers and body)."""

    received: list = []
    strategy = "request-mac"

    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.received.append((dict(self.headers.items()), body))
        nonce = self.headers.get(NONCE_HEADER, "")
        data = json.dumps({"decision": "allow", "authorization": "Bearer other"}).encode()
        material = {
            "request-mac": lambda: self.headers.get(REQUEST_MAC_HEADER, ""),
            # keys built only from the request
            "hash-of-headers": lambda: response_mac(
                hashlib.sha256(json.dumps(dict(self.headers.items())).encode()).digest(),
                nonce,
                "POST",
                self.path,
                200,
                data,
            ),
            "request-mac-as-key": lambda: response_mac(
                bytes.fromhex(self.headers.get(REQUEST_MAC_HEADER, "")),
                nonce,
                "POST",
                self.path,
                200,
                data,
            ),
        }[self.strategy]
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(VERSION_HEADER, str(PROTOCOL_VERSION))
        self.send_header(MAC_HEADER, material())
        self.end_headers()
        self.wfile.write(data)


@pytest.mark.parametrize("strategy", ["request-mac", "hash-of-headers", "request-mac-as-key"])
def test_replies_without_the_token_are_never_accepted(strategy, monkeypatch):
    monkeypatch.setattr(_NoToken, "received", [])
    monkeypatch.setattr(_NoToken, "strategy", strategy)
    server = HTTPServer(("127.0.0.1", 0), _NoToken)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = HostClient(f"http://127.0.0.1:{server.server_address[1]}", TOKEN)
    try:
        for _ in range(3):  # repeated exchanges change nothing
            with pytest.raises(PepError, match="failed authentication") as caught:
                client.post("/decide", {"server": "tools", "tool": "lookup"})
            assert caught.value.status == 503
    finally:
        server.shutdown()
        server.server_close()
    keys = PepKeys.from_token(TOKEN)
    secrets_on_wire = (
        TOKEN,
        hashlib.sha256(TOKEN.encode()).hexdigest(),
        keys.request.hex(),
        keys.reply.hex(),
    )
    for headers, body in _NoToken.received:
        wire = json.dumps(headers) + body.decode()
        assert not any(secret in wire for secret in secrets_on_wire)
        assert "Authorization" not in headers


def test_client_keeps_no_token():
    client = HostClient("http://127.0.0.1:1", TOKEN)
    assert TOKEN not in json.dumps({k: repr(v) for k, v in vars(client).items()})


# -- the real decision service ----------------------------------------------------------------


@pytest.fixture
def service_server(tmp_path):
    service = Mock()
    service.health.return_value = {"revision": "r"}
    service.decide.return_value = {"decision": "allow", "authorization": None}
    keys = PepKeys.from_token(TOKEN)
    server, address = make_server(service, {"listen": "tcp", **keys.pin_fields()}, tmp_path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server, service
    server.shutdown()
    server.server_close()


def _raw(server, method, path, body=b"", headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_service_round_trip_and_replay(service_server):
    server, service = service_server
    client = HostClient(f"http://127.0.0.1:{server.server_address[1]}", TOKEN)
    assert client.post("/decide", {"server": "s"}) == {"decision": "allow", "authorization": None}
    assert client.post("/health", {}) == {"revision": "r"}
    # The same signed request a second time is refused before any decision is made.
    keys = PepKeys.from_token(TOKEN)
    body = b'{"server": "s"}'
    import time

    _, headers = signed_headers(keys, "POST", "/decide", body, time.time())
    service.decide.reset_mock()
    assert _raw(server, "POST", "/decide", body, headers)[0] == 200
    status, reply_headers, data = _raw(server, "POST", "/decide", body, headers)
    assert status == 401 and json.loads(data) == {"code": "PEP_REPLAYED"}
    assert MAC_HEADER not in reply_headers  # a refusal yields no MAC for a chosen nonce
    service.decide.assert_called_once()


def test_service_refuses_a_skewed_client(service_server):
    import time

    server, service = service_server
    client = HostClient(
        f"http://127.0.0.1:{server.server_address[1]}", TOKEN, clock=lambda: time.time() - 120
    )
    with pytest.raises(PepError) as caught:
        client.post("/decide", {})
    assert caught.value.code == "PEP_CLOCK_SKEW" and caught.value.status == 503
    service.decide.assert_not_called()


def test_service_refuses_a_wrong_token(service_server):
    server, service = service_server
    client = HostClient(f"http://127.0.0.1:{server.server_address[1]}", "guess")
    with pytest.raises(PepError) as caught:
        client.post("/decide", {})
    assert caught.value.code == "PEP_AUTH_FAILED"
    service.decide.assert_not_called()


def test_old_sidecar_against_new_service_fails_closed_with_a_code(service_server, capfd):
    """A v1 sidecar sends its bearer token and no version header: refused, nothing decided."""
    server, service = service_server
    status, headers, data = _raw(
        server,
        "POST",
        "/decide",
        b"{}",
        {"Authorization": f"Bearer {TOKEN}", NONCE_HEADER: "ab" * 16},
    )
    assert status == 426 and json.loads(data) == {"code": "PEP_PROTOCOL_MISMATCH"}
    assert MAC_HEADER not in headers
    service.decide.assert_not_called()
    assert "PEP_PROTOCOL_MISMATCH" in capfd.readouterr().err


class _V1Service(BaseHTTPRequestHandler):
    """A decision service from before v2: bearer auth, no version header."""

    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        key = hashlib.sha256(TOKEN.encode()).hexdigest().encode()
        status = 200 if self.headers.get("Authorization") == f"Bearer {TOKEN}" else 401
        data = b"{}"
        nonce = self.headers.get(NONCE_HEADER, "")
        mac = hmac.new(key, f"{nonce}\n{status}\n".encode() + data, hashlib.sha256).hexdigest()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.send_header(MAC_HEADER, mac)
        self.end_headers()
        self.wfile.write(data)


def test_new_sidecar_against_old_service_fails_closed_with_a_code():
    server = HTTPServer(("127.0.0.1", 0), _V1Service)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(PepError) as caught:
            HostClient(f"http://127.0.0.1:{server.server_address[1]}", TOKEN).post("/decide", {})
        assert caught.value.code == "PEP_PROTOCOL_MISMATCH" and caught.value.status == 503
    finally:
        server.shutdown()
        server.server_close()


def test_healthz_reply_cannot_answer_a_decision(service_server):
    """The unauthenticated `/healthz` MACs any caller's nonce; that MAC is bound to GET /healthz
    and so never authenticates a POST reply for the same nonce."""
    server, _ = service_server
    nonce = "ab" * 16
    status, headers, data = _raw(server, "GET", "/healthz", headers={NONCE_HEADER: nonce})
    keys = PepKeys.from_token(TOKEN)
    assert status == 200
    assert response_authentic(keys.reply, nonce, "GET", "/healthz", 200, data, headers[MAC_HEADER])
    assert not response_authentic(
        keys.reply, nonce, "POST", "/health", 200, data, headers[MAC_HEADER]
    )


def test_request_mac_helper_is_deterministic():
    keys = PepKeys.from_token(TOKEN)
    one = request_mac(keys.request, "POST", "/decide", "1", "ab" * 16, b"{}")
    assert one == request_mac(keys.request, "POST", "/decide", "1", "ab" * 16, b"{}")
    assert one != request_mac(keys.reply, "POST", "/decide", "1", "ab" * 16, b"{}")


# -- old pins on the host -------------------------------------------------------------------


def test_service_process_refuses_a_v1_pin(tmp_path, capsys):
    directory = egress_dir(tmp_path, "dep")
    directory.mkdir(parents=True)
    write_private_json(directory / "pin.json", {"deployment": "dep", "token_sha256": "ab" * 32})
    assert host_module.run(tmp_path, "dep") == 2
    assert "PEP_PROTOCOL_MISMATCH" in capsys.readouterr().err
    assert not (directory / "endpoint.json").exists()


def test_boundary_never_restarts_or_trusts_a_v1_pin(tmp_path):
    boundary = Boundary.__new__(Boundary)
    boundary.state_dir = tmp_path
    boundary.spawn = Mock()
    record = {"id": "dep"}
    directory = egress_dir(tmp_path, "dep")
    directory.mkdir(parents=True)
    write_private_json(
        directory / "pin.json", {"listen": "unix", "revision": "r", "token_sha256": "ab" * 32}
    )
    assert not boundary.service_healthy(record)
    with pytest.raises(BoundaryUnverified, match="PEP_PROTOCOL_MISMATCH"):
        boundary.start_service(record)
    boundary.spawn.assert_not_called()
