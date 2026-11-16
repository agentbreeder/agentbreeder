"""#1025 D3: the model gateway's cloud credential names never reach an agent container. They are
refused as operator `bindings` (config load) and as manifest `secrets` (the gate), in every
environment, whether or not a guardrails floor is required, and however the value was stored."""

from __future__ import annotations

import io

import pytest
from conftest import v2_manifest_data

from agentbreeder import cli
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.gateway_providers import GATEWAY_ONLY
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import OperatorConfigError


@pytest.mark.parametrize("name", sorted(GATEWAY_ONLY))
@pytest.mark.parametrize("floor", [None, {"required": False}])
def test_bindings_may_not_use_gateway_only_names(identity_env, name, floor):
    environment = identity_env.config["environments"]["dev"]
    environment["bindings"] = {name: "secret://some-value-ref"}
    if floor is not None:
        environment["guardrails"] = floor
    with pytest.raises(OperatorConfigError) as error:
        identity_env.load()
    assert "SECRET_RESERVED" in str(error.value)  # the loader reports the code only
    assert "some-value-ref" not in str(error.value)


@pytest.mark.parametrize("name", sorted(GATEWAY_ONLY))
def test_manifest_secrets_may_not_use_gateway_only_names(
    identity_env, state, runtime, monkeypatch, name
):
    # Stored through stdin `secret set`, which no import rule sees: the gate still refuses it.
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"stored-value\n")))
    assert cli.main(["secret", "set", "cloud-key", "--state-dir", str(state.directory)]) == 0
    manifest = Manifest.model_validate(v2_manifest_data(secrets={name: "secret://cloud-key"}))
    service = DeploymentService(state, runtime, identity_env.load())
    with pytest.raises(GateDenied) as error:
        service.deploy(manifest, local=True)
    assert error.value.code == "SECRET_RESERVED"
    assert name in str(error.value)
    assert "stored-value" not in str(error.value)
    assert runtime.mock_calls == []


def test_other_secret_names_are_unaffected(identity_env, state, runtime):
    identity_env.config["environments"]["dev"]["bindings"] = {"AZURE_OPENAI_X": "secret://k"}
    config = identity_env.load()  # not a gateway-only name: no reservation
    manifest = Manifest.model_validate(v2_manifest_data(secrets={"MY_TOKEN": "secret://t"}))
    with pytest.raises(GateDenied) as error:  # the usual missing-file preflight
        DeploymentService(state, runtime, config).deploy(manifest, local=True)
    assert error.value.code == "SECRET_MISSING"


@pytest.mark.parametrize("name", sorted(GATEWAY_ONLY))
def test_stack_bindings_may_not_use_gateway_only_names(name):
    # Refused when stack.yaml loads, before `services up` writes them into an operator config.
    from test_services_engine import STACK

    from agentbreeder.services.schema import Stack

    data = {**STACK, "environment": {**STACK["environment"], "bindings": {name: {"ref": "x.y"}}}}
    with pytest.raises(ValueError, match="SECRET_RESERVED"):
        Stack.model_validate(data)
