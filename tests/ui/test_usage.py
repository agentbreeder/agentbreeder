"""Service usage on screen (#822, M6 console half, part 4): Home's services table across projects
(status chips, host ports, CPU and memory "of the Docker VM", network and block IO, each with a
sparkline whose aria-label carries its numbers) and a service's Usage tab, from the fake
`services usage --follow --json` through the real console's usage ring."""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)

pytestmark = pytest.mark.ui

RUNNING = {
    "postgres": {"state": "running", "health": "healthy", "host_ports": {}},
    "litellm": {"state": "running", "health": "healthy", "host_ports": {"4000": 4000}},
}
MIXED = {
    **RUNNING,
    "litellm": {"state": "exited", "health": "", "host_ports": {"4000": 4000}},
    "phoenix": {"state": "running", "health": "starting", "host_ports": {}},
}


def usage_starts(console) -> list[list[str]]:
    log = console.state / "usage-argv"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def open_home(page, console):
    sign_in(page, console)
    page.get_by_role("navigation", name="Console").get_by_role("link", name="Home").click()
    page.get_by_role("heading", level=1, name="Home").wait_for()
    return page.get_by_role("table", name="Services across projects")


def row_of(table, name: str):
    link = table.page.get_by_role("link", name=name, exact=True)
    return table.get_by_role("row").filter(has=link)


def test_home_lists_services_with_usage_that_updates(page, console):
    console.scenario(services=RUNNING)
    table = open_home(page, console)
    for header in ("CPU (of the Docker VM)", "Memory (of the Docker VM)", "Network", "Block IO"):
        expect(table.get_by_role("columnheader", name=header)).to_be_visible()
    litellm = row_of(table, "litellm")
    expect(litellm).to_contain_text("demo")
    expect(litellm).to_contain_text("Healthy")
    expect(litellm).to_contain_text("4000 → 127.0.0.1:4000")
    expect(litellm.locator(".chip.ok .chip-icon")).to_have_attribute("aria-hidden", "true")
    postgres = row_of(table, "postgres")
    expect(postgres).to_contain_text("internal only")
    expect(postgres).to_contain_text("reached by agents on the stack network")

    # Figures from the ring: text values and a sparkline per figure, named with its numbers.
    expect(litellm).to_contain_text(re.compile(r"\d+\.\d%"))
    expect(litellm).to_contain_text(re.compile(r"MiB of 8\.0 GiB"))
    cpu = litellm.get_by_role("img", name=re.compile(r"^litellm CPU, last 5 minutes: low "))
    expect(cpu).to_be_visible()
    for name in ("memory", "network, in and out", "block IO, read and written"):
        spark = litellm.get_by_role("img", name=re.compile(rf"^litellm {name}, last 5 minutes"))
        expect(spark).to_be_visible()
    # Rates need two samples: then "KiB/s in" shows.
    expect(litellm).to_contain_text(re.compile(r"KiB/s in, [\d.]+ KiB/s out"), timeout=10_000)
    expect(litellm).to_contain_text("since it started")
    memory = litellm.get_by_role("img", name=re.compile(r"^litellm memory, "))
    first = memory.get_attribute("aria-label")
    # The memory grows by 1 MiB a sample in the fake: a newer reading within 10 s.
    expect(memory).not_to_have_attribute("aria-label", first, timeout=10_000)
    expect(page.get_by_text("CPU and memory are of the Docker VM")).to_be_visible()
    # One child for the project, however many polls.
    assert len(usage_starts(console)) == 1
    assert usage_starts(console)[0][:2] == ["services", "usage"]


def test_stopped_and_starting_services_and_a_service_link(page, console):
    console.scenario(services=MIXED)
    table = open_home(page, console)
    litellm = row_of(table, "litellm")
    expect(litellm).to_contain_text("Stopped")
    expect(litellm).to_contain_text("Not running: no usage.")
    expect(litellm).to_contain_text("not listening (last: 4000 → 127.0.0.1:4000)")
    expect(row_of(table, "phoenix")).to_contain_text("Starting")
    expect(row_of(table, "phoenix").locator(".chip.warn")).to_be_visible()
    phoenix_cpu = row_of(table, "phoenix").get_by_role("img", name=re.compile("phoenix CPU"))
    expect(phoenix_cpu).to_be_visible()

    table.get_by_role("link", name="litellm", exact=True).click()
    page.wait_for_url("**/#/services/litellm")
    page.get_by_role("heading", level=1, name="Service litellm").wait_for()
    expect(page.get_by_role("region", name="Config")).to_contain_text("exited")


def status_reads(console) -> int:
    log = console.state / "status-calls"
    return len(log.read_text().splitlines()) if log.exists() else 0


