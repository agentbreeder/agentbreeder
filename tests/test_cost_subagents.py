"""`agentbreeder cost` grouped by sub-agent (#1058 EV, sub-agents spec §10.2): LiteLLM keeps a
request's `metadata.tags` in the spend row's `request_tags` (the S0 record,
`docs/superpowers/verification/1058-S0-litellm-tags.md`). Tested on fixture rows: no runtime
sends tags yet."""

# ruff: noqa: F811 - the fixtures are imported from test_cost

from __future__ import annotations

import json
import re

import pytest
from test_cost import (  # noqa: F401
    DEPLOYED,
    MASTER,
    REMOVED,
    gateway_env,
    records,
    row,
    run_cli,
    spend,
    state_dir,
)

from agentbreeder import agentfile, gateway_keys, llmcost
from agentbreeder.center import cost as center_cost
from agentbreeder.gateway_keys import LiteLLM

TAG = gateway_keys.SUBAGENT_TAG
# What LiteLLM 1.106.2 added to every row's `request_tags` in the S0 probe.
NOISE = ["User-Agent: Python-urllib", "User-Agent: Python-urllib/3.12"]
ALIAS = gateway_keys.PREFIX + DEPLOYED
OTHER = gateway_keys.PREFIX + REMOVED


def tagged(alias, agent, spend_usd=0.001, **extra):
    """A spend row as LiteLLM writes it: our tag first, then its own `User-Agent` tags."""
    tags = ([TAG + agent] if agent is not None else []) + NOISE
    return row(alias, spend_usd, request_tags=tags, **extra)


def by_role(key):
    return {(r["role"], r["name"]): r for r in key["subagents"]}


# --- reading the tag from a spend row ----------------------------------------------------------


def test_the_tag_name_is_the_agent_files_sub_agent_name():
    assert gateway_keys.SUBAGENT_TAG == "agentbreeder-subagent:"
    assert gateway_keys.SUBAGENT_NAME == agentfile.SUBAGENT_NAME.strip("^$")
    assert gateway_keys.PARENT_TAG == "-"
    assert not re.fullmatch(gateway_keys.SUBAGENT_NAME, gateway_keys.PARENT_TAG)


def test_spend_logs_keep_only_our_tags(spend):
    spend.rows = [
        tagged("x", "billing"),
        tagged("x", "-"),
        row("x", request_tags=NOISE),
        row("x"),
    ]
    rows, _ = LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert [r.get("request_tags") for r in rows] == [[TAG + "billing"], [TAG + "-"], None, None]
    # An untagged row is what it was before this change: no new key.
    assert set(rows[3]) == set(rows[2])


@pytest.mark.parametrize(
    "tags",
    [
        "agentbreeder-subagent:billing",  # not a list
        {"agentbreeder-subagent:billing": 1},
        [None, 7, True, ["agentbreeder-subagent:billing"], {"a": 1}],
        ["agentbreeder-subagent:"],
        ["agentbreeder-subagent:Billing"],
        ["agentbreeder-subagent:9lives"],
        ["agentbreeder-subagent:" + "a" * 33],
        ["agentbreeder-subagent:billing\n"],
        ["agentbreeder-subagent:billing\x1b[2J"],
        ["agentbreeder-subagent:bill ing"],
        ["agentbreeder-subagent:--"],
        [" agentbreeder-subagent:billing"],
        ["AGENTBREEDER-SUBAGENT:billing"],
        ["subagent:billing"],
    ],
)
def test_a_tag_that_is_not_a_validated_name_is_dropped(spend, tags):
    spend.rows = [row("x", request_tags=tags)]
    (kept,), _ = LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert "request_tags" not in kept


def test_spend_logs_keep_at_most_two_distinct_tags(spend):
    many = [TAG + name for name in ("a", "a", "b", "c", "d")] + NOISE * 500
    spend.rows = [row("x", request_tags=many)]
    (kept,), _ = LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert kept["request_tags"] == [TAG + "a", TAG + "b"]


def test_only_the_first_tags_of_a_row_are_read(spend):
    """A caller's tags come before LiteLLM's own (S0), so a bounded read is enough."""
    limit = gateway_keys._READ_TAGS
    spend.rows = [
        row("x", request_tags=["junk"] * (limit - 1) + [TAG + "billing"]),
        row("x", request_tags=["junk"] * limit + [TAG + "billing"]),
    ]
    (kept, cut), _ = LiteLLM(spend.url, MASTER).spend_logs("s", "e")
    assert kept["request_tags"] == [TAG + "billing"]
    assert "request_tags" not in cut


# --- the summary -------------------------------------------------------------------------------


