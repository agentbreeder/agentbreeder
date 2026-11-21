"""Build a new agent with knowledge (#1069): the Knowledge picker over the roots a new project
resolves (here an AGENTBREEDER_REGISTRY_PATH root with `knowledge/help@1.0.0` and a version with
no embedding), offered only for a provider with an embedding model, the pick in the real
`new agent --from-spec` preview (its embedding row, the pinned ref), Create on for gemini +
knowledge (verified), and Create, writing the project with its knowledge."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from playwright.sync_api import expect

from agentbreeder import registry

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_builder import create_previewed, fill, home, open_builder

pytestmark = pytest.mark.ui

HELP = "knowledge/help@1.0.0"


def knowledge(root: Path, name: str, embedding: str) -> None:
    source = root.parent / f"kb-{name}"
    source.mkdir(parents=True)
    (source / "manifest.yaml").write_text(
        f"kind: knowledge\nname: {name}\nversion: 1.0.0\ndescription: Help-center articles.\n"
        f"chunking: paragraph\n{embedding}store: pgvector\n"
    )
    (source / "docs").mkdir()
    (source / "docs" / "refunds.md").write_text(
        "# Refunds\n\nRefunds take five days.\n\nAsk support for help.\n"
    )
    registry.publish(source, root)


@pytest.fixture
def console_env(tmp_path) -> dict:
    root = tmp_path / "path-registry"
    knowledge(root, "help", "embedding: {alias: help-embedding, dimensions: 768}\n")
    knowledge(root, "bare", "")
    return {registry.PATH_ENV: str(root)}


def picker(page):
    return page.get_by_label("Knowledge version")


def test_knowledge_is_offered_for_providers_with_embeddings(page, console):
    open_builder(page, console)
    fill(page)
    expect(picker(page)).to_be_enabled()
    expect(picker(page).locator("option")).to_have_text(
        ["Not used", "help 1.0.0 (AGENTBREEDER_REGISTRY_PATH)"]
    )
    # A version with no embedding is never a choice; it is listed as text with why.
    expect(page.get_by_text("Cannot be used: knowledge/bare@1.0.0")).to_contain_text(
        "no valid embedding"
    )
    picker(page).select_option(HELP)
    expect(page.get_by_text("Embeds with the gateway alias help-embedding at 768")).to_be_visible()
    # A provider with no embedding model: the reason is next to the select, and Preview says it.
    page.get_by_label("Model provider").select_option("anthropic")
    expect(
        page.get_by_text("Knowledge needs an embedding model, and anthropic has none")
    ).to_have_count(2)
    expect(page.get_by_role("button", name="Preview", exact=True)).to_be_disabled()
    picker(page).select_option("")
    expect(picker(page)).to_be_disabled()


def test_a_knowledge_pick_previews_and_creates(page, console):
    open_builder(page, console)
    fill(page)
    picker(page).select_option(HELP)
    page.get_by_role("button", name="Preview", exact=True).click()
    pane = page.get_by_role("complementary", name="Preview: nothing is written yet")
    expect(pane).to_be_visible(timeout=60000)
    table = pane.get_by_role("table", name="Knowledge the agent searches")
    expect(table).to_contain_text(HELP)
    expect(table).to_contain_text("help-embedding, 768 dimensions")
    expect(table).to_contain_text("gemini/gemini-embedding-2")
    expect(pane.get_by_role("table", name="Parts the agent uses")).to_contain_text(HELP)
    pane.get_by_role("button", name="agent.yaml").click()
    expect(pane.locator("pre.builder-file")).to_contain_text(HELP)
    expect(pane.get_by_text("Run end to end: ")).to_contain_text("knowledge")
    create_previewed(page, date.today())
    project = home(console) / "agentbreeder-projects" / "helper"
    assert HELP in (project / "agent.yaml").read_text()
    assert "help-embedding" in (project / "stack.yaml").read_text()


@pytest.mark.parametrize("width", [320, 375])
def test_the_knowledge_picker_fits_narrow_screens(page, console, width):
    page.set_viewport_size({"width": width, "height": 800})
    open_builder(page, console)
    picker(page).select_option(HELP)
    page.wait_for_timeout(300)
    assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_the_knowledge_picker_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    open_builder(scheme_page, console)
    picker(scheme_page).select_option(HELP)
    scheme_page.wait_for_timeout(400)
    assert blocking(scheme_page, "build, knowledge") == []
