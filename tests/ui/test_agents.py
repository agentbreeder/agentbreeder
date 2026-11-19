"""Agents (#823, M7 console half, PR 2): the list across every stack (`list --all --json`, the fake
CLI answers), opening a registered agent's detail from "All projects", the gate strip, the identity
strip, and the Overview, Try it, Releases and Logs tabs."""

from __future__ import annotations

import re

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
    expect(page.locator("main h1")).to_contain_text("Agent demo")
    expect(page).to_have_title("Agent demo · AgentBreeder console")
    expect(page.locator("main h1 .scope-chip")).to_contain_text("demo")
    expect(page.locator("main h1")).to_be_focused()
    # The sidebar keeps Agents current on the detail (navAs).
    expect(
        page.get_by_role("navigation", name="Console").get_by_role(
            "link", name="Agents", exact=True
        )
    ).to_have_attribute("aria-current", "page")


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
    # The gate line (design v2 §3, #1116): a --brand ring for passed steps only, beside the word.
    expect(steps.nth(0).locator("svg.gate-check")).to_have_count(1)
    expect(steps.nth(1).locator("svg.gate-check")).to_have_count(0)
    ring = steps.nth(0).locator("svg.gate-check circle.gate-ring")
    stroke = ring.evaluate("(el) => getComputedStyle(el).stroke")
    brand = page.evaluate(
        "(() => { const s = document.createElement('span'); s.style.color = 'var(--brand)';"
        " document.body.append(s); const c = getComputedStyle(s).color; s.remove(); return c; })()"
    )
    assert stroke == brand
    identity = page.locator("dl.identity-strip")
    expect(identity.locator("svg.gate-check")).to_have_count(1)
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


def test_an_identity_no_one_has_checked_since_deploy_says_why_status_differs(page, console):
    """#1078: a deploy records no compliance (the list row has null) and only `status` or
    `reconcile` do, so `agentbreeder status` said compliant while the console said only "not
    checked". The console now says where its value comes from and where to re-check it."""
    path = str(own_project(console).resolve())
    stack = {"stack": "demo-0123abcd", "project": path, "environment": "dev"}
    unchecked = {**DEPLOYMENTS[0], "compliance": None, "updated": "2026-11-13T10:01:00Z"}
    console.scenario(
        list_all={"deployments": [{**unchecked, **stack}], "skipped": []},
        deployments=[unchecked],
        audit_rows=[RELEASE],
    )
    open_list(page, console)
    row = page.get_by_role("table", name="Agents").locator("tbody tr")
    expect(row).to_have_count(1)
    expect(row.locator(".identity")).to_have_text("not checked since deploy")
    expect(row.locator("svg.gate-check")).to_have_count(0)
    expect(page.get_by_role("region", name="Deployed agents")).to_contain_text(
        "A deploy records none"
    )
    page.get_by_role("link", name="demo", exact=True).click()
    page.wait_for_url("**/#/agents/demo")
    steps = page.get_by_role("list", name="Gate").get_by_role("listitem")
    expect(steps.nth(3)).to_contain_text("Identity: not run.")
    expect(steps.nth(3)).to_contain_text("not checked since deploy")
    identity = page.locator("dl.identity-strip")
    expect(identity.locator(".identity")).to_have_text("not checked since deploy")
    expect(identity.locator("svg.gate-check")).to_have_count(0)
    expect(identity).to_contain_text(
        "For a deployment bound to a principal, status and reconcile check the identity again "
        "and record the result, so agentbreeder status can say compliant while this does not."
    )
    expect(identity).to_contain_text(
        "The identity of a deployment with no principal is never checked again."
    )
    identity.get_by_role("link", name="Re-verify on Deployments").click()
    page.wait_for_url("**/#/deployments")
    deployments = page.get_by_role("table", name="Deployments")
    expect(deployments.locator("tbody tr")).to_contain_text("not checked since deploy")
    expect(page.get_by_role("button", name="Re-verify (reconcile)")).to_be_visible()


def test_a_recorded_identity_says_what_recorded_it(page, console):
    scenario(console)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/demo")
    identity = page.locator("dl.identity-strip")
    expect(identity).to_contain_text("recorded by the last status or reconcile")
    expect(identity.get_by_role("link", name="Re-verify on Deployments")).to_have_count(0)


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


