"""The framework everywhere but Build (#1085 S2, console spec §6 and §9): `FrameworkBadge` under
each agent's name on the Agents list, "Not recorded", the framework filter kept in the URL query
(its count, its empty state, a reload and the 3-second refresh), the Overview's Framework,
Protocol and Capabilities rows, and Try it's Runtime and Framework rows and MODEL_STEP_LIMIT line,
with axe in light and dark.

Only `runtime/adk` exists, so the rows on another framework name runtimes the fake CLI's
`registry runtimes` answer lists (LangGraph, CrewAI); the Overview and Try it read the real
command's built-in `runtime/adk` versions."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_agents import CANDIDATE, own_project, start_release
from .test_builder_framework import FRAMEWORKS
from .test_shell import DEPLOYMENTS
from .test_try import open_try, send, turns

pytestmark = pytest.mark.ui

ADK = "runtime/adk@1.4.0"
LANGGRAPH = "runtime/langgraph@1.0.0"


def deployment(name: str, runtime: str | None, **changes) -> dict:
    """A production row of `list --all --json` in the registered project's stack."""
    row = dict(DEPLOYMENTS[0], id=f"id-{name}", name=name, runtime=runtime) | changes
    if runtime is None:
        row.pop("runtime")
    return row


def listing(console, *rows) -> dict:
    stack = {"stack": "demo-0123abcd", "project": str(own_project(console).resolve())}
    return {"deployments": [{**row, **stack, "environment": "dev"} for row in rows], "skipped": []}


ROWS = (
    deployment("demo", ADK),
    deployment("graph", LANGGRAPH),
    deployment("mystery", "runtime/mystery@2.0.0"),
    deployment("old", None),
)


def open_agents(page, console, query: str = ""):
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents{query}")
    page.get_by_role("table", name="Agents").or_(page.get_by_text("No agents on")).first.wait_for()


def rows(page):
    return page.get_by_role("table", name="Agents").locator("tbody tr")


def row_of(page, name):
    """The row whose agent link is `name` (every row here is in the registered project)."""
    return rows(page).filter(has=page.get_by_role("link", name=name, exact=True))


def framework_filter(page):
    return page.get_by_role("combobox", name="Framework")


def status(page):
    return page.get_by_role("status").filter(has_text=re.compile(r"\bagents?\b"))


def test_the_badge_sits_under_the_name_with_the_ref_beside_it(page, console):
    console.scenario(list_all=listing(console, *ROWS), runtimes=FRAMEWORKS)
    open_agents(page, console)
    expect(rows(page)).to_have_count(4)
    demo = row_of(page, "demo")
    expect(demo.locator(".badge.framework")).to_have_text("Google ADK")
    expect(demo.locator(".framework-line code")).to_have_text(ADK)  # visible, not hover only
    # The badge is in the agent's own cell, not a ninth column.
    expect(page.get_by_role("table", name="Agents").locator("thead th")).to_have_count(8)
    expect(demo.locator("td").first.locator(".badge.framework")).to_have_count(1)
    expect(row_of(page, "graph").locator(".badge.framework")).to_have_text("LangGraph")
    # A ref the registries do not hold is shown as the ref itself, once.
    mystery = row_of(page, "mystery")
    expect(mystery.locator(".badge.framework")).to_have_text("runtime/mystery@2.0.0")
    expect(mystery.locator(".framework-line")).to_have_count(0)
    expect(row_of(page, "old").locator(".badge.framework")).to_have_text("Not recorded")
    expect(status(page)).to_have_text("4 agents")


def test_the_candidate_keeps_its_badge_and_the_framework_badge(page, console):
    candidate = dict(CANDIDATE, runtime=ADK)
    console.scenario(list_all=listing(console, ROWS[0], candidate), runtimes=FRAMEWORKS)
    open_agents(page, console)
    row = row_of(page, "demo-candidate")
    expect(row.locator(".badge.candidate")).to_have_text("candidate, temporary")
    expect(row.locator(".badge.framework")).to_have_text("Google ADK")


