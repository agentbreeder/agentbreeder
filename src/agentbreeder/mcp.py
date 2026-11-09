"""MCP servers through a stack-hosted relay (#744; spec §7): the `mcp` registry kind and the
per-deployment grant files a relay reads.

An `mcp/<name>@X.Y.Z` version holds `mcp.yaml`: the upstream (a remote https URL or a stack
service port), how the relay authenticates upstream, the relay's own secrets and the only tools
that can ever be granted, each labelled `read` or `write`. The agent never holds the upstream
credential: deploy mints a per-deployment relay token, writes it only into the deployment's
private secret file, and gives the relay a grant file named by the token's sha256.

A grant file holds only a hash-derived name, the deployment, agent, environment, server and
tools, never a token. Files are 0444 in a 0755 directory, written atomically, the same
convention as service mounts (the relay reads them through a read-only bind mount).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentbreeder.env_import import denied
from agentbreeder.services.schema import SecretSource, ServiceError, _problems
from agentbreeder.strict_yaml import StrictYamlError, load_file

SERVER = r"^[a-z][a-z0-9_]{0,31}$"  # stack.yaml `mcp:` keys: AGENTBREEDER_MCP_<NAME>_URL
TOOL = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$"
MCP_REF = r"^mcp/[a-z0-9][a-z0-9-]{0,62}@[0-9]+\.[0-9]+\.[0-9]+$"
SECRET_KEY = r"^[a-z][a-z0-9_-]{0,30}$"
HEADER = r"^[A-Za-z][A-Za-z0-9-]{0,63}$"
# Headers the relay owns or that would change how the request is routed or framed.
RESERVED_HEADERS = frozenset(
    {
        "authorization",
        "host",
        "content-length",
        "content-type",
        "transfer-encoding",
        "connection",
        "mcp-session-id",
        "mcp-protocol-version",
        "mcp-method",
        "mcp-name",
        "cookie",
        "proxy-authorization",
    }
)
GRANT_NAME = re.compile(r"^[0-9a-f]{64}\.json$")
TOKEN = re.compile(r"^[0-9a-f]{64}$")
MAX_BYTES = 64 * 1024


class McpError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, populate_by_name=True)


def _https(url: str, where: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or any(c.isspace() for c in url)
    ):
        raise ValueError(f"{where} must be an https URL with a host and no credentials")
    return url


class Remote(_Strict):
    url: str = Field(max_length=512)

    @model_validator(mode="after")
    def https_only(self):
        # Private, loopback and link-local destinations are refused where the relay resolves
        # the name (the sidecar's resolution check), not here: DNS can change after a lock.
        _https(self.url, "upstream.remote.url")
        return self


class ServiceUpstream(_Strict):
    ref: str = Field(pattern=r"^service/[a-z0-9][a-z0-9-]{0,62}@[0-9]+\.[0-9]+\.[0-9]+$")
    port: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,30}$")
    path: str = Field(default="/mcp", pattern=r"^/[A-Za-z0-9._~/-]{0,127}$")


class Upstream(_Strict):
    remote: Remote | None = None
    service: ServiceUpstream | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.remote is None) == (self.service is None):
            raise ValueError("upstream has exactly one of remote or service")
        return self


class HeaderAuth(_Strict):
    name: str = Field(pattern=HEADER)
    secret: str = Field(pattern=SECRET_KEY)

    @model_validator(mode="after")
    def not_reserved(self):
        if self.name.lower() in RESERVED_HEADERS:
            raise ValueError("auth.header.name is a header the relay controls")
        return self


class OAuthRefresh(_Strict):
    token_url: str = Field(max_length=512)
    client_id: str = Field(pattern=SECRET_KEY)
    client_secret: str = Field(pattern=SECRET_KEY)
    refresh_token: str = Field(pattern=SECRET_KEY)
    scopes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def token_endpoint(self):
        _https(self.token_url, "auth.oauth_refresh.token_url")
        if any(not re.fullmatch(r"[\x21-\x7e]{1,256}", scope) for scope in self.scopes):
            raise ValueError("auth.oauth_refresh.scopes are printable tokens")
        return self


class Auth(_Strict):
    type: Literal["none", "bearer", "header", "oauth_refresh"]
    secret: str | None = Field(default=None, pattern=SECRET_KEY)
    header: HeaderAuth | None = None
    oauth_refresh: OAuthRefresh | None = None

    @model_validator(mode="after")
    def shape(self):
        given = {
            "bearer": self.secret is not None,
            "header": self.header is not None,
            "oauth_refresh": self.oauth_refresh is not None,
        }
        wanted = {key: key == self.type for key in given}
        if given != wanted:
            raise ValueError(f"auth type {self.type} takes only its own field")
        return self

    def secret_names(self) -> set[str]:
        if self.type == "bearer":
            return {self.secret}
        if self.type == "header":
            return {self.header.secret}
        if self.type == "oauth_refresh":
            o = self.oauth_refresh
            return {o.client_id, o.client_secret, o.refresh_token}
        return set()


class Tool(_Strict):
    # Recorded for #742 (risk tiers, approvals) and shown in the grant. A project-origin entry's
    # labels are untrusted.
    effect: Literal["read", "write"]


class McpSpec(_Strict):
    upstream: Upstream
    auth: Auth
    secrets: dict[str, SecretSource] = Field(default_factory=dict, max_length=10)
    tools: dict[str, Tool] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def consistent(self):
        for name, source in self.secrets.items():
            if not re.fullmatch(SECRET_KEY, name):
                raise ValueError(f"secret name {name!r} is invalid")
            if source.per is not None:
                raise ValueError(f"secret {name}: `per` is for services")
            if source.import_ is not None and denied(source.import_):
                raise ValueError(f"secret {name}: {source.import_} can't be imported")
        missing = self.auth.secret_names() - set(self.secrets)
        if missing:
            raise ValueError(f"auth names undeclared secrets: {sorted(missing)}")
        if any(not re.fullmatch(TOOL, tool) for tool in self.tools):
            raise ValueError("tool names are letters, digits, `_`, `.` and `-` (max 64)")
        if self.upstream.service is not None and self.auth.type == "oauth_refresh":
            raise ValueError("oauth_refresh is for remote upstreams")
        return self


def load_mcp(version_dir: Path) -> McpSpec:
    path = version_dir / "mcp.yaml"
    try:
        data = load_file(path, MAX_BYTES)
    except StrictYamlError as error:
        raise ServiceError("MCP_INVALID", f"mcp.yaml: {error}") from None
    try:
        return McpSpec.model_validate(data)
    except ValidationError as error:
        raise ServiceError("MCP_INVALID", f"mcp.yaml: {_problems(error)}") from None


# -- per-deployment tokens and grant files ------------------------------------------------------


def mint_token() -> str:
    """256-bit random hex. The relay rejects anything else before hashing it."""
    return secrets.token_hex(32)


def token_hash(token: str) -> str:
    if not TOKEN.fullmatch(token):
        raise McpError("MCP_TOKEN_INVALID", "a relay token is 64 lowercase hex characters")
    return hashlib.sha256(token.encode()).hexdigest()


def env_name(name: str) -> str:
    """`github` -> GITHUB; ref names may hold `-`."""
    return name.upper().replace("-", "_")


def _grants_dir(directory: Path) -> Path:
    if not directory.is_absolute() or directory.is_symlink():
        raise McpError("MCP_GRANTS_DIR", "grants_dir must be an absolute, real directory")
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    directory.chmod(0o755)
    return directory


def write_grant(directory: Path, token: str, grant: dict) -> Path:
    """Atomically write `<sha256(token)>.json` (0444): {deployment, agent, environment, server,
    tools}. Never the token."""
    expected = {"deployment", "agent", "environment", "server", "tools"}
    if set(grant) != expected:
        raise McpError("MCP_GRANT_INVALID", f"a grant holds exactly {sorted(expected)}")
    directory = _grants_dir(directory)
    path = directory / f"{token_hash(token)}.json"
    temporary = directory / f".{path.name}.tmp"
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(grant, handle, sort_keys=True)
    os.chmod(temporary, 0o444)
    os.replace(temporary, path)
    return path


def grants(directory: Path) -> dict[str, dict]:
    """Grant files by token hash. Unreadable or foreign files are reported as `{}`."""
    if not directory.is_dir() or directory.is_symlink():
        return {}
    result: dict[str, dict] = {}
    for path in sorted(directory.iterdir()):
        if not GRANT_NAME.fullmatch(path.name) or not path.is_file() or path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        result[path.name.removesuffix(".json")] = data if isinstance(data, dict) else {}
    return result


def revoke(directory: Path, *, deployment: str | None = None, keep: set[str] | None = None) -> int:
    """Delete grant files: those of `deployment` (or every file when None), except hashes in
    `keep`. Safe to repeat; returns how many were deleted."""
    removed = 0
    for digest, grant in grants(directory).items():
        if keep and digest in keep:
            continue
        if deployment is not None and grant.get("deployment") != deployment:
            continue
        (directory / f"{digest}.json").unlink(missing_ok=True)
        removed += 1
    return removed
