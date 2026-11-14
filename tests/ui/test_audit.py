"""The Audit view (#824, M8 console half, part 2): each row's agent, enforcement mode and reason
codes, a release row's eval run and report path, the production link, and paging past 50 rows
with `audit list --before SEQ`. The fake CLI pages its scenario rows as the CLI does."""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)

pytestmark = pytest.mark.ui

IMAGE = "sha256:" + "e" * 64
RUN = "20261113-101500-abc123"
LIVE = "1" * 32
CANDIDATE = "2" * 32


def _row(seq: int, **fields) -> dict:
    return {
        "seq": seq,
        "at": "2026-11-13T18:00:00+00:00",
        "attempt_id": f"{seq:032x}",
        "deployment_id": None,
        "agent": None,
        "actor": "local:owner",
        "actor_verified": False,
        "action": "transition",
        "decision": "ready",
        "enforcement_mode": "enforce",
        "image_digest": None,
        "reasons": [],
        **fields,
    }


def release_story() -> list[dict]:
    """A release of `support`: candidate deployed, evals 12/12, then promoted."""
    attempt = "c" * 32
    return [
        _row(
            1, attempt_id=attempt, action="attempt_started", decision="started", image_digest=IMAGE
        ),
        _row(
            2,
            attempt_id=attempt,
            action="deploy",
            decision="allow",
            deployment_id=CANDIDATE,
            agent="support-candidate",
            image_digest=IMAGE,
            reasons=[{"code": "IDENTITY_CONFIRMED", "message": "ok"}],
        ),
        _row(
            3,
            action="release",
            decision="promoted",
            enforcement_mode="warn",
            reasons=[
                {"code": "RELEASE_EVAL", "message": f"{RUN} 12/12"},
                {"code": "RELEASE_IMAGE", "message": IMAGE},
            ],
        ),
        _row(4, action="deploy", decision="allow", deployment_id=LIVE, agent="support"),
    ]


PRODUCTION = [
    {
        "id": LIVE,
        "name": "support",
        "target": "local",
        "state": "ready",
        "endpoint": "http://127.0.0.1:9001",
        "image": IMAGE,
    }
]


def open_audit(page, console) -> None:
    sign_in(page, console)
    page.goto(f"{console.origin}/#/audit")
    expect(page.get_by_role("heading", level=1)).to_contain_text("Audit")
    page.get_by_role("table", name="Audit rows, newest first").wait_for()


def test_rows_name_agent_mode_reasons_and_release_evidence(page, console):
    console.scenario(audit_rows=release_story(), deployments=PRODUCTION)
    open_audit(page, console)
    rows = page.get_by_role("table", name="Audit rows, newest first").locator("tbody tr")
    expect(rows).to_have_count(4)
    # Newest first: seq 4 (production deploy), 3 (release), 2 (candidate), 1 (attempt_started).
    production, released, candidate, started = (rows.nth(i) for i in range(4))
    expect(production.locator("td").nth(1)).to_contain_text("support")
    link = production.get_by_role("link", name="Try support in production")
    expect(link).to_have_attribute("href", "#/agents/support/try")
    expect(production).to_contain_text("http://127.0.0.1:9001")
    expect(released.locator("td").nth(1)).to_contain_text("support")
    expect(released.locator("td").nth(4)).to_have_text("warn")
    expect(released).to_contain_text("RELEASE_EVAL, RELEASE_IMAGE")
    expect(released).to_contain_text(f"Evals 12/12, run {RUN}")
    expect(released).to_contain_text(f"Report deploy/support.eval-{RUN}.json")
    expect(candidate.locator("td").nth(1)).to_contain_text("support candidate")
    expect(candidate.get_by_role("link")).to_have_count(0)  # a candidate is never tried
    expect(candidate).to_contain_text("IDENTITY_CONFIRMED")
    expect(started.locator("td").nth(1)).to_contain_text("support candidate")


def test_a_row_without_an_agent_says_so(page, console):
    console.scenario(audit_rows=[_row(1, action="idp_disable", decision="ok")])
    open_audit(page, console)
    expect(page.get_by_role("row", name=re.compile("idp_disable"))).to_contain_text("not recorded")


