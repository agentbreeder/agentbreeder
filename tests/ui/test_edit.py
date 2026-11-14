"""Prompt editing and "Move agent to this version" (#826, M10 console half) over a copy of
Northwind. The fake CLI runs the real `registry edit` and `agent set-ref`, so the files written
are the real ones; the build after the move is the scenario's short job."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from agentbreeder import registry

from .test_registry import PROMPTS, open_northwind

pytestmark = pytest.mark.ui

NAME = "northwind-support"
ADDED = "Always answer in one short paragraph."


def open_prompt(page, console):
    project = open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/prompts/{NAME}")
    page.get_by_role("region", name="Edit 1.0.1").wait_for()
    return project


def test_edit_review_save_then_move_the_agent(page, console):
    project = open_prompt(page, console)
    editor = page.get_by_role("region", name="Edit 1.0.1")
    text = editor.get_by_label("Text of prompt.md")
    original = (project / PROMPTS / "1.0.1" / "prompt.md").read_text()
    expect(text).to_have_value(original)
    expect(editor.get_by_text("No changes")).to_be_visible()
    expect(editor.get_by_role("button", name="Review changes")).to_be_disabled()

    text.press("End")
    text.press("ControlOrMeta+End")
    text.type(f"\n{ADDED}\n")
    expect(editor.get_by_text("Unsaved changes")).to_be_visible()
    text.press("ControlOrMeta+s")  # the shortcut opens the review
    review = editor.get_by_role("region", name="Review")
    expect(review).to_contain_text("1.0.1 → 1.0.2")
    expect(review).to_contain_text(ADDED)
    assert not (project / PROMPTS / "1.0.2").exists()  # a review writes nothing

    review.get_by_role("button", name="Save as 1.0.2").click()
    expect(
        page.get_by_role("status").filter(has_text="Saved prompt/northwind-support@1.0.2")
    ).to_be_visible()
    saved = (project / PROMPTS / "1.0.2" / "prompt.md").read_text()
    assert saved == original + f"\n{ADDED}\n"
    assert (project / PROMPTS / "1.0.1" / "prompt.md").read_text() == original
    timeline = page.get_by_role("table", name="Versions, newest first")
    expect(timeline.get_by_role("row").nth(1)).to_contain_text("1.0.2")
    expect(page.get_by_role("region", name="Version 1.0.2")).to_be_visible()

    move = page.get_by_role("region", name="Move agent to this version")
    move.get_by_role("button", name="Move agent to 1.0.2").click()
    expect(move.get_by_label("agent.yaml change")).to_contain_text(
        f"+prompt: {{ref: prompt/{NAME}@1.0.2}}"
    )
    assert f"prompt/{NAME}@1.0.1" in (project / "agent.yaml").read_text()
    move.get_by_role("button", name="Write agent.yaml").click()
    expect(move.get_by_role("status")).to_contain_text(f"agent.yaml now uses prompt/{NAME}@1.0.2")
    assert f"prompt: {{ref: prompt/{NAME}@1.0.2}}" in (project / "agent.yaml").read_text()
    expect(timeline.get_by_role("row").nth(1)).to_contain_text("declared in agent.yaml")

    move.get_by_role("button", name="Build and update agent.lock").click()
    dock = page.get_by_role("complementary", name="Jobs")
    expect(dock).to_contain_text("finished.", timeout=15000)


def test_a_second_tab_saving_the_same_version_is_told_to_pick_another(page, console):
    project = open_prompt(page, console)
    editor = page.get_by_role("region", name="Edit 1.0.1")
    text = editor.get_by_label("Text of prompt.md")
    text.press("ControlOrMeta+End")
    text.type("\nOne more line.\n")
    editor.get_by_role("button", name="Review changes").click()
    save = editor.get_by_role("button", name="Save as 1.0.2")
    expect(save).to_be_visible()
    # Another tab (here: the CLI) publishes 1.0.2 first.
    ref = registry.Ref.parse(f"prompt/{NAME}@1.0.1")
    registry.edit_version(ref, project, sets=[("prompt.md", b"Other tab.\n")])
    save.click()
    expect(editor.get_by_role("alert")).to_contain_text("That version exists already")
    expect(editor.get_by_role("button", name="Save as 1.0.2")).to_have_count(0)
    # The text is kept for another try, which offers the next free version.
    assert "One more line." in text.input_value()
    timeline = page.get_by_role("table", name="Versions, newest first")
    expect(timeline.get_by_role("row").nth(1)).to_contain_text("1.0.2")  # re-read
    expect(editor.get_by_label("New version")).to_contain_text("1.0.3")
    editor.get_by_role("button", name="Review changes").click()
    expect(editor.get_by_role("button", name="Save as 1.0.3")).to_be_visible()


def test_discard_asks_first_and_restores_the_text(page, console):
    project = open_prompt(page, console)
    original = (project / PROMPTS / "1.0.1" / "prompt.md").read_text()
    editor = page.get_by_role("region", name="Edit 1.0.1")
    text = editor.get_by_label("Text of prompt.md")
    text.press("ControlOrMeta+End")
    text.type("\nscratch\n")
    editor.get_by_role("button", name="Discard").click()
    dialog = page.get_by_role("dialog", name="Discard your changes?")
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
    dialog.get_by_role("button", name="Discard").click()
    expect(text).to_have_value(original)


def test_showing_another_version_asks_while_there_are_unsaved_changes(page, console):
    open_prompt(page, console)
    editor = page.get_by_role("region", name="Edit 1.0.1")
    text = editor.get_by_label("Text of prompt.md")
    text.press("ControlOrMeta+End")
    text.type("\nkeep me\n")
    timeline = page.get_by_role("table", name="Versions, newest first")
    timeline.get_by_role("button", name="Show 1.0.0").click()
    dialog = page.get_by_role("dialog", name="Discard your changes?")
    dialog.get_by_role("button", name="Cancel").click()
    assert "keep me" in text.input_value()
    timeline.get_by_role("button", name="Show 1.0.0").click()
    page.get_by_role("dialog", name="Discard your changes?").get_by_role(
        "button", name="Discard"
    ).click()
    expect(page.get_by_role("region", name="Edit 1.0.0")).to_be_visible()
    # Nothing is unsaved now: switching back does not ask.
    timeline.get_by_role("button", name="Show 1.0.1").click()
    expect(page.get_by_role("region", name="Edit 1.0.1")).to_be_visible()
    expect(page.get_by_role("dialog")).to_have_count(0)
