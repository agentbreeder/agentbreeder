"""Registry ▸ Import (#825, M9a console half) over a copy of Northwind. The fake CLI runs the real
`registry import`, so the CLI's own checks (manifest, links, digest) run on real directories."""

from __future__ import annotations

import shutil

import pytest
from playwright.sync_api import expect

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_registry import open_northwind

pytestmark = pytest.mark.ui

PROMPT = "registry/prompt/northwind-support"
TOOL = "registry/tool/orders"


def elsewhere(console, project, ref: str):
    """A copy of one of the project's versions outside the project."""
    target = console.state.parent / "elsewhere" / ref.rsplit("/", 1)[-1]
    if not target.exists():
        shutil.copytree(project / ref / "1.0.0", target)
    return target


def open_import(page, console):
    project = open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/import")
    return project, page.get_by_role("region", name="Import a version")


def test_a_prompt_from_another_directory_is_reviewed_then_imported(page, console):
    project, card = open_import(page, console)
    source = elsewhere(console, project, PROMPT)
    review_button = card.get_by_role("button", name="Review import")
    expect(review_button).to_be_disabled()
    expect(card).to_contain_text("Type the version directory's absolute path first.")
    card.get_by_label("Version directory (absolute path)").fill(str(source))
    card.get_by_label("Import as version").fill("1.1.0")
    expect(card).to_contain_text("--version 1.1.0 --dry-run")
    review_button.click()
    review = card.get_by_role("region", name="Review")
    expect(review).to_contain_text("as prompt/northwind-support@1.1.0")
    expect(review).to_contain_text("manifest.yaml")
    expect(review).to_contain_text("steers the agent")
    expect(review.get_by_role("group", name="This code runs in agents")).to_have_count(0)
    assert not (project / PROMPT / "1.1.0").exists()  # a review writes nothing
    # A changed field needs a new review.
    card.get_by_label("Import as version").fill("1.2.0")
    expect(card.get_by_role("region", name="Review")).to_have_count(0)
    expect(card).to_contain_text("The form changed since the review")
    card.get_by_label("Import as version").fill("1.1.0")
    review.get_by_role("button", name="Import as prompt/northwind-support@1.1.0").click()
    done = card.get_by_role("status").filter(has_text="Imported prompt/northwind-support@1.1.0")
    expect(done).to_be_visible()
    assert (project / PROMPT / "1.1.0" / "manifest.yaml").is_file()
    done.get_by_role("link", name="Open prompt/northwind-support").click()
    page.wait_for_url("**/#/registry/prompts/northwind-support")
    expect(page.get_by_role("table", name="Versions, newest first")).to_contain_text("1.1.0")


def test_a_tool_import_asks_for_the_code_confirm(page, console):
    project, card = open_import(page, console)
    source = elsewhere(console, project, TOOL)
    card.get_by_label("Version directory (absolute path)").fill(str(source))
    card.get_by_label("Import as version").fill("1.1.0")
    card.get_by_role("button", name="Review import").click()
    review = card.get_by_role("region", name="Review")
    confirm = review.get_by_role("group", name="This code runs in agents")
    expect(confirm).to_contain_text("The eval gate is a quality gate, not a security review.")
    write = review.get_by_role("button", name="Import as tool/orders@1.1.0")
    expect(write).to_be_disabled()
    confirm.get_by_label("I reviewed this code and trust it to run in agents").check()
    expect(write).to_be_enabled()
    write.click()
    done = card.get_by_role("status").filter(has_text="Imported tool/orders@1.1.0")
    expect(done).to_be_visible()
    expect(done).to_be_focused()  # the Import button is gone: focus moves to the result
    card.get_by_label("Import as version").fill("1.2.0")
    expect(done).to_have_count(0)  # the last result is not about the edited form
    assert (project / TOOL / "1.1.0" / "src" / "orders.py").is_file()


def test_a_directory_that_is_not_a_version_is_refused(page, console):
    _, card = open_import(page, console)
    private = console.state.parent / "private"
    private.mkdir()
    (private / "notes.txt").write_text("private-file-marker-7f3a")
    card.get_by_label("Version directory (absolute path)").fill(str(private))
    card.get_by_role("button", name="Review import").click()
    alert = card.get_by_role("alert")
    expect(alert).to_contain_text("manifest.yaml is missing")
    expect(card.get_by_role("region", name="Review")).to_have_count(0)
    expect(page.locator("body")).not_to_contain_text("private-file-marker-7f3a")


def test_a_source_changed_after_the_review_is_refused(page, console):
    project, card = open_import(page, console)
    source = elsewhere(console, project, PROMPT)
    card.get_by_label("Version directory (absolute path)").fill(str(source))
    card.get_by_label("Import as version").fill("1.1.0")
    card.get_by_role("button", name="Review import").click()
    review = card.get_by_role("region", name="Review")
    expect(review).to_be_visible()
    (source / "prompt.md").write_text("changed after the review\n")
    review.get_by_role("button", name="Import as prompt/northwind-support@1.1.0").click()
    expect(card.get_by_role("alert")).to_contain_text("changed after your review")
    expect(card.get_by_role("region", name="Review")).to_have_count(0)
    assert not (project / PROMPT / "1.1.0").exists()


def test_every_registry_list_links_to_import(page, console):
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/prompts")
    page.get_by_role("link", name="Import a version from a directory").click()
    page.wait_for_url("**/#/registry/import")
    expect(page.get_by_role("heading", name="Import a version", level=2)).to_be_visible()


def test_the_import_review_has_no_blocking_axe_violations(scheme_page, console):  # noqa: F811
    project, card = open_import(scheme_page, console)
    card.get_by_label("Version directory (absolute path)").fill(
        str(elsewhere(console, project, TOOL))
    )
    card.get_by_label("Import as version").fill("1.1.0")
    card.get_by_role("button", name="Review import").click()
    expect(card.get_by_role("group", name="This code runs in agents")).to_be_visible()
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "/registry/import") == []
