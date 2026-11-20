"""Sign-in (M2 #818) in a real browser, against the installed server with the fake CLI: first-run
setup, then sign-in; the a2 upgrade path; the backoff; a reset while signed in; the re-auth dialog
keeping an editor's draft; Settings; axe in light and dark on every new screen."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401 (fixture)
from .test_edit import ADDED, NAME, open_prompt

pytestmark = pytest.mark.ui

NEW = "a fresh console password"
FIRST_RUN = pytest.mark.parametrize("console_args", [["--first-run"]])
TOKEN_IN_URL = re.compile(r"#t=")


def h1(page):
    return page.get_by_role("heading", level=1)


def fill_sign_in(scope, password: str) -> None:
    scope.get_by_label("Password", exact=True).fill(password)
    scope.get_by_role("button", name="Sign in").click()


def set_up(page, password: str = NEW) -> None:
    page.get_by_label("New password").fill(password)
    page.get_by_label("Type it again").fill(password)
    page.get_by_role("button", name="Set password").click()


def api_session(page, console) -> str:
    """A session from a fresh one-time link, through the API as the page does."""
    token = console.mint().split("#t=", 1)[1]
    reply = page.request.post(
        f"{console.origin}/api/session",
        headers={
            "X-AgentBreeder-Center": "1",
            "Origin": console.origin,
            "Content-Type": "application/json",
        },
        data={"token": token},
    )
    assert reply.status == 200
    return reply.json()["data"]["session"]


# -- first run and the a2 upgrade path (S-28) -----


@FIRST_RUN
def test_fresh_state_reaches_setup_then_sign_in(page, console):
    page.goto(console.login)
    expect(h1(page)).to_have_text("Set up the console")
    expect(page).not_to_have_url(TOKEN_IN_URL)
    set_up(page, "short")
    expect(page.get_by_role("alert")).to_have_text("Use at least 12 characters.")
    page.get_by_label("New password").fill(NEW)
    page.get_by_label("Type it again").fill(NEW + "x")
    page.get_by_role("button", name="Set password").click()
    expect(page.get_by_role("alert")).to_have_text("The two passwords differ.")
    set_up(page, "password1234")  # long enough, but on the common list: the server says so
    expect(page.get_by_role("alert")).to_contain_text("too common")
    set_up(page)
    expect(h1(page)).to_have_text("Sign in")
    expect(page.locator("main")).to_contain_text("Password set for admin@localhost")
    fill_sign_in(page, NEW)
    expect(h1(page)).to_have_text("Home")


@FIRST_RUN
def test_without_the_link_first_run_says_to_open_it_and_offers_no_form(page, console):
    page.goto(console.origin + "/")
    expect(h1(page)).to_have_text("Set up the console")
    expect(page.locator("main")).to_contain_text("Open the one-time setup link")
    expect(page.get_by_label("Password", exact=True)).to_have_count(0)
    # The server refuses setup without a link's session too.
    reply = page.request.post(
        f"{console.origin}/api/setup",
        headers={
            "X-AgentBreeder-Center": "1",
            "Origin": console.origin,
            "Content-Type": "application/json",
        },
        data={"password": NEW},
    )
    assert reply.status == 401


@FIRST_RUN
def test_a2_state_reaches_setup_with_projects_kept(page, console):
    # An a2 console: a project registered, no password (its users table is empty).
    session = api_session(page, console)
    added = page.request.post(
        f"{console.origin}/api/projects",
        headers={
            "X-AgentBreeder-Center": "1",
            "X-AgentBreeder-Session": session,
            "Origin": console.origin,
            "Content-Type": "application/json",
        },
        data={"path": str(console.project())},
    )
    assert added.status == 200
    page.goto(console.mint())
    expect(h1(page)).to_have_text("Set up the console")
    set_up(page)
    fill_sign_in(page, NEW)
    expect(h1(page)).to_have_text("Home")
    expect(page.get_by_label("Project scope")).to_contain_text("demo")


# -- sign-in and the backoff -----


def test_sign_in_with_the_password_returns_to_the_route(page, console):
    page.goto(console.origin + "/#/audit")
    expect(h1(page)).to_have_text("Sign in")
    expect(page.get_by_label("Email")).to_have_value("admin@localhost")
    fill_sign_in(page, "not the password")
    expect(page.get_by_role("alert")).to_have_text("Wrong email or password")
    fill_sign_in(page, console.password)
    expect(h1(page)).to_contain_text("Audit")
    page.locator("summary", has_text="Owner").click()
    expect(page.get_by_text("Signed in on this tab as admin@localhost.")).to_be_visible()


def test_the_sixth_wrong_password_waits_and_names_the_terminal_link(page, console):
    page.goto(console.origin + "/")
    expect(h1(page)).to_have_text("Sign in")
    for _ in range(5):
        with page.expect_response("**/api/session") as answer:
            fill_sign_in(page, "not the password")
        assert answer.value.status == 403
    with page.expect_response("**/api/session") as answer:
        fill_sign_in(page, console.password)  # even the right one waits
    assert answer.value.status == 429
    alert = page.get_by_role("alert")
    expect(alert).to_contain_text("Too many attempts. Wait 30 s")
    expect(alert).to_contain_text("press Enter in the terminal running `agentbreeder up`")
    expect(page.get_by_role("button", name="Sign in")).to_be_disabled()
    expect(page.get_by_role("status").filter(has_text=re.compile(r"Wait \d+ s"))).to_be_visible()
    # The terminal link is never subject to the wait (S-14).
    page.goto(console.mint())
    expect(h1(page)).to_have_text("Home")


# -- a session that ends while the page is open -----


def test_a_reset_while_signed_in_opens_the_re_auth_dialog_over_the_route(page, console):
    sign_in(page, console)
    page.goto(f"{console.origin}/#/audit")
    expect(h1(page)).to_contain_text("Audit")
    console.reset_password(NEW)  # `up --reset-password` from another terminal
    page.goto(f"{console.origin}/#/deployments")
    dialog = page.get_by_role("dialog", name="Session ended")
    expect(dialog).to_be_visible()
    # It cannot be dismissed: Chromium closes a modal on a second Esc even when `cancel` is
    # prevented, so a close by any route reopens it.
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    page.evaluate("document.querySelector('dialog.reauth').close()")
    expect(dialog).to_be_visible()
    assert page.evaluate("document.querySelector('dialog.reauth').open")
    fill_sign_in(dialog, console.password)  # the old password no longer works
    expect(dialog.get_by_role("alert")).to_have_text("Wrong email or password")
    fill_sign_in(dialog, NEW)
    expect(dialog).to_have_count(0)
    expect(h1(page)).to_contain_text("Deployments")
    expect(page).to_have_url(re.compile(r"#/deployments$"))


def test_session_ended_after_a_reset_names_the_password_cause(page, console):
    """#879 and the M2 run: a reset from the terminal ends the session, and the page says so."""
    sign_in(page, console)
    page.goto(f"{console.origin}/#/audit")
    expect(h1(page)).to_contain_text("Audit")
    console.reset_password(NEW)
    page.reload()
    expect(h1(page)).to_have_text("Session ended")
    main = page.locator("main")
    expect(main).to_contain_text("or the console was restarted")
    expect(main).to_contain_text("or you signed out everywhere")
    expect(main).to_contain_text(
        "the password was changed in another tab or reset with agentbreeder up --reset-password"
    )
    fill_sign_in(page, NEW)
    expect(h1(page)).to_contain_text("Audit")


