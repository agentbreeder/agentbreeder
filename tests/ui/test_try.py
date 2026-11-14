"""Try it (#823, M7 console half, PR 1): chat with a project's production deployment through
`invoke NAME - --json` (the fake CLI answers), with the per-turn inspector, the notes on cost and
the unauthenticated endpoint, and replies kept as plain text (S-12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_shell import DEPLOYMENTS

pytestmark = pytest.mark.ui

CLUSTER = dict(DEPLOYMENTS[0], id="d3", name="billing", target="kubernetes")
STARTING = dict(DEPLOYMENTS[0], id="d4", name="slow", state="starting")
ROWS = [*DEPLOYMENTS, CLUSTER, STARTING]


def calls(console) -> list[dict]:
    path = Path(console.state) / "invoke-calls.json"
    return json.loads(path.read_text()) if path.is_file() else []


def open_try(page, console, name="demo"):
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/{name}/try")
    expect(
        page.get_by_role("navigation", name="Agent sections").get_by_role("link", name="Try it")
    ).to_have_attribute("aria-current", "page")


def send(page, text):
    page.get_by_label("Message").fill(text)
    page.get_by_role("button", name="Send", exact=True).click()


def turns(page):
    return page.get_by_role("list", name="Conversation turns").locator("li.turn")


def test_deployments_offers_try_it_for_ready_local_production_only(page, console):
    console.scenario(deployments=ROWS)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/deployments")
    expect(page.get_by_role("link", name="Try demo", exact=True)).to_be_visible()
    for name in ("demo-candidate", "billing", "slow"):
        expect(page.get_by_role("link", name=f"Try {name}", exact=True)).to_have_count(0)
    page.get_by_role("link", name="Try demo", exact=True).click()
    page.wait_for_url("**/#/agents/demo/try")
    expect(page.locator("main h1")).to_be_focused()
    expect(page.locator(".try-agent")).to_contain_text("demo · production · local · ready")


def test_a_message_goes_by_stdin_and_the_reply_shows_its_inspector(page, console):
    console.scenario(deployments=DEPLOYMENTS)
    open_try(page, console)
    notes = page.get_by_role("region", name="Before you send")
    expect(notes).to_contain_text("Each message calls the model and costs money.")
    expect(notes).to_contain_text("unauthenticated")
    expect(notes).to_contain_text("any customer or session ID (#780)")
    send(page, "Where is order 1001? ✓")
    reply = turns(page).first
    said = "You said: Where is order 1001? ✓"
    expect(reply.locator(".bubble.agent .turn-text")).to_have_text(said)
    # The reply's words go to the live region, so a screen reader hears the answer.
    expect(page.get_by_role("status").filter(has_text=f"Reply from demo: {said}")).to_have_count(1)
    details = reply.locator("details.inspector")
    expect(details.locator("summary")).to_contain_text("1,812 in, 64 out tokens")
    details.locator("summary").click()
    facts = details.locator("dl.facts")
    expect(facts).to_contain_text("Tools" + "orders")
    expect(facts).to_contain_text("Sources" + "Returns policy")
    expect(facts).to_contain_text("over 2 model calls")
    expect(facts).to_contain_text("ab" * 16)
    expect(facts).to_contain_text(
        "Not measured: this runtime did not report it"
        " (runtime/adk 1.2.0 and later do when every model call had a cost)"
    )
    expect(details.locator("a")).to_have_count(0)  # no trace link: the span tree opens in place
    [call] = calls(console)
    assert call["argv"][:3] == ["invoke", "demo", "-"] and "--json" in call["argv"]
    assert call["stdin"] == "Where is order 1001? ✓"
    assert not any("1001" in part for part in call["argv"])
    assert "--candidate" not in call["argv"]


def test_a_turn_shows_the_cost_the_runtime_reports(page, console):
    console.scenario(deployments=DEPLOYMENTS, invoke={"cost_usd": 0.000444})
    open_try(page, console)
    send(page, "hi")
    details = turns(page).first.locator("details.inspector")
    expect(details.locator("summary")).to_contain_text("64 out tokens · $0.000444")
    details.locator("summary").click()
    expect(details.locator("dl.facts")).to_contain_text(
        "$0.000444 (LiteLLM's estimate, reported by the runtime)"
    )


def test_later_messages_continue_the_session_until_a_new_conversation(page, console):
    console.scenario(deployments=DEPLOYMENTS)
    open_try(page, console)
    send(page, "one")
    expect(page.get_by_text("Session session-1")).to_be_visible()
    send(page, "two")
    expect(turns(page).nth(1).locator(".bubble.agent .turn-text")).to_have_text("You said: two")
    page.get_by_role("button", name="New conversation").click()
    expect(turns(page)).to_have_count(0)
    send(page, "three")
    expect(turns(page).first.locator(".bubble.agent .turn-text")).to_have_text("You said: three")
    first, second, third = calls(console)
    assert "--session" not in first["argv"]
    assert second["argv"][second["argv"].index("--session") + 1] == "session-1"
    assert "--session" not in third["argv"]


def test_a_reply_is_plain_text_never_markdown_or_links(page, console, violations):
    hostile = "**bold** [click](https://evil.example) <img src=x onerror=alert(1)> \x1b[31mred"
    console.scenario(deployments=DEPLOYMENTS, invoke={"reply": hostile, "tools": ["<b>t</b>"]})
    open_try(page, console)
    send(page, "hi")
    agent = turns(page).first.locator(".bubble.agent")
    expect(agent.locator(".turn-text")).to_contain_text("**bold** [click](https://evil.example)")
    expect(agent.locator(".turn-text")).to_contain_text("<img src=x onerror=alert(1)>")
    expect(agent.locator(".esc")).to_have_text("\\x1b")  # the control shown escaped
    expect(agent.locator("a, img, b, strong")).to_have_count(0)
    agent.locator("details.inspector summary").click()
    expect(agent.locator("dl.facts")).to_contain_text("<b>t</b>")
    assert violations(page) == []


def test_failures_say_whether_the_message_was_sent(page, console):
    error = {"code": "NOT_READY", "message": "demo is starting, not ready", "sent": False}
    console.scenario(deployments=DEPLOYMENTS, invoke={"error": error})
    open_try(page, console)
    send(page, "hi")
    expect(turns(page).first.get_by_role("alert")).to_have_text(
        "Not sent: demo is starting, not ready [NOT_READY]."
    )
    console.scenario(
        deployments=DEPLOYMENTS,
        invoke={"error": {"code": "TIMEOUT", "message": "no reply in 180 s", "sent": True}},
    )
    send(page, "again")
    second = turns(page).nth(1).get_by_role("alert")
    expect(second).to_contain_text("sending it again is a second turn")
    # The typed customer ID is kept; the conversation's earlier turns too.
    expect(turns(page)).to_have_count(2)


def test_one_message_in_flight_and_send_keeps_focus(page, console):
    console.scenario(deployments=DEPLOYMENTS, invoke={"seconds": 2})
    open_try(page, console)
    send(page, "slow one")
    button = page.get_by_role("button", name="Waiting for reply…")
    expect(button).to_be_visible()
    expect(turns(page).first).to_contain_text("Waiting for the reply")
    page.get_by_label("Message").fill("too soon")
    button.click()
    expect(page.get_by_text("Wait for the reply to the last message first.")).to_be_visible()
    expect(button).to_be_focused()
    expect(button).to_be_enabled()
    agent = turns(page).first.locator(".bubble.agent .turn-text")
    expect(agent).to_have_text("You said: slow one")
    assert len(calls(console)) == 1


def test_bad_input_is_refused_before_sending(page, console):
    console.scenario(deployments=DEPLOYMENTS)
    open_try(page, console)
    page.get_by_role("button", name="Send", exact=True).click()
    expect(page.get_by_text("Type a message first.")).to_be_visible()
    page.get_by_label("Customer ID (optional)").fill("not valid!")
    expect(page.get_by_text("A customer ID is 1 to 64 letters")).to_be_visible()
    send(page, "hi")
    expect(page.locator(".reason")).to_contain_text("A customer ID is 1 to 64 letters")
    assert calls(console) == []
    page.get_by_label("Customer ID (optional)").fill("C-1001")
    send(page, "hi")
    expect(turns(page).first.locator(".bubble.agent .turn-text")).to_have_text("You said: hi")
    [call] = calls(console)
    assert call["argv"][call["argv"].index("--customer") + 1] == "C-1001"


@pytest.mark.parametrize(
    "name,why",
    [
        ("demo-candidate", "demo-candidate has no production deployment"),
        ("billing", "billing runs on kubernetes"),
        ("slow", "slow is starting, not ready"),
        ("ghost", "ghost has no production deployment"),
    ],
)
def test_only_a_ready_local_production_deployment_can_be_tried(page, console, name, why):
    console.scenario(deployments=ROWS)
    open_try(page, console, name)
    expect(page.get_by_role("region", name="Not available")).to_contain_text(why)
    expect(page.get_by_label("Message")).to_have_count(0)


def test_the_conversation_stays_in_the_tab_and_a_reload_clears_it(page, console):
    console.scenario(deployments=DEPLOYMENTS)
    open_try(page, console)
    send(page, "remember me")
    expect(turns(page).first.locator(".bubble.agent .turn-text")).to_be_visible()
    page.goto(f"{console.origin}/#/deployments")
    page.get_by_role("link", name="Try demo", exact=True).click()
    expect(turns(page)).to_have_count(1)
    stored = page.evaluate("JSON.stringify(localStorage) + JSON.stringify(sessionStorage)")
    assert "remember me" not in stored
    page.reload()
    expect(
        page.get_by_role("navigation", name="Agent sections").get_by_role("link", name="Try it")
    ).to_have_attribute("aria-current", "page")
    expect(page.get_by_text("No messages yet.")).to_be_visible()


def test_the_done_try_step_opens_try_it_for_the_guides_agent(page, console):
    hello = dict(DEPLOYMENTS[0], name="hello")
    console.scenario(guide="done", deployments=[hello])
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    step = (
        page.get_by_role("region", name="Guided path")
        .get_by_role("listitem")
        .filter(has_text="Try it")
    )
    step.get_by_role("button", name="Try it", exact=True).click()
    page.wait_for_url("**/#/agents/hello/try")
    send(page, "Hello")
    expect(turns(page).first.locator(".bubble.agent .turn-text")).to_have_text("You said: Hello")


def trace_calls(console) -> list[list[str]]:
    path = Path(console.state) / "traces-calls.json"
    return json.loads(path.read_text()) if path.is_file() else []


def open_trace(turn):
    """Open a replied turn's trace disclosure (beside its Turn details)."""
    trace = turn.locator("details.trace")
    trace.locator("summary").click()
    return trace


