"""Fork in the registry browser (#825, M9a console half, part 2) over a copy of Northwind: a
built-in service version forked into an editable project copy with the code confirm, and a fork
under another name. The fake CLI runs the real `registry fork`."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_registry import open_northwind

pytestmark = pytest.mark.ui

SERVICE = "registry/service/postgres-pgvector"


def test_a_built_in_service_is_forked_into_the_project_after_the_code_confirm(page, console):
    project = open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/services/postgres-pgvector")
    card = page.get_by_role("region", name="Fork", exact=True)
    expect(card).to_contain_text("This version is built in.")
    expect(card).to_contain_text("agentbreeder registry fork service/postgres-pgvector@1.2.0")
    card.get_by_role("button", name="Fork into this project as 1.2.1").click()
    review = card.get_by_role("region", name="Review")
    expect(review).to_contain_text("fork 1.2.0 → 1.2.1")
    expect(review).to_contain_text("manifest.yaml")
    confirm = review.get_by_role("group", name="This code runs in agents")
    fork = review.get_by_role("button", name="Fork as 1.2.1")
    expect(fork).to_be_disabled()
    assert not (project / SERVICE / "1.2.1").exists()  # a review writes nothing
    confirm.get_by_label("I reviewed this code and trust it to run in agents").check()
    fork.click()
    expect(
        page.get_by_role("status").filter(
            has_text="as service/postgres-pgvector@1.2.1; change it with agentbreeder registry"
        )
    ).to_be_visible()
    assert (project / SERVICE / "1.2.1" / "manifest.yaml").is_file()
    # The copy is the project's: shown now, with no Fork card (it is edited on disk).
    expect(page.get_by_role("region", name="Version 1.2.1")).to_contain_text("project")
    expect(page.get_by_role("region", name="Fork", exact=True)).to_have_count(0)


def test_a_fork_under_another_name_opens_the_new_name(page, console):
    project = open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/runtimes/adk")
    card = page.get_by_role("region", name="Fork", exact=True)
    card.get_by_label("Fork under another name").fill("Bad Name")
    expect(card).to_contain_text("A name is lowercase letters")
    card.get_by_label("Fork under another name").fill("adk-copy")
    expect(card).to_contain_text("--as adk-copy")
    card.get_by_role("button", name="Fork into this project as runtime/adk-copy@").click()
    review = card.get_by_role("region", name="Review")
    expect(review).to_contain_text("only the manifest's name line changes")
    review.get_by_label("I reviewed this code and trust it to run in agents").check()
    review.get_by_role("button", name="Fork as runtime/adk-copy@").click()
    page.wait_for_url("**/#/registry/runtimes/adk-copy")
    expect(page.get_by_role("table", name="Versions, newest first")).to_contain_text("project")
    assert any((project / "registry" / "runtime" / "adk-copy").iterdir())


def test_the_fork_card_has_no_blocking_axe_violations(scheme_page, console):  # noqa: F811
    open_northwind(scheme_page, console)
    scheme_page.goto(f"{console.origin}/#/registry/services/postgres-pgvector")
    card = scheme_page.get_by_role("region", name="Fork", exact=True)
    card.get_by_role("button", name="Fork into this project as 1.2.1").click()
    expect(card.get_by_role("group", name="This code runs in agents")).to_be_visible()
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "/registry/services/postgres-pgvector") == []
