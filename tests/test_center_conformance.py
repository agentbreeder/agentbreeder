# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Registry ▸ Runtimes and conformance in the console (#1085 S3, console spec §6.3 and §8): the
project's runtimes view, a runtime version's newest report (`registry conformance REF --json`)
checked and reduced, the project's built image read from its build record, and the conformance
job, which runs only on that image."""

from __future__ import annotations

import json
import os

import pytest
from test_center import (  # noqa: F401 (fixtures)
    api,
    center,
    cookie,
    fake_cli,
    project,
    project_dir,
    start,
    wait,
)

from agentbreeder.center import conformance, jobs
from agentbreeder.center.files import BadRequest
from agentbreeder.center.server import Center

RUNTIME = "runtime/adk@1.4.0"
IMAGE = "sha256:" + "c" * 64
OTHER = "sha256:" + "d" * 64


def check(identifier="serving.health", status="pass", **changes) -> dict:
    row = {"id": identifier, "group": "Serving", "title": "GET /health answers 200"}
    return row | {"status": status, "detail": ""} | changes


def full_report(**changes) -> dict:
    data = {
        "schema_version": 1,
        "protocol": 1,
        "profile": "v1",
        "runtime": RUNTIME,
        "mode": "declared",
        "agent": "support",
        "image": IMAGE,
        "result": "incomplete",
        "counts": {"pass": 1, "fail": 1, "not_run": 1, "not_applicable": 0},
        "started": "2026-11-18T10:00:00Z",
        "finished": "2026-11-18T10:05:00Z",
        "agentbreeder": "3.0.0a3",
        "checks": [
            check(),
            check("sessions.busy", "fail", group="Sessions", detail="answered 200‮"),
            check("knowledge.sources", "not_run", group="Knowledge", detail="not built"),
        ],
    }
    return data | changes


def answer(found=None, ref=RUNTIME) -> dict:
    return {"schema_version": 1, "ref": ref, "report": found}


# -- the report ---------------------------------------------------------------------------------


def test_a_report_is_checked_and_reduced():
    shown = conformance.report(answer(full_report(extra="dropped")), RUNTIME)
    assert shown["result"] == "incomplete" and shown["image"] == IMAGE
    assert shown["counts"] == {"pass": 1, "fail": 1, "not_run": 1, "not_applicable": 0}
    assert [row["id"] for row in shown["checks"]] == [
        "serving.health",
        "sessions.busy",
        "knowledge.sources",
    ]
    assert shown["checks"][1]["detail"] == "answered 200"  # the bidi control goes
    assert "extra" not in shown and "schema_version" not in shown
    assert conformance.report(answer(), RUNTIME) is None


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"schema_version": 2, "ref": RUNTIME, "report": None},
        answer(ref="runtime/adk@1.3.0"),
        answer(full_report(runtime="runtime/adk@1.3.0")),
        answer(full_report(mode="code")),
        answer(full_report(result="maybe")),
        answer(full_report(image="latest")),
        answer(full_report(finished="yesterday")),
        answer(full_report(started=7)),
        answer(full_report(agent="Bad Name")),
        answer(full_report(profile="Kit!")),
        answer(full_report(protocol=True)),
        answer(full_report(counts={"pass": -1, "fail": 0, "not_run": 0, "not_applicable": 0})),
        answer(full_report(counts=[])),
        answer(full_report(checks=[check()] * 2)),
        answer(full_report(checks=[check(status="maybe")])),
        answer(full_report(checks=[check(identifier="<b>")])),
        answer(full_report(checks=[check(title=7)])),
        answer(full_report(checks=["row"])),
        answer(full_report(checks=[check(identifier=f"g.c{n}") for n in range(101)])),
        answer("report"),
    ],
)
def test_an_unexpected_answer_is_refused(data):
    with pytest.raises(conformance.BadOutput, match="registry conformance"):
        conformance.report(data, RUNTIME)


def test_a_long_text_is_capped_and_an_odd_version_dropped():
    long = check(title="t" * 500, detail="d" * 900)
    shown = conformance.report(answer(full_report(checks=[long], agentbreeder="<b>")), RUNTIME)
    assert len(shown["checks"][0]["title"]) == conformance.MAX_TITLE
    assert len(shown["checks"][0]["detail"]) == conformance.MAX_DETAIL
    assert shown["agentbreeder"] is None


# -- the build record ---------------------------------------------------------------------------


def write_record(directory, agent="support", **changes):
    record = {"name": agent, "image": IMAGE, "runtime": RUNTIME, "refs": []} | changes
    (directory / "deploy").mkdir(exist_ok=True)
    path = directory / "deploy" / f"{agent}.build.json"
    path.write_text(json.dumps(record))
    return path


def test_the_projects_built_image_is_read_from_its_build_record(tmp_path):
    assert conformance.images(str(tmp_path), "support") == []
    write_record(tmp_path)
    [image] = conformance.images(str(tmp_path), "support")
    assert image["agent"] == "support" and image["image"] == IMAGE
    assert image["runtime"] == RUNTIME and image["built"].endswith("Z")
    assert conformance.images(str(tmp_path), None) == []
    assert conformance.images(str(tmp_path), "../x") == []


@pytest.mark.parametrize(
    "changes",
    [{"name": "other"}, {"image": "latest"}, {"runtime": "tool/x@1.0.0"}, {"runtime": None}],
)
def test_a_record_that_is_not_this_agents_build_gives_no_image(tmp_path, changes):
    write_record(tmp_path, **changes)
    assert conformance.images(str(tmp_path), "support") == []


def test_a_linked_or_broken_record_gives_no_image(tmp_path):
    path = write_record(tmp_path)
    real = tmp_path / "real.json"
    os.replace(path, real)
    path.symlink_to(real)
    assert conformance.images(str(tmp_path), "support") == []
    path.unlink()
    path.write_text("{not json")
    assert conformance.images(str(tmp_path), "support") == []
    path.write_text("[]")
    assert conformance.images(str(tmp_path), "support") == []


# -- the routes ---------------------------------------------------------------------------------


@pytest.fixture
def scripted(center, monkeypatch):
    """`registry conformance` and `registry runtimes --project` answered from `answers`; the
    project's agent is `support`. Every other child runs."""
    answers = {"conformance": None, "runtimes": None, "calls": []}
    real = center.runner.read

    def read(argv, *args, **kwargs):
        argv = list(argv)
        if argv[:2] == ["registry", "conformance"]:
            answers["calls"].append((argv, kwargs))
            return answers["conformance"]
        if argv[:2] == ["registry", "runtimes"]:
            answers["calls"].append((argv, kwargs))
            return answers["runtimes"]
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(center.runner, "read", read)
    monkeypatch.setattr(Center, "_project_agent", lambda self, project: "support")
    return answers


