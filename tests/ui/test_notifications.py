"""Notifications (#827 M11, console half): the bell (conditions from `next` plus unread stored
events, the split in words), its panel ("Needs attention" and "Recent events", mark read, a gate
failure's report path and summary read) and failure toasts for events stored after the page
loaded. The fake CLI answers `next` from a fixture and `events` from `events-store.json`."""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)

pytestmark = pytest.mark.ui

RUN = "20261113-213200-1a2b3c"


def stored(console, *rows) -> None:
    (console.state / "events-store.json").write_text(json.dumps(list(rows)))


def died(project: str, identifier: int, read: bool = False) -> dict:
    return {
        "id": identifier,
        "time": "2026-11-19T10:00:00Z",
        "project": project,
        "kind": "container_died",
        "source": "service",
        "name": "postgres",
        "deployment": None,
        "exit_code": 1,
        "code": None,
        "data": {"oom": False},
        "read": read,
        "message": "service postgres exited (exit 1)",
    }


def gate_failed(project: str, identifier: int) -> dict:
    return {
        "id": identifier,
        "time": "2026-11-19T10:05:00Z",
        "project": project,
        "kind": "gate_failed",
        "source": "release",
        "name": "hello",
        "deployment": None,
        "exit_code": None,
        "code": None,
        "data": {
            "passed": 1,
            "cases": 2,
            "failed": ["c2"],
            "more": 0,
            "report": f"{project}/deploy/hello.eval-{RUN}.json",
        },
        "read": False,
        "message": "Eval gate failed for hello: 1/2 passed; failed: c2",
    }


REPORT = {
    "schema_version": 1,
    "agent": "hello",
    "file": f"hello.eval-{RUN}.json",
    "run_id": RUN,
    "eval_set": "eval/hello@1.0.0",
    "judge": "judge-model",
    "endpoint": "http://127.0.0.1:8080",
    "cases": 2,
    "passed": 1,
    "pass_rate": 0.5,
    "flaky": [],
    "failed": ["c2"],
    "mean_correctness": 0.5,
    "p50_latency_ms": 120,
    "content": False,
    "results": [
        {
            "id": "c1",
            "category": "billing",
            "passed": True,
            "attempts": 1,
            "checks": {"agent_responded": True},
            "failed_checks": [],
            "correctness": 1.0,
            "reason": "ok",
            "latency_ms": 100,
            "trace_id": "ab" * 16,
            "tools": [],
        },
        {
            "id": "c2",
            "category": "billing",
            "passed": False,
            "attempts": 1,
            "checks": {"agent_responded": True, "judge_correct": False},
            "failed_checks": ["judge_correct"],
            "correctness": 0.1,
            "reason": "wrong answer",
            "latency_ms": 120,
            "trace_id": "cd" * 16,
            "tools": [],
        },
    ],
    "notes": [],
}


def open_console(page, console, guide: str = "conditions", **scenario) -> str:
    console.scenario(guide=guide, **scenario)
    path = str(console.project().resolve())
    return path


def read_again(page) -> None:
    """A window focus reads the events again, after the server's 2 s coalescing window."""
    page.wait_for_timeout(2100)
    page.evaluate("window.dispatchEvent(new Event('focus'))")


def bell(page):
    return page.locator("#notifications-bell")


def test_the_bell_counts_conditions_and_unread_events(page, console):
    path = open_console(page, console)
    stored(console, died(path, 1), died(path, 2, read=True))
    sign_in(page, console)
    expect(bell(page)).to_have_attribute(
        "aria-label", "Notifications: 2 need attention, 1 unread event"
    )
    expect(bell(page).locator(".bell-count")).to_have_text("3")
    bell(page).click()
    panel = page.get_by_role("region", name="Notifications", exact=True)
    expect(panel.get_by_role("heading", name="Notifications")).to_be_focused()
    expect(panel.get_by_role("heading", name="Needs attention (2)")).to_be_visible()
    expect(panel).to_contain_text("SERVICES_UNHEALTHY postgres is exited (none)")
    expect(panel.get_by_role("heading", name="Recent events (1 new)")).to_be_visible()
    expect(panel.locator("li.unread")).to_contain_text("service postgres exited (exit 1)")
    # Mark all read: the CLI marks the shown unread id, the button stays (focus is not lost).
    panel.get_by_role("button", name="Mark all read").click()
    expect(panel.get_by_role("button", name="All read")).to_have_attribute("aria-disabled", "true")
    expect(panel.get_by_role("status").filter(has_text="Marked 1 event as read.")).to_be_attached()
    expect(bell(page)).to_have_attribute(
        "aria-label", "Notifications: 2 need attention, no unread events"
    )
    calls = json.loads((console.state / "events-calls.json").read_text())
    assert ["events", "--mark-read", "1", "--limit", "1", "--json"] in calls
    # Escape closes the panel and returns focus to the bell.
    page.keyboard.press("Escape")
    expect(panel).to_have_count(0)
    expect(bell(page)).to_be_focused()