def test_an_untagged_window_has_no_subagents_field():
    rows = [row(ALIAS, 0.002, request_id="r1"), row(OTHER, 0.004, request_tags=NOISE)]
    result = llmcost.summarize(rows, records())
    assert all("subagents" not in key for key in result["keys"])
    # And the rest is what an untagged summary was before.
    assert result == llmcost.summarize([{**r, "request_tags": None} for r in rows], records())


def test_summary_groups_a_key_by_sub_agent():
    rows = [
        tagged(ALIAS, "-", 0.004, request_id="p1"),
        tagged(ALIAS, "-", 0.001, request_id="p2"),
        tagged(ALIAS, "billing", 0.002, request_id="b1"),
        tagged(ALIAS, "billing", 0.003, request_id="b2", status="failure"),
        tagged(ALIAS, "returns", 0.0005, request_id="t1"),
        row(OTHER, 0.01, request_id="o1"),
    ]
    result = llmcost.summarize(rows, records())
    key = next(k for k in result["keys"] if k["key_alias"] == ALIAS)
    assert [(r["role"], r["name"]) for r in key["subagents"]] == [
        ("parent", None),
        ("subagent", "billing"),
        ("subagent", "returns"),
    ]
    found = by_role(key)
    assert found["parent", None]["calls"] == 2
    assert found["parent", None]["cost_usd"] == pytest.approx(0.005)
    assert found["subagent", "billing"]["calls"] == 2
    assert found["subagent", "billing"]["failed"] == 1
    assert found["subagent", "billing"]["cost_usd"] == pytest.approx(0.005)
    assert found["subagent", "returns"]["prompt_tokens"] == 10
    # The rows of a key add up to the key.
    for field in ("calls", "failed", "prompt_tokens", "completion_tokens", "unpriced", "local"):
        assert sum(r[field] for r in key["subagents"]) == key[field]
    assert sum(r["cost_usd"] for r in key["subagents"]) == pytest.approx(key["cost_usd"])
    # A key with no tagged call is unchanged.
    assert "subagents" not in next(k for k in result["keys"] if k["key_alias"] == OTHER)
    # The totals and the model grouping do not depend on tags.
    plain = llmcost.summarize([{**r, "request_tags": None} for r in rows], records())
    assert (result["total"], result["models"]) == (plain["total"], plain["models"])


def test_sub_agents_are_sorted_by_cost_then_calls_then_name():
    rows = [
        row(ALIAS, 0.9, request_id="u1"),
        tagged(ALIAS, "zeta", 0.002, request_id="z1"),
        tagged(ALIAS, "alpha", 0.002, request_id="a1"),
        tagged(ALIAS, "small", 0.001, request_id="s1"),
        tagged(ALIAS, "-", 0.0001, request_id="p1"),
        tagged(ALIAS, "free_a", None, request_id="f1"),
        tagged(ALIAS, "free_b", None, request_id="f2"),
        tagged(ALIAS, "free_b", None, request_id="f3"),
    ]
    (key,) = llmcost.summarize(rows, records())["keys"]
    assert [(r["role"], r["name"]) for r in key["subagents"]] == [
        ("parent", None),
        ("subagent", "alpha"),
        ("subagent", "zeta"),
        ("subagent", "small"),
        ("subagent", "free_b"),  # no priced call: 0 for the order, more calls first
        ("subagent", "free_a"),
        ("untagged", None),
    ]


def test_more_names_than_the_cap_go_to_one_other_row(monkeypatch):
    """A tag is the caller's choice: one key cannot make the list as long as its call count."""
    monkeypatch.setattr(llmcost, "MAX_SUBAGENT_ROWS", 3)
    rows = [tagged(ALIAS, f"n{i}", 0.001, request_id=f"r{i}") for i in range(6)]
    rows += [tagged(ALIAS, "n0", 0.001, request_id="again"), tagged(ALIAS, "-", 0.001)]
    rows += [row(ALIAS, 0.001, request_id="plain")]
    (key,) = llmcost.summarize(rows, records())["keys"]
    assert [(r["role"], r["name"], r["calls"]) for r in key["subagents"]] == [
        ("parent", None, 1),
        ("subagent", "n0", 2),
        ("subagent", "n1", 1),
        ("subagent", "n2", 1),
        ("other", None, 3),
        ("untagged", None, 1),
    ]
    assert sum(r["calls"] for r in key["subagents"]) == key["calls"] == 9
    assert sum(r["cost_usd"] for r in key["subagents"]) == pytest.approx(key["cost_usd"])


def test_the_default_cap_is_above_what_an_agent_file_allows():
    assert llmcost.MAX_SUBAGENT_ROWS >= 10 * agentfile.MAX_SUBAGENTS


