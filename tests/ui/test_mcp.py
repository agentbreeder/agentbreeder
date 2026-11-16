"""The MCP grants screen (#831, M15 console half, part 1) over a copy of the M15 fixture
(`tests/fixtures/mcp_github_project`): servers, pins and grants read from stack.yaml and
stack.lock, relay state and drift from the fake `services status --json`."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)

pytestmark = pytest.mark.ui

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_github_project"
PIN = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
RUNNING = {
    "postgres": {"state": "running", "health": "healthy", "host_ports": {}},
    "litellm": {"state": "running", "health": "healthy", "host_ports": {"4000": 4000}},
    "mcp-github": {"state": "running", "health": "", "host_ports": {}},
}


def gh_reader(console, locked: bool = True) -> Path:
    """A copy of the fixture with the agent granted three tools, two of them pinned."""
    target = console.state / "projects" / "gh-reader"
    if target.exists():
        return target
    shutil.copytree(FIXTURE, target)
    stack = target / "stack.yaml"
    stack.write_text(
        stack.read_text().replace(
            "      candidate: true            # release tests a candidate before production\n",
            "      candidate: true            # release tests a candidate before production\n"
            "      mcp: {github: [get_me, list_issues, issue_read]}\n",
        )
    )
    lock = {
        "lock_version": 1,
        "entries": {},
        "imports": {},
        "mcp": {
            "github": {
                "ref": "mcp/github@1.0.0",
                "origin": "built-in",
                "upstream": "https://api.githubcopilot.com:443",
                "token_url": None,
                "tools": {"get_me": PIN, "list_issues": OTHER},
            }
        },
    }
    if locked:
        (target / "stack.lock").write_text(json.dumps(lock))
    return target


def open_mcp(page, console) -> Path:
    project = gh_reader(console)
    sign_in(page, console, project=False)
    page.get_by_label("Absolute path").fill(str(project))
    page.get_by_role("button", name="Add project").click()
    page.wait_for_url("**/#/services")
    page.get_by_role("navigation", name="Console").get_by_role("link", name="MCP grants").click()
    page.get_by_role("heading", name="mcp.github").wait_for()
    return project


def test_servers_pins_and_grants(page, console):
    console.scenario(
        services=RUNNING,
        mcp_status={
            "github": {
                "state": "ok",
                "pinned": 2,
                "changed": [],
                "missing": [],
                "unpinned": ["search_repositories"],
            }
        },
    )
    open_mcp(page, console)
    server = page.get_by_role("region", name="mcp.github", exact=True)
    expect(server).to_contain_text("mcp/github@1.0.0")
    expect(server).to_contain_text("built-in")
    expect(server).to_contain_text("https://api.githubcopilot.com:443")
    expect(server).to_contain_text("Pins match upstream")
    pins = page.get_by_role("table", name="Pinned tools of mcp.github")
    expect(pins.get_by_role("row").filter(has_text="get_me")).to_contain_text("a" * 16)
    expect(pins.get_by_role("row").filter(has_text="list_issues")).to_contain_text("Pinned")
    expect(server).to_contain_text(
        "not pinned (the relay does not serve them): search_repositories"
    )
    grants = page.get_by_role("table", name="Grants per agent")
    row = grants.get_by_role("row").filter(has_text="gh-reader")
    expect(row).to_contain_text("get_me, list_issues, issue_read")
    expect(row).to_contain_text("(not pinned yet)")
    expect(page.get_by_role("region", name="Grants per agent", exact=True)).to_contain_text(
        "takes effect at the next services up"
    )
    expect(page.get_by_text("services up").first).to_be_visible()


def test_drift_names_the_terminal_repin(page, console):
    console.scenario(
        services=RUNNING,
        mcp_status={
            "github": {
                "state": "drift",
                "pinned": 2,
                "changed": ["get_me"],
                "missing": ["list_issues"],
                "unpinned": [],
            }
        },
    )
    project = open_mcp(page, console)
    server = page.get_by_role("region", name="mcp.github", exact=True)
    expect(server).to_contain_text("Upstream changed")
    expect(server).to_contain_text(f"agentbreeder services up {project} --environment dev --repin")
    pins = page.get_by_role("table", name="Pinned tools of mcp.github")
    expect(pins.get_by_role("row").filter(has_text="get_me")).to_contain_text("hidden until")
    expect(pins.get_by_role("row").filter(has_text="list_issues")).to_contain_text(
        "Missing upstream"
    )


def test_before_the_first_services_up(page, console):
    gh_reader(console, locked=False)
    console.scenario(mcp_status={"github": {"state": "absent"}})
    open_mcp(page, console)
    server = page.get_by_role("region", name="mcp.github", exact=True)
    expect(server).to_contain_text("stack.lock has no pins for this version yet")
    expect(server).to_contain_text("Relay not running")
    expect(server).to_contain_text("No tools pinned.")


def test_mcp_screen_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(
        services=RUNNING,
        mcp_status={
            "github": {"state": "drift", "changed": ["get_me"], "missing": [], "unpinned": []}
        },
    )
    open_mcp(scheme_page, console)
    scheme_page.wait_for_timeout(300)
    assert blocking(scheme_page, "mcp") == []


PROBE_LINE = (
    "[MCP_PROBE_FAILED] the github relay couldn't list upstream tools (UPSTREAM_AUTH); "
    "see `agentbreeder services logs mcp-github`"
)


def test_a_failed_upstream_check_names_its_cause_and_the_relay_log(page, console):
    """#1042: the server card says why the upstream check failed and where to read more."""
    unverified = {"state": "unverified", "error": "MCP_PROBE_FAILED", "cause": "UPSTREAM_AUTH"}
    console.scenario(services=RUNNING, mcp_status={"github": unverified})
    project = open_mcp(page, console)
    card = page.get_by_role("region", name="mcp.github", exact=True)
    expect(card).to_contain_text("MCP_PROBE_FAILED (cause UPSTREAM_AUTH)")
    expect(card).to_contain_text("refused the credential")
    expect(card).to_contain_text(f"agentbreeder services logs {project} mcp-github")


