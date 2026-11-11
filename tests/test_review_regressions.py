"""Regression tests for the Codex review findings on the identity gate (epic #701)."""

from __future__ import annotations

import json

import pytest
from conftest import actor, enable_cedar, principal_entry, v2_manifest_data, write_private

from agentbreeder import cli
from agentbreeder.audit import AuditError, AuditEvent
from agentbreeder.commands import reconcile
from agentbreeder.deployment import AuditIncomplete, DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.identity import ProviderError
from agentbreeder.manifest import Manifest
from agentbreeder.store import DeploymentError

pytest.importorskip("cedarpy")
DANA, CAROL, ADMIN = actor("dana"), actor("carol"), actor("admin")
HIGH = {"acme/data_classification": "confidential", "acme/risk_tier": "high"}


def svc(env, state, runtime):
    return DeploymentService(state, runtime, env.load())


def deploy(env, state, runtime, deployer=DANA, environment=None, **overrides):
    return svc(env, state, runtime).deploy(
        Manifest.model_validate(v2_manifest_data(**overrides)),
        local=True,
        deployer=deployer,
        environment=environment,
    )


@pytest.fixture
def high_risk(identity_env):
    enable_cedar(identity_env)
    identity_env.principals["agent-1"] = principal_entry(attributes=HIGH)
    return identity_env


def approve_and_deploy(env, state, runtime):
    approval_id = deploy(env, state, runtime)["approval_id"]
    svc(env, state, runtime).decide(approval_id, CAROL, approve=True)
    return deploy(env, state, runtime), approval_id


def test_disable_enable_does_not_revive_consumed_approval(high_risk, state, runtime):
    first, approval_id = approve_and_deploy(high_risk, state, runtime)
    runtime.remove.side_effect = DeploymentError("engine timeout")
    assert svc(high_risk, state, runtime).disable("sample", actor("ops")).get("state") == "failed"
    runtime.remove.side_effect = None
    svc(high_risk, state, runtime).enable(first["id"], ADMIN)
    runtime.reset_mock()
    result = deploy(high_risk, state, runtime)
    assert result["state"] == "pending_approval" and result["approval_id"] != approval_id
    runtime.start.assert_not_called()


def test_audit_mode_identity_override_cannot_skip_approval(high_risk, state, runtime):
    high_risk.config["enforcement"] = "audit"
    data = v2_manifest_data()
    data["requires"]["attributes"]["acme/never"] = "granted"
    result = svc(high_risk, state, runtime).deploy(
        Manifest.model_validate(data), local=True, deployer=DANA
    )
    assert result["state"] == "pending_approval"
    assert runtime.mock_calls == []


def test_audit_mode_unresolved_principal_with_pdp_is_enforced(high_risk, state, runtime):
    high_risk.config["enforcement"] = "audit"
    high_risk.principals = {}
    with pytest.raises(GateDenied, match="PRINCIPAL_NOT_FOUND"):
        deploy(high_risk, state, runtime)
    assert runtime.mock_calls == []


def test_unresolved_audit_deployment_is_disabled_by_reference(identity_env, state, runtime):
    identity_env.config["enforcement"] = "audit"
    service = svc(identity_env, state, runtime)

    class Down:
        name = "static"

        def resolve(self, ref):
            raise ProviderError("PROVIDER_TIMEOUT", "timed out")

    service.gate.provider_loader = lambda *a, **k: Down()
    record = service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)
    assert record["state"] == "ready" and record["principal_key"] is None
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    result = svc(identity_env, state, runtime).disable(
        "-", actor("ops"), provider="static", ref="agent-1", environment="dev"
    )
    assert result["stopped"] == [record["id"]]


def test_redeploy_backfills_principal_binding(identity_env, state, runtime):
    identity_env.config["enforcement"] = "audit"
    identity_env.principals["agent-1"]["enabled"] = False
    first = deploy(identity_env, state, runtime, deployer=actor("dana", verified=False))
    identity_env.principals["agent-1"]["enabled"] = True
    second = deploy(identity_env, state, runtime, deployer=actor("dana", verified=False))
    assert second["id"] == first["id"] and second["principal_key"]


