"""Deploy-time identity and authorization gate. Runs before any runtime (Docker) operation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from agentbreeder.audit import canonical
from agentbreeder.deployer import Deployer
from agentbreeder.identity import (
    AgentPrincipal,
    GroupGrant,
    IdentityProvider,
    ProviderError,
    RoleGrant,
    load_provider,
)
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import DeploymentContext, OperatorConfig
from agentbreeder.policy import PolicyDecision, PolicyEngine, PolicyError, PolicyRequest
from agentbreeder.store import DeploymentError

IDENTITY_CHECKS = (
    "identity.resolve",
    "identity.kind",
    "identity.enabled",
    "identity.sponsor",
    "identity.review",
    "identity.grants",
    "identity.environment",
)
# Audit mode may proceed past these evaluated denies; everything else always blocks.
OVERRIDABLE = frozenset(
    {
        "IDENTITY_REQUIRED",
        "PRINCIPAL_NOT_FOUND",
        "PRINCIPAL_AMBIGUOUS",
        "PROVIDER_TIMEOUT",
        "PROVIDER_ERROR",
        "PROVIDER_UNKNOWN",
        "PROVIDER_UNSUPPORTED",
        "PRINCIPAL_KIND_REJECTED",
        "PRINCIPAL_DISABLED",
        "SPONSOR_MISSING",
        "OWNER_MISMATCH",
        "REVIEW_EXPIRED",
        "ROLE_NOT_GRANTED",
        "GROUP_NOT_GRANTED",
        "ATTRIBUTE_MISMATCH",
        "ENVIRONMENT_MISMATCH",
        "POLICY_DENIED",
    }
)


class GateDenied(DeploymentError):
    def __init__(self, code: str, message: str, attempt_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.attempt_id = attempt_id

    def __str__(self) -> str:
        suffix = f" (attempt {self.attempt_id})" if self.attempt_id else ""
        return f"[{self.code}] {self.args[0]}{suffix}"


@dataclass
class GateDecision:
    allowed: bool
    code: str | None = None
    message: str | None = None
    principal: AgentPrincipal | None = None
    checks_evaluated: list[str] = field(default_factory=list)
    checks_unavailable: list[str] = field(default_factory=list)
    obligations: tuple[str, ...] = ()
    policy_ids: tuple[str, ...] = ()
    bundle_digests: dict[str, str] = field(default_factory=dict)
    revision: str | None = None
    risk_tier: str | None = None
    warnings: list[str] = field(default_factory=list)

    forced: bool = False  # a normally overridable deny that audit mode must still enforce

    @property
    def overridable(self) -> bool:
        return self.code in OVERRIDABLE and not self.forced

    def deny(self, code: str, message: str) -> GateDecision:
        self.allowed, self.code, self.message = False, code, message
        return self


@dataclass(frozen=True)
class IdentityFailure:
    code: str
    message: str


class IdentityGate:
    def __init__(
        self,
        operator: OperatorConfig,
        *,
        provider_loader: Callable[..., IdentityProvider] = load_provider,
        policy_factory: Callable[..., PolicyEngine] = PolicyEngine,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ):
        self.operator = operator
        self.provider_loader = provider_loader
        self.policy_factory = policy_factory
        self.today = today
        self.base = operator.path.parent if operator.path else None

    # -- shared pieces -----------------------------------------------------------------------

    def resolve(self, provider: str, ref: str) -> AgentPrincipal:
        adapter = self.provider_loader(provider, self.operator.model.providers, base=self.base)
        principal = adapter.resolve(ref)
        if principal.provider != provider:
            raise ProviderError("PRINCIPAL_AMBIGUOUS", "Adapter returned another provider")
        return principal

    def provider(self, provider: str) -> IdentityProvider:
        return self.provider_loader(provider, self.operator.model.providers, base=self.base)

    def identity_failure(
        self, manifest: Manifest, principal: AgentPrincipal, context: DeploymentContext
    ) -> IdentityFailure | None:
        """Checks 2-7 against verified facts. Names never satisfy requirements; IDs do."""
        identity = manifest.identity
        if principal.kind not in ("agent", "workload"):
            return IdentityFailure(
                "PRINCIPAL_KIND_REJECTED", "Principal is not an agent or workload identity"
            )
        if not principal.enabled:
            return IdentityFailure("PRINCIPAL_DISABLED", "Principal is disabled in the IdP")
        active = [p for p in principal.sponsors if p.active and p.kind == "human"]
        if self.operator.model.require_sponsor and not active:
            return IdentityFailure("SPONSOR_MISSING", "Principal has no active human sponsor")
        if identity.expect_owner:
            people = principal.sponsors + principal.owners
            expected = identity.expect_owner.lower()
            if not any(expected in {p.id.lower(), (p.name or "").lower()} for p in people):
                return IdentityFailure("OWNER_MISMATCH", "expect_owner is not a sponsor or owner")
        if principal.review_by is not None and principal.review_by < self.today():
            return IdentityFailure("REVIEW_EXPIRED", "Principal is past its review date")
        requires = manifest.requires
        if requires:
            for role in requires.roles:
                if RoleGrant(role.authority, role.resource, role.role_id) not in principal.roles:
                    return IdentityFailure(
                        "ROLE_NOT_GRANTED", f"Required role {role.role_id} is not granted"
                    )
            for group in requires.groups:
                if GroupGrant(group.authority, group.group_id) not in principal.groups:
                    return IdentityFailure(
                        "GROUP_NOT_GRANTED", f"Required group {group.group_id} is not granted"
                    )
            for key, value in requires.attributes.items():
                if principal.attributes.get(key) != value:
                    return IdentityFailure(
                        "ATTRIBUTE_MISMATCH", f"Attribute {key} does not have the required value"
                    )
        if not (identity.environment == principal.environment == context.environment):
            return IdentityFailure(
                "ENVIRONMENT_MISMATCH",
                "identity.environment, the IdP environment and --environment must match",
            )
        return None

    def risk_tier(self, principal: AgentPrincipal | None) -> str | None:
        attribute = self.operator.model.risk_attribute
        value = principal.attributes.get(attribute) if principal and attribute else None
        return (value.strip() or None) if isinstance(value, str) else None

    def policy_request(
        self,
        action: str,
        subject: Deployer,
        *,
        agent: str,
        context: DeploymentContext,
        principal: AgentPrincipal | dict | None,
        risk_tier: str | None,
        manifest: Manifest | None = None,
        extra: dict | None = None,
        tool_actions: list[str] | None = None,
        approval_required: list[str] | None = None,
    ) -> PolicyRequest:
        if isinstance(principal, AgentPrincipal):
            principal_props = {
                "provider": principal.provider,
                "authority": principal.authority,
                "id": principal.id,
                "kind": principal.kind,
                "groups": sorted(f"{g.authority}|{g.group_id}" for g in principal.groups),
                "roles": sorted(
                    f"{r.authority}|{r.resource}|{r.role_id}" for r in principal.roles
                ),
            }
        else:
            principal_props = principal or {}
        capabilities = manifest.capabilities if manifest else None
        return PolicyRequest(
            subject={
                "type": "user",
                "id": subject.subject,
                "properties": {"verified": subject.verified, "issuer": subject.issuer},
            },
            action=action,
            resource={
                "type": "agent",
                "id": agent,
                "properties": {
                    "environment": context.environment,
                    "target": context.target,
                    "risk_tier": risk_tier or "",
                    "principal": principal_props,
                    "tool_actions": sorted(
                        {a for t in capabilities.tools for a in t.actions}
                        if capabilities
                        else (tool_actions or ())
                    ),
                    "approval_required": sorted(capabilities.approval_required)
                    if capabilities
                    else sorted(approval_required or []),
                },
            },
            context=extra or {},
        )

    def evaluate(self, request: PolicyRequest, restrictions: list[str]) -> PolicyDecision:
        pdp = self.operator.model.pdp
        if pdp is None:
            raise PolicyError("POLICY_UNAVAILABLE", "No policy decision point is configured")
        engine = self.policy_factory(pdp, restrictions, self.base)
        return engine.evaluate(request)

    # -- the gate ----------------------------------------------------------------------------

    def check(
        self,
        manifest: Manifest,
        deployer: Deployer,
        context: DeploymentContext,
        *,
        is_blocked: Callable[[str, str], bool],
        is_reference_blocked: Callable[[str, str, str], bool] = lambda *_: False,
    ) -> GateDecision:
        model = self.operator.model
        decision = GateDecision(allowed=True)
        evaluated, unavailable = decision.checks_evaluated, decision.checks_unavailable
        if model.pdp is None:
            unavailable += ["policy", "approval"]
            decision.warnings.append(
                "Identity preflight only: no policy decision point or approval checks configured"
            )
        if not deployer.verified:
            unavailable.append("deployer_verification")
            decision.warnings.append("Deployer is the unverified OS user")
        runtime = model.runtime_enforcement
        if runtime.mode == "none" and manifest.capabilities and manifest.capabilities.tools:
            unavailable.append("runtime_tool_enforcement")
            decision.warnings.append("Tool capabilities are a deploy-time ceiling only")

        # Configured controls this installation cannot enforce deny rather than being skipped.
        evaluated.append("controls")
        if runtime.mode == "mcp_http":
            evaluated.append("runtime_boundary")
            if model.pdp is None:
                return decision.deny(
                    "CONTROL_UNSUPPORTED", "mcp_http runtime enforcement needs a configured PDP"
                )
            approved = {d.url for d in runtime.destinations.values()}
            tools = manifest.capabilities.tools if manifest.capabilities else []
            if any(tool.mcp not in approved for tool in tools):
                return decision.deny(
                    "DESTINATION_UNAPPROVED",
                    "A declared MCP server is not an operator-approved destination",
                )
        if model.maker_checker.risk_tiers and (model.pdp is None or not model.risk_attribute):
            return decision.deny(
                "CONTROL_UNSUPPORTED",
                "maker_checker needs a configured PDP and risk_attribute",
            )
        evaluated.append("deployer")
        if (model.deployer.require_verified or model.pdp is not None) and not deployer.verified:
            return decision.deny(
                "DEPLOYER_UNVERIFIED",
                "A verified deployer identity is required (agentbreeder login)",
            )
        # Secrets are references only (manifest validation); local values are files checked by
        # the deployment preflight after this gate (secret_files.py). Nothing is read here.
        evaluated.append("secrets")
        if manifest.policy and manifest.policy.restrictions and model.pdp is None:
            return decision.deny(
                "POLICY_UNAVAILABLE", "Manifest selects restrictions but no PDP is configured"
            )
        # With a PDP or maker-checker configured, audit mode may not skip policy/approval by
        # overriding a failure that happens before policy can be evaluated.
        policy_required = model.pdp is not None or bool(model.maker_checker.risk_tiers)
        if manifest.identity is None:
            evaluated.append("identity.resolve")
            decision.forced = policy_required
            return decision.deny(
                "IDENTITY_REQUIRED",
                "schema_version 1 has no identity; use schema_version 2 with an identity block",
            )

        evaluated.append("blocker")
        if is_reference_blocked(
            manifest.identity.provider, manifest.identity.ref, context.environment
        ):
            return decision.deny(
                "PRINCIPAL_BLOCKED", "Principal is administratively disabled in this environment"
            )
        evaluated.append("identity.resolve")
        try:
            principal = self.resolve(manifest.identity.provider, manifest.identity.ref)
        except ProviderError as error:
            decision.forced = policy_required
            return decision.deny(error.code, str(error))
        except Exception:
            decision.forced = policy_required
            return decision.deny("PROVIDER_ERROR", "Identity provider lookup failed")
        decision.principal = principal
        if is_blocked(principal.key, context.environment):
            return decision.deny(
                "PRINCIPAL_BLOCKED", "Principal is administratively disabled in this environment"
            )
        evaluated.extend(IDENTITY_CHECKS[1:])
        failure = self.identity_failure(manifest, principal, context)
        if failure:
            decision.deny(failure.code, failure.message)
            if self.operator.enforcement != "audit":
                return decision
            # Audit mode keeps evaluating so an overridden identity deny cannot skip policy
            # obligations such as maker-checker approval.

        decision.risk_tier = self.risk_tier(principal)
        if model.maker_checker.risk_tiers and decision.risk_tier is None:
            decision.forced = False
            return decision.deny(
                "RISK_UNVERIFIABLE",
                "maker_checker is configured but the verified risk attribute is missing",
            )
        obligations: set[str] = set()
        if model.pdp is not None:
            evaluated.append("policy")
            request = self.policy_request(
                "deploy",
                deployer,
                agent=manifest.name,
                context=context,
                principal=principal,
                risk_tier=decision.risk_tier,
                manifest=manifest,
                extra={
                    "image_digest": image_digest(manifest.image),
                    "operator_config_digest": self.operator.digest,
                },
            )
            try:
                result = self.evaluate(
                    request, list(manifest.policy.restrictions) if manifest.policy else []
                )
            except PolicyError as error:
                return decision.deny(error.code, str(error))
            decision.policy_ids = result.policy_ids
            decision.bundle_digests = dict(result.bundle_digests)
            if not result.allowed:
                if result.code != "POLICY_DENIED" or self.operator.enforcement != "audit":
                    return decision.deny(
                        result.code or "POLICY_DENIED", result.message or "Denied"
                    )
                if decision.allowed:
                    decision.deny("POLICY_DENIED", result.message or "Denied")
                # Audit mode continues so obligations (approval) still apply after an override.
            obligations.update(result.obligations)
            if decision.risk_tier and decision.risk_tier in model.maker_checker.risk_tiers:
                obligations.add("require_approval")
            evaluated.append("approval")
        decision.obligations = tuple(sorted(obligations))
        decision.revision = revision_digest(
            manifest, principal, deployer, context, self.operator, decision
        )
        return decision


def image_digest(image: str) -> str:
    return "sha256:" + image.rsplit("sha256:", 1)[1]


def revision_digest(
    manifest: Manifest,
    principal: AgentPrincipal | None,
    deployer: Deployer,
    context: DeploymentContext,
    operator: OperatorConfig,
    decision: GateDecision,
) -> str:
    from agentbreeder.policy import SCHEMA_VERSION

    body = {
        "manifest": manifest.model_dump(mode="json"),
        "image": image_digest(manifest.image),
        "principal": [principal.provider, principal.authority, principal.id]
        if principal
        else None,
        "deployer": deployer.label,
        "environment": context.environment,
        "target": context.target,
        "operator_config": operator.digest,
        "policy": decision.bundle_digests,
        "schema": SCHEMA_VERSION,
        "obligations": sorted(decision.obligations),
    }
    return "sha256:" + hashlib.sha256(canonical(body).encode()).hexdigest()
