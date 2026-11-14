"""A stack service's detail screen (#822, M6 console half, part 2): its config from the fake
`services status --json`, a Logs link, and Start / Stop / Restart through the fake
`services ACTION DIR NAME --json`. A stop or restart that deployments depend on asks first and
is sent again with `--yes`; a refusal shows the CLI's message."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)

pytestmark = pytest.mark.ui

RUNNING = {
    "postgres": {"state": "running", "health": "healthy", "host_ports": {}},
    "litellm": {"state": "running", "health": "healthy", "host_ports": {"4000": 4000}},
}
STOPPED = {**RUNNING, "litellm": {"state": "exited", "health": "", "host_ports": {"4000": 4000}}}


def calls(console) -> list[list[str]]:
    log = console.state / "service-argv"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def open_service(page, console, name: str = "litellm") -> None:
    sign_in(page, console)
    table = page.get_by_role("table", name="Stack services")
    table.get_by_role("link", name=name, exact=True).click()
    page.wait_for_url(f"**/#/services/{name}")
    page.get_by_role("heading", level=1, name=f"Service {name}").wait_for()


def test_a_row_opens_the_detail_and_restart_acts_on_that_service(page, console):
    console.scenario(services=RUNNING)
    open_service(page, console)
    config = page.get_by_role("region", name="Config")
    expect(config).to_contain_text("running")
    expect(config).to_contain_text("healthy")
    expect(config).to_contain_text("4000 → 127.0.0.1:4000")
    expect(page.get_by_role("link", name="Logs of litellm")).to_have_attribute(
        "href", "#/logs/service/litellm"
    )
    nav = page.get_by_role("navigation", name="Console")
    expect(nav.get_by_role("link", name="Services")).to_have_attribute("aria-current", "page")
    controls = page.get_by_role("region", name="Controls")
    expect(controls.get_by_role("button", name="Start", exact=True)).to_be_disabled()
    expect(controls).to_contain_text("It is already running.")

    controls.get_by_role("button", name="Restart", exact=True).click()
    expect(page.get_by_role("status").filter(has_text="litellm restarted")).to_contain_text(
        "now running (starting). Its health check has not passed yet."
    )
    assert calls(console) == [["services", "restart", str(console.project()), "litellm", "--json"]]


def test_stop_while_deployments_use_the_stack_asks_first(page, console):
    console.scenario(services=RUNNING, service_control={"in_use": ["local/demo"]})
    open_service(page, console)
    controls = page.get_by_role("region", name="Controls")

    controls.get_by_role("button", name="Stop", exact=True).click()
    dialog = page.get_by_role("dialog", name="Stop litellm anyway?")
    expect(dialog).to_contain_text("This deployment uses the stack: local/demo.")
    expect(dialog).to_contain_text("They cannot reach litellm until it is started again.")
    dialog.get_by_role("button", name="Cancel").click()
    expect(page.get_by_role("status").filter(has_text="Nothing was done")).to_contain_text(
        "litellm was not stopped."
    )
    assert len(calls(console)) == 1 and "--yes" not in calls(console)[0]

    controls.get_by_role("button", name="Stop", exact=True).click()
    page.get_by_role("dialog", name="Stop litellm anyway?").get_by_role(
        "button", name="Stop litellm"
    ).click()
    expect(page.get_by_role("status").filter(has_text="litellm stopped")).to_be_visible()
    assert calls(console)[-1][-2:] == ["--json", "--yes"]
    assert len(calls(console)) == 3


def test_focus_comes_back_to_the_controls(page, console):
    console.scenario(services=RUNNING, service_control={"in_use": ["local/demo"]})
    open_service(page, console)
    controls = page.get_by_role("region", name="Controls")
    restart = controls.get_by_role("button", name="Restart", exact=True)
    stop = controls.get_by_role("button", name="Stop", exact=True)

    restart.click()
    page.get_by_role("dialog", name="Restart litellm anyway?").get_by_role(
        "button", name="Cancel"
    ).click()
    expect(page.get_by_role("status").filter(has_text="Nothing was done")).to_be_visible()
    expect(restart).to_be_focused()

    stop.click()
    # The service is stopped by the time the console reads it again: Stop no longer applies,
    # so focus goes to the first button that does (Start).
    console.scenario(services=STOPPED, service_control={"in_use": ["local/demo"]})
    page.get_by_role("dialog", name="Stop litellm anyway?").get_by_role(
        "button", name="Stop litellm"
    ).click()
    expect(page.get_by_role("status").filter(has_text="litellm stopped")).to_be_visible()
    expect(controls.get_by_role("button", name="Start", exact=True)).to_be_focused()


def test_controls_are_off_while_docker_is_down(page, console):
    down = [
        {
            "name": "Docker",
            "passed": False,
            "message": "Cannot reach the engine.",
            "required": True,
            "info": False,
        }
    ]
    console.scenario(services=RUNNING, doctor=down)
    open_service(page, console)
    controls = page.get_by_role("region", name="Controls")
    expect(controls).to_contain_text("Docker is not reachable; start it, then use Retry")
    for name in ("Start", "Stop", "Restart"):
        expect(controls.get_by_role("button", name=name, exact=True)).to_be_disabled()
    console.scenario(services=RUNNING)
    page.get_by_role("button", name="Retry").click()
    expect(controls.get_by_role("button", name="Restart", exact=True)).to_be_enabled()
    assert calls(console) == []


def test_port_busy_shows_the_clis_message(page, console):
    message = (
        "port 4000 is already in use on this machine (start); stop what uses it, then run the "
        "command again"
    )
    console.scenario(services=STOPPED, service_control={"error": f"[PORT_BUSY] {message}"})
    open_service(page, console)
    controls = page.get_by_role("region", name="Controls")
    expect(controls.get_by_role("button", name="Stop", exact=True)).to_be_disabled()
    expect(page.get_by_role("region", name="Config")).to_contain_text("not listening")
    controls.get_by_role("button", name="Start", exact=True).click()
    expect(page.get_by_role("alert")).to_have_text(message + "Dismiss")


def test_buttons_wait_while_an_action_runs(page, console):
    console.scenario(services=RUNNING, service_control={"seconds": 2})
    open_service(page, console)
    controls = page.get_by_role("region", name="Controls")
    controls.get_by_role("button", name="Restart", exact=True).click()
    expect(controls.get_by_role("button", name="Stop", exact=True)).to_be_disabled()
    expect(controls).to_contain_text("Restart is running.")
    expect(page.get_by_role("status").filter(has_text="litellm restarted")).to_be_visible()
    expect(controls.get_by_role("button", name="Stop", exact=True)).to_be_enabled()


def test_an_internal_service_and_an_unknown_one(page, console):
    console.scenario(services=RUNNING)
    open_service(page, console, "postgres")
    expect(page.get_by_role("region", name="Config")).to_contain_text(
        "internal only (reached by agents on the stack network, not from this machine)"
    )
    page.goto(f"{console.origin}/#/services/redis")
    expect(page.get_by_text('This stack has no service "redis".')).to_be_visible()
    page.get_by_role("link", name="Back to Services").click()
    page.get_by_role("heading", level=1, name="Services").wait_for()


def test_the_detail_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(services=RUNNING, service_control={"in_use": ["local/demo"]})
    open_service(scheme_page, console)
    found = blocking(scheme_page, "service detail")
    scheme_page.get_by_role("region", name="Controls").get_by_role(
        "button", name="Stop", exact=True
    ).click()
    scheme_page.get_by_role("dialog", name="Stop litellm anyway?").wait_for()
    found += blocking(scheme_page, "service in-use confirm")
    assert not found, found
