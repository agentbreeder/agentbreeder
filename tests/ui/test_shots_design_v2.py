"""Screenshots of the console on design v2 (#1116, spec §14.4), taken only when
AGENTBREEDER_UI_SHOTS names an output directory. Run with a neutral --basetemp (such as
/tmp/ab-shots) so no home path or user name can appear; each page's text is checked for one."""

from __future__ import annotations

import getpass
import os
from pathlib import Path

import pytest

from .conftest import sign_in
from .test_shell import AUDIT_ROW, DEPLOYMENTS

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(
        not os.environ.get("AGENTBREEDER_UI_SHOTS"), reason="screenshots on request"
    ),
]

WIDTHS = (1280, 390, 320)


def _private(text: str) -> list[str]:
    words = {getpass.getuser(), str(Path.home())}
    return [word for word in words if word and word in text]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_screenshots(browser, console, scheme):
    out = Path(os.environ["AGENTBREEDER_UI_SHOTS"])
    out.mkdir(parents=True, exist_ok=True)
    console.scenario(deployments=DEPLOYMENTS, audit_rows=[AUDIT_ROW])
    for width in WIDTHS:
        context = browser.new_context(
            color_scheme=scheme, viewport={"width": width, "height": 900}, device_scale_factor=1
        )
        page = context.new_page()
        try:
            if width == WIDTHS[0]:
                sign_in(page, console)
            else:  # a one-time link works once: mint another for each context
                page.goto(console.mint())
                page.get_by_role("heading", level=1, name="Home").wait_for()
            if page.get_by_label("Project scope").locator("option").count() > 1:
                page.get_by_label("Project scope").select_option(index=1)
            shots = [
                ("home", "/"),
                ("agents", "/agents"),
                ("logs", "/logs"),
                ("deployments", "/deployments"),
            ]
            for name, route in shots:
                page.goto(f"{console.origin}/#{route}")
                page.locator("main h1").wait_for()
                page.wait_for_timeout(500)
                assert _private(page.locator("body").inner_text()) == [], name
                page.screenshot(
                    path=out / f"{name}-{scheme}-{width}.png", full_page=width != WIDTHS[0]
                )
            page.goto(f"{console.origin}/#/")
            page.locator("main h1").wait_for()
            page.locator("details.menu > summary").click()
            page.get_by_role("group", name="User menu").wait_for()
            page.wait_for_timeout(300)
            page.screenshot(path=out / f"menu-{scheme}-{width}.png")
            page.keyboard.press("Escape")
            page.goto(f"{console.origin}/#/deployments")
            page.get_by_role("button", name="Remove").first.click()
            page.get_by_role("dialog").wait_for()
            page.wait_for_timeout(300)
            assert _private(page.locator("body").inner_text()) == [], "dialog"
            page.screenshot(path=out / f"dialog-{scheme}-{width}.png")
        finally:
            context.close()


def test_forced_colours_screenshot(browser, console):
    """The header, toggle and gate line in forced colours (Windows high contrast), spec §14.4."""
    out = Path(os.environ["AGENTBREEDER_UI_SHOTS"])
    console.scenario(deployments=DEPLOYMENTS)
    context = browser.new_context(forced_colors="active", viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        sign_in(page, console)
        page.goto(f"{console.origin}/#/deployments")
        page.locator("main h1").wait_for()
        page.get_by_role("radio", name="Dark", exact=True).click()
        page.wait_for_timeout(300)
        assert _private(page.locator("body").inner_text()) == []
        page.screenshot(path=out / "deployments-forced-colors-1280.png")
    finally:
        context.close()
