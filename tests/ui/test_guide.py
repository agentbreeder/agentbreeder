"""The guided path in the console (#820, M4): Create starter, the Home checklist, the Next pill,
empty-state buttons and finished-job result rows, all from `next --json` (the fake CLI serves the
fixtures in fixtures/guide, which pass `guide.Guide`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)

pytestmark = pytest.mark.ui

GUIDES = Path(__file__).resolve().parent / "fixtures" / "guide"
STATE_WORDS = {"done": "Done", "next": "Next", "blocked": "Blocked", "todo": "To do"}


def guide(name: str, project: Path) -> dict:
    return json.loads((GUIDES / f"{name}.json").read_text().replace("@PROJECT@", str(project)))


def checklist(page):
    return page.get_by_role("region", name="Guided path")


def test_create_starter_from_an_empty_home_reaches_the_checklist(page, console):
    sign_in(page, console, project=False)
    create = page.get_by_role("button", name="Create starter")
    # The empty state's primary action, off with its reason until a name is typed.
    expect(create).to_have_class("action primary")
    expect(create).to_be_disabled()
    expect(page.get_by_text("Type a name first.")).to_be_visible()
    page.get_by_label("Agent name").fill("Hello")
    expect(create).to_be_disabled()
    expect(page.get_by_text("Use a lowercase letter first")).to_be_visible()
    page.get_by_label("Agent name").fill("hello")
    create.click()
    # Registered, in scope, and Home shows its checklist from `next --json`.
    scope = page.get_by_role("combobox", name="Project scope")
    expect(scope.locator("option:checked")).to_have_text("hello (dev)")
    expect(checklist(page)).to_be_visible()
    expect(page.get_by_role("link", name="Next: Services")).to_be_visible()
    destination = Path(console.state).parent / "home" / "agentbreeder-projects" / "hello"
    assert (destination / "agent.yaml").is_file()


def test_create_starter_shows_new_agents_own_error(page, console):
    console.scenario(new_error={"code": "DIR_NOT_EMPTY", "message": "hello is not empty"})
    sign_in(page, console, project=False)
    page.get_by_label("Agent name").fill("hello")
    page.get_by_role("button", name="Create starter").click()
    expect(page.get_by_role("alert")).to_contain_text("hello is not empty")
    expect(page.get_by_role("combobox", name="Project scope").locator("option")).to_have_count(1)


@pytest.mark.parametrize("name", ["keys", "services", "build", "lock-stale", "done"])
def test_the_checklist_matches_next_json_at_every_step(page, console, name):
    console.scenario(guide=name)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    expected = guide(name, console.project())
    rows = checklist(page).get_by_role("listitem")
    expect(rows).to_have_count(len(expected["steps"]))
    for index, step in enumerate(expected["steps"]):
        row = rows.nth(index)
        expect(row).to_contain_text(step["title"])
        expect(row).to_contain_text(STATE_WORDS[step["state"]])
        expect(row).to_have_class(f"step {step['state']}")
        if step["reason"] and step["state"] != "done":
            expect(row).to_contain_text(step["reason"]["message"])
    current = [s for s in expected["steps"] if s["state"] in ("next", "blocked")]
    marked = checklist(page).locator("[aria-current='step']")
    expect(marked).to_have_count(len(current))
    pill = page.locator("#next-pill")
    if current:
        expect(marked).to_contain_text(current[0]["title"])
        expect(pill).to_have_text(f"Next: {current[0]['title']}")
    else:
        expect(pill).to_have_text("All steps done")


def test_a_stale_lock_shows_the_cli_instruction_instead_of_a_button(page, console):
    console.scenario(guide="lock-stale")
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    build = checklist(page).locator("[aria-current='step']")
    expect(build).to_contain_text("Run this in a terminal:")
    expect(build.locator("pre")).to_contain_text("agentbreeder build")
    expect(build.get_by_role("button", name="Build", exact=True)).to_have_count(0)
    expect(build.get_by_role("button", name="Copy Build command")).to_be_visible()


def test_a_step_button_opens_its_screen_never_runs_the_command(page, console):
    console.scenario(guide="services")
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    page.get_by_role("button", name="Review and start services").click()
    page.wait_for_url("**/#/services")
    expect(page.get_by_role("heading", level=1)).to_contain_text("Services")


def test_the_keys_step_links_the_fixed_key_page_with_its_host(page, console):
    console.scenario(guide="keys")
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    link = page.get_by_role("link", name="Open the provider's key page")
    expect(link).to_have_attribute("href", "https://aistudio.google.com/apikey")
    expect(link).to_have_attribute("rel", "noopener noreferrer")
    expect(checklist(page)).to_contain_text("(aistudio.google.com)")


def test_build_from_the_checklist_then_the_job_result_row_reads_the_guide_again(page, console):
    console.scenario(guide="build", job_seconds=3)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    page.get_by_role("button", name="Build", exact=True).click()
    dock = page.get_by_role("complementary", name="Jobs")
    expect(dock).to_be_visible()
    # While the job runs, the checklist says it is checked again at the end.
    expect(checklist(page)).to_contain_text("checked again when it ends")
    console.scenario(guide="done")  # what `next` answers once the build has ended
    expect(dock.locator(".result-row")).to_contain_text("Every step of the guided path is done.")
    expect(page.locator("#next-pill")).to_have_text("All steps done")
    # The done Try it step opens Try it (#823), with its command to copy as well.
    try_it = checklist(page).get_by_role("listitem").last
    expect(try_it.get_by_role("button", name="Try it", exact=True)).to_be_visible()
    expect(try_it).to_contain_text("Or in a terminal:")
    expect(try_it.locator("pre")).to_contain_text("invoke hello Hello")


def test_an_empty_deployments_screen_offers_the_next_step(page, console):
    console.scenario(guide="build")
    sign_in(page, console)
    page.goto(f"{console.origin}/#/deployments")
    expect(page.get_by_text("Nothing deployed.")).to_be_visible()
    expect(page.locator(".next-step")).to_contain_text("Next: Build")
    expect(page.locator(".next-step").get_by_role("button", name="Build")).to_have_class(
        "action primary"
    )


def test_a_failed_guide_read_says_so(page, console):
    console.scenario(guide=None)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    expect(checklist(page)).to_contain_text("The checklist could not be computed")
    expect(page.locator("#next-pill")).to_have_count(0)


def test_check_again_reads_the_guide_after_a_fix_made_in_a_terminal(page, console):
    console.scenario(guide="lock-stale")
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    expect(page.locator("#next-pill")).to_have_text("Next: Build")
    console.scenario(guide="done")  # `agentbreeder build` ran in a terminal, then a release
    checklist(page).get_by_role("button", name="Check again").click()
    expect(page.locator("#next-pill")).to_have_text("All steps done")


def test_a_failed_read_after_a_good_one_shows_the_last_answer_and_why(page, console):
    console.scenario(guide="services")
    sign_in(page, console)
    page.goto(f"{console.origin}/#/")
    expect(page.locator("#next-pill")).to_have_text("Next: Services")
    console.scenario(guide=None)
    checklist(page).get_by_role("button", name="Check again").click()
    expect(checklist(page)).to_contain_text(
        "Showing the last answer; checking again failed: The checklist could not be computed"
    )
    expect(page.locator("#next-pill")).to_have_text("Next: Services (last known)")


def test_the_pill_is_only_for_a_project_in_scope(page, console):
    sign_in(page, console)
    expect(page.locator("#next-pill")).to_have_text("Next: Services")
    page.get_by_role("combobox", name="Project scope").select_option("")
    expect(page.locator("#next-pill")).to_have_count(0)
    page.goto(f"{console.origin}/#/")
    expect(checklist(page)).to_have_count(0)


@pytest.mark.parametrize("name", ["services", "lock-stale", "done"])
def test_the_checklist_has_no_serious_axe_findings(scheme_page, console, name):  # noqa: F811
    console.scenario(guide=name)
    sign_in(scheme_page, console)
    scheme_page.goto(f"{console.origin}/#/")
    checklist(scheme_page).get_by_role("listitem").first.wait_for()
    scheme_page.wait_for_timeout(300)
    assert blocking(scheme_page, f"home checklist, {name}") == []


def test_the_empty_home_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    sign_in(scheme_page, console, project=False)
    scheme_page.get_by_role("button", name="Create starter").wait_for()
    assert blocking(scheme_page, "home, empty") == []
