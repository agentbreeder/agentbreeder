"""Build a new agent, the Framework group (#1085 S1, console spec §4 and §9): the list from
`GET /api/builder/parts` → `runtimes` (`registry runtimes --json`), the four status values, the
Version select, a runtime's note, an unusable runtime as text, keyboard selection, picks a switch
makes invalid (each removable, one announcement, cleared by switching back), the preview's
`runtime:` line, Create off for what has not been run end to end, the code confirm for a runtime
from outside the built-ins, reflow at 320 px and 200% zoom, and axe in light and dark.

Only `runtime/adk` exists, so the tests that need another framework give the fake CLI a scripted
`registry runtimes` answer; nothing is previewed or created on a runtime that does not exist. The
other tests hand both `registry runtimes` and `new agent --from-spec` to the real commands."""

from __future__ import annotations

import re
import shutil

import pytest
from playwright.sync_api import expect

from agentbreeder import registry

from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_builder import fill, home, open_builder, patient_previews  # noqa: F401

pytestmark = pytest.mark.ui

ADK = "runtime/adk@1.4.0"
PATH_RUNTIME = "runtime/adk@9.9.0"
GENERAL = "The console creates only combinations that have been run end to end."
DIGEST = "sha256:" + "a" * 64


def row(ref: str, display_name: str, **changes) -> dict:
    """One usable row of `registry runtimes --json`."""
    name, _, version = ref.removeprefix("runtime/").partition("@")
    return {
        "ref": ref,
        "name": name,
        "version": version,
        "root_kind": "builtin",
        "display_name": display_name,
        "framework": name,
        "protocol": 1,
        "profile": "v1",
        "capabilities": ["knowledge", "mcp", "notes"],
        "limits": {"knowledge": 1, "mcp_servers": 10, "tools": 50},
        "reserved_tool_names": [],
        "verified": False,
        "usable": True,
    } | changes


# Frameworks that have no runtime yet, as the CLI would list them once they land.
LANGGRAPH = row(
    "runtime/langgraph@1.0.0",
    "LangGraph",
    profile="kit",
    kit="1.0.0",
    capabilities=["knowledge"],
    limits={"knowledge": 1, "mcp_servers": 0, "tools": 1},
    reserved_tool_names=["echo_clock"],
    conformance={"passed": True, "digest": DIGEST, "date": "2026-11-18"},
    notes="Needs an Anthropic-shaped gateway route.",
)
FRAMEWORKS = [
    row(ADK, "Google ADK", verified=True, reserved_tool_names=["transfer_to_agent"]),
    LANGGRAPH,
    row("runtime/langgraph@0.9.0", "LangGraph", capabilities=["knowledge"]),
    row(
        "runtime/openai-agents@1.0.0",
        "OpenAI Agents SDK",
        conformance={"passed": False, "digest": DIGEST, "date": "2026-11-18"},
    ),
    row("runtime/crewai@1.0.0", "CrewAI"),
    {
        "ref": "runtime/example@0.1.0",
        "name": "example",
        "version": "0.1.0",
        "root_kind": "path",
        "verified": False,
        "usable": False,
        "code": "PROTOCOL_UNSUPPORTED",
        "reason": "protocol 3 is not supported by this AgentBreeder",
    },
]


@pytest.fixture
def console_env(tmp_path) -> dict:
    """An AGENTBREEDER_REGISTRY_PATH root with three tools (`clock` defines `echo_clock`,
    `notes` `echo_notes` and `weather` `echo_weather`) and `runtime/adk@9.9.0`, the built-in
    1.4.0's files under another version: a runtime from outside the built-in registry."""
    root = tmp_path / "path-registry"
    for name in ("clock", "notes", "weather"):
        registry.publish(registry.scaffold("tool", name, tmp_path / f"src-{name}"), root)
    source = tmp_path / "adk-src"
    shutil.copytree(registry.BUILTIN_ROOT / "runtime" / "adk" / "1.4.0", source)
    manifest = source / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("version: 1.4.0", "version: 9.9.0", 1))
    registry.publish(source, root)
    return {registry.PATH_ENV: str(root)}


def group(page):
    return page.get_by_role("group", name="Framework")


def preview(page):
    page.get_by_role("button", name="Preview", exact=True).click()
    pane = page.get_by_role("complementary", name="Preview: nothing is written yet")
    # Two CLI children (`registry runtimes`, then the dry run): slow on a loaded machine.
    expect(pane).to_be_visible(timeout=60000)
    return pane


