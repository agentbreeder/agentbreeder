"""P2 (#704): exact-revision maker-checker approvals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import actor, enable_cedar, principal_entry, v2_manifest_data

from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.manifest import Manifest

pytest.importorskip("cedarpy")

DANA = actor("dana")  # deployer
CAROL = actor("carol")  # authorized checker per BASELINE_POLICY
ERIN = actor("erin")  # verified but not authorized to approve


@pytest.fixture
def high_risk(identity_env):
    enable_cedar(identity_env)
    identity_env.principals["agent-1"] = principal_entry(
        attributes={"acme/data_classification": "confidential", "acme/risk_tier": "high"}
    )
    return identity_env


def service(env, state, runtime):
    return DeploymentService(state, runtime, env.load())


def deploy(env, state, runtime, deployer=DANA, **overrides):
    return service(env, state, runtime).deploy(
        Manifest.model_validate(v2_manifest_data(**overrides)), local=True, deployer=deployer
    )


def pending(env, state, runtime, **overrides):
    result = deploy(env, state, runtime, **overrides)
    assert result["state"] == "pending_approval" and result["approval_id"]
    return result


def test_pending_approval_makes_no_docker_call_and_is_audited(high_risk, state, runtime):
    result = pending(high_risk, state, runtime)
    assert runtime.mock_calls == []
    assert state.active("sample") is None
    approval = state.approval(result["approval_id"])
    assert approval["state"] == "pending" and approval["deployer"] == DANA.label
    expires = datetime.fromisoformat(approval["expires"]) - datetime.fromisoformat(
        approval["created"]
    )
    assert expires == timedelta(seconds=900)
    assert approval["revision"].startswith("sha256:") and approval["evidence"].startswith(
        "sha256:"
    )
    decisions = [r["decision"] for r in state.audit_rows(limit=10)]
    assert decisions[0] == "pending_approval"
    assert state.verify_audit()["incomplete_attempts"] == []
    # Re-running before approval reuses the same request.
    assert pending(high_risk, state, runtime)["approval_id"] == result["approval_id"]


def test_operator_maker_checker_applies_even_if_policy_does_not_ask(high_risk, state, runtime):
    lenient = 'permit(principal, action == AgentBreeder::Action::"deploy", resource);'
    enable_cedar(high_risk, baseline=lenient)
    high_risk.config["maker_checker"] = {"risk_tiers": ["high"]}
    assert deploy(high_risk, state, runtime)["state"] == "pending_approval"


def test_approve_then_redeploy_consumes_once(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    approved = service(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    assert approved["state"] == "approved" and approved["checker"] == CAROL.label
    runtime.authorize.assert_not_called()  # approval itself starts nothing
    result = deploy(high_risk, state, runtime)
    assert result["state"] == "ready" and result["approval_id"] == approval_id
    consumed = state.approval(approval_id)
    assert consumed["state"] == "consumed" and consumed["execution_id"] == result["id"]
    actions = [r["action"] for r in state.audit_rows(limit=50)]
    assert "approval_consumed" in actions and "approval_granted" in actions
    # Idempotent reconcile of the same execution reuses the consumed approval.
    assert deploy(high_risk, state, runtime)["id"] == result["id"]


def test_replay_for_another_execution_is_rejected(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    service(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    first = deploy(high_risk, state, runtime)
    runtime.inspect.side_effect = [runtime.inspect.return_value, None]
    assert service(high_risk, state, runtime).remove(first["id"], local=True)["state"] == "removed"
    runtime.inspect.side_effect = None
    runtime.reset_mock()
    second = deploy(high_risk, state, runtime)
    assert second["state"] == "pending_approval"
    assert second["approval_id"] != approval_id
    runtime.authorize.assert_not_called()


@pytest.mark.parametrize(
    "checker,code",
    [
        (DANA, "SELF_APPROVAL"),
        (actor("alice-id"), "SOLE_SPONSOR_APPROVAL"),
        (actor("someone", alias="alice@example.com"), "SOLE_SPONSOR_APPROVAL"),
        (actor("carol", verified=False), "DEPLOYER_UNVERIFIED"),
        (ERIN, "CHECKER_NOT_AUTHORIZED"),
    ],
)
def test_invalid_checkers_rejected(high_risk, state, runtime, checker, code):
    if code == "SOLE_SPONSOR_APPROVAL":
        allow_sponsor = (
            'permit(principal, action == AgentBreeder::Action::"approve", resource);\n'
            'permit(principal, action == AgentBreeder::Action::"deploy", resource);\n'
            'permit(principal, action == AgentBreeder::Action::"RequireApproval", resource);'
        )
        enable_cedar(high_risk, baseline=allow_sponsor)
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    with pytest.raises(GateDenied) as error:
        service(high_risk, state, runtime).decide(approval_id, checker, approve=True)
    assert error.value.code == code
    assert state.approval(approval_id)["state"] == "pending"
    assert state.audit_rows(limit=1)[0]["decision"] == "deny"


def test_reject_and_decided_requests_cannot_be_reused(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    svc = service(high_risk, state, runtime)
    assert svc.decide(approval_id, CAROL, approve=False)["state"] == "rejected"
    with pytest.raises(GateDenied, match="APPROVAL_NOT_PENDING"):
        svc.decide(approval_id, CAROL, approve=True)
    assert deploy(high_risk, state, runtime)["approval_id"] != approval_id


def test_changed_manifest_requires_new_approval(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    service(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    changed = deploy(high_risk, state, runtime, port=9090)
    assert changed["state"] == "pending_approval" and changed["approval_id"] != approval_id
    assert runtime.mock_calls == []


def test_changed_deployer_or_context_requires_new_approval(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    service(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    other = deploy(high_risk, state, runtime, deployer=actor("frank"))
    assert other["state"] == "pending_approval" and other["approval_id"] != approval_id


def test_changed_policy_or_operator_config_invalidates(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    service(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    high_risk.config["approval_ttl_seconds"] = 1200  # operator config digest changes
    result = deploy(high_risk, state, runtime)
    assert result["state"] == "pending_approval" and result["approval_id"] != approval_id


def test_changed_evidence_invalidates(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    service(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    entry = high_risk.principals["agent-1"]
    entry["owners"] = [{"id": "new-owner", "active": True}]
    result = deploy(high_risk, state, runtime)
    assert result["state"] == "pending_approval"
    assert state.approval(approval_id)["state"] == "invalidated"


def test_expired_approval_rejected(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    state.db.execute("UPDATE approvals SET expires=? WHERE id=?", (past, approval_id))
    with pytest.raises(GateDenied, match="APPROVAL_EXPIRED"):
        service(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    assert state.approval(approval_id)["state"] == "expired"


def test_approved_then_expired_before_resume(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    service(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    state.db.execute("UPDATE approvals SET expires=? WHERE id=?", (past, approval_id))
    result = deploy(high_risk, state, runtime)
    assert result["state"] == "pending_approval"
    assert state.approval(approval_id)["state"] == "expired"
    assert runtime.mock_calls == []


def test_revoked_checker_authority_invalidates_on_resume(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    service(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    revoked = (
        'permit(principal, action == AgentBreeder::Action::"deploy", resource)'
        " when { principal.verified };\n"
        'permit(principal, action == AgentBreeder::Action::"RequireApproval", resource)'
        ' when { resource.risk_tier == "high" };'
    )
    # Same baseline semantics for deploy but carol may no longer approve. The policy digest
    # change also alters the revision, so either check forces a fresh request.
    enable_cedar(high_risk, baseline=revoked)
    result = deploy(high_risk, state, runtime)
    assert result["state"] == "pending_approval" and result["approval_id"] != approval_id
    assert runtime.mock_calls == []


def test_checker_revocation_detected_with_identical_revision(
    high_risk, state, runtime, monkeypatch
):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    svc = service(high_risk, state, runtime)
    svc.decide(approval_id, CAROL, approve=True)
    svc = service(high_risk, state, runtime)
    monkeypatch.setattr(svc, "_checker_still_authorized", lambda *a: False)
    result = svc.deploy(Manifest.model_validate(v2_manifest_data()), local=True, deployer=DANA)
    assert result["state"] == "pending_approval"
    assert state.approval(approval_id)["state"] == "invalidated"


def test_crash_after_consumption_recovers_same_execution_only(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    service(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    runtime.inspect.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        deploy(high_risk, state, runtime)
    record = state.active("sample")
    assert record["state"] == "starting" and record["approval_id"] == approval_id
    assert state.approval(approval_id)["state"] == "consumed"
    runtime.inspect.side_effect = None
    recovered = deploy(high_risk, state, runtime)
    assert recovered["id"] == record["id"] and recovered["state"] == "ready"
    # Recovery re-ran the gate: a now-disabled principal blocks even the same execution.
    runtime.inspect.side_effect = KeyboardInterrupt()
    high_risk.principals["agent-1"]["enabled"] = False
    runtime.inspect.side_effect = None
    with pytest.raises(GateDenied, match="PRINCIPAL_DISABLED"):
        deploy(high_risk, state, runtime)


def test_disable_blocks_approval(high_risk, state, runtime):
    approval_id = pending(high_risk, state, runtime)["approval_id"]
    svc = service(high_risk, state, runtime)
    svc.disable("x", actor("ops"), environment="dev", provider="static", ref="agent-1")
    assert state.approval(approval_id)["state"] == "invalidated"
    with pytest.raises(GateDenied, match="APPROVAL_NOT_PENDING"):
        svc.decide(approval_id, CAROL, approve=True)