def test_sole_sponsor_rechecked_on_resume(high_risk, state, runtime):
    high_risk.principals["agent-1"]["sponsors"] = [
        {"id": "alice-id", "active": True},
        {"id": "carol", "active": True},
    ]
    approval_id = deploy(high_risk, state, runtime)["approval_id"]
    svc(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)
    high_risk.principals["agent-1"]["sponsors"] = [
        {"id": "alice-id", "active": False},
        {"id": "carol", "active": True},
    ]
    result = deploy(high_risk, state, runtime)
    assert result["state"] == "pending_approval"
    assert state.approval(approval_id)["state"] == "invalidated"


class Obliging:
    def __init__(self, obligations):
        self.obligations = obligations

    def evaluate(self, request):
        from agentbreeder.policy import SourceResult

        return SourceResult(True, self.obligations, "revision:r")


def test_obligations_on_approve_and_enable_fail_closed(high_risk, state, runtime):
    approval_id = deploy(high_risk, state, runtime)["approval_id"]
    service = svc(high_risk, state, runtime)
    from agentbreeder.policy import PolicyEngine

    service.gate.policy_factory = lambda pdp, r, base: PolicyEngine(
        pdp, r, base, factory=lambda *a: Obliging(("require_approval",))
    )
    with pytest.raises(GateDenied, match="OBLIGATION_UNSUPPORTED"):
        service.decide(approval_id, CAROL, approve=True)
    service.disable("-", actor("ops"), provider="static", ref="agent-1", environment="dev")
    with pytest.raises(GateDenied, match="OBLIGATION_UNSUPPORTED"):
        service.enable("-", ADMIN, provider="static", ref="agent-1", environment="dev")


def test_missing_risk_attribute_denies_when_maker_checker_configured(high_risk, state, runtime):
    high_risk.config["maker_checker"] = {"risk_tiers": ["high"]}
    high_risk.principals["agent-1"] = principal_entry(
        attributes={"acme/data_classification": "confidential"}
    )
    with pytest.raises(GateDenied, match="RISK_UNVERIFIABLE"):
        deploy(high_risk, state, runtime)


def test_workload_identity_cannot_approve_or_enable(high_risk, state, runtime):
    approval_id = deploy(high_risk, state, runtime)["approval_id"]
    ci = actor("carol")
    ci = type(ci)(ci.subject, True, ci.issuer, "oidc", (), human=False)
    with pytest.raises(GateDenied, match="CHECKER_NOT_HUMAN"):
        svc(high_risk, state, runtime).decide(approval_id, ci, approve=True)


def test_self_approval_detected_across_subjects(high_risk, state, runtime):
    deployer = actor("dana", email="dana@example.com")
    approval_id = deploy(high_risk, state, runtime, deployer=deployer)["approval_id"]
    same_person = actor("dana@example.com")
    with pytest.raises(GateDenied, match="SELF_APPROVAL"):
        svc(high_risk, state, runtime).decide(approval_id, same_person, approve=True)


def test_remove_is_audited(identity_env, state, runtime):
    record = deploy(identity_env, state, runtime, deployer=actor("dana", verified=False))
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    svc(identity_env, state, runtime).remove(record["id"], local=True)
    actions = [(r["action"], r["decision"]) for r in state.audit_rows(limit=5)]
    assert ("transition", "removed") in actions and ("remove", "allow") in actions
    assert state.verify_audit()["incomplete_attempts"] == []


def test_interrupted_execution_reported_incomplete(identity_env, state, runtime):
    runtime.inspect.side_effect = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        deploy(identity_env, state, runtime, deployer=actor("dana", verified=False))
    report = state.verify_audit()
    assert report["ok"] and len(report["incomplete_attempts"]) == 1


def test_audit_failure_after_start_reports_running_container(
    identity_env, state, runtime, monkeypatch
):
    service = svc(identity_env, state, runtime)
    original = state._append

    def failing(event):
        if event is not None and event.action == "transition" and event.decision == "ready":
            raise AuditError("Audit log write failed")
        return original(event)

    monkeypatch.setattr(state, "_append", failing)
    with pytest.raises(AuditIncomplete, match="reconcile"):
        service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)


def test_restrictions_without_pdp_deny(identity_env, state, runtime):
    with pytest.raises(GateDenied, match="POLICY_UNAVAILABLE"):
        deploy(
            identity_env,
            state,
            runtime,
            deployer=actor("dana", verified=False),
            policy={"restrictions": ["pci"]},
        )