def test_the_framework_list_and_the_four_status_values(page, console):
    console.scenario(runtimes=FRAMEWORKS)
    open_builder(page, console)
    radios = group(page).get_by_role("radio")
    # Run end to end first, then by name; the label is the framework's own name alone.
    expect(radios).to_have_count(4)
    for index, (name, about) in enumerate(
        [
            ("Google ADK", "runtime/adk@1.4.0 · Run end to end (Northwind)"),
            ("CrewAI", "runtime/crewai@1.0.0 · Not run end to end"),
            ("LangGraph", "runtime/langgraph@1.0.0 · Conformance passed; not run end to end"),
            (
                "OpenAI Agents SDK",
                "runtime/openai-agents@1.0.0 · Conformance failed; not run end to end",
            ),
        ]
    ):
        radio = radios.nth(index)
        expect(radio).to_have_accessible_name(name)
        expect(radio).to_have_accessible_description(
            about + (" Needs an Anthropic-shaped gateway route." if name == "LangGraph" else "")
        )
    # The default is Google ADK (C1), and the general line under the group is static.
    expect(group(page).get_by_role("radio", name="Google ADK")).to_be_checked()
    expect(page.get_by_text(GENERAL)).to_be_visible()
    expect(group(page)).to_have_accessible_description(
        GENERAL + " Preview shows the files for any of them, and agentbreeder new agent "
        "--from-spec writes them anyway."
    )
    # No logo and no image in the group (C4): the name is text.
    expect(group(page).locator("img, svg")).to_have_count(0)


def test_a_runtimes_note_is_a_third_line_under_its_radio(page, console):
    console.scenario(runtimes=FRAMEWORKS)
    open_builder(page, console)
    expect(group(page).get_by_text("Needs an Anthropic-shaped gateway route.")).to_be_visible()
    expect(group(page).get_by_role("radio", name="CrewAI")).to_have_accessible_description(
        "runtime/crewai@1.0.0 · Not run end to end"
    )


def test_an_unusable_runtime_is_text_not_a_disabled_radio(page, console):
    console.scenario(runtimes=FRAMEWORKS)
    open_builder(page, console)
    expect(
        page.get_by_text(
            "Cannot be used: runtime/example@0.1.0: protocol 3 is not supported by this "
            "AgentBreeder (PROTOCOL_UNSUPPORTED)"
        )
    ).to_be_visible()
    expect(group(page).get_by_role("radio", name="example")).to_have_count(0)
    expect(group(page).locator("input:disabled")).to_have_count(0)


def test_adk_1_2_0_and_1_3_0_are_usable_google_adk_through_the_legacy_profile(page, console):
    """The real `registry runtimes --json`: no `runtime/adk` version has a `protocol` key, so
    the engine's legacy profile names each one."""
    open_builder(page, console)
    adk = group(page).get_by_role("radio")
    expect(adk).to_have_count(1)  # the only framework with a runtime today
    expect(adk).to_have_accessible_name("Google ADK")
    expect(adk).to_be_checked()
    expect(adk).to_have_accessible_description("runtime/adk@9.9.0 · Not run end to end")
    version = page.get_by_label("Version of Google ADK")
    expect(version.locator("option")).to_have_text(
        ["9.9.0 (AGENTBREEDER_REGISTRY_PATH)", "1.4.0", "1.3.0", "1.2.0", "1.1.0"]
        + ["1.0.6", "1.0.5", "1.0.4"]
    )
    for number, status in (("1.2.0", "Not run end to end"), ("1.3.0", "Run end to end")):
        version.select_option(f"runtime/adk@{number}")
        expect(adk).to_have_accessible_description(
            f"runtime/adk@{number} · {status}" + (" (Northwind)" if number == "1.3.0" else "")
        )
    expect(page.get_by_text("Cannot be used:")).to_have_count(0)


