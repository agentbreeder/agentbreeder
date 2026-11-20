"""The Docker-down page blocker (M6 #822, console §8.5): it shows on every screen within 15 s of
Docker going down, names the `doctor` remedy, offers Retry, and clears without a reload when
Docker comes back. The fake CLI's `doctor` exits 1 when a required check fails, as the real one
does, and `/api/docker` is never answered from the cache, so a scenario change shows."""

from __future__ import annotations

import pytest

from .conftest import sign_in
from .test_a11y import blocking

pytestmark = pytest.mark.ui

UP = [{"name": "Docker", "passed": True, "message": "ok", "required": True, "info": False}]
DOWN = [
    {
        "name": "Docker",
        "passed": False,
        "message": "Cannot reach the engine; start it and check docker context.",
        "required": True,
        "info": False,
    }
]
WITHIN = 15000  # the acceptance: the blocker within 15 s, and gone again without a reload


def blocker(page):
    return page.get_by_role("region", name="Docker status")


def test_the_blocker_shows_on_any_screen_and_clears_without_a_reload(page, console):
    sign_in(page, console)
    page.goto(f"{console.origin}/#/deployments")
    page.locator("main h1").wait_for()
    page.evaluate("window.__sameDocument = true")
    assert blocker(page).count() == 0

    console.scenario(doctor=DOWN)
    alert = blocker(page).get_by_role("alert")
    alert.wait_for(timeout=WITHIN)
    assert "Docker is not reachable." in alert.inner_text()
    assert "Cannot reach the engine" in alert.inner_text()
    assert "agentbreeder doctor" in alert.inner_text()

    console.scenario(doctor=UP)
    blocker(page).wait_for(state="detached", timeout=WITHIN)
    assert page.evaluate("window.__sameDocument") is True  # no reload happened
    assert page.url.endswith("#/deployments")


def test_retry_asks_at_once_and_keeps_focus(page, console):
    console.scenario(doctor=DOWN)
    sign_in(page, console, project=False)
    blocker(page).wait_for(timeout=WITHIN)
    retry = page.get_by_role("button", name="Retry")
    # Retry while Docker is still down: one request, and the button keeps focus (never disabled).
    with page.expect_response("**/api/docker?fresh=1") as answer:
        retry.focus()
        page.keyboard.press("Enter")
    assert answer.value.json()["data"]["reachable"] is False
    assert page.evaluate("document.activeElement.textContent") == "Retry"
    # Docker starts: Retry asks for a new check (never the server's shared 3 s answer), so the
    # blocker clears at once, before the next timed check.
    console.scenario(doctor=UP)
    with page.expect_response("**/api/docker?fresh=1") as answer:
        retry.click()
    assert answer.value.json()["data"]["reachable"] is True
    blocker(page).wait_for(state="detached", timeout=2000)


def test_a_session_that_ends_drops_the_blocker(page, console):
    console.scenario(doctor=DOWN)
    sign_in(page, console, project=False)
    blocker(page).wait_for(timeout=WITHIN)
    page.route(
        "**/api/docker*",  # Retry's `?fresh=1` as well as the timed check
        lambda route: route.fulfill(
            status=401,
            content_type="application/json",
            body='{"data": null, "error": {"code": "SESSION", "message": "no"}, "stale": false}',
        ),
    )
    page.get_by_role("button", name="Retry").click()
    # M2 #818: the re-auth dialog opens over the page; the blocker goes while it is open.
    page.get_by_role("dialog", name="Session ended").wait_for()
    assert blocker(page).count() == 0
    page.unroute("**/api/docker*")
    console.scenario(doctor=UP)
    page.wait_for_timeout(3200)  # past the server's 3 s reuse of the last (down) answer
    # Signed in again: the blocker follows the first new answer, never the one before sign-out.
    with page.expect_response("**/api/docker") as answer:
        page.goto(console.mint())
    assert answer.value.json()["data"]["reachable"] is True
    page.get_by_role("heading", level=1, name="Home").wait_for()
    assert blocker(page).count() == 0


def test_home_lists_other_failed_checks_but_not_docker(page, console):
    compose = {
        "name": "Docker Compose (services)",
        "passed": False,
        "message": "Docker Compose v2 is not installed.",
        "required": False,
        "info": False,
    }
    console.scenario(doctor=DOWN + [compose])
    sign_in(page, console, project=False)
    blocker(page).wait_for(timeout=WITHIN)
    card = page.get_by_role("main")
    card.get_by_text("Docker Compose v2 is not installed.").wait_for()
    # Docker's own failure is shown once, in the blocker, not again in Home's doctor card.
    assert page.get_by_text("Cannot reach the engine").count() == 1


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_blocker_has_no_serious_axe_findings(browser, console, scheme):
    context = browser.new_context(bypass_csp=True, color_scheme=scheme)
    page = context.new_page()
    try:
        console.scenario(doctor=DOWN)
        sign_in(page, console, project=False)
        blocker(page).wait_for(timeout=WITHIN)
        assert blocking(page, f"docker blocker ({scheme})") == []
    finally:
        context.close()
