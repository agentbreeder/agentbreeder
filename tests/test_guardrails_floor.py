"""#1024 G1a: the environment guardrails floor, its gate deny, the Cedar `guardrails`
attribute and the reserved provider-key names. No guardrail runs in this slice."""

from __future__ import annotations

import json

import pytest
from conftest import actor, v2_manifest_data

from agentbreeder import gate as gate_module
from agentbreeder import policy
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDecision, GateDenied, revision_digest
from agentbreeder.gateway_keys import PROVIDER_KEYS
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import (
    DeploymentContext,
    OperatorConfigError,
    load_operator_config,
)

SHA = "a" * 64
FLOOR = {"required": True, "approved_sha256": SHA}

# Recorded on main before this slice (f6709b23). A manifest and an operator config that do not
# use guardrails keep these values; only the real SCHEMA_VERSION changes, once, at this slice.
GOLDEN_MANIFEST_DIGEST = "4a8edb090bea7139a2dbbae0105b89bd1bbb9d1dc45438e16f651dea52a825cf"
GOLDEN_REVISION_PINNED_SCHEMA = (
    "sha256:e4c953c7cb6b9d07924633886e59ef74f59c60e1c02d08626d7224ee04f54821"
)
GOLDEN_OPERATOR_BYTES = (
    b"enforcement: enforce\n"
    b"default_environment: dev\n"
    b"environments:\n"
    b"  dev:\n"
    b"    targets: [local]\n"
)


def _manifest() -> Manifest:
    return Manifest.model_validate(
        v2_manifest_data(model_access={"gateway": "stack", "aliases": ["chat"]})
    )


def _operator(tmp_path, data: bytes = GOLDEN_OPERATOR_BYTES):
    path = tmp_path / "operator.yaml"
    path.write_bytes(data)
    path.chmod(0o600)
    return load_operator_config(path)


def test_manifest_digest_unchanged_by_this_slice():
    assert _manifest().digest() == GOLDEN_MANIFEST_DIGEST


def test_revision_digest_unchanged_with_a_pinned_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "SCHEMA_VERSION", "cedar-schema:pinned")
    operator = _operator(tmp_path)
    value = revision_digest(
        _manifest(),
        None,
        actor("alice"),
        DeploymentContext("dev", "local"),
        operator,
        GateDecision(allowed=True),
    )
    assert value == GOLDEN_REVISION_PINNED_SCHEMA


SCHEMA_BEFORE = (
    "cedar-schema:sha256:74f6cff42fd165eb08414727951530e0b9835ffa9158d056f6e851c770127b5e"
)


def test_schema_version_changes_once_at_this_slice():
    # D6: every revision_digest changes here, so held approvals are reissued (changelog).
    assert policy.SCHEMA_VERSION != SCHEMA_BEFORE
    assert '"guardrails"' in policy.SCHEMA_TEXT


# -- operator config ------------------------------------------------------------------------


def _with_floor(identity_env, floor=None, **environment):
    identity_env.config["environments"]["dev"].update(
        {"guardrails": floor if floor is not None else FLOOR, **environment}
    )
    return identity_env.load()


def test_floor_is_accepted_and_optional(identity_env):
    config = _with_floor(identity_env)
    floor = config.model.environments["dev"].guardrails
    assert floor.required is True and floor.approved_sha256 == SHA
    assert config.model.environments["prod"].guardrails is None


@pytest.mark.parametrize(
    "floor",
    [
        {"required": True},  # a requirement names the reviewed hash
        {"required": True, "approved_sha256": "sha256:" + SHA},  # bare hex, as LiteLLM prints
        {"required": True, "approved_sha256": "A" * 64},
        {"required": "yes", "approved_sha256": SHA},
        {"required": True, "approved_sha256": SHA, "running_sha256": SHA},  # G1b writes it
        {"required": True, "approved_sha256": SHA, "effective": ["presidio:pre_call"]},
    ],
)
def test_invalid_floor_fails_config_load(identity_env, floor):
    with pytest.raises(OperatorConfigError, match="environments"):
        _with_floor(identity_env, floor)


