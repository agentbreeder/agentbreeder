"""Edge and fail-closed paths of the deploy lifecycle, CLI, store and gate."""

from __future__ import annotations

import itertools
import json
from unittest.mock import Mock

import pytest
from conftest import AUTHORITY, actor, enable_cedar, principal_entry, v2_manifest_data

from agentbreeder import cli, doctor
from agentbreeder.audit import AuditError
from agentbreeder.deployer import Deployer
from agentbreeder.deployment import AuditIncomplete, AuditUnavailable, DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.identity import AgentPrincipal, ProviderError
from agentbreeder.manifest import Manifest
from agentbreeder.policy import PolicyDecision, PolicyError
from agentbreeder.store import DeploymentError, Store

OPS = actor("ops", verified=False)
DANA, CAROL, ADMIN = actor("dana"), actor("carol"), actor("admin")
RUNNING = {"Id": "container-id", "State": {"Running": True}}
HIGH = {"acme/data_classification": "confidential", "acme/risk_tier": "high"}
KEY = json.dumps(["static", AUTHORITY, "agent-1"], separators=(",", ":"))


def service(env, state, runtime):
    return DeploymentService(state, runtime, env.load())


def deploy(env, state, runtime, deployer=None, **overrides):
    return service(env, state, runtime).deploy(
        Manifest.model_validate(v2_manifest_data(**overrides)), local=True, deployer=deployer
    )


def fail_audit(monkeypatch, state, match):
    """Make the audit append fail for the first event satisfying `match`."""
    original = state._append

    def failing(event):
        if event is not None and match(event):
            raise AuditError("Audit log write failed")
        return original(event)

    monkeypatch.setattr(state, "_append", failing)


def last_audit(state):
    row = state.audit_rows(limit=1)[0]
    return row["action"], row["decision"], (row["reasons"] or [{}])[0].get("code")


@pytest.fixture
def high_risk(identity_env):
    pytest.importorskip("cedarpy")
    enable_cedar(identity_env)
    identity_env.principals["agent-1"] = principal_entry(attributes=HIGH)
    return identity_env


def approved(env, state, runtime):
    approval_id = deploy(env, state, runtime, deployer=DANA)["approval_id"]
    service(env, state, runtime).decide(approval_id, CAROL, approve=True)
    return approval_id


# -- deploy: Docker verification -------------------------------------------------------------


def test_unverifiable_container_creation_fails_without_start(identity_env, state, runtime):
    runtime.inspect.return_value = None
    result = deploy(identity_env, state, runtime)
    assert result["state"] == "failed" and result["endpoint"] is None
    assert "could not be verified" in result["error"]
    runtime.create.assert_called_once()
    runtime.start.assert_not_called()
    assert last_audit(state)[:2] == ("transition", "failed")


def test_container_disappearing_before_health_check_fails(identity_env, state, runtime):
    runtime.inspect.side_effect = [None, {"Id": "id", "State": {"Running": False}}, None]
    result = deploy(identity_env, state, runtime)
    assert result["state"] == "failed"
    assert "disappeared before health verification" in result["error"]
    runtime.start.assert_called_once()
    runtime.healthy.assert_not_called()


# -- deploy: approvals -------------------------------------------------------------------------


def test_existing_record_consumes_new_approval_and_rebinds(high_risk, state, runtime):
    low = {"acme/data_classification": "confidential", "acme/risk_tier": "low"}
    high_risk.principals["agent-1"] = principal_entry(attributes=low)
    first = deploy(high_risk, state, runtime, deployer=DANA)
    assert first["state"] == "ready" and first["approval_id"] is None
    high_risk.principals["agent-1"] = principal_entry(attributes=HIGH)
    waiting = deploy(high_risk, state, runtime, deployer=DANA)
    assert waiting["state"] == "pending_approval" and waiting["id"] == first["id"]
    service(high_risk, state, runtime).decide(waiting["approval_id"], CAROL, approve=True)
    result = deploy(high_risk, state, runtime, deployer=DANA)
    assert result["state"] == "ready" and result["id"] == first["id"]
    assert result["approval_id"] == waiting["approval_id"]
    assert result["revision"] != first["revision"]
    consumed = state.approval(waiting["approval_id"])
    assert consumed["state"] == "consumed" and consumed["execution_id"] == first["id"]


