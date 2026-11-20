"""Registry ▸ Runtimes and the runtime detail (#1085 S3, console spec §6.3, §7 and §9): every
runtime version with its framework, protocol, kit and status; a version's manifest facts, the
packages its requirements.in names, its newest conformance report (no report, a report for
another image, a failing report with its group open) and Run conformance (no built image, the
image, as a job), with axe in light and dark.

The list's other frameworks are a scripted `registry runtimes` answer (only `runtime/adk`
exists); the detail is the real `registry show` of the built-in `runtime/adk@1.4.0`, and the
reports are scripted `registry conformance` answers: no conformance run has happened for real.
Code mode (F8) is not on `main`, so there is no code-mode report or guided-path step to test."""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import expect

from .conftest import sign_in
from .test_a11y import blocking, scheme_page  # noqa: F401  (the fixture)
from .test_agents import own_project
from .test_builder_framework import FRAMEWORKS

pytestmark = pytest.mark.ui

ADK = "runtime/adk@1.4.0"
IMAGE = "sha256:" + "c" * 64
OTHER = "sha256:" + "d" * 64


def check(identifier, status, group, title, detail=""):
    return {"id": identifier, "group": group, "title": title, "status": status, "detail": detail}


def report(image=IMAGE, result="fail"):
    return {
        "schema_version": 1,
        "protocol": 1,
        "profile": "v1",
        "runtime": ADK,
        "mode": "declared",
        "agent": "demo",
        "image": image,
        "result": result,
        "counts": {"pass": 1, "fail": 1, "not_run": 1, "not_applicable": 0},
        "started": "2026-11-18T10:00:00Z",
        "finished": "2026-11-18T10:05:00Z",
        "agentbreeder": "3.0.0a3",
        "checks": [
            check("serving.health", "pass", "Serving", "GET /health answers 200"),
            check(
                "sessions.busy",
                "fail",
                "Sessions",
                "A turn on a session that is still running one is 409",
                "answered 200 <b>not</b> 409",
            ),
            check("knowledge.sources", "not_run", "Knowledge", "A source is in the first 3"),
        ],
    }


def built(console, image=IMAGE, runtime=ADK):
    """Agent `demo`'s build record in its project, as `build` writes it."""
    deploy = own_project(console) / "deploy"
    deploy.mkdir(exist_ok=True)
    record = {"name": "demo", "image": image, "runtime": runtime, "refs": [], "aliases": []}
    (deploy / "demo.build.json").write_text(json.dumps(record))


def open_detail(page, console):
    sign_in(page, console)
    page.goto(f"{console.origin}/#/registry/runtimes/adk?version=1.4.0")
    page.get_by_role("heading", name="Conformance").wait_for(timeout=60000)


def conformance(page):
    return page.locator("section.conformance")


def test_the_list_shows_each_version_with_framework_protocol_kit_and_status(page, console):
    own_project(console)
    console.scenario(runtimes=FRAMEWORKS)
    sign_in(page, console)
    page.get_by_role("navigation", name="Console").get_by_role(
        "link", name="Runtimes", exact=True
    ).click()
    page.wait_for_url("**/#/registry/runtimes")
    table = page.get_by_role("table", name="Runtime versions")
    rows = table.locator("tbody tr")
    expect(rows).to_have_count(5)
    adk = rows.filter(has_text=ADK)
    expect(adk.locator(".badge.framework")).to_have_text("Google ADK")
    expect(adk).to_contain_text("1 (v1 profile, before the kit)")
    expect(adk).to_contain_text("Run end to end (Northwind)")
    graph = rows.filter(has_text="runtime/langgraph@1.0.0")
    expect(graph).to_contain_text("1 (kit profile, kit 1.0.0)")
    expect(graph).to_contain_text("Conformance passed; not run end to end")
    expect(rows.filter(has_text="openai-agents")).to_contain_text(
        "Conformance failed; not run end to end"
    )
    expect(page.get_by_text("Cannot be used: runtime/example@0.1.0:")).to_be_visible()
    expect(graph.get_by_role("link")).to_have_attribute(
        "href", "#/registry/runtimes/langgraph?version=1.0.0"
    )


def test_an_incomplete_report_is_not_called_a_failure(page, console):
    own_project(console)
    incomplete = {"passed": False, "result": "incomplete", "digest": IMAGE, "date": "2026-11-18"}
    rows = [dict(FRAMEWORKS[3], conformance=incomplete)]
    console.scenario(runtimes=rows)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/registry/runtimes")
    expect(page.get_by_role("table", name="Runtime versions")).to_contain_text(
        "Conformance incomplete; not run end to end"
    )


def test_the_list_says_when_the_read_failed(page, console):
    own_project(console)
    console.scenario(runtimes={"error": {"code": "REF_CONFLICT", "message": "two roots"}})
    sign_in(page, console)
    page.goto(f"{console.origin}/#/registry/runtimes")
    alert = page.get_by_role("alert").filter(has_text="REF_CONFLICT")
    expect(alert).to_contain_text("two roots")
    expect(alert.get_by_role("button", name="Try again")).to_be_visible()


