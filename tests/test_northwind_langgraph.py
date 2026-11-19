"""Northwind on LangGraph (#1083 F3, multi-framework spec §14, D13 (a)): the port builds from
Northwind's registry with its own lock, on `runtime/langgraph@1.0.0`, and means the same thing
as the ADK file. No Docker: `docker build` is replaced by a recorder.

The real acceptance (conformance, 61/61 through `release`, tools and traces, cost, coded errors,
`kind`) needs Docker and a real stack; it is `tests/test_northwind_langgraph_docker.py`, opt-in.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from agentbreeder import registry
from agentbreeder.build import BuildError, build

EXAMPLES = Path(registry.__file__).parent / "examples"
IMAGE = "sha256:" + "d" * 64


class Recorder:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def __call__(self, context: Path, iidfile: Path) -> str:
        self.files = {
            path.relative_to(context).as_posix(): path.read_bytes()
            for path in sorted(context.rglob("*"))
            if path.is_file()
        }
        return IMAGE


def _operator(identity_env, names: list[str]):
    identity_env.config["gateways"] = {
        "litellm-local": {
            "type": "litellm",
            "url": "http://127.0.0.1:4000",
            "agent_url": "http://host.docker.internal:4000",
        }
    }
    identity = {"provider": "static", "ref": "agent-1", "environment": "dev"}
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm-local",
            "models": ["support-model", "support-model-openrouter", "support-embedding"],
            "ceilings": {"budget_usd": 50, "rpm": 300},
            "bindings": {"DATABASE_URL": "secret://agent-db"},
            "agents": {name: {"identity": dict(identity)} for name in names},
        }
    )
    return identity_env.load()


@pytest.fixture
def examples(tmp_path, monkeypatch):
    for name in ("northwind", "northwind-langgraph"):
        shutil.copytree(
            EXAMPLES / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    # D13 (a): the port uses Northwind's registry through the registry path.
    monkeypatch.setenv(registry.PATH_ENV, str(tmp_path / "northwind" / "registry"))
    return tmp_path


def test_the_port_is_the_adk_file_with_only_name_and_runtime_changed():
    adk = yaml.safe_load((EXAMPLES / "northwind" / "agent.yaml").read_text())
    port = yaml.safe_load((EXAMPLES / "northwind-langgraph" / "agent.yaml").read_text())
    assert port.pop("name") == "northwind-support-langgraph"
    assert port.pop("runtime") == "runtime/langgraph@1.0.0"
    assert adk.pop("name") == "northwind-support" and adk.pop("runtime").startswith("runtime/adk@")
    assert port == adk


def test_the_ports_lock_is_northwinds_with_the_langgraph_runtime():
    adk = json.loads((EXAMPLES / "northwind" / "agent.lock").read_text())["entries"]
    port = json.loads((EXAMPLES / "northwind-langgraph" / "agent.lock").read_text())["entries"]
    # The same versions and hashes, loaded from Northwind's registry (D13 (a)).
    assert {k: v["sha256"] for k, v in port.items() if not k.startswith("runtime/")} == {
        k: v["sha256"] for k, v in adk.items() if not k.startswith("runtime/")
    }
    assert {v["source"] for k, v in port.items() if not k.startswith("runtime/")} == {
        "../northwind/registry"
    }
    assert port["runtime/langgraph@1.0.0"]["source"] == "builtin"


def test_the_port_builds_locked_and_means_what_the_adk_file_means(examples, identity_env):
    operator = _operator(identity_env, ["northwind-support", "northwind-support-langgraph"])
    adk, port = Recorder(), Recorder()
    build(examples / "northwind" / "agent.yaml", operator, "dev", mode="locked", builder=adk)
    result = build(
        examples / "northwind-langgraph" / "agent.yaml",
        operator,
        "dev",
        mode="locked",
        builder=port,
    )
    # The data-only config is the ADK one but for the agent's name.
    adk_config = json.loads(adk.files["agent.config.json"])
    port_config = json.loads(port.files["agent.config.json"])
    assert port_config.pop("name") == "northwind-support-langgraph"
    adk_config.pop("name")
    assert port_config == adk_config
    # The same artifacts, byte for byte.
    artifacts = {k: v for k, v in adk.files.items() if k.startswith("artifacts/")}
    assert {k: v for k, v in port.files.items() if k.startswith("artifacts/")} == artifacts
    # The runtime: the kit vendored, the LangGraph adapter, the labelled Dockerfile.
    assert "runtime/kit/proxy.py" in port.files and "runtime/langgraph_adapter.py" in port.files
    assert not any(name.startswith("runtime_tests/") for name in port.files)
    assert b'org.agentbreeder.runtime="runtime/langgraph@1.0.0"' in port.files["Dockerfile"]
    # A kit runtime's manifest carries `runtime` (spec §5.4); the ADK one does not.
    manifest = yaml.safe_load(result.manifest_path.read_text())
    assert manifest["runtime"] == {"ref": "runtime/langgraph@1.0.0", "profile": "kit"}
    adk_manifest = yaml.safe_load(
        (examples / "northwind" / "deploy" / "northwind-support.deploy.yaml").read_text()
    )
    assert "runtime" not in adk_manifest
    assert result.aliases == ["support-model", "support-embedding"]
    record = json.loads(result.build_record_path.read_text())
    assert record["runtime"] == "runtime/langgraph@1.0.0"


def test_langgraph_1_0_0_refuses_mcp(examples, identity_env):
    operator = _operator(identity_env, ["northwind-support-langgraph"])
    path = examples / "northwind-langgraph" / "agent.yaml"
    path.write_text(path.read_text() + "mcp:\n  - {ref: mcp/github@1.0.0, tools: [get_me]}\n")
    with pytest.raises(BuildError) as raised:
        build(path, operator, "dev", builder=Recorder())
    assert raised.value.code == "MCP_RUNTIME_UNSUPPORTED"
    assert "runtime/langgraph@1.0.0 has no `mcp` capability" in str(raised.value)


def test_a_tool_named_like_the_kit_is_refused(examples, identity_env):
    """`kit` and the runtime's modules are in its modules.json, so a tool module named so
    cannot shadow them (spec §5.3)."""
    from agentbreeder import tool_check

    assert tool_check.shadows("kit") == "shadows a runtime module"
    assert tool_check.shadows("langgraph_adapter") == "shadows a runtime module"


def test_the_port_is_not_a_standalone_example(tmp_path):
    """It needs Northwind beside it, so `agentbreeder example` does not offer it."""
    from agentbreeder import example

    assert "northwind-langgraph" not in example.names()
    with pytest.raises(example.ExampleError) as raised:
        example.copy("northwind-langgraph", tmp_path / "port")
    assert raised.value.code == "EXAMPLE_UNKNOWN"


def test_model_step_limit_is_a_model_code_for_invoke_and_try_it():
    from agentbreeder.center import tryit
    from agentbreeder.commands import invoke

    assert "MODEL_STEP_LIMIT" in invoke.MODEL_CODES and "MODEL_STEP_LIMIT" in tryit.MODEL_CODES


def test_northwinds_agent_role_can_read_what_kit_history_reads():
    """Live Docker run, 2026-11-19: every LangGraph turn failed `InsufficientPrivilege`, because
    the kit's `history()` reads `turns` and Northwind granted the agent INSERT only. The grant
    must name every column `history()` uses, and no more of the table."""
    import inspect
    import re

    from agentbreeder.runtime_kit import memory

    sql = (EXAMPLES / "northwind" / "stack" / "db" / "commerce.sql").read_text()
    grants = re.findall(r"GRANT SELECT \(([^)]*)\) ON turns TO support_agent;", sql)
    assert len(grants) == 1
    granted = {column.strip() for column in grants[0].split(",")}
    query = " ".join(inspect.getsource(memory.Memory.history).split())
    used = {"message", "reply", "customer", "session", "error", "created"}
    assert all(f"{column}" in query for column in used)
    assert granted == used
    assert "GRANT SELECT ON turns TO support_agent" not in sql
