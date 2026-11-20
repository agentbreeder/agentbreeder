"""Northwind on the Claude Agent SDK (#1089, multi-framework spec F7, §14, D13 (a)): the port
builds from Northwind's registry with its own lock, on `runtime/claude-agent-sdk@1.0.0` from
its own project registry (owner decision D8: not in `builtin_registry`), and means the same thing
as the ADK file. No Docker: `docker build` is replaced by a recorder, so nothing downloads the
SDK wheel.

The real acceptance is `tests/test_northwind_claude_agent_sdk_docker.py`: opt-in, written, not
run.
"""

from __future__ import annotations

import json
import shutil

import pytest
import yaml
from test_northwind_langgraph import EXAMPLES, Recorder, _operator

from agentbreeder import registry
from agentbreeder.build import BuildError, build

PORT = "northwind-claude-agent-sdk"
NAME = "northwind-support-claude-agent-sdk"
REF = "runtime/claude-agent-sdk@1.0.0"


@pytest.fixture
def examples(tmp_path, monkeypatch):
    for name in ("northwind", PORT):
        shutil.copytree(
            EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    monkeypatch.setenv(registry.PATH_ENV, str(tmp_path / "northwind" / "registry"))
    return tmp_path


def test_the_port_is_the_adk_file_with_only_name_and_runtime_changed():
    adk = yaml.safe_load((EXAMPLES / "northwind" / "agent.yaml").read_text())
    port = yaml.safe_load((EXAMPLES / PORT / "agent.yaml").read_text())
    assert port.pop("name") == NAME
    assert port.pop("runtime") == REF
    adk.pop("name"), adk.pop("runtime")
    assert port == adk


def test_the_ports_lock_is_northwinds_with_the_projects_runtime():
    adk = json.loads((EXAMPLES / "northwind" / "agent.lock").read_text())["entries"]
    port = json.loads((EXAMPLES / PORT / "agent.lock").read_text())["entries"]
    shared = {k: v["sha256"] for k, v in adk.items() if not k.startswith("runtime/")}
    assert {k: v["sha256"] for k, v in port.items() if not k.startswith("runtime/")} == shared
    assert port[REF]["source"] == "registry"  # the port's own registry, never "builtin"


def test_the_port_builds_locked_with_two_uids_and_the_messages_route(examples, identity_env):
    operator = _operator(identity_env, ["northwind-support", NAME])
    adk, port = Recorder(), Recorder()
    build(examples / "northwind" / "agent.yaml", operator, "dev", mode="locked", builder=adk)
    result = build(examples / PORT / "agent.yaml", operator, "dev", mode="locked", builder=port)
    adk_config = json.loads(adk.files["agent.config.json"])
    port_config = json.loads(port.files["agent.config.json"])
    assert port_config.pop("name") == NAME
    adk_config.pop("name")
    assert port_config == adk_config
    artifacts = {k: v for k, v in adk.files.items() if k.startswith("artifacts/")}
    assert {k: v for k, v in port.files.items() if k.startswith("artifacts/")} == artifacts
    for name in ("runtime/kit/proxy.py", "runtime/claude_adapter.py", "runtime/cli_host.py"):
        assert name in port.files
    assert not any(name.startswith("runtime_tests/") for name in port.files)
    assert b'org.agentbreeder.runtime="runtime/claude-agent-sdk@1.0.0"' in port.files["Dockerfile"]
    manifest = yaml.safe_load(result.manifest_path.read_text())
    assert manifest["runtime"] == {
        "ref": REF,
        "profile": "kit",
        "model_api": "anthropic-messages",
        "isolation": "two-uid",
    }


def test_the_runtime_refuses_mcp(examples, identity_env):
    operator = _operator(identity_env, [NAME])
    path = examples / PORT / "agent.yaml"
    path.write_text(path.read_text() + "mcp:\n  - {ref: mcp/github@1.0.0, tools: [get_me]}\n")
    with pytest.raises(BuildError) as raised:
        build(path, operator, "dev", builder=Recorder())
    assert raised.value.code == "MCP_RUNTIME_UNSUPPORTED"


def test_the_port_is_not_a_standalone_example(tmp_path):
    from agentbreeder import example

    assert PORT not in example.names()


def test_northwinds_stack_names_the_ports_identity():
    stack = yaml.safe_load((EXAMPLES / "northwind" / "stack.yaml").read_text())
    assert NAME in stack["environment"]["agents"]


def test_the_acceptance_pins_the_messages_gateway_only_in_a_copy(tmp_path):
    """The Docker acceptance needs `service/litellm@1.4.0` (its filter serves `/v1/messages`,
    #1187). The shipped Northwind stack stays on 1.0.0 for the other agents (owner rule: no
    repin), so the test refuses the shipped stack and accepts a copy pinned and relocked."""
    from test_northwind_claude_agent_sdk_docker import GATEWAY, gateway_refs

    shipped = EXAMPLES / "northwind"
    assert GATEWAY == "service/litellm@1.4.0"
    assert (registry.BUILTIN_ROOT / "service" / "litellm" / "1.4.0" / "service.yaml").is_file()
    assert gateway_refs(shipped) == ("service/litellm@1.0.0", ["service/litellm@1.0.0"])

    copy = tmp_path / "northwind"
    shutil.copytree(shipped, copy, ignore=shutil.ignore_patterns("__pycache__"))
    stack = copy / "stack.yaml"
    stack.write_text(stack.read_text().replace("service/litellm@1.0.0", GATEWAY))
    # Pinned but not yet up: the lock still names 1.0.0, so the fixture would refuse it.
    assert gateway_refs(copy) == (GATEWAY, ["service/litellm@1.0.0"])
    lock_file = copy / "stack.lock"
    lock = json.loads(lock_file.read_text())
    lock["entries"][GATEWAY] = lock["entries"].pop("service/litellm@1.0.0")
    lock_file.write_text(json.dumps(lock))
    assert gateway_refs(copy) == (GATEWAY, [GATEWAY])