def test_the_filter_counts_what_it_shows_and_keeps_focus(page, console):
    console.scenario(list_all=listing(console, *ROWS), runtimes=FRAMEWORKS)
    open_agents(page, console)
    select = framework_filter(page)
    options = select.locator("option")
    expect(options).to_have_text(
        ["All frameworks", "Google ADK", "LangGraph", "mystery", "Not recorded"]
    )
    select.focus()
    select.select_option(label="LangGraph")
    page.wait_for_url("**/#/agents?framework=langgraph")
    expect(rows(page)).to_have_count(1)
    expect(row_of(page, "graph")).to_have_count(1)
    expect(status(page)).to_have_text("1 of 4 agents")
    expect(select).to_be_focused()  # the route did not change: focus stays on the filter
    expect(page.locator("main h1")).not_to_be_focused()
    select.select_option(label="Not recorded")
    page.wait_for_url("**/#/agents?framework=_none")
    expect(rows(page)).to_have_count(1)
    expect(row_of(page, "old")).to_have_count(1)
    select.select_option(label="All frameworks")
    page.wait_for_url(re.compile(r"/#/agents$"))
    expect(rows(page)).to_have_count(4)
    expect(status(page)).to_have_text("4 agents")


def test_the_filter_survives_a_reload(page, console):
    console.scenario(list_all=listing(console, *ROWS), runtimes=FRAMEWORKS)
    open_agents(page, console, "?framework=adk")
    expect(rows(page)).to_have_count(1)
    page.reload()
    expect(framework_filter(page)).to_have_value("adk")
    expect(rows(page)).to_have_count(1)
    expect(status(page)).to_have_text("1 of 4 agents")


def test_a_sign_in_again_returns_to_the_filtered_list(page, console):
    """The query is part of the route a re-auth returns to (app.js RETURN_TO)."""
    console.scenario(list_all=listing(console, *ROWS), runtimes=FRAMEWORKS)
    open_agents(page, console, "?framework=langgraph")
    expect(rows(page)).to_have_count(1)
    page.route(
        "**/api/projects",
        lambda route: route.fulfill(
            status=401,
            content_type="application/json",
            body='{"data": null, "error": {"code": "SESSION", "message": "no"}, "stale": false}',
        ),
    )
    page.reload()
    expect(page.get_by_role("heading", level=1)).to_have_text("Session ended")
    page.unroute("**/api/projects")
    page.goto(console.mint())
    expect(page).to_have_url(re.compile(r"#/agents\?framework=langgraph$"))
    expect(framework_filter(page)).to_have_value("langgraph")
    expect(rows(page)).to_have_count(1)


@pytest.mark.parametrize("value", ["%3Cimg%3E", "..%2Fx", "a%20b"])
def test_an_unknown_filter_value_in_the_address_is_dropped(page, console, value):
    console.scenario(list_all=listing(console, *ROWS), runtimes=FRAMEWORKS)
    open_agents(page, console, f"?framework={value}")
    expect(framework_filter(page)).to_have_value("")
    expect(rows(page)).to_have_count(4)


def test_a_framework_no_agent_runs_has_an_empty_state_and_show_all(page, console):
    console.scenario(list_all=listing(console, *ROWS), runtimes=FRAMEWORKS)
    open_agents(page, console, "?framework=crewai")
    expect(page.get_by_text("No agents on CrewAI.")).to_be_visible()
    expect(page.get_by_role("table", name="Agents")).to_have_count(0)
    expect(framework_filter(page)).to_have_value("crewai")  # the select shows what is filtered
    expect(status(page)).to_have_text("0 of 4 agents")
    page.get_by_role("button", name="Show all frameworks").click()
    page.wait_for_url(re.compile(r"/#/agents$"))
    expect(rows(page)).to_have_count(4)
    expect(framework_filter(page)).to_be_focused()