def test_replayed_approval_rolls_back_and_is_audited_as_deny(
    high_risk, state, runtime, monkeypatch
):
    approval_id = approved(high_risk, state, runtime)
    monkeypatch.setattr(state, "decide_approval", lambda *a, **k: False)
    with pytest.raises(GateDenied) as error:
        deploy(high_risk, state, runtime, deployer=DANA)
    assert error.value.code == "APPROVAL_REPLAYED" and error.value.attempt_id
    assert state.active("sample") is None  # the create transaction rolled back
    runtime.create.assert_not_called()
    assert last_audit(state) == ("deploy", "deny", "APPROVAL_REPLAYED")
    assert state.approval(approval_id)["state"] == "approved"
    assert state.verify_audit()["incomplete_attempts"] == []


def test_bound_approval_not_consumed_is_never_reused(high_risk, state, runtime):
    approval_id = approved(high_risk, state, runtime)
    first = deploy(high_risk, state, runtime, deployer=DANA)
    assert first["state"] == "ready"
    state.db.execute("UPDATE approvals SET state='invalidated' WHERE id=?", (approval_id,))
    runtime.reset_mock()
    result = deploy(high_risk, state, runtime, deployer=DANA)
    assert result["state"] == "pending_approval" and result["approval_id"] != approval_id
    assert result["id"] == first["id"]
    runtime.start.assert_not_called()


def test_audit_failure_creating_approval_request_aborts(high_risk, state, runtime, monkeypatch):
    fail_audit(monkeypatch, state, lambda e: e.decision == "pending_approval")
    with pytest.raises(AuditUnavailable, match="nothing was deployed"):
        deploy(high_risk, state, runtime, deployer=DANA)
    assert state.db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert runtime.mock_calls == []


@pytest.mark.parametrize(
    "claims",
    [
        {"subject": "carol", "issuer": "https://issuer.example.com", "human": False},
        {"subject": "alice-id", "issuer": "https://issuer.example.com", "human": True},
        {"subject": "zed", "aliases": ["dana"], "human": True},
    ],
    ids=["checker-not-human", "checker-is-sole-sponsor", "checker-overlaps-deployer"],
)
def test_checker_no_longer_eligible_invalidates_on_resume(high_risk, state, runtime, claims):
    approval_id = approved(high_risk, state, runtime)
    state.db.execute(
        "UPDATE approvals SET checker_claims=? WHERE id=?", (json.dumps(claims), approval_id)
    )
    result = deploy(high_risk, state, runtime, deployer=DANA)
    assert result["state"] == "pending_approval" and result["approval_id"] != approval_id
    assert state.approval(approval_id)["state"] == "invalidated"
    rows = state.audit_rows(limit=20)
    invalidated = next(r for r in rows if r["action"] == "approval_invalidated")
    assert invalidated["reasons"][0]["code"] == "CHECKER_REVOKED"
    assert runtime.mock_calls == []


def test_checker_recheck_policy_error_invalidates(high_risk, state, runtime):
    approval_id = approved(high_risk, state, runtime)
    svc = service(high_risk, state, runtime)
    evaluate = svc.gate.evaluate

    def flaky(request, restrictions):
        if request.action == "approve":
            raise PolicyError("POLICY_UNAVAILABLE", "PDP unreachable")
        return evaluate(request, restrictions)

    svc.gate.evaluate = flaky
    result = svc.deploy(Manifest.model_validate(v2_manifest_data()), local=True, deployer=DANA)
    assert result["state"] == "pending_approval"
    assert state.approval(approval_id)["state"] == "invalidated"
    assert runtime.mock_calls == []


# -- approve / reject ------------------------------------------------------------------------


def test_decide_denies_when_principal_blocked(high_risk, state, runtime):
    approval_id = deploy(high_risk, state, runtime, deployer=DANA)["approval_id"]
    svc = service(high_risk, state, runtime)
    state.add_blocker(KEY, "dev", "admin", True, "x", svc._event("a", "disable", "blocked", ADMIN))
    with pytest.raises(GateDenied) as error:
        svc.decide(approval_id, CAROL, approve=True)
    assert error.value.code == "PRINCIPAL_BLOCKED"
    assert state.approval(approval_id)["state"] == "pending"
    assert last_audit(state) == ("approval_granted", "deny", "PRINCIPAL_BLOCKED")


def test_decide_policy_error_fails_closed(high_risk, state, runtime):
    approval_id = deploy(high_risk, state, runtime, deployer=DANA)["approval_id"]
    svc = service(high_risk, state, runtime)
    svc.gate.evaluate = Mock(side_effect=PolicyError("POLICY_UNAVAILABLE", "PDP down"))
    with pytest.raises(GateDenied) as error:
        svc.decide(approval_id, CAROL, approve=False)
    assert error.value.code == "POLICY_UNAVAILABLE"
    assert state.approval(approval_id)["state"] == "pending"
    assert last_audit(state) == ("approval_rejected", "deny", "POLICY_UNAVAILABLE")