# -- refresh around a release (#1073) ---------------------------------------------------------


def listing_of(console, *rows):
    stack = {"stack": "demo-0123abcd", "project": str(own_project(console).resolve())}
    return {"deployments": [{**row, **stack, "environment": "dev"} for row in rows], "skipped": []}


def start_release(page, console):
    """Run a release from Build and release, then open Agents from the sidebar (the console's
    jobs keep following it)."""
    sign_in(page, console)
    page.goto(f"{console.origin}/#/release")
    page.get_by_role("button", name="Run release").click()
    expect(
        page.get_by_role("complementary", name="Jobs").locator("section.job.running")
    ).to_have_count(1)


def test_the_list_follows_a_release_while_it_runs_and_when_it_ends(page, console):
    """lane V's M17 run: the list kept the candidate as "starting" and showed no production
    until the page was loaded again. Now it is read again every few seconds while a job runs,
    and its last releases when the job ends."""
    job = {"job_seconds": 14}
    production = dict(DEPLOYMENTS[0], image="agentbreeder/demo:old")
    console.scenario(list_all=listing_of(console, production), audit_rows=[], **job)
    start_release(page, console)
    page.get_by_role("navigation", name="Console").get_by_role(
        "link", name="Agents", exact=True
    ).click()
    rows = page.get_by_role("table", name="Agents").locator("tbody tr")
    expect(rows).to_have_count(1)
    expect(rows.first.locator(".gated")).to_have_text("not released")
    # The candidate starts while the release runs: the list shows it without a reload. The audit
    # log already holds a release here, but last releases are read only when a job ends (each
    # is two CLI children per project), not on every poll of the list.
    console.scenario(
        list_all=listing_of(console, production, CANDIDATE), audit_rows=[RELEASE], **job
    )
    expect(rows.filter(has_text="demo-candidate")).to_have_count(1, timeout=8000)
    expect(rows.first.locator(".gated")).to_have_text("not released")
    # The release promotes and removes its candidate; once the job ends, the last release shows.
    promoted = dict(DEPLOYMENTS[0], image="agentbreeder/demo:abc123")
    console.scenario(list_all=listing_of(console, promoted), audit_rows=[RELEASE], **job)
    expect(rows).to_have_count(1, timeout=8000)
    expect(rows.first).to_contain_text("demo:abc123")
    expect(
        page.get_by_role("complementary", name="Jobs").locator("section.job")
    ).not_to_have_class(re.compile(r"\brunning\b"), timeout=20000)
    expect(rows.first.locator(".gated")).to_have_text("passed 5/5", timeout=10000)


def flat(text: str) -> str:
    """inner_text() as one line: "Build\n: loading." reads "Build: loading."."""
    return re.sub(r"\s+", " ", text).replace(" :", ":")


def test_the_gate_strip_says_loading_until_releases_answer(page, console):
    """#1073 (lane V): /deployments answered before /releases, and for that gap the strip said
    "No release recorded" for an agent that had one. It says "Loading releases…" instead."""
    console.scenario(deployments=[DEPLOYMENTS[0]], audit_rows=[RELEASE])
    own_project(console)
    sign_in(page, console)
    # Every key again: the fake CLI's scenario file is replaced, not merged.
    console.scenario(deployments=[DEPLOYMENTS[0]], audit_rows=[RELEASE], audit_seconds=5)
    page.goto(f"{console.origin}/#/agents/demo")
    gate = page.get_by_role("list", name="Gate")
    steps = gate.get_by_role("listitem")
    # Once deployments answer (Identity passed) and while releases don't: one snapshot, because a
    # negative expect() retries until the read answers and would pass without the fix.
    expect(steps.nth(3)).to_contain_text("Identity: passed.")
    strip = flat(gate.inner_text())
    assert "Build: loading." in strip and "Loading releases…" in strip
    assert "No release recorded" not in strip
    # Production's "differs" comes from the releases: it waits for them, never "passed" early.
    assert "Production: loading." in strip, strip
    expect(page.locator("dl.identity-strip")).not_to_contain_text("Loading deployments…")
    expect(steps.nth(0)).to_contain_text("Build: passed.", timeout=15000)
    expect(steps.nth(2)).to_contain_text("Evals 5/5: passed.")
    expect(steps.nth(4)).to_contain_text("Production: passed.")


