"""A tool version's detail (#828, M12 console half) over a copy of Northwind: the functions from
the real `registry show` (tool.yaml plus the `ast` signature), dependencies and grants. The fake
CLI runs the real `registry show`, so a module whose top level raises proves nothing is
imported."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_registry import northwind, open_northwind

pytestmark = pytest.mark.ui

SIGNATURE = "lookup_orders(order_id: str='', status: str='') -> str"


def test_a_tool_version_shows_its_functions_and_signatures(page, console):
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    table = page.get_by_role("table", name="Functions of tool/orders@1.0.0")
    lookup = table.get_by_role("row").filter(has_text="orders.lookup_orders")
    expect(lookup).to_contain_text(SIGNATURE)
    expect(lookup).to_contain_text("Look up the current customer's Northwind orders")
    expect(lookup).to_contain_text("order_id")
    expect(lookup).to_contain_text("One order, e.g. \"NW-10021\"")
    expect(table.get_by_role("row").filter(has_text="get_product_catalog")).to_be_visible()
    version = page.get_by_role("region", name="Version 1.0.0")
    expect(version).to_contain_text("None of its own")
    expect(version.get_by_role("link", name="runtime/adk")).to_have_attribute(
        "href", "#/registry/runtimes/adk"
    )
    expect(version).to_contain_text("Available to the agents that reference it")


def test_code_that_raises_on_import_is_only_read(page, console):
    project = northwind(console)
    source = project / "registry" / "tool" / "orders" / "1.0.0" / "src" / "orders.py"
    source.write_text(source.read_text() + '\n\nraise SystemExit("imported")\n')
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    table = page.get_by_role("table", name="Functions of tool/orders@1.0.0")
    expect(table).to_contain_text(SIGNATURE)


def test_a_missing_function_says_why_there_is_no_signature(page, console):
    project = northwind(console)
    source = project / "registry" / "tool" / "orders" / "1.0.0" / "src" / "orders.py"
    source.write_text(source.read_text().replace("def get_product_catalog(", "def renamed("))
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    row = (
        page.get_by_role("table", name="Functions of tool/orders@1.0.0")
        .get_by_role("row")
        .filter(has_text="get_product_catalog")
    )
    expect(row).to_contain_text("Signature not shown: the module or the function was not found.")


def test_other_kinds_show_no_functions(page, console):
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/prompts/northwind-support")
    expect(page.get_by_role("heading", name="Files")).to_be_visible()
    expect(page.get_by_role("heading", name="Functions")).to_have_count(0)


def test_the_tool_detail_has_no_blocking_axe_violations(scheme_page, console):  # noqa: F811
    open_northwind(scheme_page, console)
    scheme_page.goto(f"{console.origin}/#/registry/tools/orders")
    scheme_page.get_by_role("table", name="Functions of tool/orders@1.0.0").wait_for()
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "/registry/tools/orders") == []
