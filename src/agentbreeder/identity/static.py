"""Static file adapter for tests and air-gapped use. The file is operator-controlled."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from agentbreeder.identity import (
    AgentPrincipal,
    GroupGrant,
    Person,
    ProviderContext,
    ProviderError,
    RoleGrant,
    evidence_digest,
)
from agentbreeder.identity._common import parse_date, require_setting
from agentbreeder.operator_config import OperatorConfigError, parse_yaml, read_private

_KINDS = {"agent", "workload", "human", "shared", "unknown"}


class StaticProvider:
    def __init__(self, context: ProviderContext):
        self.name = context.name
        self.authority = str(require_setting(context.settings, "authority"))
        path = Path(str(require_setting(context.settings, "file")))
        self.path = path if path.is_absolute() or context.base is None else context.base / path

    def resolve(self, ref: str) -> AgentPrincipal:
        try:
            document = parse_yaml(read_private(self.path, "Static identity file"), "Static file")
        except OperatorConfigError as error:
            raise ProviderError("PROVIDER_ERROR", str(error)) from error
        principals = document.get("principals") if isinstance(document, dict) else None
        if not isinstance(principals, dict):
            raise ProviderError("PROVIDER_ERROR", "Static identity file needs a principals map")
        if ref not in principals:
            raise ProviderError("PRINCIPAL_NOT_FOUND", "Principal not found in identity provider")
        entry = principals[ref]
        matches = [k for k, v in principals.items() if isinstance(v, dict) and v.get("id") == ref]
        if len(matches) > 1 or (matches and matches != [ref]):
            raise ProviderError("PRINCIPAL_AMBIGUOUS", "Principal reference is ambiguous")
        try:
            return self._principal(ref, entry)
        except ProviderError:
            raise
        except (TypeError, ValueError, KeyError, AttributeError) as error:
            raise ProviderError("PROVIDER_ERROR", "Malformed static principal entry") from error

    def _principal(self, ref: str, entry: dict) -> AgentPrincipal:
        if not isinstance(entry, dict):
            raise ProviderError("PROVIDER_ERROR", "Malformed static principal entry")
        kind = entry.get("kind", "unknown")
        if kind not in _KINDS:
            kind = "unknown"
        attributes = entry.get("attributes") or {}
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in attributes.items()):
            raise ProviderError("PROVIDER_ERROR", "Static attributes must be string pairs")
        return AgentPrincipal(
            provider=self.name,
            authority=self.authority,
            id=str(entry.get("id", ref)),
            kind=kind,
            enabled=entry.get("enabled") is True,
            sponsors=tuple(_person(p) for p in entry.get("sponsors") or ()),
            owners=tuple(_person(p) for p in entry.get("owners") or ()),
            roles=frozenset(
                RoleGrant(
                    str(r.get("authority", self.authority)),
                    str(r["resource"]),
                    str(r["role_id"]),
                    r.get("name"),
                )
                for r in entry.get("roles") or ()
            ),
            groups=frozenset(
                GroupGrant(
                    str(g.get("authority", self.authority)), str(g["group_id"]), g.get("name")
                )
                for g in entry.get("groups") or ()
            ),
            attributes=MappingProxyType(dict(attributes)),
            review_by=parse_date(entry.get("review_by")),
            environment=entry.get("environment"),
            evidence_digest=evidence_digest(self.authority, ref, entry),
        )


def _person(value: dict) -> Person:
    kind = value.get("kind", "human")
    return Person(
        id=str(value["id"]),
        active=value.get("active") is True,
        name=value.get("name"),
        kind=kind if kind in ("human", "group") else "unknown",
    )
