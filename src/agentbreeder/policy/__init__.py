"""Policy decision point contract, shaped like an OpenID AuthZEN 1.0 access evaluation.

The operator baseline AND every selected operator-approved restriction must allow; any deny or
error wins. Obligations are combined; unknown obligations fail closed. `obligations` is an
AgentBreeder convention (no standard defines it).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

from agentbreeder.operator_config import PolicyConfig, PolicySource

KNOWN_OBLIGATIONS = frozenset({"require_approval"})
SCHEMA_TEXT = resources.files(__package__).joinpath("cedar_schema.json").read_text()
SCHEMA_VERSION = "cedar-schema:sha256:" + hashlib.sha256(SCHEMA_TEXT.encode()).hexdigest()


class PolicyError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PolicyRequest:
    subject: dict
    action: str
    resource: dict
    context: dict = field(default_factory=dict)

    def to_authzen(self) -> dict:
        return {
            "subject": self.subject,
            "action": {"name": self.action},
            "resource": self.resource,
            "context": self.context,
        }


@dataclass(frozen=True)
class SourceResult:
    allowed: bool
    obligations: tuple[str, ...]
    evidence: str  # pinned digest or verified remote revision


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    obligations: tuple[str, ...]
    code: str | None
    message: str | None
    policy_ids: tuple[str, ...]
    bundle_digests: dict[str, str]
    schema_version: str = SCHEMA_VERSION


class Evaluator(Protocol):
    def evaluate(self, request: PolicyRequest) -> SourceResult: ...


def evaluator_for(engine: str, source: PolicySource, base: Path | None) -> Evaluator:
    if engine == "cedar":
        from agentbreeder.policy.cedar import CedarEvaluator

        return CedarEvaluator(source, base)
    from agentbreeder.policy.remote import AuthZenEvaluator, OpaEvaluator

    evaluator = OpaEvaluator if engine == "opa" else AuthZenEvaluator
    return evaluator(source, base=base)


class PolicyEngine:
    """Evaluates the operator baseline plus the selected approved restrictions."""

    def __init__(
        self,
        config: PolicyConfig,
        restrictions: list[str],
        base: Path | None,
        factory=evaluator_for,
    ):
        unapproved = [r for r in restrictions if r not in config.approved_restrictions]
        if unapproved:
            raise PolicyError(
                "RESTRICTION_UNAPPROVED",
                "Manifest selects a restriction the operator has not approved",
            )
        self.ids = ("baseline", *restrictions)
        self.sources = [config.baseline] + [config.approved_restrictions[r] for r in restrictions]
        self.engine = config.engine
        self.base = base
        self.factory = factory

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        obligations: set[str] = set()
        digests: dict[str, str] = {}
        denied = False
        for identifier, source in zip(self.ids, self.sources, strict=True):
            try:
                result = self.factory(self.engine, source, self.base).evaluate(request)
            except PolicyError as error:
                return self._deny(error.code, str(error), digests)
            except Exception:
                return self._deny(
                    "POLICY_ERROR", f"Policy {identifier} evaluation failed", digests
                )
            digests[identifier] = result.evidence
            unknown = set(result.obligations) - KNOWN_OBLIGATIONS
            if unknown:
                return self._deny(
                    "OBLIGATION_UNSUPPORTED", "Policy returned an unsupported obligation", digests
                )
            obligations.update(result.obligations)
            denied = denied or not result.allowed
        if denied:
            # Keep the obligations: audit mode may override a deny but never skip approval.
            return PolicyDecision(
                False,
                tuple(sorted(obligations)),
                "POLICY_DENIED",
                f"Policy denied {request.action}",
                self.ids,
                digests,
            )
        return PolicyDecision(True, tuple(sorted(obligations)), None, None, self.ids, digests)

    def _deny(self, code: str, message: str, digests: dict) -> PolicyDecision:
        return PolicyDecision(False, (), code, message, self.ids, digests)


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def as_list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple, set, frozenset)) else []


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