def test_the_filter_and_its_focus_survive_the_refresh(page, console):
    """The list is read again every 3 seconds while a job runs (#1073): the filter's value,
    the count and focus stay, and a new row on the chosen framework appears."""
    job = {"job_seconds": 14}
    console.scenario(list_all=listing(console, *ROWS), runtimes=FRAMEWORKS, **job)
    start_release(page, console)
    page.goto(f"{console.origin}/#/agents?framework=adk")
    expect(rows(page)).to_have_count(1)
    select = framework_filter(page)
    select.focus()
    more = deployment("second", ADK)
    console.scenario(list_all=listing(console, *ROWS, more), runtimes=FRAMEWORKS, **job)
    expect(rows(page)).to_have_count(2, timeout=8000)
    expect(status(page)).to_have_text("2 of 5 agents")
    expect(select).to_have_value("adk")
    expect(select).to_be_focused()


def test_the_overview_shows_the_framework_protocol_and_capabilities(page, console):
    """The agents fixture's agent.yaml is on runtime/adk@1.1.0: the real `registry runtimes`
    names it "Google ADK" through the legacy profile."""
    console.scenario(list_all=listing(console, *ROWS), deployments=[DEPLOYMENTS[0]])
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/demo")
    facts = page.get_by_role("region", name="agent.yaml").locator("dl.facts")
    expect(facts).to_contain_text("Runtime" + "runtime/adk@1.1.0")
    expect(facts.locator(".badge.framework")).to_have_text("Google ADK", timeout=30000)
    expect(facts).to_contain_text("Protocol" + "1 (v1 profile, before the kit)")
    expect(facts).to_contain_text(re.compile(r"Capabilities\s*knowledge"))


def test_an_overview_runtime_not_in_the_registries_says_so(page, console):
    own_project(console)  # agent `demo`'s project, registered by sign-in
    console.scenario(deployments=[DEPLOYMENTS[0]], runtimes=FRAMEWORKS[1:3])
    sign_in(page, console)
    page.goto(f"{console.origin}/#/agents/demo")
    facts = page.get_by_role("region", name="agent.yaml").locator("dl.facts")
    expect(facts.locator(".badge.framework")).to_have_text("runtime/adk@1.1.0")
    expect(facts).to_contain_text("Protocol" + "This version is not usable in the registries here")


def test_try_it_shows_the_runtime_and_framework_rows(page, console):
    console.scenario(deployments=DEPLOYMENTS)
    open_try(page, console)
    send(page, "hi")
    details = turns(page).first.locator("details.inspector")
    details.locator("summary").click()
    facts = details.locator("dl.facts")
    expect(facts).to_contain_text("Runtime" + "runtime/adk@1.0.6")
    expect(facts.locator(".badge.framework")).to_have_text("Google ADK", timeout=30000)
    expect(facts).to_contain_text("Not measured: the runtime did not report it for this turn.")


def test_try_it_explains_model_step_limit(page, console):
    fields = {
        "code": "MODEL_STEP_LIMIT",
        "error": "the turn reached its model-call limit",
        "correlation_id": "e" * 32,
    }
    console.scenario(deployments=DEPLOYMENTS, invoke={"status": 502, "fields": fields})
    open_try(page, console)
    send(page, "hi")
    turn = turns(page).first
    expect(turn).to_contain_text(
        "The agent reached its limit on model calls for one turn while the model still asked "
        "for tools, so there is no reply"
    )
    expect(turn).to_contain_text("[MODEL_STEP_LIMIT]")


def test_the_agents_filter_and_badges_have_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(
        list_all=listing(console, *ROWS), runtimes=FRAMEWORKS, deployments=[DEPLOYMENTS[0]]
    )
    sign_in(scheme_page, console)
    found = []
    for query in ("", "?framework=langgraph", "?framework=crewai"):
        scheme_page.goto(f"{console.origin}/#/agents{query}")
        framework_filter(scheme_page).wait_for()
        scheme_page.wait_for_timeout(400)
        found += blocking(scheme_page, f"Agents{query}")
    scheme_page.goto(f"{console.origin}/#/agents/demo")
    scheme_page.locator("dl.facts .badge.framework").wait_for()
    scheme_page.wait_for_timeout(400)  # as test_a11y waits
    found += blocking(scheme_page, "Agent overview, framework rows")
    assert found == []
