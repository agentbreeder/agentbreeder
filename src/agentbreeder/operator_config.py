"""Operator-controlled enforcement settings. Never read from the agent repository."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from agentbreeder.manifest import (
    _ALIAS,
    _ENVIRONMENT,
    _GATEWAY,
    IdentityReference,
    ManifestError,
    _UniqueLoader,
)

MAX_CONFIG_BYTES = 256 * 1024
CONFIG_ENV = "AGENTBREEDER_OPERATOR_CONFIG"
DEFAULT_PATH = Path.home() / ".agentbreeder" / "operator.yaml"
_SHA256 = r"^sha256:[a-f0-9]{64}$"
_BUNDLE_ID = r"^[a-z0-9][a-z0-9._-]{0,63}$"


class OperatorConfigError(ValueError):
    """Safe error; never includes configuration values or credentials."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


_CREDENTIAL_REF = r"^(env:[A-Z_][A-Z0-9_]*|file:\S{1,511})$"
_SERVICE_URL = r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[^\s]*)?$"


class Ceilings(_Strict):
    budget_usd: float = Field(gt=0, le=100000)
    rpm: int = Field(ge=1, le=100000)


_MCP_SERVER = r"^[a-z][a-z0-9_]{0,31}$"
_MCP_TOOL = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$"