def test_no_floor_keeps_the_in_memory_default_digest(tmp_path, monkeypatch):
    from agentbreeder import operator_config

    monkeypatch.delenv(operator_config.CONFIG_ENV, raising=False)
    monkeypatch.setattr(operator_config, "DEFAULT_PATH", tmp_path / "missing.yaml")
    # Recorded on main before this slice: a new defaulted field must not change it.
    assert load_operator_config().digest == (
        "sha256:37b042bfc9df45fb248d1f6ce30391457ada04fa9ade3ae74851d9ad7a1ee6f4"
    )


@pytest.mark.parametrize("name", sorted(PROVIDER_KEYS.values()))
def test_floor_reserves_provider_key_bindings(identity_env, name):
    with pytest.raises(OperatorConfigError) as error:
        _with_floor(identity_env, bindings={name: "secret://provider-key"})
    assert "SECRET_RESERVED" in str(error.value)
    assert "provider-key" not in str(error.value)


def test_provider_key_bindings_allowed_without_a_floor(identity_env):
    identity_env.config["environments"]["dev"]["bindings"] = {"GEMINI_API_KEY": "secret://k"}
    assert identity_env.load().model.environments["dev"].bindings


def test_floor_not_required_reserves_nothing(identity_env):
    config = _with_floor(
        identity_env,
        {"required": False},
        bindings={"GEMINI_API_KEY": "secret://k"},
    )
    assert config.model.environments["dev"].guardrails.required is False


# -- gate -----------------------------------------------------------------------------------


def _deploy(identity_env, state, runtime, data=None):
    service = DeploymentService(state, runtime, identity_env.load())
    manifest = Manifest.model_validate(data or v2_manifest_data())
    return service.deploy(manifest, local=True)


@pytest.mark.parametrize("enforcement", ["enforce", "audit"])
def test_required_floor_denies_every_deploy_until_verified(
    identity_env, state, runtime, enforcement
):
    identity_env.config["enforcement"] = enforcement
    identity_env.config["environments"]["dev"]["guardrails"] = FLOOR
    with pytest.raises(GateDenied) as error:
        _deploy(identity_env, state, runtime)
    assert error.value.code == "GUARDRAILS_UNVERIFIED"
    assert runtime.mock_calls == []
    assert "GUARDRAILS_UNVERIFIED" not in gate_module.OVERRIDABLE


def test_floor_not_required_does_not_deny(identity_env, state, runtime):
    identity_env.config["environments"]["dev"]["guardrails"] = {"required": False}
    assert _deploy(identity_env, state, runtime)["state"] == "ready"


@pytest.mark.parametrize("name", sorted(PROVIDER_KEYS.values()))
def test_floor_reserves_provider_key_secrets_in_manifests(identity_env, state, runtime, name):
    identity_env.config["environments"]["dev"]["guardrails"] = FLOOR
    data = v2_manifest_data(secrets={name: "secret://provider-key"})
    with pytest.raises(GateDenied) as error:
        _deploy(identity_env, state, runtime, data)
    assert error.value.code == "SECRET_RESERVED"
    assert runtime.mock_calls == []


def test_provider_key_secret_not_reserved_without_a_floor(identity_env, state, runtime):
    data = v2_manifest_data(secrets={"GEMINI_API_KEY": "secret://provider-key"})
    with pytest.raises(GateDenied) as error:  # the usual missing-file preflight, not reserved
        _deploy(identity_env, state, runtime, data)
    assert error.value.code == "SECRET_MISSING"


# -- policy ---------------------------------------------------------------------------------


def test_policy_request_carries_guardrails_for_remote_pdps(identity_env):
    gate = gate_module.IdentityGate(identity_env.load())
    request = gate.policy_request(
        "deploy",
        actor("alice"),
        agent="sample",
        context=DeploymentContext("dev", "local"),
        principal=None,
        risk_tier=None,
        manifest=_manifest(),
    )
    # OPA and AuthZEN receive `to_authzen()` unchanged, so the property reaches them.
    assert request.to_authzen()["resource"]["properties"]["guardrails"] == []
    assert json.dumps(request.to_authzen())


