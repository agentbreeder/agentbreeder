"""A tool version's detail (#828, M12 console half) over a copy of Northwind: the functions from
the real `registry show` (tool.yaml plus the `ast` signature), dependencies and grants. The fake
CLI runs the real `registry show`, so a module whose top level raises proves nothing is
imported."""

from __future__ import annotations

import json

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
    expect(lookup).to_contain_text('One order, e.g. "NW-10021"')
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
    scheme_page.get_by_role("table", name="Execution log of tool/orders@1.0.0").wait_for()
    scheme_page.get_by_role("button", name="Show arguments").click()
    scheme_page.get_by_text("lookup_orders (error)", exact=False).wait_for()
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "/registry/tools/orders") == []


# -- the execution log (#828 part 2), over the fake CLI's `tool log` -----------------------------


def log_calls(console) -> list[list[str]]:
    path = console.state / "tool-log-calls.jsonl"
    lines = path.read_text().splitlines() if path.is_file() else []
    return [json.loads(line) for line in lines if line.endswith("]")]


def wait_for_call(page, console, matches, after: int = 0) -> list[str]:
    """The first `tool log` argv after the first `after` calls that `matches`; a read starts in an
    effect after the render that changed it, so it can lag the page by a moment under load."""
    for _ in range(200):
        found = [argv for argv in log_calls(console)[after:] if matches(argv)]
        if found:
            return found[0]
        page.wait_for_timeout(100)
    raise AssertionError(f"no matching tool log call: {log_calls(console)[after:]}")


def test_the_log_lists_calls_without_arguments_until_asked(page, console):
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    table = page.get_by_role("table", name="Execution log of tool/orders@1.0.0")
    first = table.get_by_role("row").filter(has_text="2026-11-13T16:02:11.123Z")
    expect(first).to_contain_text("northwind-support")
    expect(first).to_contain_text("41 ms")
    expect(
        table.get_by_role("row").filter(has_text="agentbreeder_console (console)")
    ).to_contain_text("error")
    log = page.get_by_role("region", name="Execution log", exact=True)
    expect(log).to_contain_text("what the runtime claims it called")
    expect(log).to_contain_text("console credential")
    expect(log.get_by_role("link", name="http://127.0.0.1:6006")).to_have_attribute(
        "rel", "noopener noreferrer"
    )
    expect(log).not_to_contain_text("NW-10021")
    assert all("--content" not in argv for argv in log_calls(console))
    expect(log.get_by_text("Customer data", exact=True)).to_be_visible()
    log.get_by_role("button", name="Show arguments").click()
    expect(log.get_by_text("lookup_orders (error)", exact=False)).to_be_visible()
    log.locator("details.args").first.locator("summary").click()
    expect(log).to_contain_text('"order_id": "NW-10021"')
    assert "--content" in log_calls(console)[-1]
    log.get_by_role("button", name="Hide arguments").click()
    expect(log).not_to_contain_text("NW-10021")
    # Arguments never go into the page's own address.
    assert "content" not in page.url


def test_hide_drops_the_arguments_before_the_next_read_returns(page, console):
    console.scenario(tool_log={"seconds": 3})
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    log = page.get_by_role("region", name="Execution log", exact=True)
    log.get_by_role("button", name="Show arguments").click(timeout=15000)
    expect(log.get_by_text("lookup_orders (error)", exact=False)).to_be_visible()
    log.get_by_role("button", name="Hide arguments").click()
    # The plain read takes 3 s; the arguments are gone at once.
    expect(log.locator("details.args")).to_have_count(0, timeout=1000)
    expect(log.get_by_role("table", name="Execution log of tool/orders@1.0.0")).to_be_visible()


def test_scope_window_and_agent_reach_the_cli(page, console):
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    log = page.get_by_role("region", name="Execution log", exact=True)
    log.get_by_role("table", name="Execution log of tool/orders@1.0.0").wait_for()
    log.get_by_label("Versions").select_option("all")
    log.get_by_role("table", name="Execution log of tool/orders").wait_for()
    log.get_by_label("Window").select_option("168h")
    log.get_by_label("Agent").select_option("northwind-support")
    argv = wait_for_call(page, console, lambda argv: "--agent" in argv)
    expect(log.get_by_role("row").filter(has_text="agentbreeder_console")).to_have_count(0)
    expect(log.get_by_role("row").filter(has_text="northwind-support")).to_have_count(1)
    assert argv[2] == "tool/orders"
    assert argv[argv.index("--since") + 1] == "168h"
    assert argv[argv.index("--agent") + 1] == "northwind-support"
    # A new scope resets the agent filter before it reads: no read pairs the old agent with it.
    before = len(log_calls(console))
    log.get_by_label("Versions").select_option("version")
    expect(log.get_by_label("Agent")).to_have_value("")
    wait_for_call(page, console, lambda argv: argv[2] == "tool/orders@1.0.0", before)
    expect(log.get_by_role("row").filter(has_text="agentbreeder_console")).to_have_count(1)
    later = log_calls(console)[before:]
    assert later and all("--agent" not in argv for argv in later), later


def test_a_refusal_shows_unavailable_with_the_next_step(page, console):
    console.scenario(
        tool_log={
            "error": {
                "code": "CONSOLE_ROLE_MISSING",
                "message": "The log tables are owned by the database owner",
            }
        }
    )
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    log = page.get_by_role("region", name="Execution log", exact=True)
    expect(log).to_contain_text(
        "Unavailable: the execution log needs service/postgres-pgvector@1.2.0 with "
        "console_database: DB"
    )
    expect(log).to_contain_text("Next: set them in stack.yaml")
    expect(log.get_by_role("button", name="Show arguments")).to_have_count(0)


def test_no_calls_yet_is_not_a_refusal(page, console):
    console.scenario(
        tool_log={"error": {"code": "TOOL_LOG_NO_TABLES", "message": "No calls recorded yet"}}
    )
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    log = page.get_by_role("region", name="Execution log", exact=True)
    expect(log).to_contain_text("No calls recorded yet")
    expect(log).not_to_contain_text("Unavailable")
