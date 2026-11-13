"""axe over the console, light and dark (#816). axe injects its own script, so it runs in a
context that bypasses the page's CSP; the CSP itself is checked in test_csp.py."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.ui

# Impacts that fail the run. "minor" and "moderate" findings are printed, not failed.
BLOCKING = {"serious", "critical"}


@pytest.fixture(params=["light", "dark"])
def scheme_page(request, browser):
    context = browser.new_context(bypass_csp=True, color_scheme=request.param)
    page = context.new_page()
    yield page
    context.close()


def blocking(page) -> list[str]:
    from axe_playwright_python.sync_playwright import Axe

    results = Axe().run(page)
    found = []
    for violation in results.response["violations"]:
        nodes = ", ".join(node["target"][0] for node in violation["nodes"][:3])
        line = f"{violation['impact']}: {violation['id']} ({nodes})"
        print(line)
        if violation["impact"] in BLOCKING:
            found.append(line)
    return found


def test_signed_in_console_has_no_serious_axe_findings(scheme_page, console):
    scheme_page.goto(console.login)
    scheme_page.locator("#app[aria-busy=false]").wait_for(state="attached")
    assert blocking(scheme_page) == []


def test_signed_out_page_has_no_serious_axe_findings(scheme_page, console):
    scheme_page.goto(console.origin + "/")
    scheme_page.locator("#app[aria-busy=false]").wait_for(state="attached")
    assert blocking(scheme_page) == []
