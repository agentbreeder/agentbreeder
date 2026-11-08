"""P0 (#702): deploy gate checks, environment context, scoped grants and attempt audit."""

from __future__ import annotations

import sqlite3

import pytest
from conftest import AUTHORITY, actor, principal_entry, v2_manifest_data

from agentbreeder.audit import AuditError
from agentbreeder.deployment import AuditUnavailable, DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.identity import ProviderError
from agentbreeder.manifest import Manifest


def deploy(identity_env, state, runtime, data=None, *, environment=None, deployer=None):
    service = DeploymentService(state, runtime, identity_env.load())
    manifest = Manifest.model_validate(data or v2_manifest_data())
    return service.deploy(manifest, local=True, environment=environment, deployer=deployer)


def audit_actions(state):
    return [(r["action"], r["decision"]) for r in reversed(state.audit_rows(limit=1000))]


def test_allowed_deploy_records_identity_and_partial_guarantee(identity_env, state, runtime):
    result = deploy(identity_env, state, runtime)
    assert result["state"] == "ready"
    assert result["environment"] == "dev"
    assert result["principal"]["id"] == "agent-1"
    gate = result["gate"]
    assert gate["actor_verified"] is False
    assert gate["checks_unavailable"] == ["policy", "approval", "deployer_verification"]
    assert any("Identity preflight only" in w for w in gate["warnings"])
    actions = audit_actions(state)
    assert actions[0] == ("attempt_started", "started")
    assert ("deploy", "allow") in actions
    assert ("deployment_created", "allow") in actions
    assert actions[-1] == ("transition", "ready")
    allow = next(r for r in state.audit_rows(limit=100) if r["decision"] == "allow")
    assert allow["actor_verified"] is False
    assert allow["checks_unavailable"] == ["policy", "approval", "deployer_verification"]
    assert state.verify_audit()["ok"]


@pytest.mark.parametrize(
    "entry,code",
    [
        (None, "PRINCIPAL_NOT_FOUND"),
        (principal_entry(kind="human"), "PRINCIPAL_KIND_REJECTED"),
        (principal_entry(kind="shared"), "PRINCIPAL_KIND_REJECTED"),
        (principal_entry(enabled=False), "PRINCIPAL_DISABLED"),
        (principal_entry(sponsors=[]), "SPONSOR_MISSING"),
        (
            principal_entry(sponsors=[{"id": "s", "active": False}]),
            "SPONSOR_MISSING",
        ),
        (principal_entry(review_by="2000-01-01"), "REVIEW_EXPIRED"),
        (principal_entry(roles=[]), "ROLE_NOT_GRANTED"),
        (principal_entry(groups=[]), "GROUP_NOT_GRANTED"),
        (
            principal_entry(attributes={"acme/data_classification": "public"}),
            "ATTRIBUTE_MISMATCH",
        ),
        (principal_entry(environment="prod"), "ENVIRONMENT_MISMATCH"),
    ],
)
def test_each_check_denies_with_distinct_code_and_no_docker(
    identity_env, state, runtime, entry, code
):
    if entry is None:
        identity_env.principals = {}
    else:
        identity_env.principals["agent-1"] = entry
    with pytest.raises(GateDenied) as error:
        deploy(identity_env, state, runtime)
    assert error.value.code == code
    assert error.value.attempt_id
    assert runtime.mock_calls == []
    assert state.active("sample") is None
    rows = state.audit_rows(attempt_id=error.value.attempt_id)
    assert [r["action"] for r in rows] == ["attempt_started", "deploy"]
    assert rows[-1]["decision"] == "deny"
    assert rows[-1]["reasons"][0]["code"] == code
    assert rows[-1]["deployment_id"] is None


def test_expect_owner_mismatch_and_match(identity_env, state, runtime):
    data = v2_manifest_data()
    data["identity"]["expect_owner"] = "mallory@example.com"
    with pytest.raises(GateDenied, match="OWNER_MISMATCH"):
        deploy(identity_env, state, runtime, data)
    data["identity"]["expect_owner"] = "bob@example.com"
    assert deploy(identity_env, state, runtime, data)["state"] == "ready"