class AgentIdentities(_Strict):
    """Identity references `agentbreeder build` writes into generated manifests."""

    identity: IdentityReference
    candidate_identity: IdentityReference | None = None
    # MCP tools granted to this agent (both identities), per relay: {server: [tools]}.
    mcp: dict[str, list[str]] = Field(default_factory=dict, max_length=10)

    @field_validator("mcp")
    @classmethod
    def mcp_grants(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for server, tools in value.items():
            if not re.fullmatch(_MCP_SERVER, server) or not 1 <= len(tools) <= 100:
                raise ValueError("mcp grants map a relay name to 1-100 tools")
            if any(not re.fullmatch(_MCP_TOOL, t) for t in tools) or len(set(tools)) != len(tools):
                raise ValueError("mcp grants list unique tool names")
        return value


class McpRelay(_Strict):
    """A stack-hosted MCP relay (`services up` writes it): the `mcp` version it serves, the
    URL agents call on the stack's agents network, and the directory of grant files it reads."""

    ref: str = Field(pattern=r"^mcp/[a-z0-9][a-z0-9-]{0,62}@[0-9]+\.[0-9]+\.[0-9]+$")
    url: str = Field(max_length=512, pattern=_SERVICE_URL)
    grants_dir: str = Field(max_length=1024)

    @field_validator("grants_dir")
    @classmethod
    def absolute(cls, value: str) -> str:
        if not Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("grants_dir must be an absolute path")
        return value


_CIDR = r"^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$"


class KubernetesTarget(_Strict):
    """The cluster agents in this environment deploy to. `KUBECONFIG` and `current-context` are
    never used, and `cluster_uid` (the UID of `kube-system`) must match the cluster."""

    kubeconfig: str = Field(max_length=1024, pattern=r"^/[^|\s]*$")
    context: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
    cluster_uid: str = Field(pattern=r"^[0-9a-f-]{36}$")
    namespace: str = Field(pattern=r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
    registry: str = Field(pattern=r"^[a-z0-9.-]+(:[0-9]{1,5})?(/[a-z0-9._-]+)+$", max_length=256)
    # Pod, service and node ranges, and metadata: egress the agent's NetworkPolicy denies.
    blocked_cidrs: list[str] = Field(min_length=1, max_length=32)

    @field_validator("blocked_cidrs")
    @classmethod
    def cidrs(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(_CIDR, cidr) for cidr in value):
            raise ValueError("blocked_cidrs are IPv4 CIDR blocks")
        return value


class Environment(_Strict):
    targets: list[Literal["local", "kubernetes"]] = Field(min_length=1)
    # Required to deploy `target: kubernetes` in this environment.
    kubernetes: KubernetesTarget | None = None
    # Model gateway grant for agents in this environment (agent.yaml builds, spec §10).
    gateway: str | None = Field(default=None, pattern=_GATEWAY)
    models: list[str] = Field(default_factory=list, max_length=50)
    ceilings: Ceilings | None = None
    # Non-secret service URLs passed to built agents as AGENTBREEDER_SERVICE_<NAME>.
    services: dict[str, str] = Field(default_factory=dict, max_length=20)
    # ENV name -> secret:// reference written into generated manifests.
    bindings: dict[str, str] = Field(default_factory=dict, max_length=20)
    agents: dict[str, AgentIdentities] = Field(default_factory=dict, max_length=200)
    # Host-reachable database for eval fixture resets (`agentbreeder eval` / `release`).
    eval_database: str | None = Field(default=None, pattern=r"^secret://[a-z0-9][a-z0-9_-]{0,62}$")
    # A Docker network agent containers also join (`services up` writes the stack's agents
    # network). Unset: deployments are created exactly as before.
    network: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_.-]{0,127}$")
    # Stack MCP relays agents in this environment may be granted (spec §7).
    mcp_relays: dict[str, McpRelay] = Field(default_factory=dict, max_length=20)

    @field_validator("network")
    @classmethod
    def user_network(cls, value: str | None) -> str | None:
        if value in ("bridge", "host", "none"):
            raise ValueError("network names a user-defined network")
        return value

    @model_validator(mode="after")
    def gateway_needs_ceilings(self):
        # Without ceilings a manifest (and so an agent.yaml) could set any budget or rpm and
        # the minted key would carry it: an environment that grants a gateway caps it.
        if self.gateway is not None and self.ceilings is None:
            raise ValueError("an environment with a gateway must set ceilings (budget_usd, rpm)")
        for name in self.mcp_relays:
            if not re.fullmatch(_MCP_SERVER, name):
                raise ValueError("invalid mcp relay name")
        dirs = [relay.grants_dir for relay in self.mcp_relays.values()]
        if len(set(dirs)) != len(dirs):
            raise ValueError("each mcp relay has its own grants_dir")
        for agent in self.agents.values():
            if not set(agent.mcp) <= set(self.mcp_relays):
                raise ValueError("an agent's mcp grant names a relay the environment lacks")
        return self

    @field_validator("models")
    @classmethod
    def model_names(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(_ALIAS, alias) for alias in value):
            raise ValueError("invalid model alias")
        return value

    @field_validator("services")
    @classmethod
    def service_urls(cls, value: dict[str, str]) -> dict[str, str]:
        for key, url in value.items():
            if not re.fullmatch(r"^[a-z][a-z0-9_]{0,31}$", key) or not re.fullmatch(
                _SERVICE_URL, url
            ):
                raise ValueError("invalid service name or URL")
        return value

    @field_validator("bindings")
    @classmethod
    def binding_refs(cls, value: dict[str, str]) -> dict[str, str]:
        for key, ref in value.items():
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) or not re.fullmatch(
                r"secret://[a-z0-9][a-z0-9_-]{0,62}", ref
            ):
                raise ValueError("bindings map ENV names to secret://name references")
        return value

    @field_validator("agents")
    @classmethod
    def agent_names(cls, value: dict[str, AgentIdentities]) -> dict[str, AgentIdentities]:
        if any(not re.fullmatch(r"^[a-z0-9]([a-z0-9-]{0,51}[a-z0-9])?$", k) for k in value):
            raise ValueError("invalid agent name")
        return value


class Gateway(_Strict):
    """A model gateway AgentBreeder grants agents access to. Only LiteLLM in Phase A."""

    type: Literal["litellm"]
    url: str = Field(pattern=_SERVICE_URL)  # how AgentBreeder reaches it
    agent_url: str = Field(pattern=_SERVICE_URL)  # how agent containers reach it
    master_key: str | None = Field(default=None, pattern=_CREDENTIAL_REF)


class PolicySource(_Strict):
    """A pinned local Cedar bundle or a remote PDP with an expected policy revision."""

    source: str | None = None
    digest: str | None = Field(default=None, pattern=_SHA256)
    url: str | None = Field(default=None, max_length=512)
    path: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_]+(/[A-Za-z0-9_]+)*$")
    bundle: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._/-]{1,128}$")
    revision: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:@/+-]{1,128}$")
    token: str | None = None


class PolicyConfig(_Strict):
    engine: Literal["cedar", "opa", "authzen"]
    baseline: PolicySource
    approved_restrictions: dict[str, PolicySource] = Field(default_factory=dict)

    @field_validator("approved_restrictions")
    @classmethod
    def bundle_ids(cls, value: dict[str, PolicySource]) -> dict[str, PolicySource]:
        if any(not re.fullmatch(_BUNDLE_ID, key) for key in value):
            raise ValueError("invalid restriction bundle ID")
        return value