def test_cedar_entity_builders_carry_guardrails():
    from agentbreeder.policy import PolicyRequest
    from agentbreeder.policy.cedar import _call_entities, _entities

    deploy = PolicyRequest(
        subject={"type": "user", "id": "d", "properties": {}},
        action="deploy",
        resource={"type": "agent", "id": "a", "properties": {"guardrails": ["presidio:pre_call"]}},
    )
    assert _entities(deploy)[1]["attrs"]["guardrails"] == ["presidio:pre_call"]
    bare = PolicyRequest(subject={}, action="deploy", resource={})
    assert _entities(bare)[1]["attrs"]["guardrails"] == []
    call = PolicyRequest(subject={"id": "p", "properties": {}}, action="call_tool", resource={})
    assert _call_entities(call)[0]["attrs"]["guardrails"] == []


GUARDED = """
permit(principal, action == AgentBreeder::Action::"deploy", resource)
when { principal.verified && resource.principal_kind == "agent" };
forbid(principal, action == AgentBreeder::Action::"deploy", resource)
when { resource.risk_tier == "high" && !resource.guardrails.contains("presidio:pre_call") };
permit(principal, action == AgentBreeder::Action::"call_tool", resource);
"""


def test_cedar_bundle_can_require_guardrails(identity_env):
    pytest.importorskip("cedarpy")
    from conftest import enable_cedar

    from agentbreeder.policy import PolicyEngine, PolicyRequest

    enable_cedar(identity_env, baseline=GUARDED)
    config = identity_env.load()
    engine = PolicyEngine(config.model.pdp, [], identity_env.directory)

    def deploy(risk, guardrails):
        return PolicyRequest(
            subject={"type": "user", "id": "d", "properties": {"verified": True, "issuer": "i"}},
            action="deploy",
            resource={
                "type": "agent",
                "id": "a",
                "properties": {
                    "risk_tier": risk,
                    "principal": {"kind": "agent"},
                    "guardrails": guardrails,
                },
            },
        )

    assert engine.evaluate(deploy("low", [])).allowed
    assert not engine.evaluate(deploy("high", [])).allowed
    assert engine.evaluate(deploy("high", ["presidio:pre_call"])).allowed
    call = PolicyRequest(
        subject={"type": "agent", "id": "p", "properties": {"name": "a"}},
        action="call_tool",
        resource={"type": "tool", "id": "s/t", "properties": {}},
    )
    assert engine.evaluate(call).allowed  # call_tool still evaluates with the new attribute


def test_reserved_names_match_the_gateway_provider_keys():
    from agentbreeder.operator_config import PROVIDER_KEY_NAMES

    assert PROVIDER_KEY_NAMES == frozenset(PROVIDER_KEYS.values())


def test_config_error_codes_never_echo_pattern_brackets(identity_env):
    # A pattern failure message contains `[A-Z_]`-style brackets; only our fixed codes print.
    identity_env.config["environments"]["dev"]["bindings"] = {"bad-name": "secret://k"}
    with pytest.raises(OperatorConfigError) as error:
        identity_env.load()
    assert "[" not in str(error.value)


def test_floor_deny_is_audited_and_never_proceeds_in_audit_mode(identity_env, state, runtime):
    identity_env.config["enforcement"] = "audit"
    identity_env.config["environments"]["dev"]["guardrails"] = FLOOR
    with pytest.raises(GateDenied) as error:
        _deploy(identity_env, state, runtime)
    rows = state.audit_rows(attempt_id=error.value.attempt_id)
    assert rows[-1]["reasons"][0]["code"] == "GUARDRAILS_UNVERIFIED"
    assert not any(r["action"] == "audit_mode_proceed" for r in rows)


def test_floor_denies_release_preflight(identity_env, state, runtime):
    identity_env.config["environments"]["dev"]["guardrails"] = FLOOR
    service = DeploymentService(state, runtime, identity_env.load())
    decision = service.preflight(Manifest.model_validate(v2_manifest_data()), "dev", actor("d"))
    assert not decision.allowed and decision.code == "GUARDRAILS_UNVERIFIED"


def test_floor_is_per_environment(identity_env, state, runtime):
    identity_env.config["environments"]["prod"]["guardrails"] = FLOOR
    assert _deploy(identity_env, state, runtime)["state"] == "ready"  # dev has no floor