def test_missing_secret_file_denied_before_runtime(identity_env, state, runtime):
    with pytest.raises(GateDenied, match="SECRET_MISSING"):
        deploy(identity_env, state, runtime, v2_manifest_data(secrets={"KEY": "secret://k"}))
    assert runtime.mock_calls == []
    assert ("deploy", "deny") in audit_actions(state)


def test_secret_file_mounted_and_value_never_recorded(identity_env, state, runtime):
    from agentbreeder import secret_files

    secret_files.set_secret(state.directory, "k", b"hunter2-value")
    info = {"Id": "container-id", "State": {"Running": True}}
    runtime.inspect.side_effect = [None, info, info, info]
    result = deploy(identity_env, state, runtime, v2_manifest_data(secrets={"KEY": "secret://k"}))
    assert result["state"] == "ready"
    mounts = runtime.create.call_args.kwargs["mounts"]
    assert [(m.envs, m.name, m.target) for m in mounts] == [(("KEY",), "k", "/run/secrets/k")]
    assert mounts[0].source.read_bytes() == b"hunter2-value"
    assert oct(mounts[0].source.stat().st_mode & 0o777) == "0o444"
    rows = state.audit_rows(limit=1000)
    assert "hunter2-value" not in repr(rows)


def test_v1_rejected_under_enforce_and_warned_under_audit(identity_env, state, runtime):
    v1 = {"schema_version": 1, "name": "legacy", "image": "sha256:" + "a" * 64}
    with pytest.raises(GateDenied, match="IDENTITY_REQUIRED"):
        deploy(identity_env, state, runtime, v1)
    assert runtime.mock_calls == []
    identity_env.config["enforcement"] = "audit"
    result = deploy(identity_env, state, runtime, v1)
    assert result["state"] == "ready"
    assert any("AUDIT MODE" in w and "IDENTITY_REQUIRED" in w for w in result["gate"]["warnings"])
    actions = audit_actions(state)
    assert ("deploy", "deny") in actions and ("audit_mode_proceed", "override") in actions
    # An overridden attempt never records an evaluated allow.
    override_attempt = state.audit_rows(limit=1000)[0]["attempt_id"]
    rows = state.audit_rows(attempt_id=override_attempt)
    assert not any(r["action"] == "deploy" and r["decision"] == "allow" for r in rows)


def test_prod_context_with_operator_allowed_local_target(identity_env, state, runtime):
    identity_env.principals["agent-1"] = principal_entry(environment="prod")
    data = v2_manifest_data()
    data["identity"]["environment"] = "prod"
    result = deploy(identity_env, state, runtime, data, environment="prod")
    assert result["state"] == "ready" and result["environment"] == "prod"


@pytest.mark.parametrize(
    "environments,selected,code",
    [
        ({"dev": {"targets": ["local"]}}, "staging", "CONTEXT_ENVIRONMENT_UNKNOWN"),
        ({"dev": {"targets": ["kubernetes"]}}, "dev", "CONTEXT_TARGET_NOT_ALLOWED"),
        ({}, None, "CONTEXT_ENVIRONMENT_UNKNOWN"),
    ],
)
def test_unknown_environment_and_invalid_pairing_deny(
    identity_env, state, runtime, environments, selected, code
):
    identity_env.config["environments"] = environments
    if not environments:
        identity_env.config.pop("default_environment")
    with pytest.raises(GateDenied) as error:
        deploy(identity_env, state, runtime, environment=selected)
    assert error.value.code == code
    assert runtime.mock_calls == []
    rows = state.audit_rows(attempt_id=error.value.attempt_id)
    assert rows[-1]["decision"] == "deny"


def test_manifest_environment_cannot_override_context(identity_env, state, runtime):
    data = v2_manifest_data()
    data["identity"]["environment"] = "prod"
    with pytest.raises(GateDenied, match="ENVIRONMENT_MISMATCH"):
        deploy(identity_env, state, runtime, data, environment="dev")


