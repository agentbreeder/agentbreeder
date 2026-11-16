"""JSON-RPC validation and the sidecar-to-host decision protocol. Standard library only.

The request body is authoritative. `Mcp-Method` / `Mcp-Name` headers (MCP revision 2026-07-28)
must equal the body; a missing header or a mismatch is a deny, never an inferred allow.
"""

from __future__ import annotations

import hashlib
import heapq
import hmac
import http.client
import json
import secrets
import socket
import time
from dataclasses import dataclass

# Methods the PEP understands. Only tools/call is policy-evaluated; the rest carry no tool effect
# and are forwarded to the approved destination after validation and rate limiting.
METHODS = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "notifications/cancelled",
        "ping",
        "tools/list",
        "tools/call",
    }
)
DEFAULT_MAX_BODY = 1024 * 1024
DEFAULT_CALLS_PER_MINUTE = 60
DECISION_TIMEOUT = 15
_NAME_LIMIT = 128


class PepError(Exception):
    """A request the PEP refuses. `status` is the HTTP status returned to the agent."""

    def __init__(self, code: str, message: str, status: int = 403, rpc_code: int = -32003):
        super().__init__(message)
        self.code = code
        self.status = status
        self.rpc_code = rpc_code


@dataclass(frozen=True)
class RpcRequest:
    method: str
    request_id: object
    tool: str | None
    arguments_digest: str | None
    # The canonical re-serialization of exactly what was validated; this, never the raw bytes,
    # is what the sidecar forwards, so the PEP and the upstream cannot read different requests.
    body: bytes = b""

    @property
    def notification(self) -> bool:
        return self.request_id is None


def _unique_pairs(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate JSON key")
    return dict(pairs)


def _no_constants(name):
    raise ValueError(f"non-finite number {name}")


def _finite_float(text: str) -> float:
    value = float(text)
    if value in (float("inf"), float("-inf")) or value != value:
        raise ValueError("non-finite number")
    return value


def canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def arguments_digest(arguments: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical(arguments).encode("utf-8")).hexdigest()


def parse_request(body: bytes, headers, max_body: int = DEFAULT_MAX_BODY) -> RpcRequest:
    if len(body) > max_body:
        raise PepError("BODY_TOO_LARGE", "Request body exceeds the configured limit", 413, -32600)
    try:
        message = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_no_constants,
            parse_float=_finite_float,
        )
        # Re-encoding proves the body is forwardable: a lone surrogate escape ("\ud800")
        # parses but cannot be encoded, and deep nesting exhausts the recursion limit.
        forwarded = json.dumps(
            message, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (UnicodeError, ValueError, RecursionError) as error:
        raise PepError("MALFORMED_JSON", "Request body is not valid JSON", 400, -32700) from error
    if isinstance(message, list):
        raise PepError("BATCH_UNSUPPORTED", "JSON-RPC batches are not supported", 400, -32600)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise PepError("MALFORMED_RPC", "Not a JSON-RPC 2.0 request", 400, -32600)
    method = message.get("method")
    if not isinstance(method, str):
        raise PepError("MALFORMED_RPC", "Client responses are not supported", 400, -32600)
    if method not in METHODS:
        raise PepError("METHOD_UNSUPPORTED", "MCP method is not supported", 403, -32601)
    request_id = message.get("id")
    if request_id is not None and (
        isinstance(request_id, bool) or not isinstance(request_id, (str, int))
    ):
        raise PepError("MALFORMED_RPC", "Invalid JSON-RPC id", 400, -32600)
    if method.startswith("notifications/") != (request_id is None):
        raise PepError("MALFORMED_RPC", "Notifications must not carry an id", 400, -32600)
    if headers.get("Mcp-Method") != method:
        raise PepError("HEADER_MISMATCH", "Mcp-Method header must equal the body method", 400)
    tool = digest = None
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict):
            raise PepError("MALFORMED_RPC", "tools/call needs params", 400, -32602)
        tool = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(tool, str) or not 0 < len(tool) <= _NAME_LIMIT:
            raise PepError("MALFORMED_RPC", "tools/call needs a tool name", 400, -32602)
        if not isinstance(arguments, dict):
            raise PepError("MALFORMED_RPC", "tools/call arguments must be an object", 400, -32602)
        if headers.get("Mcp-Name") != tool:
            raise PepError("HEADER_MISMATCH", "Mcp-Name header must equal the tool name", 400)
        digest = arguments_digest(arguments)  # encodable: the whole message already was
    return RpcRequest(method, request_id, tool, digest, forwarded)


