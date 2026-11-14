"""Agents (#823, M7 console half, PR 2): the list across every stack (`list --all --json`, the fake
CLI answers), opening a registered agent's detail from "All projects", the gate strip, the identity
strip, and the Overview, Try it, Releases and Logs tabs."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_shell import DEPLOYMENTS

pytestmark = pytest.mark.ui

CANDIDATE = dict(DEPLOYMENTS[0], id="d2", name="demo-candidate", state="starting", endpoint=None)
RELEASE = {
    "seq": 12,
    "at": "2026-11-13T10:00:00Z",
    "attempt_id": "a" * 32,
    "action": "release",
    "decision": "promoted",
    "actor": "local:owner",
    "actor_verified": False,
    "enforcement_mode": "enforce",
    "reasons": [
        {"code": "RELEASE_EVAL", "message": "run-7 5/5"},
        {"code": "RELEASE_IMAGE", "message": "agentbreeder/demo:abc123"},
        {"code": "RELEASE_THRESHOLD", "message": "1.0"},
    ],
}
EVAL_FAILED = dict(
    RELEASE,
    seq=9,
    decision="eval_failed",
    reasons=[{"code": "RELEASE_EVAL", "message": "run-6 2/5"}],
)


def own_project(console):
    """The fixture project whose agent.yaml and stack.yaml are agent `demo`'s (before sign-in
    adds it, so sign-in registers this copy)."""
    return console.project("demo", fixture="agents")


def scenario(console, **extra):
    path = str(own_project(console).resolve())
    stack = {"stack": "demo-0123abcd", "project": path, "environment": "dev"}
    listing = {
        "deployments": [
            {
                **DEPLOYMENTS[0],
                **stack,
                "image": "agentbreeder/demo:abc123",
                "updated": "2026-11-13T10:01:00Z",
            },
            {**CANDIDATE, **stack},
            {
                **DEPLOYMENTS[0],
                "id": "e1",
                "name": "elsewhere",
                "stack": "other-89abcdef",
                "project": "/not/registered",
                "environment": "dev",
            },
        ],
        "skipped": [
            {
                "stack": "broken-00000000",
                "environment": "dev",
                "code": "STATE_BUSY",
                "reason": "a job holds the lock",
            }
        ],
    }
    console.scenario(
        list_all=listing,
        deployments=[DEPLOYMENTS[0], CANDIDATE],
        audit_rows=[RELEASE, EVAL_FAILED],
        **extra,
    )


def open_list(page, console):
    sign_in(page, console)
    page.get_by_role("navigation", name="Console").get_by_role(
        "link", name="Agents", exact=True
    ).click()
    page.wait_for_url("**/#/agents")


def test_the_list_shows_every_stack_with_candidates_and_unregistered_projects(page, console):
    scenario(console)
    open_list(page, console)
    table = page.get_by_role("table", name="Agents")
    rows = table.locator("tbody tr")
    expect(rows).to_have_count(3)
    demo = rows.filter(has=page.get_by_role("link", name="demo", exact=True))
    expect(demo).to_contain_text("compliant")
    expect(demo).to_contain_text("demo:abc123")
    expect(demo.locator(".gated")).to_have_text("passed 5/5")
    candidate = rows.filter(has_text="demo-candidate")
    expect(candidate.locator(".badge.candidate")).to_have_text("candidate, temporary")
    other = rows.filter(has_text="elsewhere")
    expect(other).to_contain_text("not registered")
    expect(other.get_by_role("link")).to_have_count(0)  # no detail without a registered project
    expect(page.get_by_role("region", name="Not read")).to_contain_text(
        "[STATE_BUSY] a job holds the lock"
    )
    expect(
        page.get_by_role("navigation", name="Console").get_by_role(
            "link", name="Agents", exact=True
        )
    ).to_have_attribute("aria-current", "page")


def test_opening_an_agent_from_all_projects_switches_the_scope(page, console):
    scenario(console)
    open_list(page, console)
    page.get_by_label("Project scope").select_option("")
    page.get_by_role("link", name="demo", exact=True).click()
    page.wait_for_url("**/#/agents/demo")
    expect(page.locator("main h1")).to_contain_text("Agent")
    expect(page.locator("main h1 .scope-chip")).to_contain_text("demo")
    expect(page.locator("main h1")).to_be_focused()
    # The sidebar keeps Agents current on the detail (navAs) once the shell honours it (#915).


def test_the_gate_strip_and_identity_strip(page, console):
    scenario(console)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/demo")
    strip = page.get_by_role("list", name="Gate")
    steps = strip.get_by_role("listitem")
    expect(steps).to_have_count(5)
    expect(steps.nth(0)).to_contain_text("Build: passed.")
    expect(steps.nth(0)).to_contain_text("agentbreeder/demo:abc123")
    expect(steps.nth(1)).to_contain_text("Candidate: in progress.")  # demo-candidate is running
    expect(steps.nth(2)).to_contain_text("Evals 5/5: passed.")
    expect(steps.nth(3)).to_contain_text("Identity: passed.")
    expect(steps.nth(4)).to_contain_text("Production: passed.")
    identity = page.locator("dl.identity-strip")
    expect(identity).to_contain_text("declared in stack.yaml")
    expect(identity).to_contain_text("owner@example.com")
    expect(identity).to_contain_text("2099-01-01")
    expect(identity).not_to_contain_text("review date has passed")
    expect(identity).to_contain_text("compliant")
    agent_file = page.get_by_role("region", name="agent.yaml")
    expect(agent_file).to_contain_text("runtime/adk@1.1.0")
    expect(agent_file).to_contain_text("none: release has no eval gate")
    overview = page.get_by_role("region", name="Production")
    expect(overview).to_contain_text("http://127.0.0.1:9001")
    expect(overview).to_contain_text("unauthenticated")
    expect(page.get_by_role("region", name="Production")).to_contain_text("demo-candidate")


def test_tabs_keep_focus_and_show_releases_and_logs(page, console):
    scenario(console)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/demo")
    tabs = page.get_by_role("navigation", name="Agent sections")
    expect(tabs.get_by_role("link", name="Overview")).to_have_attribute("aria-current", "page")
    tabs.get_by_role("link", name="Releases").click()
    page.wait_for_url("**/#/agents/demo/releases")
    expect(tabs.get_by_role("link", name="Releases")).to_be_focused()  # same section: focus stays
    releases = page.get_by_role("table", name="Releases").locator("tbody tr")
    expect(releases).to_have_count(2)
    expect(releases.nth(0)).to_contain_text("passed 5/5")
    expect(releases.nth(1)).to_contain_text("eval failed 2/5")
    expect(releases.nth(0)).to_contain_text("(unverified)")
    tabs.get_by_role("link", name="Logs").click()
    page.wait_for_url("**/#/agents/demo/logs")
    expect(page.get_by_role("link", name="Logs of demo on local", exact=True)).to_be_visible()
    expect(
        page.get_by_role("link", name="Logs of demo-candidate on local", exact=True)
    ).to_be_visible()
    tabs.get_by_role("link", name="Try it").click()
    page.wait_for_url("**/#/agents/demo/try")
    expect(page.get_by_label("Message")).to_be_visible()


def test_an_agent_without_releases_or_deployments(page, console):
    own_project(console)
    console.scenario(deployments=[], audit_rows=[])
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/demo")
    steps = page.get_by_role("list", name="Gate").get_by_role("listitem")
    for index in range(5):
        expect(steps.nth(index)).to_contain_text("not run.")
    expect(page.get_by_role("region", name="Production")).to_contain_text(
        "No production deployment"
    )
    page.goto(f"{console.origin}/#/agents/demo/releases")
    expect(page.get_by_text("No release recorded in this project's audit log.")).to_be_visible()
    page.goto(f"{console.origin}/#/agents")
    expect(page.get_by_text("No agent is deployed in any stack on this machine.")).to_be_visible()


def test_another_agents_releases_are_not_shown_as_its_own(page, console):
    # The stub fixture's agent.yaml is not agent `demo`: release rows name no agent, so the
    # strip and the Releases tab show none of them for `demo`.
    console.scenario(deployments=[DEPLOYMENTS[0]], audit_rows=[RELEASE])
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/demo/releases")
    expect(page.get_by_role("region", name="Releases")).to_contain_text(
        "its releases are not this agent's"
    )
    expect(page.get_by_role("table", name="Releases")).to_have_count(0)
    steps = page.get_by_role("list", name="Gate").get_by_role("listitem")
    expect(steps.nth(2)).to_contain_text("Evals: not run.")
    expect(page.locator("dl.identity-strip")).to_contain_text("unknown")  # stack.yaml is a stub