@pytest.mark.parametrize(
    "role,group",
    [
        ({"authority": "tenant-b", "resource": "payments-api", "role_id": "refunds-role"}, None),
        ({"authority": AUTHORITY, "resource": "crm-api", "role_id": "refunds-role"}, None),
        (None, {"authority": "tenant-b", "group_id": "finance-group"}),
    ],
)
def test_same_name_grants_in_other_scope_do_not_satisfy(identity_env, state, runtime, role, group):
    data = v2_manifest_data()
    if role:
        data["requires"]["roles"] = [role]
    if group:
        data["requires"]["groups"] = [group]
    with pytest.raises(GateDenied) as error:
        deploy(identity_env, state, runtime, data)
    assert error.value.code in {"ROLE_NOT_GRANTED", "GROUP_NOT_GRANTED"}


def test_display_names_never_satisfy_ids(identity_env, state, runtime):
    data = v2_manifest_data()
    data["requires"]["roles"][0]["role_id"] = "Refunds"  # the display name
    with pytest.raises(GateDenied, match="ROLE_NOT_GRANTED"):
        deploy(identity_env, state, runtime, data)


def test_attribute_exact_namespaced_value(identity_env, state, runtime):
    identity_env.principals["agent-1"] = principal_entry(
        attributes={"other/data_classification": "confidential"}
    )
    with pytest.raises(GateDenied, match="ATTRIBUTE_MISMATCH"):
        deploy(identity_env, state, runtime)


def test_attempt_start_durable_before_provider_call(identity_env, state, runtime):
    seen = []

    class Recorder:
        name = "static"

        def resolve(self, ref):
            seen.append([r["action"] for r in state.audit_rows(limit=10)])
            raise ProviderError("PROVIDER_TIMEOUT", "Identity provider timed out")

    service = DeploymentService(state, runtime, identity_env.load())
    service.gate.provider_loader = lambda *a, **k: Recorder()
    with pytest.raises(GateDenied) as error:
        service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)
    assert seen == [["attempt_started"]]
    assert error.value.code == "PROVIDER_TIMEOUT"
    assert state.active("sample") is None and runtime.mock_calls == []
    rows = state.audit_rows(attempt_id=error.value.attempt_id)
    assert rows[-1]["principal_snapshot"] is None and rows[-1]["deployment_id"] is None


@pytest.mark.parametrize("mode", ["enforce", "audit"])
@pytest.mark.parametrize("fail_on", [1, 2, 3])
def test_audit_write_failure_prevents_docker(
    identity_env, state, runtime, monkeypatch, mode, fail_on
):
    identity_env.config["enforcement"] = mode
    service = DeploymentService(state, runtime, identity_env.load())
    from agentbreeder import audit as audit_module

    real = audit_module.append
    calls = {"n": 0}

    def flaky(db, event):
        calls["n"] += 1
        if calls["n"] == fail_on:
            raise sqlite3.OperationalError("database or disk is full")
        return real(db, event)

    monkeypatch.setattr(audit_module, "append", flaky)
    with pytest.raises(AuditUnavailable) as error:
        service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)
    assert error.value.code == "AUDIT_WRITE_FAILED"
    assert "nothing was deployed" in str(error.value)
    runtime.create.assert_not_called()
    runtime.start.assert_not_called()


def test_audit_failure_while_creating_deployment_rolls_back(
    identity_env, state, runtime, monkeypatch
):
    service = DeploymentService(state, runtime, identity_env.load())
    original = state._append

    def failing(event):
        if event is not None and event.action == "deployment_created":
            raise AuditError("Audit log write failed")
        return original(event)

    monkeypatch.setattr(state, "_append", failing)
    with pytest.raises(AuditUnavailable):
        service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)
    assert state.active("sample") is None
    runtime.create.assert_not_called()


def test_unsupported_configured_controls_deny_even_in_audit_mode(identity_env, state, runtime):
    identity_env.config["enforcement"] = "audit"
    identity_env.config["runtime_enforcement"] = {"mode": "mcp_http"}
    with pytest.raises(GateDenied, match="CONTROL_UNSUPPORTED"):
        deploy(identity_env, state, runtime)
    identity_env.config["runtime_enforcement"] = {"mode": "none"}
    identity_env.config["maker_checker"] = {"risk_tiers": ["high"]}
    with pytest.raises(GateDenied, match="CONTROL_UNSUPPORTED"):
        deploy(identity_env, state, runtime)
    assert runtime.mock_calls == []


