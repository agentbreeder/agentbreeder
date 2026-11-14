# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Service controls (#822, M6 console half, part 2): `POST .../services/NAME/{start|stop|restart}`
runs `services ACTION DIR NAME --json` as a short write that holds the project. SERVICE_IN_USE is
passed through for the console's confirm, then the request is sent again with `yes`; every other
refusal is the CLI's coded message."""

from __future__ import annotations

import json
import re
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from test_center import DIGEST, api, center, cookie, project, project_dir, wait  # noqa: F401

from agentbreeder.center import server

FAKE = textwrap.dedent(
    """
    import json, os, sys, time
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    def has(name):
        return os.path.exists(os.path.join(marker, name))
    if argv[:1] == ["services"] and argv[1:2] in (["start"], ["stop"], ["restart"]):
        with open(os.path.join(marker, "service-argv"), "a") as log:
            log.write(json.dumps(argv) + "\\n")
        if has("service-pause"):
            time.sleep(1.5)
        action, name = argv[1], argv[3]
        if has("service-error"):
            print(open(os.path.join(marker, "service-error")).read().strip(), file=sys.stderr)
            sys.exit(2)
        in_use = ["local/support", "cluster/orders"] if has("service-in-use") else []
        if in_use and action != "start" and "--yes" not in argv:
            print(json.dumps({"schema_version": 1, "error": {"code": "SERVICE_IN_USE",
                  "message": "x"}}))
            print("[SERVICE_IN_USE] these deployments use the stack: " + ", ".join(in_use)
                  + "; add --yes to " + action + " " + name + " anyway", file=sys.stderr)
            sys.exit(2)
        if has("service-answer"):
            print(open(os.path.join(marker, "service-answer")).read())
            sys.exit(0)
        if in_use and "--yes" in argv:
            print("WARNING: these deployments use the stack", file=sys.stderr)
        state = "exited" if action == "stop" else "running"
        health = "" if action == "stop" else "starting"
        print(json.dumps({"schema_version": 1, "stack": "s" * 12, "service": name,
                          "action": action, "state": state, "health": health,
                          "in_use": in_use if action != "start" else []}))
    elif argv[:2] == ["services", "up"]:
        print("argv", json.dumps(argv[2:]), flush=True)
        time.sleep(float(open(os.path.join(marker, "up-sleep")).read()) if has("up-sleep") else 0)
    elif argv[:2] == ["services", "plan"]:
        print(json.dumps({"digest": "DIGEST", "text": "grant text", "keys": [], "mcp": [],
                          "grant": {"mcp": []}}))
    else:
        print(json.dumps({}))
    """
).replace("DIGEST", DIGEST)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "fake_services_cli.py"
    path.write_text(FAKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    return [sys.executable, str(path)]


def control(center, project, cookie, name, action, body=None):
    path = f"/api/projects/{project['id']}/services/{name}/{action}"
    return api(center, "POST", path, {} if body is None else body, cookie)


def argvs(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "service-argv"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.parametrize("action", ["start", "stop", "restart"])
def test_each_action_runs_the_cli_on_one_service(center, project, cookie, tmp_path, action):
    status, reply, _ = control(center, project, cookie, "litellm", action)
    assert status == 200 and reply["error"] is None, reply
    assert reply["data"]["service"] == "litellm" and reply["data"]["action"] == action
    assert reply["data"]["state"] == ("exited" if action == "stop" else "running")
    assert set(reply["data"]) == {"service", "action", "state", "health", "in_use"}
    assert argvs(tmp_path) == [["services", action, project["path"], "litellm", "--json"]]


def test_in_use_is_refused_until_sent_again_with_yes(center, project, cookie, tmp_path):
    (tmp_path / "service-in-use").write_text("")
    status, reply, _ = control(center, project, cookie, "litellm", "stop")
    assert status == 200 and reply["data"] is None
    assert reply["error"]["code"] == "SERVICE_IN_USE"
    assert "local/support, cluster/orders" in reply["error"]["message"]

    status, reply, _ = control(center, project, cookie, "litellm", "stop", {"yes": True})
    assert status == 200 and reply["error"] is None, reply
    assert reply["data"]["in_use"] == ["local/support", "cluster/orders"]
    assert argvs(tmp_path)[-1][-2:] == ["--json", "--yes"]


def test_start_needs_no_confirm_and_takes_no_yes(center, project, cookie, tmp_path):
    (tmp_path / "service-in-use").write_text("")
    status, reply, _ = control(center, project, cookie, "litellm", "start")
    assert status == 200 and reply["error"] is None
    status, reply, _ = control(center, project, cookie, "litellm", "start", {"yes": True})
    assert status == 400
    assert len(argvs(tmp_path)) == 1


@pytest.mark.parametrize(
    "body", [{"yes": "true"}, {"yes": 1}, {"force": True}, ["yes"], {"yes": True, "x": 1}]
)
def test_a_malformed_body_never_reaches_the_cli(center, project, cookie, tmp_path, body):
    status, _, _ = control(center, project, cookie, "litellm", "restart", body)
    assert status == 400
    assert argvs(tmp_path) == []


def test_an_mcp_relay_with_the_longest_server_name_has_controls(center, project, cookie, tmp_path):
    relay = "mcp-" + "a" * 32  # `mcp-` + a 32-character server name (services.schema)
    status, reply, _ = control(center, project, cookie, relay, "restart")
    assert status == 200 and reply["error"] is None, reply
    assert argvs(tmp_path)[-1][3] == relay


def test_the_route_names_match_the_clis_names():
    from agentbreeder.services import schema

    names = re.compile(server.SERVICE_OR_RELAY)
    longest = next(n for n in range(64, 0, -1) if re.fullmatch(schema.MCP_SERVER, "a" * n))
    assert names.fullmatch("mcp-" + "a" * longest)
    assert not names.fullmatch("mcp-" + "a" * (longest + 1))
    service = next(n for n in range(64, 0, -1) if re.fullmatch(schema.SERVICE_NAME, "a" * n))
    assert names.fullmatch("a" * service)  # every stack service name too


@pytest.mark.parametrize("name", ["LiteLLM", "-x", "a" * 37, "..", "lite_llm"])
def test_a_bad_service_name_is_not_a_route(center, project, cookie, tmp_path, name):
    status, _, _ = control(center, project, cookie, name, "restart")
    assert status == 404
    assert argvs(tmp_path) == []


def test_service_action_checks_the_action_itself(center, project, tmp_path):
    """The route allows only the three verbs; the method checks again, for any other caller."""
    from agentbreeder.center.files import BadRequest

    with pytest.raises(BadRequest):
        center.service_action(project, "litellm", "kill", {})
    assert argvs(tmp_path) == []


def test_an_unknown_action_is_not_a_route(center, project, cookie, tmp_path):
    status, _, _ = control(center, project, cookie, "litellm", "kill")
    assert status == 404
    status, _, _ = api(
        center, "GET", f"/api/projects/{project['id']}/services/litellm/restart", cookie=cookie
    )
    assert status in (404, 405)
    assert argvs(tmp_path) == []


@pytest.mark.parametrize(
    ("line", "code", "message"),
    [
        (
            "[PORT_BUSY] port 4000 is already in use on this machine (start); stop what uses it,"
            " then run the command again",
            "PORT_BUSY",
            "port 4000 is already in use on this machine (start); stop what uses it, then run "
            "the command again",
        ),
        (
            "[SERVICE_NOT_CREATED] litellm has no container; run `agentbreeder services up` first",
            "SERVICE_NOT_CREATED",
            "litellm has no container; run `agentbreeder services up` first",
        ),
        ("[SERVICES_BUSY] another services operation is running", "SERVICES_BUSY", None),
        ("Error response from daemon: something /Users/x", "COMMAND_FAILED", None),
    ],
)
def test_refusals_are_the_clis_coded_message(
    center, project, cookie, tmp_path, line, code, message
):
    (tmp_path / "service-error").write_text(line)
    status, reply, _ = control(center, project, cookie, "litellm", "start")
    assert status == 200 and reply["data"] is None
    assert reply["error"]["code"] == code
    if message is not None:
        assert reply["error"]["message"] == message
    if code == "SERVICES_BUSY":
        assert reply["error"]["message"] == server.SERVICES_BUSY_TEXT
    assert "daemon" not in reply["error"]["message"]  # Docker's text never reaches the browser


@pytest.mark.parametrize(
    "answer",
    [
        {"service": "postgres", "action": "start", "state": "running"},
        {"service": "litellm", "action": "stop", "state": "running"},
        {"service": "litellm", "action": "start"},
        {"service": "litellm", "action": "start", "state": "running", "in_use": "x"},
        {"service": "litellm", "action": "start", "state": "running", "in_use": [1]},
        [],
    ],
)
def test_an_unexpected_answer_is_bad_output(center, project, cookie, tmp_path, answer):
    (tmp_path / "service-answer").write_text(json.dumps(answer))
    status, reply, _ = control(center, project, cookie, "litellm", "start")
    assert status == 200 and reply["data"] is None
    assert reply["error"]["code"] == "BAD_OUTPUT"


def test_only_the_checked_fields_reach_the_browser(center, project, cookie, tmp_path):
    answer = {
        "schema_version": 1,
        "stack": "s" * 12,
        "service": "litellm",
        "action": "start",
        "state": "running",
        "path": "/private/somewhere",
    }
    (tmp_path / "service-answer").write_text(json.dumps(answer))
    status, reply, _ = control(center, project, cookie, "litellm", "start")
    assert status == 200 and reply["error"] is None
    assert reply["data"] == {
        "service": "litellm",
        "action": "start",
        "state": "running",
        "health": "",
        "in_use": [],
    }


def test_a_running_services_job_holds_the_project(center, project, cookie, tmp_path):
    (tmp_path / "up-sleep").write_text("2")
    status, reply, _ = api(
        center,
        "POST",
        f"/api/projects/{project['id']}/jobs",
        {"kind": "services-up", "options": {"digest": DIGEST}},
        cookie,
    )
    assert status == 200, reply
    job = reply["data"]["job"]
    status, reply, _ = control(center, project, cookie, "litellm", "restart")
    assert status == 409 and reply["error"]["code"] == "WRITE_BUSY"
    assert argvs(tmp_path) == []
    wait(center, job, cookie)
    status, reply, _ = control(center, project, cookie, "litellm", "restart")
    assert status == 200 and reply["error"] is None


def test_two_controls_on_one_project_do_not_overlap(center, project, cookie, tmp_path):
    (tmp_path / "service-pause").write_text("")
    answers: list[tuple[int, dict]] = []

    def first():
        status, reply, _ = control(center, project, cookie, "litellm", "restart")
        answers.append((status, reply))

    thread = threading.Thread(target=first)
    thread.start()
    deadline = time.monotonic() + 10
    while not argvs(tmp_path) and time.monotonic() < deadline:
        time.sleep(0.02)
    status, reply, _ = control(center, project, cookie, "postgres", "restart")
    thread.join()
    assert status == 409 and reply["error"]["code"] == "WRITE_BUSY"
    assert answers[0][0] == 200 and answers[0][1]["error"] is None
    assert [argv[3] for argv in argvs(tmp_path)] == ["litellm"]


def test_a_service_control_reaches_the_cli_only_with_a_session(center, project, tmp_path):
    status, _, _ = control(center, project, None, "litellm", "restart")
    assert status == 401
    assert argvs(tmp_path) == []