def test_the_version_select_changes_the_status_and_says_so_once(page, console):
    console.scenario(runtimes=FRAMEWORKS)
    open_builder(page, console)
    # One version: static text, no select.
    expect(page.get_by_text("Version of Google ADK: 1.4.0")).to_be_visible()
    expect(page.get_by_role("combobox", name="Version of Google ADK")).to_have_count(0)
    langgraph = group(page).get_by_role("radio", name="LangGraph")
    langgraph.check()
    version = page.get_by_label("Version of LangGraph")
    expect(version).to_have_value("runtime/langgraph@1.0.0")  # each framework starts on its newest
    expect(page.locator("#announce")).to_have_text(
        "LangGraph 1.0.0: Conformance passed; not run end to end"
    )
    version.focus()
    version.select_option("runtime/langgraph@0.9.0")
    expect(langgraph).to_have_accessible_description(
        "runtime/langgraph@0.9.0 · Not run end to end"
    )
    expect(page.locator("#announce")).to_have_text("LangGraph 0.9.0: Not run end to end")
    # The note is the chosen version's own: 0.9.0 has none.
    expect(group(page).get_by_text("Needs an Anthropic-shaped gateway route.")).to_have_count(0)
    expect(version).to_be_focused()


def test_a_framework_is_chosen_with_the_keyboard(page, console):
    console.scenario(runtimes=FRAMEWORKS)
    open_builder(page, console)
    radios = group(page).get_by_role("radio")
    page.get_by_label("Name", exact=True).focus()
    page.keyboard.press("Tab")
    expect(radios.nth(0)).to_be_focused()  # one tab stop: the checked radio
    page.keyboard.press("ArrowDown")
    expect(radios.nth(1)).to_be_checked()
    expect(radios.nth(1)).to_be_focused()
    expect(page.locator("#announce")).to_have_text("CrewAI 1.0.0: Not run end to end")
    page.keyboard.press("ArrowDown")
    expect(group(page).get_by_role("radio", name="LangGraph")).to_be_checked()
    page.keyboard.press("Tab")
    expect(page.get_by_label("Version of LangGraph")).to_be_focused()


def test_a_switch_marks_picks_invalid_and_switching_back_clears_them(page, console):
    """An MCP server on a framework with no MCP client, a tool past the framework's limit and
    a tool with a reserved function name: each stays on the form with its reason and a Remove
    control, one announcement covers them, and Preview says why it is off."""
    console.scenario(runtimes=FRAMEWORKS)
    open_builder(page, console)
    fill(page)
    page.get_by_label("Tool clock").select_option("tool/clock@0.1.0")
    page.get_by_label("Tool notes").select_option("tool/notes@0.1.0")
    page.get_by_label("Tool weather").select_option("tool/weather@0.1.0")
    page.get_by_label("MCP server github").select_option("mcp/github@1.0.0")
    page.get_by_role("group", name="Tools granted to github").get_by_label("get_me").check()
    previewing = page.get_by_role("button", name="Preview", exact=True)
    expect(previewing).to_be_enabled()
    expect(
        page.get_by_role("list", name="Tools that do not work with this framework")
    ).to_have_count(0)

    group(page).get_by_role("radio", name="LangGraph").check()
    tools = page.get_by_role("list", name="Tools that do not work with this framework")
    servers = page.get_by_role("list", name="MCP servers that do not work with this framework")
    expect(tools.get_by_role("listitem")).to_contain_text(
        [
            "tool/clock@0.1.0: LangGraph 1.0.0 reserves the tool name echo_clock.",
            "tool/weather@0.1.0: LangGraph 1.0.0 takes at most 1 tool.",
        ]
    )
    expect(servers.get_by_role("listitem")).to_contain_text(
        ["mcp/github@1.0.0: LangGraph 1.0.0 has no MCP client."]
    )
    # One message, not one per pick.
    expect(page.locator("#announce")).to_have_text(
        "LangGraph 1.0.0: Conformance passed; not run end to end. "
        "3 picks do not work with LangGraph 1.0.0."
    )
    expect(previewing).to_be_disabled()
    expect(previewing).to_have_accessible_description(
        "3 picks do not work with LangGraph 1.0.0. Remove them or choose another framework."
    )
    # Each reason is tied to its Remove control; the MCP picker itself is not offered.
    remove = servers.get_by_role("button", name="Remove github")
    expect(remove).to_have_accessible_description(
        "mcp/github@1.0.0: LangGraph 1.0.0 has no MCP client."
    )
    expect(
        page.get_by_text("LangGraph 1.0.0 has no MCP client, so MCP servers are not")
    ).to_be_visible()
    expect(page.get_by_label("MCP server github")).to_have_count(0)
    # A reserved name cannot be picked: the version is off in the picker, with the reason.
    expect(page.get_by_label("Tool clock")).to_have_accessible_description(
        re.compile(r"^LangGraph 1\.0\.0 reserves the tool name echo_clock\.")
    )

    # Switching back clears the marks: nothing was dropped.
    group(page).get_by_role("radio", name="Google ADK").check()
    expect(tools).to_have_count(0)
    expect(servers).to_have_count(0)
    expect(page.get_by_label("Tool clock")).to_have_value("tool/clock@0.1.0")
    expect(page.get_by_label("MCP server github")).to_have_value("mcp/github@1.0.0")
    expect(
        page.get_by_role("group", name="Tools granted to github").get_by_label("get_me")
    ).to_be_checked()
    expect(previewing).to_be_enabled()
    expect(page.locator("#announce")).to_have_text("Google ADK 1.4.0: Run end to end (Northwind)")

    # Remove takes one pick off, by keyboard; the others keep their marks. Focus is never
    # dropped: it goes to the next marked pick's Remove, else to the section's heading.
    group(page).get_by_role("radio", name="LangGraph").check()
    servers.get_by_role("button", name="Remove github").focus()
    page.keyboard.press("Enter")
    expect(servers).to_have_count(0)
    expect(page.get_by_role("heading", name="MCP servers")).to_be_focused()
    expect(previewing).to_have_accessible_description(
        "2 picks do not work with LangGraph 1.0.0. Remove them or choose another framework."
    )
    tools.get_by_role("button", name="Remove clock").focus()
    page.keyboard.press("Enter")
    # notes is the one tool LangGraph takes; weather is still past the limit. A tool with a
    # reserved name never counted towards it.
    expect(tools.get_by_role("listitem")).to_contain_text(
        ["tool/weather@0.1.0: LangGraph 1.0.0 takes at most 1 tool."]
    )
    expect(tools.get_by_role("button", name="Remove weather")).to_be_focused()
    page.keyboard.press("Enter")
    expect(tools).to_have_count(0)
    expect(page.get_by_role("heading", name="Tools", exact=True)).to_be_focused()
    expect(previewing).to_be_enabled()
    # At the limit, a tool that is not picked cannot be added, and the picker says why.
    expect(page.get_by_label("Tool weather")).to_be_disabled()
    expect(
        page.get_by_text("LangGraph 1.0.0 takes at most 1 tool. Set one to Not used")
    ).to_be_visible()
    expect(page.get_by_label("Tool notes")).to_be_enabled()