def test_require_verified_deployer(identity_env, state, runtime):
    identity_env.config["deployer"] = {"require_verified": True}
    with pytest.raises(GateDenied, match="DEPLOYER_UNVERIFIED"):
        deploy(identity_env, state, runtime)
    result = deploy(identity_env, state, runtime, deployer=actor("dana"))
    assert result["gate"]["actor_verified"] is True
    assert "deployer_verification" not in result["gate"]["checks_unavailable"]


def test_unknown_provider_connection(identity_env, state, runtime):
    data = v2_manifest_data()
    data["identity"]["provider"] = "okta"
    with pytest.raises(GateDenied, match="PROVIDER_UNKNOWN"):
        deploy(identity_env, state, runtime, data)


def test_capabilities_are_reported_as_ceiling_only(identity_env, state, runtime):
    data = v2_manifest_data(
        capabilities={
            "tools": [{"mcp": "https://mcp.example.com/pay", "actions": ["refund.create"]}],
            "approval_required": ["refund.create"],
        }
    )
    result = deploy(identity_env, state, runtime, data)
    assert "runtime_tool_enforcement" in result["gate"]["checks_unavailable"]


def test_conflicting_spec_aborts_and_is_audited(identity_env, state, runtime):
    deploy(identity_env, state, runtime)
    runtime.reset_mock()
    from agentbreeder.store import DeploymentError

    with pytest.raises(DeploymentError, match="different spec"):
        deploy(identity_env, state, runtime, v2_manifest_data(port=9000))
    assert runtime.mock_calls == []
    assert audit_actions(state)[-1] == ("deploy", "aborted")
    assert state.verify_audit()["incomplete_attempts"] == []


def test_secret_copies_removed_on_failure_and_remove(identity_env, state, runtime):
    from agentbreeder import secret_files

    secret_files.set_secret(state.directory, "k", b"v")
    data = v2_manifest_data(secrets={"KEY": "secret://k"})
    runtime.healthy.return_value = False
    failed = deploy(identity_env, state, runtime, data)
    assert failed["state"] == "failed"
    assert not secret_files.deployment_dir(state.directory, failed["id"]).exists()
    runtime.healthy.return_value = True
    ready = deploy(identity_env, state, runtime, data)
    assert ready["state"] == "ready"
    assert secret_files.deployment_dir(state.directory, ready["id"]).exists()
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    service = DeploymentService(state, runtime, identity_env.load())
    assert service.remove(ready["id"], local=True)["state"] == "removed"
    assert not secret_files.deployment_dir(state.directory, ready["id"]).exists()


def test_existing_container_without_secret_copies_is_recreated(identity_env, state, runtime):
    from agentbreeder import secret_files

    secret_files.set_secret(state.directory, "k", b"v1")
    data = v2_manifest_data(secrets={"KEY": "secret://k"})
    running = {"Id": "c", "State": {"Running": True}}
    runtime.inspect.side_effect = [None, running, running, running]
    first = deploy(identity_env, state, runtime, data)
    secret_files.cleanup(state.directory, first["id"])  # e.g. an earlier failed attempt
    store_record = state.get(first["id"])
    state.transition(first["id"], "failed", error="x")
    runtime.inspect.side_effect = [running, running, running, running]
    runtime.create.reset_mock()
    again = deploy(identity_env, state, runtime, data)
    assert again["state"] == "ready" and store_record["id"] == again["id"]
    runtime.remove.assert_called_once()
    runtime.create.assert_called_once()
    assert secret_files.present(state.directory, again["id"], {"KEY": "secret://k"})


def test_materialise_os_error_fails_and_cleans(identity_env, state, runtime, monkeypatch):
    from agentbreeder import secret_files

    secret_files.set_secret(state.directory, "k", b"v")
    runtime.inspect.side_effect = [None] + [{"Id": "c", "State": {"Running": True}}] * 3

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(secret_files, "materialise", boom)
    result = deploy(identity_env, state, runtime, v2_manifest_data(secrets={"KEY": "secret://k"}))
    assert result["state"] == "failed" and "SECRET_UNREADABLE" in result["error"]
    runtime.create.assert_not_called()