def test_a_start_that_fails_at_the_probe_says_why_in_the_job_row(page, console):
    """#1042: the job row names the code, its cause and the CLI's next step, not only
    "failed (exit 2). See its log"."""
    console.scenario(
        plan_mcp=[BUILT_IN],
        keys=KEYS,
        job_lines={"services-up": ["Starting postgres", PROBE_LINE]},
        job_exit={"services-up": 2},
    )
    sign_in(page, console)
    page.get_by_role("button", name="Review and update").click()
    page.get_by_role("button", name="Confirm and start").click()
    dock = page.get_by_role("complementary", name="Jobs")
    expect(dock).to_contain_text(
        "Start services failed (exit 2): MCP_PROBE_FAILED (cause UPSTREAM_AUTH)"
    )
    expect(dock).to_contain_text("see `agentbreeder services logs mcp-github`")
    expect(dock).to_contain_text("refused the credential")
    expect(dock).not_to_contain_text("See its log")


# -- grant and revoke (#831 part 2): the real `mcp grant|revoke` on the copy's stack.yaml ---------


def change_card(page):
    return page.get_by_role("region", name="Change a grant", exact=True)


def review(page, verb: str, *tools: str):
    card = change_card(page)
    card.get_by_role("radio", name=f"{verb.capitalize()} tools").check()
    for tool in tools:
        card.get_by_role("checkbox", name=re.compile(rf"^{tool}\b")).check()
    card.get_by_role("button", name="Review change").click()
    return card


def test_grant_is_reviewed_then_written(page, console):
    console.scenario(services=RUNNING)
    project = open_mcp(page, console)
    card = review(page, "grant", "search_repositories")
    diff = card.get_by_label("stack.yaml change")
    expect(diff).to_contain_text("search_repositories")
    expect(card).to_contain_text("takes effect at the next services up")
    assert "search_repositories" not in (project / "stack.yaml").read_text()
    card.get_by_role("button", name="Write grant").click()
    expect(card).to_contain_text("Granted search_repositories for gh-reader on mcp.github.")
    expect(card).to_contain_text(f"agentbreeder services up {project} --environment dev")
    assert "search_repositories" in (project / "stack.yaml").read_text()
    grants = page.get_by_role("table", name="Grants per agent")
    expect(grants).to_contain_text("search_repositories")


