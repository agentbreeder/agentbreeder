"""The log viewer (#821, M5 console half, PR 1): one snapshot of a service's or a deployment's
logs through `services logs --json` / `logs ID --json` (the fake CLI makes the lines), filtered,
searched, wrapped and copied in the browser. Follow arrives with the pumped children (PR 2)."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_shell import DEPLOYMENTS

pytestmark = pytest.mark.ui

LOCAL = "0" * 31 + "1"
CLUSTER = "0" * 31 + "2"
ROWS = [dict(DEPLOYMENTS[0], id=LOCAL), dict(DEPLOYMENTS[0], id=CLUSTER, target="kubernetes")]


def lines(page):
    return page.get_by_role("region", name="Log lines").locator(".logline")


def open_service(page, console, name="litellm"):
    sign_in(page, console)
    page.get_by_role("link", name=f"Logs of {name}").click()
    page.wait_for_url(f"**/#/logs/service/{name}")
    expect(page.get_by_role("heading", name=f"Logs: {name}")).to_be_visible()


def test_a_service_row_opens_its_logs_with_the_notes(page, console):
    open_service(page, console)
    expect(lines(page).first).to_contain_text("request 1 handled")
    notes = page.locator(".log-notes")
    expect(notes).to_contain_text("200 lines read (at most the last 200).")
    expect(notes).to_contain_text("[redacted]")
    expect(notes).not_to_contain_text("4 MiB")
    # The source picker shows what is open, and the sidebar still marks Logs.
    expect(page.get_by_label("Read the logs of")).to_have_value("service:litellm")
    expect(page.locator("nav.sidebar a[aria-current='page']")).to_have_text("Logs")


def test_ten_thousand_lines_are_windowed(page, console):
    console.scenario(generated_lines=10000)
    open_service(page, console)
    page.get_by_label("Lines").select_option("10000")
    page.get_by_role("button", name="Read again").click()
    expect(page.locator(".log-notes")).to_contain_text("10000 lines read")
    # Only the rows in view (plus a margin) are in the page.
    assert lines(page).count() < 200
    page.get_by_role("region", name="Log lines").evaluate(
        "box => { box.scrollTop = box.scrollHeight; }"
    )
    expect(lines(page).last).to_contain_text("request 10000 handled")
    assert lines(page).count() < 200


def test_level_filter_text_filter_and_search(page, console):
    open_service(page, console)
    page.get_by_label("Lines").select_option("1000")
    page.get_by_role("button", name="Read again").click()
    expect(page.locator(".log-notes")).to_contain_text("1000 lines read")
    page.get_by_label("Level").select_option("error")
    expect(lines(page)).to_have_count(10)
    for text in lines(page).all_inner_texts():
        assert "ERROR" in text, text
    page.get_by_label("Level").select_option("warning")
    expect(lines(page)).to_have_count(20)
    page.get_by_label("Level").select_option("")
    page.get_by_label("Filter").fill("request 420 ")
    expect(lines(page)).to_have_count(1)
    page.get_by_label("Filter").fill("")
    search = page.get_by_label("Search")
    search.fill("request 99")
    status = page.locator(".search-step [role='status']")
    expect(status).to_have_text("11 matches")  # 99 and 990–999
    page.get_by_role("button", name="Next match").click()
    expect(status).to_have_text("1 of 11")
    current = page.locator(".logline.current")
    expect(current).to_contain_text("request 99 handled")
    expect(current.locator("mark")).to_have_text("request 99")
    search.press("Enter")
    expect(status).to_have_text("2 of 11")
    expect(page.locator(".logline.current")).to_contain_text("request 990 handled")
    expect(page.locator(".logline.current")).to_be_in_viewport()
    search.press("Shift+Enter")
    expect(status).to_have_text("1 of 11")
    page.get_by_label("Wrap lines").check()
    expect(page.locator(".logview.wrap")).to_be_visible()
    expect(lines(page)).to_have_count(1000)


def test_copy_copies_the_shown_lines_escaped(page, console, context):
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    console.scenario(
        logs={
            "litellm": {
                "lines": ["INFO: one", "ERROR: \u001b[31mred\u001b[0m two"],
                "levels": ["info", "error"],
                "format": "python",
            }
        }
    )
    open_service(page, console)
    page.get_by_label("Level").select_option("error")
    page.get_by_role("button", name="Copy the 1 shown lines").click()
    assert page.evaluate("navigator.clipboard.readText()") == "ERROR: \\x1b[31mred\\x1b[0m two"
    # Escapes are shown apart from the text; a search match across one is still marked.
    expect(lines(page).first.locator(".esc").first).to_have_text("\\x1b")
    page.get_by_label("Search").fill("d\\x1b")
    expect(lines(page).first.locator("mark")).to_have_count(2)


def test_an_unclassified_log_has_no_level_filter(page, console):
    console.scenario(
        services={"ops": {"state": "running", "health": None, "host_ports": {}}},
        logs={"ops": {"lines": ["hello"], "levels": [None], "format": None}},
    )
    open_service(page, console, "ops")
    level = page.get_by_label("Level")
    expect(level).to_be_disabled()
    expect(
        page.get_by_text("This log's format is not known, so its lines have no level.").first
    ).to_be_visible()


def test_a_bad_since_is_refused_with_its_reason(page, console):
    open_service(page, console)
    page.get_by_label("Since (optional)").fill("yesterday")
    expect(page.get_by_role("button", name="Read again")).to_be_disabled()
    expect(
        page.get_by_text("Since must look like 10m, 2h or 2026-11-11T09:00:00Z.")
    ).to_be_visible()
    page.get_by_label("Since (optional)").fill("2h")
    expect(page.get_by_role("button", name="Read again")).to_be_enabled()


def test_a_deployment_row_opens_its_logs_and_kubernetes_offers_the_previous_container(
    page, console
):
    console.scenario(deployments=ROWS)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/deployments")
    page.get_by_role("link", name="Logs of demo").first.click()
    page.wait_for_url(f"**/#/logs/deployment/{LOCAL}")
    expect(lines(page).first).to_contain_text("request 1 handled")
    expect(page.get_by_label("Previous container (after a restart)")).to_have_count(0)
    # The picker moves to the cluster deployment and keeps focus.
    picker = page.get_by_label("Read the logs of")
    picker.focus()  # select_option alone does not focus it
    picker.select_option(f"deployment:{CLUSTER}")
    page.wait_for_url(f"**/#/logs/deployment/{CLUSTER}")
    expect(picker).to_be_focused()
    page.get_by_label("Previous container (after a restart)").check()
    page.get_by_role("button", name="Read again").click()
    expect(page.locator(".log-notes")).to_contain_text("From the previous container")


def test_log_unavailable_is_an_empty_state_not_an_error(page, console):
    message = "kubectl returned no log for the pod: unable to retrieve container logs"
    console.scenario(
        deployments=ROWS,
        logs={CLUSTER: {"error": {"code": "LOG_UNAVAILABLE", "message": message}}},
    )
    sign_in(page, console)
    page.goto(f"{console.origin}/#/logs/deployment/{CLUSTER}")
    empty = page.locator(".log-empty")
    expect(empty).to_contain_text("Kubernetes no longer has this container's log")
    expect(empty).to_contain_text("LOG_UNAVAILABLE")
    expect(page.get_by_role("alert")).to_have_count(0)


def test_a_pending_pod_says_why_it_has_no_lines(page, console):
    console.scenario(
        deployments=ROWS,
        logs={CLUSTER: {"lines": [], "levels": [], "pod_reason": "ContainerCreating"}},
    )
    sign_in(page, console)
    page.goto(f"{console.origin}/#/logs/deployment/{CLUSTER}")
    expect(page.locator(".log-empty")).to_contain_text(
        "The pod has no output yet (ContainerCreating)."
    )


def test_another_coded_error_is_shown_as_an_error(page, console):
    console.scenario(
        deployments=ROWS,
        logs={
            LOCAL: {
                "error": {"code": "REDACTION_INCOMPLETE", "message": "a secret file is unreadable"}
            }
        },
    )
    sign_in(page, console)
    page.goto(f"{console.origin}/#/logs/deployment/{LOCAL}")
    alert = page.get_by_role("alert")
    expect(alert).to_contain_text("a secret file is unreadable (REDACTION_INCOMPLETE)")
    expect(lines(page)).to_have_count(0)


def test_logs_with_lines_have_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(
        logs={
            "litellm": {
                "lines": ["INFO: a", "WARNING: b", "ERROR: \u001b[31mc"],
                "levels": ["info", "warning", "error"],
                "format": "python",
                "truncated": True,
            }
        }
    )
    open_service(scheme_page, console)
    scheme_page.get_by_label("Search").fill("a")
    scheme_page.get_by_role("button", name="Next match").click()
    found = blocking(scheme_page, "logs")
    scheme_page.get_by_label("Since (optional)").fill("nope")
    found += blocking(scheme_page, "logs, bad since")
    assert found == []


def test_read_again_keeps_focus_and_a_single_match_scrolls_back(page, console):
    open_service(page, console)
    page.get_by_label("Lines").select_option("1000")
    button = page.get_by_role("button", name="Read again")
    button.focus()
    page.keyboard.press("Enter")
    expect(page.locator(".log-notes")).to_contain_text("1000 lines read")
    expect(page.locator("form.log-options button[type='submit']")).to_be_focused()
    page.get_by_label("Search").fill("request 500 handled")
    page.get_by_role("button", name="Next match").click()
    current = page.locator(".logline.current")
    expect(current).to_be_in_viewport()
    box = page.get_by_role("region", name="Log lines")
    box.evaluate("box => { box.scrollTop = 0; box.dispatchEvent(new Event('scroll')); }")
    expect(current).not_to_be_in_viewport()
    page.get_by_role("button", name="Next match").click()
    expect(page.locator(".logline.current")).to_be_in_viewport()
    # A new search drops the chosen match at once.
    page.get_by_label("Search").fill("request 7")
    expect(page.locator(".logline.current")).to_have_count(0)
    expect(page.locator(".search-step [role='status']")).to_contain_text("matches")


BAD_ROUTES = ["/logs/service/Nope", "/logs/deployment/short", "/logs/foo/bar"]


@pytest.mark.parametrize("route", BAD_ROUTES)
def test_an_address_with_no_valid_source_says_so(page, console, route):
    sign_in(page, console)
    page.goto(f"{console.origin}/#{route}")
    alert = page.get_by_role("alert")
    expect(alert).to_contain_text("This address names no service or deployment")
    expect(page.locator(".log-options")).to_have_count(0)


def test_a_pod_reason_from_the_cluster_is_escaped(page, console):
    console.scenario(
        deployments=ROWS,
        logs={CLUSTER: {"lines": [], "levels": [], "pod_reason": "Crash\u001b[31mLoop"}},
    )
    sign_in(page, console)
    page.goto(f"{console.origin}/#/logs/deployment/{CLUSTER}")
    empty = page.locator(".log-empty")
    expect(empty).to_contain_text("Crash\\x1b[31mLoop")
    expect(empty.locator(".esc")).to_have_text("\\x1b")