def ok(data) -> dict:
    return {"data": data, "error": None, "stale": False}


def test_the_conformance_route_reads_the_report_and_the_built_image(
    center, cookie, project, project_dir, scripted
):
    write_record(project_dir)
    scripted["conformance"] = ok(answer(full_report()))
    path = f"/api/projects/{project['id']}/conformance?ref={RUNTIME}"
    status, data, _ = api(center, "GET", path, None, cookie)
    assert status == 200 and data["error"] is None, data
    assert data["data"]["report"]["result"] == "incomplete"
    assert [row["image"] for row in data["data"]["images"]] == [IMAGE]
    [(argv, kwargs)] = scripted["calls"]
    assert argv == ["registry", "conformance", RUNTIME, "--json"]
    assert kwargs["cache"] is False and kwargs["track"] is True


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ({"data": None, "error": {"code": "REF_INVALID", "message": "no"}}, "REF_INVALID"),
        ({"data": {"schema_version": 1, "ref": "x", "report": None}, "error": None}, "BAD_OUTPUT"),
    ],
)
def test_the_conformance_route_fails_with_the_reads_code(
    center, cookie, project, scripted, result, code
):
    scripted["conformance"] = result | {"stale": False}
    path = f"/api/projects/{project['id']}/conformance?ref={RUNTIME}"
    status, data, _ = api(center, "GET", path, None, cookie)
    assert status == 200 and data["data"] is None and data["error"]["code"] == code, data


