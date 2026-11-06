"""Microsoft Entra Agent ID adapter (Graph v1.0). Read-only unless write is opted in.

Required application permissions (read): Application.Read.All, AgentIdentity.Read.All (or the
directory-read equivalent), GroupMember.Read.All, CustomSecAttributeAssignment.Read.All.
The typed lookup `GET /v1.0/servicePrincipals/{id}/microsoft.graph.agentIdentity` and response
shapes are covered by recorded fixtures; verify them against a live tenant (pytest -m entra_live)
before relying on this adapter in production.
"""

from __future__ import annotations

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
from agentbreeder.identity._common import call, paged, parse_date, require_setting, safe_id
from agentbreeder.operator_config import resolve_credential

_WORKLOAD_TYPES = {"Application", "ManagedIdentity"}


class EntraProvider:
    def __init__(self, context: ProviderContext):
        settings = context.settings
        self.name = context.name
        self.transport = context.transport
        self.tenant = str(require_setting(settings, "tenant_id"))
        self.client_id = str(require_setting(settings, "client_id"))
        self._credential_ref = require_setting(settings, "credential")
        self._base = context.base
        self.graph = str(settings.get("graph_base", "https://graph.microsoft.com")).rstrip("/")
        self.login = str(settings.get("login_base", "https://login.microsoftonline.com")).rstrip(
            "/"
        )
        self.environment_attribute = settings.get(
            "environment_attribute", "AgentBreeder/environment"
        )
        self.review_attribute = settings.get("review_attribute", "AgentBreeder/reviewBy")
        self.write_enabled = settings.get("write_enabled") is True

    def _headers(self) -> dict:
        secret = resolve_credential(self._credential_ref, self._base)
        body = call(
            self.transport,
            "POST",
            f"{self.login}/{safe_id(self.tenant)}/oauth2/v2.0/token",
            form={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": secret or "",
                "scope": f"{self.graph}/.default",
            },
        )
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise ProviderError("PROVIDER_ERROR", "Entra token request failed")
        return {"Authorization": f"Bearer {token}"}

    def resolve(self, ref: str) -> AgentPrincipal:
        identifier = safe_id(ref)
        headers = self._headers()
        base = f"{self.graph}/v1.0/servicePrincipals/{identifier}"
        select = "$select=id,accountEnabled,servicePrincipalType,customSecurityAttributes"
        try:
            principal = call(
                self.transport,
                "GET",
                f"{base}/microsoft.graph.agentIdentity?{select}",
                headers=headers,
            )
            kind = "agent"
        except ProviderError as error:
            if error.code != "PRINCIPAL_NOT_FOUND":
                raise
            principal = call(self.transport, "GET", f"{base}?{select}", headers=headers)
            kind = (
                "workload"
                if isinstance(principal, dict)
                and principal.get("servicePrincipalType") in _WORKLOAD_TYPES
                else "unknown"
            )
        if not isinstance(principal, dict) or str(principal.get("id", "")).lower() != ref.lower():
            raise ProviderError("PRINCIPAL_AMBIGUOUS", "Entra returned a different principal")
        assignments = list(paged(self.transport, f"{base}/appRoleAssignments", headers))
        groups = list(
            paged(
                self.transport,
                f"{base}/transitiveMemberOf/microsoft.graph.group?$select=id,displayName",
                headers,
            )
        )
        people = "$select=id,accountEnabled,userPrincipalName"
        sponsors = (
            list(
                paged(
                    self.transport,
                    f"{base}/microsoft.graph.agentIdentity/sponsors?{people}",
                    headers,
                )
            )
            if kind == "agent"
            else []
        )
        owners = list(paged(self.transport, f"{base}/owners?{people}", headers))
        attributes = _flatten(principal.get("customSecurityAttributes"))
        try:
            roles = frozenset(
                RoleGrant(
                    self.tenant,
                    str(a["resourceId"]),
                    str(a["appRoleId"]),
                    a.get("resourceDisplayName"),
                )
                for a in assignments
                if str(a.get("principalId", ref)).lower() == ref.lower()
            )
            group_grants = frozenset(
                GroupGrant(self.tenant, str(g["id"]), g.get("displayName")) for g in groups
            )
        except (KeyError, TypeError) as error:
            raise ProviderError("PROVIDER_ERROR", "Unexpected Entra grant shape") from error
        return AgentPrincipal(
            provider=self.name,
            authority=self.tenant,
            id=str(principal["id"]),
            kind=kind,
            enabled=principal.get("accountEnabled") is True,
            sponsors=tuple(_person(p) for p in sponsors),
            owners=tuple(_person(p) for p in owners),
            roles=roles,
            groups=group_grants,
            attributes=MappingProxyType(attributes),
            review_by=parse_date(attributes.get(self.review_attribute)),
            environment=attributes.get(self.environment_attribute),
            evidence_digest=evidence_digest(principal, assignments, groups, sponsors, owners),
        )

    def disable(self, ref: str) -> None:
        if not self.write_enabled:
            raise ProviderError("PROVIDER_UNSUPPORTED", "Entra write access is not opted in")
        call(
            self.transport,
            "PATCH",
            f"{self.graph}/v1.0/servicePrincipals/{safe_id(ref)}",
            headers=self._headers(),
            json_body={"accountEnabled": False},
        )


def _person(value: dict) -> Person:
    odata = str(value.get("@odata.type", ""))
    is_user = odata in ("#microsoft.graph.user", "")
    return Person(
        id=str(value.get("id", "")),
        active=is_user and value.get("accountEnabled") is True,
        name=value.get("userPrincipalName"),
        kind="human" if is_user else "group",
    )


def _flatten(attributes) -> dict[str, str]:
    """`{set: {name: value}}` -> `{"set/name": value}`; only single string values are kept."""
    result: dict[str, str] = {}
    if not isinstance(attributes, dict):
        return result
    for set_name, values in attributes.items():
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            if not key.startswith("@") and isinstance(value, str):
                result[f"{set_name}/{key}"] = value
    return result
