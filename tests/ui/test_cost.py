"""LLM cost (#906, console half): Home's cost card for the project in scope and an agent's Overview
card, over the fake CLI's `cost --json`. Amounts show with their currency, the window and the time
they were read, labelled as LiteLLM spend, not a bill; a refusal shows the CLI's next step."""

from __future__ import annotations

import json
import re
from datetime import date

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_agents import scenario

pytestmark = pytest.mark.ui

AS_OF = re.compile(r"as of \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC")
MODEL = {
    "model_group": "chat", "model": "gemini/gemini-2.5-flash", "calls": 3, "failed": 0,
    "prompt_tokens": 30, "completion_tokens": 60, "cost_usd": 0.0012, "unpriced": 0,
}  # fmt: skip


def cost_calls(console) -> list[list[str]]:
    path = console.state / "cost-calls.jsonl"
    lines = path.read_text().splitlines() if path.is_file() else []
    return [json.loads(line) for line in lines if line.endswith("]")]


def wait_for_calls(page, console, count: int) -> list[list[str]]:
    """At least `count` cost reads; a read starts in an effect after the render."""
    for _ in range(200):
        calls = cost_calls(console)
        if len(calls) >= count:
            return calls
        page.wait_for_timeout(100)
    raise AssertionError(f"fewer than {count} cost calls: {cost_calls(console)}")


def home_card(page):
    return page.get_by_role("region", name="LLM cost", exact=True)


def open_home(page, console):
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    page.get_by_role("heading", level=1, name="Home").wait_for()


def test_home_shows_the_projects_spend_per_model(page, console):
    open_home(page, console)
    card = home_card(page)
    expect(card).to_contain_text("USD 0.003108")
    expect(card).to_contain_text("7 calls, 1 failed")
    expect(card).to_contain_text(AS_OF)
    expect(card).to_contain_text("(UTC days)")
    expect(card).to_contain_text("LiteLLM spend")
    expect(card).to_contain_text("not a bill")
    table = card.get_by_role("table", name="Spend per model")
    expect(table).to_contain_text("chat (gemini/gemini-2.5-flash)")
    expect(table).to_contain_text("1,400")
    argv = wait_for_calls(page, console, 1)[0]
    assert argv[:2] == ["cost", "--stack"] and argv[-1] == "--json"
    assert argv[argv.index("--environment") + 1] == "dev"
    first = len(cost_calls(console))
    card.get_by_label("Window").select_option("7")
    argv = wait_for_calls(page, console, first + 1)[-1]
    since, until = argv[argv.index("--since") + 1], argv[argv.index("--until") + 1]
    assert (date.fromisoformat(until) - date.fromisoformat(since)).days == 6
    expect(card).to_contain_text(f"{since} to {until}")
    card.get_by_role("button", name="Refresh").click()
    wait_for_calls(page, console, first + 2)


def test_a_refusal_names_the_next_step(page, console):
    console.scenario(
        cost={"error": {"code": "GATEWAY_UNREACHABLE", "message": "the gateway did not answer"}}
    )
    open_home(page, console)
    card = home_card(page)
    expect(card).to_contain_text("Unavailable: the model gateway did not answer")
    expect(card).to_contain_text("Next: start the services")
    expect(card.get_by_role("alert")).to_contain_text("Unavailable")
    expect(card.get_by_role("table")).to_have_count(0)


def test_no_calls_says_so(page, console):
    empty = {"calls": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0}
    console.scenario(
        cost={"keys": [], "models": [], "total": {**empty, "cost_usd": 0, "unpriced": 0}}
    )
    open_home(page, console)
    expect(home_card(page)).to_contain_text("No model calls by this project's deployments")
    expect(home_card(page)).to_contain_text(AS_OF)


def test_an_agent_in_the_cut_part_is_not_called_unused(page, console):
    other = {
        "key_alias": "agentbreeder-" + "e3" * 16, "kind": "deployment", "id": "e3" * 16,
        "name": "other", "target": "local", "state": "ready", "environment": "dev",
        **{k: v for k, v in MODEL.items() if k not in ("model_group", "model")}, "models": [MODEL],
    }  # fmt: skip
    scenario(console, cost={"keys": [other], "keys_cut": 0, "truncated": True})
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/demo")
    card = page.get_by_role("region", name="LLM cost", exact=True)
    expect(card).to_contain_text("in the part of this window that was read")
    expect(card).to_contain_text("totals are partial")


def test_cut_keys_are_noted_on_the_agent_not_on_home(page, console):
    other = {
        "key_alias": "agentbreeder-" + "e3" * 16, "kind": "deployment", "id": "e3" * 16,
        "name": "other", "target": "local", "state": "ready", "environment": "dev",
        **{k: v for k, v in MODEL.items() if k not in ("model_group", "model")}, "models": [MODEL],
    }  # fmt: skip
    # The server passes on 200 keys and counts the rest; the CLI sends no `keys_cut`.
    scenario(console, cost={"keys": [other] * 204, "truncated": False})
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    home = home_card(page)
    home.get_by_role("table", name="Spend per model").wait_for()
    expect(home).not_to_contain_text("smaller keys")
    page.goto(f"{console.origin}/#/agents/demo")
    card = page.get_by_role("region", name="LLM cost", exact=True)
    expect(card).to_contain_text("in the part of this window that was read")
    expect(card).to_contain_text("4 smaller keys are not listed")


def test_the_agent_overview_shows_its_own_deployments(page, console):
    hello = {
        "key_alias": "agentbreeder-" + "d1" * 16, "kind": "deployment", "id": "d1" * 16,
        "name": "demo", "target": "local", "state": "ready", "environment": "dev",
        **{k: v for k, v in MODEL.items() if k not in ("model_group", "model")}, "models": [MODEL],
    }  # fmt: skip
    removed = {**hello, "key_alias": "agentbreeder-" + "c2" * 16, "id": "c2" * 16,
               "name": "demo-candidate", "state": "removed", "cost_usd": 0.0008}  # fmt: skip
    other = {**hello, "key_alias": "agentbreeder-" + "e3" * 16, "id": "e3" * 16, "name": "other"}
    scenario(console, cost={"keys": [hello, removed, other], "truncated": True})
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/demo")
    card = page.get_by_role("region", name="LLM cost", exact=True)
    expect(card).to_contain_text("USD 0.002000")  # 0.0012 + 0.0008, not `other`
    expect(card).to_contain_text("6 calls")
    expect(card).to_contain_text(AS_OF)
    deployments = card.get_by_role("table", name="Spend per deployment")
    expect(deployments.locator("tbody tr")).to_have_count(2)
    expect(deployments).to_contain_text("demo-candidate")
    expect(deployments).to_contain_text("local, removed")
    expect(deployments).not_to_contain_text("other")
    expect(card.get_by_role("table", name="Spend per model")).to_contain_text("6")
    expect(card).to_contain_text("totals are partial")


def test_cost_cards_have_no_blocking_axe_violations(scheme_page, console):  # noqa: F811
    scenario(console)
    sign_in(scheme_page, console)
    scheme_page.goto(f"{console.origin}/#/")
    home_card(scheme_page).get_by_role("table", name="Spend per model").wait_for()
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "/") == []
    scheme_page.goto(f"{console.origin}/#/agents/demo")
    scheme_page.get_by_role("table", name="Spend per deployment").wait_for()
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "/agents/demo") == []
