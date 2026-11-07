"""Okta adapter — EXPERIMENTAL. Requires `experimental: true` in the provider connection.

Okta's AI-agent API is Beta. This adapter reads a service (OAuth `service`) application as the
workload principal using stable endpoints. Role and assignment semantics for agents must be
verified against a live org before production use.
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

ADMIN_ROLE_RESOURCE = "okta-admin-roles"


class OktaProvider:
    def __init__(self, context: ProviderContext):
        settings = context.settings
        if settings.get("experimental") is not True:
            raise ProviderError(
                "PROVIDER_UNSUPPORTED", "The Okta adapter is experimental; opt in explicitly"
            )
        self.name = context.name
        self.transport = context.transport
        self.org = str(require_setting(settings, "org_url")).rstrip("/")
        self._token_ref = require_setting(settings, "token")
        self._base = context.base
        self.namespace = settings.get("attribute_namespace", "okta")
        self.kind_attribute = settings.get("kind_attribute", "agentbreederKind")
        self.sponsor_attribute = settings.get("sponsor_attribute", "agentbreederSponsors")
        self.environment_attribute = settings.get(
            "environment_attribute", "agentbreederEnvironment"
        )
        self.review_attribute = settings.get("review_attribute", "agentbreederReviewBy")
        self.write_enabled = settings.get("write_enabled") is True

    def _headers(self) -> dict:
        return {"Authorization": f"SSWS {resolve_credential(self._token_ref, self._base)}"}

    def resolve(self, ref: str) -> AgentPrincipal:
        identifier = safe_id(ref)
        headers = self._headers()
        app = call(self.transport, "GET", f"{self.org}/api/v1/apps/{identifier}", headers=headers)
        if not isinstance(app, dict) or app.get("id") != ref:
            raise ProviderError("PRINCIPAL_AMBIGUOUS", "Okta returned a different application")
        profile = app.get("profile") if isinstance(app.get("profile"), dict) else {}
        oauth = (
            ((app.get("settings") or {}).get("oauthClient") or {})
            if isinstance(app.get("settings"), dict)
            else {}
        )
        is_service = oauth.get("application_type") == "service"
        kind = (
            "agent"
            if is_service and profile.get(self.kind_attribute) == "agent"
            else ("workload" if is_service else "unknown")
        )
        client_id = ((app.get("credentials") or {}).get("oauthClient") or {}).get("client_id")
        roles = []
        if client_id:
            roles = call(
                self.transport,
                "GET",
                f"{self.org}/oauth2/v1/clients/{safe_id(client_id)}/roles",
                headers=headers,
            )
        # /apps/{id}/groups lists app *assignments*, not principal memberships, so group
        # requirements are unsupported and deny (no groups are reported).
        sponsors = []
        sponsor_ids = profile.get(self.sponsor_attribute) or []
        for sponsor in sponsor_ids[:16] if isinstance(sponsor_ids, list) else []:
            try:
                user = call(
                    self.transport,
                    "GET",
                    f"{self.org}/api/v1/users/{safe_id(sponsor)}",
                    headers=headers,
                )
                if not isinstance(user, dict):
                    raise ProviderError("PROVIDER_ERROR", "Unexpected Okta sponsor shape")
                profile_data = user.get("profile")
                login = profile_data.get("login") if isinstance(profile_data, dict) else None
                sponsors.append(Person(str(sponsor), user.get("status") == "ACTIVE", login))
            except ProviderError as error:
                if error.code != "PRINCIPAL_NOT_FOUND":
                    raise
                sponsors.append(Person(str(sponsor), False))
        attributes = {
            f"{self.namespace}/{key}": value
            for key, value in profile.items()
            if isinstance(value, str)
        }
        try:
            role_grants = frozenset(
                RoleGrant(
                    self.org,
                    ADMIN_ROLE_RESOURCE,
                    str(r.get("role") or r["type"]),
                    r.get("label"),
                )
                for r in roles or ()
                if r.get("status", "ACTIVE") == "ACTIVE"
            )
            group_grants: frozenset[GroupGrant] = frozenset()
        except (KeyError, TypeError, AttributeError) as error:
            raise ProviderError("PROVIDER_ERROR", "Unexpected Okta grant shape") from error
        return AgentPrincipal(
            provider=self.name,
            authority=self.org,
            id=ref,
            kind=kind,
            enabled=app.get("status") == "ACTIVE",
            sponsors=tuple(sponsors),
            roles=role_grants,
            groups=group_grants,
            attributes=MappingProxyType(attributes),
            review_by=parse_date(profile.get(self.review_attribute)),
            environment=profile.get(self.environment_attribute),
            evidence_digest=evidence_digest(app, roles, [(p.id, p.active) for p in sponsors]),
        )

    def disable(self, ref: str) -> None:
        if not self.write_enabled:
            raise ProviderError("PROVIDER_UNSUPPORTED", "Okta write access is not opted in")
        call(
            self.transport,
            "POST",
            f"{self.org}/api/v1/apps/{safe_id(ref)}/lifecycle/deactivate",
            headers=self._headers(),
        )