def test_an_unknown_cost_call_is_counted_as_unpriced_never_as_zero():
    rows = [
        tagged(ALIAS, "billing", None, request_id="b1"),
        tagged(ALIAS, "billing", 0.002, request_id="b2"),
        tagged(ALIAS, "returns", None, request_id="t1"),
        tagged(ALIAS, "local_one", 0, request_id="l1", custom_llm_provider="ollama_chat"),
    ]
    (key,) = llmcost.summarize(rows, records())["keys"]
    found = by_role(key)
    assert (found["subagent", "billing"]["unpriced"], found["subagent", "billing"]["calls"]) == (
        1,
        2,
    )
    assert found["subagent", "billing"]["cost_usd"] == pytest.approx(0.002)
    assert found["subagent", "returns"]["unpriced"] == 1
    assert found["subagent", "local_one"]["local"] == 1
    assert found["subagent", "local_one"]["unpriced"] == 0


def test_two_different_tags_on_one_call_are_not_attributed():
    rows = [
        row(ALIAS, 0.003, request_id="c1", request_tags=[TAG + "billing", TAG + "returns"]),
        row(ALIAS, 0.001, request_id="c2", request_tags=[TAG + "billing", TAG + "billing"]),
    ]
    (key,) = llmcost.summarize(rows, records())["keys"]
    found = by_role(key)
    assert found["untagged", None]["calls"] == 1
    assert found["untagged", None]["cost_usd"] == pytest.approx(0.003)
    assert found["subagent", "billing"]["calls"] == 1


def test_a_tag_is_grouped_inside_its_key_only():
    """A tag is the caller's claim (S0, item 4): another key sending this key's tag is billed
    and grouped under its own key."""
    rows = [
        tagged(ALIAS, "billing", 0.002, request_id="a1"),
        tagged(OTHER, "billing", 0.5, request_id="o1"),
    ]
    result = llmcost.summarize(rows, records())
    mine = next(k for k in result["keys"] if k["key_alias"] == ALIAS)
    assert mine["cost_usd"] == pytest.approx(0.002)
    assert by_role(mine)["subagent", "billing"]["cost_usd"] == pytest.approx(0.002)


@pytest.mark.parametrize(
    "tags",
    [
        "agentbreeder-subagent:billing",
        [TAG + "Billing"],
        [TAG + "billing\x1b[2J"],
        [7, None, {"a": 1}],
        [TAG],
    ],
)
def test_summary_checks_the_tag_itself(tags):
    """`summarize` does not rely on `gateway_keys` having cleaned the row."""
    (key,) = llmcost.summarize([row(ALIAS, 0.001, request_tags=tags)], records())["keys"]
    assert "subagents" not in key


def test_an_eval_key_and_an_unknown_key_are_grouped_too():
    rows = [
        tagged(gateway_keys.PREFIX + "eval-run1", "billing", 0.001, request_id="e1"),
        tagged(gateway_keys.PREFIX + "c" * 32, "-", 0.001, request_id="x1"),
    ]
    result = llmcost.summarize(rows, records())
    assert {k["kind"]: [r["role"] for r in k["subagents"]] for k in result["keys"]} == {
        "eval": ["subagent"],
        "unknown": ["parent"],
    }


# --- the command -------------------------------------------------------------------------------


def seed(spend):
    spend.rows = [
        tagged(ALIAS, "-", 0.004, request_id="p1"),
        tagged(ALIAS, "billing", 0.002, request_id="b1"),
        tagged(ALIAS, "billing", None, request_id="b2"),
        tagged(ALIAS, "returns", None, request_id="t1"),
        row(ALIAS, 0.001, request_id="u1", request_tags=NOISE),
        row(OTHER, 0.01, request_id="o1"),
    ]


def test_cost_json_has_sub_agents_only_for_a_tagged_key(gateway_env, spend, state_dir, capsys):
    seed(spend)
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--json")
    assert code == 0, err
    result = json.loads(out)
    assert result["schema_version"] == 1
    keys = {k["key_alias"]: k for k in result["keys"]}
    assert "subagents" not in keys[OTHER]
    found = [(r["role"], r["name"], r["calls"], r["unpriced"]) for r in keys[ALIAS]["subagents"]]
    assert found == [
        ("parent", None, 1, 0),
        ("subagent", "billing", 2, 1),
        ("subagent", "returns", 1, 1),
        ("untagged", None, 1, 0),
    ]  # fmt: skip
    assert MASTER not in out + err