def test_a_401_on_mark_read_opens_re_auth_and_the_panel_recovers(page, console):
    # M2 (#818): a screen or panel never rethrows a session error; the re-auth dialog opens over
    # the page, the panel says the marking failed, and it works again after signing in.
    path = open_console(page, console)
    stored(console, died(path, 1))
    sign_in(page, console)
    bell(page).click()
    panel = page.get_by_role("region", name="Notifications", exact=True)
    unauthorized = '{"data": null, "error": {"code": "SIGN_IN", "message": "no"}, "stale": false}'
    page.route(
        "**/api/events/read*",
        lambda route: route.fulfill(
            status=401, content_type="application/json", body=unauthorized
        ),
    )
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    panel.get_by_role("button", name="Mark all read").click()
    dialog = page.get_by_role("dialog", name="Session ended")
    expect(dialog).to_be_visible()
    page.unroute("**/api/events/read*")
    dialog.get_by_label("Password", exact=True).fill(console.password)
    dialog.get_by_role("button", name="Sign in").click()
    expect(dialog).to_have_count(0)
    expect(
        panel.get_by_role("status").filter(has_text="Could not mark the events read.")
    ).to_be_attached()
    panel.get_by_role("button", name="Mark all read").click()
    expect(panel.get_by_role("button", name="All read")).to_have_attribute("aria-disabled", "true")
    assert errors == []


def test_nothing_to_show_says_so_and_shows_no_count(page, console):
    open_console(page, console, guide="build")
    sign_in(page, console)
    expect(bell(page)).to_have_attribute(
        "aria-label", "Notifications: nothing needs attention, no unread events"
    )
    expect(bell(page).locator(".bell-count")).to_have_count(0)
    bell(page).click()
    panel = page.get_by_role("region", name="Notifications", exact=True)
    expect(panel).to_contain_text("Nothing needs attention.")
    expect(panel).to_contain_text("No events stored.")


def test_a_failed_events_read_is_said_in_the_panel(page, console):
    open_console(page, console, guide="build")
    (console.state / "events-error.txt").write_text("[EVENTS_UNREADABLE] the store is busy")
    sign_in(page, console)
    bell(page).click()
    panel = page.get_by_role("region", name="Notifications", exact=True)
    expect(panel).to_contain_text("(EVENTS_UNREADABLE)")
    expect(bell(page)).to_have_attribute("aria-label", re.compile("events could not be read"))


def test_a_failure_stored_after_load_is_a_toast_once(page, console):
    path = open_console(page, console, guide="build")
    stored(console, died(path, 1))
    sign_in(page, console)
    expect(bell(page)).to_have_attribute("aria-label", re.compile("1 unread event"))
    toasts = page.get_by_role("region", name="Failure notifications")
    expect(toasts.get_by_role("alert")).to_have_count(0)  # already stored at load: no toast
    stored(console, died(path, 1), gate_failed(path, 2))
    read_again(page)
    toast = toasts.get_by_role("alert")
    expect(toast).to_have_count(1)
    expect(toast).to_contain_text("Eval gate failed for hello: 1/2 passed; failed: c2")
    toast.get_by_role("button", name="Dismiss: Eval gate failed").click()
    expect(toasts.get_by_role("alert")).to_have_count(0)
    read_again(page)
    page.wait_for_timeout(500)
    expect(toasts.get_by_role("alert")).to_have_count(0)  # a dismissed toast does not return


def test_a_gate_failure_shows_its_report_and_reads_the_summary(page, console):
    path = open_console(page, console, guide="build", evals={"report": REPORT})
    stored(console, gate_failed(path, 3))
    sign_in(page, console)
    bell(page).click()
    panel = page.get_by_role("region", name="Notifications", exact=True)
    expect(panel).to_contain_text(f"{path}/deploy/hello.eval-{RUN}.json")
    show = panel.get_by_role("button", name="Show summary of failed cases")
    show.click()
    expect(show).to_have_attribute("aria-expanded", "true")
    expect(panel.get_by_role("status").filter(has_text="1/2 passed")).to_contain_text("Failed: c2")
    expect(panel.get_by_role("link", name="Open the report")).to_have_attribute(
        "href", f"#/audit/evals/{RUN}/hello"
    )
    calls = json.loads((console.state / "evals-calls.json").read_text())
    assert any(call[:3] == ["evals", "show", RUN] and "--agent" in call for call in calls)


@pytest.mark.parametrize("width", [320, 375])
def test_the_panel_and_toasts_fit_narrow_screens(page, console, width):
    path = open_console(page, console)
    page.set_viewport_size({"width": width, "height": 800})
    stored(console, died(path, 1))
    sign_in(page, console)
    stored(console, died(path, 1), gate_failed(path, 2))
    read_again(page)
    expect(
        page.get_by_role("region", name="Failure notifications").get_by_role("alert")
    ).to_have_count(1)
    bell(page).click()
    expect(page.get_by_role("region", name="Notifications", exact=True)).to_be_visible()
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_notifications_have_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    path = open_console(scheme_page, console, evals={"report": REPORT})
    stored(console, died(path, 1))
    sign_in(scheme_page, console)
    stored(console, died(path, 1), gate_failed(path, 2))
    read_again(scheme_page)
    scheme_page.get_by_role("region", name="Failure notifications").get_by_role("alert").wait_for()
    bell(scheme_page).click()
    scheme_page.get_by_role("region", name="Notifications", exact=True).wait_for()
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "notifications") == []
