"""Model gateway access in the manifest, granted by the operator environment (spec §10, §18.4)."""

from __future__ import annotations

import pytest
from conftest import v2_manifest_data
from pydantic import ValidationError

from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import OperatorConfigError


def grant(identity_env, **environment):
    identity_env.config["gateways"] = {
        "litellm-local": {
            "type": "litellm",
            "url": "http://127.0.0.1:4000",
            "agent_url": "http://host.docker.internal:4000",
            # no master_key: access checks only, no key minting (see test_gateway_keys.py)
        }
    }
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm-local",
            "models": ["support-model", "support-embedding"],
            "ceilings": {"budget_usd": 50, "rpm": 300},
            "services": {"traces_url": "http://host.docker.internal:8787/v1/traces"},
            **environment,
        }
    )


def deploy(identity_env, state, runtime, **access):
    data = v2_manifest_data(
        model_access={"gateway": "litellm-local", "aliases": ["support-model"], **access}
    )
    service = DeploymentService(state, runtime, identity_env.load())
    return service.deploy(Manifest.model_validate(data), local=True)


def test_granted_access_deploys_with_service_environment(identity_env, state, runtime):
    grant(identity_env)
    running = {"Id": "c", "State": {"Running": True}}
    runtime.inspect.side_effect = [None, running, running, running]
    result = deploy(identity_env, state, runtime, budget_usd=25, rpm=120)
    assert result["state"] == "ready"
    assert "model_access" in result["gate"]["checks_evaluated"]
    kwargs = runtime.create.call_args.kwargs
    assert kwargs["environment"] == {
        "AGENTBREEDER_GATEWAY_URL": "http://host.docker.internal:4000",
        "AGENTBREEDER_SERVICE_TRACES_URL": "http://host.docker.internal:8787/v1/traces",
    }
    assert kwargs["host_gateway"] is True


def test_manifest_without_model_access_is_unchanged(identity_env, state, runtime):
    grant(identity_env)
    running = {"Id": "c", "State": {"Running": True}}
    runtime.inspect.side_effect = [None, running, running, running]
    service = DeploymentService(state, runtime, identity_env.load())
    service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)
    assert "environment" not in runtime.create.call_args.kwargs
    assert "host_gateway" not in runtime.create.call_args.kwargs


@pytest.mark.parametrize(
    "environment,access,code",
    [
        ({"gateway": None}, {}, "MODEL_ACCESS_UNCONFIGURED"),
        ({}, {"gateway": "other"}, "GATEWAY_MISMATCH"),
        ({}, {"aliases": ["support-model", "gpt-unlisted"]}, "MODEL_NOT_ALLOWED"),
        ({}, {"budget_usd": 51}, "BUDGET_CEILING"),
        ({}, {"rpm": 301}, "BUDGET_CEILING"),
    ],
)
def test_denials_before_runtime(identity_env, state, runtime, environment, access, code):
    grant(identity_env, **environment)
    with pytest.raises(GateDenied) as error:
        deploy(identity_env, state, runtime, **access)
    assert error.value.code == code
    assert runtime.create.call_count == 0


def test_model_access_denials_not_overridable_in_audit_mode(identity_env, state, runtime):
    grant(identity_env)
    identity_env.config["enforcement"] = "audit"
    with pytest.raises(GateDenied) as error:
        deploy(identity_env, state, runtime, budget_usd=1000)
    assert error.value.code == "BUDGET_CEILING"


def test_gateway_without_ceilings_is_rejected(identity_env):
    grant(identity_env, ceilings=None)
    with pytest.raises(OperatorConfigError, match="environments"):
        identity_env.load()


@pytest.mark.parametrize(
    "access",
    [
        {"gateway": "litellm-local", "aliases": []},
        {"gateway": "litellm-local", "aliases": ["a", "a"]},
        {"gateway": "litellm-local", "aliases": ["bad alias"]},
        {"gateway": "Bad!", "aliases": ["a"]},
        {"gateway": "litellm-local", "aliases": ["a"], "budget_usd": 0},
    ],
)
def test_manifest_model_access_validation(access):
    with pytest.raises(ValidationError):
        Manifest.model_validate(v2_manifest_data(model_access=access))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["environments"]["dev"].update({"gateway": "missing"}),
        lambda c: c["environments"]["dev"].update({"models": ["bad alias"]}),
        lambda c: c["environments"]["dev"].update({"services": {"Bad": "http://x"}}),
        lambda c: c["environments"]["dev"].update({"services": {"ok": "ftp://x"}}),
        lambda c: c["environments"]["dev"].update({"bindings": {"lower": "secret://x"}}),
        lambda c: c["environments"]["dev"].update({"bindings": {"DB": "literal-value"}}),
        lambda c: c["environments"]["dev"].update({"agents": {"Bad_Name": {}}}),
        lambda c: c["gateways"]["litellm-local"].update({"master_key": "literal-value"}),
    ],
)
def test_operator_config_validation(identity_env, mutation):
    grant(identity_env)
    mutation(identity_env.config)
    with pytest.raises(OperatorConfigError):
        identity_env.load()


def test_operator_agents_and_bindings_parse(identity_env):
    grant(
        identity_env,
        bindings={"DATABASE_URL": "secret://northwind-db"},
        agents={
            "northwind-support": {
                "identity": {"provider": "static", "ref": "agent-1", "environment": "dev"},
                "candidate_identity": {
                    "provider": "static",
                    "ref": "agent-1",
                    "environment": "dev",
                },
            }
        },
    )
    environment = identity_env.load().model.environments["dev"]
    assert environment.bindings == {"DATABASE_URL": "secret://northwind-db"}
    assert environment.agents["northwind-support"].identity.ref == "agent-1"


def test_preflight_applies_the_deploy_requirements(identity_env, state, runtime):
    from conftest import actor

    grant(identity_env)
    service = DeploymentService(state, runtime, identity_env.load())
    data = v2_manifest_data(
        model_access={"gateway": "litellm-local", "aliases": ["support-model"]},
        secrets={"DATABASE_URL": "secret://missing-db"},
    )
    decision = service.preflight(Manifest.model_validate(data), "dev", actor("dave"))
    assert not decision.allowed and decision.code == "SECRET_MISSING"
    runtime.create.assert_not_called()