def test_decide_concurrent_change_is_denied(high_risk, state, runtime, monkeypatch):
    approval_id = deploy(high_risk, state, runtime, deployer=DANA)["approval_id"]
    svc = service(high_risk, state, runtime)
    monkeypatch.setattr(state, "decide_approval", lambda *a, **k: False)
    with pytest.raises(GateDenied, match="changed concurrently") as error:
        svc.decide(approval_id, CAROL, approve=True)
    assert error.value.code == "APPROVAL_NOT_PENDING"
    assert state.approval(approval_id)["state"] == "pending"


# -- remove ----------------------------------------------------------------------------------


def test_remove_requires_local(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    runtime.reset_mock()
    rows = len(state.audit_rows(limit=1000))
    with pytest.raises(DeploymentError, match="--local"):
        service(identity_env, state, runtime).remove(first["id"], local=False)
    assert runtime.mock_calls == [] and len(state.audit_rows(limit=1000)) == rows
    assert state.get(first["id"])["state"] == "ready"


def test_remove_runtime_unavailable_is_aborted_and_audited(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    runtime.authorize.side_effect = DeploymentError("Docker context changed")
    with pytest.raises(DeploymentError, match="context changed") as raised:
        service(identity_env, state, runtime).remove(first["id"], local=True)
    # The error names the context the deployment was created in, so the user can restore it.
    assert repr(first["context"]) in str(raised.value)
    assert last_audit(state) == ("remove", "aborted", "RUNTIME_UNAVAILABLE")
    assert state.get(first["id"])["state"] == "ready"
    runtime.remove.assert_not_called()


def test_remove_unverified_container_removal_fails(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    result = service(identity_env, state, runtime).remove(first["id"], local=True)
    assert result["state"] == "failed"
    assert "removal could not be verified" in result["error"]
    runtime.remove.assert_called_once()


def test_remove_audit_failure_after_removal_reports_incomplete(
    identity_env, state, runtime, monkeypatch
):
    first = deploy(identity_env, state, runtime)
    runtime.inspect.side_effect = [RUNNING, None]
    fail_audit(monkeypatch, state, lambda e: e.decision == "removed")
    with pytest.raises(AuditIncomplete, match="after the container was removed"):
        service(identity_env, state, runtime).remove(first["id"], local=True)
    runtime.remove.assert_called_once()
    assert state.get(first["id"])["state"] == "removing"  # removed transition rolled back


# -- disable / enable ------------------------------------------------------------------------


def test_disable_unknown_principal_ref_is_denied(identity_env, state, runtime):
    with pytest.raises(GateDenied) as error:
        service(identity_env, state, runtime).disable(
            "-", OPS, provider="static", ref="ghost", environment="dev"
        )
    assert error.value.code == "PRINCIPAL_NOT_FOUND"
    assert state.db.execute("SELECT COUNT(*) FROM blockers").fetchone()[0] == 0


def test_enable_unknown_principal_ref_is_denied(identity_env, state, runtime):
    with pytest.raises(GateDenied) as error:
        service(identity_env, state, runtime).enable(
            "-", ADMIN, provider="static", ref="ghost", environment="dev"
        )
    assert error.value.code == "PRINCIPAL_NOT_FOUND"


def test_disable_audit_failure_persists_nothing(identity_env, state, runtime, monkeypatch):
    first = deploy(identity_env, state, runtime)
    runtime.reset_mock()
    fail_audit(monkeypatch, state, lambda e: e.action == "disable")
    with pytest.raises(AuditUnavailable, match="nothing was disabled"):
        service(identity_env, state, runtime).disable("sample", OPS)
    assert state.blocker(first["principal_key"], "dev") is None
    assert state.get(first["id"])["revision"] == first["revision"]
    runtime.remove.assert_not_called()


@pytest.mark.parametrize(
    "outcome,expected_state",
    [(None, "disabled"), (ProviderError("PROVIDER_FORBIDDEN", "no write scope"), "failed")],
)
def test_idp_disable_outcome_is_audited(identity_env, state, runtime, outcome, expected_state):
    deploy(identity_env, state, runtime)
    runtime.inspect.side_effect = [RUNNING, None]
    svc = service(identity_env, state, runtime)
    adapter = Mock(spec=["disable"])
    adapter.disable.side_effect = outcome
    svc.gate.provider_loader = lambda *a, **k: adapter
    result = svc.disable("sample", OPS, idp=True)
    adapter.disable.assert_called_once_with("agent-1")
    decision = "disabled" if outcome is None else "PROVIDER_FORBIDDEN"
    assert result["idp"] == decision and result["state"] == expected_state
    row = next(r for r in state.audit_rows(limit=20) if r["action"] == "idp_disable")
    assert row["decision"] == decision
    assert state.blocker(KEY, "dev")  # the local blocker holds either way


def test_disable_leaves_other_principals_running(identity_env, state, runtime):
    identity_env.principals["agent-2"] = principal_entry()
    first = deploy(identity_env, state, runtime)
    other_identity = {"provider": "static", "ref": "agent-2", "environment": "dev"}
    other = deploy(identity_env, state, runtime, name="other", identity=other_identity)
    runtime.reset_mock()
    runtime.inspect.side_effect = [RUNNING, None]
    result = service(identity_env, state, runtime).disable("sample", OPS)
    assert result["state"] == "disabled" and result["stopped"] == [first["id"]]
    assert state.get(other["id"])["state"] == "ready"
    runtime.remove.assert_called_once()


def test_disable_unverified_stop_is_failed_and_blocker_kept(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    result = service(identity_env, state, runtime).disable("sample", OPS)
    assert result["state"] == "failed"
    assert result["failed"][0]["id"] == first["id"]
    record = state.get(first["id"])
    assert record["state"] == "failed"
    assert record["error"].startswith("Disable incomplete: Container stop could not be verified")
    assert state.blocker(first["principal_key"], "dev")


def test_enable_rejects_non_human_actor(identity_env, state, runtime):
    deploy(identity_env, state, runtime)
    runtime.inspect.side_effect = [RUNNING, None]
    svc = service(identity_env, state, runtime)
    svc.disable("sample", OPS)
    bot = Deployer("bot", True, "https://issuer.example.com", "oidc", (), human=False)
    with pytest.raises(GateDenied) as error:
        svc.enable("sample", bot)
    assert error.value.code == "CHECKER_NOT_HUMAN"
    assert state.blocker(KEY, "dev")
    assert last_audit(state) == ("enable", "deny", "CHECKER_NOT_HUMAN")


def test_enable_audit_failure_keeps_blocker(identity_env, state, runtime, monkeypatch):
    pytest.importorskip("cedarpy")
    enable_cedar(identity_env)
    deploy(identity_env, state, runtime, deployer=DANA)
    runtime.inspect.side_effect = [RUNNING, None]
    svc = service(identity_env, state, runtime)
    svc.disable("sample", OPS)
    fail_audit(monkeypatch, state, lambda e: e.action == "enable" and e.decision == "allow")
    with pytest.raises(AuditUnavailable):
        svc.enable("sample", ADMIN)
    assert state.blocker(KEY, "dev")  # still disabled


# -- status / verify / reconcile -------------------------------------------------------------


def test_unbound_record_is_not_reverified(identity_env, state, runtime):
    state.create("legacy", {"name": "legacy"}, "digest", "local-test")
    svc = service(identity_env, state, runtime)
    assert svc.verify(state.get("legacy")) == "unbound"
    assert svc.status("legacy")["compliance"] is None


def test_unexpected_adapter_failure_is_unverifiable_not_raised(
    identity_env, state, runtime, monkeypatch
):
    # Regression: status/reconcile must not crash on an adapter bug (e.g. AttributeError).
    first = deploy(identity_env, state, runtime)
    svc = service(identity_env, state, runtime)

    def broken(*_args):
        raise AttributeError("list has no attribute get")

    monkeypatch.setattr(svc.gate, "resolve", broken)
    assert svc.verify(state.get(first["id"])) == "unverifiable:PROVIDER_ERROR"


def test_status_reports_blocker_on_a_running_record(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    svc = service(identity_env, state, runtime)
    state.add_blocker(KEY, "dev", "admin", True, "x", svc._event("a", "disable", "blocked", ADMIN))
    record = svc.status(first["id"])
    assert record["state"] == "ready"
    assert record["compliance"] == "noncompliant:PRINCIPAL_BLOCKED"


def test_status_detects_rotated_principal_id(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    identity_env.principals["agent-1"] = {**principal_entry(), "id": "rotated-id"}
    identity_env.write()
    record = service(identity_env, state, runtime).status(first["id"])
    assert record["compliance"] == "noncompliant:PRINCIPAL_CHANGED"


def test_reconcile_skips_unbound_records(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    state.create("legacy", {"name": "legacy"}, "digest", "local-test")
    runtime.reset_mock()
    results = service(identity_env, state, runtime).reconcile(OPS)
    assert results == [{"id": first["id"], "name": "sample", "compliance": "compliant"}]
    legacy = state.get("legacy")
    assert legacy["state"] == "pending" and legacy["compliance"] is None
    assert runtime.mock_calls == []
    assert last_audit(state)[:2] == ("reconcile", "allow")


# -- egress boundary -------------------------------------------------------------------------

URL = "https://mcp.example.com/mcp"
PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


class FakeBoundary:
    """Records the boundary orchestration the service requests; no Docker."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.pins: dict[str, dict] = {}
        self.calls: list[str] = []
        self.healthy = True

    def names(self, record):
        return {"sidecar": record["container"] + "-pep"}

    def enforced(self, record):
        return record["id"] in self.pins

    def pin(self, record):
        return self.pins[record["id"]]

    def write_pin(self, record, pin):
        self.calls.append("write_pin")
        self.pins[record["id"]] = {**pin, "deployment": record["id"], "token_sha256": "x"}
        return "token"

    def set_serving(self, record, serving):
        self.calls.append(f"serving={serving}")
        self.pins[record["id"]]["serving"] = serving

    def start_service(self, record):
        self.calls.append("start_service")

    def service_healthy(self, record):
        return self.healthy

    def create(self, record, manifest, token):
        self.calls.append("create")
        self.runtime.create(record, manifest)

    def start(self, record):
        self.calls.append("start")

    def verify(self, record):
        self.calls.append("verify")
        return {}

    def endpoint(self, record):
        return "http://127.0.0.1:4321"

    def teardown(self, record):
        self.calls.append("teardown")


@pytest.fixture
def boundary_env(identity_env, monkeypatch):
    pytest.importorskip("cedarpy")
    from egress_support import enable_boundary

    identity_env.principals["agent-1"] = principal_entry(
        sponsors=[
            {"id": "alice-id", "name": "alice@example.com", "active": True},
            {"id": "frank-id", "name": "frank@example.com", "active": True},
        ]
    )
    enable_boundary(identity_env, URL, PEM, monkeypatch)
    return identity_env


def boundary_service(env, state, runtime):
    svc = DeploymentService(state, runtime, env.load())
    svc.boundary = FakeBoundary(runtime)
    return svc


def boundary_deploy(svc):
    from egress_support import tool_capabilities

    manifest = Manifest.model_validate(v2_manifest_data(capabilities=tool_capabilities(URL)))
    return svc.deploy(manifest, local=True, deployer=actor("dave"))


def test_unhealthy_reused_boundary_is_rebuilt(boundary_env, state, runtime):
    svc = boundary_service(boundary_env, state, runtime)
    first = boundary_deploy(svc)
    runtime.healthy.side_effect = [False, True]
    again = boundary_deploy(svc)
    assert again["state"] == "ready" and again["id"] == first["id"]
    assert svc.boundary.calls.count("write_pin") == 2


@pytest.mark.parametrize("fault", ["service_unhealthy", "corrupt_pin"])
def test_boundary_not_reused_when_it_cannot_be_trusted(boundary_env, state, runtime, fault):
    svc = boundary_service(boundary_env, state, runtime)
    boundary_deploy(svc)
    if fault == "service_unhealthy":
        svc.boundary.healthy = False
    else:
        svc.boundary.pin = Mock(side_effect=ValueError("corrupt pin"))
    again = boundary_deploy(svc)
    assert again["state"] == "ready"
    assert svc.boundary.calls.count("write_pin") == 2
    assert svc.boundary.calls.count("start_service") == 2


def test_unverifiable_agent_container_tears_down_boundary(boundary_env, state, runtime):
    runtime.inspect.return_value = None
    svc = boundary_service(boundary_env, state, runtime)
    result = boundary_deploy(svc)
    assert result["state"] == "failed"
    assert "Agent container creation could not be verified" in result["error"]
    assert svc.boundary.calls[-1] == "teardown" and "start" not in svc.boundary.calls


def test_reconcile_restarts_sidecar_when_inspect_fails(boundary_env, state, runtime):
    svc = boundary_service(boundary_env, state, runtime)
    boundary_deploy(svc)

    def command(*args):
        if args[:2] == ("container", "inspect"):
            raise DeploymentError("engine timeout")
        return ""

    runtime.command.side_effect = command
    [item] = svc.reconcile(actor("dave"))
    assert item["compliance"] == "compliant"
    assert svc.boundary.calls[-3:] == ["serving=False", "verify", "serving=True"]
    calls = [c.args for c in runtime.command.call_args_list]
    started = [args for args in calls if args[:2] == ("container", "start")]
    assert len(started) == 1 and started[0][2].endswith("-pep")


def held_call(svc, state, result):
    from agentbreeder.egress.host import DecisionService

    pin = svc.boundary.pin(state.get(result["id"]))
    reply = DecisionService(state, pin).decide(
        {
            "server": "tools",
            "method": "tools/call",
            "tool": "refund",
            "arguments_digest": "sha256:" + "1" * 64,
            "request_id": "1",
        }
    )
    assert reply["decision"] == "held"
    return reply["held_call_id"]


def test_held_call_denied_after_principal_rotation(boundary_env, state, runtime):
    svc = boundary_service(boundary_env, state, runtime)
    result = boundary_deploy(svc)
    held = held_call(svc, state, result)
    boundary_env.principals["agent-1"]["id"] = "rotated-id"
    boundary_env.write()
    with pytest.raises(GateDenied) as error:
        svc.decide_call(held, CAROL, approve=True)
    assert error.value.code == "PRINCIPAL_CHANGED"
    assert state.held(held)["state"] == "pending"


@pytest.mark.parametrize(
    "evaluate,code",
    [
        (Mock(side_effect=PolicyError("POLICY_UNAVAILABLE", "PDP down")), "POLICY_UNAVAILABLE"),
        (
            Mock(return_value=PolicyDecision(True, ("require_approval",), None, None, (), {})),
            "OBLIGATION_UNSUPPORTED",
        ),
    ],
    ids=["policy-error", "obligation"],
)
def test_held_call_checker_policy_fails_closed(boundary_env, state, runtime, evaluate, code):
    svc = boundary_service(boundary_env, state, runtime)
    result = boundary_deploy(svc)
    held = held_call(svc, state, result)
    svc.gate.evaluate = evaluate
    with pytest.raises(GateDenied) as error:
        svc.decide_call(held, CAROL, approve=True)
    assert error.value.code == code
    assert state.held(held)["state"] == "pending"
    assert last_audit(state) == ("call_approval_granted", "deny", code)


def test_held_call_concurrent_change_is_denied(boundary_env, state, runtime, monkeypatch):
    svc = boundary_service(boundary_env, state, runtime)
    result = boundary_deploy(svc)
    held = held_call(svc, state, result)
    monkeypatch.setattr(state, "decide_held", lambda *a, **k: False)
    with pytest.raises(GateDenied, match="changed concurrently") as error:
        svc.decide_call(held, CAROL, approve=False)
    assert error.value.code == "HELD_NOT_PENDING"


# -- gate ------------------------------------------------------------------------------------


def test_adapter_returning_another_provider_is_ambiguous(identity_env, state, runtime):
    svc = service(identity_env, state, runtime)
    adapter = Mock()
    adapter.resolve.return_value = AgentPrincipal("other", AUTHORITY, "agent-1", "agent", True)
    svc.gate.provider_loader = lambda *a, **k: adapter
    with pytest.raises(GateDenied) as error:
        svc.deploy(Manifest.model_validate(v2_manifest_data()), local=True)
    assert error.value.code == "PRINCIPAL_AMBIGUOUS"
    assert runtime.mock_calls == []


def test_unexpected_provider_exception_is_a_safe_deny(identity_env, state, runtime):
    svc = service(identity_env, state, runtime)
    adapter = Mock()
    adapter.resolve.side_effect = RuntimeError("secret-token-xyz leaked")
    svc.gate.provider_loader = lambda *a, **k: adapter
    with pytest.raises(GateDenied) as error:
        svc.deploy(Manifest.model_validate(v2_manifest_data()), local=True)
    assert error.value.code == "PROVIDER_ERROR"
    assert "secret-token-xyz" not in str(error.value)
    assert "secret-token-xyz" not in json.dumps(state.audit_rows(limit=5))
    assert runtime.mock_calls == []


def test_blocker_on_resolved_key_denies_alias_reference(identity_env, state, runtime):
    identity_env.principals = {"alias-ref": {**principal_entry(), "id": "immutable-id"}}
    data = v2_manifest_data()
    data["identity"]["ref"] = "alias-ref"
    svc = service(identity_env, state, runtime)
    key = json.dumps(["static", AUTHORITY, "immutable-id"], separators=(",", ":"))
    state.add_blocker(key, "dev", "admin", True, "x", svc._event("a", "disable", "blocked", ADMIN))
    assert not state.blocked_reference("static", "alias-ref", "dev")
    with pytest.raises(GateDenied) as error:
        svc.deploy(Manifest.model_validate(data), local=True)
    assert error.value.code == "PRINCIPAL_BLOCKED"
    assert runtime.mock_calls == []


def test_manifest_without_requires_still_checks_environment(identity_env, state, runtime):
    assert deploy(identity_env, state, runtime, requires=None)["state"] == "ready"
    identity_env.principals["agent-1"] = principal_entry(environment="prod")
    with pytest.raises(GateDenied) as error:
        deploy(identity_env, state, runtime, requires=None)
    assert error.value.code == "ENVIRONMENT_MISMATCH"


def test_audit_mode_keeps_first_deny_when_policy_also_denies(identity_env, state, runtime):
    pytest.importorskip("cedarpy")
    forbid = 'forbid(principal, action == AgentBreeder::Action::"deploy", resource);'
    enable_cedar(identity_env, restrictions={"no-deploy": forbid})
    identity_env.config["enforcement"] = "audit"
    identity_env.principals["agent-1"] = principal_entry(review_by="2000-01-01")
    result = deploy(
        identity_env, state, runtime, deployer=DANA, policy={"restrictions": ["no-deploy"]}
    )
    assert result["state"] == "ready"
    assert any("[REVIEW_EXPIRED] was not enforced" in w for w in result["gate"]["warnings"])
    rows = state.audit_rows(limit=50)
    override = next(r for r in rows if r["action"] == "audit_mode_proceed")
    assert override["reasons"][0]["code"] == "REVIEW_EXPIRED"


# -- store -----------------------------------------------------------------------------------


def test_symlinked_state_directory_is_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(DeploymentError, match="owned by the current user"):
        Store(link)


@pytest.mark.parametrize(
    "lookup,match",
    [
        ("manifest", "Deployment not found"),
        ("approval", "Approval request not found"),
        ("held", "Held call not found"),
    ],
)
def test_unknown_identifiers_raise_safe_errors(state, lookup, match):
    with pytest.raises(DeploymentError, match=match):
        getattr(state, lookup)("missing")


def test_decide_approval_compare_and_set_writes_nothing_on_mismatch(high_risk, state, runtime):
    approval_id = deploy(high_risk, state, runtime, deployer=DANA)["approval_id"]
    svc = service(high_risk, state, runtime)
    rows = len(state.audit_rows(limit=1000))
    changed = state.decide_approval(
        approval_id,
        expected=("approved",),
        state="consumed",
        audit=svc._event("a", "approval_consumed", "consumed", DANA),
    )
    assert changed is False
    assert state.approval(approval_id)["state"] == "pending"
    assert len(state.audit_rows(limit=1000)) == rows


def test_blocked_reference_matches_only_the_blocked_principal(state):
    key = json.dumps(["static", AUTHORITY, "agent-9"], separators=(",", ":"))
    other = json.dumps(["static", AUTHORITY, "agent-8"], separators=(",", ":"))
    identity = {"provider": "static", "ref": "nickname", "environment": "dev"}
    state.create(
        "d1", {"name": "a", "identity": identity}, "d", "ctx", principal_key=key, environment="dev"
    )
    state.create(
        "d2",
        {"name": "b", "identity": {**identity, "ref": "other-nick"}},
        "d",
        "ctx",
        principal_key=other,
        environment="dev",
    )
    from agentbreeder.audit import AuditEvent

    for blocked in (other, key):
        event = AuditEvent("a", "disable", "blocked", "os:ops", False, "enforce")
        state.add_blocker(blocked, "dev", "ops", False, "x", event)
    assert state.blocked_reference("static", "nickname", "dev")
    assert state.blocked_reference("static", "AGENT-9", "dev")
    assert not state.blocked_reference("static", "unrelated", "dev")
    assert not state.blocked_reference("okta", "nickname", "dev")
    assert not state.blocked_reference("static", "nickname", "prod")


# -- CLI -------------------------------------------------------------------------------------


def test_cli_validate_success_plain_and_json(tmp_path, capsys):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(json.dumps(v2_manifest_data()))
    assert cli.main(["validate", str(manifest)]) == 0
    assert "Valid manifest: sample (schema 2). Nothing deployed." in capsys.readouterr().out
    assert cli.main(["validate", str(manifest), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "sample"


def test_cli_doctor_plain_output(monkeypatch, capsys):
    monkeypatch.setattr(
        "agentbreeder.commands.doctor.check_docker",
        lambda: [doctor.Check("Docker", True, "1.0"), doctor.Check("Engine", False, "down")],
    )
    monkeypatch.setattr(
        "agentbreeder.commands.doctor.check_compose",
        lambda: [doctor.Check("Compose", False, "old", required=False)],
    )
    assert cli.main(["doctor"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "PASS Docker: 1.0",
        "FAIL Engine: down",
        "WARN Compose: old",
    ]


def test_cli_login_success(monkeypatch, capsys):
    seen = {}

    def login(operator, issuer=None):
        seen["issuer"] = issuer
        return CAROL

    monkeypatch.setattr("agentbreeder.commands.login.device_login", login)
    assert cli.main(["login", "--issuer", "https://issuer.example.com"]) == 0
    assert capsys.readouterr().out.strip() == f"Signed in as {CAROL.label}"
    assert seen["issuer"] == "https://issuer.example.com"


@pytest.fixture
def cli_docker(monkeypatch, tmp_path):
    fake = Mock(context="local-test")
    fake.endpoint.return_value = "http://127.0.0.1:1"
    fake.healthy.return_value = True
    monkeypatch.setattr(cli, "Docker", lambda: fake)
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(json.dumps(v2_manifest_data()))
    return fake, manifest, ["--state-dir", str(tmp_path / "state")]


def test_cli_reconcile_json_remove_and_audit_verify_json(cli_docker, capsys):
    fake, manifest, state = cli_docker
    fake.inspect.side_effect = [None, RUNNING, RUNNING]
    assert cli.main(["deploy", str(manifest), "--local", *state, "--json"]) == 0
    identifier = json.loads(capsys.readouterr().out)["id"]
    assert cli.main(["reconcile", "--local", *state, "--json"]) == 0
    [item] = json.loads(capsys.readouterr().out)
    assert item == {"id": identifier, "name": "sample", "compliance": "compliant"}
    fake.inspect.side_effect = [RUNNING, None]
    assert cli.main(["remove", identifier, "--local", *state]) == 0
    assert f"{identifier}  removed" in capsys.readouterr().out
    assert cli.main(["audit", "verify", *state, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True and report["incomplete_attempts"] == []


def test_cli_list_empty_then_plain_and_json(cli_docker, capsys):
    fake, manifest, state = cli_docker
    assert cli.main(["list", *state]) == 0
    assert capsys.readouterr().out == "No deployments\n"
    assert cli.main(["list", *state, "--json"]) == 0
    assert capsys.readouterr().out == "[]\n"
    fake.inspect.side_effect = itertools.chain([None], itertools.repeat(RUNNING))
    assert cli.main(["deploy", str(manifest), "--local", *state, "--json"]) == 0
    identifier = json.loads(capsys.readouterr().out)["id"]
    assert cli.main(["list", *state]) == 0
    assert capsys.readouterr().out == f"{identifier}  sample  local  ready\n"
    assert cli.main(["list", *state, "--json"]) == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["id"] == identifier and row["target"] == "local" and row["state"] == "ready"


def test_cli_forward_prints_the_loopback_url_until_interrupted(monkeypatch, capsys):
    from contextlib import contextmanager

    from agentbreeder.commands import forward

    service = Mock()
    service.store.get.return_value = {"state": "ready", "context": "ctx", "endpoint": "svc"}
    service.store.manifest.return_value = {"target": "kubernetes"}

    @contextmanager
    def tunnel(endpoint, port):
        assert (endpoint, port) == ("svc", 0)
        yield 4321

    service.kubernetes.forward = tunnel
    # A stub module, not the process-wide time.sleep, so no other thread sees the interrupt.
    monkeypatch.setattr(forward, "time", Mock(sleep=Mock(side_effect=KeyboardInterrupt)))
    assert forward._forward(service, Mock(id="abc", port=0)) == 0
    service.kubernetes.authorize.assert_called_once_with("ctx")
    assert capsys.readouterr().out.startswith("http://127.0.0.1:4321  (unauthenticated")


def test_cli_unusable_state_dir_exits_2(tmp_path, capsys):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    assert cli.main(["status", "abc", "--state-dir", str(blocker)]) == 2
    assert "No AgentBreeder state" in capsys.readouterr().err
    # A command that may create state still reports the unusable directory.
    assert cli.main(["disable", "abc", "--local", "--state-dir", str(blocker)]) == 2
    assert "Cannot access local state" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [["audit", "verify"], ["audit", "list"], ["status", "abc"], ["reconcile", "--local"]],
)
def test_cli_mistyped_state_dir_is_an_error_not_an_empty_success(tmp_path, capsys, argv):
    # Regression: `audit verify --state-dir /typo` printed "OK: 0 rows" and created the directory.
    typo = tmp_path / "typo"
    assert cli.main([*argv, "--state-dir", str(typo)]) == 2
    assert "No AgentBreeder state" in capsys.readouterr().err
    assert not typo.exists()