def test_the_group_says_it_is_reading_the_registries(page, console):
    console.scenario(runtimes=FRAMEWORKS, runtimes_seconds=3)
    open_builder(page, console, framework=False)
    expect(group(page).get_by_role("status")).to_have_text("Reading the registries…")
    expect(page.get_by_role("button", name="Preview", exact=True)).to_be_disabled()
    expect(group(page).get_by_role("radio", name="Google ADK")).to_be_checked(timeout=60000)
    expect(group(page).get_by_role("status")).to_have_count(0)


def test_a_reserved_tool_name_cannot_be_picked(page, console):
    console.scenario(runtimes=FRAMEWORKS)
    open_builder(page, console)
    group(page).get_by_role("radio", name="LangGraph").check()
    clock = page.get_by_label("Tool clock")
    expect(clock).to_be_disabled()  # every version of it has the reserved name
    expect(clock.locator("option")).to_have_text(
        ["Not used", "0.1.0 (AGENTBREEDER_REGISTRY_PATH) (cannot be picked)"]
    )
    expect(page.get_by_text("LangGraph 1.0.0 reserves the tool name echo_clock.")).to_be_visible()
    expect(page.get_by_label("Tool weather")).to_be_enabled()


def test_a_switch_marks_the_preview_out_of_date_and_the_preview_shows_the_runtime(page, console):
    open_builder(page, console)
    fill(page)
    version = page.get_by_label("Version of Google ADK")
    version.select_option(ADK)
    pane = preview(page)
    expect(pane.get_by_label("agent.yaml, as written")).to_contain_text(f"runtime: {ADK}")
    expect(pane).to_contain_text("on Google ADK (runtime/adk@1.4.0)")
    create = page.get_by_role("button", name="Create agent and add project")
    expect(create).to_be_enabled()
    version.select_option("runtime/adk@1.3.0")
    expect(page.get_by_role("complementary", name="Preview (out of date)")).to_be_visible()
    expect(create).to_be_disabled()
    expect(page.get_by_text("The form changed: preview again.")).to_be_visible()
    pane = preview(page)
    expect(pane.get_by_label("agent.yaml, as written")).to_contain_text(
        "runtime: runtime/adk@1.3.0"
    )
    expect(pane.get_by_role("table", name="Parts the agent uses")).to_contain_text(
        "runtime/adk@1.3.0"
    )