def test_cost_table_by_subagent(gateway_env, spend, state_dir, capsys):
    seed(spend)
    code, out, err = run_cli(capsys, "--state-dir", str(state_dir), "--by", "subagent")
    assert code == 0, err
    lines = out.splitlines()
    assert lines[1].split() == ["DEPLOYMENT", "NAME", "AGENT", "CALLS", "FAILED", "TOKENS",
                                "IN/OUT", "CACHED", "IN", "USD"]  # fmt: skip
    assert lines[2].split() == [REMOVED, "old", "(not", "tagged)", "1", "0", "10/20", "0",
                                "0.010000"]  # fmt: skip
    assert lines[3].split() == [DEPLOYED, "hello", "(parent)", "1", "0", "10/20", "0", "0.004000"]
    assert lines[4].split() == [DEPLOYED, "hello", "billing", "2", "0", "20/40", "0",
                                "0.002000", "+", "1", "unknown"]  # fmt: skip
    # No priced call at all: unknown, never 0.000000.
    assert lines[5].split() == [DEPLOYED, "hello", "returns", "1", "0", "10/20", "0", "unknown"]
    assert lines[6].split() == [DEPLOYED, "hello", "(not", "tagged)", "1", "0", "10/20", "0",
                                "0.001000"]  # fmt: skip
    assert lines[7].split() == ["Total", "6", "0", "60/120", "0", "0.017000", "+", "2", "unknown"]
    assert "2 call(s) had no price" in out
    assert "the gateway does not check" in out
    assert "not a bill" in out


def test_the_other_tables_are_unchanged_by_tags(gateway_env, spend, state_dir, capsys):
    seed(spend)
    _, tagged_out, _ = run_cli(capsys, "--state-dir", str(state_dir))
    _, tagged_models, _ = run_cli(capsys, "--state-dir", str(state_dir), "--by", "model")
    for item in spend.rows:
        item.pop("request_tags", None)
    _, plain_out, _ = run_cli(capsys, "--state-dir", str(state_dir))
    _, plain_models, _ = run_cli(capsys, "--state-dir", str(state_dir), "--by", "model")
    assert (tagged_out, tagged_models) == (plain_out, plain_models)
    assert "the gateway does not check" not in tagged_out


def test_cost_table_shows_the_other_row_and_a_mixed_local_row(
    gateway_env, spend, state_dir, capsys, monkeypatch
):
    monkeypatch.setattr(llmcost, "MAX_SUBAGENT_ROWS", 1)
    spend.rows = [
        tagged(ALIAS, "billing", 0, request_id="l1", custom_llm_provider="ollama"),
        tagged(ALIAS, "billing", None, request_id="l2"),
        tagged(ALIAS, "returns", 0.002, request_id="t1"),
        tagged(OTHER, "billing", 0.5, request_id="o1"),
    ]
    code, out, _ = run_cli(capsys, "--state-dir", str(state_dir), "--by", "subagent")
    assert code == 0
    lines = out.splitlines()
    # The same name under two keys is two rows.
    assert lines[2].split() == [REMOVED, "old", "billing", "1", "0", "10/20", "0", "0.500000"]
    # One local call and one with no price: no priced call, so unknown (never 0.000000).
    assert lines[3].split() == [DEPLOYED, "hello", "billing", "2", "0", "20/40", "0", "unknown"]
    assert lines[4].split() == [DEPLOYED, "hello", "(other", "tags)", "1", "0", "10/20", "0",
                                "0.002000"]  # fmt: skip
    assert "1 call(s) went to a local model" in out and "1 call(s) had no price" in out


def test_cost_by_subagent_with_no_calls(gateway_env, spend, state_dir, capsys):
    code, out, _ = run_cli(capsys, "--state-dir", str(state_dir), "--by", "subagent")
    assert code == 0
    assert "No model calls by AgentBreeder keys in this window." in out
    assert "the gateway does not check" not in out


def test_cost_by_subagent_shows_a_local_model_without_a_price(
    gateway_env, spend, state_dir, capsys
):
    spend.rows = [tagged(ALIAS, "billing", 0, request_id="l1", custom_llm_provider="ollama")]
    code, out, _ = run_cli(capsys, "--state-dir", str(state_dir), "--by", "subagent")
    assert code == 0
    assert llmcost.LOCAL_LABEL in out.splitlines()[2]
    assert "unknown" not in out.splitlines()[2]


def test_the_console_still_accepts_an_answer_with_sub_agents():
    """`center/cost.py` passes on known keys only, so the new field does not reach the browser
    and does not make the answer COST_INVALID."""
    summary = llmcost.summarize([tagged(ALIAS, "billing", 0.002, request_id="b1")], records())
    data = {
        "schema_version": 1,
        "since": "2026-11-01",
        "until": "2026-11-07",
        "gateway": "litellm",
        "currency": "USD",
        "source": "litellm spend logs",
        "truncated": False,
        **summary,
    }
    assert "subagents" in data["keys"][0]
    answer, problem = center_cost.cost_answer(
        data, {"days": 7, "since": "2026-11-01", "until": "2026-11-07"}
    )
    assert problem is None
    assert "subagents" not in answer["keys"][0]
    assert answer["keys"][0]["calls"] == 1