def rpc_error(request_id, rpc_code: int, code: str, message: str, **data) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": rpc_code, "message": message, "data": {"reason": code, **data}},
        }
    ).encode()


# Decision protocol v2. The per-deployment token never leaves the sidecar: both sides derive
# separate request and reply keys from it (HKDF-SHA256, RFC 5869), the sidecar authenticates
# every request with the request key, and the service authenticates every reply with the reply
# key, bound to that request's nonce, method and path. Only MACs travel on the wire, never key
# material.
PROTOCOL_VERSION = 2
VERSION_HEADER = "X-AgentBreeder-Pep-Version"
NONCE_HEADER = "X-AgentBreeder-Nonce"
TIMESTAMP_HEADER = "X-AgentBreeder-Timestamp"
REQUEST_MAC_HEADER = "X-AgentBreeder-Request-Mac"
MAC_HEADER = "X-AgentBreeder-Mac"  # the reply MAC
MAX_CLOCK_SKEW = 30  # seconds a request may be old
# Seconds a request may be dated ahead of the host. Kept small: it bounds how much of a
# sidecar's own traffic from just before a service restart can still look fresh to the new
# process (whose in-memory nonce cache starts empty).
MAX_FUTURE_SKEW = 5
REPLAY_CAPACITY = 65536  # nonces live at most MAX_CLOCK_SKEW + MAX_FUTURE_SKEW seconds
_HKDF_SALT = b"agentbreeder decision protocol"
_REQUEST_LABEL = b"agentbreeder decision request v2"
_REPLY_LABEL = b"agentbreeder decision reply v2"


class ProtocolMismatch(ValueError):
    """The other side, or the pin, speaks another decision-protocol version."""

    code = "PEP_PROTOCOL_MISMATCH"


