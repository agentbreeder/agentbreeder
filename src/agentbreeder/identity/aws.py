"""AWS Bedrock AgentCore workload-identity adapter (requires `agentbreeder[aws]`).

Facts come from the workload identity and its tags:
`agentbreeder:environment`, `agentbreeder:review-by`, `agentbreeder:sponsors` (space-separated
IAM Identity Center user IDs, resolved in the configured identity store). Other tags become
attributes under the `aws-tag/` namespace.

Limitations (fail closed): AgentCore exposes no enabled flag, so local blockers are the disable
mechanism; IAM role grants are not reported until IAM policy simulation is verified, so any
required role in this authority denies; Identity Store exposes no user status, so a sponsor is
active only when found.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from types import MappingProxyType
from typing import Any

from agentbreeder.identity import (
    AgentPrincipal,
    Person,
    ProviderContext,
    ProviderError,
    evidence_digest,
)
from agentbreeder.identity._common import parse_date, require_setting

_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


def _boto3_clients(region: str) -> Callable[[str], Any]:
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as error:
        raise ProviderError(
            "PROVIDER_ERROR", "Install agentbreeder[aws] to use the AWS adapter"
        ) from error
    from botocore.config import Config  # type: ignore[import-not-found]

    config = Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 1})
    return lambda service: boto3.client(service, region_name=region, config=config)


class AwsProvider:
    def __init__(self, context: ProviderContext, clients: Callable[[str], Any] | None = None):
        settings = context.settings
        self.name = context.name
        self.account = str(require_setting(settings, "account_id"))
        self.region = str(require_setting(settings, "region"))
        self.identity_store = settings.get("identity_store_id")
        # Identity Store exposes no user status; presence counts as active only on opt-in.
        self.trust_presence = settings.get("trust_identity_store_presence") is True
        self._clients = clients

    def _client(self, service: str):
        if self._clients is None:
            self._clients = _boto3_clients(self.region)
        return self._clients(service)

    def resolve(self, ref: str) -> AgentPrincipal:
        if not _NAME.fullmatch(ref):
            raise ProviderError("PROVIDER_ERROR", "Unsupported principal reference format")
        control = self._client("bedrock-agentcore-control")
        try:
            identity = control.get_workload_identity(name=ref)
            arn = identity["workloadIdentityArn"]
            tags = control.list_tags_for_resource(resourceArn=arn).get("tags") or {}
        except ProviderError:
            raise
        except Exception as error:
            raise _translate(error) from error
        if identity.get("name") != ref or f":{self.account}:" not in str(arn):
            raise ProviderError("PRINCIPAL_AMBIGUOUS", "Workload identity is in another account")
        sponsors = tuple(
            self._sponsor(s) for s in str(tags.get("agentbreeder:sponsors", "")).split()[:16]
        )
        attributes = {
            f"aws-tag/{key}": value
            for key, value in tags.items()
            if isinstance(value, str) and not key.startswith("agentbreeder:")
        }
        return AgentPrincipal(
            provider=self.name,
            authority=self.account,
            id=ref,
            kind="agent",
            enabled=True,
            sponsors=sponsors,
            attributes=MappingProxyType(attributes),
            review_by=parse_date(tags.get("agentbreeder:review-by")),
            environment=tags.get("agentbreeder:environment"),
            evidence_digest=evidence_digest(
                {k: v for k, v in identity.items() if k != "ResponseMetadata"},
                tags,
                [(p.id, p.active) for p in sponsors],
            ),
        )

    def _sponsor(self, user_id: str) -> Person:
        if not self.identity_store:
            return Person(user_id, False)
        try:
            user = self._client("identitystore").describe_user(
                IdentityStoreId=self.identity_store, UserId=user_id
            )
        except Exception as error:
            translated = _translate(error)
            if translated.code == "PRINCIPAL_NOT_FOUND":
                return Person(user_id, False)
            raise translated from error
        return Person(user_id, self.trust_presence, user.get("UserName"))


def _translate(error: Exception) -> ProviderError:
    code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
    if code in ("ResourceNotFoundException", "NoSuchEntity"):
        return ProviderError("PRINCIPAL_NOT_FOUND", "Principal not found in identity provider")
    if "Timeout" in type(error).__name__:
        return ProviderError("PROVIDER_TIMEOUT", "Identity provider timed out")
    return ProviderError("PROVIDER_ERROR", "AWS identity lookup failed")
