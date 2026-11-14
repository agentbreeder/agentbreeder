"""Onboard an agent.yaml alone from Home (#823, M7): the three-tier confirm (preview, the "this
code runs in agents" checkbox, the confirm button) over the real `new agent --stack-for` (the
fake CLI hands that command to the real one), then the project is added and opened."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)

pytestmark = pytest.mark.ui


def agent_only(console, name: str = "hello", evals: bool = True) -> Path:
    """A starter project with its stack removed: an agent.yaml with its own registry."""
    from agentbreeder import newagent

    project = Path(console.state) / "projects" / name
    project.parent.mkdir(mode=0o755, exist_ok=True)
    newagent.create(newagent.plan(name, "gemini", project, today=date(2026, 11, 13)))
    (project / "stack.yaml").unlink()
    shutil.rmtree(project / "stack")
    if not evals:
        data = yaml.safe_load((project / "agent.yaml").read_text())
        del data["evals"]
        (project / "agent.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return project


def fill(page, project: Path, name: str = "hello") -> None:
    page.get_by_label("Path to agent.yaml").fill(str(project / "agent.yaml"))
    page.get_by_label("in agent.yaml").fill(name)


def test_onboarding_code_needs_the_checkbox_then_adds_the_project(page, console):
    project = agent_only(console)
    sign_in(page, console, project=False)
    preview = page.get_by_role("button", name="Preview", exact=True)
    expect(preview).to_be_disabled()
    expect(page.get_by_text("Type the absolute path of the agent.yaml first.")).to_be_visible()
    fill(page, project)
    preview.click()
    # Tier 1: the server's preview; nothing is written yet.
    region = page.get_by_role("region", name="Preview: nothing is written yet")
    expect(region).to_be_visible()
    expect(region).to_contain_text("stack/litellm/config.yaml")
    expect(region).to_contain_text("GEMINI_API_KEY")
    refs = page.get_by_role("table", name="What the agent uses")
    expect(refs.get_by_role("row", name="eval/hello-evals@0.1.0")).to_contain_text(
        "Runs in agents"
    )
    expect(refs.get_by_role("row", name="prompt/hello-prompt@0.1.0")).to_contain_text("Text")
    assert not (project / "stack.yaml").exists()
    # Tier 2: the checkbox; tier 3 stays off with its reason until it is ticked.
    write = page.get_by_role("button", name="Write stack.yaml and add project")
    expect(write).to_be_disabled()
    expect(page.get_by_text("Confirm that this code runs in agents first.")).to_be_visible()
    expect(page.get_by_role("group", name="This code runs in agents")).to_contain_text(
        "eval/hello-evals@0.1.0"
    )
    page.get_by_label("I trust this code to run in agents").check()
    write.click()
    page.wait_for_url("**/#/services")
    scope = page.get_by_role("combobox", name="Project scope")
    expect(scope.locator("option:checked")).to_have_text("hello (dev)")
    assert (project / "stack.yaml").is_file() and (project / "stack/litellm/config.yaml").is_file()


def test_editing_the_form_drops_the_preview(page, console):
    project = agent_only(console)
    sign_in(page, console, project=False)
    fill(page, project)
    page.get_by_role("button", name="Preview", exact=True).click()
    region = page.get_by_role("region", name="Preview: nothing is written yet")
    expect(region).to_be_visible()
    page.get_by_label("Model provider").select_option("openai")
    expect(region).to_have_count(0)


def test_built_in_code_only_needs_no_checkbox(page, console):
    project = agent_only(console, evals=False)
    sign_in(page, console, project=False)
    fill(page, project)
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_role("region", name="Preview: nothing is written yet")).to_be_visible()
    expect(page.get_by_role("group", name="This code runs in agents")).to_have_count(0)
    page.get_by_role("button", name="Write stack.yaml and add project").click()
    page.wait_for_url("**/#/services")
    assert (project / "stack.yaml").is_file()


def test_new_agents_own_refusal_is_shown(page, console):
    project = agent_only(console)
    sign_in(page, console, project=False)
    fill(page, project, name="other")
    page.get_by_role("button", name="Preview", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("NAME must be the agent's name")
    expect(page.get_by_role("region", name="Preview: nothing is written yet")).to_have_count(0)
    assert not (project / "stack.yaml").exists()


def test_a_preview_with_code_has_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    project = agent_only(console)
    sign_in(scheme_page, console, project=False)
    fill(scheme_page, project)
    scheme_page.get_by_role("button", name="Preview", exact=True).click()
    scheme_page.get_by_role("group", name="This code runs in agents").wait_for()
    assert blocking(scheme_page, "Home onboarding preview") == []