def test_reconcile_exit_code_on_cleanup_failure(identity_env, tmp_path, monkeypatch, capsys):
    from unittest.mock import Mock

    fake = Mock(context="local-test")
    running = {"Id": "c", "State": {"Running": True}}
    fake.inspect.return_value = running
    fake.endpoint.return_value = "http://127.0.0.1:1"
    fake.healthy.return_value = True
    fake.remove.side_effect = DeploymentError("engine timeout")
    monkeypatch.setattr(cli, "Docker", lambda: fake)
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(json.dumps(v2_manifest_data()))
    state = ["--state-dir", str(tmp_path / "state")]
    assert cli.main(["deploy", str(manifest), "--local", *state]) == 0
    assert cli.main(["disable", "sample", "--local", *state]) == 1
    assert cli.main(["reconcile", "--local", *state]) == 1
    assert ":cleanup_failed" in capsys.readouterr().out


def test_remote_pdp_token_relative_to_operator_dir(identity_env, tmp_path, monkeypatch):
    from agentbreeder.operator_config import PolicySource
    from agentbreeder.policy.remote import AuthZenEvaluator

    write_private(identity_env.directory / "pdp-token", "operator-token")
    monkeypatch.chdir(tmp_path)
    write_private(tmp_path / "pdp-token", "author-token")
    evaluator = AuthZenEvaluator(
        PolicySource(url="https://pdp.example.com", revision="r", token="file:pdp-token"),
        base=identity_env.directory,
    )
    assert evaluator.headers["Authorization"] == "Bearer operator-token"


def test_keycloak_service_account_sponsor_not_human(monkeypatch):
    from test_adapters import FakeTransport, keycloak

    monkeypatch.setenv("IDP_SECRET", "s")
    transport = FakeTransport("keycloak")
    transport.routes["GET https://kc.example.com/admin/realms/acme/users/alice-user"][1] = {
        "id": "alice-user",
        "username": "service-account-other",
        "enabled": True,
    }
    principal = keycloak(transport).resolve("client-uuid-1")
    assert not any(p.active for p in principal.sponsors)


def test_sponsor_change_changes_keycloak_evidence(monkeypatch):
    from test_adapters import FakeTransport, keycloak

    monkeypatch.setenv("IDP_SECRET", "s")
    before = keycloak(FakeTransport("keycloak")).resolve("client-uuid-1").evidence_digest
    transport = FakeTransport("keycloak")
    transport.routes["GET https://kc.example.com/admin/realms/acme/users/alice-user"][1] = {
        "id": "alice-user",
        "username": "alice",
        "enabled": False,
    }
    assert keycloak(transport).resolve("client-uuid-1").evidence_digest != before


def test_audit_event_fields_roundtrip(state):
    state.audit(AuditEvent("a", "attempt_started", "started", "os:me", False, "enforce"))
    assert state.audit_rows(limit=1)[0]["actor_verified"] is False


# -- second Codex pass ----------------------------------------------------------------------


def test_audit_mode_policy_deny_keeps_approval(high_risk, state, runtime):
    forbid = 'forbid(principal, action == AgentBreeder::Action::"deploy", resource);'
    enable_cedar(high_risk, restrictions={"no-deploy": forbid})
    high_risk.config["enforcement"] = "audit"
    result = deploy(high_risk, state, runtime, policy={"restrictions": ["no-deploy"]})
    assert result["state"] == "pending_approval"
    assert runtime.mock_calls == []


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_risk_tier_is_unverifiable(high_risk, state, runtime, blank):
    high_risk.config["maker_checker"] = {"risk_tiers": ["high"]}
    high_risk.principals["agent-1"] = principal_entry(
        attributes={"acme/data_classification": "confidential", "acme/risk_tier": blank}
    )
    with pytest.raises(GateDenied, match="RISK_UNVERIFIABLE"):
        deploy(high_risk, state, runtime)


def test_reconcile_retries_unbound_audit_mode_cleanup(identity_env, state, runtime):
    identity_env.config["enforcement"] = "audit"
    service = svc(identity_env, state, runtime)

    class Down:
        name = "static"

        def resolve(self, ref):
            raise ProviderError("PROVIDER_TIMEOUT", "timed out")

    service.gate.provider_loader = lambda *a, **k: Down()
    record = service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)
    runtime.remove.side_effect = DeploymentError("engine timeout")
    result = svc(identity_env, state, runtime).disable(
        "-", actor("ops"), provider="static", ref="agent-1", environment="dev"
    )
    assert result["state"] == "failed"
    runtime.remove.side_effect = None
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    results = svc(identity_env, state, runtime).reconcile(actor("ops"))
    assert results[0]["id"] == record["id"]
    assert results[0]["compliance"] == "noncompliant:PRINCIPAL_BLOCKED"
    assert state.get(record["id"])["state"] == "disabled"


