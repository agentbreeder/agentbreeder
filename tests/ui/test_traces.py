"""Traces in the console (#907, console half): an agent's Traces tab lists one deployment's recent
traces through `traces ID --last N --json` (the fake CLI answers), each with its span tree on
request, and an eval case's trace id opens the same span tree in the eval report viewer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_evals import REPORT, open_report
from .test_shell import DEPLOYMENTS

pytestmark = pytest.mark.ui

PRODUCTION_ID = "1" * 32
CANDIDATE_ID = "2" * 32
ROWS = [
    dict(DEPLOYMENTS[0], id=PRODUCTION_ID),
    dict(DEPLOYMENTS[1], id=CANDIDATE_ID),
    dict(DEPLOYMENTS[0], id="3" * 32, name="billing"),
]


def calls(console) -> list[list[str]]:
    path = Path(console.state) / "traces-calls.json"
    return json.loads(path.read_text()) if path.is_file() else []


def wait_for_calls(page, console, count: int) -> list[list[str]]:
    for _ in range(100):
        found = calls(console)
        if len(found) >= count:
            return found
        page.wait_for_timeout(50)
    raise AssertionError(f"expected {count} traces calls, saw {calls(console)}")


def open_traces(page, console, name="demo"):
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/{name}/traces")
    expect(
        page.get_by_role("navigation", name="Agent sections").get_by_role("link", name="Traces")
    ).to_have_attribute("aria-current", "page")


def items(page):
    return page.get_by_role("list", name="Traces, newest first").locator(":scope > li")


def test_the_tab_lists_production_traces_and_opens_a_span_tree(page, console):
    console.scenario(deployments=ROWS)
    open_traces(page, console)
    expect(items(page)).to_have_count(2)
    [argv] = wait_for_calls(page, console, 1)
    assert argv[:2] == ["traces", PRODUCTION_ID]
    assert argv[2:] == ["--stack", argv[3], "--environment", "dev", "--last", "20", "--json"]
    first = items(page).first
    expect(first).to_contain_text("2026-11-14 03:10:33 UTC")
    expect(first).to_contain_text(
        "Error · 41 ms · model gemini-2.5-flash · 321 in, 70 out tokens · 3 spans"
    )
    expect(first).to_contain_text("Tool calls: lookup_order (error, 11 ms)")
    expect(first).to_contain_text("ab" * 16)
    expect(items(page).nth(1)).to_contain_text("Tool calls: none")
    expect(page.get_by_role("status").filter(has_text="traces read")).to_have_text(
        "2 traces read, newest first."
    )
    # Only this deployment's own rows are offered; billing is another agent.
    options = page.get_by_label("Deployment").locator("option")
    expect(options).to_have_count(2)
    # The span tree is read only when opened, through `traces --trace`.
    trace = first.locator("details.trace")
    trace.locator("summary").click()
    expect(trace.get_by_role("list", name="Spans of this trace")).to_be_visible()
    found = wait_for_calls(page, console, 2)
    assert found[1][:3] == ["traces", "--trace", "ab" * 16]
    expect(page.locator(".card a[href^='http']")).to_have_count(0)


def test_choosing_a_deployment_and_a_count_reads_again(page, console):
    console.scenario(deployments=ROWS)
    open_traces(page, console)
    expect(items(page)).to_have_count(2)
    page.get_by_label("Deployment").select_option(CANDIDATE_ID)
    found = wait_for_calls(page, console, 2)
    assert found[1][:2] == ["traces", CANDIDATE_ID]
    expect(items(page)).to_have_count(2)
    page.get_by_label("Show").select_option("50")
    found = wait_for_calls(page, console, 3)
    assert found[2][1] == CANDIDATE_ID and found[2][-3:] == ["--last", "50", "--json"]
    expect(items(page)).to_have_count(2)
    page.get_by_role("button", name="Read again").click()
    assert len(wait_for_calls(page, console, 4)) == 4


def test_a_failed_read_says_why(page, console):
    problem = {"code": "TRACES_NO_PHOENIX", "message": "no phoenix"}
    console.scenario(deployments=ROWS, traces={"error": problem})
    open_traces(page, console)
    expect(page.get_by_role("alert")).to_contain_text(
        "This stack does not run Phoenix (service/phoenix), so there are no traces."
        " [TRACES_NO_PHOENIX]"
    )
    expect(items(page)).to_have_count(0)


def test_no_traces_and_window_attribution_are_explained(page, console):
    note = f"{PRODUCTION_ID}: no Phoenix project demo (no traces yet, or `traces: none`)"
    console.scenario(deployments=ROWS, traces={"list": [], "notes": [note]})
    open_traces(page, console)
    expect(page.get_by_text("Phoenix has no traces for this deployment yet.")).to_be_visible()
    expect(page.get_by_text(note)).to_be_visible()
    window = [{"trace_id": "ef" * 16, "attribution": "window", "status": "ok"}]
    console.scenario(deployments=ROWS, traces={"list": window, "overlapping": [CANDIDATE_ID]})
    page.get_by_role("button", name="Read again").click()
    expect(items(page)).to_have_count(1)
    overlap = page.get_by_text("1 other deployment of this agent ran at the same time")
    expect(overlap).to_be_visible()
    expect(items(page).first).to_contain_text("start unknown")


def test_another_deployments_trace_is_not_shown(page, console):
    rows = [{"trace_id": "ab" * 16}, {"trace_id": "cd" * 16, "deployment_id": "9" * 32}]
    console.scenario(deployments=ROWS, traces={"list": rows})
    open_traces(page, console)
    expect(items(page)).to_have_count(1)
    expect(page.get_by_text("1 trace was left out")).to_be_visible()


def test_an_agent_with_no_deployment_has_nothing_to_read(page, console):
    console.scenario(deployments=ROWS[2:])
    open_traces(page, console)
    expect(page.get_by_text("No deployment of this agent in this project")).to_be_visible()
    assert calls(console) == []


def test_untrusted_strings_stay_text(page, console):
    hostile = [{
        "trace_id": "ab" * 16, "models": ["<img src=x onerror=alert(1)>"],
        "tool_calls": [{"name": "<b>bold</b>\u202e", "status": "ok"}],
    }]  # fmt: skip
    console.scenario(deployments=ROWS, traces={"list": hostile})
    open_traces(page, console)
    expect(items(page)).to_have_count(1)
    expect(items(page).first).to_contain_text("<img src=x onerror=alert(1)>")
    expect(items(page).first.locator("img, b")).to_have_count(0)
    expect(items(page).first.locator(".esc")).to_have_count(1)


def test_an_eval_cases_trace_opens_its_span_tree(page, console):
    console.scenario(evals={"report": REPORT})
    open_report(page, console)
    rows = page.get_by_role("table", name="Eval cases, failed first").locator("tbody tr")
    cell = rows.nth(0).locator("td").nth(6)
    assert calls(console) == []  # read only when opened
    cell.locator("details.trace summary").click()
    expect(cell.get_by_role("list", name="Spans of this trace")).to_be_visible()
    [argv] = wait_for_calls(page, console, 1)
    assert argv[:3] == ["traces", "--trace", "ab" * 16]


def test_an_eval_case_without_a_valid_trace_id_has_no_trace_panel(page, console):
    report = json.loads(json.dumps(REPORT))
    report["results"][1]["trace_id"] = "AB" * 16  # outside the rule
    report["results"][2]["trace_id"] = "ab" * 15 + "<x>"
    console.scenario(evals={"report": report})
    open_report(page, console)
    rows = page.get_by_role("table", name="Eval cases, failed first").locator("tbody tr")
    expect(rows).to_have_count(3)
    for index in range(3):
        cell = rows.nth(index).locator("td").nth(6)
        expect(cell).to_have_text("none")
        expect(cell.locator("details.trace")).to_have_count(0)
    page.wait_for_timeout(200)
    assert calls(console) == []


def test_the_traces_tab_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(deployments=ROWS)
    open_traces(scheme_page, console)
    trace = items(scheme_page).first.locator("details.trace")
    trace.locator("summary").click()
    expect(trace.get_by_role("list", name="Spans of this trace")).to_be_visible()
    found = blocking(scheme_page, "traces tab")
    console.scenario(deployments=ROWS, traces={"error": {"code": "TRACES_AUTH", "message": "no"}})
    scheme_page.get_by_role("button", name="Read again").click()
    expect(scheme_page.get_by_role("alert")).to_be_visible()
    found += blocking(scheme_page, "traces tab, failure")
    assert found == []
