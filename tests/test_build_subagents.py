"""`agentbreeder build` with `subagents:` (#1058, sub-agents spec §5.3, §6), stub builder only."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
import yaml
from test_build import FakeBuilder, version
from test_build import operator as operator  # noqa: F401  (fixture)
from test_build import project as project  # noqa: F401  (fixture)
from test_build_template import IMAGE
from test_build_template import northwind as northwind  # noqa: F401  (fixture)
from test_mcp import GITHUB

from agentbreeder import gate
from agentbreeder.build import BuildError, build
from agentbreeder.operator_config import DeploymentContext

# What origin/main (b4a4d744, before sub-agents) wrote for these builds: sha256 of
# agent.config.json, the deploy template and the build record. Sub-agents must not change a
# byte for a file without them (plan §2, "unchanged happy path").
GOLDEN = {
    "northwind": {
        "config": "fcf0e6f4108bc3ed90659c4f16461526db73b9ecc63c506d74df78290e26a084",
        "template": "39aea2a7421d34812640fb151e7547659c29689895924bcfd0a1584ed8b8b7a9",
        "record": "af47addedf3ba5ad0b2ead4101fda409497512bd372a625582f278226337a6fa",
    },
    "helper": {
        "config": "37e915bb6cf771e25f68d35d322592306054e1359370f77d55f98ab2722be488",
        "template": "14a456a5abeb7223184fbcd052ff598834e6db4ce1514b297bc6dbe126a0e088",
        "record": "763bc7b8127398466a1f2f7f9447f35fab23a4f8e1441af0f5836e3e5b9b9e15",
    },
}


def _sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


class ConfigBuilder:
    def __init__(self):
        self.config = None

    def __call__(self, context, iidfile):
        self.config = (context / "agent.config.json").read_bytes()
        return IMAGE


def _hashes(result, builder) -> dict:
    record = json.loads(result.build_record_path.read_text())
    # The template as package.json writes it: an unset `guardrails` (#1053) is left out
    # (`AgentPackage.to_bytes`), so the field alone does not change these hashes.
    exclude = {n for n in ("guardrails", "runtime") if getattr(result.deploy, n) is None}
    template = result.deploy.model_dump(mode="json", exclude=exclude)
    return {
        "config": _sha(builder.config),
        "template": _sha(json.dumps(template, sort_keys=True)),
        "record": _sha(json.dumps(record, sort_keys=True)),
    }


def test_golden_northwind_is_byte_identical(northwind):  # noqa: F811
    project, operator = northwind
    builder = ConfigBuilder()
    result = build(project / "agent.yaml", operator.load(), "dev", mode="locked", builder=builder)
    assert _hashes(result, builder) == GOLDEN["northwind"]


def test_golden_minimal_agent_is_byte_identical(project, operator):  # noqa: F811
    builder = ConfigBuilder()
    result = build(project / "agent.yaml", operator.load(), "dev", builder=builder)
    record = json.loads(result.build_record_path.read_text())
    assert "subagents" not in record and "subagents" not in json.loads(builder.config)
    assert _hashes(result, builder) == GOLDEN["helper"]


def _capable(project, capabilities="[mcp, subagents]"):
    manifest = project / "registry" / "runtime" / "fake" / "1.0.0" / "manifest.yaml"
    text = manifest.read_text().replace("capabilities: [mcp]", f"capabilities: {capabilities}")
    manifest.write_text(text)


def _with_subagents(project, operator, **changes):
    version(project / "registry", "prompt", "billing", {"prompt.md": "Billing."})
    version(
        project / "registry",
        "tool",
        "refunds",
        {
            "tool.yaml": "functions:\n  - {name: refund_request, module: refunds}\n",
            "src/refunds.py": "",
        },
    )
    version(project / "registry", "mcp", "github", {"mcp.yaml": yaml.safe_dump(GITHUB)})
    (project / "src").mkdir(exist_ok=True)
    (project / "src" / "returns.txt").write_text("Returns.")
    agent = yaml.safe_load((project / "agent.yaml").read_text())
    agent["mcp"] = [{"ref": "mcp/github@1.0.0", "tools": ["list_issues", "create_issue"]}]
    agent["subagents"] = [
        {
            "name": "billing",
            "description": "Refunds.",
            "model": {"alias": "billing-model", "reasoning": "medium", "max_model_calls": 6},
            "prompt": {"ref": "prompt/billing@1.0.0"},
            "tools": [{"ref": "tool/refunds@1.0.0"}, {"ref": "tool/orders@1.0.0"}],
        },
        {
            "name": "returns",
            "description": "Returns.",
            "model": {"alias": "support-model"},
            "prompt": {"path": "src/returns.txt"},
            "knowledge": True,
            "mcp": [{"ref": "mcp/github@1.0.0", "tools": ["list_issues"]}],
            "max_calls_per_turn": 1,
        },
    ]
    agent.update(changes)
    (project / "agent.yaml").write_text(yaml.safe_dump(agent))
    env = operator.config["environments"]["dev"]
    env["models"] = [*env["models"], "billing-model"]
    return agent


def test_build_writes_subagents_config_aliases_and_record(project, operator):  # noqa: F811
    _capable(project)
    _with_subagents(project, operator)
    fake = FakeBuilder()
    result = build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    listing, config = fake.contexts[0]
    assert config["subagents"] == [
        {
            "name": "billing",
            "description": "Refunds.",
            "model": {"alias": "billing-model", "reasoning": "medium", "max_model_calls": 6},
            "max_calls_per_turn": 2,
            "prompt": {"dir": "artifacts/prompt/billing"},
            "tools": [
                {"name": "refunds", "version": "1.0.0", "dir": "artifacts/tool/refunds"},
                {"name": "orders", "version": "1.0.0", "dir": "artifacts/tool/orders"},
            ],
            "mcp": [],
            "knowledge": False,
        },
        {
            "name": "returns",
            "description": "Returns.",
            "model": {"alias": "support-model", "reasoning": "low", "max_model_calls": 4},
            "max_calls_per_turn": 1,
            "prompt": {"dir": "artifacts/paths/src__returns.txt"},
            "tools": [],
            "mcp": [{"name": "github", "version": "1.0.0", "tools": ["list_issues"]}],
            "knowledge": True,
        },
    ]
    assert config["limits"] == {"turn_model_calls": 10}  # 4 + 6
    # The path prompt is renamed for the runtime; the shared tool is placed once.
    assert "artifacts/paths/src__returns.txt/prompt.md" in listing
    assert listing.count("artifacts/tool/orders/src/orders.py") == 1
    # One key: parent, each sub-agent, then the embedding alias, each once (spec §6).
    expected = ["support-model", "billing-model", "support-embedding"]
    assert result.aliases == expected
    assert list(result.deploy.aliases) == expected
    assert result.deploy.mcp_access[0].tools == ["list_issues", "create_issue"]
    record = json.loads(result.build_record_path.read_text())
    assert record["subagents"] == [
        {"name": "billing", "alias": "billing-model"},
        {"name": "returns", "alias": "support-model"},
    ]
    lock = (project / "agent.lock").read_text()
    assert "prompt/billing@1.0.0" in lock and "tool/refunds@1.0.0" in lock


def test_limits_reach_the_config(project, operator):  # noqa: F811
    _capable(project)
    _with_subagents(project, operator, limits={"turn_model_calls": 7})
    fake = FakeBuilder()
    build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    assert fake.contexts[0][1]["limits"] == {"turn_model_calls": 7}


def test_runtime_without_subagents_is_refused_before_docker(project, operator):  # noqa: F811
    _with_subagents(project, operator)
    fake = FakeBuilder()
    with pytest.raises(BuildError) as caught:
        build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    assert caught.value.code == "RUNTIME_NO_SUBAGENTS" and fake.contexts == []


def _clash(project, operator, mutate):
    _capable(project)
    agent = _with_subagents(project, operator)
    mutate(agent)
    (project / "agent.yaml").write_text(yaml.safe_dump(agent))
    with pytest.raises(BuildError) as caught:
        build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    assert caught.value.code == "SUBAGENT_NAME_CLASH"
    return str(caught.value)


def test_ask_name_clashes_with_a_parent_function(project, operator):  # noqa: F811
    version(
        project / "registry",
        "tool",
        "asker",
        {"tool.yaml": "functions:\n  - {name: ask_billing, module: asker}\n", "src/a.py": ""},
    )
    message = _clash(project, operator, lambda a: a["tools"].append({"ref": "tool/asker@1.0.0"}))
    assert "subagents[0].name" in message and "tool/asker@1.0.0" in message


def test_ask_name_clashes_with_a_parent_mcp_tool(project, operator):  # noqa: F811
    # The model sees MCP tools as <server>_<tool>: server "ask", tool "list_issues".
    version(project / "registry", "mcp", "ask", {"mcp.yaml": yaml.safe_dump(GITHUB)})

    def mutate_server(agent):
        agent["mcp"].append({"ref": "mcp/ask@1.0.0", "tools": ["list_issues"]})
        agent["subagents"][0]["name"] = "list_issues"

    message = _clash(project, operator, mutate_server)
    assert "ask_list_issues" in message and "mcp/ask@1.0.0" in message


def test_ask_name_clashes_with_the_agents_own_name(project, operator, identity_env):  # noqa: F811
    def mutate(agent):
        agent["name"] = "ask-billing"

    env = operator.config["environments"]["dev"]
    env["agents"]["ask-billing"] = copy.deepcopy(env["agents"]["helper"])
    assert "the agent's own name" in _clash(project, operator, mutate)


def test_tools_clash_inside_one_subagent(project, operator):  # noqa: F811
    version(
        project / "registry",
        "tool",
        "kb",
        {"tool.yaml": "functions:\n  - {name: search_knowledge_base, module: kb}\n", "s/k.py": ""},
    )

    def mutate(agent):
        agent["subagents"][1]["tools"] = [{"ref": "tool/kb@1.0.0"}]

    message = _clash(project, operator, mutate)
    assert "subagents[1]" in message and "search_knowledge_base" in message


def test_reserved_adk_names_inside_a_subagent(project, operator):  # noqa: F811
    version(
        project / "registry",
        "tool",
        "xfer",
        {"tool.yaml": "functions:\n  - {name: transfer_to_agent, module: xfer}\n", "s/x.py": ""},
    )

    def mutate(agent):
        agent["subagents"][0]["tools"] = [{"ref": "tool/xfer@1.0.0"}]

    message = _clash(project, operator, mutate)
    assert "transfer_to_agent" in message and "ADK built-in" in message


def test_tool_modules_unique_across_agents(project, operator):  # noqa: F811
    _capable(project)
    version(
        project / "registry",
        "tool",
        "copy",
        {"tool.yaml": "functions:\n  - {name: other, module: refunds}\n", "src/c.py": ""},
    )
    agent = _with_subagents(project, operator)
    agent["subagents"][1]["tools"] = [{"ref": "tool/copy@1.0.0"}]
    (project / "agent.yaml").write_text(yaml.safe_dump(agent))
    with pytest.raises(BuildError) as caught:
        build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    assert caught.value.code == "TOOL_MODULE_CONFLICT"


def test_subagent_alias_outside_the_environment_is_model_not_allowed(project, operator):  # noqa: F811
    """No gate change (spec §8.1): the sub-agent alias is in model_access.aliases, and the
    existing check denies it."""
    _capable(project)
    _with_subagents(project, operator)
    result = build(project / "agent.yaml", operator.load(), "dev", builder=FakeBuilder())
    from agentbreeder.manifest import load_manifest

    manifest = load_manifest(result.manifest_path)
    assert "billing-model" in manifest.model_access.aliases
    env = operator.config["environments"]["dev"]
    env["models"] = [m for m in env["models"] if m != "billing-model"]
    denied = gate.IdentityGate(operator.load())._model_access(
        manifest.model_access, DeploymentContext(environment="dev", target="local")
    )
    assert denied is not None and denied[0] == "MODEL_NOT_ALLOWED"
    assert "billing-model" in denied[1]
