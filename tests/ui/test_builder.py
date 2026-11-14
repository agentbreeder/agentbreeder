"""Build a new agent (#833, M17 console half): Home → Build an agent, the form, the sticky preview
of every file as the real `new agent --from-spec --dry-run --json` returns it (the fake CLI hands
that command to the real one), Create pinned to the previewed plan, and only combinations run end
to end created."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)

pytestmark = pytest.mark.ui

PROMPT = "You are helper, an agent that answers questions about support hours."


def home(console) -> Path:
    return Path(console.state).parent / "home"


def open_builder(page, console) -> None:
    sign_in(page, console, project=False)
    page.get_by_role("link", name="Build an agent").click()
    page.wait_for_url("**/#/build")
    page.get_by_role("heading", level=1, name="Build an agent").wait_for()


def fill(page, name: str = "helper") -> None:
    page.get_by_label("Name", exact=True).fill(name)
    page.get_by_label("Prompt (the agent's instructions)").fill(PROMPT)


def test_build_preview_then_create_adds_the_project(page, console):
    open_builder(page, console)
    preview = page.get_by_role("button", name="Preview", exact=True)
    expect(preview).to_be_disabled()
    expect(page.get_by_text("Type the agent's name first.")).to_be_visible()
    fill(page)
    preview.click()
    pane = page.get_by_role("complementary", name="Preview: nothing is written yet")
    expect(pane).to_be_visible()
    expect(pane).to_contain_text("GEMINI_API_KEY")
    expect(pane).to_contain_text("Run end to end: model, prompt, evals.")
    files = pane.get_by_role("group", name="Files")
    expect(files.get_by_role("button", name="agent.yaml", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(pane.get_by_label("agent.yaml, as written")).to_contain_text("--from-spec")
    files.get_by_role("button", name="src/prompt/prompt.md").click()
    expect(pane.get_by_label("src/prompt/prompt.md, as written")).to_have_text(PROMPT)
    files.get_by_role("button", name="src/evals/golden.jsonl").click()
    expect(pane.get_by_label("src/evals/golden.jsonl, as written")).to_contain_text(
        '"id": "hello"'
    )
    destination = home(console) / "agentbreeder-projects" / "helper"
    assert not destination.exists()
    page.get_by_role("button", name="Create agent and add project").click()
    page.wait_for_url("**/#/")
    scope = page.get_by_role("combobox", name="Project scope")
    expect(scope.locator("option:checked")).to_have_text("helper (dev)")
    assert (destination / "src/prompt/prompt.md").read_text() == PROMPT + "\n"
    assert (destination / "agent.lock").is_file()


def test_an_edit_marks_the_preview_out_of_date(page, console):
    open_builder(page, console)
    fill(page)
    page.get_by_role("button", name="Preview", exact=True).click()
    create = page.get_by_role("button", name="Create agent and add project")
    expect(create).to_be_enabled()
    page.get_by_label("Prompt (the agent's instructions)").fill(PROMPT + " Be brief.")
    pane = page.get_by_role("complementary", name="Preview (out of date)")
    expect(pane).to_contain_text("The form changed since this preview.")
    expect(create).to_be_disabled()
    expect(page.get_by_text("The form changed: preview again.")).to_be_visible()
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(
        page.get_by_role("complementary", name="Preview: nothing is written yet")
    ).to_be_visible()
    expect(create).to_be_enabled()


def test_a_combination_not_run_end_to_end_is_previewed_but_not_created(page, console):
    open_builder(page, console)
    fill(page)
    page.get_by_label("Model provider").select_option("openai")
    page.get_by_role("button", name="Preview", exact=True).click()
    pane = page.get_by_role("complementary", name="Preview: nothing is written yet")
    expect(pane).to_contain_text("Not run end to end yet: the openai provider.")
    expect(pane).to_contain_text("OPENAI_API_KEY")
    expect(page.get_by_role("button", name="Create agent and add project")).to_be_disabled()
    expect(page.get_by_text("This combination has not been run end to end.")).to_be_visible()


def test_the_eval_cases_are_edited_and_written(page, console):
    open_builder(page, console)
    fill(page)
    page.get_by_role("button", name="Add a case").click()
    preview = page.get_by_role("button", name="Preview", exact=True)
    expect(preview).to_be_disabled()
    expect(page.get_by_text("Fix the eval cases first (1 to fix).")).to_be_visible()
    # The builder's cases take no cited sources: a new agent has no knowledge.
    expect(page.get_by_label("Cites one of these sources", exact=False)).to_have_count(0)
    page.get_by_label("Turn 1", exact=True).fill("When is support open?")
    page.get_by_label("Reply matches (one pattern per line)").fill("9")
    page.get_by_label("Reference answer (what a correct reply says)").fill("9am to 5pm Pacific.")
    expect(preview).to_be_enabled()
    preview.click()
    pane = page.get_by_role("complementary", name="Preview: nothing is written yet")
    pane.get_by_role("group", name="Files").get_by_role(
        "button", name="src/evals/golden.jsonl"
    ).click()
    text = pane.get_by_label("src/evals/golden.jsonl, as written").inner_text()
    cases = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert [case["id"] for case in cases] == ["hello", "case-1"]
    assert cases[1]["turns"] == ["When is support open?"]
    assert cases[1]["expect"] == {"must_match": ["9"]}


def test_the_clis_own_refusal_is_shown(page, console):
    destination = home(console) / "agentbreeder-projects" / "helper"
    destination.mkdir(parents=True)
    (destination / "notes.txt").write_text("mine")
    open_builder(page, console)
    fill(page)
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_role("alert")).to_be_visible()
    expect(page.get_by_role("button", name="Create agent and add project")).to_have_count(0)


def test_the_builder_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    open_builder(scheme_page, console)
    fill(scheme_page)
    scheme_page.get_by_role("button", name="Preview", exact=True).click()
    scheme_page.get_by_role("complementary", name="Preview: nothing is written yet").wait_for()
    assert blocking(scheme_page, "Agent builder preview") == []
