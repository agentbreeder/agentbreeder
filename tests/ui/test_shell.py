"""The console shell and every a2 screen in a real browser (#816 M0b), against the installed server
with the fake CLI: routing and sign-in states, the scope switcher, jobs in the dock, and the
walkthrough fixes from #811."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from .conftest import sign_in

pytestmark = pytest.mark.ui

AUDIT_ROW = {
    "seq": 3,
    "at": "2026-11-11T18:02:03+00:00",
    "attempt_id": "a" * 32,
    "deployment_id": "d" * 32,
    "actor": "local:owner",
    "actor_verified": False,
    "action": "deploy",
    "decision": "allow",
    "enforcement_mode": "enforce",
    "checks_evaluated": ["identity"],
    "checks_unavailable": [],
    "reasons": [{"code": "IDENTITY_CONFIRMED"}],
}
DEPLOYMENTS = [
    {
        "id": "d1",
        "name": "demo",
        "target": "local",
        "state": "ready",
        "compliance": "compliant",
        "endpoint": "http://127.0.0.1:9001",
    },
    {
        "id": "d2",
        "name": "demo-candidate",
        "target": "local",
        "state": "ready",
        "compliance": "compliant",
        "endpoint": "http://127.0.0.1:9002",
    },
]


def go(page, console, route: str, heading: str) -> None:
    page.goto(f"{console.origin}/#{route}")
    expect(page.get_by_role("heading", level=1)).to_contain_text(heading)


def test_sign_in_drops_the_token_and_titles_the_route(page, console):
    sign_in(page, console, project=False)
    assert "#t=" not in page.url
    expect(page).to_have_title("Home · AgentBreeder console")
    page.get_by_role("link", name="Audit").click()
    expect(page).to_have_title("Audit · AgentBreeder console")
    # Scoped screen without a project: it asks which one.
    expect(page.get_by_text("This screen works on one project")).to_be_visible()
    # Focus moves to the new route's h1.
    assert page.evaluate("document.activeElement.tagName") == "H1"


def test_skip_link_moves_focus_to_the_heading(page, console):
    sign_in(page, console, project=False)
    page.keyboard.press("Tab")
    expect(page.get_by_role("link", name="Skip to content")).to_be_focused()
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.tagName") == "H1"
    assert page.url.endswith("#/"), "the skip link does not change the route"


def test_services_show_internal_only_ports(page, console):
    sign_in(page, console)
    row = page.get_by_role("row", name=re.compile("postgres"))
    expect(row).to_contain_text("internal only")
    expect(page.get_by_role("row", name=re.compile("litellm"))).to_contain_text("127.0.0.1:4000")
    expect(page.get_by_role("combobox", name="Project scope")).to_have_value(
        re.compile("[0-9a-f]{12}")
    )


def test_grant_review_keeps_typed_values_and_starts_the_job(page, console):
    sign_in(page, console)
    page.get_by_role("button", name="Review and update").click()
    expect(page.get_by_role("heading", name="What will run")).to_be_visible()
    page.get_by_role("button", name="Confirm and start").click()
    expect(page.get_by_role("alert")).to_contain_text("GOOGLE_API_KEY is required.")
    key = page.get_by_label("GOOGLE_API_KEY")
    key.fill("a-very-secret-test-value")
    page.get_by_role("button", name="Confirm and start").click()
    dock = page.get_by_role("complementary", name="Jobs")
    expect(dock).to_be_visible()
    expect(dock).to_contain_text("Start services finished.", timeout=15000)
    assert "a-very-secret-test-value" not in page.content()


def test_job_log_appends_and_shows_controls_as_escapes(page, console):
    console.scenario(
        job_lines={"build": ["first line", "\x1b[31mred\x1b[0m", "abc\u202etxt.exe", "last line"]},
        job_seconds=2.4,
    )
    sign_in(page, console)
    go(page, console, "/release", "Build and release")
    page.get_by_role("button", name="Build only").click()
    log = page.get_by_role("log", name="Build log")
    expect(log.locator(".line").first).to_have_text("first line", timeout=10000)
    expect(page.get_by_role("complementary", name="Jobs")).to_contain_text(
        "Build finished.", timeout=15000
    )
    page.get_by_role("button", name="Show log").click()
    escapes = page.get_by_role("log", name="Build log").locator(".esc").all_text_contents()
    assert escapes == ["\\x1b", "\\x1b", "U+202E"]
    text = page.get_by_role("log", name="Build log").inner_text()
    assert "\x1b" not in text and "\u202e" not in text


def test_running_log_keeps_existing_rows(page, console):
    console.scenario(job_lines={"build": [f"line {n}" for n in range(6)]}, job_seconds=4.5)
    sign_in(page, console)
    go(page, console, "/release", "Build and release")
    page.get_by_role("button", name="Build only").click()
    log = page.get_by_role("log", name="Build log")
    expect(log.locator(".line").first).to_have_text("line 0", timeout=10000)
    handle = log.locator(".line").first.element_handle()
    expect(log.locator(".line")).to_have_count(6, timeout=15000)
    assert handle.evaluate("node => node.isConnected"), "the log rebuilt instead of appending"


def test_disabled_controls_say_why(page, console):
    console.scenario(job_seconds=6)
    sign_in(page, console)
    go(page, console, "/release", "Build and release")
    page.get_by_role("button", name="Build only").click()
    release = page.get_by_role("button", name="Run release")
    expect(release).to_be_disabled()
    reason_id = release.get_attribute("aria-describedby")
    assert reason_id
    expect(page.locator(f"#{reason_id}")).to_contain_text("Build is running in this project")
    expect(page.locator(".reason")).to_have_count(1)  # one shared reason, not one per button


def test_cancel_is_offered_once(page, console):
    console.scenario(job_seconds=20)
    sign_in(page, console)
    go(page, console, "/release", "Build and release")
    page.get_by_role("button", name="Build only").click()
    dock = page.get_by_role("complementary", name="Jobs")
    dock.get_by_role("button", name="Cancel").click()
    expect(dock.get_by_role("button", name="Cancel")).to_have_count(0)
    expect(dock).not_to_contain_text("running", timeout=15000)


def test_confirm_focuses_cancel_and_returns_focus(page, console):
    console.scenario(deployments=DEPLOYMENTS)
    sign_in(page, console)
    go(page, console, "/deployments", "Deployments")
    remove = page.get_by_role("row", name=re.compile("demo-candidate")).get_by_role(
        "button", name="Remove"
    )
    remove.click()
    dialog = page.get_by_role("dialog", name="Remove demo-candidate?")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("button", name="Cancel")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)
    expect(remove).to_be_focused()


def test_candidates_are_violet_and_labelled_temporary(page, console):
    console.scenario(deployments=DEPLOYMENTS)
    sign_in(page, console)
    go(page, console, "/deployments", "Deployments")
    candidate = page.get_by_role("row", name=re.compile("demo-candidate"))
    badge = candidate.locator(".badge.candidate")
    expect(badge).to_have_text("candidate, temporary")
    # The badge's colour is the violet token (resolved through a probe element using the token).
    colours = badge.evaluate(
        """(node) => {
            const probe = document.createElement('span');
            probe.style.color = 'var(--chart-2)';
            document.body.append(probe);
            const violet = getComputedStyle(probe).color;
            probe.remove();
            const style = getComputedStyle(node);
            return { violet, color: style.color, border: style.borderTopColor };
        }"""
    )
    assert colours["color"] == colours["violet"] == colours["border"], colours
    expect(
        page.get_by_role("row", name=re.compile(r"^demo\b(?!-)")).locator(".badge")
    ).to_have_count(0)


def test_hostile_cli_text_shows_escapes(page, console):
    console.scenario(
        deployments=[
            {
                "id": "d9",
                "name": "bot\u202egpj.exe",
                "target": "local",
                "state": "fai\x1b[2Jled",
                "compliance": "<img src=x onerror=alert(1)>",
                "error": "\x1b]0;title\x07",
                "endpoint": None,
            }
        ]
    )
    sign_in(page, console)
    go(page, console, "/deployments", "Deployments")
    row = page.get_by_role("row", name=re.compile("bot"))
    # U+202E (name), \x1b (state), \x1b and \x07 (error); the compliance text has no controls.
    expect(row.locator(".esc")).to_have_text(["U+202E", "\\x1b", "\\x1b", "\\x07"])
    assert row.locator("img").count() == 0
    expect(row).to_contain_text("<img src=x onerror=alert(1)>")
    assert "\u202e" not in row.inner_text() and "\x1b" not in row.inner_text()


def test_audit_time_from_at_and_verify_in_words(page, console):
    console.scenario(
        audit_rows=[AUDIT_ROW],
        audit_verify={
            "ok": False,
            "rows": 2,
            "head": "f" * 64,
            "first_invalid_seq": 3,
            "problem": "hash mismatch",
            "incomplete_attempts": ["b" * 32],
            "limitations": "Evidence, not proof.",
        },
    )
    sign_in(page, console)
    go(page, console, "/audit", "Audit")
    expect(page.get_by_text("Chain broken at row 3: hash mismatch.")).to_be_visible()
    expect(page.get_by_text("1 attempt has no final row: bbbbbbbb.")).to_be_visible()
    body = page.locator("main").inner_text()
    assert "first_invalid_seq" not in body and "incomplete_attempts" not in body
    row = page.get_by_role("row", name=re.compile("deploy"))
    expect(row.locator("td").first).to_contain_text("2026")
    expect(row).to_contain_text("IDENTITY_CONFIRMED")
    expect(row).to_contain_text("(unverified)")


def test_missing_kind_names_the_install_and_the_restart(page, console):
    console.scenario(
        cluster={
            "tools": {
                "kind": {"present": False, "supported": False, "install": "brew install kind"},
                "kubectl": {"present": True, "supported": True, "install": "brew install kubectl"},
            },
            "cluster": False,
            "registry": False,
            "configured": False,
        }
    )
    sign_in(page, console)
    go(page, console, "/targets/kubernetes", "Kubernetes")
    expect(
        page.get_by_text(re.compile("brew install kind.*restart agentbreeder up"))
    ).to_be_visible()
    connect = page.get_by_role("button", name="Connect local cluster")
    expect(connect).to_be_disabled()
    expect(page.locator(f"#{connect.get_attribute('aria-describedby')}")).to_contain_text(
        "Install kind"
    )
    deploy = page.get_by_role("button", name="Deploy to cluster")
    expect(page.locator(f"#{deploy.get_attribute('aria-describedby')}")).to_contain_text(
        "Needs a connected cluster"
    )


def test_grant_pending_shows_the_services_step_not_promote(page, console):
    console.scenario(
        deployments=DEPLOYMENTS[:1],
        cluster={
            "tools": {
                "kind": {"present": True, "supported": True, "install": ""},
                "kubectl": {"present": True, "supported": True, "install": ""},
            },
            "cluster": True,
            "registry": True,
            "configured": False,
        },
    )
    sign_in(page, console)
    go(page, console, "/targets/kubernetes", "Kubernetes")
    expect(page.get_by_role("heading", name="Next: accept the grant change")).to_be_visible()
    deploy = page.get_by_role("button", name="Deploy to cluster")
    expect(deploy).to_be_disabled()
    expect(page.locator(f"#{deploy.get_attribute('aria-describedby')}")).to_contain_text(
        "accepted grant"
    )
    page.get_by_role("button", name="Review the grant change").click()
    page.wait_for_url("**/#/services")


def test_cost_figures_only_for_northwind(page, console):
    sign_in(page, console)
    go(page, console, "/release", "Build and release")
    expect(page.locator("main")).not_to_contain_text("$0.24")
    expect(page.locator("main")).to_contain_text("costs money in model usage")


def test_scope_switcher_all_projects(page, console):
    sign_in(page, console)
    page.get_by_role("combobox", name="Project scope").select_option(label="All projects")
    expect(page.get_by_text("This screen works on one project")).to_be_visible()
    page.get_by_role("button", name=re.compile("demo")).click()
    expect(page.get_by_role("heading", name="Stack services")).to_be_visible()


def test_session_ended_then_sign_in_returns_to_the_route(page, console):
    sign_in(page, console)
    go(page, console, "/audit", "Audit")
    # The server ends the session: its next answer is a 401 (as after 8 hours, or 1 hour idle).
    page.route(
        "**/api/projects",
        lambda route: route.fulfill(
            status=401,
            content_type="application/json",
            body='{"data": null, "error": {"code": "SESSION", "message": "no"}, "stale": false}',
        ),
    )
    page.reload()
    expect(page.get_by_role("heading", level=1)).to_have_text("Session ended")
    page.unroute("**/api/projects")
    page.goto(console.mint())
    expect(page.get_by_role("heading", level=1)).to_contain_text("Audit")
    assert "#t=" not in page.url and page.url.endswith("#/audit")


def test_a_link_pasted_into_a_signed_out_tab_is_redeemed(page, console):
    page.goto(console.origin + "/#/audit")
    expect(page.get_by_role("heading", level=1)).to_have_text("Sign in")
    token = console.login.split("#", 1)[1]
    page.evaluate("(hash) => { location.hash = hash; }", "#" + token)
    expect(page.get_by_role("heading", level=1)).to_contain_text("Audit")
    assert "#t=" not in page.url


def test_a_used_link_says_so(page, console):
    page.goto(console.login)
    page.get_by_role("heading", level=1, name="Home").wait_for()
    other = page.context.new_page()
    other.goto(console.login)
    expect(other.get_by_role("heading", level=1)).to_have_text("Sign in")
    expect(other.get_by_text("already used or has expired")).to_be_visible()


def test_unreachable_is_not_session_ended(page, console):
    sign_in(page, console)
    page.route("**/api/**", lambda route: route.abort())
    # No click: an aborted background poll can switch to the unreachable page first, and the
    # link would be gone. Either the poll or this navigation's read gets no answer.
    page.evaluate("location.hash = '#/deployments'")
    expect(page.get_by_role("heading", level=1)).to_have_text(
        "Console not reachable at this address", timeout=10000
    )
    page.unroute("**/api/**")
    page.get_by_role("button", name="Try now").click()
    expect(page.get_by_role("heading", level=1)).to_contain_text("Deployments")


ROUTES = ["/", "/services", "/release", "/deployments", "/targets/kubernetes", "/audit"]


def test_every_screen_has_zero_csp_violations(page, console, violations):
    console.scenario(deployments=DEPLOYMENTS, audit_rows=[AUDIT_ROW])
    sign_in(page, console)
    for route in ROUTES:
        page.goto(f"{console.origin}/#{route}")
        page.locator("main h1").wait_for()
        page.wait_for_timeout(300)
    assert violations(page) == []


def add_project(page, console, name: str) -> None:
    page.goto(f"{console.origin}/#/")
    page.get_by_label("Absolute path").fill(str(console.project(name)))
    page.get_by_role("button", name="Add project").click()
    page.wait_for_url("**/#/services")
    # Wait for the Services h1 itself to take focus: the scope change can focus the same h1 while
    # it still reads "Home", and the route change then focuses it again a moment later.
    expect(page.locator("main h1")).to_contain_text("Services")
    expect(page.locator("main h1")).to_be_focused()
    page.get_by_role("heading", name="Stack services").wait_for()


def test_two_projects_run_jobs_at_once(page, console):
    console.scenario(job_seconds=8)
    sign_in(page, console)
    add_project(page, console, "second")
    scope = page.get_by_role("combobox", name="Project scope")
    for label in ("demo (dev)", "second (dev)"):
        scope.select_option(label=label)
        go(page, console, "/release", "Build and release")
        page.get_by_role("button", name="Build only").click()
        expect(page.get_by_role("button", name="Build only")).to_be_disabled()
    expect(page.locator("#activity-pill")).to_have_text("2 running")
    dock = page.get_by_role("complementary", name="Jobs")
    expect(dock.get_by_role("region")).to_have_count(2)
    expect(dock).to_contain_text("demo: Build")
    expect(dock).to_contain_text("second: Build")


def test_scope_change_keeps_focus_on_the_switcher(page, console):
    sign_in(page, console)
    add_project(page, console, "second")
    scope = page.get_by_role("combobox", name="Project scope")
    scope.focus()
    scope.select_option(label="demo (dev)")
    expect(scope).to_be_focused()


def test_starting_a_job_moves_focus_to_the_dock_and_closing_returns_it(page, console):
    console.scenario(job_seconds=4)
    sign_in(page, console)
    go(page, console, "/release", "Build and release")
    page.get_by_role("button", name="Build only").click()
    expect(page.locator("#dock-title")).to_be_focused()
    page.get_by_role("complementary", name="Jobs").get_by_role("button", name="Close").click()
    expect(page.locator("#activity-pill")).to_be_focused()


def test_user_menu_closes_on_escape_and_outside_click(page, console):
    sign_in(page, console, project=False)
    summary = page.locator("summary", has_text="Owner")
    expect(summary).to_have_accessible_name("Owner menu")
    summary.click()
    expect(page.get_by_role("button", name="Sign out of this tab")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Sign out of this tab")).to_be_hidden()
    expect(summary).to_be_focused()
    summary.click()
    page.locator("main h1").click()
    expect(page.get_by_role("button", name="Sign out of this tab")).to_be_hidden()


def test_a_new_link_in_a_signed_in_tab_keeps_the_route(page, console):
    sign_in(page, console)
    go(page, console, "/deployments", "Deployments")
    token = console.mint().split("#", 1)[1]
    page.evaluate("(hash) => { location.hash = hash; }", "#" + token)
    expect(page.get_by_role("heading", level=1)).to_contain_text("Deployments")
    assert "#t=" not in page.url