def test_a_turns_trace_opens_as_a_span_tree(page, console):
    console.scenario(deployments=DEPLOYMENTS)
    open_try(page, console)
    send(page, "Where is order 1001?")
    turn = turns(page).first
    expect(turn.locator(".bubble.agent .turn-text")).to_be_visible()
    assert trace_calls(console) == []  # read only when opened
    trace = open_trace(turn)
    expect(trace).to_contain_text(
        "Error · 41 ms · 3 spans · model gemini-2.5-flash · 391 tokens · 1 tool call"
    )
    expect(trace).to_contain_text("Recorded by the agent's runtime for this deployment.")
    expect(trace).to_contain_text("Prompts, replies and tool arguments are never read")
    tree = trace.get_by_role("list", name="Spans of this trace")
    root = tree.locator(":scope > li")
    expect(root).to_have_count(1)
    expect(root.locator(".span-name").first).to_have_text("agent_turn")
    children = root.locator(":scope > ul > li")
    expect(children).to_have_count(2)
    expect(children.nth(0)).to_contain_text(
        "litellm.completion llm · 20 ms · model gemini-2.5-flash · 321 in, 70 out tokens"
    )
    expect(children.nth(1)).to_contain_text("lookup_order tool · error · 11 ms")
    expect(children.nth(1)).to_contain_text("Error: order not found")
    expect(trace.locator("a")).to_have_count(0)
    [argv] = trace_calls(console)
    assert argv[:3] == ["traces", "--trace", "ab" * 16]
    assert argv[3:] == ["--stack", argv[4], "--environment", "dev", "--json"]
    # Closing and opening again does not read it again.
    trace.locator("summary").click()
    trace.locator("summary").click()
    expect(tree).to_be_visible()
    assert len(trace_calls(console)) == 1