def test_a_401_during_an_edit_keeps_the_draft_and_the_route(page, console):
    open_prompt(page, console)
    editor = page.get_by_role("region", name="Edit 1.0.1")
    text = editor.get_by_label("Text of prompt.md")
    text.press("ControlOrMeta+End")
    text.type(f"\n{ADDED}\n")
    # The session ends exactly at the edit's request (a 401 from the server), not at a poll.
    page.route(
        "**/registry/edit",
        lambda route: route.fulfill(
            status=401,
            content_type="application/json",
            body='{"data": null, "error": {"code": "SIGN_IN", "message": "no"}, "stale": false}',
        ),
    )
    editor.get_by_role("button", name="Review changes").click()
    dialog = page.get_by_role("dialog", name="Session ended")
    expect(dialog).to_be_visible()
    page.unroute("**/registry/edit")
    fill_sign_in(dialog, console.password)
    expect(dialog).to_have_count(0)
    expect(page).to_have_url(re.compile(rf"#/registry/prompts/{NAME}$"))
    expect(text).to_have_value(re.compile(re.escape(ADDED)))
    editor.get_by_role("button", name="Review changes").click()
    expect(editor.get_by_role("region", name="Review")).to_contain_text(ADDED)


def test_a_draft_is_restored_after_a_reload_only_once_signed_in(page, console):
    open_prompt(page, console)
    editor = page.get_by_role("region", name="Edit 1.0.1")
    text = editor.get_by_label("Text of prompt.md")
    text.press("ControlOrMeta+End")
    text.type(f"\n{ADDED}\n")
    console.restart()  # every session ends; the tab keeps its sessionStorage
    page.reload()
    # "Session ended" when the reload is the first to see the 401, "Sign in" when a poll saw it
    # first (the tab then dropped its token); either way no editor shows before sign-in.
    expect(h1(page)).to_have_text(re.compile(r"^(Session ended|Sign in)$"))
    expect(page.get_by_label("Text of prompt.md")).to_have_count(0)
    fill_sign_in(page, console.password)
    restored = page.get_by_role("region", name="Edit 1.0.1").get_by_label("Text of prompt.md")
    expect(restored).to_have_value(re.compile(re.escape(ADDED)))
    expect(page.get_by_text("Unsaved changes")).to_be_visible()