def test_pages_past_fifty_and_back(page, console):
    console.scenario(audit_rows=[_row(seq) for seq in range(1, 121)])
    open_audit(page, console)
    table = page.get_by_role("table", name="Audit rows, newest first")
    expect(page.get_by_text("Page 1, 50 rows, newest first.")).to_be_visible()
    expect(page.get_by_role("button", name="Newer")).to_have_count(0)
    expect(table.locator("tbody tr").first.locator("td").nth(2)).to_have_text("transition")
    page.get_by_role("button", name="Older").click()
    expect(page.get_by_text("Page 2, 50 rows, newest first.")).to_be_visible()
    page.get_by_role("button", name="Older").click()
    expect(page.get_by_text("Page 3, 20 rows, newest first.")).to_be_visible()
    expect(table.locator("tbody tr")).to_have_count(20)
    expect(page.get_by_role("button", name="Older")).to_have_count(0)
    page.get_by_role("button", name="Newer").click()
    expect(page.get_by_text("Page 2, 50 rows, newest first.")).to_be_visible()
    page.get_by_role("button", name="Newest").click()
    expect(page.get_by_text("Page 1, 50 rows, newest first.")).to_be_visible()


def _focused_status(page) -> None:
    expect(page.locator("main [role=status][tabindex='-1']")).to_be_focused()


def test_paging_keeps_keyboard_focus_on_the_page_status(page, console):
    console.scenario(audit_rows=[_row(seq) for seq in range(1, 61)])
    open_audit(page, console)
    page.get_by_role("button", name="Older").focus()
    page.keyboard.press("Enter")
    expect(page.get_by_text("Page 2, 10 rows, newest first.")).to_be_visible()
    _focused_status(page)  # the Older button is gone on the last page
    page.get_by_role("button", name="Newest").click()
    expect(page.get_by_text("Page 1, 50 rows, newest first.")).to_be_visible()
    _focused_status(page)


def test_a_failed_page_says_so_and_retries(page, console):
    console.scenario(audit_rows=[_row(seq) for seq in range(1, 61)])
    open_audit(page, console)
    calls = []

    def handle(route):
        calls.append(route.request.url)
        if len(calls) == 1:  # only the first read fails
            route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"data": None, "error": {"code": "BOOM", "message": "Down"}}),
            )
        else:
            route.continue_()

    page.route(re.compile(r".*/audit\?before=.*"), handle)
    page.get_by_role("button", name="Older").click()
    expect(page.get_by_text("This page could not be read: Down")).to_be_visible()
    expect(page.get_by_role("button", name="Newest")).to_be_enabled()
    expect(page.get_by_role("button", name="Older")).to_have_count(0)
    expect(page.get_by_role("table", name="Audit rows, newest first")).to_have_count(0)
    page.get_by_role("button", name="Retry").click()
    expect(page.get_by_text("Page 2, 10 rows, newest first.")).to_be_visible()
    _focused_status(page)  # Retry is gone once the page loads
    assert len(calls) >= 2


def test_production_link_once_per_agent_per_page(page, console):
    rows = [
        _row(s, action="deploy", decision="allow", deployment_id=LIVE, agent="support")
        for s in range(1, 4)
    ]
    console.scenario(audit_rows=rows, deployments=PRODUCTION)
    open_audit(page, console)
    expect(page.get_by_role("link", name="Try support in production")).to_have_count(1)


def test_exactly_fifty_rows_has_no_older_page(page, console):
    console.scenario(audit_rows=[_row(seq) for seq in range(1, 51)])
    open_audit(page, console)
    expect(page.get_by_text("Page 1, 50 rows, newest first.")).to_be_visible()
    expect(page.get_by_role("button", name="Older")).to_have_count(0)


def test_untrusted_agent_and_reason_text_is_shown_as_text(page, console):
    hostile = _row(
        1,
        action="release",
        decision="failed",
        reasons=[{"code": "<img src=x onerror=alert(1)>\u202e", "message": "x"}],
    )
    console.scenario(audit_rows=[hostile, _row(2, agent="Bad\u202eName", deployment_id=LIVE)])
    open_audit(page, console)
    body = page.get_by_role("table", name="Audit rows, newest first")
    expect(body).to_contain_text("<img src=x onerror=alert(1)>")
    assert body.locator("img").count() == 0
    assert "\u202e" not in body.inner_text()
    # A name that is not an agent name is not shown as one.
    agents = body.locator("tbody tr td:nth-child(2)")
    expect(agents).to_have_text(["not recorded", "not recorded"])


def test_audit_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(
        audit_rows=release_story() + [_row(s) for s in range(5, 60)], deployments=PRODUCTION
    )
    open_audit(scheme_page, console)
    found = blocking(scheme_page, "audit page 1")
    scheme_page.get_by_role("button", name="Older").click()
    scheme_page.get_by_text("Page 2", exact=False).wait_for()
    found += blocking(scheme_page, "audit page 2")
    assert found == []
