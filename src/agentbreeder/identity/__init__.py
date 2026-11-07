"""Read-only identity-provider adapters. Verify, don't mint."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from importlib.metadata import entry_points
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

from agentbreeder.identity.transport import JsonTransport, Transport

ENTRY_POINT_GROUP = "agentbreeder.identity_providers"
Kind = Literal["agent", "workload", "human", "shared", "unknown"]


class ProviderError(RuntimeError):
    """Typed, safe adapter failure. Messages never include raw IdP payloads."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Person:
    id: str
    active: bool
    name: str | None = None
    kind: Literal["human", "group", "unknown"] = "human"


@dataclass(frozen=True)
class RoleGrant:
    authority: str
    resource: str
    role_id: str
    display_name: str | None = field(default=None, compare=False, hash=False)


@dataclass(frozen=True)
class GroupGrant:
    authority: str
    group_id: str
    display_name: str | None = field(default=None, compare=False, hash=False)


@dataclass(frozen=True)
class AgentPrincipal:
    provider: str
    authority: str
    id: str
    kind: Kind
    enabled: bool
    sponsors: tuple[Person, ...] = ()
    owners: tuple[Person, ...] = ()
    roles: frozenset[RoleGrant] = frozenset()
    groups: frozenset[GroupGrant] = frozenset()
    attributes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    review_by: date | None = None
    environment: str | None = None
    evidence_digest: str = ""

    @property
    def key(self) -> str:
        """Stable blocker/approval key: connection, authority and immutable principal ID."""
        return json.dumps([self.provider, self.authority, self.id], separators=(",", ":"))

    def snapshot(self) -> dict:
        """Bounded, sanitized snapshot for audit records (IDs and flags only)."""
        return {
            "provider": self.provider,
            "authority": self.authority,
            "id": self.id,
            "kind": self.kind,
            "enabled": self.enabled,
            "environment": self.environment,
            "review_by": self.review_by.isoformat() if self.review_by else None,
            "sponsors": [{"id": p.id, "active": p.active} for p in self.sponsors[:16]],
            "roles": sorted([r.authority, r.resource, r.role_id] for r in self.roles)[:64],
            "groups": sorted([g.authority, g.group_id] for g in self.groups)[:64],
            "attribute_keys": sorted(self.attributes)[:64],
        }


def principal_key(provider: str, authority: str, identifier: str) -> str:
    return json.dumps([provider, authority, identifier], separators=(",", ":"))


def evidence_digest(*responses: Any) -> str:
    canonical = json.dumps(responses, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


@runtime_checkable
class IdentityProvider(Protocol):
    name: str

    def resolve(self, ref: str) -> AgentPrincipal: ...


@dataclass(frozen=True)
class ProviderContext:
    name: str  # operator connection name
    settings: Mapping[str, Any]
    transport: Transport
    base: Path | None  # operator config directory, for relative file references


Factory = Callable[[ProviderContext], IdentityProvider]


def _builtins() -> dict[str, Factory]:
    from agentbreeder.identity import aws, entra, keycloak, okta, static

    return {
        "static": static.StaticProvider,
        "entra": entra.EntraProvider,
        "keycloak": keycloak.KeycloakProvider,
        "okta": okta.OktaProvider,
        "aws": aws.AwsProvider,
    }


def available_types() -> dict[str, Factory]:
    """Built-ins first; plugins cannot shadow a built-in adapter type."""
    factories = _builtins()
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if entry.name not in factories:
            factories[entry.name] = entry.load
    return factories


def load_provider(
    name: str,
    providers: Mapping[str, Mapping[str, Any]],
    *,
    transport: Transport | None = None,
    base: Path | None = None,
) -> IdentityProvider:
    if name not in providers:
        raise ProviderError("PROVIDER_UNKNOWN", f"Identity provider {name} is not configured")
    settings = dict(providers[name])
    kind = settings.get("type", name)
    factories = available_types()
    if kind not in factories:
        raise ProviderError("PROVIDER_UNKNOWN", f"No adapter installed for provider type {kind}")
    factory = factories[kind]
    if kind not in _builtins():
        factory = factory()  # entry point: load lazily, only when selected by the operator
    try:
        return factory(ProviderContext(name, settings, transport or JsonTransport(), base))
    except ProviderError:
        raise
    except Exception as error:
        raise ProviderError("PROVIDER_ERROR", f"Cannot initialize provider {name}") from error


__all__ = [
    "AgentPrincipal",
    "GroupGrant",
    "IdentityProvider",
    "Person",
    "ProviderContext",
    "ProviderError",
    "RoleGrant",
    "evidence_digest",
    "load_provider",
    "principal_key",
]