@pytest.mark.parametrize("flap", [False, True], ids=["steady", "flapping"])
def test_a_disagreement_with_status_is_read_again_once(page, console, flap):
    # Status says litellm runs; every sample lists only postgres (stopped from a terminal, say).
    only_postgres = [
        {
            "source": "service",
            "name": "postgres",
            "deployment": None,
            **dict.fromkeys(("cpu_percent", "memory_used", "memory_limit", "memory_percent"), 1),
            **dict.fromkeys(("network_rx", "network_tx", "block_read", "block_write"), 0),
            "pids": 1,
        }
    ]
    # Flapping: every other sample lists litellm again (a crash-looping container), so the
    # disagreement comes and goes; re-reads are spaced 30 s apart and capped.
    console.scenario(services=RUNNING, usage={"rows": only_postgres, "flap": flap})
    sign_in(page, console)
    page.wait_for_timeout(500)
    before = status_reads(console)
    page.get_by_role("navigation", name="Console").get_by_role("link", name="Home").click()
    table = page.get_by_role("table", name="Services across projects")
    if not flap:
        expect(row_of(table, "litellm")).to_contain_text("Not in the latest reading.")
    # Home's own read, then one re-read for this disagreement, and no more: the samples keep
    # disagreeing, and a status read would keep the session alive.
    page.wait_for_timeout(9000)
    assert status_reads(console) - before == 2


def test_a_failed_usage_command_is_named_above_the_rows(page, console):
    console.scenario(
        services=RUNNING,
        usage={"error": "[DOCKER_STATS_FAILED] docker stats ended three times in a row"},
    )
    table = open_home(page, console)
    expect(table).to_contain_text(
        "demo: docker stats ended three times in a row It is tried again in a few seconds."
    )
    expect(row_of(table, "litellm")).to_contain_text("No reading: see the note above.")


def test_no_usage_child_while_nothing_runs(page, console):
    console.scenario(services={"litellm": {"state": "exited", "health": "", "host_ports": {}}})
    table = open_home(page, console)
    expect(row_of(table, "litellm")).to_contain_text("Not running: no usage.")
    page.wait_for_timeout(4000)
    assert usage_starts(console) == []


def test_the_usage_tab_shows_tiles_and_the_disk_reading(page, console):
    console.scenario(services=RUNNING)
    sign_in(page, console)
    page.get_by_role("table", name="Stack services").get_by_role(
        "link", name="litellm", exact=True
    ).click()
    page.get_by_role("heading", level=1, name="Service litellm").wait_for()
    tabs = page.get_by_role("navigation", name="Service sections")
    expect(tabs.get_by_role("link", name="Overview")).to_have_attribute("aria-current", "page")
    tabs.get_by_role("link", name="Usage").click()
    page.wait_for_url("**/#/services/litellm/usage")
    expect(tabs.get_by_role("link", name="Usage")).to_have_attribute("aria-current", "page")
    # Switching tabs keeps focus on the tab link (one screen for both tabs).
    expect(tabs.get_by_role("link", name="Usage")).to_be_focused()
    usage = page.get_by_role("region", name="Usage")
    tiles = usage.get_by_role("list", name="Usage of litellm")
    for label in ("CPU", "Memory", "Network in", "Network out", "Block read", "Block written"):
        expect(tiles.get_by_role("listitem").filter(has_text=label).first).to_be_visible()
    expect(tiles).to_contain_text("of the Docker VM")
    expect(tiles.get_by_role("img", name=re.compile(r"^litellm processes, "))).to_be_visible()
    expect(usage).to_contain_text("Writable layer")
    expect(usage).to_contain_text("4.0 KiB")
    expect(usage).to_contain_text("agentbreeder-0123456789ab_pgdata 50.0 MiB")
    expect(usage).to_contain_text(re.compile(r"as of \d"))
    # The Overview's controls are not on this tab.
    expect(page.get_by_role("region", name="Controls")).to_have_count(0)
    tabs.get_by_role("link", name="Overview").click()
    expect(page.get_by_role("region", name="Controls")).to_be_visible()


def test_an_unknown_tab_says_so(page, console):
    console.scenario(services=RUNNING)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/services/litellm/cpu")
    expect(page.get_by_text('A service has no "cpu" tab.')).to_be_visible()


def test_a_stopped_service_usage_tab_says_why_it_is_empty(page, console):
    console.scenario(services=MIXED)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/services/litellm/usage")
    usage = page.get_by_role("region", name="Usage")
    expect(usage).to_contain_text("Not running, so there is no usage to show.")


def test_home_and_the_usage_tab_have_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(services=MIXED)
    table = open_home(scheme_page, console)
    expect(row_of(table, "phoenix")).to_contain_text(re.compile(r"KiB/s in"), timeout=10_000)
    found = blocking(scheme_page, "home services")
    scheme_page.goto(f"{console.origin}/#/services/postgres/usage")
    scheme_page.get_by_role("list", name="Usage of postgres").wait_for()
    found += blocking(scheme_page, "service usage")
    assert found == []