def test_a_trace_phoenix_does_not_have_yet_can_be_read_again(page, console):
    console.scenario(deployments=DEPLOYMENTS, traces={"missing": 1})
    open_try(page, console)
    send(page, "hi")
    expect(turns(page).first.locator(".bubble.agent .turn-text")).to_be_visible()
    trace = open_trace(turns(page).first)
    expect(trace.get_by_role("alert")).to_contain_text(
        "Phoenix does not have this trace yet; it can take a few seconds to arrive."
        " [TRACES_NOT_FOUND]"
    )
    trace.get_by_role("button", name="Read again").click()
    expect(trace.get_by_role("list", name="Spans of this trace")).to_be_visible()
    # The button is gone while reading; focus stays in the panel, on its status line.
    expect(trace.locator(".trace-status")).to_be_focused()
    expect(trace.locator(".trace-status")).to_have_text("Trace read: 3 spans.")
    expect(trace.get_by_role("alert")).to_have_count(0)
    assert len(trace_calls(console)) == 2


def test_a_stack_without_phoenix_says_so(page, console):
    problem = {"code": "TRACES_NO_PHOENIX", "message": "no phoenix"}
    console.scenario(deployments=DEPLOYMENTS, traces={"error": problem})
    open_try(page, console)
    send(page, "hi")
    expect(turns(page).first.locator(".bubble.agent .turn-text")).to_be_visible()
    trace = open_trace(turns(page).first)
    expect(trace.get_by_role("alert")).to_contain_text("This stack does not run Phoenix")