def test_a_stack_changed_after_review_is_reviewed_again(page, console):
    console.scenario(services=RUNNING)
    project = open_mcp(page, console)
    card = review(page, "revoke", "issue_read")
    expect(card.get_by_label("stack.yaml change")).to_contain_text("issue_read")
    stack = project / "stack.yaml"
    stack.write_text(stack.read_text() + "# edited meanwhile\n")
    card.get_by_role("button", name="Write revoke").click()
    expect(card).to_contain_text("stack.yaml changed after your review")
    expect(card.get_by_role("button", name="Write revoke")).to_have_count(0)
    assert "issue_read" in stack.read_text()


IN_USE = {
    "schema_version": 1,
    "file": "/x/stack.yaml",
    "verb": "revoke",
    "agent": "gh-reader",
    "server": "github",
    "ref": "mcp/github@1.0.0",
    "removed": ["get_me"],
    "tools": [{"name": "list_issues", "effect": "read"}, {"name": "issue_read", "effect": "read"}],
    "changed": True,
    "base_sha256": "sha256:" + "a" * 64,
    "sha256": "sha256:" + "b" * 64,
    "diff": "--- stack.yaml\n+++ stack.yaml\n-      mcp: {github: [get_me]}\n",
    "dry_run": True,
    "in_use": [{"deployment": "d" * 32, "environment": "dev", "tools": ["get_me"]}],
}


def test_a_revoke_held_by_a_deployment_names_the_next_steps(page, console):
    console.scenario(services=RUNNING, mcp_grant=IN_USE)
    project = open_mcp(page, console)
    card = review(page, "revoke", "get_me")
    warning = card.locator(".notice.warn")
    expect(warning).to_contain_text(f"Deployment {'d' * 32} (dev) still holds get_me")
    expect(warning).to_contain_text("The next services up takes it away")
    expect(warning).not_to_contain_text("MCP_GRANT_IN_USE")
    expect(warning).not_to_contain_text(
        f"agentbreeder remove {'d' * 32} --local --stack {project}"
    )


def test_the_grant_review_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(services=RUNNING, mcp_grant=IN_USE)
    open_mcp(scheme_page, console)
    card = review(scheme_page, "revoke", "get_me")
    card.get_by_role("button", name="Write revoke").wait_for()
    assert blocking(scheme_page, "mcp grant review") == []


def test_a_write_whose_answer_fails_its_check_reads_the_grants_again(page, console):
    console.scenario(services=RUNNING, mcp_grant_write={"schema_version": 1, "verb": "x"})
    project = open_mcp(page, console)
    card = review(page, "grant", "search_repositories")
    card.get_by_role("button", name="Write grant").click()
    expect(card).to_contain_text("The write may have happened")
    expect(card.get_by_role("button", name="Write grant")).to_have_count(0)
    assert "search_repositories" in (project / "stack.yaml").read_text()
    grants = page.get_by_role("table", name="Grants per agent")
    expect(grants).to_contain_text("search_repositories")  # read again


def test_a_new_choice_clears_the_last_result(page, console):
    console.scenario(services=RUNNING)
    open_mcp(page, console)
    card = review(page, "grant", "search_repositories")
    card.get_by_role("button", name="Write grant").click()
    expect(card).to_contain_text("Granted search_repositories")
    card.get_by_role("radio", name="Revoke tools").check()
    expect(card).not_to_contain_text("Granted search_repositories")


