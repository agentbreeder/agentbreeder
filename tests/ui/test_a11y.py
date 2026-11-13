"""axe over every console screen, light and dark (#816). axe injects its own script, so it runs
in a context that bypasses the page's CSP; test_csp.py and test_shell.py check the CSP itself."""

from __future__ import annotations

import pytest

from .conftest import sign_in
from .test_shell import AUDIT_ROW, DEPLOYMENTS, ROUTES

pytestmark = pytest.mark.ui

# Impacts that fail the run. "minor" and "moderate" findings are printed, not failed.
BLOCKING = {"serious", "critical"}


@pytest.fixture(params=["light", "dark"])
def scheme_page(request, browser):
    context = browser.new_context(bypass_csp=True, color_scheme=request.param)
    page = context.new_page()
    yield page
    context.close()


def blocking(page, where: str) -> list[str]:
    from axe_playwright_python.sync_playwright import Axe

    results = Axe().run(page)
    found = []
    for violation in results.response["violations"]:
        nodes = ", ".join(str(node["target"][0]) for node in violation["nodes"][:3])
        line = f"{where}: {violation['impact']}: {violation['id']} ({nodes})"
        print(line)
        if violation["impact"] in BLOCKING:
            found.append(line)
    return found


def test_every_screen_has_no_serious_axe_findings(scheme_page, console):
    console.scenario(deployments=DEPLOYMENTS, audit_rows=[AUDIT_ROW])
    sign_in(scheme_page, console)
    found = []
    for route in ROUTES:
        scheme_page.goto(f"{console.origin}/#{route}")
        scheme_page.locator("main h1").wait_for()
        scheme_page.wait_for_timeout(400)
        found += blocking(scheme_page, route)
    # A confirm dialog, open.
    scheme_page.goto(f"{console.origin}/#/deployments")
    scheme_page.get_by_role("button", name="Remove").first.click()
    scheme_page.get_by_role("dialog").wait_for()
    found += blocking(scheme_page, "dialog")
    scheme_page.keyboard.press("Escape")
    # The dock, open, with a job.
    scheme_page.goto(f"{console.origin}/#/release")
    scheme_page.get_by_role("button", name="Build only").click()
    scheme_page.get_by_role("complementary", name="Jobs").wait_for()
    found += blocking(scheme_page, "dock")
    assert found == []


def test_signed_out_page_has_no_serious_axe_findings(scheme_page, console):
    scheme_page.goto(console.origin + "/")
    scheme_page.locator("main h1").wait_for()
    assert blocking(scheme_page, "signed out") == []


def test_deployments_with_a_job_running_has_no_serious_axe_findings(scheme_page, console):
    # #881: the off row Remove buttons and their shared reason, light and dark.
    rows = [dict(row, id=f"{n:032x}") for n, row in enumerate(DEPLOYMENTS, 1)]
    console.scenario(deployments=rows, job_seconds=20)
    sign_in(scheme_page, console)
    scheme_page.goto(f"{console.origin}/#/deployments")
    scheme_page.get_by_role("button", name="Remove").first.click()
    scheme_page.get_by_role("dialog").get_by_role("button", name="Remove").click()
    scheme_page.locator(".reason").wait_for()
    assert blocking(scheme_page, "deployments, busy") == []
