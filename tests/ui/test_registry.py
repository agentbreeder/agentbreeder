"""The registry browser (#824, M8 console half) over a copy of Northwind: the list per kind, a
name's version timeline, `registry show`, the diff between two versions and used-by. The fake CLI
runs the real `registry show` and `registry diff`; the list, verify status and the declared and
locked parts of used-by are the real reads too, and only the deployed part comes from the
scenario."""

from __future__ import annotations

import difflib
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import expect

import agentbreeder

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)

pytestmark = pytest.mark.ui

NORTHWIND = Path(agentbreeder.__file__).parent / "examples" / "northwind"
PROMPTS = "registry/prompt/northwind-support"
DEPLOYED = [
    {
        "deployment": "0123456789abcdef0123456789abcdef",
        "name": "northwind-support",
        "state": "running",
        "target": "local",
        "environment": "dev",
        "image": "northwind-support:1",
    }
]


def northwind(console) -> Path:
    target = console.state / "projects" / "northwind"
    if not target.exists():
        shutil.copytree(NORTHWIND, target)
    return target


def open_northwind(page, console) -> Path:
    project = northwind(console)
    sign_in(page, console, project=False)
    page.get_by_label("Absolute path").fill(str(project))
    page.get_by_role("button", name="Add project").click()
    page.wait_for_url("**/#/services")
    page.get_by_role("heading", name="Stack services").wait_for()
    return project


def row(page, table: str, name: str):
    return page.get_by_role("table", name=table).get_by_role("row").filter(has_text=name)


def test_every_kind_lists_northwind_and_the_built_ins(page, console):
    open_northwind(page, console)
    nav = page.get_by_role("navigation", name="Console")
    expected = {
        "Prompts": ["northwind-support"],
        "Eval sets": ["northwind-golden"],
        "Tools": ["cancel-order", "customer-notes", "escalate-to-human", "orders", "web-search"],
        "Knowledge": ["northwind-help"],
        "MCP servers": ["github", "playwright"],
        "Service definitions": [
            "echo",
            "litellm",
            "northwind-ops",
            "northwind-proxy",
            "postgres-pgvector",
        ],
        "Runtimes": ["adk"],
    }
    for label, names in expected.items():
        nav.get_by_role("link", name=label, exact=True).click()
        expect(page.locator("main h1")).to_contain_text(label)
        expect(nav.get_by_role("link", name=label, exact=True)).to_have_attribute(
            "aria-current", "page"
        )
        for name in names:
            expect(row(page, label, name).first).to_be_visible()
    # Prompts: latest, version count, root, verify and how this project uses it.
    nav.get_by_role("link", name="Prompts", exact=True).click()
    prompt = row(page, "Prompts", "northwind-support")
    expect(prompt).to_contain_text("1.0.1")
    expect(prompt.get_by_role("cell").nth(2)).to_have_text("2")
    expect(prompt).to_contain_text("project")
    expect(prompt).to_contain_text("Passed")
    expect(prompt).to_contain_text("1.0.1 (declared, locked)")
    # A built-in the project does not use.
    nav.get_by_role("link", name="MCP servers", exact=True).click()
    github = row(page, "MCP servers", "github")
    expect(github).to_contain_text("built-in")
    expect(github).to_contain_text("not used")
    # The roots in use, in resolve order.
    page.get_by_text("Search roots, in resolve order").click()
    expect(page.locator(".roots li").first).to_contain_text("project")
    expect(page.locator(".roots li").last).to_contain_text("built-in")


