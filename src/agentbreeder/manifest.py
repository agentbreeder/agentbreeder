"""Strict, bounded parsing for the new deployment contract."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

MAX_MANIFEST_BYTES = 64 * 1024
_REPOSITORY_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
_IMAGE_PATTERN = (
    r"^(?:[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?/)?"
    + _REPOSITORY_COMPONENT
    + r"(?:/"
    + _REPOSITORY_COMPONENT
    + r")*(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?@sha256:[a-f0-9]{64}$"
)


class ManifestError(ValueError):
    """A safe user-facing validation error; never includes input values."""


_ID = r"^[A-Za-z0-9][A-Za-z0-9._:@/+=-]{0,255}$"
_ENVIRONMENT = r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$"
_ATTRIBUTE_KEY = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_BUNDLE_ID = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_ACTION = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class IdentityReference(_Strict):
    """A reference to an existing IdP principal. AgentBreeder never creates it."""

    provider: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    ref: str = Field(pattern=_ID)
    environment: str = Field(pattern=_ENVIRONMENT)
    expect_owner: str | None = Field(default=None, pattern=_ID)


class RoleRequirement(_Strict):
    authority: str = Field(pattern=_ID)
    resource: str = Field(pattern=_ID)
    role_id: str = Field(pattern=_ID)


class GroupRequirement(_Strict):
    authority: str = Field(pattern=_ID)
    group_id: str = Field(pattern=_ID)


class Requirements(_Strict):
    roles: list[RoleRequirement] = Field(default_factory=list, max_length=64)
    groups: list[GroupRequirement] = Field(default_factory=list, max_length=64)
    attributes: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("attributes")
    @classmethod
    def namespaced(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not re.fullmatch(_ATTRIBUTE_KEY, key):
                raise ValueError("attribute keys must be namespace/name")
            if not 1 <= len(item) <= 256:
                raise ValueError("attribute values must be 1-256 characters")
        return value


class ToolCapability(_Strict):
    mcp: str = Field(max_length=512, pattern=r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[^\s]*)?$")
    actions: list[str] = Field(min_length=1, max_length=64)

    @field_validator("actions")
    @classmethod
    def action_names(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(_ACTION, action) for action in value):
            raise ValueError("invalid action name")
        return value


class Capabilities(_Strict):
    """Deploy-time ceiling; enforced per MCP HTTP call when the operator enables mcp_http."""

    tools: list[ToolCapability] = Field(default_factory=list, max_length=32)
    approval_required: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def declared(self):
        actions = {action for tool in self.tools for action in tool.actions}
        if not set(self.approval_required) <= actions:
            raise ValueError("approval_required must name declared tool actions")
        return self


class PolicySelection(_Strict):
    """Operator-approved restriction bundle IDs only; never paths or OCI references."""

    restrictions: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("restrictions")
    @classmethod
    def bundle_ids(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(_BUNDLE_ID, item) for item in value) or len(set(value)) != len(
            value
        ):
            raise ValueError("restrictions must be unique operator-approved bundle IDs")
        return value


_ALIAS = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"
_GATEWAY = r"^[a-z0-9][a-z0-9_-]{0,31}$"


class ModelAccess(_Strict):
    """Model gateway access this deployment requests. The operator's environment grants it:
    the gate checks the gateway, the alias allow-list and the budget/rate ceilings."""

    gateway: str = Field(pattern=_GATEWAY)
    aliases: list[str] = Field(min_length=1, max_length=20)
    budget_usd: float | None = Field(default=None, gt=0, le=100000)
    rpm: int | None = Field(default=None, ge=1, le=100000)

    @field_validator("aliases")
    @classmethod
    def alias_names(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(_ALIAS, alias) for alias in value) or len(set(value)) != len(
            value
        ):
            raise ValueError("aliases must be unique gateway model names")
        return value


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1, 2]
    name: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
    image: str = Field(max_length=512, pattern=f"(?:{_IMAGE_PATTERN})|(?:^sha256:[a-f0-9]{{64}}$)")
    target: Literal["local", "kubernetes"] = "local"
    port: int = Field(default=8080, ge=1, le=65535)
    health_path: str = Field(default="/health", pattern=r"^/[A-Za-z0-9/_-]*$")
    secrets: dict[str, str] = Field(default_factory=dict)
    identity: IdentityReference | None = None
    requires: Requirements | None = None
    capabilities: Capabilities | None = None
    policy: PolicySelection | None = None
    model_access: ModelAccess | None = None

    def digest(self) -> str:
        """Spec digest. Optional fields added later are left out when unset, so a manifest
        deployed before they existed keeps the same digest (no NAME_CONFLICT on redeploy)."""
        exclude = {"model_access"} if self.model_access is None else set()
        return hashlib.sha256(self.model_dump_json(exclude=exclude).encode()).hexdigest()

    @model_validator(mode="after")
    def version_fields(self):
        v2 = (self.identity, self.requires, self.capabilities, self.policy)
        if self.schema_version == 1 and any(item is not None for item in v2):
            raise ValueError("identity, requires, capabilities and policy need schema_version 2")
        if self.schema_version == 2 and self.identity is None:
            raise ValueError("schema_version 2 requires an identity block")
        return self

    @model_validator(mode="after")
    def local_image(self):
        if self.image.startswith("sha256:") and self.target != "local":
            raise ValueError("Local image IDs require the local target")
        return self

    @field_validator("image")
    @classmethod
    def registry_port(cls, value: str) -> str:
        first, separator, _ = value.partition("/")
        if separator and ":" in first and not 1 <= int(first.rsplit(":", 1)[1]) <= 65535:
            raise ValueError("invalid registry port")
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("secrets")
    @classmethod
    def secret_references(cls, value: dict[str, str]) -> dict[str, str]:
        for name, ref in value.items():
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
                raise ValueError("secret environment names must use uppercase letters/digits/_")
            if not re.fullmatch(r"secret://[a-zA-Z0-9][a-zA-Z0-9/_-]{0,127}", ref):
                raise ValueError("secrets must be secret:// references, never literal values")
        return value


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ManifestError("YAML mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_manifest(path: Path) -> Manifest:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
    except OSError as error:
        raise ManifestError("Cannot read manifest; check the path and file permissions") from error
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError("Manifest exceeds the 64 KiB limit")
    try:
        text = raw.decode("utf-8")
        # Aliases/anchors are unnecessary for this small contract and complicate bounded parsing.
        if any(isinstance(t, (yaml.AliasToken, yaml.AnchorToken)) for t in yaml.scan(text)):
            raise ManifestError("YAML anchors and aliases are not supported")
        data = yaml.load(text, Loader=_UniqueLoader)
    except ManifestError:
        raise
    except (ValueError, yaml.YAMLError, RecursionError) as error:
        raise ManifestError(
            "Invalid UTF-8 YAML; use one document with simple key/value fields"
        ) from error
    try:
        return Manifest.model_validate(data)
    except ValidationError as error:
        # Do not print Pydantic's default error rendering: it includes supplied input values.
        fields = sorted({str(e["loc"][0]) if e["loc"] else "document" for e in error.errors()})
        known = sorted(set(fields) & set(Manifest.model_fields))
        location = ", ".join(known) if known else "document or unknown fields"
        raise ManifestError(
            f"Invalid {location}; see docs/manifest.md for the contract"
        ) from error
