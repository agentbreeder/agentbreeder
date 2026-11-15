"""Container events on Home (#821, M5 console half, part 3): the fake `watch --stack DIR --json
--record` through the real console's watch feed, listed under each project's services."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_usage import RUNNING, open_home, status_reads

pytestmark = pytest.mark.ui

DIED = {
    "event": "died",
    "source": "service",
    "name": "postgres",
    "deployment": None,
    "exit_code": 0,
    "oom": False,
}
RESTARTED = {"event": "restarted", "source": "service", "name": "postgres", "deployment": None}
UNHEALTHY = {
    "event": "unhealthy",
    "source": "agent",
    "name": "support",
    "deployment": "a" * 32,
    "role": "egress",
}


def watch_starts(console) -> list[list[str]]:
    log = console.state / "watch-argv"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def events_of(page):
    return page.get_by_role("list", name="Container events, demo")


def test_events_show_newest_first_and_a_new_one_reads_status_again(page, console):
    console.scenario(
        services=RUNNING,
        watch={"events": [DIED, {**DIED, "exit_code": 137, "oom": True}, RESTARTED, UNHEALTHY]},
    )
    open_home(page, console)
    events = events_of(page)
    expect(events.get_by_role("listitem")).to_have_count(4, timeout=10_000)
    items = events.get_by_role("listitem")
    expect(items.nth(0)).to_contain_text("agent support (egress) became unhealthy")
    expect(items.nth(1)).to_contain_text("postgres started again")
    expect(items.nth(2)).to_contain_text("postgres exited by itself (exit 137, out of memory)")
    expect(items.nth(3)).to_contain_text("postgres exited by itself (exit 0)")
    expect(page.get_by_text("Watching its containers while this page is open.")).to_be_visible()
    starts = watch_starts(console)
    assert len(starts) == 1  # one child however many polls
    assert starts[0][0] == "watch" and starts[0][-2:] == ["--json", "--record"]
    # Events after the first one seen re-read the project's status (so the chips follow).
    assert status_reads(console) >= 2


def test_one_event_after_an_empty_first_answer_reads_status_again(page, console):
    console.scenario(services=RUNNING, watch={"events": [DIED], "every": 4})
    open_home(page, console)
    expect(page.get_by_text("None since this console started watching.")).to_be_visible(
        timeout=10_000
    )
    before = status_reads(console)
    expect(events_of(page).get_by_role("listitem")).to_have_count(1, timeout=15_000)
    page.wait_for_timeout(1500)
    assert status_reads(console) == before + 1


def test_no_events_says_so(page, console):
    console.scenario(services=RUNNING)
    open_home(page, console)
    expect(page.get_by_text("None since this console started watching.")).to_be_visible(
        timeout=10_000
    )
    expect(page.get_by_text("Container events come from agentbreeder watch")).to_be_visible()


def test_a_failed_watch_is_named(page, console):
    console.scenario(
        services=RUNNING,
        watch={"error": "[STACK_NOT_UP] this stack was never brought up; run `services up`"},
    )
    open_home(page, console)
    expect(page.get_by_text("this stack was never brought up")).to_be_visible(timeout=10_000)
    expect(page.get_by_text("Container events are not being watched")).to_be_visible()


def test_no_watch_while_nothing_runs(page, console):
    stopped = {name: {**info, "state": "exited"} for name, info in RUNNING.items()}
    console.scenario(services=stopped)
    open_home(page, console)
    page.wait_for_timeout(1500)
    assert watch_starts(console) == []
    expect(page.get_by_text("None since this console started watching.")).to_have_count(0)


def test_home_events_have_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(services=RUNNING, watch={"events": [DIED, UNHEALTHY]})
    open_home(scheme_page, console)
    expect(events_of(scheme_page).get_by_role("listitem")).to_have_count(2, timeout=10_000)
    assert blocking(scheme_page, "home events") == []