def test_create_is_off_for_what_has_not_been_run_end_to_end(page, console):
    """A runtime version that was not run (1.2.0), one that was run but not with `new agent`
    (1.3.0, Northwind), and the run version with a combination that was not (MCP servers)."""
    open_builder(page, console)
    fill(page)
    version = page.get_by_label("Version of Google ADK")
    create = page.get_by_role("button", name="Create agent and add project")
    for number in ("1.2.0", "1.3.0"):
        version.select_option(f"runtime/adk@{number}")
        pane = preview(page)
        expect(pane).to_contain_text(
            f"Not run end to end yet: a new agent on Google ADK {number}."
        )
        expect(create).to_be_disabled()
        expect(create).to_have_accessible_description(
            f"A new agent on Google ADK {number} has not been run end to end."
        )
    version.select_option(ADK)
    preview(page)
    expect(create).to_be_enabled()
    page.get_by_label("MCP server github").select_option("mcp/github@1.0.0")
    page.get_by_role("group", name="Tools granted to github").get_by_label("get_me").check()
    pane = preview(page)
    expect(pane).to_contain_text("Not run end to end yet: Google ADK 1.4.0 with MCP servers.")
    expect(create).to_be_disabled()
    expect(create).to_have_accessible_description(
        "Google ADK 1.4.0 with MCP servers has not been run end to end."
    )
    assert not (home(console) / "agentbreeder-projects" / "helper").exists()


def test_a_runtime_without_mcp_does_not_offer_mcp_servers(page, console):
    """The real `runtime/adk@1.0.4` has no `mcp` capability: the name and version in the
    sentence come from the runtime, not from the console."""
    open_builder(page, console)
    fill(page)
    page.get_by_label("MCP server github").select_option("mcp/github@1.0.0")
    page.get_by_role("group", name="Tools granted to github").get_by_label("get_me").check()
    page.get_by_label("Version of Google ADK").select_option("runtime/adk@1.0.4")
    servers = page.get_by_role("list", name="MCP servers that do not work with this framework")
    expect(servers.get_by_role("listitem")).to_contain_text(
        ["mcp/github@1.0.0: Google ADK 1.0.4 has no MCP client."]
    )
    previewing = page.get_by_role("button", name="Preview", exact=True)
    expect(previewing).to_have_accessible_description(
        "1 pick does not work with Google ADK 1.0.4. Remove it or choose another framework."
    )
    servers.get_by_role("button", name="Remove github").click()
    expect(previewing).to_be_enabled()


def test_the_code_confirm_lists_a_runtime_from_outside_the_built_ins(page, console):
    open_builder(page, console)
    fill(page)
    # The newest version is the path registry's, so the form starts on it.
    expect(page.get_by_label("Version of Google ADK")).to_have_value(PATH_RUNTIME)
    pane = preview(page)
    parts = pane.get_by_role("table", name="Parts the agent uses")
    expect(parts).to_contain_text(PATH_RUNTIME)
    expect(parts).to_contain_text("Runs in agents")
    confirm = pane.get_by_role("group", name="This code runs in agents")
    expect(confirm).to_contain_text(f"{PATH_RUNTIME} is not shipped with AgentBreeder")
    confirm.get_by_label("I trust this code to run in agents").check()
    # Not run end to end either, so Create stays off even when confirmed.
    create = page.get_by_role("button", name="Create agent and add project")
    expect(create).to_be_disabled()
    expect(create).to_have_accessible_description(
        "A new agent on Google ADK 9.9.0 has not been run end to end."
    )
    # A built-in runtime ships with AgentBreeder: no confirm.
    page.get_by_label("Version of Google ADK").select_option(ADK)
    pane = preview(page)
    expect(pane.get_by_role("group", name="This code runs in agents")).to_have_count(0)
    expect(create).to_be_enabled()


