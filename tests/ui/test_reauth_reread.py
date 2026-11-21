"""A read that got a 401 is read again once the re-auth dialog signs in (spec §8.2, #1188).
Before this, `useView` dropped a read that failed with a session error, so the view it fed stayed
on its loading state after sign-in: Build's Framework group on "Reading the registries…", or a
registry version card that never showed its files."""

from __future__ import annotations

import re
from urllib.parse import unquote

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_builder import patient_previews  # noqa: F401  (the fixture)
from .test_registry import open_northwind
from .test_signin import fill_sign_in

pytestmark = pytest.mark.ui

ENDED = '{"data": null, "error": {"code": "SIGN_IN", "message": "no"}, "stale": false}'


def ended(route):
    route.fulfill(status=401, content_type="application/json", body=ENDED)


def refused(page, part: str):
    """Waits for a read of `part` to get the routed 401. A view shows its loading state before its
    read is sent (`useView` reads in an effect after the first paint), so unrouting once the
    loading state is visible could let the read through to the server: no 401, no dialog
    (#1189)."""
    return page.expect_response(
        lambda response: part in unquote(response.url) and response.status == 401
    )


def sign_in_again(page, console) -> None:
    dialog = page.get_by_role("dialog", name="Session ended")
    expect(dialog).to_be_visible()
    fill_sign_in(dialog, console.password)
    expect(dialog).to_have_count(0)


def test_the_build_forms_framework_list_is_read_again_after_re_auth(page, console):
    sign_in(page, console, project=False)
    page.route("**/api/builder/parts", ended)
    with refused(page, "/api/builder/parts"):
        page.get_by_role("link", name="Build an agent").click()
    page.wait_for_url("**/#/build")
    expect(page.get_by_text("Reading the registries…")).to_be_visible()
    page.unroute("**/api/builder/parts")
    sign_in_again(page, console)
    radios = page.get_by_role("group", name="Framework").get_by_role("radio")
    expect(radios.first).to_be_visible(timeout=60_000)
    expect(page.get_by_text("Reading the registries…")).to_have_count(0)
    expect(page).to_have_url(re.compile(r"#/build$"))


def test_a_registry_version_card_is_read_again_after_re_auth(page, console):
    open_northwind(page, console)
    page.route("**/registry/show?**", ended)
    # Each version card reads its own; wait for the card the test follows.
    with refused(page, "/registry/show?ref=prompt/northwind-support@1.0.1"):
        page.goto(f"{console.origin}/#/registry/prompts/northwind-support")
    version = page.get_by_role("region", name="Version 1.0.1")
    expect(version).to_have_attribute("aria-busy", "true")
    page.unroute("**/registry/show?**")
    sign_in_again(page, console)
    expect(version.get_by_role("heading", name="Files")).to_be_visible()
    expect(version).to_contain_text("prompt/northwind-support@1.0.1")
    expect(page).to_have_url(re.compile(r"#/registry/prompts/northwind-support$"))


def test_a_view_that_did_not_fail_is_not_read_again_after_re_auth(page, console):
    """Only the failed reads are repeated: a view that already has its answer keeps it."""
    open_northwind(page, console)
    page.goto(f"{console.origin}/#/registry/prompts/northwind-support")
    version = page.get_by_role("region", name="Version 1.0.1")
    expect(version.get_by_role("heading", name="Files")).to_be_visible()
    shows: list[str] = []
    page.on(
        "request",
        lambda request: "/registry/show?" in request.url and shows.append(request.url),
    )
    # The notifications read runs on focus; its 401 opens the dialog over this route.
    page.route("**/api/events**", ended)
    page.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(page.get_by_role("dialog", name="Session ended")).to_be_visible()
    page.unroute("**/api/events**")
    sign_in_again(page, console)
    expect(version.get_by_role("heading", name="Files")).to_be_visible()
    page.wait_for_timeout(500)
    assert shows == []
