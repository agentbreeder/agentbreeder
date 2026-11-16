"""runtime/adk@1.3.0 and 1.4.0 in the console (#1062), through the fake CLI: a failed model call
in Try it keeps its code, the runtime's next step, the correlation ID and a rate limit's wait; a
reply's inspector shows a gateway fallback, refused unknown tool names and cached prompt tokens;
a trace (Try it and the Traces tab) shows its root span's facts, a guardrail block included; the
cost tables show cached prompt tokens."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_cost import MODEL, home_card, open_home
from .test_shell import DEPLOYMENTS
from .test_traces import ROWS as TRACE_ROWS
from .test_traces import items, open_traces
from .test_try import open_trace, open_try, send, turns

pytestmark = pytest.mark.ui

CORRELATION = "e" * 32
RATE_LIMITED = {
    "error": "The gateway's rate limit for this agent was reached (retry in 7 s). Wait and send "
    "the message again, or raise model.rpm in agent.yaml (within the environment's ceiling) and "
    "deploy again.",
    "code": "MODEL_RATE_LIMITED",
    "correlation_id": CORRELATION,
    "trace_id": "ab" * 16,
    "retry_after_s": 7,
}
BLOCKED = {
    "error": "A guardrail on the model gateway blocked this turn: the message or the model's "
    "answer had content it does not allow, so there is no reply. Sending it again won't help; "
    "rephrase the message without that content.",
    "code": "MODEL_BLOCKED",
    "correlation_id": CORRELATION,
    "trace_id": "ab" * 16,
    "retry_after_s": None,
}
ROOT = {
    "fallbacks": 1,
    "served_by": ["chat-backup"],
    "cached_prompt_tokens": 300,
    "unknown_tools": ["delete_everything"],
    "guardrail": {"action": "blocked", "name": "presidio-pii", "entity": "US_SSN"},
}


def test_a_rate_limited_turn_names_its_code_wait_and_correlation_id(page, console):
    console.scenario(deployments=DEPLOYMENTS, invoke={"status": 429, "fields": RATE_LIMITED})
    open_try(page, console)
    send(page, "hi")
    turn = turns(page).first
    alert = turn.get_by_role("alert")
    expect(alert).to_contain_text(
        "The model call failed, so there is no reply: The agent answered 429:"
    )
    expect(alert).to_contain_text("raise model.rpm in agent.yaml")
    expect(alert).to_contain_text("[MODEL_RATE_LIMITED]")
    expect(alert).to_contain_text("(retry in 7 s)")  # the runtime's own words; not said twice
    expect(alert).not_to_contain_text("asks to wait")
    expect(alert).to_contain_text("Tools may already have run in this turn")
    expect(turn).to_contain_text(f"Correlation ID {CORRELATION}")
    expect(turn).to_contain_text("open the agent's Logs tab")
    expect(turn.get_by_role("button", name="Copy correlation ID")).to_be_visible()


def test_a_guardrail_block_is_model_blocked_with_no_wait(page, console):
    console.scenario(deployments=DEPLOYMENTS, invoke={"status": 422, "fields": BLOCKED})
    open_try(page, console)
    send(page, "my ssn is 123-45-6789")
    turn = turns(page).first
    alert = turn.get_by_role("alert")
    expect(alert).to_contain_text("The agent answered 422: A guardrail on the model gateway")
    expect(alert).to_contain_text("[MODEL_BLOCKED]")
    expect(alert).not_to_contain_text("wait")
    expect(alert).not_to_contain_text("second turn")  # sending it again won't help
    expect(turn).to_contain_text(f"Correlation ID {CORRELATION}")


def test_a_reply_shows_its_fallback_unknown_tools_and_cached_tokens(page, console):
    fields = {k: ROOT[k] for k in ("fallbacks", "served_by", "unknown_tools")}
    fields["cached_prompt_tokens"] = 1500
    console.scenario(deployments=DEPLOYMENTS, invoke={"fields": fields}, traces={"root": ROOT})
    open_try(page, console)
    send(page, "hi")
    turn = turns(page).first
    details = turn.locator("details.inspector")
    expect(details.locator("summary")).to_contain_text("1,812 in (1,500 cached), 64 out tokens")
    details.locator("summary").click()
    facts = details.locator("dl.facts")
    expect(facts).to_contain_text(
        "Unknown tools" + "The model asked for a tool this agent does not have "
        "(delete_everything); the runtime refused it, so it is not a tool call."
    )
    expect(facts).to_contain_text(
        "Fallback" + "A gateway fallback served this turn (served by chat-backup)."
    )
    trace = open_trace(turn)
    expect(trace).to_contain_text("391 tokens (300 cached)")
    expect(trace).to_contain_text("Blocked by guardrail presidio-pii (US_SSN).")
    expect(trace).to_contain_text("A gateway fallback served this turn (served by chat-backup).")


def test_an_older_runtimes_reply_shows_none_of_them(page, console):
    console.scenario(deployments=DEPLOYMENTS)
    open_try(page, console)
    send(page, "hi")
    details = turns(page).first.locator("details.inspector")
    expect(details.locator("summary")).to_contain_text("1,812 in, 64 out tokens")
    details.locator("summary").click()
    facts = details.locator("dl.facts")
    expect(facts).to_contain_text("Tools")
    for word in ("Unknown tools", "Fallback", "cached"):
        expect(facts).not_to_contain_text(word)


def test_the_traces_tab_shows_each_traces_root_facts(page, console):
    listed = {
        "trace_id": "ab" * 16, "attribution": "exact", "start_time": "2026-11-14T03:10:33+00:00",
        "latency_ms": 41, "status": "error", "models": ["gemini-2.5-flash"],
        "tokens": {"prompt": 321, "completion": 70, "total": 391}, "tool_calls": [],
        "spans": [], "spans_truncated": False, **ROOT,
    }  # fmt: skip
    console.scenario(deployments=TRACE_ROWS, traces={"list": [listed]})
    open_traces(page, console)
    first = items(page).first
    expect(first).to_contain_text("321 in (300 cached), 70 out tokens")
    expect(first).to_contain_text("A gateway fallback served this turn (served by chat-backup).")
    expect(first).to_contain_text("the runtime refused it, so it is not a tool call.")
    expect(first).to_contain_text("Blocked by guardrail presidio-pii (US_SSN).")


def test_cost_tables_show_cached_prompt_tokens(page, console):
    model = {**MODEL, "prompt_tokens": 3000, "cached_prompt_tokens": 1200}
    total = {k: v for k, v in model.items() if k not in ("model_group", "model")}
    console.scenario(cost={"models": [model], "total": total, "keys": []})
    open_home(page, console)
    table = home_card(page).get_by_role("table", name="Spend per model")
    expect(table.locator("thead")).to_contain_text("Cached in")
    expect(table.locator("tbody tr").first).to_contain_text("1,200")


def test_a_cost_without_cached_tokens_shows_a_dash(page, console):
    console.scenario(cost={"models": [MODEL], "keys": []})
    open_home(page, console)
    table = home_card(page).get_by_role("table", name="Spend per model")
    expect(table.locator("thead")).to_contain_text("Cached in")
    expect(table.locator("tbody tr").first.locator("td.num").nth(2)).to_have_text("-")
