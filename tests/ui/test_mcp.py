"""The MCP grants screen (#831, M15 console half, part 1) over a copy of the M15 fixture
(`tests/fixtures/mcp_github_project`): servers, pins and grants read from stack.yaml and
stack.lock, relay state and drift from the fake `services status --json`."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)

pytestmark = pytest.mark.ui

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_github_project"
PIN = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
RUNNING = {
    "postgres": {"state": "running", "health": "healthy", "host_ports": {}},
    "litellm": {"state": "running", "health": "healthy", "host_ports": {"4000": 4000}},
    "mcp-github": {"state": "running", "health": "", "host_ports": {}},
}


def gh_reader(console, locked: bool = True) -> Path:
    """A copy of the fixture with the agent granted three tools, two of them pinned."""
    target = console.state / "projects" / "gh-reader"
    if target.exists():
        return target
    shutil.copytree(FIXTURE, target)
    stack = target / "stack.yaml"
    stack.write_text(
        stack.read_text().replace(
            "      candidate: true            # release tests a candidate before production\n",
            "      candidate: true            # release tests a candidate before production\n"
            "      mcp: {github: [get_me, list_issues, issue_read]}\n",
        )
    )
    lock = {
        "lock_version": 1,
        "entries": {},
        "imports": {},
        "mcp": {
            "github": {
                "ref": "mcp/github@1.0.0",
                "origin": "built-in",
                "upstream": "https://api.githubcopilot.com:443",
                "token_url": None,
                "tools": {"get_me": PIN, "list_issues": OTHER},
            }
        },
    }
    if locked:
        (target / "stack.lock").write_text(json.dumps(lock))
    return target


def open_mcp(page, console) -> Path:
    project = gh_reader(console)
    sign_in(page, console, project=False)
    page.get_by_label("Absolute path").fill(str(project))
    page.get_by_role("button", name="Add project").click()
    page.wait_for_url("**/#/services")
    page.get_by_role("navigation", name="Console").get_by_role("link", name="MCP grants").click()
    page.get_by_role("heading", name="mcp.github").wait_for()
    return project


def test_servers_pins_and_grants(page, console):
    console.scenario(
        services=RUNNING,
        mcp_status={
            "github": {
                "state": "ok",
                "pinned": 2,
                "changed": [],
                "missing": [],
                "unpinned": ["search_repositories"],
            }
        },
    )
    open_mcp(page, console)
    server = page.get_by_role("region", name="mcp.github", exact=True)
    expect(server).to_contain_text("mcp/github@1.0.0")
    expect(server).to_contain_text("built-in")
    expect(server).to_contain_text("https://api.githubcopilot.com:443")
    expect(server).to_contain_text("Pins match upstream")
    pins = page.get_by_role("table", name="Pinned tools of mcp.github")
    expect(pins.get_by_role("row").filter(has_text="get_me")).to_contain_text("a" * 16)
    expect(pins.get_by_role("row").filter(has_text="list_issues")).to_contain_text("Pinned")
    expect(server).to_contain_text(
        "not pinned (the relay does not serve them): search_repositories"
    )
    grants = page.get_by_role("table", name="Grants per agent")
    row = grants.get_by_role("row").filter(has_text="gh-reader")
    expect(row).to_contain_text("get_me, list_issues, issue_read")
    expect(row).to_contain_text("(not pinned yet)")
    expect(page.get_by_role("region", name="Grants per agent", exact=True)).to_contain_text(
        "takes effect at the next services up"
    )
    expect(page.get_by_text("services up").first).to_be_visible()


def test_drift_names_the_terminal_repin(page, console):
    console.scenario(
        services=RUNNING,
        mcp_status={
            "github": {
                "state": "drift",
                "pinned": 2,
                "changed": ["get_me"],
                "missing": ["list_issues"],
                "unpinned": [],
            }
        },
    )
    project = open_mcp(page, console)
    server = page.get_by_role("region", name="mcp.github", exact=True)
    expect(server).to_contain_text("Upstream changed")
    expect(server).to_contain_text(f"agentbreeder services up {project} --environment dev --repin")
    pins = page.get_by_role("table", name="Pinned tools of mcp.github")
    expect(pins.get_by_role("row").filter(has_text="get_me")).to_contain_text("hidden until")
    expect(pins.get_by_role("row").filter(has_text="list_issues")).to_contain_text(
        "Missing upstream"
    )


def test_before_the_first_services_up(page, console):
    gh_reader(console, locked=False)
    console.scenario(mcp_status={"github": {"state": "absent"}})
    open_mcp(page, console)
    server = page.get_by_role("region", name="mcp.github", exact=True)
    expect(server).to_contain_text("stack.lock has no pins for this version yet")
    expect(server).to_contain_text("Relay not running")
    expect(server).to_contain_text("No tools pinned.")


def test_mcp_screen_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(
        services=RUNNING,
        mcp_status={
            "github": {"state": "drift", "changed": ["get_me"], "missing": [], "unpinned": []}
        },
    )
    open_mcp(scheme_page, console)
    scheme_page.wait_for_timeout(300)
    assert blocking(scheme_page, "mcp") == []


# -- grant and revoke (#831 part 2): the real `mcp grant|revoke` on the copy's stack.yaml ---------


def change_card(page):
    return page.get_by_role("region", name="Change a grant", exact=True)


def review(page, verb: str, *tools: str):
    card = change_card(page)
    card.get_by_role("radio", name=f"{verb.capitalize()} tools").check()
    for tool in tools:
        card.get_by_role("checkbox", name=re.compile(rf"^{tool}\b")).check()
    card.get_by_role("button", name="Review change").click()
    return card


def test_grant_is_reviewed_then_written(page, console):
    console.scenario(services=RUNNING)
    project = open_mcp(page, console)
    card = review(page, "grant", "search_repositories")
    diff = card.get_by_label("stack.yaml change")
    expect(diff).to_contain_text("search_repositories")
    expect(card).to_contain_text("takes effect at the next services up")
    assert "search_repositories" not in (project / "stack.yaml").read_text()
    card.get_by_role("button", name="Write grant").click()
    expect(card).to_contain_text("Granted search_repositories for gh-reader on mcp.github.")
    expect(card).to_contain_text(f"agentbreeder services up {project} --environment dev")
    assert "search_repositories" in (project / "stack.yaml").read_text()
    grants = page.get_by_role("table", name="Grants per agent")
    expect(grants).to_contain_text("search_repositories")


def test_a_stack_changed_after_review_is_reviewed_again(page, console):
    console.scenario(services=RUNNING)
    project = open_mcp(page, console)
    card = review(page, "revoke", "issue_read")
    expect(card.get_by_label("stack.yaml change")).to_contain_text("issue_read")
    stack = project / "stack.yaml"
    stack.write_text(stack.read_text() + "# edited meanwhile\n")
    card.get_by_role("button", name="Write revoke").click()
    expect(card).to_contain_text("stack.yaml changed after your review")
    expect(card.get_by_role("button", name="Write revoke")).to_have_count(0)
    assert "issue_read" in stack.read_text()


IN_USE = {
    "schema_version": 1,
    "file": "/x/stack.yaml",
    "verb": "revoke",
    "agent": "gh-reader",
    "server": "github",
    "ref": "mcp/github@1.0.0",
    "removed": ["get_me"],
    "tools": [{"name": "list_issues", "effect": "read"}, {"name": "issue_read", "effect": "read"}],
    "changed": True,
    "base_sha256": "sha256:" + "a" * 64,
    "sha256": "sha256:" + "b" * 64,
    "diff": "--- stack.yaml\n+++ stack.yaml\n-      mcp: {github: [get_me]}\n",
    "dry_run": True,
    "in_use": [{"deployment": "d" * 32, "environment": "dev", "tools": ["get_me"]}],
}


def test_a_revoke_held_by_a_deployment_names_the_next_steps(page, console):
    console.scenario(services=RUNNING, mcp_grant=IN_USE)
    project = open_mcp(page, console)
    card = review(page, "revoke", "get_me")
    warning = card.locator(".notice.warn")
    expect(warning).to_contain_text("MCP_GRANT_IN_USE")
    expect(warning).to_contain_text(f"agentbreeder remove {'d' * 32} --local --stack {project}")
    expect(warning).to_contain_text("The revoke is still written.")


def test_the_grant_review_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(services=RUNNING, mcp_grant=IN_USE)
    open_mcp(scheme_page, console)
    card = review(scheme_page, "revoke", "get_me")
    card.get_by_role("button", name="Write revoke").wait_for()
    assert blocking(scheme_page, "mcp grant review") == []


def test_a_write_whose_answer_fails_its_check_reads_the_grants_again(page, console):
    console.scenario(services=RUNNING, mcp_grant_write={"schema_version": 1, "verb": "x"})
    project = open_mcp(page, console)
    card = review(page, "grant", "search_repositories")
    card.get_by_role("button", name="Write grant").click()
    expect(card).to_contain_text("The write may have happened")
    expect(card.get_by_role("button", name="Write grant")).to_have_count(0)
    assert "search_repositories" in (project / "stack.yaml").read_text()
    grants = page.get_by_role("table", name="Grants per agent")
    expect(grants).to_contain_text("search_repositories")  # read again


def test_a_new_choice_clears_the_last_result(page, console):
    console.scenario(services=RUNNING)
    open_mcp(page, console)
    card = review(page, "grant", "search_repositories")
    card.get_by_role("button", name="Write grant").click()
    expect(card).to_contain_text("Granted search_repositories")
    card.get_by_role("radio", name="Revoke tools").check()
    expect(card).not_to_contain_text("Granted search_repositories")
