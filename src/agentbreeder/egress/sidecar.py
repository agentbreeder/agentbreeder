"""The policy enforcement point (PEP) in the per-deployment sidecar. Standard library only.

Two listeners:

* gateway (internal network only reaches it): `POST /mcp/<server>` for MCP Streamable HTTP and
  `GET /healthz`. Each request is validated, rate limited, decided by the host decision service
  (which writes the audit row before answering) and only then forwarded over TLS that the sidecar
  originates and validates. The agent's own `Authorization` header is never forwarded.
* ingress (published on host loopback): reverse proxy to the agent's HTTP port.

Configuration arrives in `AGENTBREEDER_SIDECAR_CONFIG` (JSON) and `AGENTBREEDER_PEP_TOKEN`.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import socket
import ssl
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from agentbreeder.egress.protocol import (
    DEFAULT_CALLS_PER_MINUTE,
    DEFAULT_MAX_BODY,
    HostClient,
    PepError,
    parse_request,
    rpc_error,
)

UPSTREAM_TIMEOUT = 60
MAX_RESPONSE = 16 * 1024 * 1024
# Passed through from the agent. `Mcp-Method`/`Mcp-Name` are not: they are rebuilt from the
# parsed body so upstream never sees a header the decision did not cover.
_FORWARD_HEADERS = (
    "Content-Type",
    "Accept",
    "Mcp-Session-Id",
    "Mcp-Protocol-Version",
    "Last-Event-ID",
)
_RETURN_HEADERS = ("Content-Type", "Mcp-Session-Id", "Mcp-Protocol-Version")


class RateLimiter:
    """Sliding one-minute window per key."""

    def __init__(self, per_minute: int, clock=time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self.calls: dict[tuple, deque] = {}
        self.lock = threading.Lock()

    def allow(self, key: tuple) -> bool:
        now = self.clock()
        with self.lock:
            window = self.calls.setdefault(key, deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= self.per_minute:
                return False
            window.append(now)
            return True


class _PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to a pre-resolved address; certificate and SNI still use the hostname."""

    def __init__(self, host, port, addresses, context, timeout):
        super().__init__(host, port, context=context, timeout=timeout)
        self.addresses = addresses

    def connect(self):
        error: OSError = OSError("no address")
        for address in self.addresses:  # every address was validated before pinning
            try:
                raw = socket.create_connection((address, self.port), self.timeout)
                break
            except OSError as failure:
                error = failure
        else:
            raise error
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def _public(address) -> bool:
    """`is_global`, plus the IPv4 address embedded in IPv6 translation and tunnel forms
    (NAT64, 6to4, Teredo, mapped), which `is_global` judges by the IPv6 prefix alone."""
    if not address.is_global:
        return False
    if address.version == 6:
        embedded = [address.ipv4_mapped, address.sixtofour]
        if address.teredo:
            embedded.append(address.teredo[1])
        if any(address in network for network in _NAT64):
            embedded.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
        return all(ip.is_global for ip in embedded if ip is not None)
    return True


def resolve_destination(host: str, port: int, allow_private: bool, resolver=socket.getaddrinfo):
    """Resolve once and pin. Every resolved address must be public unless allow-listed, so a
    rebinding answer cannot smuggle a private target in next to a public one."""
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise PepError("DESTINATION_UNRESOLVED", "Destination did not resolve", 502) from error
    addresses = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not allow_private and not _public(address):
            raise PepError(
                "DESTINATION_PRIVATE", "Destination resolved to a non-public address", 502
            )
        addresses.append(str(address))
    if not addresses:
        raise PepError("DESTINATION_UNRESOLVED", "Destination did not resolve", 502)
    return list(dict.fromkeys(addresses))


