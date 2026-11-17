"""The console on design v2 in a real browser (#1116): no theme flash (spec §14.3), the System /
Light / Dark toggle (§10.1), glass on the header, menus and dialogs (§6) and links."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_shell import DEPLOYMENTS

pytestmark = pytest.mark.ui

# Records the theme at the moment the parser inserts <body>: after the classic theme.js in <head>
# and before app.js (a module, which runs only after parsing), so it sees theme.js alone.
FIRST_PAINT = """
new MutationObserver((records, observer) => {
  if (!document.body || window.__first) return;
  const root = document.documentElement;
  window.__first = { dark: root.classList.contains("dark"), scheme: root.style.colorScheme };
  observer.disconnect();
}).observe(document, { childList: true, subtree: true });
"""
BLOCKED_STORAGE = """
Object.defineProperty(window, "localStorage", {
  get() { throw new DOMException("blocked", "SecurityError"); },
});
"""


def stored(value: str) -> str:
    return f'try {{ localStorage.setItem("console-theme", {value!r}); }} catch (e) {{}}'


def first_paint(browser, console, scheme: str, *scripts: str) -> dict:
    context = browser.new_context(color_scheme=scheme)
    for script in (FIRST_PAINT, *scripts):
        context.add_init_script(script)
    # app.js never loads, so whatever theme the page has comes from theme.js alone.
    context.route("**/app.js", lambda route: route.abort())
    page = context.new_page()
    try:
        page.goto(console.origin + "/", wait_until="load")
        first = page.evaluate("window.__first")
        settled = page.evaluate(
            "({ dark: document.documentElement.classList.contains('dark'),"
            " background: getComputedStyle(document.body).backgroundColor,"
            " meta: document.querySelector('meta[name=theme-color]').content })"
        )
        assert settled["dark"] == first["dark"]
        return {**first, "background": settled["background"], "meta": settled["meta"]}
    finally:
        context.close()


DARK_BG = "rgb(10, 10, 10)"
LIGHT_BG = "rgb(255, 255, 255)"


@pytest.mark.parametrize(
    ("scheme", "scripts", "dark"),
    [
        ("dark", (), True),  # (a) a dark system and no stored preference
        ("light", (stored("dark"),), True),  # (b) a stored "dark" beats a light system
        ("dark", (stored("light"),), False),
        ("dark", (BLOCKED_STORAGE,), True),  # (c) storage throws: follow the system
        ("light", (BLOCKED_STORAGE,), False),
        ("dark", (stored("x y"),), True),  # a tampered value is "system"
        ("light", (stored("x y"),), False),
    ],
    ids=[
        "system-dark",
        "stored-dark",
        "stored-light",
        "blocked-dark",
        "blocked-light",
        "tampered-dark",
        "tampered-light",
    ],
)
def test_the_theme_is_set_before_first_paint(browser, console, scheme, scripts, dark):
    seen = first_paint(browser, console, scheme, *scripts)
    assert seen["dark"] is dark
    assert seen["scheme"] == ("dark" if dark else "light")
    assert seen["background"] == (DARK_BG if dark else LIGHT_BG)
    assert seen["meta"] == ("#0a0a0a" if dark else "#ffffff")


def test_a_tampered_value_never_becomes_a_class(browser, console):
    context = browser.new_context(color_scheme="light")
    context.add_init_script(stored("dark evil"))
    page = context.new_page()
    try:
        page.goto(console.origin + "/")
        page.locator("main h1").wait_for()
        assert page.evaluate("[...document.documentElement.classList]") == []
    finally:
        context.close()


def test_the_toggle_is_a_radiogroup_that_persists(page, console):
    sign_in(page, console, project=False)
    group = page.get_by_role("radiogroup", name="Theme")
    system = group.get_by_role("radio", name="System", exact=True)
    light = group.get_by_role("radio", name="Light", exact=True)
    dark = group.get_by_role("radio", name="Dark", exact=True)
    expect(system).to_have_attribute("aria-checked", "true")
    expect(system).to_have_attribute("tabindex", "0")
    expect(dark).to_have_attribute("tabindex", "-1")
    dark.click()
    expect(dark).to_have_attribute("aria-checked", "true")
    expect(page.locator("html")).to_have_class("dark")
    assert page.evaluate("document.documentElement.style.colorScheme") == "dark"
    assert page.evaluate("localStorage.getItem('console-theme')") == "dark"
    metas = page.locator('meta[name="theme-color"]')
    for index in range(metas.count()):
        expect(metas.nth(index)).to_have_attribute("content", "#0a0a0a")
    # Arrow keys move to, and choose, the next option (wrapping round).
    dark.focus()
    page.keyboard.press("ArrowRight")
    expect(system).to_have_attribute("aria-checked", "true")
    expect(system).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(light).to_be_focused()
    expect(page.locator("html")).not_to_have_class("dark")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    expect(dark).to_be_focused()
    page.keyboard.press("Home")
    expect(system).to_be_focused()
    expect(system).to_have_attribute("aria-checked", "true")
    page.keyboard.press("End")
    expect(dark).to_be_focused()
    expect(dark).to_have_attribute("aria-checked", "true")
    page.reload()
    page.get_by_role("radiogroup", name="Theme").wait_for()
    expect(page.locator("html")).to_have_class("dark")
    expect(page.get_by_role("radio", name="Dark", exact=True)).to_have_attribute(
        "aria-checked", "true"
    )
    # The theme left the user menu.
    page.locator("details.menu > summary").click()
    expect(page.get_by_role("group", name="User menu")).to_be_visible()
    expect(page.locator("select#theme")).to_have_count(0)


def _blurred(locator) -> str:
    return locator.evaluate("(el) => getComputedStyle(el).backdropFilter")


def test_glass_is_on_the_header_menu_and_dialog_only(page, console):
    console.scenario(deployments=DEPLOYMENTS)
    sign_in(page, console)
    header = page.locator("header.top")
    assert "blur(16px)" in _blurred(header)
    assert header.evaluate("(el) => getComputedStyle(el).position") == "sticky"
    page.locator("details.menu > summary").click()
    assert "blur(16px)" in _blurred(page.get_by_role("group", name="User menu"))
    page.keyboard.press("Escape")
    assert _blurred(page.locator("main .card").first) == "none"
    page.goto(f"{console.origin}/#/deployments")
    page.get_by_role("button", name="Remove").first.click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for()
    assert "blur(16px)" in _blurred(dialog)
    assert dialog.evaluate("(el) => getComputedStyle(el).borderTopLeftRadius") == "16px"
    page.keyboard.press("Escape")


def test_links_are_underlined_and_text_coloured(page, console):
    sign_in(page, console, project=False)
    page.goto(f"{console.origin}/#/nowhere")
    link = page.get_by_role("link", name="Go to Home")
    style = link.evaluate(
        "(el) => [getComputedStyle(el).textDecorationLine, getComputedStyle(el).color,"
        " getComputedStyle(document.body).color]"
    )
    assert style[0] == "underline"
    assert style[1] == style[2]


@pytest.mark.parametrize("route", ["/", "/agents", "/logs", "/deployments"])
def test_no_page_scrolls_sideways_at_320_px(browser, console, route):
    """WCAG 1.4.10 reflow: at 320 CSS px only a table or a log scrolls sideways, inside itself."""
    console.scenario(deployments=DEPLOYMENTS)
    context = browser.new_context(viewport={"width": 320, "height": 800})
    page = context.new_page()
    try:
        sign_in(page, console)
        page.goto(f"{console.origin}/#{route}")
        page.locator("main h1").wait_for()
        page.wait_for_timeout(300)
        assert page.evaluate("document.documentElement.scrollWidth") <= 320
    finally:
        context.close()


def test_the_toggle_has_44_px_targets_on_touch(browser, console):
    context = browser.new_context(
        viewport={"width": 390, "height": 800}, has_touch=True, is_mobile=True
    )
    page = context.new_page()
    try:
        sign_in(page, console, project=False)
        for name in ("System", "Light", "Dark"):
            box = page.get_by_role("radio", name=name, exact=True).bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44, name
    finally:
        context.close()


@pytest.mark.parametrize(
    ("width", "position"), [(1280, "sticky"), (1100, "sticky"), (1000, "static"), (390, "static")]
)
def test_the_header_is_sticky_only_when_it_fits_one_row(browser, console, width, position):
    """Focus not obscured (WCAG 2.4.11): a header that wraps scrolls away with the page."""
    context = browser.new_context(viewport={"width": width, "height": 800})
    page = context.new_page()
    try:
        sign_in(page, console, project=False)
        header = page.locator("header.top")
        assert header.evaluate("(el) => getComputedStyle(el).position") == position
        if position == "sticky":
            assert header.bounding_box()["height"] <= 72  # the scroll padding it reserves
    finally:
        context.close()