# -- Settings -----


def open_settings(page, console) -> None:
    sign_in(page, console, project=False)
    page.locator("summary", has_text="Owner").click()
    page.get_by_role("link", name="Settings").click()
    expect(h1(page)).to_have_text("Settings")


def test_settings_change_password_needs_the_current_one(page, console):
    open_settings(page, console)
    other = page.context.new_page()
    other.goto(console.mint())
    expect(h1(other)).to_have_text("Home")
    card = page.get_by_role("region", name="Password", exact=True)
    card.get_by_role("button", name="Change password").click()
    expect(card.get_by_role("alert")).to_contain_text("Enter your current password.")
    card.get_by_label("Current password").fill("not the password")
    card.get_by_label("New password", exact=True).fill(NEW)
    card.get_by_label("Type the new password again").fill(NEW)
    card.get_by_role("button", name="Change password").click()
    expect(card.get_by_role("alert")).to_contain_text("The current password is wrong")
    card.get_by_label("Current password").fill(console.password)
    card.get_by_role("button", name="Change password").click()
    expect(card.get_by_role("status")).to_contain_text("Password changed")
    # This tab stays signed in; the other tab's session ended.
    page.goto(f"{console.origin}/#/audit")
    expect(h1(page)).to_contain_text("Audit")
    other.goto(f"{console.origin}/#/audit")
    other.reload()
    expect(h1(other)).to_have_text("Session ended")
    # The page names a password change in another tab as a cause (#879, the M2 run).
    expect(other.locator("main")).to_contain_text(
        "the password was changed in another tab or reset with agentbreeder up --reset-password"
    )
    fill_sign_in(other, NEW)
    expect(h1(other)).to_contain_text("Audit")