def test_a_revoke_waits_for_the_declared_tools_before_saying_undeclared(page, console):
    """#1043: while the server's declared tools are still loading, a granted tool is "checking",
    never "no longer declared"; once they arrive, a declared tool shows its effect."""
    console.scenario(services=RUNNING)
    held = []
    page.route(re.compile(r".*/mcp/tools\?ref=.*"), lambda route: held.append(route))
    project = gh_reader(console)
    sign_in(page, console, project=False)
    page.get_by_label("Absolute path").fill(str(project))
    page.get_by_role("button", name="Add project").click()
    page.wait_for_url("**/#/services")
    page.get_by_role("navigation", name="Console").get_by_role("link", name="MCP grants").click()
    card = change_card(page)
    card.get_by_role("radio", name="Revoke tools").check()
    get_me = card.get_by_role("checkbox", name=re.compile(r"^get_me\b"))
    expect(get_me).to_be_visible()
    tools = card.locator("fieldset", has=page.get_by_text("Tools", exact=True))
    expect(tools).to_have_attribute("aria-busy", "true")
    expect(card).to_contain_text("checking the declared tools")
    expect(card).not_to_contain_text("no longer declared")
    assert held, "the declared-tools read was not held"
    for route in held:
        route.continue_()
    expect(tools).to_have_attribute("aria-busy", "false")
    expect(card).not_to_contain_text("checking the declared tools")
    expect(card).not_to_contain_text("no longer declared")
    expect(card.locator("label.check", has_text="get_me")).to_contain_text("read")


def test_a_revoke_whose_declared_tools_fail_to_read_says_unavailable(page, console):
    """#1043: a failed read of the declared tools is "declared tools unavailable", never
    "no longer declared", and never "checking" forever."""
    console.scenario(services=RUNNING)
    page.route(
        re.compile(r".*/mcp/tools\?ref=.*"),
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"data": None, "error": {"code": "BOOM", "message": "Down"}}),
        ),
    )
    open_mcp(page, console)
    card = change_card(page)
    card.get_by_role("radio", name="Revoke tools").check()
    expect(card.locator("label.check", has_text="get_me")).to_contain_text(
        "declared tools unavailable"
    )
    expect(card).not_to_contain_text("no longer declared")
    expect(card).not_to_contain_text("checking the declared tools")
    card.get_by_role("radio", name="Grant tools").check()
    expect(card).to_contain_text("The declared tools are unavailable")


def test_the_grant_form_choices_line_up(scheme_page, console):  # noqa: F811
    """#1043: each radio and checkbox sits at the start of its fieldset, with its text right next
    to it (the text-input width made them drift), in light and dark."""
    console.scenario(services=RUNNING)
    open_mcp(scheme_page, console)
    card = change_card(scheme_page)
    card.get_by_role("radio", name="Revoke tools").check()
    card.get_by_role("checkbox", name=re.compile(r"^get_me\b")).wait_for()
    boxes = card.locator("fieldset.choices").evaluate_all(
        """sets => sets.map(set => ({
          legend: set.querySelector("legend").getBoundingClientRect().left,
          inputs: [...set.querySelectorAll("label > input")].map(input => {
            const box = input.getBoundingClientRect();
            const label = input.parentElement.getBoundingClientRect();
            return {left: box.left, width: box.width, label: label.left};
          }),
        }))"""
    )
    assert len(boxes) == 2
    lefts = set()
    for fieldset in boxes:
        assert fieldset["inputs"], fieldset
        for item in fieldset["inputs"]:
            assert item["width"] < 40, item  # a native control, not a 440px text input
            assert abs(item["left"] - item["label"]) < 8, item
            lefts.add(round(item["left"]))
        assert all(abs(i["left"] - fieldset["legend"]) < 16 for i in fieldset["inputs"]), fieldset
    assert max(lefts) - min(lefts) <= 1, lefts  # both fieldsets start their controls alike


# -- services up for MCP stacks (#831 part 3): the services review with consent ------------------

