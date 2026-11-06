"""JSON-RPC validation and the sidecar-to-host decision protocol. Standard library only.

The request body is authoritative. `Mcp-Method` / `Mcp-Name` headers (MCP revision 2026-07-28)
must equal the body; a missing header or a mismatch is a deny, never an inferred allow.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import secrets
import socket
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


NONCE_HEADER = "X-AgentBreeder-Nonce"
MAC_HEADER = "X-AgentBreeder-Mac"


def valid_nonce(nonce: str | None) -> bool:
    return (
        isinstance(nonce, str)
        and 32 <= len(nonce) <= 128
        and all(c in "0123456789abcdef" for c in nonce)
    )


def response_mac(token_sha256: str, nonce: str, status: int, body: bytes) -> str:
    """Authenticates a decision-service reply to the caller that sent `nonce`.

    Keyed by the token hash, which the host keeps in its 0600 pin and the sidecar derives from
    its token: a process that merely holds the loopback port (Docker Desktop TCP mode) can
    neither answer health checks nor issue decisions."""
    message = nonce.encode() + b"\n" + str(status).encode() + b"\n" + body
    return hmac.new(token_sha256.encode(), message, hashlib.sha256).hexdigest()


def response_authentic(token_sha256: str, nonce: str, status: int, body: bytes, mac) -> bool:
    expected = response_mac(token_sha256, nonce, status, body)
    return isinstance(mac, str) and hmac.compare_digest(mac, expected)


def token_matches(presented: str | None, expected_sha256: str) -> bool:
    if not presented:
        return False
    digest = hashlib.sha256(presented.encode()).hexdigest()
    return hmac.compare_digest(digest, expected_sha256)


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class HostClient:
    """Calls the host decision service. `address` is `unix:/path` or `http://host:port`."""

    def __init__(self, address: str, token: str, timeout: float = DECISION_TIMEOUT):
        self.address = address
        self.token = token
        self.key = hashlib.sha256(token.encode()).hexdigest()
        self.timeout = timeout

    def _connection(self) -> http.client.HTTPConnection:
        if self.address.startswith("unix:"):
            return _UnixConnection(self.address[5:], self.timeout)
        if not self.address.startswith("http://"):
            raise PepError("HOST_UNAVAILABLE", "Invalid decision service address", 503, -32005)
        host, _, port = self.address[7:].rstrip("/").rpartition(":")
        return http.client.HTTPConnection(host, int(port), timeout=self.timeout)

    def post(self, path: str, body: dict) -> dict:
        nonce = secrets.token_hex(16)
        try:
            connection = self._connection()
            try:
                connection.request(
                    "POST",
                    path,
                    body=json.dumps(body).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.token}",
                        NONCE_HEADER: nonce,
                    },
                )
                response = connection.getresponse()
                data = response.read(64 * 1024)
                mac = response.getheader(MAC_HEADER)
            finally:
                connection.close()
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise PepError(
                "HOST_UNAVAILABLE", "Decision service unavailable; call not forwarded", 503, -32005
            ) from error
        if response.status != 200:
            raise PepError("HOST_UNAVAILABLE", "Decision service refused the request", 503, -32005)
        if not response_authentic(self.key, nonce, response.status, data, mac):
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