class RequestRejected(Exception):
    """A decision-service request that failed authentication. Nothing is decided."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def hkdf_sha256(ikm: bytes, info: bytes, salt: bytes = _HKDF_SALT, length: int = 32) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    output, block, counter = b"", b"", 1
    while len(output) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
        counter += 1
    return output[:length]


@dataclass(frozen=True, repr=False)
class PepKeys:
    request: bytes
    reply: bytes

    def __repr__(self) -> str:  # never print key material
        return "PepKeys(<redacted>)"

    @classmethod
    def from_token(cls, token: str) -> PepKeys:
        if not token:
            raise ValueError("empty decision-service token")
        ikm = token.encode()
        return cls(hkdf_sha256(ikm, _REQUEST_LABEL), hkdf_sha256(ikm, _REPLY_LABEL))

    def pin_fields(self) -> dict:
        """What the host keeps in its 0600 pin: the derived keys, never the token."""
        return {
            "pep_protocol": PROTOCOL_VERSION,
            "request_key": self.request.hex(),
            "reply_key": self.reply.hex(),
        }

    @classmethod
    def from_pin(cls, pin: dict) -> PepKeys:
        if pin.get("pep_protocol") != PROTOCOL_VERSION:
            raise ProtocolMismatch("The egress pin predates decision protocol v2; redeploy")
        invalid = "The egress pin has no valid decision keys; redeploy"
        try:
            request, reply = bytes.fromhex(pin["request_key"]), bytes.fromhex(pin["reply_key"])
        except (KeyError, TypeError, ValueError) as error:
            raise ProtocolMismatch(invalid) from error
        if len(request) != 32 or len(reply) != 32 or request == reply:
            raise ProtocolMismatch(invalid)
        return cls(request, reply)


def valid_nonce(nonce: str | None) -> bool:
    return (
        isinstance(nonce, str)
        and 32 <= len(nonce) <= 128
        and all(c in "0123456789abcdef" for c in nonce)
    )


def _mac(key: bytes, *fields: str) -> str:
    return hmac.new(key, "\n".join(fields).encode(), hashlib.sha256).hexdigest()


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def request_mac(
    key: bytes, method: str, path: str, timestamp: str, nonce: str, body: bytes
) -> str:
    return _mac(key, f"v{PROTOCOL_VERSION} request", method, path, timestamp, nonce, _digest(body))


def response_mac(key: bytes, nonce: str, method: str, path: str, status: int, body: bytes) -> str:
    """Authenticates a reply to the request that carried `nonce` (and `method`/`path`), so a
    reply to one request, or to the unauthenticated `GET /healthz`, cannot answer another."""
    return _mac(key, f"v{PROTOCOL_VERSION} reply", nonce, method, path, str(status), _digest(body))


def response_authentic(
    key: bytes, nonce: str, method: str, path: str, status: int, body: bytes, mac
) -> bool:
    expected = response_mac(key, nonce, method, path, status, body)
    return isinstance(mac, str) and hmac.compare_digest(mac.encode(), expected.encode())


def signed_headers(keys: PepKeys, method: str, path: str, body: bytes, now: float):
    """Headers that authenticate one request; returns `(nonce, headers)`."""
    nonce = secrets.token_hex(16)
    timestamp = str(int(now))
    return nonce, {
        VERSION_HEADER: str(PROTOCOL_VERSION),
        NONCE_HEADER: nonce,
        TIMESTAMP_HEADER: timestamp,
        REQUEST_MAC_HEADER: request_mac(keys.request, method, path, timestamp, nonce, body),
    }


class RequestVerifier:
    """Host side: version, freshness, MAC, then single use of the nonce. Fails closed.

    The nonce cache is in memory and a request is accepted at most once per process. A new
    process also refuses requests dated before it started; after a restart, only requests dated
    at most MAX_FUTURE_SKEW ahead of the host can still look fresh."""

    def __init__(
        self,
        keys: PepKeys,
        clock=time.time,
        window: int = MAX_CLOCK_SKEW,
        future: int = MAX_FUTURE_SKEW,
        capacity: int = REPLAY_CAPACITY,
        started: int | None = None,
    ):
        self.keys = keys
        self.clock = clock
        self.window = window
        self.future = future
        self.capacity = capacity
        self.started = int(clock()) if started is None else started
        self.seen: dict[str, int] = {}
        self._expiries: list[tuple[int, str]] = []  # heap, so pruning is not O(cache)

    def verify(self, method: str, path: str, headers, body: bytes) -> str:
        if headers.get(VERSION_HEADER) != str(PROTOCOL_VERSION):
            raise RequestRejected(ProtocolMismatch.code)
        nonce, timestamp = headers.get(NONCE_HEADER), headers.get(TIMESTAMP_HEADER)
        mac = headers.get(REQUEST_MAC_HEADER)
        if (
            not valid_nonce(nonce)
            or not isinstance(timestamp, str)
            or not timestamp.isascii()
            or not timestamp.isdigit()
            or len(timestamp) > 12
            or not isinstance(mac, str)
        ):
            raise RequestRejected("PEP_AUTH_FAILED")
        now = self.clock()
        signed = int(timestamp)
        if now - signed > self.window or signed - now > self.future:
            raise RequestRejected("PEP_CLOCK_SKEW")
        expected = request_mac(self.keys.request, method, path, timestamp, nonce, body)
        if not hmac.compare_digest(mac.encode(), expected.encode()):
            raise RequestRejected("PEP_AUTH_FAILED")
        if signed < self.started:
            raise RequestRejected("PEP_REPLAYED")  # signed before this process could see it
        # Only authenticated nonces are cached.
        # An entry lives until its timestamp leaves the window, after which the skew check
        # alone refuses a replay.
        while self._expiries and self._expiries[0][0] < now:
            _, old = heapq.heappop(self._expiries)
            self.seen.pop(old, None)
        if nonce in self.seen:
            raise RequestRejected("PEP_REPLAYED")
        if len(self.seen) >= self.capacity:
            raise RequestRejected("PEP_REPLAY_CACHE_FULL")
        self.seen[nonce] = signed + self.window
        heapq.heappush(self._expiries, (signed + self.window, nonce))
        return nonce


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


_REFUSALS = frozenset(
    {"PEP_AUTH_FAILED", "PEP_CLOCK_SKEW", "PEP_REPLAYED", "PEP_REPLAY_CACHE_FULL"}
)


def _refusal_code(data: bytes) -> str:
    try:
        code = json.loads(data.decode()).get("code")
    except (ValueError, AttributeError):
        return "HOST_UNAVAILABLE"
    return code if code in _REFUSALS else "HOST_UNAVAILABLE"


class HostClient:
    """Calls the host decision service. `address` is `unix:/path` or `http://host:port`.

    Keeps only the derived keys; the token itself is never sent or stored here."""

    def __init__(
        self, address: str, token: str, timeout: float = DECISION_TIMEOUT, clock=time.time
    ):
        self.address = address
        self.keys = PepKeys.from_token(token)
        self.timeout = timeout
        self.clock = clock

    def _connection(self) -> http.client.HTTPConnection:
        if self.address.startswith("unix:"):
            return _UnixConnection(self.address[5:], self.timeout)
        if not self.address.startswith("http://"):
            raise PepError("HOST_UNAVAILABLE", "Invalid decision service address", 503, -32005)
        host, _, port = self.address[7:].rstrip("/").rpartition(":")
        return http.client.HTTPConnection(host, int(port), timeout=self.timeout)

    def post(self, path: str, body: dict) -> dict:
        encoded = json.dumps(body).encode()
        nonce, signed = signed_headers(self.keys, "POST", path, encoded, self.clock())
        try:
            connection = self._connection()
            try:
                connection.request(
                    "POST",
                    path,
                    body=encoded,
                    headers={"Content-Type": "application/json", **signed},
                )
                response = connection.getresponse()
                data = response.read(64 * 1024)
                mac = response.getheader(MAC_HEADER)
                version = response.getheader(VERSION_HEADER)
            finally:
                connection.close()
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise PepError(
                "HOST_UNAVAILABLE", "Decision service unavailable; call not forwarded", 503, -32005
            ) from error
        if version != str(PROTOCOL_VERSION) or response.status == 426:
            # Unauthenticated, so it only ever refuses: a sidecar and a decision service from
            # different releases never decide anything together. Redeploy to match them.
            raise PepError(
                ProtocolMismatch.code,
                "Decision service speaks another protocol version; call not forwarded",
                503,
                -32005,
            )
        if response.status == 401:
            # Unauthenticated too, so only a known refusal code is surfaced, as a refusal.
            code = _refusal_code(data)
            raise PepError(code, f"Decision service refused the request ({code})", 503, -32005)
        if response.status != 200:
            raise PepError("HOST_UNAVAILABLE", "Decision service refused the request", 503, -32005)
        if not response_authentic(self.keys.reply, nonce, "POST", path, 200, data, mac):
            raise PepError(
                "HOST_UNAVAILABLE", "Decision service failed authentication", 503, -32005
            )
        try:
            payload = json.loads(data.decode())
        except ValueError as error:
            raise PepError(
                "HOST_UNAVAILABLE", "Decision service unavailable; call not forwarded", 503, -32005
            ) from error
        if not isinstance(payload, dict):
            raise PepError("HOST_UNAVAILABLE", "Decision service refused the request", 503, -32005)
        return payload
