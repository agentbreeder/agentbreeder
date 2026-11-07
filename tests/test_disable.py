"""P3 (#705): durable disable, authorized enable, review expiry and reconcile."""

from __future__ import annotations

import json

import pytest
from conftest import actor, enable_cedar, principal_entry, v2_manifest_data

from agentbreeder import cli
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.manifest import Manifest
from agentbreeder.store import DeploymentError, Store

OPS = actor("ops", verified=False)
ADMIN = actor("admin")


def service(env, state, runtime):
    return DeploymentService(state, runtime, env.load())


def deploy(env, state, runtime, deployer=None, **overrides):
    return service(env, state, runtime).deploy(
        Manifest.model_validate(v2_manifest_data(**overrides)), local=True, deployer=deployer
    )


def stopping(runtime):
    """inspect -> running container, then gone after remove."""
    running = {"Id": "container-id", "State": {"Running": True}}
    runtime.inspect.side_effect = [running, None]


def test_disable_persists_before_stop_and_leaves_nothing_running(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    order = []
    runtime.remove.side_effect = lambda info: order.append(
        state.blocker(first["principal_key"], "dev") is not None
    )
    stopping(runtime)
    result = service(identity_env, state, runtime).disable("sample", OPS)
    assert result["state"] == "disabled" and result["stopped"] == [first["id"]]
    assert order == [True]  # blocker was durable before the container stop
    assert state.get(first["id"])["state"] == "disabled"
    actions = [(r["action"], r["decision"]) for r in state.audit_rows(limit=20)]
    assert ("disable", "blocked") in actions and ("transition", "disabled") in actions


@pytest.mark.parametrize("mode", ["enforce", "audit"])
def test_blocker_survives_restart_rename_and_remove_recreate(
    identity_env, state, runtime, tmp_path, mode
):
    first = deploy(identity_env, state, runtime)
    stopping(runtime)
    service(identity_env, state, runtime).disable(first["id"], OPS)
    runtime.inspect.side_effect = None
    identity_env.config["enforcement"] = mode
    reopened = Store(state.directory)  # process restart
    try:
        runtime.reset_mock()
        for overrides in ({}, {"name": "renamed-agent"}):
            with pytest.raises(GateDenied) as error:
                deploy(identity_env, reopened, runtime, **overrides)
            assert error.value.code == "PRINCIPAL_BLOCKED"
        assert runtime.create.call_count == 0 and runtime.start.call_count == 0
        runtime.inspect.side_effect = [None, None]
        service(identity_env, reopened, runtime).remove(first["id"], local=True)
        runtime.inspect.side_effect = None
        with pytest.raises(GateDenied, match="PRINCIPAL_BLOCKED"):
            deploy(identity_env, reopened, runtime)
    finally:
        reopened.close()


def test_disable_without_deployment_by_principal_ref(identity_env, state, runtime):
    result = service(identity_env, state, runtime).disable(
        "-", OPS, provider="static", ref="agent-1", environment="dev"
    )
    assert result["stopped"] == [] and result["state"] == "disabled"
    with pytest.raises(GateDenied, match="PRINCIPAL_BLOCKED"):
        deploy(identity_env, state, runtime)
    # The block is per environment.
    identity_env.principals["agent-1"] = principal_entry(environment="prod")
    data = v2_manifest_data()
    data["identity"]["environment"] = "prod"
    svc = service(identity_env, state, runtime)
    assert (
        svc.deploy(Manifest.model_validate(data), local=True, environment="prod")["state"]
        == "ready"
    )


def test_disable_unknown_target(identity_env, state, runtime):
    with pytest.raises(DeploymentError, match="No identity-bound deployment"):
        service(identity_env, state, runtime).disable("nope", OPS)
    with pytest.raises(DeploymentError, match="--environment"):
        service(identity_env, state, runtime).disable("-", OPS, provider="static", ref="agent-1")


def test_stop_failure_retains_blocker_and_returns_failed(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    runtime.remove.side_effect = DeploymentError("engine timeout")
    result = service(identity_env, state, runtime).disable("sample", OPS)
    assert result["state"] == "failed" and result["failed"][0]["id"] == first["id"]
    assert state.blocker(first["principal_key"], "dev")
    assert "Disable incomplete" in state.get(first["id"])["error"]
    # Reconcile retries cleanup and never restarts anything.
    runtime.remove.side_effect = None
    stopping(runtime)
    runtime.reset_mock(return_value=False, side_effect=False)
    stopping(runtime)
    results = service(identity_env, state, runtime).reconcile(OPS)
    assert results[0]["compliance"] == "noncompliant:PRINCIPAL_BLOCKED"
    runtime.create.assert_not_called()
    runtime.start.assert_not_called()
    assert state.get(first["id"])["state"] == "disabled"


def test_enable_requires_verified_and_policy(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    stopping(runtime)
    svc = service(identity_env, state, runtime)
    svc.disable("sample", OPS)
    with pytest.raises(GateDenied, match="DEPLOYER_UNVERIFIED"):
        svc.enable(first["id"], OPS)
    with pytest.raises(GateDenied, match="POLICY_UNAVAILABLE"):
        svc.enable(first["id"], ADMIN)
    pytest.importorskip("cedarpy")
    enable_cedar(identity_env)
    svc = service(identity_env, state, runtime)
    with pytest.raises(GateDenied, match="ENABLE_NOT_AUTHORIZED"):
        svc.enable(first["id"], actor("erin"))
    runtime.reset_mock()
    result = svc.enable(first["id"], ADMIN)
    assert result == {"enabled": result["enabled"], "state": "enabled", "started": []}
    assert runtime.mock_calls == []  # enable starts nothing
    assert state.get(first["id"])["state"] == "disabled"
    with pytest.raises(GateDenied, match="NOT_BLOCKED"):
        svc.enable(first["id"], ADMIN)
    # A later deployment passes a fresh gate.
    runtime.inspect.side_effect = None
    runtime.inspect.return_value = None
    created = {"Id": "new", "State": {"Running": True}}
    runtime.inspect.side_effect = [None, created, created]
    redeployed = service(identity_env, state, runtime).deploy(
        Manifest.model_validate(v2_manifest_data()), local=True, deployer=actor("dana")
    )
    assert redeployed["state"] == "ready"
    runtime.create.assert_called_once()


def test_idp_disable_is_additional_and_reported(identity_env, state, runtime):
    deploy(identity_env, state, runtime)
    stopping(runtime)
    result = service(identity_env, state, runtime).disable("sample", OPS, idp=True)
    assert result["idp"] == "PROVIDER_UNSUPPORTED" and result["state"] == "failed"
    assert state.blocker(
        json.dumps(["static", "tenant-a", "agent-1"], separators=(",", ":")), "dev"
    )


def test_review_expiry_blocks_redeploy_and_reconcile_marks_noncompliant(
    identity_env, state, runtime
):
    first = deploy(identity_env, state, runtime)
    identity_env.principals["agent-1"]["review_by"] = "2000-01-01"
    svc = service(identity_env, state, runtime)
    assert svc.status(first["id"])["compliance"] == "noncompliant:REVIEW_EXPIRED"
    results = svc.reconcile(OPS)
    assert results == [
        {"id": first["id"], "name": "sample", "compliance": "noncompliant:REVIEW_EXPIRED"}
    ]
    runtime.reset_mock()
    with pytest.raises(GateDenied, match="REVIEW_EXPIRED"):
        deploy(identity_env, state, runtime)
    runtime.start.assert_not_called()


def test_status_reverification_states(identity_env, state, runtime):
    first = deploy(identity_env, state, runtime)
    svc = service(identity_env, state, runtime)
    assert svc.status(first["id"])["compliance"] == "compliant"
    identity_env.principals = {}
    identity_env.write()
    assert svc.status(first["id"])["compliance"] == "unverifiable:PRINCIPAL_NOT_FOUND"
    assert (
        svc.status(first["id"], verify=False)["compliance"] == "unverifiable:PRINCIPAL_NOT_FOUND"
    )


def test_cli_kill_switch_flow(identity_env, tmp_path, monkeypatch, capsys):
    from unittest.mock import Mock

    fake = Mock(context="local-test")
    fake.inspect.side_effect = [
        None,
        {"Id": "c", "State": {"Running": True}},
        {"Id": "c", "State": {"Running": True}},
        {"Id": "c", "State": {"Running": True}},
        None,
    ]
    fake.endpoint.return_value = "http://127.0.0.1:1"
    fake.healthy.return_value = True
    monkeypatch.setattr(cli, "Docker", lambda: fake)
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(json.dumps(v2_manifest_data()))
    state = ["--state-dir", str(tmp_path / "state")]
    assert cli.main(["deploy", str(manifest), "--local", *state]) == 0
    out = capsys.readouterr()
    assert "unavailable checks: policy, approval, deployer_verification" in out.out
    assert "WARNING: Identity preflight only" in out.err
    assert cli.main(["disable", "sample", "--local", *state, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "disabled"
    fake.inspect.side_effect = None
    fake.inspect.return_value = None
    assert cli.main(["deploy", str(manifest), "--local", *state]) == 2
    assert "[PRINCIPAL_BLOCKED]" in capsys.readouterr().err
    assert cli.main(["enable", "sample", *state]) == 2
    assert "[DEPLOYER_UNVERIFIED]" in capsys.readouterr().err
    # Drift (here: a blocked principal) exits 4 so scripts and CI can gate on it.
    assert cli.main(["reconcile", "--local", *state]) == 4
    assert "noncompliant:PRINCIPAL_BLOCKED" in capsys.readouterr().out
    assert cli.main(["audit", "verify", *state]) == 0
    assert "OK:" in capsys.readouterr().out


def test_audit_mode_idp_outage_cannot_bypass_blocker(identity_env, state, runtime):
    deploy(identity_env, state, runtime)
    stopping(runtime)
    service(identity_env, state, runtime).disable("sample", OPS)
    identity_env.config["enforcement"] = "audit"
    svc = service(identity_env, state, runtime)

    class Down:
        name = "static"

        def resolve(self, ref):
            from agentbreeder.identity import ProviderError

            raise ProviderError("PROVIDER_TIMEOUT", "Identity provider timed out")

    svc.gate.provider_loader = lambda *a, **k: Down()
    runtime.reset_mock()
    for overrides in ({}, {"name": "renamed"}):
        with pytest.raises(GateDenied, match="PRINCIPAL_BLOCKED"):
            svc.deploy(Manifest.model_validate(v2_manifest_data(**overrides)), local=True)
    runtime.create.assert_not_called()


def test_static_alias_reference_blocked_before_resolution(identity_env, state, runtime):
    identity_env.principals = {"alias-ref": {**principal_entry(), "id": "immutable-id"}}
    data = v2_manifest_data()
    data["identity"]["ref"] = "alias-ref"
    svc = service(identity_env, state, runtime)
    svc.deploy(Manifest.model_validate(data), local=True)
    stopping(runtime)
    svc.disable("sample", OPS)
    assert state.blocked_reference("static", "alias-ref", "dev")
    assert not state.blocked_reference("static", "alias-ref", "prod")