@pytest.mark.parametrize("ref", ["tool/x@1.0.0", "runtime/adk", ""])
def test_the_conformance_route_takes_only_a_runtime_version(center, cookie, project, ref):
    path = f"/api/projects/{project['id']}/conformance?ref={ref}"
    status, data, _ = api(center, "GET", path, None, cookie)
    assert status == 400 and data["error"]["code"].startswith("REF_"), data


def test_the_projects_runtimes_read_its_own_roots(center, cookie, project, scripted):
    row = {
        "ref": RUNTIME,
        "name": "adk",
        "version": "1.4.0",
        "root_kind": "builtin",
        "display_name": "Google ADK",
        "framework": "google-adk",
        "protocol": 1,
        "profile": "v1",
        "capabilities": ["mcp"],
        "limits": {"tools": 50},
        "reserved_tool_names": [],
        "verified": True,
        "usable": True,
        "conformance": {
            "passed": False,
            "result": "incomplete",
            "digest": IMAGE,
            "date": "2026-11-18T10:05:00Z",
        },
    }
    scripted["runtimes"] = ok({"schema_version": 1, "runtimes": [row]})
    status, data, _ = api(center, "GET", f"/api/projects/{project['id']}/runtimes", None, cookie)
    assert status == 200 and data["error"] is None, data
    assert data["data"][0]["conformance"]["result"] == "incomplete"
    [(argv, kwargs)] = scripted["calls"]
    assert argv == ["registry", "runtimes", "--project", project["path"], "--json"]
    assert kwargs["track"] is True
    scripted["runtimes"] = ok({"schema_version": 1, "runtimes": [row | {"usable": "yes"}]})
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/runtimes", None, cookie)
    assert data["data"] is None and data["error"]["code"] == "BAD_OUTPUT"
    scripted["runtimes"] = {"data": None, "error": {"code": "X_Y", "message": "m"}, "stale": False}
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/runtimes", None, cookie)
    assert data["data"] is None and data["error"]["code"] == "X_Y"


# -- the job ------------------------------------------------------------------------------------


def test_a_conformance_job_runs_only_on_the_projects_built_image(
    center, cookie, project, project_dir, scripted
):
    write_record(project_dir)
    for options in (
        {"image": OTHER, "runtime": RUNTIME},
        {"image": IMAGE, "runtime": "runtime/adk@1.3.0"},
        {"image": "latest", "runtime": RUNTIME},
        {},
    ):
        status, data, _ = start(center, project, cookie, "conformance", options)
        assert status == 400 and data["error"]["code"] == "CONFORMANCE_IMAGE_UNKNOWN", data
    status, data, _ = start(
        center, project, cookie, "conformance", {"image": IMAGE, "runtime": RUNTIME, "x": 1}
    )
    assert status == 200, data
    ended = wait(center, data["data"]["job"], cookie)
    assert ended["kind"] == "conformance"


def test_the_conformance_argv_is_fixed():
    project = {"path": "/p", "environment": "dev"}
    argv = jobs.argv_for("conformance", project, {"image": IMAGE, "runtime": RUNTIME})
    assert argv == ["conformance", IMAGE, "--project", "/p"]
    for image in ("latest", "sha256:" + "C" * 64, "--json", None):
        with pytest.raises(BadRequest):
            jobs.argv_for("conformance", project, {"image": image})
