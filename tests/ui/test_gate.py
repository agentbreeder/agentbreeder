"""The gate-failed view (#826, M10 console half, PR 3) over a copy of Northwind: when the agent's
latest release failed the eval gate, its Overview and Build and release show one row per failed
check (expected from the real eval set via `registry show`), links to edit the prompt and the case
(which opens that case in the editor), and the candidate's saved log tail (`logs --failed`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_registry import open_northwind

pytestmark = pytest.mark.ui

NAME = "northwind-support"
RUN = "20261114-091500-0a1b2c"
CASE = "refund-annual-in-window"
ROW = {
    "at": "2026-11-14T09:20:00Z",
    "attempt_id": "a" * 32,
    "action": "release",
    "actor": "local:owner",
    "actor_verified": False,
    "enforcement_mode": "enforce",
}
EVAL_FAILED = {
    **ROW,
    "seq": 9,
    "decision": "eval_failed",
    "reasons": [{"code": "RELEASE_EVAL", "message": f"{RUN} 59/61"}],
}
PROMOTED = {
    **ROW,
    "seq": 12,
    "decision": "promoted",
    "reasons": [{"code": "RELEASE_EVAL", "message": "20261114-100000-ffffff 61/61"}],
}
REPORT = {
    "schema_version": 1,
    "agent": NAME,
    "file": f"{NAME}.eval-{RUN}.json",
    "run_id": RUN,
    "eval_set": "eval/northwind-golden@1.0.1",
    "judge": "judge-model",
    "endpoint": "http://127.0.0.1:9002",
    "cases": 61,
    "passed": 59,
    "pass_rate": 59 / 61,
    "flaky": [],
    "failed": [CASE, "odd id"],
    "mean_correctness": 0.9,
    "p50_latency_ms": 900,
    "content": False,
    "results": [
        {
            "id": CASE, "category": "refunds", "passed": False, "attempts": 2,
            "checks": {"agent_responded": True, "matches:30 days": False, "judge_correct": False},
            "failed_checks": ["matches:30 days", "judge_correct"], "correctness": 0.2,
            "reason": "says 14 days\u202e", "latency_ms": 900, "trace_id": None, "tools": [],
        },
        {
            "id": "odd id", "category": None, "passed": False, "attempts": 1,
            "checks": {"agent_responded": True, "called:refund_lookup": False},
            "failed_checks": ["called:refund_lookup"], "correctness": 0.9,
            "reason": "", "latency_ms": 700, "trace_id": None, "tools": ["web_search"],
        },
        {
            "id": "refund-monthly", "category": "refunds", "passed": True, "attempts": 1,
            "checks": {"agent_responded": True}, "failed_checks": [], "correctness": 1.0,
            "reason": "", "latency_ms": 800, "trace_id": None, "tools": [],
        },
    ],
    "notes": [],
}  # fmt: skip
TAIL = ["2026-11-14T09:19:58Z INFO started", "2026-11-14T09:19:59Z ERROR \x1b[31mboom"]


def evals_calls(console) -> list[list[str]]:
    path = Path(console.state) / "evals-calls.json"
    return json.loads(path.read_text()) if path.is_file() else []


def open_overview(page, console, **scenario):
    console.scenario(
        audit_rows=scenario.pop("audit_rows", [EVAL_FAILED]),
        evals=scenario.pop("evals", {"report": REPORT}),
        failed_tail=scenario.pop("failed_tail", TAIL),
        **scenario,
    )
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/agents/{NAME}")
    expect(page.get_by_role("heading", level=1)).to_contain_text(f"Agent {NAME}")


def test_the_overview_shows_each_failed_check_with_what_was_expected(page, console):
    open_overview(page, console)
    gate = page.get_by_role("region", name="Eval gate failed")
    expect(gate.get_by_role("status")).to_have_text(
        "59 of 61 cases passed (96.7%). Production is unchanged."
    )
    rows = gate.get_by_role("table", name="Failed checks").locator("tbody tr")
    expect(rows).to_have_count(3)  # two checks of one case, one of the other; passes left out
    first = rows.nth(0).locator("td")
    expect(first.nth(0)).to_have_text(CASE)
    expect(first.nth(1)).to_have_text("matches:30 days")
    expect(first.nth(2)).to_have_text("The reply matches 30 days")
    expect(first.nth(3)).to_have_text("It does not")
    expect(first.nth(4)).to_contain_text("says 14 days")
    expect(rows.nth(0).locator(".esc")).to_have_count(1)  # the bidi control shown, not applied
    judged = rows.nth(1).locator("td")
    expect(judged.nth(1)).to_have_text("judge_correct")
    # The reference answer comes from the eval set the run used (`registry show`).
    expect(judged.nth(2)).to_contain_text("Annual Pro plans are refunded in full within 30 days")
    expect(judged.nth(3)).to_have_text("Judged 0.20 (a pass needs 0.70)")
    odd = rows.nth(2).locator("td")
    expect(odd.nth(2)).to_have_text("Calls refund_lookup")
    expect(odd.nth(3)).to_have_text("Called web_search")
    # A case id the router does not take links to the eval set itself.
    expect(gate.get_by_role("link", name="Edit case odd id")).to_have_attribute(
        "href", "#/registry/evals/northwind-golden"
    )
    expect(gate.get_by_role("link", name="Edit the prompt")).to_have_attribute(
        "href", f"#/registry/prompts/{NAME}"
    )
    expect(gate.get_by_role("link", name="Full eval report")).to_have_attribute(
        "href", f"#/audit/evals/{RUN}/{NAME}"
    )
    # The report is read without replies until they are asked for.
    assert all("--content" not in argv for argv in evals_calls(console))


def test_the_saved_candidate_tail_is_shown_as_text(page, console):
    open_overview(page, console)
    tail = page.get_by_role("region", name="Candidate log (saved tail)")
    expect(tail).to_contain_text("The candidate's last 2 lines")
    block = tail.get_by_label("candidate log tail")
    expect(block).to_contain_text("INFO started")
    expect(block.locator(".esc")).to_have_count(1)  # the escape character is shown, not applied
    expect(tail).to_contain_text(f"agentbreeder logs --failed {NAME}")
    expect(tail).not_to_contain_text("fake-state")  # the state directory's path is not passed on


def test_without_a_saved_tail_it_says_why(page, console):
    open_overview(page, console, failed_tail=None)
    tail = page.get_by_role("region", name="Candidate log (saved tail)")
    expect(tail).to_contain_text("No tail is saved")


def test_replies_are_read_only_when_asked_for(page, console):
    open_overview(page, console)
    gate = page.get_by_role("region", name="Eval gate failed")
    gate.get_by_role("button", name="Show the agent's replies").click()
    expect(gate.get_by_role("button", name="Hide the agent's replies")).to_be_visible()
    rows = gate.get_by_role("table", name="Failed checks").locator("tbody tr")
    rows.nth(0).get_by_text("Reply", exact=True).click()
    expect(rows.nth(0).get_by_label(f"reply to {CASE}")).to_have_text(f"Reply to {CASE}")
    expect(rows.nth(1).get_by_text("Reply", exact=True)).to_have_count(0)  # once per case
    assert any("--content" in argv for argv in evals_calls(console))


def test_edit_case_opens_that_case_in_the_editor(page, console):
    open_overview(page, console)
    gate = page.get_by_role("region", name="Eval gate failed")
    gate.get_by_role("link", name=f"Edit case {CASE}").click()
    page.wait_for_url(f"**/#/registry/evals/northwind-golden/case/{CASE}")
    expect(page.get_by_role("heading", level=1)).to_contain_text(
        f"Eval set northwind-golden, case {CASE}"
    )
    editor = page.get_by_role("region", name="Edit 1.0.1")
    case = editor.locator("details.case[open]")
    expect(case).to_have_count(1)
    expect(case.get_by_label("Case id")).to_have_value(CASE)
    expect(case.locator("summary")).to_be_focused()


def test_a_later_passing_release_hides_the_view(page, console):
    open_overview(page, console, audit_rows=[PROMOTED, EVAL_FAILED])
    page.get_by_role("region", name="agent.yaml").wait_for()
    expect(page.get_by_role("region", name="Eval gate failed")).to_have_count(0)


def test_build_and_release_shows_the_view_too(page, console):
    open_overview(page, console)
    page.goto(f"{console.origin}/#/release")
    gate = page.get_by_role("region", name="Eval gate failed")
    expect(gate.get_by_role("table", name="Failed checks").locator("tbody tr")).to_have_count(3)
    expect(page.get_by_role("region", name="Candidate log (saved tail)")).to_contain_text(
        "INFO started"
    )


def test_the_view_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    open_overview(scheme_page, console)
    scheme_page.get_by_role("table", name="Failed checks").wait_for()
    scheme_page.get_by_label("candidate log tail").wait_for()
    assert blocking(scheme_page, "gate failed") == []


def test_the_deep_link_opens_its_case_once(page, console):
    """After a save shows the new version, the case does not open (and grab focus) again."""
    open_overview(page, console)
    page.goto(f"{console.origin}/#/registry/evals/northwind-golden/case/{CASE}")
    editor = page.get_by_role("region", name="Edit 1.0.1")
    case = editor.locator("details.case[open]")
    case.get_by_label("Reference answer (what a correct reply says)").fill("Full refund, 30 days.")
    editor.get_by_role("button", name="Review changes").click()
    editor.get_by_role("region", name="Review").get_by_role("button", name="Save as 1.0.2").click()
    expect(
        page.get_by_role("status").filter(has_text="Saved eval/northwind-golden@1.0.2")
    ).to_be_visible()
    saved = page.get_by_role("region", name="Edit 1.0.2")
    expect(saved.get_by_role("status").filter(has_text="61 cases")).to_be_visible()
    expect(saved.locator("details.case[open]")).to_have_count(0)


def test_a_case_not_in_the_latest_version_says_so(page, console):
    open_overview(page, console)
    page.goto(f"{console.origin}/#/registry/evals/northwind-golden/case/renamed-case")
    editor = page.get_by_role("region", name="Edit 1.0.1")
    expect(editor).to_contain_text("Case renamed-case is not in this version")
    expect(editor.locator("details.case[open]")).to_have_count(0)


def test_an_unreadable_eval_set_and_an_empty_tail(page, console):
    open_overview(
        page, console, evals={"report": {**REPORT, "eval_set": "not a ref"}}, failed_tail=[]
    )
    gate = page.get_by_role("region", name="Eval gate failed")
    expect(gate).to_contain_text("The eval set's cases could not be read")
    rows = gate.get_by_role("table", name="Failed checks").locator("tbody tr")
    # The rule stands in for the reference answer, and no case can be linked.
    expect(rows.nth(1).locator("td").nth(2)).to_have_text(
        "An answer the judge scores at least 0.70"
    )
    expect(gate.get_by_role("link", name=f"Edit case {CASE}")).to_have_count(0)
    tail = page.get_by_role("region", name="Candidate log (saved tail)")
    expect(tail).to_contain_text("The saved tail is empty")


def test_another_case_link_opens_that_case(page, console):
    open_overview(page, console)
    page.goto(f"{console.origin}/#/registry/evals/northwind-golden/case/{CASE}")
    editor = page.get_by_role("region", name="Edit 1.0.1")
    expect(editor.locator("details.case[open]").get_by_label("Case id")).to_have_value(CASE)
    page.goto(f"{console.origin}/#/registry/evals/northwind-golden/case/refund-monthly")
    opened = page.get_by_role("region", name="Edit 1.0.1").locator("details.case[open]")
    expect(opened).to_have_count(1)
    expect(opened.get_by_label("Case id")).to_have_value("refund-monthly")
