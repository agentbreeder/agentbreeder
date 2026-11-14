"""The tool editor and "Fork to edit" (#828, M12 console half, part 3) over a copy of Northwind.
The fake CLI runs the real `registry edit` and `registry fork`, so the CLI's tool check runs on the
review and the files written are the real ones. The console gets a shared registry root
(AGENTBREEDER_REGISTRY_PATH) for the fork tests."""

from __future__ import annotations

import shutil

import pytest
from playwright.sync_api import expect

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_registry import northwind, open_northwind

pytestmark = pytest.mark.ui

TOOLS = "registry/tool/orders"
SOURCE = "src/orders.py"
ADDED = "\n\ndef helper() -> int:\n    return 1\n"


@pytest.fixture
def console_env(tmp_path):
    shared = tmp_path / "shared"
    (shared / "tool").mkdir(parents=True)
    return {"AGENTBREEDER_REGISTRY_PATH": str(shared)}


def share_orders(console) -> None:
    """Move Northwind's tool/orders to the shared root, so the project does not own it."""
    project = northwind(console)
    shared = console.state.parent / "shared" / "tool" / "orders"
    shutil.move(project / TOOLS, shared)


def type_at_end(editor, text: str) -> None:
    area = editor.get_by_label(f"Text of {SOURCE}")
    area.press("ControlOrMeta+End")
    area.type(text)


def test_a_tool_edit_needs_the_code_confirm_before_it_saves(page, console):
    project = open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    editor = page.get_by_role("region", name="Edit 1.0.0")
    expect(editor).to_contain_text("the eval gate is a quality gate, not a security review")
    # Only Python under src/ opens; tool.yaml (what the model sees) is listed as not editable.
    expect(editor.get_by_text(f"File {SOURCE}")).to_be_visible()
    expect(editor).to_contain_text("tool.yaml (the console edits only Python source files")
    original = (project / TOOLS / "1.0.0" / SOURCE).read_text()
    type_at_end(editor, ADDED)
    editor.get_by_role("button", name="Review changes").click()
    review = editor.get_by_role("region", name="Review")
    expect(review).to_contain_text("1.0.0 → 1.0.1")
    expect(review).to_contain_text("def helper() -> int:")
    confirm = review.get_by_role("group", name="This code runs in agents")
    expect(confirm).to_contain_text("The eval gate is a quality gate, not a security review.")
    save = review.get_by_role("button", name="Save as 1.0.1")
    expect(save).to_be_disabled()
    assert not (project / TOOLS / "1.0.1").exists()
    confirm.get_by_label("I reviewed this code and trust it to run in agents").check()
    expect(save).to_be_enabled()
    save.click()
    expect(page.get_by_role("status").filter(has_text="Saved tool/orders@1.0.1")).to_be_visible()
    assert (project / TOOLS / "1.0.1" / SOURCE).read_text() == original + ADDED
    assert (project / TOOLS / "1.0.0" / SOURCE).read_text() == original
    # The saved version's own detail reads its new function with the CLI's `ast` check.
    expect(page.get_by_role("region", name="Version 1.0.1")).to_contain_text("helper")


def test_the_clis_tool_check_refuses_a_renamed_function(page, console):
    project = open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    editor = page.get_by_role("region", name="Edit 1.0.0")
    area = editor.get_by_label(f"Text of {SOURCE}")
    area.fill(area.input_value().replace("def lookup_orders(", "def renamed("))
    editor.get_by_role("button", name="Review changes").click()
    expect(editor.get_by_role("alert")).to_contain_text("lookup_orders")
    expect(editor.get_by_role("region", name="Review")).to_have_count(0)
    assert not (project / TOOLS / "1.0.1").exists()


def test_a_shared_tool_is_forked_after_a_review_then_edited(page, console):
    share_orders(console)
    project = open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    card = page.get_by_role("region", name="Edit", exact=True)
    expect(card).to_contain_text("in a shared path, so it is not edited in place")
    expect(card).to_contain_text("agentbreeder registry fork tool/orders@1.0.0 --version 1.0.1")
    card.get_by_role("button", name="Fork to edit as 1.0.1").click()
    review = card.get_by_role("region", name="Review")
    expect(review).to_contain_text("fork 1.0.0 → 1.0.1")
    expect(review).to_contain_text(SOURCE)
    expect(review).to_contain_text("This version holds code that runs in agents.")
    assert not (project / TOOLS).exists()  # a review writes nothing
    review.get_by_role("button", name="Fork as 1.0.1").click()
    expect(
        page.get_by_role("status").filter(has_text="as tool/orders@1.0.1; edit the copy")
    ).to_be_visible()
    shared = console.state.parent / "shared" / "tool" / "orders"
    copied = (project / TOOLS / "1.0.1" / SOURCE).read_text()
    assert copied == (shared / "1.0.0" / SOURCE).read_text()
    assert not (shared / "1.0.1").exists()
    # The copy is the project's: it opens in the editor.
    editor = page.get_by_role("region", name="Edit 1.0.1")
    expect(editor.get_by_label(f"Text of {SOURCE}")).to_have_value(copied)


def test_the_tool_editor_has_no_blocking_axe_violations(scheme_page, console):  # noqa: F811
    open_northwind(scheme_page, console)
    scheme_page.goto(f"{console.origin}/#/registry/tools/orders")
    editor = scheme_page.get_by_role("region", name="Edit 1.0.0")
    type_at_end(editor, ADDED)
    editor.get_by_role("button", name="Review changes").click()
    editor.get_by_role("group", name="This code runs in agents").wait_for()
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "/registry/tools/orders") == []


def test_the_fork_card_has_no_blocking_axe_violations(scheme_page, console):  # noqa: F811
    share_orders(console)
    open_northwind(scheme_page, console)
    scheme_page.goto(f"{console.origin}/#/registry/tools/orders")
    card = scheme_page.get_by_role("region", name="Edit", exact=True)
    card.get_by_role("button", name="Fork to edit as 1.0.1").click()
    card.get_by_role("region", name="Review").wait_for()
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "/registry/tools/orders") == []
