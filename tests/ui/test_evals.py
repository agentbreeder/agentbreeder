"""The eval report viewer (#932, console half): an Audit release row and an agent's Releases tab
link to `#/audit/evals/RUN/AGENT`, which reads `evals show RUN --json` and shows the summary and
every case, failed first; replies and explanations are read only when asked for."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_audit import PRODUCTION, RUN, open_audit, release_story

pytestmark = pytest.mark.ui

RELEASE = {
    "at": "2026-11-13T10:00:00Z",
    "attempt_id": "a" * 32,
    "action": "release",
    "decision": "promoted",
    "actor": "local:owner",
    "actor_verified": False,
    "enforcement_mode": "enforce",
}
REPORT = {
    "schema_version": 1,
    "agent": "support",
    "file": f"support.eval-{RUN}.json",
    "run_id": RUN,
    "eval_set": "eval/support-evals@1.0.0",
    "judge": "judge-model",
    "endpoint": "http://127.0.0.1:9002",
    "cases": 3,
    "passed": 2,
    "pass_rate": 2 / 3,
    "flaky": ["c3"],
    "failed": ["c2"],
    "mean_correctness": 0.7,
    "p50_latency_ms": 120,
    "content": False,
    "results": [
        {
            "id": "c1", "category": None, "passed": True, "attempts": 1,
            "checks": {"agent_responded": True}, "failed_checks": [], "correctness": 0.9,
            "reason": "", "latency_ms": 100, "trace_id": None, "tools": [],
        },
        {
            "id": "c2", "category": "billing", "passed": False, "attempts": 1,
            "checks": {"agent_responded": True, "judge_correct": False},
            "failed_checks": ["judge_correct"], "correctness": 0.1,
            "reason": "wrong answer\u202e", "latency_ms": 120, "trace_id": "ab" * 16,
            "tools": ["orders"],
        },
        {
            "id": "c3", "category": None, "passed": True, "attempts": 2,
            "checks": {"agent_responded": True}, "failed_checks": [], "correctness": 1.0,
            "reason": "", "latency_ms": 140, "trace_id": None, "tools": [],
        },
    ],
    "notes": ["1 case was skipped as malformed"],
}  # fmt: skip


def calls(console) -> list[list[str]]:
    path = Path(console.state) / "evals-calls.json"
    return json.loads(path.read_text()) if path.is_file() else []


def open_report(page, console) -> None:
    sign_in(page, console)
    page.goto(f"{console.origin}/#/audit/evals/{RUN}/support")
    expect(page.get_by_role("heading", level=1)).to_contain_text(f"Eval run {RUN}")
    page.get_by_role("table", name="Eval cases, failed first").wait_for()


def test_the_audit_release_row_opens_the_report(page, console):
    console.scenario(audit_rows=release_story(), deployments=PRODUCTION, evals={"report": REPORT})
    open_audit(page, console)
    link = page.get_by_role("link", name=f"Eval report of run {RUN}")
    expect(link).to_have_attribute("href", f"#/audit/evals/{RUN}/support")
    link.click()
    expect(page.get_by_role("heading", level=1)).to_be_focused()
    expect(page.get_by_role("heading", level=1)).to_contain_text(f"Eval run {RUN}")
    expect(page.get_by_role("status").filter(has_text="cases passed")).to_have_text(
        "2 of 3 cases passed (66.7%)"
    )
    # The sidebar keeps Audit current.
    expect(page.get_by_role("link", name="Audit", exact=True)).to_have_attribute(
        "aria-current", "page"
    )


def test_the_report_shows_the_summary_and_cases_failed_first(page, console):
    console.scenario(evals={"report": REPORT})
    open_report(page, console)
    facts = page.locator("dl.facts")
    for text in (
        "eval/support-evals@1.0.0",
        "judge-model",
        "0.70",
        "120 ms",
        "http://127.0.0.1:9002",
    ):
        expect(facts).to_contain_text(text)
    expect(facts).to_contain_text(f"deploy/support.eval-{RUN}.json")
    expect(page.get_by_text("1 case was skipped as malformed")).to_be_visible()
    rows = page.get_by_role("table", name="Eval cases, failed first").locator("tbody tr")
    expect(rows).to_have_count(3)
    expect(rows.nth(0).locator("td").nth(0)).to_contain_text("c2")
    expect(rows.nth(0).locator("td").nth(0)).to_contain_text("billing")
    expect(rows.nth(0).locator("td").nth(1)).to_have_text("Failed")
    expect(rows.nth(0).locator("td").nth(2)).to_have_text("judge_correct")
    expect(rows.nth(0).locator("td").nth(4)).to_contain_text("wrong answer")
    # The bidi control is shown escaped, never applied.
    expect(rows.nth(0).locator(".esc")).to_have_count(1)
    expect(rows.nth(0).locator("td").nth(6)).to_contain_text("ab" * 16)
    expect(rows.nth(1).locator("td").nth(0)).to_contain_text("c3")
    expect(rows.nth(1).locator("td").nth(1)).to_contain_text("Passed after a retry")
    expect(rows.nth(1).locator("td").nth(1)).to_contain_text("2 attempts")
    expect(rows.nth(2).locator("td").nth(0)).to_contain_text("c1")
    expect(rows.nth(2).locator("td").nth(1)).to_have_text("Passed")
    (argv,) = calls(console)
    assert Path(argv[4]).parts[-2:] == ("projects", "demo")
    assert argv[:4] + argv[5:] == [
        "evals", "show", RUN, "--project", "--state-dir", "/nonexistent/fake-state",
        "--agent", "support", "--json",
    ]  # fmt: skip


def test_replies_and_explanations_only_when_asked(page, console):
    console.scenario(evals={"report": REPORT})
    open_report(page, console)
    expect(page.get_by_text("Reply and explanation")).to_have_count(0)
    page.get_by_role("button", name="Show replies and explanations").click()
    expect(page.get_by_role("button", name="Hide replies and explanations")).to_be_visible()
    first = page.get_by_role("table", name="Eval cases, failed first").locator("tbody tr").first
    first.get_by_text("Reply and explanation").click()
    expect(first.get_by_label("reply", exact=True)).to_have_text("Reply to c2")
    expect(first.get_by_label("judge's explanation", exact=True)).to_have_text("Why c2")
    assert "--content" in calls(console)[-1]
    page.get_by_role("button", name="Hide replies and explanations").click()
    expect(page.get_by_text("Reply and explanation")).to_have_count(0)


def test_a_failed_read_shows_its_code_message(page, console):
    error = {"code": "EVAL_REPORT_NOT_FOUND", "message": "no report for run " + RUN}
    console.scenario(evals={"error": error})
    sign_in(page, console)
    page.goto(f"{console.origin}/#/audit/evals/{RUN}/support")
    expect(page.get_by_text(f"no report for run {RUN}")).to_be_visible()
    expect(page.get_by_role("button", name="Retry")).to_be_visible()
    expect(page.get_by_role("table", name="Eval cases, failed first")).to_have_count(0)


def test_a_content_read_that_fails_can_go_back_to_the_report(page, console):
    error = {"code": "REDACTION_INCOMPLETE", "message": "nothing printed"}
    console.scenario(evals={"report": REPORT, "content_error": error})
    open_report(page, console)
    page.get_by_role("button", name="Show replies and explanations").click()
    expect(page.get_by_text("nothing printed")).to_be_visible()
    page.get_by_role("button", name="Show the report without replies").click()
    page.get_by_role("table", name="Eval cases, failed first").wait_for()
    expect(page.get_by_role("button", name="Show replies and explanations")).to_be_visible()
    assert "--content" not in calls(console)[-1]


def test_the_tone_follows_the_saved_header(page, console):
    # The failed case was skipped as malformed: every readable case passed, the run did not.
    only_passed = [row for row in REPORT["results"] if row["passed"]]
    console.scenario(evals={"report": {**REPORT, "results": only_passed}})
    open_report(page, console)
    verdict = page.locator("p.verdict")
    expect(verdict).to_have_text("2 of 3 cases passed (66.7%)")
    expect(verdict).to_have_class(re.compile(r"\bbad\b"))


def test_a_bad_agent_name_reads_nothing(page, console):
    console.scenario(evals={"report": REPORT})
    sign_in(page, console)
    page.goto(f"{console.origin}/#/audit/evals/{RUN}/Support")
    expect(page.get_by_text("That is not an agent name.")).to_be_visible()
    assert calls(console) == []


def test_a_bad_run_id_reads_nothing(page, console):
    console.scenario(evals={"report": REPORT})
    sign_in(page, console)
    page.goto(f"{console.origin}/#/audit/evals/run-1/support")
    expect(page.get_by_text("That is not an eval run id.")).to_be_visible()
    assert calls(console) == []


def test_the_releases_tab_links_each_scored_release(page, console):
    # The `agents` fixture's agent.yaml is agent `demo`: its project's releases are its own.
    console.project("demo", fixture="agents")
    rows = [
        {**RELEASE, "seq": 12, "reasons": [{"code": "RELEASE_EVAL", "message": f"{RUN} 5/5"}]},
        # A run id outside the rule gets no link.
        {**RELEASE, "seq": 9, "reasons": [{"code": "RELEASE_EVAL", "message": "run-6 2/5"}]},
    ]
    console.scenario(audit_rows=rows, evals={"report": {**REPORT, "agent": "demo"}})
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/demo/releases")
    expect(page.get_by_role("table", name="Releases").locator("tbody tr")).to_have_count(2)
    link = page.get_by_role("link", name="Eval report")
    expect(link).to_have_count(1)
    expect(link).to_have_attribute("href", f"#/audit/evals/{RUN}/demo")
    link.click()
    expect(page).to_have_url(re.compile(rf"#/audit/evals/{RUN}/demo$"))
    page.get_by_role("table", name="Eval cases, failed first").wait_for()
    assert calls(console)[-1][-3:] == ["--agent", "demo", "--json"]


def test_the_viewer_has_no_blocking_axe_violations(scheme_page, console):  # noqa: F811
    console.scenario(evals={"report": REPORT})
    open_report(scheme_page, console)
    scheme_page.get_by_role("button", name="Show replies and explanations").click()
    scheme_page.get_by_text("Reply and explanation").first.click()
    assert blocking(scheme_page, "eval report") == []
