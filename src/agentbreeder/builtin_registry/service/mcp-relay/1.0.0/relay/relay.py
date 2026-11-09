"""AgentBreeder MCP relay (service/mcp-relay@1.0.0). Standard library only.

One relay serves one MCP server for one stack (spec §7.4, §7.6, §7.7). It holds the upstream
credential; agents hold only a per-deployment token. For every request it:

- authenticates the token by its sha256 grant file, re-read on every request, and intersects
  the file's tools with the stack's configured grant and the version's declared tools;
- accepts only initialize, notifications/initialized, notifications/cancelled, ping,
  tools/list and tools/call (no batches, resources, prompts, sampling or GET streams);
- mints its own session ids, mapped per token to upstream sessions;
- answers tools/list from the pinned (locked) tool definitions, filtered to the grant, and
  hides and refuses tools whose live definition changed or that are missing upstream;
- passes back only the matching response plus progress and log notifications, and rewrites the
  initialize result's capabilities to {tools: {}};
- connects upstream over https to port 443 only, to an address resolved and checked once per
  connection (no private, loopback or link-local targets), with no redirects and no proxies.

It never logs arguments, results, upstream error bodies or credentials.

Files: /run/params/config (JSON, no secrets), /run/secrets/<name> (0400 credentials),
/run/grants/<env>/<server>/<sha256>.json (read-only), /run/pins/pins.json (read-only).
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import stat
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlsplit

VERSION = "1.0.0"
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
# Handshake-era protocol versions only (spec §7.7.7): sessions exist, and tools/list is paged.
PROTOCOL_VERSIONS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"})
PASS_NOTIFICATIONS = frozenset({"notifications/progress", "notifications/message"})
TOKEN_CHARS = frozenset("0123456789abcdef")
GRANT_KEYS = frozenset({"deployment", "agent", "environment", "server", "tools"})
ENVIRONMENT = re.compile(r"[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?")
DEPLOYMENT = re.compile(r"[0-9a-f]{32}")
SESSION = re.compile(r"[0-9a-f]{32}")
MAX_BODY = 1024 * 1024
REWRITE_MAX = 1024 * 1024  # buffered responses: initialize and tools/list pages
STREAM_MAX = 16 * 1024 * 1024
DEADLINE = 120.0
CONCURRENCY = 4
CALLS_PER_MINUTE = 60  # the egress sidecar's default
# Methods that reach upstream (tools/list runs the live check) count against the rate limit.
METERED = frozenset({"initialize", "tools/list", "tools/call", "notifications/cancelled"})
CLIENT_TIMEOUT = 30.0  # an agent connection idle this long mid-request is closed
MAX_CONNECTIONS = 64
CLOSE_DEADLINE = 30.0
SESSIONS_PER_TOKEN = 16
SESSION_IDLE = 3600.0
CHECK_AGE = 300.0
PAGES = 20
GRANT_LIMIT = 64 * 1024
DEFAULT_PATHS = {
    "config": "/run/params/config",
    "secrets": "/run/secrets",
    "grants": "/run/grants",
    "pins": "/run/pins/pins.json",
}


class RelayError(Exception):
    """A refusal. `status` is the HTTP status, `rpc` the JSON-RPC error code."""

    def __init__(self, code: str, message: str, status: int = 403, rpc: int = -32003):
        super().__init__(message)
        self.code = code
        self.status = status
        self.rpc = rpc


# -- JSON -----------------------------------------------------------------------------------------


def _unique(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate JSON key")
    return dict(pairs)


def _no_constant(name):
    raise ValueError(f"non-finite number {name}")


def _finite(text: str) -> float:
    value = float(text)
    if value in (float("inf"), float("-inf")) or value != value:
        raise ValueError("non-finite number")
    return value


def loads(data: bytes):
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique,
        parse_constant=_no_constant,
        parse_float=_finite,
    )


def dumps(value) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def tool_hash(tool: dict) -> str:
    """The whole canonical tool object: name, title, description, schemas, annotations, _meta."""
    return "sha256:" + hashlib.sha256(canonical(tool)).hexdigest()


def rpc_error(request_id, error: RelayError) -> bytes:
    return dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": error.rpc, "message": str(error), "data": {"reason": error.code}},
        }
    )


@dataclass(frozen=True)
class Request:
    method: str
    id: object
    tool: str | None
    message: dict
    body: bytes  # the canonical re-serialisation of exactly what was validated

    @property
    def notification(self) -> bool:
        return self.id is None


def parse_request(body: bytes, headers) -> Request:
    """Validate one JSON-RPC request. `Mcp-Method` / `Mcp-Name` are optional, and when present
    must equal the body (the body is authoritative)."""
    if len(body) > MAX_BODY:
        raise RelayError("BODY_TOO_LARGE", "Request body exceeds the limit", 413, -32600)
    try:
        message = loads(body)
        forwarded = dumps(message)
    except (UnicodeError, ValueError, RecursionError):
        raise RelayError("MALFORMED_JSON", "Request body is not valid JSON", 400, -32700) from None
    if isinstance(message, list):
        raise RelayError("BATCH_UNSUPPORTED", "JSON-RPC batches are not supported", 400, -32600)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise RelayError("MALFORMED_RPC", "Not a JSON-RPC 2.0 request", 400, -32600)
    method = message.get("method")
    if not isinstance(method, str):
        raise RelayError("MALFORMED_RPC", "Client responses are not supported", 400, -32600)
    request_id = message.get("id")
    if request_id is not None and (
        isinstance(request_id, bool) or not isinstance(request_id, (str, int))
    ):
        raise RelayError("MALFORMED_RPC", "Invalid JSON-RPC id", 400, -32600)
    if method not in METHODS:
        raise RelayError("METHOD_UNSUPPORTED", "MCP method is not supported", 403, -32601)
    if method.startswith("notifications/") != (request_id is None):
        raise RelayError("MALFORMED_RPC", "Notifications carry no id", 400, -32600)
    if headers.get("Mcp-Method") is not None and headers.get("Mcp-Method") != method:
        raise RelayError("HEADER_MISMATCH", "Mcp-Method must equal the body method", 400, -32600)
    tool = None
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict):
            raise RelayError("MALFORMED_RPC", "tools/call needs params", 400, -32602)
        tool = params.get("name")
        if not isinstance(tool, str) or not 0 < len(tool) <= 128:
            raise RelayError("MALFORMED_RPC", "tools/call needs a tool name", 400, -32602)
        if not isinstance(params.get("arguments", {}), dict):
            raise RelayError("MALFORMED_RPC", "tools/call arguments are an object", 400, -32602)
        if headers.get("Mcp-Name") is not None and headers.get("Mcp-Name") != tool:
            raise RelayError("HEADER_MISMATCH", "Mcp-Name must equal the tool name", 400, -32600)
    if method == "notifications/cancelled":
        message = cancel_message(message.get("params"))
        forwarded = dumps(message)
    return Request(method, request_id, tool, message, forwarded)


def cancel_message(params) -> dict:
    """Only `{requestId, reason?}` is forwarded. The relay's own request ids (`relay-N`) can't
    be cancelled by an agent."""
    request_id = params.get("requestId") if isinstance(params, dict) else None
    reason = params.get("reason") if isinstance(params, dict) else None
    if (
        not isinstance(params, dict)
        or not set(params) <= {"requestId", "reason"}
        or isinstance(request_id, bool)
        or not isinstance(request_id, (str, int))
        or (isinstance(request_id, str) and request_id.startswith("relay-"))
        or (reason is not None and not isinstance(reason, str))
    ):
        raise RelayError("MALFORMED_RPC", "Invalid notifications/cancelled params", 400, -32602)
    clean = {"requestId": request_id}
    if reason is not None:
        clean["reason"] = reason[:256]
    return {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": clean}


# -- network: resolve once, check, pin (as the egress sidecar does) ----------------------------

_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def public(address) -> bool:
    """`is_global`, plus the IPv4 address embedded in IPv6 translation and tunnel forms."""
    if not address.is_global:
        return False
    if address.version == 6:
        embedded = [address.ipv4_mapped, address.sixtofour]
        if address.teredo:
            embedded.append(address.teredo[1])
        if any(address in network for network in _NAT64) or int(address) >> 32 == 0:
            # NAT64, and the deprecated IPv4-compatible ::a.b.c.d form.
            embedded.append(ipaddress.IPv4Address(int(address) & 0xFFFFFFFF))
        return all(ip.is_global for ip in embedded if ip is not None)
    return True


def resolve_destination(host: str, port: int, allow_private: bool, resolver=socket.getaddrinfo):
    """Every resolved address must be public, so a rebinding answer can't add a private one."""
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise RelayError(
            "DESTINATION_UNRESOLVED", "Upstream did not resolve", 502, -32005
        ) from None
    addresses = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not allow_private and not public(address):
            raise RelayError(
                "DESTINATION_PRIVATE", "Upstream resolved to a non-public address", 502, -32005
            )
        addresses.append(str(address))
    if not addresses:
        raise RelayError("DESTINATION_UNRESOLVED", "Upstream did not resolve", 502, -32005)
    return list(dict.fromkeys(addresses))


class PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to a pre-resolved address; certificate and SNI still use the hostname."""

    def __init__(self, host, port, addresses, context, timeout):
        super().__init__(host, port, context=context, timeout=timeout)
        self.addresses = addresses

    def connect(self):
        error: OSError = OSError("no address")
        for address in self.addresses:  # every address was checked before pinning
            try:
                raw = socket.create_connection((address, self.port), self.timeout)
                break
            except OSError as failure:
                error = failure
        else:
            raise error
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def strip_proxies(environ=os.environ) -> None:
    """http.client never reads them, but nothing in the relay should be able to."""
    for key in list(environ):
        if key.upper().endswith("_PROXY"):
            del environ[key]


# -- configuration, grants and pins -------------------------------------------------------------


@dataclass(frozen=True)
class Grant:
    deployment: str
    agent: str
    environment: str
    token_hash: str
    tools: frozenset


def read_limited(path: str, limit: int) -> bytes | None:
    """A regular file (not a symlink), at most `limit` bytes; None if absent or unusable."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        return None
    with os.fdopen(descriptor, "rb") as handle:
        data = handle.read(limit + 1)
    return data if len(data) <= limit else None


def token_from(authorization: str | None) -> str | None:
    """The bearer token, or None. Shape is checked before hashing: at most 128 characters and
    lowercase hex only, then exactly 64 (256-bit)."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    if len(token) > 128 or not set(token) <= TOKEN_CHARS or len(token) != 64:
        return None
    return token


class Pins:
    """The pinned tool definitions, reloaded whenever the file changes (re-pinning doesn't
    recreate the relay)."""

    def __init__(self, path: str):
        self.path = path
        self.stamp = None
        self.tools: dict[str, dict] = {}
        self.lock = threading.Lock()

    def current(self) -> dict[str, dict]:
        try:
            info = os.stat(self.path)
            stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
        except OSError:
            stamp = None
        with self.lock:
            if stamp != self.stamp:
                self.stamp = stamp
                self.tools = self._load() if stamp else {}
            return self.tools

    def _load(self) -> dict[str, dict]:
        data = read_limited(self.path, 4 * 1024 * 1024)
        try:
            document = loads(data) if data else {}
        except (UnicodeError, ValueError, RecursionError):
            document = {}
        tools = document.get("tools") if isinstance(document, dict) else None
        result = {}
        for name, entry in (tools or {}).items():
            if not isinstance(entry, dict) or not isinstance(entry.get("definition"), dict):
                continue
            definition = entry["definition"]
            # A pin is only trusted when its definition still hashes to its recorded hash.
            if definition.get("name") == name and tool_hash(definition) == entry.get("sha256"):
                result[name] = entry
        return result


@dataclass
class Session:
    id: str
    token_hash: str
    upstream: str | None
    protocol: str
    used: float
    checked: float | None = None  # when the definitions were last checked live; never yet


@dataclass
class Live:
    """The last live check of upstream definitions: tool -> ok | changed | missing."""

    status: dict[str, str] = field(default_factory=dict)
    at: float = 0.0


# -- the relay ----------------------------------------------------------------------------------


class Relay:
    def __init__(self, config: dict, paths: dict | None = None, clock=time.monotonic):
        self.config = config
        self.paths = {**DEFAULT_PATHS, **(paths or {})}
        self.server = config["server"]
        self.upstream = config["upstream"]
        self.auth = config["auth"]
        self.declared = frozenset(config["tools"])
        self.agents = {agent: frozenset(tools) for agent, tools in config["agents"].items()}
        self.allowed_ports = frozenset(config.get("allowed_ports") or [443])
        self.admin_hash = config.get("admin_token_sha256") or ""
        self.pins = Pins(self.paths["pins"])
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self.sessions_lock = threading.Lock()
        self.active: dict[str, int] = {}
        self.active_lock = threading.Lock()
        self.calls: dict[str, deque] = {}
        self.calls_lock = threading.Lock()
        self.live = Live()
        self.live_lock = threading.Lock()
        self.oauth: tuple[str, float] | None = None
        self.oauth_lock = threading.Lock()
        self.secret_cache: dict[str, str] = {}
        # Tests only: a private CA and loopback upstreams. The generated config never sets them.
        self.ssl_context: ssl.SSLContext | None = None
        self.allow_private = False
        self.resolver = socket.getaddrinfo
        self.log_stream = sys.stderr
        self.log_lock = threading.Lock()
        self.counter = 0

    # -- logs -------------------------------------------------------------------------------

    def log(self, **fields) -> None:
        line = json.dumps({"ts": round(time.time(), 3), "server": self.server, **fields})
        with self.log_lock:
            self.log_stream.write(line + "\n")
            self.log_stream.flush()

    # -- grants -----------------------------------------------------------------------------

    def grant(self, authorization: str | None) -> Grant:
        token = token_from(authorization)
        if token is None:
            raise RelayError("UNAUTHENTICATED", "A valid relay token is required", 401, -32001)
        digest = hashlib.sha256(token.encode()).hexdigest()
        root = self.paths["grants"]
        try:
            environments = sorted(os.listdir(root))
        except OSError:
            environments = []
        for environment in environments:
            if not ENVIRONMENT.fullmatch(environment):
                continue
            data = read_limited(
                os.path.join(root, environment, self.server, digest + ".json"), GRANT_LIMIT
            )
            if data is None:
                continue
            grant = self._grant(data, environment, digest)
            if grant is not None:
                return grant
        raise RelayError("UNAUTHENTICATED", "A valid relay token is required", 401, -32001)

    def _grant(self, data: bytes, environment: str, digest: str) -> Grant | None:
        try:
            document = loads(data)
        except (UnicodeError, ValueError, RecursionError):
            return None
        if not isinstance(document, dict) or set(document) != GRANT_KEYS:
            return None
        tools = document["tools"]
        agent = document["agent"]
        if (
            document["server"] != self.server
            or document["environment"] != environment
            or not isinstance(document["deployment"], str)
            or not DEPLOYMENT.fullmatch(document["deployment"])
            or not isinstance(agent, str)
            or not isinstance(tools, list)
            or not all(isinstance(tool, str) for tool in tools)
        ):
            return None
        # A narrowed stack grant takes effect at once: the file only ever loses tools here.
        allowed = frozenset(tools) & self.agents.get(agent, frozenset()) & self.declared
        return Grant(document["deployment"], agent, environment, digest, allowed)

    # -- concurrency --------------------------------------------------------------------------

    def acquire(self, token_hash: str) -> None:
        with self.active_lock:
            if self.active.get(token_hash, 0) >= int(self.config.get("concurrency", CONCURRENCY)):
                raise RelayError("CONCURRENCY_LIMIT", "Too many concurrent requests", 429, -32029)
            self.active[token_hash] = self.active.get(token_hash, 0) + 1

    def meter(self, token_hash: str) -> None:
        """A sliding one-minute window of upstream-bound calls per token."""
        limit = int(self.config.get("calls_per_minute", CALLS_PER_MINUTE))
        now = self.clock()
        with self.calls_lock:
            for key in [k for k, w in self.calls.items() if not w or now - w[-1] >= 60]:
                del self.calls[key]
            window = self.calls.setdefault(token_hash, deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= limit:
                raise RelayError("RATE_LIMITED", "Call rate limit exceeded", 429, -32029)
            window.append(now)

    def release(self, token_hash: str) -> None:
        with self.active_lock:
            count = self.active.get(token_hash, 1) - 1
            if count:
                self.active[token_hash] = count
            else:
                self.active.pop(token_hash, None)

    # -- sessions -----------------------------------------------------------------------------

    def session(self, session_id: str | None, grant: Grant) -> Session:
        now = self.clock()
        with self.sessions_lock:
            gone = self._expire(now)
            session = self.sessions.get(session_id or "")
            # Another token's id is indistinguishable from an unknown one.
            if session is not None and hmac.compare_digest(session.token_hash, grant.token_hash):
                session.used = now
        self.close_later(gone)
        if session is None or not hmac.compare_digest(session.token_hash, grant.token_hash):
            raise RelayError("SESSION_UNKNOWN", "Unknown or expired session", 404, -32001)
        return session

    def _expire(self, now: float) -> list[Session]:
        gone = [s for s in self.sessions.values() if now - s.used > SESSION_IDLE]
        for session in gone:
            del self.sessions[session.id]
        return gone

    def close_later(self, sessions: list[Session]) -> None:
        """Close dropped sessions upstream too, off the request path, so upstream sessions
        (a browser context, say) don't accumulate."""
        sessions = [s for s in sessions if s.upstream]
        if sessions:
            threading.Thread(target=self._close_all, args=(sessions,), daemon=True).start()

    def _close_all(self, sessions: list[Session]) -> None:
        for session in sessions:
            try:
                self.close_upstream(session, self.clock() + CLOSE_DEADLINE)
            except (OSError, http.client.HTTPException, ssl.SSLError):
                pass

    def open_session(self, grant: Grant, upstream: str | None, protocol: str) -> Session:
        now = self.clock()
        session = Session(secrets.token_hex(16), grant.token_hash, upstream, protocol, now)
        with self.sessions_lock:
            gone = self._expire(now)
            mine = sorted(
                (s for s in self.sessions.values() if s.token_hash == grant.token_hash),
                key=lambda s: s.used,
            )
            for old in mine[: max(0, len(mine) - SESSIONS_PER_TOKEN + 1)]:
                del self.sessions[old.id]
                gone.append(old)
            self.sessions[session.id] = session
        self.close_later(gone)
        return session

    def drop_session(self, session: Session) -> None:
        with self.sessions_lock:
            self.sessions.pop(session.id, None)

    # -- upstream -----------------------------------------------------------------------------

    def secret(self, name: str) -> str:
        if name not in self.secret_cache:
            data = read_limited(os.path.join(self.paths["secrets"], name), 16 * 1024)
            value = data.decode("utf-8", "strict").strip() if data else ""
            if not value or any(c in value for c in "\r\n\0"):
                raise RelayError(
                    "CREDENTIAL_UNAVAILABLE", "Upstream credential missing", 503, -32005
                )
            self.secret_cache[name] = value
        return self.secret_cache[name]

    def connection(self, url: str, deadline: float):
        parts = urlsplit(url)
        timeout = max(1.0, min(60.0, deadline - self.clock()))
        if self.upstream.get("kind") == "service" and url == self.upstream["url"]:
            # A stack service on a private network shared only with this relay.
            if parts.scheme != "http" or not parts.hostname:
                raise RelayError("UPSTREAM_INVALID", "Invalid service upstream", 502, -32005)
            connection = http.client.HTTPConnection(
                parts.hostname, parts.port or 80, timeout=timeout
            )
        else:
            port = parts.port or 443
            if parts.scheme != "https" or not parts.hostname or port not in self.allowed_ports:
                raise RelayError(
                    "UPSTREAM_INVALID", "Upstream must be https on port 443", 502, -32005
                )
            addresses = resolve_destination(
                parts.hostname, port, self.allow_private, self.resolver
            )
            context = self.ssl_context or ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            connection = PinnedHTTPS(parts.hostname, port, addresses, context, timeout)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return connection, path

    def credential_headers(self, deadline: float) -> dict[str, str]:
        kind = self.auth.get("type")
        if kind == "bearer":
            return {"Authorization": "Bearer " + self.secret(self.auth["secret"])}
        if kind == "header":
            return {self.auth["header"]: self.secret(self.auth["secret"])}
        if kind == "oauth_refresh":
            return {"Authorization": "Bearer " + self.access_token(deadline)}
        return {}

    def access_token(self, deadline: float) -> str:
        """An OAuth access token from the refresh token, cached in memory until shortly before
        it expires. Providers that rotate refresh tokens are refused. The token endpoint's
        body and error_description are never echoed or logged."""
        with self.oauth_lock:
            now = self.clock()
            if self.oauth is not None and now < self.oauth[1]:
                return self.oauth[0]
            oauth = self.auth["oauth"]
            refresh = self.secret(oauth["refresh_token"])
            form = {
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": self.secret(oauth["client_id"]),
                "client_secret": self.secret(oauth["client_secret"]),
            }
            if oauth.get("scopes"):
                form["scope"] = " ".join(oauth["scopes"])
            failed = RelayError("OAUTH_FAILED", "Upstream token refresh failed", 502, -32005)
            connection, path = self.connection(oauth["token_url"], deadline)
            try:
                connection.request(
                    "POST",
                    path,
                    body=urlencode(form).encode(),
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                )
                response = connection.getresponse()
                data = response.read(64 * 1024 + 1)
            except (OSError, http.client.HTTPException, ssl.SSLError):
                raise failed from None
            finally:
                connection.close()
            if response.status != 200 or len(data) > 64 * 1024:
                raise failed
            try:
                document = loads(data)
            except (UnicodeError, ValueError, RecursionError):
                raise failed from None
            if not isinstance(document, dict):
                raise failed
            rotated = document.get("refresh_token")
            if rotated is not None and rotated != refresh:
                raise RelayError(
                    "OAUTH_ROTATING_REFRESH",
                    "The provider rotates refresh tokens, which the relay doesn't support",
                    502,
                    -32005,
                )
            token = document.get("access_token")
            expires = document.get("expires_in", 3600)
            if (
                not isinstance(token, str)
                or not 0 < len(token) <= 8192
                or any(c in token for c in "\r\n\0 ")
                or isinstance(expires, bool)
                or not isinstance(expires, (int, float))
            ):
                raise failed
            self.oauth = (token, now + max(0.0, min(float(expires), 86400.0) - 60.0))
            return token

    def call(
        self,
        body: bytes,
        *,
        method: str,
        tool: str | None,
        session: Session | None,
        deadline: float,
        http_method: str = "POST",
    ):
        """Send one request upstream; returns (response, connection). Redirects are refused."""
        connection, path = self.connection(self.upstream["url"], deadline)
        headers = {
            "Accept": "application/json, text/event-stream",
            **self.credential_headers(deadline),
        }
        if http_method == "POST":
            headers["Content-Type"] = "application/json"
            headers["Mcp-Method"] = method
            if tool is not None:
                headers["Mcp-Name"] = tool
        if session is not None:
            headers["Mcp-Protocol-Version"] = session.protocol
            if session.upstream:
                headers["Mcp-Session-Id"] = session.upstream
        try:
            connection.request(http_method, path, body=body or None, headers=headers)
            sock = connection.sock  # getresponse() forgets it when the reply closes the connection
            response = connection.getresponse()
            response.relay_socket = sock
        except TimeoutError:
            connection.close()
            raise RelayError(
                "UPSTREAM_TIMEOUT", "Upstream exceeded the time limit", 504, -32005
            ) from None
        except (OSError, http.client.HTTPException, ssl.SSLError):
            connection.close()
            raise RelayError(
                "UPSTREAM_ERROR", "Upstream MCP server unavailable", 502, -32005
            ) from None
        if 300 <= response.status < 400:
            connection.close()
            raise RelayError("REDIRECT_REFUSED", "Upstream redirect refused", 502, -32005)
        return response, connection

    def _status(self, response, session: Session | None) -> None:
        if response.status == 404 and session is not None:
            # The upstream session is gone: forget ours too, so the client initializes again.
            self.drop_session(session)
            raise RelayError("SESSION_UNKNOWN", "Unknown or expired session", 404, -32001)
        if response.status in (401, 403):
            raise RelayError(
                "UPSTREAM_AUTH", "Upstream refused the relay's credential", 502, -32005
            )
        if not 200 <= response.status < 300:
            raise RelayError(
                "UPSTREAM_ERROR", "Upstream MCP server returned an error", 502, -32005
            )

    def messages(self, response, deadline: float, limit: int):
        """Every JSON-RPC message of a JSON or SSE response, in order."""
        content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
        if content_type == "application/json":
            data = self._read(response, deadline, limit)
            try:
                message = loads(data)
            except (UnicodeError, ValueError, RecursionError):
                raise RelayError(
                    "UPSTREAM_INVALID", "Upstream sent invalid JSON", 502, -32005
                ) from None
            yield message
            return
        if content_type != "text/event-stream":
            raise RelayError(
                "UPSTREAM_INVALID", "Upstream sent an unexpected content type", 502, -32005
            )
        yield from self._events(response, deadline, limit)

    def _chunk(self, response, deadline: float) -> bytes:
        """One read, which may block only for the time left before the deadline."""
        self._check_deadline(deadline)
        sock = getattr(response, "relay_socket", None)
        if sock is not None:
            sock.settimeout(max(0.05, deadline - self.clock()))
        try:
            return response.read1(65536)
        except TimeoutError:
            raise RelayError(
                "UPSTREAM_TIMEOUT", "Upstream exceeded the time limit", 504, -32005
            ) from None

    def _read(self, response, deadline: float, limit: int) -> bytes:
        chunks, size = [], 0
        while True:
            chunk = self._chunk(response, deadline)
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > limit:
                raise RelayError(
                    "RESPONSE_TOO_LARGE", "Upstream response exceeds the limit", 502, -32005
                )
            chunks.append(chunk)

    def _check_deadline(self, deadline: float) -> None:
        if self.clock() >= deadline:
            raise RelayError("UPSTREAM_TIMEOUT", "Upstream exceeded the time limit", 504, -32005)

    def _events(self, response, deadline: float, limit: int):
        buffer = b""
        data: list[bytes] = []
        size = 0
        while True:
            chunk = self._chunk(response, deadline)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise RelayError(
                    "RESPONSE_TOO_LARGE", "Upstream response exceeds the limit", 502, -32005
                )
            buffer += chunk
            while True:
                line, newline, rest = buffer.partition(b"\n")
                if not newline:
                    break
                buffer = rest
                line = line.rstrip(b"\r")
                if not line:
                    if data:
                        yield self._event(data)
                        data = []
                elif line.startswith(b"data:"):
                    data.append(line[5:].removeprefix(b" "))
                # event:, id:, retry: and comments are dropped: ids would allow resumption.
        # An event cut off before its blank line is discarded, as the SSE spec requires.

    def _event(self, data: list[bytes]):
        try:
            return loads(b"\n".join(data))
        except (UnicodeError, ValueError, RecursionError):
            raise RelayError(
                "UPSTREAM_INVALID", "Upstream sent an invalid event", 502, -32005
            ) from None

    @staticmethod
    def classify(message, request_id) -> str:
        """response: the reply to this request; pass: progress or log; drop: anything else
        (sampling, elicitation, roots, list_changed, server pings, other ids)."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return "drop"
        if "method" in message:
            if message["method"] in PASS_NOTIFICATIONS and "id" not in message:
                return "pass"
            return "drop"
        if (
            message.get("id") == request_id
            and type(message.get("id")) is type(request_id)
            and (("result" in message) != ("error" in message))
        ):
            return "response"
        return "drop"

    def response(self, response, request_id, deadline: float, limit: int) -> dict:
        """The matching response of a buffered exchange; everything else is dropped."""
        for message in self.messages(response, deadline, limit):
            if self.classify(message, request_id) == "response":
                return message
        raise RelayError("UPSTREAM_INVALID", "Upstream sent no response", 502, -32005)

    def exchange(
        self, message: dict, session: Session | None, deadline: float, *, tool: str | None = None
    ) -> tuple[dict, object]:
        """A buffered request (initialize, a tools/list page). Returns (response, http)."""
        response, connection = self.call(
            dumps(message), method=message["method"], tool=tool, session=session, deadline=deadline
        )
        try:
            self._status(response, session)
            reply = self.response(response, message.get("id"), deadline, REWRITE_MAX)
            return reply, response
        finally:
            connection.close()

    def notify(self, message: dict, session: Session, deadline: float) -> None:
        response, connection = self.call(
            dumps(message), method=message["method"], tool=None, session=session, deadline=deadline
        )
        try:
            self._status(response, session)
        finally:
            connection.close()

    def next_id(self) -> str:
        with self.live_lock:
            self.counter += 1
            return f"relay-{self.counter}"

    # -- pinning ------------------------------------------------------------------------------

    def upstream_tools(self, session: Session, deadline: float) -> dict[str, dict]:
        """Every tool upstream lists, following nextCursor up to 20 pages."""
        tools: dict[str, dict] = {}
        cursor = None
        for _ in range(PAGES):
            params = {"cursor": cursor} if cursor else {}
            message = {
                "jsonrpc": "2.0",
                "id": self.next_id(),
                "method": "tools/list",
                "params": params,
            }
            reply, _ = self.exchange(message, session, deadline)
            result = reply.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise RelayError("UPSTREAM_INVALID", "Upstream tools/list failed", 502, -32005)
            for tool in result["tools"]:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    tools.setdefault(tool["name"], tool)
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                return tools
        raise RelayError("UPSTREAM_INVALID", "Upstream tools/list has too many pages", 502, -32005)

    def check(self, session: Session, deadline: float) -> dict[str, str]:
        """Compare live definitions with the pins; tools that differ are hidden and refused."""
        live = self.upstream_tools(session, deadline)
        pins = self.pins.current()
        status = {}
        for name in sorted(self.declared):
            if name not in pins:
                status[name] = "unpinned"
            elif name not in live:
                status[name] = "missing"
            elif tool_hash(live[name]) != pins[name]["sha256"]:
                status[name] = "changed"
            else:
                status[name] = "ok"
        with self.live_lock:
            self.live = Live(status, self.clock())
        session.checked = self.clock()
        for name, state in status.items():
            if state in ("changed", "missing"):
                self.log(event="pin_check", tool=name, decision=state)
        return status

    def probe(self, deadline: float) -> dict:
        """Phase 2 of `services up` (spec §7.7.1): definitions of every declared tool, upstream
        now, for the operator to pin. Runs in its own upstream session, closed afterwards."""
        session, _ = self.handshake(None, deadline)
        try:
            live = self.upstream_tools(session, deadline)
        finally:
            self.close_upstream(session, deadline)
        found = {
            name: {"sha256": tool_hash(live[name]), "definition": live[name]}
            for name in sorted(self.declared)
            if name in live
        }
        return {
            "server": self.server,
            "tools": found,
            "missing": sorted(self.declared - set(live)),
        }

    # -- protocol handlers --------------------------------------------------------------------

    def handshake(self, request: Request | None, deadline: float, grant: Grant | None = None):
        """Initialize upstream; returns (session, rewritten result). The relay's own probe
        passes no request and gets an unregistered session."""
        if request is not None:
            params = request.message.get("params")
            version = params.get("protocolVersion") if isinstance(params, dict) else None
            if version not in PROTOCOL_VERSIONS:
                raise RelayError(
                    "PROTOCOL_UNSUPPORTED", "Unsupported MCP protocol version", 400, -32602
                )
            message = {**request.message, "params": {**params, "capabilities": {}}}
        else:
            message = {
                "jsonrpc": "2.0",
                "id": self.next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": max(PROTOCOL_VERSIONS),
                    "capabilities": {},
                    "clientInfo": {"name": "agentbreeder-mcp-relay", "version": VERSION},
                },
            }
        # The relay offers upstream no client capabilities (no sampling, roots or elicitation).
        reply, response = self.exchange(message, None, deadline)
        upstream_id = response.getheader("Mcp-Session-Id")
        if upstream_id is not None and not re.fullmatch(r"[\x21-\x7e]{1,256}", upstream_id):
            raise RelayError("UPSTREAM_INVALID", "Upstream session id is invalid", 502, -32005)
        try:
            return self._complete(reply, upstream_id, deadline, grant)
        except RelayError:
            # Never leave an upstream session behind that no relay session refers to.
            self.close_upstream(Session("", "", upstream_id, "", self.clock()), deadline)
            raise

    def _complete(self, reply: dict, upstream_id, deadline: float, grant: Grant | None):
        result = reply.get("result")
        protocol = result.get("protocolVersion") if isinstance(result, dict) else None
        if "error" in reply:
            raise RelayError("UPSTREAM_ERROR", "Upstream refused initialize", 502, -32005)
        if protocol not in PROTOCOL_VERSIONS:
            raise RelayError(
                "UPSTREAM_PROTOCOL_UNSUPPORTED",
                "Upstream speaks no supported MCP protocol version",
                502,
                -32005,
            )
        temporary = Session("", "", upstream_id, protocol, self.clock())
        self.notify({"jsonrpc": "2.0", "method": "notifications/initialized"}, temporary, deadline)
        info = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
        rewritten = {
            "protocolVersion": protocol,
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": _label(info.get("name"), "upstream"),
                "version": _label(info.get("version"), "unknown"),
            },
        }
        if grant is None:
            return temporary, rewritten
        return self.open_session(grant, upstream_id, protocol), rewritten

    def close_upstream(self, session: Session, deadline: float) -> None:
        if not session.upstream:
            return
        try:
            response, connection = self.call(
                b"", method="", tool=None, session=session, deadline=deadline, http_method="DELETE"
            )
            connection.close()
        except RelayError:
            pass

    def handle(self, grant: Grant, request: Request, session_id: str | None):
        """Dispatch a validated request. Returns ("json", status, body, headers) or
        ("stream", response, connection) for a forwarded tools/call."""
        deadline = self.clock() + float(self.config.get("deadline", DEADLINE))
        if request.method in METERED:
            self.meter(grant.token_hash)
        if request.method == "initialize":
            if session_id is not None:
                raise RelayError("MALFORMED_RPC", "initialize starts a new session", 400, -32600)
            session, result = self.handshake(request, deadline, grant)
            body = dumps({"jsonrpc": "2.0", "id": request.id, "result": result})
            return "json", 200, body, {"Mcp-Session-Id": session.id}
        session = self.session(session_id, grant)
        if request.method == "notifications/initialized":
            return "json", 202, b"", {}  # the relay already completed the upstream handshake
        if request.method == "notifications/cancelled":
            self.notify(request.message, session, deadline)
            return "json", 202, b"", {}
        if request.method == "ping":
            return "json", 200, dumps({"jsonrpc": "2.0", "id": request.id, "result": {}}), {}
        if request.method == "tools/list":
            status = self.check(session, deadline)
            pins = self.pins.current()  # may have been replaced since the check: keep both
            tools = [
                pins[name]["definition"]
                for name in sorted(grant.tools)
                if status.get(name) == "ok" and name in pins
            ]
            body = dumps({"jsonrpc": "2.0", "id": request.id, "result": {"tools": tools}})
            return "json", 200, body, {}
        # tools/call
        if request.tool not in grant.tools:
            raise RelayError(
                "TOOL_NOT_GRANTED", "Tool is not granted to this deployment", 200, -32602
            )
        if session.checked is None or self.clock() - session.checked > CHECK_AGE:
            self.check(session, deadline)
        with self.live_lock:
            state = self.live.status.get(request.tool)
        if state == "changed":
            raise RelayError(
                "TOOL_DEFINITION_CHANGED",
                "The tool's definition changed upstream since it was pinned",
                200,
                -32602,
            )
        if state == "missing":
            raise RelayError("TOOL_MISSING_UPSTREAM", "The tool is missing upstream", 200, -32602)
        if state != "ok":
            raise RelayError("TOOL_NOT_PINNED", "The tool's definition isn't pinned", 200, -32602)
        response, connection = self.call(
            request.body,
            method=request.method,
            tool=request.tool,
            session=session,
            deadline=deadline,
        )
        try:
            self._status(response, session)
        except RelayError:
            connection.close()
            raise
        return "stream", response, connection, deadline

    def stream(self, response, connection, request_id, deadline: float, write) -> str:
        """Forward a tools/call reply: only its response, progress and log messages."""
        try:
            for message in self.messages(response, deadline, STREAM_MAX):
                kind = self.classify(message, request_id)
                if kind == "drop":
                    continue
                write(message)
                if kind == "response":
                    return "allow"
        finally:
            connection.close()
        raise RelayError("UPSTREAM_INVALID", "Upstream sent no response", 502, -32005)


def _label(value, default: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"[\x20-\x7e]{1,64}", value):
        return value
    return default


# -- HTTP -----------------------------------------------------------------------------------------


def _send(handler, status: int, body: bytes, headers: dict | None = None) -> None:
    handler.send_response(status)
    if body:
        handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.send_header("Connection", "close")
    handler.end_headers()
    if body:
        handler.wfile.write(body)


def _read_body(handler) -> bytes:
    if handler.headers.get("Transfer-Encoding"):
        raise RelayError(
            "LENGTH_REQUIRED", "Chunked request bodies are not supported", 411, -32600
        )
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        raise RelayError("MALFORMED_RPC", "Invalid Content-Length", 400, -32600) from None
    if length < 0 or length > MAX_BODY:
        raise RelayError("BODY_TOO_LARGE", "Request body exceeds the limit", 413, -32600)
    return handler.rfile.read(length)


def agent_handler(relay: Relay):
    class Agent(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "agentbreeder-mcp-relay"
        sys_version = ""
        timeout = CLIENT_TIMEOUT  # a stalled client can't hold a thread (or a slot) forever

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass  # the relay writes its own JSON lines

        def _path(self) -> str:
            return self.path.split("?", 1)[0]

        def do_GET(self):  # noqa: N802 - stdlib naming
            if self._path() == "/healthz":
                return _send(self, 200, dumps({"status": "ok"}))
            # No standalone server stream: server-initiated messages are never relayed.
            error = RelayError("METHOD_NOT_ALLOWED", "Method not allowed", 405, -32601)
            _send(self, 405, rpc_error(None, error), {"Allow": "POST, DELETE"})

        def do_DELETE(self):  # noqa: N802
            if self._path() != "/mcp":
                return _send(self, 404, b"")
            started = relay.clock()
            fields = {"method": "DELETE"}
            try:
                grant = relay.grant(self.headers.get("Authorization"))
                fields.update(deployment=grant.deployment, agent=grant.agent)
                session = relay.session(self.headers.get("Mcp-Session-Id"), grant)
                relay.drop_session(session)
                relay.close_upstream(session, relay.clock() + 30)
                _send(self, 200, b"")
                fields["decision"] = "closed"
            except RelayError as error:
                _send(self, error.status, rpc_error(None, error))
                fields["decision"] = error.code
            relay.log(**fields, ms=round((relay.clock() - started) * 1000))

        def do_POST(self):  # noqa: N802
            if self._path() != "/mcp":
                return _send(self, 404, b"")
            started = relay.clock()
            fields: dict = {"method": None}
            request_id = None
            grant = None
            acquired = False
            try:
                grant = relay.grant(self.headers.get("Authorization"))
                fields.update(deployment=grant.deployment, agent=grant.agent)
                relay.acquire(grant.token_hash)
                acquired = True
                request = parse_request(_read_body(self), self.headers)
                request_id = request.id
                fields["method"] = request.method
                if request.tool is not None:
                    if request.tool in grant.tools:
                        fields["tool"] = request.tool
                    else:
                        fields["tool"] = "ungranted"
                        fields["tool_sha256"] = hashlib.sha256(request.tool.encode()).hexdigest()[
                            :16
                        ]
                outcome = relay.handle(grant, request, self.headers.get("Mcp-Session-Id"))
                if outcome[0] == "json":
                    _, status, body, headers = outcome
                    _send(self, status, body, headers)
                    fields["decision"] = "allow"
                else:
                    _, response, connection, deadline = outcome
                    fields["decision"] = self._stream(response, connection, request_id, deadline)
            except RelayError as error:
                fields["decision"] = error.code
                if not getattr(self, "_streaming", False):
                    status = error.status if error.status != 200 or request_id is not None else 400
                    _send(self, status, rpc_error(request_id, error))
                else:
                    self._event(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {
                                "code": error.rpc,
                                "message": str(error),
                                "data": {"reason": error.code},
                            },
                        }
                    )
            except (OSError, http.client.HTTPException, ssl.SSLError):
                fields["decision"] = "UPSTREAM_ERROR"
                if not getattr(self, "_streaming", False):
                    error = RelayError(
                        "UPSTREAM_ERROR", "Upstream MCP server unavailable", 502, -32005
                    )
                    _send(self, 502, rpc_error(request_id, error))
            finally:
                if acquired:
                    relay.release(grant.token_hash)
            relay.log(**fields, ms=round((relay.clock() - started) * 1000))

        def _event(self, message: dict) -> None:
            self.wfile.write(b"event: message\ndata: " + dumps(message) + b"\n\n")
            self.wfile.flush()

        def _stream(self, response, connection, request_id, deadline) -> str:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self._streaming = True
            return relay.stream(response, connection, request_id, deadline, self._event)

    return Agent


def admin_handler(relay: Relay):
    """Loopback inside the relay container only: `services up` runs the probe through
    `docker compose exec` with the stack's admin token on stdin."""

    class Admin(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # noqa: A002
            pass

        def do_POST(self):  # noqa: N802
            presented = self.headers.get("Authorization") or ""
            digest = hashlib.sha256(presented.removeprefix("Bearer ").encode()).hexdigest()
            if (
                self.path != "/probe"
                or not relay.admin_hash
                or not presented.startswith("Bearer ")
                or not hmac.compare_digest(digest, relay.admin_hash)
            ):
                return _send(self, 401, b"")
            try:
                result = relay.probe(relay.clock() + DEADLINE)
                _send(self, 200, dumps(result))
                relay.log(
                    event="probe",
                    decision="allow",
                    tools=len(result["tools"]),
                    missing=len(result["missing"]),
                )
            except RelayError as error:
                _send(self, 502, dumps({"error": error.code}))
                relay.log(event="probe", decision=error.code)
            except (OSError, http.client.HTTPException, ssl.SSLError):
                _send(self, 502, dumps({"error": "UPSTREAM_ERROR"}))
                relay.log(event="probe", decision="UPSTREAM_ERROR")

    return Admin


AGENT_PORT = 8080
ADMIN_PORT = 8081


def probe_client(stdin=sys.stdin, stdout=sys.stdout, port: int = ADMIN_PORT) -> int:
    """`relay.py probe`: read the admin token from stdin, ask the running relay, print JSON.
    It exits 0 whenever it printed a result: a failure is `{"error": CODE}`, so `services up`
    can show the relay's code instead of a failed `docker compose exec`."""
    token = stdin.readline().strip()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=DEADLINE + 10)
    try:
        connection.request(
            "POST", "/probe", body=b"", headers={"Authorization": f"Bearer {token}"}
        )
        response = connection.getresponse()
        body = response.read(8 * 1024 * 1024)
    except OSError:
        stdout.write(json.dumps({"error": "RELAY_UNAVAILABLE"}) + "\n")
        return 0
    finally:
        connection.close()
    if response.status == 401 or not body:
        body = json.dumps({"error": "PROBE_REFUSED"}).encode()
    stdout.write(body.decode("utf-8", "replace") + "\n")
    return 0


def load_config(path: str) -> dict:
    data = read_limited(path, 1024 * 1024)
    if data is None:
        raise SystemExit("relay config missing")
    return loads(data)


class BoundedServer(ThreadingHTTPServer):
    """At most MAX_CONNECTIONS connections at once; more are closed on accept."""

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def serve(relay: Relay, agent_port: int = AGENT_PORT, admin_port: int = ADMIN_PORT):
    agents = BoundedServer(("0.0.0.0", agent_port), agent_handler(relay))
    admin = ThreadingHTTPServer(("127.0.0.1", admin_port), admin_handler(relay))
    agents.daemon_threads = admin.daemon_threads = True
    return agents, admin


def main(argv=None) -> int:  # pragma: no cover - container entry point
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["probe"]:
        return probe_client()
    strip_proxies()
    relay = Relay(load_config(DEFAULT_PATHS["config"]))
    agents, admin = serve(relay)
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    relay.log(event="start", version=VERSION)
    agents.serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
