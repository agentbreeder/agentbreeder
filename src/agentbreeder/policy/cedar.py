"""Cedar evaluation via pinned `cedarpy` (install `agentbreeder[cedar]`).

Each source is one operator-controlled policy file whose bytes are read once, checked against the
pinned digest, validated against the shipped schema and then evaluated. A source adds the
`require_approval` obligation when it permits the `RequireApproval` action, so restrictions can
add approval requirements but never remove baseline ones.
"""

from __future__ import annotations

import hmac
from pathlib import Path

from agentbreeder.operator_config import OperatorConfigError, PolicySource, read_private
from agentbreeder.policy import (
    SCHEMA_TEXT,
    PolicyError,
    PolicyRequest,
    SourceResult,
    as_list,
    sha256_digest,
)

MAX_POLICY_BYTES = 512 * 1024
_ACTIONS = {"deploy", "approve", "enable", "call_tool"}
# The obligation-probing action for each action that supports `require_approval`.
_APPROVAL_PROBES = {"deploy": "RequireApproval", "call_tool": "RequireCallApproval"}


def _cedarpy():
    try:
        import cedarpy  # type: ignore[import-not-found]
    except ImportError as error:
        raise PolicyError(
            "POLICY_ERROR", "Install agentbreeder[cedar] for the Cedar PDP"
        ) from error
    return cedarpy


class CedarEvaluator:
    def __init__(self, source: PolicySource, base: Path | None):
        if not source.source or not source.digest:
            raise PolicyError("POLICY_ERROR", "Cedar sources need an operator path and digest")
        path = Path(source.source)
        self.path = path if path.is_absolute() or base is None else base / path
        self.digest = source.digest

    def _policies(self) -> str:
        try:
            data = read_private(self.path, "Cedar policy", MAX_POLICY_BYTES)
        except OperatorConfigError as error:
            raise PolicyError("POLICY_ERROR", "Cannot read pinned Cedar policy") from error
        if not hmac.compare_digest(sha256_digest(data), self.digest):
            raise PolicyError("POLICY_DIGEST_MISMATCH", "Policy bytes do not match pinned digest")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PolicyError("POLICY_ERROR", "Cedar policy must be UTF-8") from error

    def evaluate(self, request: PolicyRequest) -> SourceResult:
        if request.action not in _ACTIONS:
            raise PolicyError("POLICY_ERROR", "Unsupported policy action")
        cedarpy = _cedarpy()
        policies = self._policies()
        validation = cedarpy.validate_policies(policies, SCHEMA_TEXT)
        if not validation.validation_passed:
            raise PolicyError("POLICY_INVALID", "Cedar policy failed schema validation")
        if request.action == "call_tool":
            entities = _call_entities(request)
            parties = ('AgentBreeder::Agent::"agent"', 'AgentBreeder::Tool::"tool"')
        else:
            entities = _entities(request)
            parties = ('AgentBreeder::Deployer::"subject"', 'AgentBreeder::Agent::"resource"')
        allowed = self._decide(cedarpy, policies, entities, request.action, parties)
        obligations: tuple[str, ...] = ()
        probe = _APPROVAL_PROBES.get(request.action)
        if probe and self._decide(cedarpy, policies, entities, probe, parties):
            obligations = ("require_approval",)
        return SourceResult(allowed, obligations, self.digest)

    @staticmethod
    def _decide(cedarpy, policies: str, entities: list, action: str, parties) -> bool:
        result = cedarpy.is_authorized(
            {
                "principal": parties[0],
                "action": f'AgentBreeder::Action::"{action}"',
                "resource": parties[1],
                "context": {},
            },
            policies,
            entities,
            schema=SCHEMA_TEXT,
        )
        if result.diagnostics.errors:
            raise PolicyError("POLICY_ERROR", "Cedar evaluation reported errors")
        return result.allowed is True


def _entities(request: PolicyRequest) -> list[dict]:
    subject = request.subject.get("properties") or {}
    resource = request.resource.get("properties") or {}
    principal = resource.get("principal") or {}
    return [
        {
            "uid": {"type": "AgentBreeder::Deployer", "id": "subject"},
            "attrs": {
                "verified": subject.get("verified") is True,
                "issuer": str(subject.get("issuer") or ""),
                "subject": str(request.subject.get("id") or ""),
            },
            "parents": [],
        },
        {
            "uid": {"type": "AgentBreeder::Agent", "id": "resource"},
            "attrs": {
                "name": str(request.resource.get("id") or ""),
                "environment": str(resource.get("environment") or ""),
                "target": str(resource.get("target") or ""),
                "risk_tier": str(resource.get("risk_tier") or ""),
                "image_digest": str(request.context.get("image_digest") or ""),
                "principal_provider": str(principal.get("provider") or ""),
                "principal_authority": str(principal.get("authority") or ""),
                "principal_id": str(principal.get("id") or ""),
                "principal_kind": str(principal.get("kind") or ""),
                "groups": [str(g) for g in as_list(principal.get("groups"))],
                "roles": [str(r) for r in as_list(principal.get("roles"))],
                "tool_actions": [str(a) for a in as_list(resource.get("tool_actions"))],
                "approval_required": [str(a) for a in as_list(resource.get("approval_required"))],
            },
            "parents": [],
        },
    ]


def _call_entities(request: PolicyRequest) -> list[dict]:
    """call_tool: the deployed agent (its verified principal snapshot) calls a tool."""
    agent = request.subject.get("properties") or {}
    tool = request.resource.get("properties") or {}
    return [
        {
            "uid": {"type": "AgentBreeder::Agent", "id": "agent"},
            "attrs": {
                "name": str(agent.get("name") or ""),
                "environment": str(agent.get("environment") or ""),
                "target": str(agent.get("target") or ""),
                "risk_tier": str(agent.get("risk_tier") or ""),
                "image_digest": str(agent.get("image_digest") or ""),
                "principal_provider": str(agent.get("provider") or ""),
                "principal_authority": str(agent.get("authority") or ""),
                "principal_id": str(request.subject.get("id") or ""),
                "principal_kind": str(agent.get("kind") or ""),
                "groups": [str(g) for g in as_list(agent.get("groups"))],
                "roles": [str(r) for r in as_list(agent.get("roles"))],
                "tool_actions": [str(a) for a in as_list(agent.get("tool_actions"))],
                "approval_required": [str(a) for a in as_list(agent.get("approval_required"))],
            },
            "parents": [],
        },
        {
            "uid": {"type": "AgentBreeder::Tool", "id": "tool"},
            "attrs": {
                "server": str(tool.get("server") or ""),
                "name": str(tool.get("name") or ""),
                "url": str(tool.get("url") or ""),
                "environment": str(tool.get("environment") or ""),
                "arguments_digest": str(request.context.get("arguments_digest") or ""),
            },
            "parents": [],
        },
    ]