def test_a_failed_runtimes_read_shows_its_code_and_try_again(page, console):
    problem = {"code": "REF_CONFLICT", "message": "runtime/adk@1.4.0 differs between two roots"}
    console.scenario(runtimes={"error": problem})
    open_builder(page, console, framework=False)
    fill(page)
    alert = group(page).get_by_role("alert")
    expect(alert).to_contain_text("runtime/adk@1.4.0 differs between two roots (REF_CONFLICT)")
    expect(group(page).get_by_role("radio")).to_have_count(0)
    previewing = page.get_by_role("button", name="Preview", exact=True)
    expect(previewing).to_be_disabled()
    expect(previewing).to_have_accessible_description("Choose a framework first.")
    console.scenario(runtimes=FRAMEWORKS)
    alert.get_by_role("button", name="Try again").click()
    expect(group(page).get_by_role("radio", name="Google ADK")).to_be_checked()
    expect(group(page).get_by_role("alert")).to_have_count(0)
    expect(previewing).to_be_enabled()


def test_no_usable_runtime_is_an_error_with_the_clis_code(page, console):
    console.scenario(runtimes=[FRAMEWORKS[-1]])
    open_builder(page, console, framework=False)
    fill(page)
    expect(group(page).get_by_role("alert")).to_contain_text("No framework can be used")
    expect(page.get_by_text("(PROTOCOL_UNSUPPORTED)")).to_be_visible()
    expect(page.get_by_role("button", name="Preview", exact=True)).to_have_accessible_description(
        "Choose a framework first."
    )


def test_the_server_refuses_a_runtime_that_is_no_longer_listed(page, console):
    """The list changed between the form's read and Preview: SPEC_UNSUPPORTED, nothing run."""
    console.scenario(runtimes=FRAMEWORKS)
    open_builder(page, console)
    fill(page)
    group(page).get_by_role("radio", name="CrewAI").check()
    console.scenario(runtimes=FRAMEWORKS[:1])
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text(
        "runtime/crewai@1.0.0 is not a runtime version in the registries"
    )
    expect(page.get_by_role("button", name="Create agent and add project")).to_have_count(0)


@pytest.mark.parametrize(
    "width", [320, 640], ids=["320 px reflow", "200% zoom of a 1280 px window"]
)
def test_the_form_reflows_without_sideways_scrolling(browser, console, width):
    """WCAG 1.4.10: 320 CSS px, and 1280 px at 200% zoom (640 CSS px), with every part of the
    group on the page: long refs, a note, an unusable runtime and invalid picks."""
    console.scenario(runtimes=FRAMEWORKS)
    context = browser.new_context(viewport={"width": width, "height": 800})
    page = context.new_page()
    try:
        open_builder(page, console)
        fill(page)
        page.get_by_label("Tool clock").select_option("tool/clock@0.1.0")
        page.get_by_label("MCP server github").select_option("mcp/github@1.0.0")
        group(page).get_by_role("radio", name="LangGraph").check()
        expect(page.get_by_role("button", name="Remove github")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        for radio in group(page).get_by_role("radio").all():
            expect(radio).to_be_visible()
        expect(page.get_by_label("Version of LangGraph")).to_be_visible()
    finally:
        context.close()


def test_the_framework_group_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    console.scenario(runtimes=FRAMEWORKS)
    open_builder(scheme_page, console)
    fill(scheme_page)
    scheme_page.get_by_label("Tool clock").select_option("tool/clock@0.1.0")
    scheme_page.get_by_label("Tool weather").select_option("tool/weather@0.1.0")
    scheme_page.get_by_label("MCP server github").select_option("mcp/github@1.0.0")
    group(scheme_page).get_by_role("radio", name="LangGraph").check()
    expect(scheme_page.get_by_role("button", name="Remove github")).to_be_visible()
    assert blocking(scheme_page, "Agent builder framework group, invalid picks") == []
    group(scheme_page).get_by_role("radio", name="Google ADK").check()
    assert blocking(scheme_page, "Agent builder framework group") == []


def test_the_real_framework_group_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    """The group as it is today: Google ADK alone, with its Version select."""
    open_builder(scheme_page, console)
    fill(scheme_page)
    scheme_page.get_by_label("Version of Google ADK").select_option(ADK)
    preview(scheme_page)
    assert blocking(scheme_page, "Agent builder framework group, real runtimes") == []