@pytest.mark.parametrize("tab", ["overview", "logs", "traces"])
def test_the_agent_tabs_say_loading_until_deployments_answer(page, console, tab):
    """#1094 review: Overview, Logs and Traces said "no deployment" before the read answered."""
    console.scenario(deployments=[DEPLOYMENTS[0]], audit_rows=[RELEASE])
    own_project(console)
    sign_in(page, console)
    console.scenario(deployments=[DEPLOYMENTS[0]], audit_rows=[RELEASE], list_seconds=5)
    page.goto(f"{console.origin}/#/agents/demo/{tab}")
    main = page.locator("main")
    # Once the agent.yaml and releases are in (Build passed), while deployments are not: a
    # snapshot, because a negative expect() retries until the read answers.
    expect(main.get_by_role("list", name="Gate")).to_contain_text("Build: passed.")
    text = flat(main.inner_text())
    assert "Identity: loading." in text, text  # deployments still out
    tab_card = text.split("Overview Try it Releases Logs Traces", 1)[1]  # the tab's own card
    assert "Loading deployments…" in tab_card, tab_card
    for wrong in ("No production deployment", "No running deployment", "No deployment of this"):
        assert wrong not in text, text
    expect(main).not_to_contain_text("Loading deployments…", timeout=15000)
    row = DEPLOYMENTS[0]
    expect(main).to_contain_text(row["id"] if tab == "overview" else row["name"])


def test_a_project_switch_shows_loading_not_the_other_projects_agent(page, console):
    """#1094 review: the same agent name open across a project switch kept the previous
    project's views, so its production card and identity showed as this project's."""
    console.scenario(deployments=[DEPLOYMENTS[0]], audit_rows=[RELEASE])
    own_project(console)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    page.get_by_label("Absolute path").fill(str(console.project("other", fixture="agents")))
    page.get_by_role("button", name="Add project").click()
    page.wait_for_url("**/#/services")
    page.goto(f"{console.origin}/#/agents/demo")
    steps = page.get_by_role("list", name="Gate").get_by_role("listitem")
    expect(steps.nth(3)).to_contain_text("Identity: passed.", timeout=15000)
    expect(page.locator("main")).to_contain_text("http://127.0.0.1:9001")
    console.scenario(deployments=[DEPLOYMENTS[0]], audit_rows=[RELEASE], list_seconds=5)
    page.get_by_role("combobox", name="Project scope").select_option(label="demo (dev)")
    expect(steps.nth(3)).to_contain_text("Identity: loading.")
    text = flat(page.locator("main").inner_text())
    assert "127.0.0.1:9001" not in text, text  # the other project's production card
    assert "Loading deployments…" in text.split("Overview Try it Releases Logs Traces", 1)[1], text
    expect(steps.nth(3)).to_contain_text("Identity: passed.", timeout=15000)


def test_the_agent_page_shows_the_release_when_the_job_ends(page, console):
    """lane V's M17 run: the gate strip said "No release recorded" after a console release."""
    job = {"job_seconds": 8}
    console.scenario(deployments=[DEPLOYMENTS[0]], audit_rows=[], **job)
    own_project(console)
    start_release(page, console)
    page.goto(f"{console.origin}/#/agents/demo")
    steps = page.get_by_role("list", name="Gate").get_by_role("listitem")
    expect(steps.nth(0)).to_contain_text("Build: not run.")
    expect(steps.nth(0)).to_contain_text("No release recorded")
    console.scenario(deployments=[DEPLOYMENTS[0]], audit_rows=[RELEASE], **job)
    expect(
        page.get_by_role("complementary", name="Jobs").locator("section.job")
    ).not_to_have_class(re.compile(r"\brunning\b"), timeout=20000)
    expect(steps.nth(0)).to_contain_text("Build: passed.", timeout=10000)
    expect(steps.nth(2)).to_contain_text("Evals 5/5: passed.")
    page.get_by_role("link", name="Releases", exact=True).click()
    expect(page.get_by_role("table", name="Releases")).to_contain_text("passed 5/5")