def test_duplicate_sponsor_entries_count_once(high_risk, state, runtime):
    high_risk.principals["agent-1"]["sponsors"] = [
        {"id": "carol", "active": True},
        {"id": "CAROL", "active": True},
    ]
    approval_id = deploy(high_risk, state, runtime)["approval_id"]
    with pytest.raises(GateDenied, match="SOLE_SPONSOR_APPROVAL"):
        svc(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)


def test_approve_policy_sees_image_digest(high_risk, state, runtime):
    policy = (
        'permit(principal, action == AgentBreeder::Action::"deploy", resource);\n'
        'permit(principal, action == AgentBreeder::Action::"RequireApproval", resource);\n'
        'permit(principal, action == AgentBreeder::Action::"approve", resource)'
        f' when {{ resource.image_digest == "sha256:{"a" * 64}" }};'
    )
    enable_cedar(high_risk, baseline=policy)
    approval_id = deploy(high_risk, state, runtime)["approval_id"]
    assert svc(high_risk, state, runtime).decide(approval_id, CAROL, approve=True)["state"] == (
        "approved"
    )
    assert deploy(high_risk, state, runtime)["state"] == "ready"


def test_consumed_approval_not_reused_for_ready_after_expiry(high_risk, state, runtime):
    from datetime import UTC, datetime, timedelta

    first, approval_id = approve_and_deploy(high_risk, state, runtime)
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    state.db.execute("UPDATE approvals SET expires=? WHERE id=?", (past, approval_id))
    runtime.reset_mock()
    result = deploy(high_risk, state, runtime)
    assert result["state"] == "pending_approval" and result["id"] == first["id"]
    runtime.start.assert_not_called()


def test_disable_clears_bindings_atomically_with_blocker(high_risk, state, runtime, monkeypatch):
    first, _ = approve_and_deploy(high_risk, state, runtime)
    service = svc(high_risk, state, runtime)
    monkeypatch.setattr(
        service, "_stop_blocked", lambda *a, **k: (_ for _ in ()).throw(SystemExit)
    )
    with pytest.raises(SystemExit):  # crash right after the blocker transaction
        service.disable("sample", actor("ops"))
    record = state.get(first["id"])
    assert record["approval_id"] is None and record["revision"] is None
    assert state.blocker(record["principal_key"], "dev")


def test_runtime_authorize_failure_is_recorded_as_aborted(identity_env, state, runtime):
    runtime.authorize.side_effect = DeploymentError("Cannot verify a local Docker context")
    with pytest.raises(DeploymentError):
        deploy(identity_env, state, runtime, deployer=actor("dana", verified=False))
    assert state.audit_rows(limit=1)[0]["decision"] == "aborted"
    assert state.verify_audit()["incomplete_attempts"] == []


def test_remove_audit_failure_before_docker_says_nothing_removed(
    identity_env, state, runtime, monkeypatch
):
    from agentbreeder.deployment import AuditUnavailable

    record = deploy(identity_env, state, runtime, deployer=actor("dana", verified=False))
    original = state._append

    def failing(event):
        if event is not None and event.decision == "removing":
            raise AuditError("Audit log write failed")
        return original(event)

    monkeypatch.setattr(state, "_append", failing)
    runtime.reset_mock()
    with pytest.raises(AuditUnavailable, match="nothing was removed"):
        svc(identity_env, state, runtime).remove(record["id"], local=True)
    runtime.remove.assert_not_called()


@pytest.mark.parametrize(
    "compliance,code",
    [
        ([], 0),
        (["compliant", "unbound", None], 0),
        (["compliant", "noncompliant:REVIEW_EXPIRED"], 4),
        (["unverifiable:PRINCIPAL_NOT_FOUND"], 4),
        (["noncompliant:REVIEW_EXPIRED", "noncompliant:PRINCIPAL_BLOCKED:cleanup_failed"], 1),
        (["compliant", "gateway_unreachable:GATEWAY_UNREACHABLE"], 1),
        (["gateway_credential_error"], 1),
    ],
)
def test_reconcile_exit_code_precedence(compliance, code):
    # Regression: reconcile used to exit 0 for noncompliant/unverifiable deployments.
    results = [{"id": str(i), "name": "n", "compliance": c} for i, c in enumerate(compliance)]
    assert reconcile._reconcile_exit_code(results) == code