class Sidecar:
    def __init__(self, config: dict, token: str, host_client: HostClient | None = None):
        self.config = config
        limits = config.get("limits") or {}
        self.max_body = int(limits.get("max_body_bytes", DEFAULT_MAX_BODY))
        self.limiter = RateLimiter(int(limits.get("calls_per_minute", DEFAULT_CALLS_PER_MINUTE)))
        self.destinations = config.get("destinations") or {}
        self.host = host_client or HostClient(config["host_address"], token)
        self.resolver = socket.getaddrinfo

    # -- gateway -----------------------------------------------------------------------------

    def health(self) -> dict:
        host = self.host.post("/health", {})
        return {
            "deployment": self.config.get("deployment"),
            "code_digest": self.config.get("code_digest"),
            "host": host,
        }

    def decide(self, server: str, body: bytes, headers):
        destination = self.destinations.get(server)
        if destination is None:
            raise PepError("DESTINATION_UNAPPROVED", "Unknown MCP server", 403)
        request = parse_request(body, headers, self.max_body)
        session = headers.get("Mcp-Session-Id")
        if session is not None and not 0 < len(session) <= 256:
            # Bound exactly as forwarded: never truncate an identifier used in a binding.
            raise PepError("MALFORMED_RPC", "Mcp-Session-Id is too long", 400, -32600)
        declared = destination.get("tools")
        if request.tool is not None and (declared is None or request.tool not in declared):
            # Refused before rate limiting and the decision service: invented tool names can
            # neither mint fresh rate-limit buckets nor flood the audit log.
            raise PepError("CAPABILITY_UNDECLARED", "Tool is not a declared capability", 403)
        if not self.limiter.allow((server, request.tool or request.method)):
            raise PepError("RATE_LIMITED", "Call rate limit exceeded", 429, -32029)
        decision = self.host.post(
            "/decide",
            {
                "server": server,
                "method": request.method,
                "tool": request.tool,
                "arguments_digest": request.arguments_digest,
                "request_id": None if request.request_id is None else str(request.request_id),
                "session": session,
            },
        )
        return request, destination, decision

    def connection(self, destination: dict):
        parts = urlsplit(destination["url"])
        if parts.scheme != "https" or not parts.hostname:
            raise PepError("DESTINATION_INVALID", "Destinations must be https URLs", 502)
        port = parts.port or 443
        addresses = resolve_destination(
            parts.hostname, port, bool(destination.get("allow_private")), self.resolver
        )
        context = ssl.create_default_context(cadata=destination.get("ca_pem") or None)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return _PinnedHTTPS(parts.hostname, port, addresses, context, UPSTREAM_TIMEOUT), path


def _send_error(handler, error: PepError, request_id=None, **data):
    body = rpc_error(request_id, error.rpc_code, error.code, str(error), **data)
    handler.send_response(error.status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler, limit: int) -> bytes:
    if handler.headers.get("Transfer-Encoding"):
        raise PepError("LENGTH_REQUIRED", "Chunked request bodies are not supported", 411, -32600)
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as error:
        raise PepError("MALFORMED_RPC", "Invalid Content-Length", 400, -32600) from error
    if length < 0 or length > limit:
        raise PepError("BODY_TOO_LARGE", "Request body exceeds the configured limit", 413, -32600)
    return handler.rfile.read(length)


def _relay(handler, response, allowed_headers) -> None:
    """Stream an upstream response back. Redirects are refused, never followed or relayed."""
    if 300 <= response.status < 400:
        raise PepError("REDIRECT_REFUSED", "Upstream redirect refused", 502, -32005)
    handler.send_response(response.status)
    for name in allowed_headers:
        value = response.getheader(name)
        if value:
            handler.send_header(name, value)
    length = response.getheader("Content-Length")
    if length and length.isdigit():
        handler.send_header("Content-Length", length)
    handler.send_header("Connection", "close")
    handler.end_headers()
    sent = 0
    streaming = "text/event-stream" in (response.getheader("Content-Type") or "")
    while True:
        chunk = response.read1(65536)
        if not chunk:
            break
        sent += len(chunk)
        if sent > MAX_RESPONSE:
            # Never let a cut-off response look complete: SSE clients get an explicit error
            # event; length-delimited clients see a short body and fail.
            if streaming:
                handler.wfile.write(b'\n\nevent: error\ndata: {"reason":"RESPONSE_TOO_LARGE"}\n\n')
            break
        handler.wfile.write(chunk)
        handler.wfile.flush()


