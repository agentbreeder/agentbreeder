"""Keycloak adapter: a confidential client with a service account is the workload principal.

`ref` is the client's internal UUID. Realm roles use the explicit resource namespace `realm`;
client roles use the owning client's UUID. Group membership uses stable group IDs.
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
from agentbreeder.identity._common import call, parse_date, require_setting, safe_id
from agentbreeder.operator_config import resolve_credential

REALM_RESOURCE = "realm"
GROUP_LIMIT = 500


class KeycloakProvider:
    def __init__(self, context: ProviderContext):
        settings = context.settings
        self.name = context.name
        self.transport = context.transport
        self.base_url = str(require_setting(settings, "base_url")).rstrip("/")
        self.realm = str(require_setting(settings, "realm"))
        self.client_id = str(require_setting(settings, "client_id"))
        self._credential_ref = require_setting(settings, "credential")
        self._base = context.base
        self.authority = f"{self.base_url}/realms/{self.realm}"
        self.kind_attribute = settings.get("kind_attribute", "agentbreeder.kind")
        self.sponsor_attribute = settings.get("sponsor_attribute", "agentbreeder.sponsors")
        self.environment_attribute = settings.get(
            "environment_attribute", "agentbreeder/environment"
        )
        self.review_attribute = settings.get("review_attribute", "agentbreeder/review_by")

    def _headers(self) -> dict:
        body = call(
            self.transport,
            "POST",
            f"{self.authority}/protocol/openid-connect/token",
            form={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": resolve_credential(self._credential_ref, self._base) or "",
            },
        )
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise ProviderError("PROVIDER_ERROR", "Keycloak token request failed")
        return {"Authorization": f"Bearer {token}"}

    def resolve(self, ref: str) -> AgentPrincipal:
        identifier = safe_id(ref)
        headers = self._headers()
        admin = f"{self.base_url}/admin/realms/{safe_id(self.realm)}"
        client = call(self.transport, "GET", f"{admin}/clients/{identifier}", headers=headers)
        if not isinstance(client, dict) or client.get("id") != ref:
            raise ProviderError("PRINCIPAL_AMBIGUOUS", "Keycloak returned a different client")
        if client.get("serviceAccountsEnabled") is not True:
            kind = "unknown"
            user = {"id": "", "enabled": False, "attributes": {}}
            mappings, groups = {}, []
        else:
            user = call(
                self.transport,
                "GET",
                f"{admin}/clients/{identifier}/service-account-user",
                headers=headers,
            )
            if not isinstance(user, dict) or not user.get("id"):
                raise ProviderError("PROVIDER_ERROR", "Unexpected Keycloak service account")
            uid = safe_id(user["id"])
            mappings = call(
                self.transport, "GET", f"{admin}/users/{uid}/role-mappings", headers=headers
            )
            groups = call(
                self.transport,
                "GET",
                f"{admin}/users/{uid}/groups?briefRepresentation=true&max={GROUP_LIMIT}",
                headers=headers,
            )
            if not isinstance(groups, list) or len(groups) >= GROUP_LIMIT:
                raise ProviderError("PROVIDER_ERROR", "Keycloak group result is incomplete")
            client_attributes = client.get("attributes") or {}
            kind = "agent" if client_attributes.get(self.kind_attribute) == "agent" else "workload"
        attributes = _single_valued(user.get("attributes"))
        raw_attributes = user.get("attributes") if isinstance(user.get("attributes"), dict) else {}
        sponsor_ids = raw_attributes.get(self.sponsor_attribute) or []
        if not isinstance(sponsor_ids, list):
            sponsor_ids = []
        sponsors = []
        for sponsor in sponsor_ids[:16]:
            try:
                person = call(
                    self.transport, "GET", f"{admin}/users/{safe_id(sponsor)}", headers=headers
                )
                if not isinstance(person, dict):
                    raise ProviderError("PROVIDER_ERROR", "Unexpected Keycloak sponsor shape")
                username = str(person.get("username") or "")
                is_human = not person.get("serviceAccountClientId") and not username.startswith(
                    "service-account-"
                )
                sponsors.append(
                    Person(
                        str(sponsor),
                        is_human and person.get("enabled") is True,
                        username or None,
                        "human" if is_human else "unknown",
                    )
                )
            except ProviderError as error:
                if error.code != "PRINCIPAL_NOT_FOUND":
                    raise
                sponsors.append(Person(str(sponsor), False))
        try:
            roles = {
                RoleGrant(self.authority, REALM_RESOURCE, str(r["id"]), r.get("name"))
                for r in (mappings or {}).get("realmMappings") or ()
            }
            for entry in ((mappings or {}).get("clientMappings") or {}).values():
                roles.update(
                    RoleGrant(self.authority, str(entry["id"]), str(r["id"]), r.get("name"))
                    for r in entry.get("mappings") or ()
                )
            group_grants = frozenset(
                GroupGrant(self.authority, str(g["id"]), g.get("path")) for g in groups or ()
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ProviderError("PROVIDER_ERROR", "Unexpected Keycloak grant shape") from error
        return AgentPrincipal(
            provider=self.name,
            authority=self.authority,
            id=ref,
            kind=kind,
            enabled=client.get("enabled") is True and user.get("enabled") is True,
            sponsors=tuple(sponsors),
            roles=frozenset(roles),
            groups=group_grants,
            attributes=MappingProxyType(attributes),
            review_by=parse_date(attributes.get(self.review_attribute)),
            environment=attributes.get(self.environment_attribute),
            evidence_digest=evidence_digest(
                client, user, mappings, groups, [(p.id, p.active, p.kind) for p in sponsors]
            ),
        )


def _single_valued(attributes) -> dict[str, str]:
    """Keycloak attributes are lists; multi-valued entries are ambiguous and dropped."""
    result = {}
    for key, values in (attributes or {}).items():
        if isinstance(values, list) and len(values) == 1 and isinstance(values[0], str):
            result[key] = values[0]
    return result