class TrustedIssuer(_Strict):
    issuer: str = Field(pattern=r"^https://\S{1,255}$")
    audience: str = Field(min_length=1, max_length=256)
    jwks_file: str | None = None
    jwks_uri: str | None = Field(default=None, pattern=r"^https://\S{1,511}$")
    subject_claim: str = Field(default="sub", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    # Only `human` issuers may approve or enable; CI/workload issuers can only deploy.
    kind: Literal["human", "workload"] = "workload"
    device_authorization_endpoint: str | None = Field(default=None, pattern=r"^https://\S+$")
    token_endpoint: str | None = Field(default=None, pattern=r"^https://\S+$")
    client_id: str | None = Field(default=None, max_length=256)


class DeployerConfig(_Strict):
    require_verified: bool = False
    issuers: list[TrustedIssuer] = Field(default_factory=list, max_length=8)


class MakerChecker(_Strict):
    risk_tiers: list[str] = Field(default_factory=list)


_DESTINATION_URL = r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[^\s]*)?$"


class TokenExchange(_Strict):
    """RFC 8693 token exchange at the MCP server's authorization server."""

    token_endpoint: str = Field(pattern=r"^https://\S{1,511}$")
    audience: str = Field(min_length=1, max_length=512)
    scope: str | None = Field(default=None, max_length=512)
    client_id: str | None = Field(default=None, max_length=256)
    client_secret: str | None = Field(default=None, pattern=_CREDENTIAL_REF)


class Destination(_Strict):
    """One operator-approved MCP Streamable-HTTP server. Credentials stay on the host."""

    url: str = Field(max_length=512, pattern=_DESTINATION_URL)
    credential: str | None = Field(default=None, pattern=_CREDENTIAL_REF)
    token_exchange: TokenExchange | None = None
    # Private/loopback/link-local resolution is refused unless explicitly allow-listed.
    allow_private: bool = False
    ca_file: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def exchange_needs_subject(self):
        if self.token_exchange and not self.credential:
            raise ValueError("token_exchange needs a credential to exchange")
        return self


class EgressLimits(_Strict):
    max_body_bytes: int = Field(default=1024 * 1024, ge=1024, le=8 * 1024 * 1024)
    calls_per_minute: int = Field(default=60, ge=1, le=6000)


class RuntimeEnforcement(_Strict):
    mode: Literal["none", "mcp_http"] = "none"
    destinations: dict[str, Destination] = Field(default_factory=dict, max_length=32)
    limits: EgressLimits = EgressLimits()

    @field_validator("destinations")
    @classmethod
    def destination_names(cls, value: dict[str, Destination]) -> dict[str, Destination]:
        if any(not re.fullmatch(r"^[a-z0-9][a-z0-9_-]{0,31}$", key) for key in value):
            raise ValueError("invalid destination name")
        if len({d.url for d in value.values()}) != len(value):
            raise ValueError("destination URLs must be unique")
        return value


