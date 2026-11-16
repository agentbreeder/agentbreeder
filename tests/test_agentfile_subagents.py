"""`agent.yaml` `subagents:` and `limits:` (#1058, sub-agents spec §5)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from agentbreeder.agentfile import AgentFile, AgentFileError, load_agent_file

BASE = {
    "kind": "agent",
    "schema_version": 1,
    "name": "support-desk",
    "runtime": "runtime/adk@1.9.0",
    "model": {"alias": "flash", "max_model_calls": 6},
    "prompt": {"ref": "prompt/triage@1.0.0"},
    "tools": [{"ref": "tool/order-lookup@1.2.0"}, {"ref": "tool/customer-notes@1.0.0"}],
    "knowledge": [{"ref": "knowledge/help@2.0.0"}],
    "mcp": [{"ref": "mcp/github@1.0.0", "tools": ["get_issue", "list_issues"]}],
    "evals": {"ref": "eval/desk@1.0.0"},
    "subagents": [
        {
            "name": "billing",
            "description": "Refunds and invoices.",
            "model": {"alias": "pro", "reasoning": "medium", "max_model_calls": 4},
            "prompt": {"ref": "prompt/billing@1.1.0"},
            "tools": [{"ref": "tool/refund-request@1.0.0"}],
        },
        {
            "name": "returns",
            "description": "Return eligibility.",
            "model": {"alias": "flash"},
            "prompt": {"path": "src/prompts/returns.md"},
            "knowledge": True,
            "mcp": [{"ref": "mcp/github@1.0.0", "tools": ["get_issue"]}],
        },
    ],
}


def write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def refused(tmp_path: Path, data: dict) -> AgentFileError:
    with pytest.raises(AgentFileError) as caught:
        load_agent_file(write(tmp_path, data))
    return caught.value


def base() -> dict:
    return copy.deepcopy(BASE)


def test_valid_file_and_defaults(tmp_path):
    agent = load_agent_file(write(tmp_path, base()))
    billing, returns = agent.subagents
    assert billing.max_calls_per_turn == 2 and returns.knowledge
    assert returns.model.reasoning == "low" and returns.model.max_model_calls == 4
    assert agent.limits is None
    assert agent.turn_model_calls() == 12  # min(40, 6 + 6)


def test_without_subagents_nothing_changes(tmp_path):
    data = base()
    del data["subagents"]
    agent = load_agent_file(write(tmp_path, data))
    assert agent.subagents == [] and agent.turn_model_calls() is None
    assert agent.aliases() == ["flash"]
    assert "subagents" not in agent.model_dump(exclude_defaults=True)


def test_turn_cap_default_is_at_most_40_and_limits_wins(tmp_path):
    data = base()
    data["model"]["max_model_calls"] = 20
    data["subagents"][0]["model"]["max_model_calls"] = 20
    assert load_agent_file(write(tmp_path, data)).turn_model_calls() == 26
    data["limits"] = {"turn_model_calls": 40}
    assert load_agent_file(write(tmp_path, data)).turn_model_calls() == 40
    data["limits"] = {"turn_model_calls": 41}
    assert refused(tmp_path, data).code == "AGENT_INVALID"
    data["limits"] = {"turn_model_calls": 0}
    assert refused(tmp_path, data).code == "AGENT_INVALID"


def test_limits_only_with_subagents(tmp_path):
    data = base()
    del data["subagents"]
    data["limits"] = {"turn_model_calls": 10}
    error = refused(tmp_path, data)
    assert error.code == "AGENT_INVALID" and "limits: valid only with subagents" in str(error)


def test_at_most_five_subagents(tmp_path):
    data = base()
    one = data["subagents"][0]
    data["subagents"] = [{**copy.deepcopy(one), "name": f"s{i}"} for i in range(6)]
    error = refused(tmp_path, data)
    assert error.code == "AGENT_INVALID" and "subagents" in str(error)
    data["subagents"] = data["subagents"][:5]
    assert len(load_agent_file(write(tmp_path, data)).subagents) == 5


@pytest.mark.parametrize("name", ["Billing", "9x", "a" * 33, "bill-ing", ""])
def test_name_pattern(tmp_path, name):
    data = base()
    data["subagents"][0]["name"] = name
    error = refused(tmp_path, data)
    assert error.code == "AGENT_INVALID" and "subagents.0.name" in str(error)
    assert name not in str(error).split("Invalid", 1)[1] or name == ""


def test_names_unique(tmp_path):
    data = base()
    data["subagents"][1]["name"] = "billing"
    error = refused(tmp_path, data)
    assert "subagents[1].name: duplicates subagents[0]" in str(error)


@pytest.mark.parametrize("description", ["", "x" * 501])
def test_description_length(tmp_path, description):
    data = base()
    data["subagents"][0]["description"] = description
    assert "subagents.0.description" in str(refused(tmp_path, data))


@pytest.mark.parametrize("key", ["budget_usd", "rpm"])
def test_key_fields_refused_on_a_subagent(tmp_path, key):
    data = base()
    data["subagents"][0]["model"][key] = 987654
    error = refused(tmp_path, data)
    assert error.code == "SUBAGENT_FIELD_UNSUPPORTED"
    assert "subagents.0.model" in str(error) and "987654" not in str(error)


def test_depth_is_one(tmp_path):
    data = base()
    data["subagents"][0]["subagents"] = [copy.deepcopy(data["subagents"][1])]
    error = refused(tmp_path, data)
    assert error.code == "SUBAGENT_DEPTH" and "subagents.0" in str(error)


def test_customer_notes_never_on_a_subagent(tmp_path):
    data = base()
    data["subagents"][0]["tools"].append({"ref": "tool/customer-notes@1.0.0"})
    error = refused(tmp_path, data)
    assert error.code == "SUBAGENT_NOTES"
    assert "subagents[0].tools[1].ref" in str(error)


def test_unknown_field_refused(tmp_path):
    data = base()
    data["subagents"][0]["budget"] = 3
    error = refused(tmp_path, data)
    assert error.code == "AGENT_INVALID" and "subagents.0.budget" in str(error)


def test_max_calls_per_turn_bounds(tmp_path):
    data = base()
    data["subagents"][0]["max_calls_per_turn"] = 3
    load_agent_file(write(tmp_path, data))
    for bad in (0, 4):
        data["subagents"][0]["max_calls_per_turn"] = bad
        assert "subagents.0.max_calls_per_turn" in str(refused(tmp_path, data))


def test_at_most_twenty_tools(tmp_path):
    data = base()
    data["subagents"][0]["tools"] = [{"ref": f"tool/t{i}@1.0.0"} for i in range(21)]
    assert "subagents.0.tools" in str(refused(tmp_path, data))


@pytest.mark.parametrize(
    "mcp",
    [
        [{"ref": "mcp/other@1.0.0", "tools": ["get_issue"]}],  # server not granted
        [{"ref": "mcp/github@1.0.0", "tools": ["delete_repo"]}],  # tool not granted
    ],
)
def test_subagent_mcp_must_be_in_the_parents_grant(tmp_path, mcp):
    data = base()
    data["subagents"][1]["mcp"] = mcp
    error = refused(tmp_path, data)
    assert error.code == "SUBAGENT_MCP_UNGRANTED"
    assert "subagents[1].mcp[0]" in str(error)
    assert "delete_repo" not in str(error)


def test_subagent_mcp_another_version_is_one_version_per_name(tmp_path):
    data = base()
    data["subagents"][1]["mcp"] = [{"ref": "mcp/github@1.1.0", "tools": ["get_issue"]}]
    error = refused(tmp_path, data)
    assert "subagents[1].mcp[0].ref: duplicates mcp[0].ref (one version per name)" in str(error)


def test_subagent_mcp_duplicate_tool(tmp_path):
    data = base()
    data["subagents"][1]["mcp"] = [{"ref": "mcp/github@1.0.0", "tools": ["get_issue"] * 2}]
    assert "subagents[1].mcp[0].tools: duplicate tool" in str(refused(tmp_path, data))


def test_knowledge_true_needs_a_knowledge_set(tmp_path):
    data = base()
    del data["knowledge"]
    error = refused(tmp_path, data)
    assert "subagents[1].knowledge: needs a knowledge: entry" in str(error)


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.md"])
def test_prompt_path_inside_the_project(tmp_path, path):
    data = base()
    data["subagents"][1]["prompt"] = {"path": path}
    assert "subagents[1].prompt.path" in str(refused(tmp_path, data))


def test_bad_ref_names_the_location(tmp_path):
    data = base()
    data["subagents"][0]["tools"] = [{"ref": "prompt/x@1.0.0"}]
    error = refused(tmp_path, data)
    assert "subagents[0].tools[0].ref: must be a tool/name@X.Y.Z ref" in str(error)


def test_same_ref_in_parent_and_subagent_is_placed_once(tmp_path):
    data = base()
    data["subagents"][0]["tools"].append({"ref": "tool/order-lookup@1.2.0"})
    data["subagents"][1]["prompt"] = {"ref": "prompt/triage@1.0.0"}
    agent = load_agent_file(write(tmp_path, data))
    refs = agent.refs()
    assert refs.count("tool/order-lookup@1.2.0") == 1
    assert refs.count("prompt/triage@1.0.0") == 1


def test_another_version_of_the_same_name_is_refused(tmp_path):
    data = base()
    data["subagents"][0]["tools"].append({"ref": "tool/order-lookup@1.3.0"})
    error = refused(tmp_path, data)
    assert "subagents[0].tools[1].ref: duplicates tools[0].ref (one version per name)" in str(
        error
    )


def test_same_ref_twice_in_one_agent_is_still_refused(tmp_path):
    data = base()
    data["subagents"][0]["tools"].append({"ref": "tool/refund-request@1.0.0"})
    assert "subagents[0].tools[1].ref: duplicates" in str(refused(tmp_path, data))
    data = base()
    data["tools"].append({"ref": "tool/order-lookup@1.2.0"})
    assert "tools[2].ref: duplicates" in str(refused(tmp_path, data))


def test_refs_order(tmp_path):
    data = base()
    data["skills"] = [{"ref": "skill/tone@1.0.0"}]
    agent = load_agent_file(write(tmp_path, data))
    assert agent.refs() == [
        "runtime/adk@1.9.0",
        "prompt/triage@1.0.0",
        "knowledge/help@2.0.0",
        "tool/order-lookup@1.2.0",
        "tool/customer-notes@1.0.0",
        "mcp/github@1.0.0",
        "eval/desk@1.0.0",
        "prompt/billing@1.1.0",
        "tool/refund-request@1.0.0",
        "skill/tone@1.0.0",
    ]
    assert agent.paths() == ["src/prompts/returns.md"]


def test_paths_parent_first_and_deduplicated(tmp_path):
    data = base()
    data["prompt"] = {"path": "src/prompts/returns.md"}
    data["subagents"][0]["prompt"] = {"path": "src/prompts/billing.md"}
    agent = load_agent_file(write(tmp_path, data))
    assert agent.paths() == ["src/prompts/returns.md", "src/prompts/billing.md"]


def test_aliases_union_order(tmp_path):
    agent = load_agent_file(write(tmp_path, base()))
    assert agent.aliases() == ["flash", "pro"]
    assert agent.aliases(["embed", "pro"]) == ["flash", "pro", "embed"]


def test_eval_tag_threshold(tmp_path):
    data = base()
    data["evals"]["tag_threshold"] = 0.8
    assert load_agent_file(write(tmp_path, data)).evals.tag_threshold == 0.8
    data["evals"]["tag_threshold"] = 1.5
    assert "evals.tag_threshold" in str(refused(tmp_path, data))
    del data["evals"]["tag_threshold"]
    assert load_agent_file(write(tmp_path, data)).evals.tag_threshold is None


def test_an_older_cli_refuses_subagents_by_name():
    """An older CLI (the same strict model without the new fields) names the field and
    refuses the file, never silently drops it (spec §5.1)."""
    from pydantic import ValidationError, create_model

    fields = {
        name: (info.annotation, info)
        for name, info in AgentFile.model_fields.items()
        if name not in ("subagents", "limits")
    }
    older = create_model("Older", __config__=AgentFile.model_config, **fields)
    with pytest.raises(ValidationError) as caught:
        older.model_validate(base())
    assert ("subagents",) in [e["loc"] for e in caught.value.errors()]