def test_settings_session_length_events_and_sign_out_everywhere(page, console):
    open_settings(page, console)
    sessions = page.get_by_role("region", name="Sessions", exact=True)
    save = sessions.get_by_role("button", name="Save")
    expect(save).to_be_disabled()
    sessions.get_by_label("Session length").select_option("4")
    save.click()
    expect(sessions.get_by_role("status")).to_contain_text("Sessions now last 4 hours")
    events = page.get_by_role("table", name="Security events")
    expect(events).to_contain_text("Session length changed")
    # An HTTP event names the channel, not a client the server cannot tell apart (#1180).
    changed = events.get_by_role("row").filter(has_text="Session length changed").first
    expect(changed).to_contain_text("Console (HTTP)")
    expect(events).not_to_contain_text("Browser")
    expect(events).to_contain_text("Sign-in with a one-time link")
    sessions.get_by_role("button", name="Sign out everywhere").click()
    dialog = page.get_by_role("dialog", name="Sign out everywhere?")
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
    dialog.get_by_role("button", name="Sign out everywhere").click()
    expect(h1(page)).to_have_text("Signed out")
    fill_sign_in(page, console.password)
    expect(h1(page)).to_have_text("Settings")


def test_sign_out_of_this_tab_ends_its_session_on_the_server(page, console):
    sign_in(page, console, project=False)
    token = page.evaluate("sessionStorage.getItem('console-session')")
    page.locator("summary", has_text="Owner").click()
    page.get_by_role("button", name="Sign out of this tab").click()
    expect(h1(page)).to_have_text("Sign in")
    reply = page.request.get(
        f"{console.origin}/api/projects",
        headers={"X-AgentBreeder-Center": "1", "X-AgentBreeder-Session": token},
    )
    assert reply.status == 401


def test_sign_out_clears_this_tab_s_drafts(page, console):
    open_prompt(page, console)
    text = page.get_by_role("region", name="Edit 1.0.1").get_by_label("Text of prompt.md")
    text.press("ControlOrMeta+End")
    text.type(f"\n{ADDED}\n")
    drafts = "Object.keys(sessionStorage).filter((key) => key.startsWith('console-draft:'))"
    page.wait_for_function(f"{drafts}.length > 0")
    page.locator("summary", has_text="Owner").click()
    page.get_by_role("button", name="Sign out of this tab").click()
    expect(h1(page)).to_have_text("Sign in")
    assert page.evaluate(drafts) == []


def test_localhost_redirects_to_the_one_host_form(page, console):
    port = console.origin.rsplit(":", 1)[1]
    page.goto(f"http://localhost:{port}/")
    expect(page).to_have_url(f"{console.origin}/")
    expect(h1(page)).to_have_text("Sign in")


# -- axe, light and dark -----


@FIRST_RUN
def test_setup_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    scheme_page.goto(console.login)
    expect(h1(scheme_page)).to_have_text("Set up the console")
    set_up(scheme_page, "short")
    expect(scheme_page.get_by_role("alert")).to_be_visible()
    assert blocking(scheme_page, "setup") == []


def test_sign_in_settings_and_re_auth_have_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    scheme_page.goto(console.origin + "/")
    expect(h1(scheme_page)).to_have_text("Sign in")
    fill_sign_in(scheme_page, "not the password")
    expect(scheme_page.get_by_role("alert")).to_be_visible()
    found = blocking(scheme_page, "sign-in")
    fill_sign_in(scheme_page, console.password)
    expect(h1(scheme_page)).to_have_text("Home")
    scheme_page.goto(f"{console.origin}/#/settings")
    expect(scheme_page.get_by_role("table", name="Security events")).to_be_visible()
    found += blocking(scheme_page, "settings")
    console.reset_password(NEW)
    scheme_page.goto(f"{console.origin}/#/audit")
    expect(scheme_page.get_by_role("dialog", name="Session ended")).to_be_visible()
    expect(scheme_page.get_by_label("Password", exact=True)).to_be_visible()
    found += blocking(scheme_page, "re-auth dialog")
    assert found == []