def test_a_deep_chain_nests_eight_levels_then_lists_the_rest(page, console):
    spans = [
        {"span_id": f"{i + 1:016x}", "parent_id": f"{i:016x}" if i else None, "name": f"s{i}"}
        for i in range(500)
    ]
    console.scenario(deployments=DEPLOYMENTS, traces={"spans": spans})
    open_try(page, console)
    send(page, "hi")
    expect(turns(page).first.locator(".bubble.agent .turn-text")).to_be_visible()
    trace = open_trace(turns(page).first)
    tree = trace.get_by_role("list", name="Spans of this trace")
    expect(tree.locator("li")).to_have_count(500)
    assert tree.locator("ul").count() == 9  # depths 1-8, then one flat list
    expect(tree.locator("li").last).to_contain_text("s499")


def test_a_trace_with_no_spans_says_so(page, console):
    console.scenario(deployments=DEPLOYMENTS, traces={"spans": []})
    open_try(page, console)
    send(page, "hi")
    expect(turns(page).first.locator(".bubble.agent .turn-text")).to_be_visible()
    trace = open_trace(turns(page).first)
    expect(trace).to_contain_text("Phoenix returned no spans for this trace.")
    expect(trace.get_by_role("list", name="Spans of this trace")).to_have_count(0)


def test_a_turn_without_a_trace_id_has_no_trace_panel(page, console):
    console.scenario(deployments=DEPLOYMENTS, invoke={"trace_id": None})
    open_try(page, console)
    send(page, "hi")
    turn = turns(page).first
    expect(turn.locator(".bubble.agent .turn-text")).to_be_visible()
    turn.locator("details.inspector summary").click()
    expect(turn.locator("dl.facts")).to_contain_text("No trace for this turn")
    expect(turn.locator("details.trace")).to_have_count(0)


def test_span_text_is_shown_as_text(page, console):
    hostile = {
        "span_id": "d" * 16,
        "parent_id": None,
        "name": "<img src=x onerror=alert(1)>\u202e",
        "kind": "TOOL",
        "status": "error",
        "tool": "<b>t</b>",
        "error": "<script>alert(1)</script>",
    }
    console.scenario(deployments=DEPLOYMENTS, traces={"spans": [hostile]})
    open_try(page, console)
    send(page, "hi")
    expect(turns(page).first.locator(".bubble.agent .turn-text")).to_be_visible()
    trace = open_trace(turns(page).first)
    tree = trace.get_by_role("list", name="Spans of this trace")
    expect(tree).to_contain_text("<img src=x onerror=alert(1)>")
    expect(tree).to_contain_text("tool <b>t</b>")
    expect(tree).to_contain_text("Error: <script>alert(1)</script>")
    assert tree.locator("img, b, script").count() == 0
    assert "\u202e" not in tree.inner_text()


def test_try_it_with_a_conversation_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(deployments=DEPLOYMENTS)
    open_try(scheme_page, console)
    send(scheme_page, "hello")
    expect(turns(scheme_page).first.locator(".bubble.agent .turn-text")).to_be_visible()
    trace = open_trace(turns(scheme_page).first)
    expect(trace.get_by_role("list", name="Spans of this trace")).to_be_visible()
    found = blocking(scheme_page, "try it, reply")
    console.scenario(
        deployments=DEPLOYMENTS,
        invoke={"error": {"code": "NOT_READY", "message": "not ready", "sent": False}},
    )
    send(scheme_page, "again")
    expect(turns(scheme_page).nth(1).get_by_role("alert")).to_be_visible()
    found += blocking(scheme_page, "try it, failure")
    assert found == []