class OperatorModel(_Strict):
    enforcement: Literal["enforce", "audit"] = "enforce"
    default_environment: str | None = Field(default=None, pattern=_ENVIRONMENT)
    environments: dict[str, Environment] = Field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    deployer: DeployerConfig = DeployerConfig()
    pdp: PolicyConfig | None = None
    require_sponsor: bool = True
    risk_attribute: str | None = Field(default=None, max_length=129)
    maker_checker: MakerChecker = MakerChecker()
    approval_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    runtime_enforcement: RuntimeEnforcement = RuntimeEnforcement()
    gateways: dict[str, Gateway] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def gateway_references(self):
        for environment in self.environments.values():
            if environment.gateway and environment.gateway not in self.gateways:
                raise ValueError("environment gateway is not defined in gateways")
            # The egress boundary puts agents on its own internal network only; a stack network
            # would be silently dropped, so refuse the combination instead.
            if environment.network and self.runtime_enforcement.mode != "none":
                raise ValueError("environment network is not supported with runtime_enforcement")
        return self

    @field_validator("environments")
    @classmethod
    def environment_names(cls, value: dict[str, Environment]) -> dict[str, Environment]:
        if any(not re.fullmatch(_ENVIRONMENT, key) for key in value):
            raise ValueError("invalid environment name")
        return value

    @field_validator("providers")
    @classmethod
    def provider_names(cls, value: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if any(not re.fullmatch(r"^[a-z0-9][a-z0-9_-]{0,31}$", key) for key in value):
            raise ValueError("invalid provider connection name")
        return value


@dataclass(frozen=True)
class DeploymentContext:
    """Trusted context selected by the deployer from the operator mapping."""

    environment: str
    target: str


@dataclass(frozen=True)
class OperatorConfig:
    model: OperatorModel
    digest: str
    path: Path | None

    @property
    def enforcement(self) -> str:
        return self.model.enforcement

    def context(self, environment: str | None, target: str) -> DeploymentContext:
        name = environment or self.model.default_environment
        if not name or name not in self.model.environments:
            raise ContextError("CONTEXT_ENVIRONMENT_UNKNOWN", "Unknown or missing --environment")
        if target not in self.model.environments[name].targets:
            raise ContextError(
                "CONTEXT_TARGET_NOT_ALLOWED",
                f"Target {target} is not allowed in environment {name}",
            )
        return DeploymentContext(name, target)


class ContextError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def check_private_file(path: Path, what: str) -> None:
    """Accept files owned by this user (0600-style) or by root (not group/world writable)."""
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise OperatorConfigError(f"{what} must be a regular file, not a symlink")
    if metadata.st_uid == os.getuid() and not metadata.st_mode & 0o077:
        return
    if metadata.st_uid == 0 and not metadata.st_mode & 0o022:
        return
    raise OperatorConfigError(
        f"{what} must be owned by you with mode 0600, or by root and not group/world writable"
    )


def read_private(path: Path, what: str, limit: int = MAX_CONFIG_BYTES) -> bytes:
    try:
        check_private_file(path, what)
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as error:
        raise OperatorConfigError(f"Cannot read {what}; check the path and permissions") from error
    if len(data) > limit:
        raise OperatorConfigError(f"{what} is too large")
    return data


def parse_yaml(data: bytes, what: str) -> Any:
    try:
        text = data.decode("utf-8")
        if any(isinstance(t, (yaml.AliasToken, yaml.AnchorToken)) for t in yaml.scan(text)):
            raise OperatorConfigError(f"{what} must not use YAML anchors or aliases")
        return yaml.load(text, Loader=_UniqueLoader)
    except OperatorConfigError:
        raise
    except (ValueError, yaml.YAMLError, RecursionError, ManifestError) as error:
        raise OperatorConfigError(f"{what} is not valid UTF-8 YAML") from error


def load_operator_config(path: Path | None = None) -> OperatorConfig:
    """Load operator config. A missing file yields fail-closed defaults (enforce, no envs)."""
    explicit = path or (Path(os.environ[CONFIG_ENV]) if os.environ.get(CONFIG_ENV) else None)
    candidate = explicit or DEFAULT_PATH
    if explicit is None and not candidate.exists():
        model = OperatorModel()
        return OperatorConfig(model, _digest(model.model_dump_json().encode()), None)
    data = read_private(candidate, "Operator config")
    raw = parse_yaml(data, "Operator config")
    try:
        model = OperatorModel.model_validate(raw if raw is not None else {})
    except ValidationError as error:
        fields = sorted({str(e["loc"][0]) for e in error.errors() if e["loc"]})
        raise OperatorConfigError(
            f"Invalid operator config field(s): {', '.join(fields) or 'document'}"
        ) from error
    return OperatorConfig(model, _digest(data), candidate)


def resolve_credential(reference: str | None, base: Path | None) -> str | None:
    """Resolve `env:NAME` or `file:/path` credential references; literals are rejected."""
    if reference is None:
        return None
    kind, _, value = reference.partition(":")
    if kind == "env" and re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
        secret = os.environ.get(value)
        if not secret:
            raise OperatorConfigError("A provider credential environment variable is unset")
        return secret
    if kind == "file" and value:
        target = Path(value) if Path(value).is_absolute() or base is None else base / value
        return read_private(target, "Credential file", 64 * 1024).decode().strip()
    raise OperatorConfigError("Credentials must be env:NAME or file:/path references")


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