BUILT_IN = {
    "server": "github",
    "ref": "mcp/github@1.0.0",
    "origin": "built-in",
    "upstream": "https://api.githubcopilot.com:443",
    "auth": "bearer",
    "credentials": [
        {"variable": "GITHUB_MCP_TOKEN", "destination": "https://api.githubcopilot.com:443"}
    ],
    "egress": "yes",
    "agents": {},
}
PROJECT_MCP = BUILT_IN | {
    "server": "tracker",
    "ref": "mcp/tracker@0.1.0",
    "origin": "project",
    "upstream": "https://tracker.example:443",
    "credentials": [{"variable": "TRACKER_TOKEN", "destination": "https://tracker.example:443"}],
}
KEYS = [{"name": "GITHUB_MCP_TOKEN", "optional": False, "stored": True}]


def started_argv(page) -> list[str]:
    dock = page.get_by_role("complementary", name="Jobs")
    expect(dock).to_contain_text("Start services finished.", timeout=15000)
    dock.get_by_role("button", name="Show log").click()
    line = page.get_by_role("log").last.locator(".line").filter(has_text="argv ").first
    return json.loads(line.inner_text()[5:])


def test_a_built_in_mcp_stack_starts_from_the_review(page, console):
    console.scenario(plan_mcp=[BUILT_IN], keys=KEYS)
    sign_in(page, console)
    page.get_by_role("button", name="Review and update").click()
    review = page.get_by_role("group", name="MCP servers")
    expect(review).to_contain_text(
        "Credential GITHUB_MCP_TOKEN will be sent to https://api.githubcopilot.com:443"
    )
    expect(review).to_contain_text("trust on first use")
    expect(review.get_by_role("checkbox")).to_have_count(0)
    page.get_by_role("button", name="Confirm and start").click()
    argv = started_argv(page)
    assert "--allow-project-mcp" not in argv and "--repin" not in argv


def test_a_project_mcp_server_starts_only_with_its_consent(page, console):
    console.scenario(plan_mcp=[BUILT_IN, PROJECT_MCP], keys=KEYS)
    sign_in(page, console)
    page.get_by_role("button", name="Review and update").click()
    start = page.get_by_role("button", name="Confirm and start")
    expect(start).to_be_disabled()
    consent = page.get_by_role("checkbox", name=re.compile(r"^Allow mcp\.tracker, a project"))
    expect(consent).to_have_accessible_name(
        re.compile(r"send TRACKER_TOKEN to https://tracker\.example:443")
    )
    consent.check()
    expect(start).to_be_enabled()
    start.click()
    argv = started_argv(page)
    assert argv[argv.index("--allow-project-mcp") + 1] == "tracker"
    assert argv.count("--allow-project-mcp") == 1


def test_drift_in_the_review_names_the_terminal_repin(page, console):
    console.scenario(
        plan_mcp=[BUILT_IN],
        keys=KEYS,
        mcp_status={"github": {"state": "drift", "changed": ["get_me"], "missing": []}},
    )
    sign_in(page, console)
    page.get_by_role("button", name="Review and update").click()
    review = page.get_by_role("group", name="MCP servers")
    expect(review).to_contain_text("Pinned tools changed upstream on mcp.github")
    expect(review).to_contain_text("--repin")


def test_the_mcp_services_review_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(
        plan_mcp=[BUILT_IN, PROJECT_MCP],
        keys=KEYS,
        mcp_status={"github": {"state": "drift", "changed": ["get_me"], "missing": []}},
    )
    sign_in(scheme_page, console)
    scheme_page.get_by_role("button", name="Review and update").click()
    scheme_page.get_by_role("group", name="MCP servers").wait_for()
    assert blocking(scheme_page, "services review with MCP") == []


# -- repin from the console (#964): the review read, checked and rendered by the server -----------

DRIFT = {"github": {"state": "drift", "changed": ["get_me"], "missing": []}}
OLD_DEF = {"name": "get_me", "description": "Who am I", "inputSchema": {"type": "object"}}
NEW_DEF = {**OLD_DEF, "description": "Who am I \u202e now", "limit": 1.0}