def test_detail_shows_the_timeline_a_version_and_the_diff(page, console):
    project = open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/prompts")
    page.get_by_role("link", name="northwind-support").click()
    page.wait_for_url("**/#/registry/prompts/northwind-support")
    expect(page.locator("main h1")).to_contain_text("Prompt")
    expect(page.locator("main h1")).to_be_focused()
    # The list link stays current on the detail.
    nav = page.get_by_role("navigation", name="Console")
    expect(nav.get_by_role("link", name="Prompts", exact=True)).to_have_attribute(
        "aria-current", "page"
    )
    timeline = page.get_by_role("table", name="Versions, newest first")
    expect(timeline.get_by_role("row").nth(1)).to_contain_text("1.0.1")
    expect(timeline.get_by_role("row").nth(1)).to_contain_text(
        "declared in agent.yaml; locked in agent.lock"
    )
    expect(timeline.get_by_role("row").nth(2)).to_contain_text("1.0.0")
    expect(timeline.get_by_role("row").nth(2)).to_contain_text("not used")
    # The latest version, from `registry show`.
    version = page.get_by_role("region", name="Version 1.0.1")
    expect(version).to_contain_text("prompt/northwind-support@1.0.1")
    expect(version).to_contain_text("sha256:")
    text = (project / PROMPTS / "1.0.1" / "prompt.md").read_text()
    first_line = text.splitlines()[0]
    expect(version.get_by_label("prompt.md")).to_contain_text(first_line)
    # Another version.
    page.get_by_role("button", name="Show 1.0.0").click()
    expect(page.get_by_role("region", name="Version 1.0.0")).to_contain_text(
        "prompt/northwind-support@1.0.0"
    )
    # The diff 1.0.0 → 1.0.1 equals diff(1) of the files.
    compare = page.get_by_role("region", name="Compare versions")
    expect(compare.get_by_role("status")).to_contain_text("1.0.0 → 1.0.1")
    before = (project / PROMPTS / "1.0.0" / "prompt.md").read_text().splitlines()
    after = text.splitlines()
    expected = [
        line
        for line in difflib.unified_diff(before, after, lineterm="", n=3)
        if not line.startswith(("---", "+++"))
    ]
    shown = compare.get_by_label("Changes in modified prompt.md").inner_text().splitlines()
    assert [line.rstrip() for line in shown] == [line.rstrip() for line in expected]
    # Swapping the sides is a new diff; the same version twice is refused with its reason.
    compare.get_by_label("From").select_option("1.0.1")
    expect(compare.get_by_role("button", name="Compare")).to_be_disabled()
    expect(compare.get_by_text("Pick two different versions.")).to_be_visible()
    compare.get_by_label("To").select_option("1.0.0")
    compare.get_by_role("button", name="Compare").click()
    expect(compare.get_by_role("status")).to_contain_text("1.0.1 → 1.0.0")
    # The hunks are the new pair's, never the last pair's under the new labels.
    manifest = compare.get_by_label("Changes in modified manifest.yaml")
    expect(manifest).to_contain_text("-version: 1.0.1")
    expect(manifest).to_contain_text("+version: 1.0.0")


def test_another_name_opens_on_its_own_latest_version(page, console):
    project = northwind(console)
    orders = project / "registry" / "tool" / "orders"
    shutil.copytree(orders / "1.0.0", orders / "1.0.1")
    manifest = orders / "1.0.1" / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("version: 1.0.0", "version: 1.0.1"))
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/cancel-order")
    expect(page.locator("main h1")).to_contain_text("Tool cancel-order")
    assert page.title().startswith("Tool cancel-order")
    page.get_by_role("button", name="Show 1.0.0").click()
    expect(page.get_by_role("region", name="Version 1.0.0")).to_be_visible()
    page.goto(f"{console.origin}/#/registry/tools/orders")
    expect(page.locator("main h1")).to_contain_text("Tool orders")
    expect(page.get_by_role("region", name="Version 1.0.1")).to_contain_text("tool/orders@1.0.1")


def test_used_by_shows_declared_locked_and_deployed(page, console):
    console.scenario(used_by_deployed=DEPLOYED)
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    used = page.get_by_role("table", name="Used by tool/orders@1.0.0")
    northwind_row = used.get_by_role("row").filter(has_text="northwind-support")
    expect(northwind_row).to_contain_text("agent.yaml at tools[2].ref")
    expect(northwind_row).to_contain_text("agent.lock")
    expect(northwind_row).to_contain_text(
        "Running: northwind-support 0123456789ab running (local, dev)"
    )


def test_a_failed_verification_shows_on_the_list_and_the_version(page, console):
    project = northwind(console)
    path = str(project / "registry" / "tool" / "orders" / "1.0.0")
    console.scenario(
        registry_problems=[
            {"ref": "tool/orders@1.0.0", "path": path, "error": "[HASH_MISMATCH] changed"}
        ]
    )
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools")
    expect(row(page, "Tools", "orders")).to_contain_text("✕ Failed")
    expect(row(page, "Tools", "customer-notes")).to_contain_text("✓ Passed")
    page.get_by_role("link", name="orders", exact=True).click()
    expect(page.get_by_role("table", name="Versions, newest first")).to_contain_text(
        "[HASH_MISMATCH] changed"
    )


def test_an_unknown_name_says_so(page, console):
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/prompts/nope")
    expect(page.get_by_text("No prompt named")).to_be_visible()
    page.get_by_role("link", name="All prompts").click()
    page.wait_for_url("**/#/registry/prompts")


def test_registry_detail_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    open_northwind(scheme_page, console)
    found = []
    for route in ("/registry/prompts", "/registry/prompts/northwind-support"):
        scheme_page.goto(f"{console.origin}/#{route}")
        scheme_page.locator("main h1").wait_for()
        scheme_page.wait_for_timeout(600)
        found += blocking(scheme_page, route)
    assert found == []
