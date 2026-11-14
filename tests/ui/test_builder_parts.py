"""Build a new agent, part 2 (#833, M17 console half): the tool and MCP pickers over the roots a
new project resolves (here an AGENTBREEDER_REGISTRY_PATH root with `tool/clock` and the built-in
`mcp/` servers), the picks in the real `new agent --from-spec` preview, the "this code runs in
agents" confirm for a tool from outside the built-ins, and Create off for a combination not run
end to end."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from agentbreeder import registry

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_builder import fill, home, open_builder

pytestmark = pytest.mark.ui


@pytest.fixture
def console_env(tmp_path) -> dict:
    root = tmp_path / "path-registry"
    for version in ("0.1.0", "0.2.0"):
        registry.publish(registry.scaffold("tool", "clock", tmp_path / version, version), root)
    return {registry.PATH_ENV: str(root)}


def preview(page):
    page.get_by_role("button", name="Preview", exact=True).click()
    pane = page.get_by_role("complementary", name="Preview: nothing is written yet")
    expect(pane).to_be_visible()
    return pane


def test_a_path_tool_needs_the_code_confirm_and_is_not_created(page, console):
    open_builder(page, console)
    fill(page)
    tool = page.get_by_label("Tool clock")
    expect(tool.locator("option")).to_have_text(
        ["Not used", "0.2.0 (AGENTBREEDER_REGISTRY_PATH)", "0.1.0 (AGENTBREEDER_REGISTRY_PATH)"]
    )
    tool.select_option("tool/clock@0.2.0")
    pane = preview(page)
    expect(pane).to_contain_text("Not run end to end yet: a new agent with tools.")
    parts = pane.get_by_role("table", name="Parts the agent uses")
    expect(parts).to_contain_text("tool/clock@0.2.0")
    expect(parts).to_contain_text("Runs in agents")
    expect(pane.get_by_label("agent.yaml, as written")).to_contain_text("tool/clock@0.2.0")
    confirm = pane.get_by_role("group", name="This code runs in agents")
    expect(confirm).to_contain_text("tool/clock@0.2.0 is not shipped with AgentBreeder")
    checkbox = confirm.get_by_label("I trust this code to run in agents")
    create = page.get_by_role("button", name="Create agent and add project")
    expect(create).to_be_disabled()
    checkbox.check()
    # Confirmed, but the combination has not been run end to end: still off.
    expect(create).to_be_disabled()
    expect(page.get_by_text("This combination has not been run end to end.")).to_be_visible()
    # Another pick marks the preview out of date and clears the confirm.
    tool.select_option("tool/clock@0.1.0")
    expect(page.get_by_role("complementary", name="Preview (out of date)")).to_be_visible()
    preview(page)
    expect(checkbox).not_to_be_checked()
    assert not (home(console) / "agentbreeder-projects" / "helper").exists()


def test_an_mcp_server_grants_the_checked_tools(page, console):
    open_builder(page, console)
    fill(page)
    server = page.get_by_label("MCP server github")
    expect(server).to_have_accessible_description(re.compile(r"^Newest, 1\.0\.0: GitHub's hosted"))
    server.select_option("mcp/github@1.0.0")
    expect(server).to_have_accessible_description(re.compile(r"^GitHub's hosted MCP server"))
    grants = page.get_by_role("group", name="Tools granted to github")
    expect(grants.get_by_role("checkbox")).to_have_count(5)
    previewing = page.get_by_role("button", name="Preview", exact=True)
    expect(previewing).to_be_disabled()
    expect(page.get_by_text("Grant github at least one of its tools")).to_be_visible()
    grants.get_by_label("get_me").check()
    grants.get_by_label("issue_read").check()
    pane = preview(page)
    expect(pane).to_contain_text("GITHUB_MCP_TOKEN")
    expect(pane).to_contain_text("Not run end to end yet: a new agent with MCP servers.")
    # A built-in server is shipped code: no "this code runs in agents" confirm.
    expect(pane.get_by_role("group", name="This code runs in agents")).to_have_count(0)
    pane.get_by_role("group", name="Files").get_by_role(
        "button", name="stack.yaml", exact=True
    ).click()
    stack = pane.get_by_label("stack.yaml, as written")
    expect(stack).to_contain_text("mcp/github@1.0.0")
    expect(stack).to_contain_text("issue_read")
    expect(stack).not_to_contain_text("list_issues")
    expect(page.get_by_role("button", name="Create agent and add project")).to_be_disabled()
    server.select_option("")
    expect(grants).to_have_count(0)


def test_the_pickers_have_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    open_builder(scheme_page, console)
    fill(scheme_page)
    scheme_page.get_by_label("Tool clock").select_option("tool/clock@0.2.0")
    scheme_page.get_by_label("MCP server github").select_option("mcp/github@1.0.0")
    scheme_page.get_by_role("group", name="Tools granted to github").get_by_label("get_me").check()
    preview(scheme_page)
    assert blocking(scheme_page, "Agent builder pickers") == []
