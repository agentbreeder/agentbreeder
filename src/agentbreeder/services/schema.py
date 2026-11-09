"""`service.yaml` (in a `service/<name>@X.Y.Z` registry version) and `stack.yaml` (a project's
local backing services). Schema only: references and taint are checked in `refs.py`.

See docs/superpowers/specs/2026-11-08-agent-services-design.md. Everything here is local-only
preview, and nothing in a stack can widen the identity gate: the operator config generated from it
is limited to a local demo environment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agentbreeder.registry import Ref, RegistryError
from agentbreeder.strict_yaml import StrictYamlError, load_file

MAX_BYTES = 64 * 1024
KEY = r"^[a-z][a-z0-9_-]{0,30}$"  # port, param, secret, input and output names
SERVICE_NAME = r"^[a-z][a-z0-9-]{0,30}$"
ENV_NAME = r"^[A-Z][A-Z0-9_]{0,63}$"
IMPORT_NAME = r"^[A-Z_][A-Z0-9_]{0,127}$"
DB_NAME = r"^[a-z][a-z0-9_]{0,62}$"
IMAGE = r"^[a-z0-9][a-z0-9./_-]{0,254}(:[A-Za-z0-9._-]{1,128})?@sha256:[a-f0-9]{64}$"
ABS_PATH = r"^/[A-Za-z0-9._/-]{1,255}$"
RESERVED = frozenset({"params", "ports", "secrets", "self", "item", "address"})
MAX_ARG = 4096
# Interpreters that turn an argument into a command line. Refused outright as argv[0]: option
# clusters (`-xc`), wrappers (`env sh -c`) and busybox applets make a narrower check bypassable.
SHELLS = frozenset(
    {"sh", "bash", "dash", "zsh", "ash", "ksh", "mksh", "fish", "csh", "tcsh", "busybox", "env"}
)


class ServiceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def _argv(value: list[str] | None, where: str) -> list[str] | None:
    if value is None:
        return None
    if not value or len(value) > 64 or any(not a or len(a) > MAX_ARG for a in value):
        raise ValueError(f"{where} must be a non-empty exec-form list of arguments")
    if Path(value[0]).name in SHELLS:
        raise ValueError(f"{where} must be exec form: run a program, not a shell or wrapper")
    return value


# -- service.yaml -------------------------------------------------------------------------------


class Port(_Strict):
    port: int = Field(ge=1, le=65535)
    protocol: Literal["http", "tcp"] = "http"
    # One scope or a list: `[agents, host]` serves agent containers and AgentBreeder on the host
    # (a gateway, or a database used both by agents and for eval fixture resets).
    expose: Literal["internal", "agents", "host"] | list[Literal["agents", "host"]] = "internal"
    guard: Literal["litellm-auth", "bearer", "host-allowlist"] | None = None

    @property
    def scopes(self) -> frozenset[str]:
        """Where the port is reachable from: always `internal`, plus `agents` and/or `host`."""
        given = [self.expose] if isinstance(self.expose, str) else self.expose
        return frozenset({"internal", *given})

    @model_validator(mode="after")
    def http_needs_guard(self):
        if isinstance(self.expose, list) and (
            not self.expose or len(set(self.expose)) != len(self.expose)
        ):
            raise ValueError("expose lists name agents and/or host, once each")
        # Loopback HTTP is reachable from browsers (CSRF, DNS rebinding): require a check.
        if self.protocol == "http" and self.scopes != {"internal"} and self.guard is None:
            raise ValueError("an HTTP port reachable outside the stack needs a guard")
        if self.protocol == "tcp" and self.guard is not None:
            raise ValueError("guard applies to HTTP ports only")
        return self


class Param(_Strict):
    type: Literal["string", "file", "list", "db_name"]
    item: Literal["string", "db_name"] | None = None
    required: bool = False
    default: str | list[str] | None = None

    @model_validator(mode="after")
    def coherent(self):
        if (self.type == "list") != (self.item is not None):
            raise ValueError("`item` is required for list params and only for them")
        if self.required and self.default is not None:
            raise ValueError("a required param has no default")
        if self.type == "file" and self.default is not None:
            raise ValueError("file params have no default")
        return self


class SecretSource(_Strict):
    generate: Literal["token"] | None = None
    import_: str | None = Field(default=None, alias="import", pattern=IMPORT_NAME)
    optional: bool = False
    per: str | None = Field(default=None, pattern=KEY)  # one secret per item of a list param
    # A fixed lowercase prefix for generated tokens a service expects in a known shape
    # (LiteLLM keys are `sk-…`; Phoenix secrets need a digit and a letter, so `s1-…`). The random
    # part stays hex.
    prefix: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9]{0,7}-$")

    @model_validator(mode="after")
    def one_source(self):
        sources = [s for s in (self.generate, self.import_) if s is not None]
        if len(sources) != 1:
            raise ValueError("a secret has exactly one of generate or import")
        if self.optional and self.import_ is None:
            raise ValueError("only imported secrets can be optional")
        if self.prefix is not None and self.generate is None:
            raise ValueError("`prefix` applies to generated secrets only")
        if self.per is not None and self.generate is None:
            raise ValueError("`per` applies to generated secrets only")
        return self


class Input(_Strict):
    """A value another service provides, wired in stack.yaml. Secret inputs are mounted at
    /run/secrets/<name>; url inputs may be used in env and command as {inputs.<name>}."""

    type: Literal["url", "secret"]
    optional: bool = False


class Health(_Strict):
    http: str | None = Field(default=None, pattern=ABS_PATH)
    command: list[str] | None = None
    port: str | None = Field(default=None, pattern=KEY)

    @model_validator(mode="after")
    def one_check(self):
        if (self.http is None) == (self.command is None):
            raise ValueError("health needs exactly one of http or command")
        _argv(self.command, "health.command")
        return self


class OneShot(_Strict):
    command: list[str]
    secrets: list[str] = Field(default_factory=list, max_length=20)
    writes_secret: str | None = Field(default=None, pattern=KEY)

    @model_validator(mode="after")
    def exec_form(self):
        _argv(self.command, "command")
        return self


class Output(_Strict):
    type: Literal["url", "secret"]
    port: str | None = Field(default=None, pattern=KEY)
    path: str = Field(default="", pattern=r"^(/[A-Za-z0-9._~/-]*)?$")
    secret: str | None = Field(default=None, pattern=KEY)  # a whole secret, by name
    template: str | None = Field(default=None, max_length=512)  # secret built from parts
    per: str | None = Field(default=None, pattern=KEY)  # one value per item of a list param
    # Only AgentBreeder itself may use it (the gateway master key mints agent keys): never
    # wired to another service, bound into an agent or used as the eval database.
    operator_only: bool = False
    # Only other services may receive it (wired to a secret input): never bound into an agent
    # or used as the eval database (Phoenix's ingest key reads every trace; its proxy holds it).
    services_only: bool = False

    @model_validator(mode="after")
    def shape(self):
        if self.operator_only and self.services_only:
            raise ValueError("an output is either operator_only or services_only")
        if self.services_only and self.type != "secret":
            raise ValueError("services_only applies to secret outputs")
        if self.type == "url":
            if self.port is None or self.secret is not None or self.template is not None:
                raise ValueError("a url output names a port, and nothing secret")
        elif (self.secret is None) == (self.template is None):
            raise ValueError("a secret output has exactly one of secret or template")
        elif self.template is not None and self.port is None:
            raise ValueError("a secret template needs the port its {address} refers to")
        return self


TMPFS_LIMIT = 4
# Paths a service tmpfs may not cover: AgentBreeder's own mounts, the default /tmp, kernel trees.
TMPFS_RESERVED = ("/tmp", "/proc", "/sys", "/dev", "/run/secrets", "/run/params", "/files")


def _tmpfs(paths: list[str], volume_targets: set[str]) -> None:
    if len(paths) > TMPFS_LIMIT:
        raise ValueError(f"tmpfs: at most {TMPFS_LIMIT} paths")
    if len(set(paths)) != len(paths):
        raise ValueError("tmpfs paths must be unique")
    for path in paths:
        parts = path.split("/")[1:]
        if (
            not _matches(ABS_PATH, path)
            or path.endswith("/")
            or any(part in ("", ".", "..") for part in parts)
        ):
            raise ValueError("tmpfs paths must be normalised absolute container paths")
        if any(
            path == r or path.startswith(r + "/") or r.startswith(path + "/")
            for r in TMPFS_RESERVED
        ):
            raise ValueError("tmpfs path is reserved")
        if any(
            path == t or t.startswith(path + "/") or path.startswith(t + "/")
            for t in volume_targets
        ):
            raise ValueError("tmpfs path overlaps a volume")


class ServiceSpec(_Strict):
    image: str | None = Field(default=None, pattern=IMAGE)
    build: Literal["Dockerfile"] | None = None
    provides: Literal["gateway/litellm"] | None = None
    command: list[str] | None = None
    user: str = Field(default="image", pattern=r"^(image|host|[0-9]{1,6}:[0-9]{1,6})$")
    ports: dict[str, Port] = Field(default_factory=dict, max_length=8)
    params: dict[str, Param] = Field(default_factory=dict, max_length=20)
    env: dict[str, str] = Field(default_factory=dict, max_length=50)
    secrets: dict[str, SecretSource] = Field(default_factory=dict, max_length=20)
    inputs: dict[str, Input] = Field(default_factory=dict, max_length=20)
    volumes: dict[str, str] = Field(default_factory=dict, max_length=2)
    # Extra writable scratch paths (noexec, nosuid, size-capped) on top of the read-only root,
    # e.g. postgres's /var/run/postgresql socket directory.
    tmpfs: list[str] = Field(default_factory=list)
    health: Health | None = None
    egress: bool = False
    init: OneShot | None = None
    bootstrap: OneShot | None = None
    outputs: dict[str, Output] = Field(default_factory=dict, max_length=20)

    @model_validator(mode="after")
    def consistent(self):
        if (self.image is None) == (self.build is None):
            raise ValueError("a service has exactly one of image (digest-pinned) or build")
        _argv(self.command, "command")
        for kind, names in (
            ("port", self.ports),
            ("param", self.params),
            ("secret", self.secrets),
            ("input", self.inputs),
            ("output", self.outputs),
            ("volume", self.volumes),
        ):
            for name in names:
                if not _matches(KEY, name) or name in RESERVED:
                    raise ValueError(f"{kind} name {name!r} is invalid or reserved")
        for name in self.env:
            if not _matches(ENV_NAME, name):
                raise ValueError(f"env name {name!r} must be an uppercase variable name")
            if name.startswith("AGENTBREEDER_"):
                raise ValueError(f"env name {name!r} uses the reserved AGENTBREEDER_ prefix")
        for target in self.volumes.values():
            if not _matches(ABS_PATH, target):
                raise ValueError("volume targets must be absolute container paths")
        _tmpfs(self.tmpfs, set(self.volumes.values()))
        if self.volumes and self.user in ("image", "host"):
            raise ValueError("a service with a data volume needs a fixed user (uid:gid)")
        clash = set(self.secrets) & set(self.inputs)
        if clash:
            raise ValueError(f"names used as both secret and input: {sorted(clash)}")
        lists = {n for n, p in self.params.items() if p.type == "list"}
        for name, secret in self.secrets.items():
            if secret.per is not None and secret.per not in lists:
                raise ValueError(f"secret {name}: per names a list param")
        for name, output in self.outputs.items():
            if output.port is not None and output.port not in self.ports:
                raise ValueError(f"output {name}: unknown port {output.port}")
            written = {self.bootstrap.writes_secret} if self.bootstrap else set()
            if output.secret is not None and output.secret not in set(self.secrets) | written:
                raise ValueError(f"output {name}: unknown secret {output.secret}")
            if output.per is not None and output.per not in lists:
                raise ValueError(f"output {name}: per names a list param")
        if self.health is not None and self.health.http is not None:
            port = self.health.port or next(iter(self.ports), None)
            if port is None or port not in self.ports or self.ports[port].protocol != "http":
                raise ValueError("health.http needs an HTTP port")
        for step_name, step in (("init", self.init), ("bootstrap", self.bootstrap)):
            if step is None:
                continue
            for secret in step.secrets:
                if secret not in self.mounted_secrets():
                    raise ValueError(f"{step_name}: unknown secret {secret}")
            if step_name == "init" and step.writes_secret is not None:
                raise ValueError("only bootstrap can write a secret")
        if self.bootstrap is not None:
            target = self.bootstrap.writes_secret
            if target is None:
                raise ValueError("bootstrap must name the secret it writes (writes_secret)")
            if target in self.secrets:
                raise ValueError("bootstrap's writes_secret must not also be declared")
        if self.provides == "gateway/litellm":
            url = self.outputs.get("url")
            key = self.outputs.get("master_key")
            if url is None or url.type != "url" or key is None or key.type != "secret":
                raise ValueError("gateway/litellm needs a url output and a master_key secret")
            if not key.operator_only or key.secret is None:
                raise ValueError(
                    "gateway/litellm's master_key output must be operator_only and name its secret"
                )
            config = self.params.get("config")
            if config is None or config.type != "file":
                raise ValueError("gateway/litellm needs a `config` file param (its model list)")
        # Operator-only is a property of the secret, not of one output's name: every output that
        # exposes an operator-only secret (whole, or inside a template) must be operator-only.
        # services_only likewise: an output exposing such a secret is at least as restricted.
        for flag, allowed in (
            ("operator_only", ("operator_only",)),
            ("services_only", ("services_only", "operator_only")),
        ):
            guarded = {o.secret for o in self.outputs.values() if getattr(o, flag) and o.secret}
            for name, output in self.outputs.items():
                exposes = {output.secret} if output.secret else set()
                if output.template:
                    exposes |= {s for s in guarded if "{secret." + s + "}" in output.template}
                if exposes & guarded and not any(getattr(output, a) for a in allowed):
                    article = "an" if flag == "operator_only" else "a"
                    raise ValueError(f"output {name} exposes {article} {flag} secret")
        return self

    def mounted_secrets(self) -> set[str]:
        """Secret names the service's container may receive (own, secret inputs, bootstrap's)."""
        names = set(self.secrets) | {n for n, i in self.inputs.items() if i.type == "secret"}
        if self.bootstrap is not None and self.bootstrap.writes_secret:
            names.add(self.bootstrap.writes_secret)
        return names


# -- stack.yaml ---------------------------------------------------------------------------------


class StackService(_Strict):
    ref: str
    with_: dict[str, str | list[str]] = Field(default_factory=dict, alias="with", max_length=20)
    wire: dict[str, str] = Field(default_factory=dict, max_length=20)  # input -> svc.output[.item]


class SecretRef(_Strict):
    ref: str = Field(max_length=128)


class Ceilings(_Strict):
    budget_usd: float = Field(gt=0, le=100000)
    rpm: int = Field(ge=1, le=100000)


class DemoAgent(_Strict):
    sponsor: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+$")
    review_by: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    candidate: bool = False


class StackEnvironment(_Strict):
    gateway: str = Field(pattern=SERVICE_NAME)
    models: list[str] = Field(min_length=1, max_length=50)
    ceilings: Ceilings
    services: dict[str, str] = Field(default_factory=dict, max_length=20)
    bindings: dict[str, SecretRef] = Field(default_factory=dict, max_length=20)
    eval_database: SecretRef | None = None
    agents: dict[str, DemoAgent] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def names(self):
        for name in self.services:
            if not _matches(r"^[a-z][a-z0-9_]{0,30}$", name):
                raise ValueError(f"environment.services name {name!r} is invalid")
        for name in self.bindings:
            if not _matches(ENV_NAME, name):
                raise ValueError(f"binding name {name!r} must be an uppercase variable name")
        for name in self.agents:
            if not _matches(r"^[a-z0-9]([a-z0-9-]{0,51}[a-z0-9])?$", name):
                raise ValueError(f"agent name {name!r} is invalid")
        return self


class Stack(_Strict):
    kind: Literal["stack"]
    schema_version: Literal[1]
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,30}$")
    services: dict[str, StackService] = Field(min_length=1, max_length=20)
    environment: StackEnvironment

    @model_validator(mode="after")
    def service_refs(self):
        for name, entry in self.services.items():
            if not _matches(SERVICE_NAME, name) or name in RESERVED:
                raise ValueError(f"service name {name!r} is invalid or reserved")
            try:
                ref = Ref.parse(entry.ref)
            except RegistryError as error:
                raise ValueError(f"services.{name}.ref: not a service/name@X.Y.Z ref") from error
            if ref.kind != "service":
                raise ValueError(f"services.{name}.ref: must be a service/name@X.Y.Z ref")
        if self.environment.gateway not in self.services:
            raise ValueError("environment.gateway must name a service in this stack")
        return self


def _matches(pattern: str, value: str) -> bool:
    import re

    return re.fullmatch(pattern.strip("^$"), value) is not None


def _problems(error: ValidationError) -> str:
    # Name the fields; never echo supplied values (an env value may hold a pasted secret).
    problems = []
    for item in error.errors():
        where = ".".join(str(part) for part in item["loc"]) or "document"
        message = item["msg"].removeprefix("Value error, ")
        problems.append(f"{where}: {message}" if item["type"] == "value_error" else where)
    return "; ".join(problems[:10])


def _load(path: Path, model, code: str):
    try:
        data = load_file(path, MAX_BYTES)
    except StrictYamlError as error:
        raise ServiceError(code, f"{path.name}: {error}") from None
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise ServiceError(code, f"{path.name}: {_problems(error)}") from None


def load_service(path: Path) -> ServiceSpec:
    return _load(path, ServiceSpec, "SERVICE_INVALID")


def load_stack(path: Path) -> Stack:
    return _load(path, Stack, "STACK_INVALID")
