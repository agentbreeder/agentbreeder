"""`--stack DIR` on enable/disable/approve/reject/approve-call/reject-call (#860): they act on
the deployments `services up` state holds, gated by the stack's generated operator config."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from conftest import IdentityEnv, enable_cedar, principal_entry, v2_manifest_data, write_private

from agentbreeder import cli, operator_config
from agentbreeder import deployer as deployer_module
from agentbreeder.services.state import ROOT_ENV, StackState
from agentbreeder.store import Store

STACK = """kind: stack
schema_version: 1
name: helper
services:
  postgres:
    ref: service/postgres-pgvector@1.0.0
  litellm:
    ref: service/litellm@1.0.0
    with: {config: stack/litellm/config.yaml}
environment:
  gateway: litellm
  models: [support-model]
  ceilings: {budget_usd: 5, rpm: 60}
  agents:
    sample:
      sponsor: you@example.com
      review_by: "2099-01-01"
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project whose stack was brought up for `dev`: the stack's operator config and state
    live under the stacks root, never in the default state or the shell's operator config."""
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    for name in (deployer_module.TOKEN_ENV, deployer_module.TOKEN_FILE_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(deployer_module, "CACHE_PATH", tmp_path / "no-token-cache")
    # The shell's operator config points elsewhere; --stack must replace it.
    monkeypatch.setenv(operator_config.CONFIG_ENV, str(tmp_path / "missing" / "operator.yaml"))
    directory = tmp_path / "helper"
    directory.mkdir()
    (directory / "stack.yaml").write_text(STACK)
    (directory / "agent.yaml").write_text(json.dumps(v2_manifest_data()))
    state = StackState("helper", directory.resolve(), tmp_path / "stacks")
    env = state.env_dir("dev")
    env.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.close()
    return directory, IdentityEnv(env)


@pytest.fixture
def docker(monkeypatch):
    fake = Mock(context="local-test")
    running = {"Id": "c", "State": {"Running": True}}
    fake.inspect.side_effect = [None, running, running, running, None]
    fake.endpoint.return_value = "http://127.0.0.1:1"
    fake.healthy.return_value = True
    monkeypatch.setattr(cli, "Docker", lambda: fake)
    return fake


def stack_store(identity: IdentityEnv) -> Store:
    return Store(identity.directory / "state")


def test_disable_and_enable_act_on_the_stack_deployment(project, docker, capsys):
    directory, identity = project
    stack = ["--stack", str(directory)]
    assert cli.main(["deploy", str(directory / "agent.yaml"), "--local", *stack]) == 0
    capsys.readouterr()
    assert (
        cli.main(["disable", "sample", "--local", *stack, "--environment", "dev", "--json"]) == 0
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["state"] == "disabled"
    assert "Using stack helper-" in captured.err
    store = stack_store(identity)
    try:
        record = store.active("sample")
        assert record and store.blocker(record["principal_key"], "dev") is not None
    finally:
        store.close()
    # enable reads the same stack state and config: the demo identity is unverified.
    assert cli.main(["enable", "sample", *stack, "--environment", "dev"]) == 2
    assert "[DEPLOYER_UNVERIFIED]" in capsys.readouterr().err


def test_disable_by_principal_ref_takes_the_stack_environment(project, docker, capsys):
    directory, identity = project
    args = ["disable", "-", "--local", "--provider", "static", "--ref", "agent-1"]
    assert cli.main([*args, "--stack", str(directory), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "disabled"
    store = stack_store(identity)
    try:
        environments = [row[0] for row in store.db.execute("SELECT environment FROM blockers")]
    finally:
        store.close()
    assert environments == ["dev"]


def test_a_command_without_stack_does_not_see_the_stack_state(project, tmp_path, capsys):
    other = ["--state-dir", str(tmp_path / "default-state")]
    assert cli.main(["approve", "a1", *other]) == 2
    assert "No AgentBreeder state" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["approve", "reject", "approve-call", "reject-call"])
def test_environment_needs_stack(project, capsys, command):
    with pytest.raises(SystemExit) as error:
        cli.main([command, "x", "--environment", "dev"])
    assert error.value.code == 2
    assert "--environment needs --stack" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["enable", "disable"])
def test_enable_disable_keep_environment_without_stack(command, tmp_path, capsys):
    """Their --environment names the principal's environment; it never needed --stack."""
    argv = [command, "x", "--environment", "dev", "--state-dir", str(tmp_path / "s")]
    if command == "disable":
        argv.append("--local")
    assert cli.main(argv) == 2
    assert "--environment needs --stack" not in capsys.readouterr().err


@pytest.mark.parametrize("command", ["approve", "reject", "enable", "disable", "approve-call"])
def test_stack_and_state_dir_together_are_refused(project, tmp_path, capsys, command):
    directory, _ = project
    argv = [command, "x", "--stack", str(directory), "--state-dir", str(tmp_path / "s")]
    if command == "disable":
        argv.append("--local")
    with pytest.raises(SystemExit):
        cli.main(argv)
    assert "use --stack or --state-dir, not both" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["approve-call", "reject-call"])
def test_held_call_decisions_read_the_stack_state(project, docker, capsys, command):
    directory, _ = project
    deploy = ["deploy", str(directory / "agent.yaml"), "--local", "--stack", str(directory)]
    assert cli.main(deploy) == 0
    capsys.readouterr()
    code = cli.main([command, "held-unknown", "--stack", str(directory), "--environment", "dev"])
    err = capsys.readouterr().err
    assert code == 2 and "Using stack helper-" in err
    assert "Held call not found in this state directory" in err  # the stack's state opened


def test_an_unknown_stack_environment_is_coded(project, capsys):
    directory, _ = project
    argv = ["approve", "a1", "--stack", str(directory), "--environment", "prod", "--json"]
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[STACK_NOT_UP] ")
    assert json.loads(captured.out)["error"]["code"] == "STACK_NOT_UP"


def test_second_person_approves_with_the_stack_config(
    project, docker, monkeypatch, capsys, tmp_path
):
    pytest.importorskip("cedarpy")
    from test_deployer import ISSUER, KEY, jwk, token

    directory, identity = project
    enable_cedar(identity)
    write_private(identity.directory / "jwks.json", json.dumps({"keys": [jwk(KEY)]}))
    identity.config["deployer"] = {
        "issuers": [
            {
                "issuer": ISSUER,
                "audience": "agentbreeder",
                "jwks_file": "jwks.json",
                "kind": "human",
            }
        ]
    }
    identity.principals["agent-1"] = principal_entry(
        attributes={"acme/data_classification": "confidential", "acme/risk_tier": "high"}
    )
    identity.write()
    stack = ["--stack", str(directory), "--environment", "dev"]
    monkeypatch.setenv(deployer_module.TOKEN_ENV, token({"sub": "dana"}))
    assert cli.main(["deploy", str(directory / "agent.yaml"), "--local", *stack]) == (
        cli.EXIT_PENDING
    )
    approval_id = capsys.readouterr().out.split()[1]
    other = tmp_path / "other-state"
    Store(other).close()  # an existing state that is not the stack's
    assert cli.main(["approve", approval_id, "--state-dir", str(other)]) == 2
    assert approval_id not in capsys.readouterr().out
    assert cli.main(["approve", approval_id, *stack]) == 2  # dana cannot self-approve
    assert "[SELF_APPROVAL]" in capsys.readouterr().err
    monkeypatch.setenv(deployer_module.TOKEN_ENV, token({"sub": "carol"}))
    assert cli.main(["approve", approval_id, *stack, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "approved"
    assert cli.main(["reject", approval_id, *stack]) == 2  # already decided