def test_the_detail_shows_the_manifest_packages_and_no_report(page, console):
    own_project(console)
    open_detail(page, console)
    facts = page.locator("dl.facts").filter(has_text="Reserved tool names")
    expect(facts.locator(".badge.framework")).to_have_text("Google ADK", timeout=60000)
    expect(facts).to_contain_text("Run end to end (Northwind)")
    expect(facts).to_contain_text("transfer_to_agent")
    packages = page.locator("ul.packages")
    expect(packages).to_contain_text("google-adk[db]==2.12.0")
    expect(packages).to_contain_text("filelock>=4.2.1")
    expect(conformance(page)).to_contain_text("Not run for this runtime version.")
    run = page.get_by_role("button", name="Run conformance")
    expect(run).to_be_disabled()
    expect(conformance(page)).to_contain_text(
        "No declared-mode image of this runtime version in this project."
    )


def test_a_failing_report_is_expanded_and_text_stays_text(page, console):
    built(console)
    console.scenario(conformance={ADK: report()})
    open_detail(page, console)
    section = conformance(page)
    expect(section).to_contain_text("Failed")
    expect(section).to_contain_text("1 passed, 1 failed, 1 not run, 0 not applicable")
    sessions = section.locator("details.check-group").filter(has_text="Sessions")
    expect(sessions).to_have_attribute("open", "")
    expect(sessions).to_contain_text("answered 200 <b>not</b> 409")  # text, never markup
    expect(
        section.locator("details.check-group").filter(has_text="Serving")
    ).not_to_have_attribute("open", "")
    expect(section.get_by_text("not the current build")).to_have_count(0)


def test_a_report_for_another_image_is_not_the_current_build(page, console):
    built(console)
    console.scenario(conformance={ADK: report(image=OTHER, result="incomplete")})
    open_detail(page, console)
    expect(conformance(page)).to_contain_text("Incomplete: not a pass")
    expect(conformance(page).get_by_text("not the current build")).to_be_visible()


def test_run_conformance_starts_a_job_on_the_built_image(page, console):
    built(console)
    console.scenario(conformance={}, job_exit={"conformance": 1})
    open_detail(page, console)
    expect(conformance(page)).to_contain_text("Image: demo, recorded")
    page.get_by_role("button", name="Run conformance").click()
    dock = page.get_by_role("complementary", name="Jobs")
    expect(dock).to_contain_text("Conformance run")
    expect(dock).to_contain_text(
        "Conformance ran. The report failed or is incomplete (not a pass)", timeout=20000
    )
    argv = json.loads((console.state / "conformance-argv.json").read_text())
    assert argv[:2] == ["conformance", IMAGE] and argv[2] == "--project"


def test_run_conformance_says_why_it_is_off(page, console):
    """The spec's other disabled reasons (§6.3): a job running in the project, and Docker down."""
    from .test_docker import DOWN

    built(console)
    console.scenario(conformance={}, job_seconds=20)
    open_detail(page, console)
    run = page.get_by_role("button", name="Run conformance")
    expect(run).to_be_enabled()
    run.click()
    expect(run).to_be_disabled()
    expect(conformance(page)).to_contain_text("is running in this project")
    console.scenario(conformance={}, doctor=DOWN)
    page.goto(f"{console.origin}/#/registry/runtimes/adk?version=1.4.0")
    page.get_by_role("region", name="Docker status").wait_for(timeout=20000)
    expect(page.get_by_role("button", name="Run conformance")).to_be_disabled()
    expect(conformance(page)).to_contain_text("Docker is not reachable.")


def test_the_report_is_read_again_when_the_run_ends(page, console):
    built(console)
    console.scenario(conformance={}, job_seconds=3)
    open_detail(page, console)
    expect(conformance(page)).to_contain_text("Not run for this runtime version.")
    # The run writes a report: the fake answers it from now on.
    console.scenario(conformance={ADK: report(result="incomplete")}, job_seconds=3)
    page.get_by_role("button", name="Run conformance").click()
    expect(conformance(page)).to_contain_text("Incomplete: not a pass", timeout=30000)


def test_runtimes_have_no_serious_axe_findings(scheme_page, console):  # noqa: F811
    built(console)
    console.scenario(conformance={ADK: report()})
    sign_in(scheme_page, console)
    found = []
    scheme_page.goto(f"{console.origin}/#/registry/runtimes")
    scheme_page.get_by_role("table", name="Runtime versions").wait_for(timeout=60000)
    scheme_page.wait_for_timeout(400)
    found += blocking(scheme_page, "Registry runtimes")
    scheme_page.goto(f"{console.origin}/#/registry/runtimes/adk?version=1.4.0")
    conformance(scheme_page).get_by_text("Failed").first.wait_for(timeout=60000)
    scheme_page.wait_for_timeout(400)
    found += blocking(scheme_page, "Runtime detail, failing report")
    assert found == []


def test_the_version_in_the_address_opens_first(page, console):
    own_project(console)
    sign_in(page, console)
    page.goto(f"{console.origin}/#/registry/runtimes/adk?version=1.3.0")
    expect(page.get_by_role("heading", name="Version 1.3.0")).to_be_visible(timeout=60000)
    expect(page.locator("tr.current")).to_contain_text(re.compile(r"1\.3\.0"))