def gateway_handler(sidecar: Sidecar):
    class Gateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "agentbreeder-pep"
        sys_version = ""

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            sys.stderr.write(f"gateway {self.command} {self.path.split('?')[0][:80]}\n")

        def do_GET(self):  # noqa: N802 - stdlib naming
            if self.path.startswith("/mcp/"):
                # Server-initiated SSE streams are not mediated (documented).
                return _send_error(
                    self, PepError("METHOD_NOT_ALLOWED", "Method not allowed", 405, -32601)
                )
            if self.path != "/healthz":
                return _send_error(self, PepError("NOT_FOUND", "Not found", 404, -32601))
            try:
                body = json.dumps(sidecar.health()).encode()
            except PepError as error:
                return _send_error(self, error)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            request_id = None
            if not self.path.startswith("/mcp/"):
                return _send_error(self, PepError("NOT_FOUND", "Not found", 404, -32601))
            server = self.path[5:].split("?", 1)[0]
            try:
                body = _read_body(self, sidecar.max_body)
                request, destination, decision = sidecar.decide(server, body, self.headers)
                request_id = request.request_id
                verdict = decision.get("decision")
                if verdict == "held":
                    return _send_error(
                        self,
                        PepError("APPROVAL_PENDING", "Call held for approval", 200, -32002),
                        request_id,
                        held_call_id=decision.get("held_call_id"),
                        retry_after=decision.get("retry_after", 30),
                    )
                if verdict != "allow":
                    return _send_error(
                        self,
                        PepError(
                            str(decision.get("code") or "CALL_DENIED"),
                            str(decision.get("message") or "Call denied by policy"),
                        ),
                        request_id,
                    )
                connection, path = sidecar.connection(destination)
                headers = {k: self.headers[k] for k in _FORWARD_HEADERS if self.headers.get(k)}
                headers["Mcp-Method"] = request.method
                if request.tool is not None:
                    headers["Mcp-Name"] = request.tool
                if decision.get("authorization"):
                    headers["Authorization"] = decision["authorization"]
                try:
                    connection.request("POST", path, body=request.body, headers=headers)
                    _relay(self, connection.getresponse(), _RETURN_HEADERS)
                finally:
                    connection.close()
            except PepError as error:
                _send_error(self, error, request_id)
            except (OSError, http.client.HTTPException, ssl.SSLError):
                _send_error(
                    self,
                    PepError("UPSTREAM_ERROR", "Upstream MCP server unavailable", 502, -32005),
                    request_id,
                )

        def do_PUT(self):  # noqa: N802
            _send_error(self, PepError("METHOD_NOT_ALLOWED", "Method not allowed", 405, -32601))

        do_DELETE = do_PATCH = do_PUT  # noqa: N815

    return Gateway


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})


def loopback_host(value: str) -> bool:
    """The ingress port is published on loopback only. Upstream, the agent sees `Host: agent`,
    so a DNS-rebinding page (Host: its own name) must be refused here, before forwarding."""
    if value.startswith("["):
        name = value.partition("]")[0] + "]"
    else:
        name = value.rpartition(":")[0] if ":" in value else value
    return name in LOOPBACK_HOSTS


def ingress_handler(agent_host: str, agent_port: int, max_body: int):
    class Ingress(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "agentbreeder-ingress"
        sys_version = ""

        def log_message(self, format, *args):  # noqa: A002
            sys.stderr.write(f"ingress {self.command}\n")

        def _proxy(self):
            try:
                if not loopback_host(self.headers.get("Host") or ""):
                    raise PepError("HOST_REFUSED", "Address the agent as 127.0.0.1 or localhost")
                body = _read_body(self, max_body) if self.command == "POST" else None
                connection = http.client.HTTPConnection(agent_host, agent_port, timeout=60)
                try:
                    headers = {
                        k: v
                        for k, v in self.headers.items()
                        if k.lower() in {"content-type", "accept", "authorization"}
                    }
                    connection.request(self.command, self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    self.send_response(response.status)
                    content_type = response.getheader("Content-Type")
                    if content_type:
                        self.send_header("Content-Type", content_type)
                    length = response.getheader("Content-Length")
                    if length and length.isdigit():
                        self.send_header("Content-Length", length)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if self.command != "HEAD":
                        while chunk := response.read1(65536):
                            self.wfile.write(chunk)
                finally:
                    connection.close()
            except PepError as error:
                _send_error(self, error)
            except (OSError, http.client.HTTPException):
                _send_error(self, PepError("AGENT_UNAVAILABLE", "Agent unavailable", 502))

        do_GET = do_POST = do_HEAD = _proxy  # noqa: N815

    return Ingress


def serve(config: dict, token: str) -> None:  # pragma: no cover - exercised in Docker
    sidecar = Sidecar(config, token)
    servers = [
        ThreadingHTTPServer(("0.0.0.0", int(config["gateway_port"])), gateway_handler(sidecar)),
        ThreadingHTTPServer(
            ("0.0.0.0", int(config["ingress_port"])),
            ingress_handler(config["agent_host"], int(config["agent_port"]), sidecar.max_body),
        ),
    ]
    for server in servers:
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        time.sleep(3600)


def main() -> None:  # pragma: no cover - container entry point
    config = json.loads(os.environ["AGENTBREEDER_SIDECAR_CONFIG"])
    token = os.environ.pop("AGENTBREEDER_PEP_TOKEN")
    serve(config, token)


if __name__ == "__main__":  # pragma: no cover
    main()
