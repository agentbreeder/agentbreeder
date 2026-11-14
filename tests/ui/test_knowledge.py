"""A knowledge version's index status (#830, M14 console half) over a copy of Northwind: the
fake CLI answers `knowledge status --json`; the version card shows the state, the counts, the
embedding and the CLI's next step when it refuses."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_registry import open_northwind

pytestmark = pytest.mark.ui

REF = "knowledge/northwind-help@1.0.0"
PAGE = "/#/registry/knowledge/northwind-help"


def status_calls(console) -> list[list[str]]:
    path = console.state / "knowledge-calls.jsonl"
    lines = path.read_text().splitlines() if path.is_file() else []
    return [json.loads(line) for line in lines if line.endswith("]")]


def wait_for_calls(page, console, count: int) -> list[list[str]]:
    """At least `count` status reads; a read starts in an effect after the render."""
    for _ in range(200):
        calls = status_calls(console)
        if len(calls) >= count:
            return calls
        page.wait_for_timeout(100)
    raise AssertionError(f"fewer than {count} knowledge status calls: {status_calls(console)}")


def index(page):
    return page.get_by_role("region", name="Index status", exact=True)


def test_a_fresh_version_shows_its_counts(page, console):
    open_northwind(page, console)
    page.goto(f"{console.origin}{PAGE}")
    status = index(page)
    expect(status).to_contain_text("Fresh")
    expect(status).to_contain_text("support_agent.kb_northwind_help_1_0_0")
    expect(status).to_contain_text("21 indexed of 21 (fresh 21, stale 0, missing 0, extra 0)")
    expect(status).to_contain_text("5 indexed of 5")
    expect(status).to_contain_text("support-embedding, 768 dimensions")
    expect(status).to_contain_text("agent credential")
    argv = wait_for_calls(page, console, 1)[0]
    assert argv[:3] == ["knowledge", "status", REF]
    assert argv[argv.index("--environment") + 1] == "dev" and argv[-1] == "--json"
    status.get_by_role("button", name="Refresh").click()
    wait_for_calls(page, console, 2)


def test_a_stale_version_says_release_reindexes(page, console):
    console.scenario(
        knowledge_status={
            "state": "stale",
            "chunks": {"expected": 22, "indexed": 21, "fresh": 21, "stale": 0, "missing": 1,
                       "extra": 0},
            "notes": ["out of date: the runtime re-indexes at its next process start"],
        }
    )  # fmt: skip
    open_northwind(page, console)
    page.goto(f"{console.origin}{PAGE}")
    status = index(page)
    expect(status).to_contain_text("Stale")
    expect(status).to_contain_text("missing 1")
    expect(status).to_contain_text("which release's eval does")
    expect(status).to_contain_text("Note: out of date")


def test_a_refusal_shows_unavailable_with_the_next_step(page, console):
    console.scenario(
        knowledge_status={"error": {"code": "SERVICES_DOWN", "message": "no stack state"}}
    )
    open_northwind(page, console)
    page.goto(f"{console.origin}{PAGE}")
    status = index(page)
    expect(status).to_contain_text("Unavailable: the stack's services are not running")
    expect(status).to_contain_text("Next: start the services")
    expect(status).not_to_contain_text("Fresh")


def test_other_kinds_have_no_index_status(page, console):
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/tools/orders")
    page.get_by_role("table", name="Functions of tool/orders@1.0.0").wait_for()
    expect(index(page)).to_have_count(0)


def test_the_knowledge_detail_has_no_blocking_axe_violations(scheme_page, console):  # noqa: F811
    console.scenario(knowledge_status={"state": "stale"})
    open_northwind(scheme_page, console)
    scheme_page.goto(f"{console.origin}{PAGE}")
    expect(index(scheme_page)).to_contain_text("Stale")
    scheme_page.wait_for_timeout(600)
    assert blocking(scheme_page, "/registry/knowledge/northwind-help") == []