def pins_answer() -> dict:
    """A `services pins --json` answer as the CLI builds it (`mcp_stack.pins_review`)."""
    from types import SimpleNamespace

    from agentbreeder.services import mcp_stack

    entry = SimpleNamespace(
        identity=lambda: {"ref": "mcp/github@1.0.0", "origin": "built-in", "upstream": "u"},
        origin="built-in",
    )
    lock = SimpleNamespace(mcp={"github": {"tools": {"get_me": mcp_stack.tool_hash(OLD_DEF)}}})
    live = {"sha256": mcp_stack.tool_hash(NEW_DEF), "definition": NEW_DEF}
    previous = {"get_me": {"sha256": mcp_stack.tool_hash(OLD_DEF), "definition": OLD_DEF}}
    review = mcp_stack.pins_review(
        "stack-1",
        {"github": entry},
        lock,
        {"github": {"tools": {"get_me": live}, "missing": []}},
        {"github": previous},
    )
    return {
        "schema_version": 1,
        "stack": "stack-1",
        "digest": mcp_stack.pins_review_digest(review.document),
        "review": review.document,
        "servers": review.details,
    }


def open_repin(page, console, **scenario):
    console.scenario(
        plan_mcp=[BUILT_IN], keys=KEYS, mcp_status=DRIFT, services=RUNNING, **scenario
    )
    project = gh_reader(console)  # a valid stack with one `mcp:` server
    sign_in(page, console, project=False)
    page.get_by_label("Absolute path").fill(str(project))
    page.get_by_role("button", name="Add project").click()
    page.wait_for_url("**/#/services")
    page.get_by_role("button", name="Review and update").click()
    page.get_by_role("button", name="Review repin").click()


def test_a_reviewed_repin_starts_with_the_digest(page, console):
    answer = pins_answer()
    open_repin(page, console, pins=answer)
    review = page.get_by_role("group", name="MCP servers")
    expect(review).to_contain_text("changed get_me")
    definition = review.get_by_label("Definition of get_me")
    expect(definition).to_contain_text("\\u202e")  # bidi control shown escaped, never applied
    expect(definition).to_contain_text("1.0")  # the server's text, not re-serialized
    expect(review).to_contain_text(answer["digest"])
    tick = review.get_by_role("checkbox", name=re.compile("I reviewed these tool definitions"))
    expect(page.get_by_role("button", name="Confirm and start")).to_be_enabled()
    tick.check()
    page.get_by_role("button", name="Confirm, repin and start").click()
    argv = started_argv(page)
    at = argv.index("--repin")
    assert argv[at : at + 3] == ["--repin", "--expect-pins", answer["digest"]]


def test_an_unticked_review_starts_without_a_repin(page, console):
    open_repin(page, console, pins=pins_answer())
    page.get_by_role("group", name="MCP servers").get_by_text("changed get_me").wait_for()
    page.get_by_role("button", name="Confirm and start").click()
    assert "--repin" not in started_argv(page)


def test_a_review_read_problem_is_said_plainly(page, console):
    error = {"code": "MCP_PINS_NEED_UP", "message": "x"}
    open_repin(page, console, pins={"error": error})
    expect(page.get_by_role("alert")).to_contain_text("Start services first, then review")
    expect(page.get_by_role("checkbox", name=re.compile("I reviewed"))).to_have_count(0)


def test_an_answer_that_fails_its_check_is_never_shown(page, console):
    answer = pins_answer()
    answer["servers"]["github"]["changed"]["get_me"]["definition"]["limit"] = 2
    open_repin(page, console, pins=answer)
    expect(page.get_by_role("alert")).to_contain_text("does not match its contract")
    expect(page.get_by_text("changed get_me")).to_have_count(0)


def test_start_waits_for_the_review_read(page, console):
    open_repin(page, console, pins={**pins_answer(), "seconds": 2})
    expect(page.get_by_role("button", name="Confirm and start")).to_be_disabled()
    expect(page.get_by_role("button", name="Reading the repin review…")).to_be_disabled()
    expect(page.get_by_role("button", name="Confirm and start")).to_be_enabled(timeout=10000)


def test_the_repin_review_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    open_repin(scheme_page, console, pins=pins_answer())
    scheme_page.get_by_text("changed get_me").wait_for()
    assert blocking(scheme_page, "repin review") == []
