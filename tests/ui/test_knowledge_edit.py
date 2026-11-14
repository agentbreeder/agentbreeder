"""Knowledge documents (#830, M14 console half, part 2) over a copy of Northwind: a document is
edited, added and removed through the real `registry edit` (the fake CLI runs it), each reviewed
first, with the git-tracked warning before Save."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_registry import open_northwind

pytestmark = pytest.mark.ui

KNOWLEDGE = "registry/knowledge/northwind-help"
PAGE = "/#/registry/knowledge/northwind-help"
WARNING = "committed to git: do not add customer data or secrets"


def editor(page):
    return page.get_by_role("region", name="Edit 1.0.0")


def test_a_document_edit_is_reviewed_with_the_warning_then_saved(page, console):
    project = open_northwind(page, console)
    page.goto(f"{console.origin}{PAGE}")
    edit = editor(page)
    expect(edit).to_contain_text("indexed only after you move the agent to it and release")
    edit.get_by_label("File", exact=True).select_option("docs/refunds.md")
    area = edit.get_by_label("Text of docs/refunds.md")
    area.press("ControlOrMeta+End")
    area.type("\nRefunds take five days.\n")
    edit.get_by_role("button", name="Review changes").click()
    review = edit.get_by_role("region", name="Review")
    expect(review).to_contain_text("1.0.0 → 1.0.1")
    expect(review).to_contain_text("Refunds take five days.")
    expect(review.get_by_role("note")).to_contain_text(WARNING)
    assert not (project / KNOWLEDGE / "1.0.1").exists()
    review.get_by_role("button", name="Save as 1.0.1").click()
    expect(
        page.get_by_role("status").filter(has_text="Saved knowledge/northwind-help@1.0.1")
    ).to_be_visible()
    text = (project / KNOWLEDGE / "1.0.1" / "docs" / "refunds.md").read_text()
    assert text.endswith("Refunds take five days.\n")
    assert "five days" not in (project / KNOWLEDGE / "1.0.0" / "docs" / "refunds.md").read_text()


def test_a_new_document_is_added(page, console):
    project = open_northwind(page, console)
    page.goto(f"{console.origin}{PAGE}")
    edit = editor(page)
    name = edit.get_by_label("New document")
    name.fill("bad name.md")
    edit.get_by_role("button", name="Add", exact=True).click()
    expect(edit.get_by_role("alert")).to_contain_text("Use a plain name")
    name.fill("billing.md")
    edit.get_by_role("button", name="Add", exact=True).click()
    expect(edit.get_by_role("alert")).to_contain_text("exists already")
    name.fill("Billing.txt")
    edit.get_by_role("button", name="Add", exact=True).click()
    expect(edit.get_by_role("alert")).to_contain_text("has the same name")
    name.fill("warranty.md")
    edit.get_by_role("button", name="Add", exact=True).click()
    expect(edit.get_by_label("File", exact=True)).to_have_value("docs/warranty.md")
    expect(edit.get_by_role("button", name="Remove this document")).to_have_count(0)
    edit.get_by_label("Text of docs/warranty.md").fill("# Warranty\n\nTwo years on hardware.\n")
    edit.get_by_role("button", name="Review changes").click()
    review = edit.get_by_role("region", name="Review")
    expect(review).to_contain_text("Two years on hardware.")
    expect(review.get_by_role("note")).to_contain_text(WARNING)
    review.get_by_role("button", name="Save as 1.0.1").click()
    expect(
        page.get_by_role("status").filter(has_text="Saved knowledge/northwind-help@1.0.1")
    ).to_be_visible()
    added = project / KNOWLEDGE / "1.0.1" / "docs" / "warranty.md"
    assert added.read_text() == "# Warranty\n\nTwo years on hardware.\n"
    assert not (project / KNOWLEDGE / "1.0.0" / "docs" / "warranty.md").exists()


def test_a_document_is_removed_after_a_review(page, console):
    project = open_northwind(page, console)
    page.goto(f"{console.origin}{PAGE}")
    edit = editor(page)
    edit.get_by_label("File", exact=True).select_option("docs/billing.md")
    # A removal needs no unsaved text: it is refused while there are changes.
    area = edit.get_by_label("Text of docs/billing.md")
    area.press("ControlOrMeta+End")
    area.type("x")
    expect(edit.get_by_role("button", name="Remove this document")).to_be_disabled()
    area.press("Backspace")
    edit.get_by_role("button", name="Remove this document").click()
    review = edit.get_by_role("region", name="Review")
    expect(review).to_contain_text("1.0.0 → 1.0.1, without docs/billing.md")
    expect(review).to_contain_text("removed")
    expect(review.get_by_role("note")).to_contain_text(WARNING)
    assert not (project / KNOWLEDGE / "1.0.1").exists()
    # Text typed after the review makes it stale: no Save until it is reviewed again.
    area.type("y")
    expect(edit.get_by_role("button", name="Remove and save as 1.0.1")).to_have_count(0)
    expect(edit).to_contain_text("review again")
    area.press("Backspace")
    edit.get_by_role("button", name="Remove this document").click()
    review = edit.get_by_role("region", name="Review")
    review.get_by_role("button", name="Remove and save as 1.0.1").click()
    expect(page.get_by_role("status").filter(has_text="without docs/billing.md")).to_be_visible()
    assert not (project / KNOWLEDGE / "1.0.1" / "docs" / "billing.md").exists()
    assert (project / KNOWLEDGE / "1.0.1" / "docs" / "refunds.md").exists()
    assert (project / KNOWLEDGE / "1.0.0" / "docs" / "billing.md").exists()


def test_the_document_editor_has_no_blocking_axe_violations(scheme_page, console):  # noqa: F811
    open_northwind(scheme_page, console)
    scheme_page.goto(f"{console.origin}{PAGE}")
    edit = editor(scheme_page)
    edit.get_by_label("New document").fill("bad name.md")
    edit.get_by_role("button", name="Add", exact=True).click()
    expect(edit.get_by_role("alert")).to_be_visible()
    edit.get_by_role("button", name="Remove this document").click()
    expect(edit.get_by_role("region", name="Review").get_by_role("note")).to_contain_text(WARNING)
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "/registry/knowledge/northwind-help") == []
