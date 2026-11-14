"""`agentbreeder up`: the loopback server, its checks, and jobs through a fake CLI."""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import socket
import sys
import textwrap
import threading
import time
from argparse import Namespace
from pathlib import Path
from urllib.parse import quote

import pytest

from agentbreeder.center import jobs, server
from agentbreeder.center.jobs import BadRequest, CenterState, Runner, argv_for, redact
from agentbreeder.center.server import Center, make_server

DIGEST = "a" * 64
KEY_VALUE = "very-secret-key-value"
FAKE_CLI = textwrap.dedent(
    f"""
    import json, os, stat, sys, time
    argv = sys.argv[1:]
    marker = os.environ.get("AGENTBREEDER_FAKE_DIR", "")
    if argv[:2] == ["services", "plan"]:
        mcp = ["relay"] if os.path.exists(marker + "/mcp") else []
        print(json.dumps({{"digest": "{DIGEST}", "text": "grant text", "keys": [], "mcp": mcp}}))
    elif argv[:2] == ["services", "status"]:
        print(json.dumps({{"services": {{}}}}))
    elif argv[:2] == ["services", "up"]:
        path = argv[argv.index("--env-file") + 1] if "--env-file" in argv else None
        if path:
            print("mode", oct(stat.S_IMODE(os.stat(path).st_mode)), flush=True)
            value = open(path).read().split("=", 1)[1].strip().strip("'")
            print("echo", value, flush=True)
            import base64, urllib.parse
            print("b64", base64.b64encode(value.encode()).decode(), flush=True)
            print("url", urllib.parse.quote(value, safe=""), flush=True)
            print("envfile", path, flush=True)
    elif argv[:2] == ["services", "env"]:
        print("export AGENTBREEDER_STATE_DIR=/tmp/fake-state")
    elif argv[:2] == ["registry", "used-by"]:
        print(json.dumps({{"ref": argv[2], "projects": [{{"deployed": []}}]}}))
        if os.path.exists(marker + "/used-by-errors"):
            sys.exit(2)
    elif argv[0] == "registry":
        print("[]")
    elif argv[0] == "list":
        print(json.dumps([{{"id": "1"}}]))
    elif argv[:2] == ["audit", "list"] and os.path.exists(marker + "/audit-rows"):
        open(marker + "/audit-argv", "w").write(json.dumps(argv))
        count = int(open(marker + "/audit-rows").read())
        print(json.dumps([{{"seq": count - i}} for i in range(count)]))
    elif argv[:2] == ["audit", "list"] or argv[:2] == ["audit", "verify"]:
        print(json.dumps({{"args": argv}}))
    elif argv[0] == "doctor":
        print("[]")
    elif argv[:2] == ["cluster", "status"]:
        sys.stderr.write("[STACK_NOT_UP] nope\\n")
        sys.exit(2)
    elif argv[0] == "reconcile":
        import signal, subprocess
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        print("grandchild", child.pid, flush=True)
        time.sleep(60)
    elif argv[0] == "remove":
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        print("grandchild", child.pid, flush=True)
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            sys.exit(130)
    elif argv[0] in ("build", "cluster"):
        try:
            print("started", flush=True)
            time.sleep(30)
        except KeyboardInterrupt:
            print("interrupted", flush=True)
            sys.exit(130)
    elif argv[0] == "promote":
        sys.exit(3)
    elif argv[0] == "release":
        print("noise" * 2000, flush=True)
        sys.exit(5)
    elif argv[0] == "next":
        open(marker + "/next-argv", "w").write(json.dumps(argv))
        guide = marker + "/guide.json"
        if not os.path.exists(guide):
            sys.stderr.write("[NEXT_FAILED] no guide\\n")
            sys.exit(2)
        print(open(guide).read().replace("@PROJECT@", argv[1]))
    elif argv[:2] == ["new", "agent"]:
        target = argv[argv.index("--dir") + 1]
        open(marker + "/new-argv", "w").write(json.dumps(argv))
        if os.path.exists(marker + "/new-refuse"):
            print(json.dumps({{"schema_version": 1, "error": {{
                "code": "DIR_NOT_EMPTY", "message": "\\x1b[31m" + target + " is not empty"}}}}))
            sys.stderr.write("[DIR_NOT_EMPTY] not empty\\n")
            sys.exit(2)
        os.makedirs(target, mode=0o755, exist_ok=True)
        for name in ("agent.yaml", "stack.yaml"):
            with open(os.path.join(target, name), "w") as handle:
                handle.write("kind: x\\n")
        summary = {{"schema_version": 1, "dry_run": False, "name": argv[2], "directory": target}}
        print(json.dumps(summary))
    elif argv[:2] == ["services", "logs"] or argv[0] == "logs":
        open(marker + "/logs-argv", "w").write(json.dumps(argv))
        if os.path.exists(marker + "/logs-error"):
            sys.stderr.write(open(marker + "/logs-error").read())
            sys.exit(2)
        print(open(marker + "/logs.json").read())
    elif argv[0] == "env-dump":
        print(json.dumps(dict(os.environ)))
    elif argv[0] == "garbage":
        print("not json")
    else:
        print("ok")
    """
)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "fake_cli.py"
    path.write_text(FAKE_CLI)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    return [sys.executable, str(path)]


@pytest.fixture
def center(tmp_path, fake_cli):
    directory = tmp_path / "center"
    directory.mkdir(mode=0o700)
    instance = Center(directory, prefix=fake_cli)
    http_server = make_server(instance, 0)
    thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    thread.start()
    yield instance
    http_server.shutdown()
    http_server.server_close()


@pytest.fixture
def project_dir(tmp_path) -> Path:
    path = tmp_path / "proj"
    path.mkdir()
    (path / "agent.yaml").write_text("kind: agent\n")
    (path / "stack.yaml").write_text("kind: stack\n")
    return path


def call(center, method, path, body=None, cookie=None, **overrides):
    headers = {
        "Host": f"127.0.0.1:{center.port}",
        "X-AgentBreeder-Center": "1",
        **({"X-AgentBreeder-Session": cookie} if cookie else {}),
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers |= {"Content-Type": "application/json", "Origin": center.origin}
    for key, value in overrides.items():
        name = key.replace("_", "-")
        if value is None:
            headers.pop(name, None)
        else:
            headers[name] = value
    connection = http.client.HTTPConnection("127.0.0.1", center.port, timeout=30)
    connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for name, value in headers.items():
        connection.putheader(name, value)
    if data is not None:
        connection.putheader("Content-Length", str(len(data)))
    connection.endheaders(data)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response, raw


def api(center, method, path, body=None, cookie=None, **overrides):
    response, raw = call(center, method, path, body, cookie, **overrides)
    return response.status, json.loads(raw or b"{}"), response


@pytest.fixture
def cookie(center):
    url, _ = center.mint_login()
    token = url.split("#t=")[1]
    status, data, _ = api(center, "POST", "/api/session", {"token": token})
    assert status == 200
    return data["data"]["session"]


@pytest.fixture
def project(center, project_dir, cookie):
    status, data, _ = api(center, "POST", "/api/projects", {"path": str(project_dir)}, cookie)
    assert status == 200
    return data["data"]


def wait(center, job, cookie):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        _, data, _ = api(center, "GET", f"/api/jobs/{job}", cookie=cookie)
        if data["data"]["state"] != "running":
            return data["data"]
        time.sleep(0.05)
    raise AssertionError("job did not end")


def start(center, project, cookie, kind, options=None, keys=None):
    return api(
        center,
        "POST",
        f"/api/projects/{project['id']}/jobs",
        {"kind": kind, "options": options or {}, "keys": keys},
        cookie,
    )


# -- login and request checks ---------------------------------------------------------------------


def test_static_files_carry_the_security_headers(center):
    response, raw = call(center, "GET", "/")
    assert response.status == 200 and b"AgentBreeder console" in raw
    policy = response.getheader("Content-Security-Policy")
    assert "default-src 'none'" in policy and "form-action 'none'" in policy
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("Referrer-Policy") == "no-referrer"
    assert (
        call(center, "GET", "/app.js")[0].getheader("Content-Type").startswith("text/javascript")
    )
    assert call(center, "GET", "/nope")[0].status == 404


def test_token_is_single_use_and_session_is_never_a_cookie(center):
    url, page = center.mint_login()
    token = url.split("#t=")[1]
    assert page.stat().st_mode & 0o077 == 0 and token in page.read_text()
    status, data, response = api(center, "POST", "/api/session", {"token": token})
    assert status == 200 and data["data"]["session"] and response.getheader("Set-Cookie") is None
    assert api(center, "POST", "/api/session", {"token": token})[0] == 403


def test_expired_and_wrong_tokens_are_refused(center, monkeypatch):
    url, _ = center.mint_login()
    assert api(center, "POST", "/api/session", {"token": "wrong"})[0] == 403
    real = time.time
    monkeypatch.setattr(server.time, "time", lambda: real() + server.TOKEN_TTL + 1)
    assert api(center, "POST", "/api/session", {"token": url.split("#t=")[1]})[0] == 403


def test_session_expires_when_idle(center, cookie, monkeypatch):
    assert api(center, "GET", "/api/projects", cookie=cookie)[0] == 200
    real = time.time
    monkeypatch.setattr(server.time, "time", lambda: real() + server.SESSION_IDLE + 1)
    assert api(center, "GET", "/api/projects", cookie=cookie)[0] == 401


def test_bad_host_is_refused(center, cookie):
    assert api(center, "GET", "/api/projects", cookie=cookie, Host="evil.test")[0] == 403
    assert api(center, "GET", "/", Host=f"localhost:{center.port}")[0] == 403


@pytest.mark.parametrize(
    "overrides",
    [
        {"Origin": "http://evil.test"},
        {"Origin": None},
        {"X_AgentBreeder_Center": None},
        {"Content_Type": "text/plain"},
        {"Sec_Fetch_Site": "cross-site"},
    ],
    ids=["cross-origin", "no-origin", "no-header", "wrong-type", "fetch-metadata"],
)
def test_post_guards(center, cookie, overrides):
    status, _, _ = api(
        center, "POST", "/api/projects", {"example": "northwind"}, cookie, **overrides
    )
    assert status == 403


def test_oversized_body_and_non_object_are_refused(center, cookie):
    big = {"path": "x" * (server.MAX_BODY + 1)}
    status, data, _ = api(center, "POST", "/api/projects", big, cookie)
    assert status == 413 and data["error"]["code"] == "TOO_LARGE"
    response, raw = call(center, "POST", "/api/projects", [1], cookie)
    assert response.status == 400


def test_api_needs_a_session(center):
    assert api(center, "GET", "/api/projects")[0] == 401
    assert api(center, "POST", "/api/projects", {})[0] == 401
    assert api(center, "GET", "/api/projects", X_AgentBreeder_Center=None)[0] == 403


def test_server_only_binds_loopback(center):
    with socket.create_connection(("127.0.0.1", center.port), timeout=2):
        pass
    assert center.origin.startswith("http://127.0.0.1:")


# -- projects and reads ---------------------------------------------------------------------------


def test_project_paths_are_validated(center, cookie, tmp_path, project_dir):
    missing = {"path": str(tmp_path)}
    assert api(center, "POST", "/api/projects", missing, cookie)[0] == 400
    (project_dir / "agent.yaml").unlink()
    (project_dir / "agent.yaml").symlink_to(project_dir / "stack.yaml")
    assert api(center, "POST", "/api/projects", {"path": str(project_dir)}, cookie)[0] == 400
    assert api(center, "POST", "/api/projects", {}, cookie)[0] == 400
    assert api(center, "POST", "/api/projects", {"example": "other"}, cookie)[0] == 400


@pytest.mark.parametrize(
    "view", ["services", "registry", "deployments", "audit", "cluster", "agent", "lock"]
)
def test_views(center, project, cookie, view):
    status, data, _ = api(center, "GET", f"/api/projects/{project['id']}/{view}", cookie=cookie)
    assert status == 200 and data


def test_audit_reads_the_stack_state_dir(center, project, cookie):
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/audit", cookie=cookie)
    assert "/tmp/fake-state" in data["data"]["rows"]["data"]["args"]


def test_audit_pages_by_seq(center, project, cookie, tmp_path):
    """51 rows read, 50 shown, and `older` names the seq the next page starts before."""
    (tmp_path / "audit-rows").write_text("51")
    url = f"/api/projects/{project['id']}/audit"
    _, data, _ = api(center, "GET", url, cookie=cookie)
    assert len(data["data"]["rows"]["data"]) == 50 and data["data"]["older"] == 2
    argv = json.loads((tmp_path / "audit-argv").read_text())
    assert argv[argv.index("--limit") + 1] == "51" and "--before" not in argv
    assert data["data"]["deployments"]["data"] == [{"id": "1"}]
    (tmp_path / "audit-rows").write_text("50")
    _, data, _ = api(center, "GET", url + "?before=51", cookie=cookie)
    assert len(data["data"]["rows"]["data"]) == 50 and data["data"]["older"] is None
    argv = json.loads((tmp_path / "audit-argv").read_text())
    assert argv[argv.index("--before") + 1] == "51" and data["data"]["before"] == 51


def test_audit_before_takes_eighteen_digits(center, project, cookie, tmp_path):
    (tmp_path / "audit-rows").write_text("1")
    url = f"/api/projects/{project['id']}/audit?before={'9' * 18}"
    status, data, _ = api(center, "GET", url, cookie=cookie)
    assert status == 200 and data["data"]["before"] == int("9" * 18)


@pytest.mark.parametrize("before", ["0", "01", "-1", "x", "1e3", "9" * 19, "1%0A2", "%D9%A1"])
def test_audit_before_must_be_a_seq(center, project, cookie, before):
    url = f"/api/projects/{project['id']}/audit?before={before}"
    status, data, _ = api(center, "GET", url, cookie=cookie)
    assert status == 400 and data["error"]["code"] == "BEFORE_INVALID"


def test_failed_view_reports_the_error_then_last_known_data(center, project, cookie, tmp_path):
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/cluster", cookie=cookie)
    assert data == {
        "data": None,
        "error": {"code": "STACK_NOT_UP", "message": "nope"},
        "stale": False,
    }
    runner = center.runner
    assert runner.read(["list"], key="k") == {"data": [{"id": "1"}], "error": None, "stale": False}
    stale = runner._stale("k", {"code": "BUSY", "message": "busy"})
    assert stale["stale"] is True and stale["data"] == [{"id": "1"}]
    assert runner.read(["garbage"])["error"]["code"] == "BAD_OUTPUT"


def test_unknown_routes_and_projects(center, cookie):
    assert api(center, "GET", "/api/other", cookie=cookie)[0] == 404
    assert api(center, "POST", "/api/other", {}, cookie)[0] == 404
    assert api(center, "GET", "/api/projects/000000000000/services", cookie=cookie)[0] == 400
    assert (
        api(center, "GET", f"/api/projects/{'0' * 12}/services", cookie=cookie)[1]["error"]["code"]
        == "UNKNOWN_PROJECT"
    )
    assert api(center, "GET", "/api/jobs/" + "0" * 32, cookie=cookie)[0] == 404


def test_overview(center, cookie):
    status, data, _ = api(center, "GET", "/api/overview", cookie=cookie)
    assert status == 200 and data["data"]["jobs"] == [] and "version" in data["data"]


# -- jobs -----------------------------------------------------------------------------------------


def test_services_job_needs_the_reviewed_grant(center, project, cookie):
    assert start(center, project, cookie, "services-up")[0] == 400
    assert start(center, project, cookie, "services-up", {"digest": "b" * 64})[0] == 400


def test_services_job_passes_keys_privately_and_redacts_them(center, project, cookie):
    status, data, _ = start(
        center, project, cookie, "services-up", {"digest": DIGEST}, {"GEMINI_API_KEY": KEY_VALUE}
    )
    assert status == 200
    job = wait(center, data["data"]["job"], cookie)
    text = "\n".join(line["text"] for line in job["lines"])
    assert job["state"] == "succeeded"
    assert "mode 0o600" in text
    assert KEY_VALUE not in text
    assert base64.b64encode(KEY_VALUE.encode()).decode() not in text
    assert "echo [redacted]" in text and "b64 [redacted]" in text
    env_file = Path(re.search(r"envfile (\S+)", text)[1])
    assert not env_file.exists()
    deadline = time.monotonic() + 5
    while center.state.project(project["id"])["accepted_grant"] is None:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert center.state.project(project["id"])["accepted_grant"] == "grant text"
    assert KEY_VALUE.encode() not in (center.directory / "center.sqlite3").read_bytes()


def test_keys_are_only_for_services_and_must_be_valid(center, project, cookie):
    keys = {"GEMINI_API_KEY": "x"}
    assert start(center, project, cookie, "build", keys=keys)[0] == 400
    bad = start(center, project, cookie, "services-up", {"digest": DIGEST}, {"bad name": "x"})
    assert bad[0] == 400
    newline = {"GOOD": "two\nlines"}
    assert start(center, project, cookie, "services-up", {"digest": DIGEST}, newline)[0] == 400


def test_stack_with_mcp_is_refused_from_the_ui(center, project, cookie, tmp_path):
    (tmp_path / "mcp").write_text("")
    status, data, _ = start(center, project, cookie, "services-up", {"digest": DIGEST})
    assert status == 400 and "MCP" in data["error"]["message"]
    assert data["error"]["code"] == "MCP_NEEDS_CLI"


def test_one_job_at_a_time_and_cancel(center, project, cookie):
    status, data, _ = start(center, project, cookie, "build")
    assert status == 200
    job = data["data"]["job"]
    deadline = time.monotonic() + 15
    while not center.state.job(job)["lines"]:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    busy = start(center, project, cookie, "release")
    assert busy[0] == 409 and busy[1]["data"]["job"] == job
    assert busy[1]["error"]["code"] == "BUSY"
    _, cancelled, _ = api(center, "POST", f"/api/jobs/{job}/cancel", {}, cookie)
    assert cancelled["data"] == {"cancelled": True}
    finished = wait(center, job, cookie)
    assert finished["state"] == "failed" and finished["exit_code"] == 130
    assert api(center, "POST", f"/api/jobs/{job}/cancel", {}, cookie)[1]["data"] == {
        "cancelled": False
    }


def test_job_exit_codes_and_line_limit(center, project, cookie):
    job = wait(center, start(center, project, cookie, "release")[1]["data"]["job"], cookie)
    assert job["state"] == "failed" and job["exit_code"] == 5
    assert all(len(line["text"]) <= jobs.MAX_LINE for line in job["lines"])
    job = wait(center, start(center, project, cookie, "promote")[1]["data"]["job"], cookie)
    assert job["state"] == "waiting_approval"
    data = api(center, "GET", f"/api/jobs/{job['id']}?after=999", cookie=cookie)[1]
    assert data["data"]["lines"] == []


def test_request_cannot_supply_an_env_file(center, project, cookie, tmp_path):
    options = {"digest": DIGEST, "env_file": str(tmp_path / "secrets.env")}
    job = wait(
        center, start(center, project, cookie, "services-up", options)[1]["data"]["job"], cookie
    )
    assert not any("envfile" in line["text"] for line in job["lines"])


def test_a_project_keeps_its_environment(center, project, cookie, project_dir):
    center.environment = "prod"
    status, data, _ = api(center, "POST", "/api/projects", {"path": str(project_dir)}, cookie)
    assert status == 400 and "registered for dev" in data["error"]["message"]


def test_key_values_are_quoted_for_the_env_parser():
    assert jobs._dotenv_quote("a b") == "'a b'"
    assert jobs._dotenv_quote("it's") == '"it\'s"'
    with pytest.raises(BadRequest):
        jobs._dotenv_quote("a'b\"c")


def test_odd_numbers_do_not_crash_the_server(center, cookie, project, project_dir):
    job = start(center, project, cookie, "services-down")[1]["data"]["job"]
    wait(center, job, cookie)
    assert api(center, "GET", f"/api/jobs/{job}?after=%C2%B2", cookie=cookie)[0] == 200
    assert api(center, "POST", "/api/projects", {"path": "a\x00b"}, cookie)[0] == 400


def test_unknown_kind(center, project, cookie):
    assert start(center, project, cookie, "rm -rf")[0] == 400


def test_running_jobs_become_interrupted_after_a_restart(tmp_path):
    state = CenterState(tmp_path)
    state.add_project("p", "n", "/x", "dev")
    state.create_job("j", "p", "release")
    state.db.close()
    again = CenterState(tmp_path)
    assert again.job("j")["state"] == "running"  # opening alone never sweeps (M2 commands)
    again.interrupt_running()
    assert again.job("j")["state"] == "interrupted"
    assert again.latest_jobs("p")["release"]["state"] == "interrupted"


def test_old_jobs_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "KEEP_JOBS", 1)
    state = CenterState(tmp_path)
    for name in "abc":
        state.create_job(name, "p", "build")
        state.append(name, 1, "line")
        state.finish_job(name, "succeeded", 0)
    state.create_job("d", "p", "build")
    assert state.job("a") is None and state.job("c") is not None


PROJECT = {"id": "p", "path": "/work/proj", "environment": "dev"}
STACK = ["--stack", "/work/proj", "--environment", "dev"]


@pytest.mark.parametrize(
    "kind,options,expected",
    [
        ("services-down", {}, ["services", "down", "/work/proj"]),
        ("build", {}, ["build", "/work/proj/agent.yaml", *STACK, "--locked"]),
        ("release", {}, ["release", "/work/proj/agent.yaml", *STACK]),
        ("rollback", {}, ["release", "/work/proj/agent.yaml", *STACK, "--rollback"]),
        ("cluster-down", {}, ["cluster", "down", "--stack", "/work/proj"]),
        ("promote", {}, ["promote", "/work/proj/agent.yaml", *STACK, "--to", "kubernetes"]),
        ("reconcile", {}, ["reconcile", "--local", *STACK]),
        (
            "cluster-up",
            {},
            ["cluster", "up", "--stack", "/work/proj", "--environment", "dev"],
        ),
        (
            "remove",
            {"deployment": "c" * 32, "target": "kubernetes"},
            ["remove", "c" * 32, "--cluster", *STACK],
        ),
        (
            "remove",
            {"deployment": "c" * 32, "target": "local"},
            ["remove", "c" * 32, "--local", *STACK],
        ),
    ],
)
def test_argv_is_fixed(kind, options, expected):
    assert argv_for(kind, PROJECT, options) == expected


@pytest.mark.parametrize(
    "options",
    [
        {"deployment": "--purge", "target": "local"},
        {"deployment": "c" * 32, "target": "elsewhere"},
        {},
    ],
)
def test_remove_rejects_anything_but_a_known_shape(options):
    with pytest.raises(BadRequest):
        argv_for("remove", PROJECT, options)


def test_argv_for_unknown_kind():
    with pytest.raises(BadRequest):
        argv_for("nope", PROJECT, {})


def test_redact_forms():
    text = f"a {KEY_VALUE} b {quote('p@ss word', safe='')}"
    out = redact(text, [KEY_VALUE, "p@ss word", ""])
    assert KEY_VALUE not in out and "p%40ss" not in out


def test_child_environment_is_an_allow_list(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/evil")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("AGENTBREEDER_STATE_DIR", "/s")
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    monkeypatch.setenv("DOCKER_HOST", "tcp://x")
    env = jobs.child_environment()
    assert "PYTHONPATH" not in env and "GEMINI_API_KEY" not in env and "DOCKER_HOST" not in env
    assert env["AGENTBREEDER_STATE_DIR"] == "/s" and env["DOCKER_CONTEXT"] == "default"


def test_planted_module_in_a_project_never_runs_on_the_host(tmp_path):
    """The real child command: `-I` and a cwd outside the project ignore a planted `yaml.py`."""
    project = tmp_path / "evil"
    project.mkdir()
    marker = tmp_path / "pwned"
    (project / "yaml.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
    center_dir = tmp_path / "center"
    center_dir.mkdir()
    runner = Runner(CenterState(center_dir), center_dir)
    assert runner.prefix[1:] == ["-I", "-m", "agentbreeder"]
    answer = runner.read(["registry", "list", "--project", str(project), "--json"], timeout=60)
    assert "data" in answer and not marker.exists()


def test_center_never_opens_the_engine_directly():
    root = Path(server.__file__).parent
    forbidden = re.compile(
        r"agentbreeder\.(store|deployment|release|promote|gate)\b|import Store|DeploymentService"
    )
    for path in root.glob("*.py"):
        assert not forbidden.search(path.read_text()), path.name


# -- main -----------------------------------------------------------------------------------------


def arguments(**overrides) -> Namespace:
    return Namespace(
        **{
            "dir": None,
            "environment": "dev",
            "port": 0,
            "no_browser": True,
            "yes": False,
            **overrides,
        }
    )


@pytest.fixture
def quiet_main(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "center-main"))
    from agentbreeder import doctor

    monkeypatch.setattr(doctor, "check_docker", lambda: [])
    monkeypatch.setattr(doctor, "check_compose", lambda: [])
    monkeypatch.setattr(
        server.ThreadingHTTPServer,
        "serve_forever",
        lambda self: (_ for _ in ()).throw(KeyboardInterrupt()),
    )


def test_main_prints_a_link_and_explains_what_keeps_running(quiet_main, capsys):
    assert server.main(arguments()) == 0
    captured = capsys.readouterr()
    assert "http://127.0.0.1:" in captured.out and "#t=" in captured.out
    assert "keep running" in captured.err


def test_main_opens_the_private_file_not_the_token(quiet_main, monkeypatch):
    opened = []
    monkeypatch.setattr(server.webbrowser, "open", opened.append)
    assert server.main(arguments(no_browser=False)) == 0
    assert opened[0].startswith("file://") and "#t=" not in opened[0]


def test_main_with_yes_survives_a_refused_stack(quiet_main, monkeypatch, project_dir, capsys):
    def refused(self, project, body):
        raise BadRequest("This stack has MCP servers")

    monkeypatch.setattr(Center, "start_job", refused)
    monkeypatch.setattr(Center, "plan", lambda self, project: {"data": {"text": "grant"}})
    assert server.main(arguments(dir=project_dir, yes=True)) == 0
    assert "Services not started" in capsys.readouterr().err


def test_main_rejects_a_bad_environment_and_project(quiet_main, tmp_path, capsys):
    assert server.main(arguments(environment="NOT VALID")) == 2
    assert server.main(arguments(dir=tmp_path)) == 2
    assert "agent.yaml" in capsys.readouterr().err


def test_explicit_busy_port_is_an_error(quiet_main, capsys):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        assert server.main(arguments(port=blocker.getsockname()[1])) == 2


def test_center_directory_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "c"))
    assert os.stat(server.center_directory()).st_mode & 0o077 == 0


# -- route table and guards (S-03, S-25), envelope, body caps, static allow-list ------------------


GUARDS = {
    "host": ({"Host": "evil.test"}, "FORBIDDEN"),
    "api-header": ({"X_AgentBreeder_Center": None}, "FORBIDDEN"),
    "fetch-site": ({"Sec_Fetch_Site": "cross-site"}, "FORBIDDEN"),
    "session": ({"X_AgentBreeder_Session": None}, "SIGN_IN"),
    "origin": ({"Origin": None}, "FORBIDDEN"),
    "content-type": ({"Content_Type": "text/plain"}, "FORBIDDEN"),
}


def _route_cases():
    for route in server.ROUTES:
        for name in GUARDS:
            if name in ("origin", "content-type") and route.method == "GET":
                continue
            if name == "session" and not route.session:
                continue
            yield pytest.param(route, name, id=f"{route.method} {route.pattern} -{name}")


def _send_route(center, route, cookie, **overrides):
    body = {"token": "x"} if route.method != "GET" else None
    return api(center, route.method, route.sample, body, cookie, **overrides)


def test_every_route_sample_matches_its_pattern():
    for route in server.ROUTES:
        assert route.regex.fullmatch(route.sample), route


@pytest.mark.parametrize("route", server.ROUTES, ids=lambda r: f"{r.method} {r.pattern}")
def test_every_route_passes_its_guards_with_all_headers(center, cookie, route):
    _, data, _ = _send_route(center, route, cookie)
    assert set(data) == {"data", "error", "stale"}
    assert (data["error"] or {}).get("code") not in ("FORBIDDEN", "SIGN_IN")


@pytest.mark.parametrize("route,guard", list(_route_cases()))
def test_every_route_refuses_a_request_missing_any_guard(center, cookie, route, guard):
    overrides, code = GUARDS[guard]
    status, data, _ = _send_route(center, route, cookie, **overrides)
    assert status == (401 if code == "SIGN_IN" else 403)
    assert data["error"]["code"] == code and data["data"] is None


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_same_site_page_with_a_valid_session_is_refused(center, cookie, method):
    """S-25: another 127.0.0.1 port is same-site, not same-origin."""
    body = {} if method == "POST" else None
    status, data, _ = api(
        center, method, "/api/projects", body, cookie, Sec_Fetch_Site="same-site"
    )
    assert status == 403 and data["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "FOO"])
def test_unknown_methods_get_405_after_the_guards(center, cookie, method):
    status, data, response = api(center, method, "/api/projects", {}, cookie)
    assert status == 405 and data["error"]["code"] == "METHOD"
    assert response.getheader("Allow") == "GET, POST"
    assert api(center, method, "/api/projects", {}, cookie, Host="evil.test")[0] == 403
    assert api(center, method, "/", {})[0] == 405


def test_head_of_a_static_file_has_no_body(center):
    response, raw = call(center, "HEAD", "/")
    assert response.status == 200 and raw == b""


def test_a_write_needs_a_content_length(center, cookie):
    connection = http.client.HTTPConnection("127.0.0.1", center.port, timeout=30)
    connection.putrequest("POST", "/api/projects", skip_host=True, skip_accept_encoding=True)
    for name, value in {
        "Host": f"127.0.0.1:{center.port}",
        "X-AgentBreeder-Center": "1",
        "X-AgentBreeder-Session": cookie,
        "Origin": center.origin,
        "Content-Type": "application/json",
    }.items():
        connection.putheader(name, value)
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 411
    connection.close()


def _claimed_length(center, cookie, path, length):
    """A request that claims `length` bytes and sends two: the cap is checked before reading."""
    connection = http.client.HTTPConnection("127.0.0.1", center.port, timeout=30)
    connection.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
    for name, value in {
        "Host": f"127.0.0.1:{center.port}",
        "X-AgentBreeder-Center": "1",
        "X-AgentBreeder-Session": cookie,
        "Origin": center.origin,
        "Content-Type": "application/json",
        "Content-Length": str(length),
    }.items():
        connection.putheader(name, value)
    connection.endheaders(b"{}")
    try:
        connection.sock.shutdown(socket.SHUT_WR)
    except OSError:
        pass  # the server already answered (413) and closed
    status = connection.getresponse().status
    connection.close()
    return status


def test_editor_routes_take_a_larger_body(center, cookie, monkeypatch):
    editor = server.Route(
        "POST",
        r"/api/echo",
        lambda request: server.envelope(len(request.body["text"])),
        "/api/echo",
        limit=server.MAX_EDITOR_BODY,
    )
    monkeypatch.setattr(server.Handler, "routes", (*server.ROUTES, editor))
    status, data, _ = api(center, "POST", "/api/echo", {"text": "x" * 200_000}, cookie)
    assert status == 200 and data["data"] == 200_000
    assert _claimed_length(center, cookie, "/api/echo", server.MAX_EDITOR_BODY + 1) == 413
    assert _claimed_length(center, cookie, "/api/echo", server.MAX_EDITOR_BODY) == 400
    status, data, _ = api(center, "POST", "/api/echo", {"other": 1}, cookie)
    assert status == 500 and data["error"]["code"] == "INTERNAL"
    assert api(center, "POST", "/api/projects", {"text": "x" * 70_000}, cookie)[0] == 413


def test_static_allow_list_serves_fonts_and_nothing_else(center):
    response, raw = call(center, "GET", "/fonts/Geist-Variable.woff2")
    assert response.status == 200 and response.getheader("Content-Type") == "font/woff2"
    assert raw[:4] == b"wOF2"
    assert call(center, "GET", "/brand.css")[0].getheader("Content-Type").startswith("text/css")
    for path in ("/../server.py", "/fonts/../../server.py", "/server.py", "/fonts/"):
        assert call(center, "GET", path)[0].status == 404, path
    assert "font-src 'self'" in call(center, "GET", "/")[0].getheader("Content-Security-Policy")


def test_static_files_skip_dotfiles_symlinks_and_unknown_types(tmp_path):
    (tmp_path / "index.html").write_text("<p>")
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "a.js").write_text("1")
    (tmp_path / ".hidden.js").write_text("1")
    (tmp_path / "notes.md").write_text("1")
    (tmp_path / "link.js").symlink_to(tmp_path / "deep" / "a.js")
    files = server.static_files(tmp_path)
    assert sorted(files) == ["/", "/deep/a.js", "/index.html"]
    assert files["/deep/a.js"] == (b"1", "text/javascript; charset=utf-8")


def test_static_files_are_read_once_at_start(center, monkeypatch):
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("read per request"))
    assert call(center, "GET", "/app.js")[0].status == 200


def test_polls_do_not_keep_a_session_alive(center, cookie, monkeypatch):
    """Guided §14.9 R7: a job-log poll does not refresh the idle timer."""
    real = time.time
    monkeypatch.setattr(server.time, "time", lambda: real() + server.SESSION_IDLE - 5)
    assert api(center, "GET", "/api/jobs/" + "0" * 32, cookie=cookie)[0] == 404
    monkeypatch.setattr(server.time, "time", lambda: real() + server.SESSION_IDLE + 5)
    assert api(center, "GET", "/api/projects", cookie=cookie)[0] == 401


def test_a_request_refreshes_the_idle_timer(center, cookie, monkeypatch):
    real = time.time
    monkeypatch.setattr(server.time, "time", lambda: real() + server.SESSION_IDLE - 5)
    assert api(center, "GET", "/api/projects", cookie=cookie)[0] == 200
    monkeypatch.setattr(server.time, "time", lambda: real() + server.SESSION_IDLE + 5)
    assert api(center, "GET", "/api/projects", cookie=cookie)[0] == 200


def test_local_file_errors_become_a_coded_500(center, cookie, monkeypatch):
    def broken(self):
        raise PermissionError("secret path /home/x")

    monkeypatch.setattr(CenterState, "projects", broken)
    status, data, _ = api(center, "GET", "/api/projects", cookie=cookie)
    assert status == 500 and data["error"]["code"] == "LOCAL_FILE"
    assert "/home/x" not in json.dumps(data)


# -- S-17: only coded CLI errors reach the browser ------------------------------------------------


def test_cli_error_takes_the_last_coded_line():
    stderr = "Using stack x\n[FIRST] one\nnoise\n[LOCK_STALE] run agentbreeder build\n"
    assert jobs.cli_error(stderr, 2) == {"code": "LOCK_STALE", "message": "run agentbreeder build"}


def test_cli_error_never_forwards_free_stderr():
    stderr = 'Traceback (most recent call last):\n  File "/Users/me/x.py"\nKeyError: secret\n'
    problem = jobs.cli_error(stderr, 1)
    assert problem["code"] == "COMMAND_FAILED" and "secret" not in problem["message"]
    assert "/Users" not in problem["message"] and "exit 1" in problem["message"]


def test_cli_error_strips_control_and_bidi_characters_and_caps_length():
    problem = jobs.cli_error("[BAD_THING] a\x1b[31mb‮c" + "z" * 900, 2)
    assert problem["message"].startswith("a[31mbc") and len(problem["message"]) == jobs.MAX_MESSAGE


def test_read_timeouts_and_start_failures_are_coded(tmp_path, monkeypatch):
    runner = Runner(CenterState(tmp_path), tmp_path, ["/nonexistent/python"])
    assert runner.read(["list"])["error"]["code"] == "COMMAND_FAILED"

    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n")
    slow = Runner(CenterState(tmp_path), tmp_path, [sys.executable, str(script)])
    started = time.monotonic()
    assert slow.read(["list"], timeout=0.3)["error"]["code"] == "TIMEOUT"
    assert slow.read(["list"], timeout=0.3, track=True)["error"]["code"] == "TIMEOUT"
    assert time.monotonic() - started < 10 and slow.children == set()


# -- S-01: projects only from directories you alone control ---------------------------------------


@pytest.fixture
def owned_tmp():
    """A user-owned directory under the sticky /tmp."""
    import shutil
    import tempfile

    path = Path(tempfile.mkdtemp(dir="/tmp")).resolve()
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _make_project(path: Path) -> Path:
    path.mkdir(mode=0o755)
    (path / "agent.yaml").write_text("kind: agent\n")
    (path / "stack.yaml").write_text("kind: stack\n")
    return path


def test_a_user_owned_project_under_tmp_passes(owned_tmp):
    from agentbreeder.center.files import check_project

    check_project(_make_project(owned_tmp / "proj"))


@pytest.mark.parametrize("target", [".", "agent.yaml", "stack.yaml"])
def test_world_or_group_writable_project_files_are_refused(owned_tmp, target):
    from agentbreeder.center.files import check_project

    project = _make_project(owned_tmp / "proj")
    (project / target).chmod(0o777 if target == "." else 0o664)
    with pytest.raises(BadRequest, match="writable by other users") as caught:
        check_project(project)
    assert caught.value.code == "PROJECT_UNSAFE"


def test_a_writable_ancestor_is_refused_unless_sticky(owned_tmp):
    from agentbreeder.center.files import check_project

    shared = owned_tmp / "shared"
    shared.mkdir()
    project = _make_project(shared / "proj")
    shared.chmod(0o777)
    with pytest.raises(BadRequest, match="shared is writable"):
        check_project(project)
    shared.chmod(0o1777)
    check_project(project)


def test_another_users_files_are_refused(owned_tmp, monkeypatch):
    from agentbreeder.center import files

    project = _make_project(owned_tmp / "proj")
    real = os.lstat

    def other_owner(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if str(path).endswith("stack.yaml") or Path(path) == owned_tmp:
            values = list(info)
            values[4] = os.getuid() + 1  # st_uid
            return os.stat_result(values)
        return info

    monkeypatch.setattr(files.os, "lstat", other_owner)
    with pytest.raises(BadRequest, match="stack.yaml is owned by another user"):
        files.check_project(project)
    with pytest.raises(BadRequest, match="owned by another user"):
        files.check_ancestors(project)


def test_a_missing_or_non_directory_project_is_refused(owned_tmp):
    from agentbreeder.center.files import check_ancestors, check_project

    (owned_tmp / "file").write_text("")
    for path in (owned_tmp / "missing", owned_tmp / "file"):
        with pytest.raises(BadRequest, match="must be a directory"):
            check_project(path)
    with pytest.raises(BadRequest, match="cannot be read"):
        check_ancestors(owned_tmp / "missing")


def test_registration_and_job_start_refuse_an_unsafe_project(center, cookie, project_dir):
    project_dir.chmod(0o777)
    status, data, _ = api(center, "POST", "/api/projects", {"path": str(project_dir)}, cookie)
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"
    assert "writable by other users" in data["error"]["message"]
    project_dir.chmod(0o755)
    added = api(center, "POST", "/api/projects", {"path": str(project_dir)}, cookie)[1]["data"]
    (project_dir / "agent.yaml").chmod(0o666)
    status, data, _ = start(center, added, cookie, "build")
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"
    assert center.runner.running() == []


def test_onboarding_refuses_an_unsafe_destination(center, cookie, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: home))
    (home / "agentbreeder-projects").mkdir()
    (home / "agentbreeder-projects").chmod(0o777)
    ran = []
    monkeypatch.setattr(center.runner, "read_text", lambda *a, **k: ran.append(a))
    status, data, _ = api(center, "POST", "/api/projects", {"example": "northwind"}, cookie)
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE" and not ran


def test_private_dir_refuses_a_symlink_and_tightens_modes(tmp_path):
    from agentbreeder.center import files

    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    real.chmod(0o755)
    assert files.private_dir(real).stat().st_mode & 0o077 == 0
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(files.UnsafeDirectory):
        files.private_dir(tmp_path / "link")


def test_write_private_leaves_no_temp_file_on_failure(tmp_path, monkeypatch):
    from agentbreeder.center import files

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", fail)
    directory = tmp_path / "d"
    directory.mkdir()
    with pytest.raises(OSError):
        files.write_private(directory / "login.html", b"x")
    assert list(directory.iterdir()) == []


# -- review round 1 (Fable): dispatch errors, discarded bodies, guard order, moved projects ------


def _raw(center, request: bytes) -> tuple[int, bytes]:
    with socket.create_connection(("127.0.0.1", center.port), timeout=30) as connection:
        connection.sendall(request)
        connection.shutdown(socket.SHUT_WR)
        data = b""
        while chunk := connection.recv(65536):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


def test_only_sign_in_skips_the_session_and_only_job_polls_keep_it_idle():
    assert [(r.method, r.pattern) for r in server.ROUTES if not r.session] == [
        ("POST", "/api/session")
    ]
    assert [(r.method, r.sample) for r in server.ROUTES if r.poll] == [
        ("GET", "/api/activity"),
        ("GET", "/api/jobs/" + "0" * 32),
    ]


@pytest.mark.parametrize(
    "overrides,status",
    [({"Origin": None}, 403), ({"X_AgentBreeder_Session": None}, 401)],
    ids=["no-origin", "no-session"],
)
@pytest.mark.parametrize("path", ["/api/projects", "/api/nothing-here"])
def test_404_and_405_come_after_every_guard(center, cookie, overrides, status, path):
    assert api(center, "PUT", path, {}, cookie, **overrides)[0] == status
    assert api(center, "GET", path, None, cookie, X_AgentBreeder_Session=None)[0] == 401


def test_a_missing_session_is_reported_before_the_body_cap(center):
    big = {"path": "x" * (server.MAX_BODY + 1)}
    status, data, _ = api(center, "POST", "/api/projects", big)
    assert status == 401 and data["error"]["code"] == "SIGN_IN"


def test_a_really_large_body_still_gets_its_413(center, cookie):
    big = json.dumps({"path": "x" * (4 * 1024 * 1024)}).encode()
    request = (
        f"POST /api/projects HTTP/1.1\r\nHost: 127.0.0.1:{center.port}\r\n"
        f"X-AgentBreeder-Center: 1\r\nX-AgentBreeder-Session: {cookie}\r\n"
        f"Origin: {center.origin}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(big)}\r\n\r\n"
    ).encode()
    status, body = _raw(center, request + big)
    assert status == 413 and json.loads(body)["error"]["code"] == "TOO_LARGE"


def test_deeply_nested_json_is_a_400_not_a_crash(center):
    status, body = _raw(
        center,
        (
            f"POST /api/session HTTP/1.1\r\nHost: 127.0.0.1:{center.port}\r\n"
            f"X-AgentBreeder-Center: 1\r\nOrigin: {center.origin}\r\n"
            "Content-Type: application/json\r\nContent-Length: 60000\r\n\r\n"
        ).encode()
        + b"[" * 60000,
    )
    assert status == 400 and json.loads(body)["error"]["code"] == "BAD_REQUEST"


def test_a_bad_request_path_is_a_coded_400(center, cookie):
    status, body = _raw(
        center,
        (
            f"GET http://[x/api/overview HTTP/1.1\r\nHost: 127.0.0.1:{center.port}\r\n"
            f"X-AgentBreeder-Center: 1\r\nX-AgentBreeder-Session: {cookie}\r\n\r\n"
        ).encode(),
    )
    assert status == 400 and json.loads(body)["error"]["code"] == "BAD_REQUEST"


def test_http_parse_errors_get_the_envelope_and_security_headers(center):
    with socket.create_connection(("127.0.0.1", center.port), timeout=30) as connection:
        connection.sendall(b"GET / HTTP/9.9\r\nHost: x\r\n\r\n")
        data = b""
        while chunk := connection.recv(65536):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    assert b"Content-Security-Policy" in head and b"<html" not in body.lower()
    assert json.loads(body)["error"]["code"] == "HTTP"


def test_an_unexpected_error_outside_a_handler_is_a_coded_500(center, cookie, monkeypatch):
    monkeypatch.setattr(server, "parse_qs", lambda query: (_ for _ in ()).throw(KeyError("x")))
    status, data, _ = api(center, "GET", "/api/projects", cookie=cookie)
    assert status == 500 and data["error"]["code"] == "INTERNAL"


def test_cli_error_ignores_coded_lines_before_the_last_line():
    assert jobs.cli_error("[WARNING] /Users/me/x\nplain last line\n", 2)["code"] == (
        "COMMAND_FAILED"
    )


def test_a_moved_or_relinked_project_is_refused_at_job_start(center, cookie, owned_tmp):
    real = owned_tmp / "real"
    real.mkdir()
    project_dir = _make_project(real / "proj")
    status, data, _ = api(center, "POST", "/api/projects", {"path": str(project_dir)}, cookie)
    added = data["data"]
    shared = owned_tmp / "shared"
    shared.mkdir()
    real.rename(shared / "real")
    shared.chmod(0o777)
    real.symlink_to(shared / "real")
    status, data, _ = start(center, added, cookie, "build")
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"
    assert "moved or replaced by a link" in data["error"]["message"]
    real.unlink()
    status, data, _ = start(center, added, cookie, "build")
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"


def test_a_link_among_the_ancestors_is_refused(owned_tmp):
    from agentbreeder.center.files import check_ancestors

    (owned_tmp / "target").mkdir()
    (owned_tmp / "link").symlink_to(owned_tmp / "target")
    with pytest.raises(BadRequest, match="not a plain directory"):
        check_ancestors(owned_tmp / "link")


def test_the_unsafe_message_names_the_fix(owned_tmp):
    from agentbreeder.center.files import check_project

    project = _make_project(owned_tmp / "proj")
    project.chmod(0o775)
    with pytest.raises(BadRequest, match=r"chmod go-w"):
        check_project(project)


def test_services_never_start_from_a_stale_grant(center, project, cookie, monkeypatch):
    monkeypatch.setattr(
        Center,
        "plan",
        lambda self, project: {
            "data": {"digest": DIGEST, "text": "old", "mcp": []},
            "error": {"code": "TIMEOUT", "message": "x"},
            "stale": True,
            "accepted_text": None,
        },
    )
    status, data, _ = start(center, project, cookie, "services-up", {"digest": DIGEST})
    assert status == 400 and data["error"]["code"] == "GRANT_UNAVAILABLE"


def test_a_stalled_body_times_out_with_a_coded_408(center, cookie, monkeypatch):
    monkeypatch.setattr(server.Handler, "timeout", 0.5)
    with socket.create_connection(("127.0.0.1", center.port), timeout=30) as connection:
        connection.sendall(
            (
                f"POST /api/projects HTTP/1.1\r\nHost: 127.0.0.1:{center.port}\r\n"
                f"X-AgentBreeder-Center: 1\r\nX-AgentBreeder-Session: {cookie}\r\n"
                f"Origin: {center.origin}\r\nContent-Type: application/json\r\n"
                "Content-Length: 100\r\n\r\n{"
            ).encode()
        )
        data = b""
        while chunk := connection.recv(65536):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    assert int(head.split()[1]) == 408 and json.loads(body)["error"]["code"] == "TIMEOUT"


def test_a_client_that_goes_away_mid_request_leaves_no_traceback(center, cookie, capfd):
    import struct

    started, release = threading.Event(), threading.Event()

    def slow(argv, timeout=30, key=None):
        started.set()
        release.wait(10)
        return {"data": [], "error": None, "stale": False}

    center.runner.read = slow
    connection = socket.create_connection(("127.0.0.1", center.port), timeout=30)
    connection.sendall(
        (
            f"GET /api/overview HTTP/1.1\r\nHost: 127.0.0.1:{center.port}\r\n"
            f"X-AgentBreeder-Center: 1\r\nX-AgentBreeder-Session: {cookie}\r\n\r\n"
        ).encode()
    )
    assert started.wait(10)
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    connection.close()  # a reset, like a closed tab
    release.set()
    time.sleep(0.5)
    assert api(center, "GET", "/api/projects", cookie=cookie)[0] == 200
    err = capfd.readouterr().err
    assert "Traceback" not in err and "request failed" not in err


def test_a_trickled_body_is_discarded_for_a_bounded_time(center, monkeypatch):
    monkeypatch.setattr(server, "DISCARD_SECONDS", 0.5)
    with socket.create_connection(("127.0.0.1", center.port), timeout=30) as connection:
        connection.sendall(
            b"POST /api/projects HTTP/1.1\r\nHost: evil.test\r\nContent-Length: 16000000\r\n\r\nx"
        )
        assert connection.recv(65536).split()[1] == b"403"
        started, dropped = time.monotonic(), False
        while time.monotonic() - started < 10:
            try:
                connection.sendall(b"x")  # trickle, as a slow client would
            except OSError:
                dropped = True  # the server stopped discarding and closed
                break
            time.sleep(0.1)
        assert dropped and time.monotonic() - started < 3


def test_onboarding_checks_before_creating_anything(center, cookie, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    home.chmod(0o777)
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: home))
    status, data, _ = api(center, "POST", "/api/projects", {"example": "northwind"}, cookie)
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"
    assert not (home / "agentbreeder-projects").exists()


def test_a_reset_while_headers_are_read_leaves_no_traceback(center, capfd):
    import struct

    connection = socket.create_connection(("127.0.0.1", center.port), timeout=30)
    connection.sendall(b"GET /api/overview HTTP/1.1\r\nHost: x")
    time.sleep(0.2)
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    connection.close()
    time.sleep(0.5)
    err = capfd.readouterr().err
    assert "Traceback" not in err and "Exception occurred" not in err


def test_onboarding_refuses_a_broken_link_for_the_projects_folder(
    center, cookie, tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    (home / "agentbreeder-projects").symlink_to(tmp_path / "nowhere")
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: home))
    status, data, _ = api(center, "POST", "/api/projects", {"example": "northwind"}, cookie)
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"
    assert "broken link" in data["error"]["message"]


# -- several jobs, lock scopes, short writes, shutdown (S-11, S-18) -------------------------------


@pytest.fixture
def second_project(center, cookie, tmp_path):
    path = tmp_path / "proj2"
    path.mkdir()
    (path / "agent.yaml").write_text("kind: agent\n")
    (path / "stack.yaml").write_text("kind: stack\n")
    status, data, _ = api(center, "POST", "/api/projects", {"path": str(path)}, cookie)
    assert status == 200
    return data["data"]


def _started(center, job):
    """Wait until a job's child has printed its first line."""
    deadline = time.monotonic() + 15
    while not center.state.job(job)["lines"]:
        assert time.monotonic() < deadline, "job did not start"
        time.sleep(0.05)
    return center.state.job(job)["lines"]


def _job_id(response):
    status, data, _ = response
    assert status == 200, data
    return data["data"]["job"]


def test_lock_keys():
    project = {"id": "abc"}
    assert jobs.lock_keys("build", project) == ("project:abc",)
    assert jobs.lock_keys("services-up", project) == ("project:abc",)
    for kind in ("cluster-up", "cluster-down", "promote"):
        assert jobs.lock_keys(kind, project) == ("machine", "project:abc")


def test_two_projects_run_engine_jobs_at_once(center, cookie, project, second_project):
    first = _job_id(start(center, project, cookie, "build"))
    second = _job_id(start(center, second_project, cookie, "build"))
    _started(center, first), _started(center, second)
    _, overview, _ = api(center, "GET", "/api/overview", cookie=cookie)
    running = overview["data"]["jobs"]
    assert [job["id"] for job in running] == [first, second]
    assert {job["project"] for job in running} == {project["id"], second_project["id"]}
    _, activity, _ = api(center, "GET", "/api/activity", cookie=cookie)
    assert len(activity["data"]["running"]) == 2
    assert {row["id"] for row in activity["data"]["recent"]} >= {first, second}
    # Cancel reaches only the job it names.
    assert api(center, "POST", f"/api/jobs/{first}/cancel", {}, cookie)[1]["data"]["cancelled"]
    assert wait(center, first, cookie)["exit_code"] == 130
    assert center.state.job(second)["state"] == "running"
    api(center, "POST", f"/api/jobs/{second}/cancel", {}, cookie)
    wait(center, second, cookie)


def test_cluster_jobs_serialise_machine_wide(center, cookie, project, second_project):
    first = _job_id(start(center, project, cookie, "cluster-up"))
    _started(center, first)
    for other in ("cluster-down", "promote"):
        status, data, _ = start(center, second_project, cookie, other)
        assert status == 409 and data["data"]["job"] == first, other
    # A plain engine job in the other project is not blocked by the cluster job...
    build = _job_id(start(center, second_project, cookie, "build"))
    # ...and the cluster job holds its own project too.
    assert start(center, project, cookie, "release")[0] == 409
    api(center, "POST", f"/api/jobs/{first}/cancel", {}, cookie)
    wait(center, first, cookie)
    status, data, _ = start(center, second_project, cookie, "cluster-down")
    assert status == 409 and data["data"]["job"] == build
    api(center, "POST", f"/api/jobs/{build}/cancel", {}, cookie)
    wait(center, build, cookie)
    after = _job_id(start(center, second_project, cookie, "cluster-down"))
    api(center, "POST", f"/api/jobs/{after}/cancel", {}, cookie)
    wait(center, after, cookie)
    assert center.runner.held == {}


def test_a_failed_start_releases_its_locks(center, project, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("no python")

    monkeypatch.setattr(jobs.subprocess, "Popen", broken)
    with pytest.raises(OSError):
        center.runner.start(project, "cluster-up", {})
    assert center.runner.held == {} and center.runner.running() == []


def test_shutdown_stops_every_child_and_their_groups(center, cookie, project, second_project):
    import signal as signals
    import subprocess

    gentle = _job_id(start(center, project, cookie, "build"))
    stubborn = _job_id(start(center, second_project, cookie, "reconcile"))
    _started(center, gentle)
    lines = _started(center, stubborn)
    grandchild = int(lines[0]["text"].split()[1])
    follow = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    center.runner.adopt(follow)
    started = time.monotonic()
    stopped = center.runner.shutdown(grace=1.0)
    assert sorted(stopped) == sorted([gentle, stubborn])
    assert time.monotonic() - started < 10
    assert follow.poll() is not None
    assert wait(center, gentle, cookie)["exit_code"] == 130  # SIGINT, the graceful step
    assert wait(center, stubborn, cookie)["exit_code"] == -signals.SIGTERM
    deadline = time.monotonic() + 5
    while True:  # the grandchild was in the job's process group
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "grandchild survived shutdown"
        time.sleep(0.05)
    status, data, _ = start(center, project, cookie, "build")
    assert status == 400 and data["error"]["code"] == "STOPPING"


def test_shutdown_tolerates_children_that_already_ended(tmp_path):
    import subprocess

    runner = Runner(CenterState(tmp_path), tmp_path)
    done = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    done.wait()
    runner.adopt(done)
    assert runner.shutdown(grace=0.1) == []


def test_short_writes_lock_per_target_and_are_not_blocked_by_jobs(center, cookie, project):
    runner = center.runner
    build = _job_id(start(center, project, cookie, "build"))
    _started(center, build)
    assert runner.short_write("registry-publish", "tools/a", ["list"])["data"] == [{"id": "1"}]
    entered, release = threading.Event(), threading.Event()
    real_read = runner.read
    seen = []

    def slow_read(argv, timeout=30, key=None):
        seen.append(timeout)
        entered.set()
        release.wait(10)
        return real_read(argv, timeout, key)

    runner.read = slow_read
    thread = threading.Thread(target=runner.short_write, args=("memory-edit", "n1", ["list"]))
    thread.start()
    assert entered.wait(10)
    with pytest.raises(jobs.WriteBusy):
        runner.short_write("memory-edit", "n1", ["list"])
    runner.read = real_read
    assert runner.short_write("memory-edit", "n2", ["list"])["error"] is None
    release.set()
    thread.join(10)
    assert seen == [jobs.SHORT_TIMEOUT]
    with pytest.raises(jobs.WriteBusy):
        runner.short_write("policy-save", "operator", ["list"])
    api(center, "POST", f"/api/jobs/{build}/cancel", {}, cookie)
    wait(center, build, cookie)
    assert runner.short_write("policy-save", "operator", ["list"])["error"] is None
    assert runner.held == {}


def test_short_write_timeouts_per_kind(tmp_path, monkeypatch):
    runner = Runner(CenterState(tmp_path), tmp_path)
    seen = []
    monkeypatch.setattr(runner, "read", lambda argv, timeout, key: seen.append(timeout) or {})
    runner.short_write("tool-publish", "tools/x", ["registry", "publish"])
    runner.short_write("set-ref", "agent", ["agent", "set-ref"])
    assert seen == [300, 60]
    runner.shutdown(grace=0)
    with pytest.raises(BadRequest):
        runner.short_write("set-ref", "agent", ["agent", "set-ref"])


def test_a_job_end_clears_the_read_cache(center, cookie, project):
    center.runner.read(["list"], key="cached")
    assert "cached" in center.runner.cache
    wait(center, _job_id(start(center, project, cookie, "services-down")), cookie)
    assert "cached" not in center.runner.cache


def test_main_stops_running_jobs_on_exit(quiet_main, monkeypatch, capsys):
    monkeypatch.setattr(jobs.Runner, "shutdown", lambda self, grace=5.0: ["a", "b"])
    assert server.main(arguments()) == 0
    assert "Stopped 2 running job(s)" in capsys.readouterr().err


# -- review round 1 on jobs (Fable): leaks, groups, policy both ways, set-ref, write busy ---------


def _gone(pid: int, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _stop_group(pid: int | None, ended, grace: float = 2.0) -> None:
    """Test cleanup that cannot leave a process behind: SIGTERM the process group led by
    `pid`, then SIGKILL it if `ended(grace)` says it is still there. A console stuck in a
    deadlock ignores SIGTERM, so the SIGKILL step is what stops it outliving the test."""
    import signal as signals

    if pid is None:
        return
    for number in (signals.SIGTERM, signals.SIGKILL):
        try:
            os.killpg(pid, number)
        except ProcessLookupError:
            return
        except PermissionError:  # macOS: a group left with only a zombie leader
            ended(0)  # reap it
            return
        if ended(grace):
            return


def _popen_ended(process):
    """For `_stop_group`: waits for (and reaps) a direct child."""
    import subprocess

    def ended(timeout: float) -> bool:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True

    return ended


def _forked_ended(pid: int):
    """For `_stop_group`: waits for (and reaps) a child from `pty.fork`."""

    def ended(timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:  # at least one waitpid, so `ended(0)` reaps a zombie
            try:
                done, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return True
            if done:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    return ended


def test_a_job_that_cannot_be_recorded_releases_its_locks_and_stops(center, project, monkeypatch):
    import sqlite3

    started = []
    real_popen = jobs.subprocess.Popen

    def tracking(*args, **kwargs):
        started.append(real_popen(*args, **kwargs))
        return started[-1]

    def broken(*args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(jobs.subprocess, "Popen", tracking)
    monkeypatch.setattr(center.state, "create_job", broken)
    with pytest.raises(sqlite3.OperationalError):
        center.runner.start(project, "cluster-up", {})
    assert center.runner.held == {} and center.runner.running() == []
    assert started and started[0].poll() is not None  # stopped, not orphaned


def test_shutdown_stops_the_group_of_a_job_that_honoured_sigint(center, cookie, project):
    removing = _job_id(
        start(center, project, cookie, "remove", {"deployment": "c" * 32, "target": "local"})
    )
    grandchild = int(_started(center, removing)[0]["text"].split()[1])
    assert center.runner.shutdown(grace=1.0) == [removing]
    assert wait(center, removing, cookie)["exit_code"] == 130
    assert _gone(grandchild), "a grandchild of a job that exited on SIGINT survived shutdown"


def test_a_second_ctrl_c_skips_the_grace_wait(center, cookie, project, monkeypatch):
    stubborn = _job_id(start(center, project, cookie, "reconcile"))
    _started(center, stubborn)
    calls = []

    def interrupted(processes, deadline):
        calls.append(deadline)
        if len(calls) == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(jobs, "_wait_all", interrupted)
    started = time.monotonic()
    center.runner.shutdown(grace=30)
    assert time.monotonic() - started < 5 and len(calls) == 2
    assert wait(center, stubborn, cookie)["state"] == "failed"  # SIGTERM still sent


def test_no_engine_job_starts_during_a_policy_save(center, cookie, project):
    runner = center.runner
    entered, release = threading.Event(), threading.Event()
    real_read = runner.read

    def slow_read(argv, timeout=30, key=None):
        entered.set()
        release.wait(10)
        return real_read(argv, timeout, key)

    runner.read = slow_read
    thread = threading.Thread(target=runner.short_write, args=("policy-save", "op", ["list"]))
    thread.start()
    assert entered.wait(10)
    status, data, _ = start(center, project, cookie, "build")
    assert status == 409 and data["error"]["code"] == "WRITE_BUSY" and data["data"] is None
    release.set()
    thread.join(10)
    runner.read = real_read
    job = _job_id(start(center, project, cookie, "services-down"))
    wait(center, job, cookie)


def test_set_ref_holds_its_project(center, cookie, project):
    runner = center.runner
    build = _job_id(start(center, project, cookie, "build"))
    _started(center, build)
    with pytest.raises(jobs.WriteBusy):
        runner.short_write("set-ref", "agent", ["list"], project=project)
    api(center, "POST", f"/api/jobs/{build}/cancel", {}, cookie)
    wait(center, build, cookie)
    entered, release = threading.Event(), threading.Event()
    real_read = runner.read

    def slow_read(argv, timeout=30, key=None):
        entered.set()
        release.wait(10)
        return real_read(argv, timeout, key)

    runner.read = slow_read
    thread = threading.Thread(
        target=runner.short_write, args=("set-ref", "agent", ["list"]), kwargs={"project": project}
    )
    thread.start()
    assert entered.wait(10)
    status, data, _ = start(center, project, cookie, "build")
    assert status == 409 and data["error"]["code"] == "WRITE_BUSY"
    release.set()
    thread.join(10)
    runner.read = real_read
    assert runner.held == {}


def test_a_job_whose_log_cannot_be_written_is_stopped_gracefully(center, cookie, project):
    calls = []

    def broken(*args):
        calls.append(args)
        raise OSError("disk full")

    center.state.append = broken
    job = _job_id(start(center, project, cookie, "build"))
    finished = wait(center, job, cookie)
    # SIGINT first (the fake build exits 130 on it), as Cancel does; then marked failed.
    assert calls and finished["state"] == "failed" and finished["exit_code"] == 130
    assert center.runner.running() == [] and center.runner.held == {}


def test_adopt_prunes_ended_children_and_forget_removes_one(tmp_path):
    import subprocess

    runner = Runner(CenterState(tmp_path), tmp_path)
    done = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    done.wait()
    runner.adopt(done)
    alive = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    runner.adopt(alive)
    assert runner.children == {alive}
    runner.forget(alive)
    assert runner.children == set()
    alive.kill()
    alive.wait()


def test_main_says_it_is_stopping_jobs(quiet_main, monkeypatch, capsys):
    monkeypatch.setattr(
        jobs.Runner, "running", lambda self: [{"id": "a", "project": "p", "kind": "build"}]
    )
    monkeypatch.setattr(jobs.Runner, "shutdown", lambda self, grace=5.0: ["a"])
    assert server.main(arguments()) == 0
    assert "Stopping 1 running job(s)" in capsys.readouterr().err


# -- review round 2 on jobs (Fable) ---------------------------------------------------------------


def test_a_failed_start_stops_its_child_outside_the_mutex(center, project, monkeypatch):
    import sqlite3
    import subprocess

    monkeypatch.setattr(jobs, "SHUTDOWN_GRACE", 1.0)
    stubborn = []
    real_popen = subprocess.Popen

    def ignoring_term(*args, **kwargs):
        code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        stubborn.append(
            real_popen(
                [sys.executable, "-c", code], start_new_session=True, stdout=subprocess.PIPE
            )
        )
        return stubborn[-1]

    def broken(*args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(jobs.subprocess, "Popen", ignoring_term)
    monkeypatch.setattr(center.state, "create_job", broken)
    errors = []
    thread = threading.Thread(
        target=lambda: errors.append(
            pytest.raises(sqlite3.OperationalError, center.runner.start, project, "build", {})
        )
    )
    thread.start()
    time.sleep(0.3)
    assert center.runner.mutex.acquire(timeout=0.5), "start() held the mutex while stopping"
    center.runner.mutex.release()
    thread.join(10)
    assert errors and stubborn[0].poll() is not None  # SIGKILL after the ignored SIGTERM
    assert center.runner.held == {}


def test_shutdown_never_signals_a_long_ended_childs_group(tmp_path, monkeypatch):
    import subprocess

    runner = Runner(CenterState(tmp_path), tmp_path)
    ended = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    runner.children.add(ended)
    ended.wait()
    signalled = []
    monkeypatch.setattr(jobs, "_signal_group", lambda process, number: signalled.append(process))
    runner.shutdown(grace=0.1)
    assert signalled == []


def test_write_busy_says_why(center, cookie, project):
    runner = center.runner
    build = _job_id(start(center, project, cookie, "build"))
    _started(center, build)
    with pytest.raises(jobs.WriteBusy) as busy:
        runner.short_write("policy-save", "op", ["list"])
    assert busy.value.reason == "job"
    with pytest.raises(jobs.WriteBusy) as busy:
        runner.short_write("set-ref", "agent", ["list"], project=project)
    assert busy.value.reason == "job"
    api(center, "POST", f"/api/jobs/{build}/cancel", {}, cookie)
    wait(center, build, cookie)
    runner.held["policy-save:op"] = "short:x"
    status, data, _ = start(center, project, cookie, "build")
    assert status == 409 and "Another save is in progress" in data["error"]["message"]
    runner.held.clear()


def test_finishing_a_job_is_retried_once(center, cookie, project, monkeypatch):
    real = CenterState.finish_job
    failures = []

    def flaky(self, *args):
        if not failures:
            failures.append(1)
            raise OSError("busy")
        return real(self, *args)

    monkeypatch.setattr(CenterState, "finish_job", flaky)
    job = _job_id(start(center, project, cookie, "services-down"))
    assert wait(center, job, cookie)["state"] == "succeeded" and failures


def test_a_key_file_that_cannot_be_removed_still_frees_the_locks(
    center, cookie, project, monkeypatch
):
    real_unlink = Path.unlink

    def refuse(self, *args, **kwargs):
        if self.name.startswith("env-"):
            raise PermissionError("EPERM")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    status, data, _ = start(
        center, project, cookie, "services-up", {"digest": DIGEST}, {"GEMINI_API_KEY": KEY_VALUE}
    )
    wait(center, data["data"]["job"], cookie)
    assert center.runner.held == {} and center.runner.running() == []
    monkeypatch.setattr(Path, "unlink", real_unlink)
    for leftover in center.directory.glob("env-*"):
        leftover.unlink()


# -- up: isolated re-exec, single instance, leftovers, port policy (S-16, S-29) -------------------


def _free_ports(count: int) -> list[int]:
    """`count` ports free right now (bound and released)."""
    sockets = []
    for _ in range(count):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sockets.append(sock)
    ports = [sock.getsockname()[1] for sock in sockets]
    for sock in sockets:
        sock.close()
    return ports


@pytest.fixture
def blocked_port():
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        yield blocker.getsockname()[1]


def _center(tmp_path) -> Center:
    directory = tmp_path / "c"
    directory.mkdir(exist_ok=True)
    return Center(directory)


def test_first_run_scans_past_a_busy_preferred_port(tmp_path, monkeypatch, blocked_port):
    free = _free_ports(1)[0]
    monkeypatch.setattr(server, "PORTS", (blocked_port, free))
    instance = _center(tmp_path)
    assert not instance.state.has_password()
    http_server = make_server(instance, None)
    try:
        assert instance.port == free
        assert instance.state.setting("port") == str(free)
    finally:
        http_server.server_close()


def test_once_a_password_exists_a_busy_port_is_refused(tmp_path, monkeypatch, blocked_port):
    monkeypatch.setattr(server, "PORTS", (blocked_port, _free_ports(1)[0]))
    instance = _center(tmp_path)
    instance.state.db.execute("CREATE TABLE users (email TEXT)")
    instance.state.db.execute("INSERT INTO users VALUES ('admin@localhost')")
    assert instance.state.has_password()
    with pytest.raises(server.PortBusy) as busy:
        make_server(instance, None)
    assert busy.value.port == blocked_port and not busy.value.scanned


def test_the_remembered_port_is_preferred(tmp_path, monkeypatch):
    first, second = _free_ports(2)
    monkeypatch.setattr(server, "PORTS", (first, second))
    instance = _center(tmp_path)
    instance.state.set_setting("port", str(second))
    assert server.port_candidates(instance, None) == ([second, first], True)
    assert server.port_candidates(instance, 1234) == ([1234], False)


def test_no_free_port_in_the_range(tmp_path, monkeypatch, blocked_port):
    monkeypatch.setattr(server, "PORTS", (blocked_port,))
    with pytest.raises(server.PortBusy) as busy:
        make_server(_center(tmp_path), None)
    assert busy.value.scanned


def test_the_default_range_is_8080_to_8099():
    assert server.PORTS[0] == 8080 and server.PORTS[-1] == 8099 and len(server.PORTS) == 20


@pytest.mark.parametrize("password", [False, True])
def test_main_names_the_busy_port(quiet_main, monkeypatch, capsys, blocked_port, password):
    monkeypatch.setattr(server, "PORTS", (blocked_port,))
    monkeypatch.setattr(CenterState, "has_password", lambda self: password)
    assert server.main(arguments(port=None)) == 2
    err = capsys.readouterr().err
    assert "--port" in err and str(blocked_port) in err
    assert ("is in use by another program" in err) is password
    assert ("No free port" in err) is not password


def test_a_second_up_prints_the_running_url(quiet_main, tmp_path, capsys):
    from agentbreeder.center.instance import SingleInstance

    first = SingleInstance(server.center_directory())
    first.acquire()
    first.publish("http://127.0.0.1:8085")
    try:
        assert server.main(arguments()) == 0
        out = capsys.readouterr().out
        assert "already running: http://127.0.0.1:8085" in out and "#t=" not in out
    finally:
        first.release()
    assert server.main(arguments()) == 0
    assert "already running" not in capsys.readouterr().out


def test_an_unreadable_lock_url_is_not_printed(tmp_path):
    from agentbreeder.center.instance import AlreadyRunning, SingleInstance

    tmp_path.chmod(0o700)
    first = SingleInstance(tmp_path)
    first.acquire()
    os.pwrite(first.descriptor, b"http://evil.test/#t=x", 0)
    with pytest.raises(AlreadyRunning) as running:
        SingleInstance(tmp_path).acquire()
    assert running.value.url is None
    first.release()


def test_leftovers_are_removed_only_by_the_lock_holder(quiet_main, capsys):
    from agentbreeder.center.instance import SingleInstance

    directory = server.center_directory()
    for name in ("env-abc", "edit-123", "keep.txt"):
        (directory / name).write_text("secret")
    holder = SingleInstance(directory)
    holder.acquire()
    assert server.main(arguments()) == 0
    assert (directory / "env-abc").exists()  # a running console's file is never touched
    holder.release()
    assert server.main(arguments()) == 0
    assert sorted(p.name for p in directory.iterdir() if not p.name.startswith(".")) == [
        "center.sqlite3",
        "keep.txt",
        "login.html",
        "up.lock",
    ]
    assert "Removed 2 file(s)" in capsys.readouterr().err


def test_main_refuses_a_newer_schema(quiet_main, capsys):
    import sqlite3

    directory = server.center_directory()
    database = sqlite3.connect(directory / "center.sqlite3")
    database.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    database.execute("INSERT INTO schema_version VALUES (99)")
    database.commit()
    database.close()
    assert server.main(arguments()) == 2
    assert "newer than this AgentBreeder" in capsys.readouterr().err


def test_sigterm_stops_the_console_like_ctrl_c():
    with pytest.raises(KeyboardInterrupt):
        server._stop(15, None)


# -- center.sqlite3 schema versions and the a2 upgrade --------------------------------------------

A2_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
    environment TEXT NOT NULL, accepted_grant TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL,
    state TEXT NOT NULL, exit_code INTEGER, created REAL NOT NULL,
    finished REAL);
CREATE TABLE IF NOT EXISTS lines (
    job TEXT NOT NULL, seq INTEGER NOT NULL, text TEXT NOT NULL,
    PRIMARY KEY (job, seq));
"""


def test_a2_state_is_migrated_with_projects_and_history_kept(tmp_path):
    import sqlite3

    database = sqlite3.connect(tmp_path / "center.sqlite3")
    database.executescript(A2_SCHEMA)
    database.execute("INSERT INTO projects VALUES ('p1','nw','/x','dev','grant',1.0)")
    database.execute("INSERT INTO jobs VALUES ('j1','p1','release','succeeded',0,1.0,2.0)")
    database.execute("INSERT INTO jobs VALUES ('j2','p1','build','running',NULL,3.0,NULL)")
    database.execute("INSERT INTO lines VALUES ('j1',1,'released')")
    database.commit()
    database.close()
    state = CenterState(tmp_path)
    assert state.migrated_from == 0
    version = state.db.execute("SELECT version FROM schema_version").fetchall()
    assert version == [(jobs.SCHEMA_VERSION,)]
    assert state.project("p1")["accepted_grant"] == "grant"
    assert state.job("j1")["lines"] == [{"seq": 1, "text": "released"}]
    state.interrupt_running()
    assert state.job("j2")["state"] == "interrupted"
    assert not state.has_password()
    state.db.close()
    again = CenterState(tmp_path)
    assert again.migrated_from == jobs.SCHEMA_VERSION


def test_a_failed_migration_changes_nothing(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setitem(jobs.MIGRATIONS, 3, ("CREATE TABLE extra (x)", "THIS IS NOT SQL"))
    monkeypatch.setattr(jobs, "SCHEMA_VERSION", 3)
    with pytest.raises(sqlite3.Error):
        CenterState(tmp_path)
    database = sqlite3.connect(tmp_path / "center.sqlite3")
    tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master")}
    assert "extra" not in tables and "projects" not in tables


def test_a_newer_schema_is_refused(tmp_path):
    import sqlite3

    database = sqlite3.connect(tmp_path / "center.sqlite3")
    database.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    database.execute("INSERT INTO schema_version VALUES (99)")
    database.commit()
    with pytest.raises(jobs.SchemaTooNew):
        CenterState(tmp_path)


# -- the `up` command: re-exec under -I, and the isolated entry point -----------------------------


def test_up_re_execs_the_server_isolated(monkeypatch, tmp_path):
    from agentbreeder import __version__, doctor
    from agentbreeder.center import launch
    from agentbreeder.commands import up

    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "center"))
    monkeypatch.setattr(doctor, "check_docker", lambda: [])
    monkeypatch.setattr(doctor, "check_compose", lambda: [])
    seen = []

    def fake_exec(path, argv):
        seen.append(argv)
        raise SystemExit(0)

    monkeypatch.setattr(launch.os, "execv", fake_exec)
    monkeypatch.chdir(tmp_path)
    args = Namespace(dir=Path("-proj"), environment="dev", port=8090, no_browser=True, yes=True)
    with pytest.raises(SystemExit):
        up.run(args)
    assert seen[0][:4] == [sys.executable, "-I", "-m", "agentbreeder.center"]
    import agentbreeder as package

    assert seen[0][4:] == [
        f"--expect-version={__version__}",
        f"--expect-package={Path(package.__file__).resolve().parent}",
        "--environment=dev",
        "--port",
        "8090",
        "--no-browser",
        "--yes",
        "--",
        str(tmp_path / "-proj"),
    ]


def test_up_reports_failed_docker_checks_before_the_re_exec(monkeypatch, capsys, tmp_path):
    from agentbreeder import doctor
    from agentbreeder.center import launch
    from agentbreeder.commands import up

    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "center"))
    failed = doctor.Check("docker", False, "not running")
    monkeypatch.setattr(doctor, "check_docker", lambda: [failed])
    monkeypatch.setattr(doctor, "check_compose", lambda: [])
    monkeypatch.setattr(
        launch.os, "execv", lambda path, argv: (_ for _ in ()).throw(SystemExit(0))
    )
    with pytest.raises(SystemExit):
        up.run(Namespace(dir=None, environment="dev", port=None, no_browser=False, yes=False))
    assert "FAIL docker" in capsys.readouterr().err


def test_the_isolated_entry_point_parses_what_up_passes(monkeypatch):
    from agentbreeder.center import __main__ as entry

    seen = []
    monkeypatch.setattr(entry.server, "serve_process", lambda args: seen.append(args) or 0)
    assert entry.run(["--environment", "prod", "--port", "8081", "--no-browser", "--", "-p"]) == 0
    args = seen[0]
    assert (args.environment, args.port, args.no_browser, args.yes) == ("prod", 8081, True, False)
    assert args.dir == Path("-p")


def _wait_for_line(stream, pattern: str, timeout: float = 60) -> str:
    """The first line of `stream` matching `pattern`, read on a thread so a silent process
    cannot hang the test past `timeout`."""
    import queue

    lines: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in stream], daemon=True).start()
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            break
        seen.append(line)
        if re.search(pattern, line):
            return line
    raise AssertionError(f"no {pattern!r} in {seen!r}")


def _console_script(tmp_path, fake_cli) -> Path:
    """A real console process (`server.main`) that starts one fake `build` job and prints
    `JOB <id> <pid>`."""
    project = tmp_path / "p"
    project.mkdir(exist_ok=True)
    (project / "agent.yaml").write_text("kind: agent\n")
    (project / "stack.yaml").write_text("kind: stack\n")
    script = tmp_path / "run.py"
    script.write_text(
        "import sys\n"
        "from argparse import Namespace\n"
        "from pathlib import Path\n"
        "from agentbreeder.center import server\n"
        f"fake = {fake_cli!r}\n"
        "real = server.Center.__init__\n"
        "def init(self, directory, environment='dev', prefix=None):\n"
        "    real(self, directory, environment, fake)\n"
        "server.Center.__init__ = init\n"
        "orig = server.make_server\n"
        "def make(center, port):\n"
        "    s = orig(center, port)\n"
        f"    project = center.register(Path({str(project)!r}))\n"
        "    job = center.runner.start(project, 'build', {})\n"
        "    import time\n"
        "    deadline = time.monotonic() + 30\n"
        "    while not center.state.job(job)['lines']:  # its SIGINT handler is in place\n"
        "        assert time.monotonic() < deadline, 'the fake job never started'\n"
        "        time.sleep(0.05)\n"
        "    print('JOB', job, center.runner.jobs[job].process.pid, flush=True)\n"
        "    return s\n"
        "server.make_server = make\n"
        "args = Namespace(dir=None, environment='dev', port=0, no_browser=True, yes=False)\n"
        "sys.exit(server.serve_process(args))\n"
    )
    return script


@pytest.mark.parametrize(
    "names,gap",
    [
        (["SIGTERM"], 0.05),
        (["SIGHUP"], 0.05),
        (["SIGHUP", "SIGHUP", "SIGTERM"], 0.05),
        (["SIGTERM", "SIGHUP"], 0),  # back to back: the second lands while the first unwinds
        (["SIGHUP", "SIGTERM", "SIGHUP"], 0),
    ],
)
def test_sigterm_and_sighup_stop_the_console_and_its_jobs(tmp_path, fake_cli, names, gap):
    """A signal stops the console and its jobs. Repeated signals do not cut the grace short:
    the fake build still exits through its SIGINT handler (130)."""
    import signal as signals
    import subprocess

    env = {**os.environ, "AGENTBREEDER_CENTER_DIR": str(tmp_path / "center")}
    console = subprocess.Popen(
        [sys.executable, str(_console_script(tmp_path, fake_cli))],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    job_pid = None
    try:
        job_pid = int(_wait_for_line(console.stdout, r"^JOB ").split()[2])
        time.sleep(1.0)  # let the fake build start
        for name in names:
            console.send_signal(getattr(signals, name))
            time.sleep(gap)
        _, err = console.communicate(timeout=30)
        assert console.returncode == 0, err
        assert "Stopped 1 running job(s)" in err
        assert _gone(job_pid)
        job_pid = None  # confirmed gone: nothing for the cleanup to signal
    finally:  # on any failure or timeout: neither the console nor its job outlives the test
        if console.returncode is None:
            _stop_group(console.pid, _popen_ended(console))
        _stop_group(job_pid, lambda timeout: _gone(job_pid, timeout))
    database = CenterState(tmp_path / "center")
    rows = database.recent_jobs()
    assert rows[0]["exit_code"] == 130  # SIGINT honoured, not SIGTERM


def test_closing_the_terminal_stops_the_jobs(tmp_path, fake_cli):
    """The real case: `up` on a terminal that closes. Writes to the dead tty fail, and the
    jobs are still stopped."""
    import pty

    script = _console_script(tmp_path, fake_cli)
    env = {**os.environ, "AGENTBREEDER_CENTER_DIR": str(tmp_path / "center")}
    pid, master = pty.fork()
    if pid == 0:  # the child: a session leader on a new terminal
        try:
            os.execve(sys.executable, [sys.executable, str(script)], env)
        finally:
            os._exit(127)  # never run the rest of the test in the forked child
    job_pid = None
    closed = False
    try:
        output = b""
        deadline = time.monotonic() + 60
        while b"JOB " not in output or not output.split(b"JOB ", 1)[1].count(b"\n"):
            assert time.monotonic() < deadline, output
            try:
                output += os.read(master, 4096)
            except OSError:
                break
        job_pid = int(output.split(b"JOB ", 1)[1].split()[1])
        time.sleep(1.0)
        os.close(master)  # the terminal goes away: SIGHUP
        closed = True
        done = 0
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            done, _status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.1)
        assert done, "the console did not stop when its terminal closed"
        assert _gone(job_pid), "a job survived the terminal closing"
        pid = job_pid = None  # both reaped or confirmed gone: nothing for the cleanup to signal
    finally:  # on any failure or timeout: neither the console nor its job outlives the test
        if not closed:
            os.close(master)
        if pid is not None:
            _stop_group(pid, _forked_ended(pid))
        _stop_group(job_pid, lambda timeout: _gone(job_pid, timeout))


@pytest.fixture
def real_handlers():
    """Put back this process's signal handlers whatever a test of `main` leaves behind."""
    import signal as signals

    saved = {number: signals.getsignal(number) for number in server.STOP_SIGNALS}
    yield
    for number, handler in saved.items():
        if handler is not None:
            signals.signal(number, handler)
    server._stopping.clear()


def test_stop_raises_once(real_handlers):
    import signal as signals

    server._stopping.clear()
    with pytest.raises(KeyboardInterrupt):
        server._stop(signals.SIGTERM, None)
    assert server._stop(signals.SIGHUP, None) is None  # a second stop signal is a no-op


def test_main_restores_handlers_and_skips_ones_it_cannot(quiet_main, real_handlers, monkeypatch):
    import signal as signals

    before = {number: signals.getsignal(number) for number in server.STOP_SIGNALS}
    assert server.main(arguments()) == 0
    assert {number: signals.getsignal(number) for number in server.STOP_SIGNALS} == before
    calls = []
    real_signal = signals.signal

    def recording(number, handler):
        calls.append(handler)
        return real_signal(number, handler)

    monkeypatch.setattr(signals, "getsignal", lambda number: None)  # set outside Python
    monkeypatch.setattr(signals, "signal", recording)
    assert server.main(arguments()) == 0  # no TypeError from signal.signal(n, None)
    assert None not in calls


def test_shutdown_still_terminates_when_every_attempt_is_interrupted(tmp_path, monkeypatch):
    center = Center(tmp_path)
    monkeypatch.setattr(
        center.runner, "shutdown", lambda grace=5.0: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    terminated = []
    monkeypatch.setattr(center.runner, "terminate_all", lambda: terminated.append(1))
    server._shut_down(center)
    assert terminated == [1]


def test_terminate_all_signals_every_live_group(tmp_path, monkeypatch):
    import subprocess

    runner = Runner(CenterState(tmp_path), tmp_path)
    live = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    runner.adopt(live)
    runner.terminate_all()
    assert live.wait(timeout=10) == -15 and runner.stopping


def test_a_closed_stderr_does_not_stop_shutdown(monkeypatch):
    import io

    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)
    server._say("hello")


def test_a_dead_terminal_does_not_break_the_last_flush(monkeypatch):
    class Dead:
        def write(self, text):
            raise OSError(5, "Input/output error")

        def flush(self):
            raise OSError(5, "Input/output error")

    monkeypatch.setattr(sys, "stderr", Dead())
    monkeypatch.setattr(sys, "stdout", Dead())
    server._say("hello")
    print("still fine")
    print("still fine", file=sys.stderr)


def test_shutdown_retries_when_a_signal_interrupts_it(tmp_path, monkeypatch):
    center = Center(tmp_path)
    calls = []

    def interrupted(grace=5.0):
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return []

    monkeypatch.setattr(center.runner, "shutdown", interrupted)
    server._shut_down(center)
    assert len(calls) == 2


def test_up_end_to_end_isolated_single_instance(tmp_path):
    """The real CLI as a subprocess: `up` re-execs as `python -I -m agentbreeder.center` (same
    pid), and a second `up` prints the running URL and exits."""
    import signal as signals
    import subprocess

    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "AGENTBREEDER_CENTER_DIR": str(tmp_path / "center"),
    }
    command = [sys.executable, "-m", "agentbreeder", "up", "--no-browser", "--port", "0"]
    first = subprocess.Popen(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        line = _wait_for_line(first.stdout, r"http://127\.0\.0\.1:[0-9]+ ")
        url = re.search(r"http://127\.0\.0\.1:[0-9]+", line)[0]
        listing = subprocess.run(
            ["ps", "-o", "args=", "-p", str(first.pid)], capture_output=True, text=True
        ).stdout
        assert "-I -m agentbreeder.center" in listing, listing
        second = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
        )
        assert second.returncode == 0
        assert f"already running: {url}" in second.stdout
    finally:
        first.send_signal(signals.SIGTERM)
        try:
            first.wait(timeout=30)
        finally:  # a console that ignores SIGTERM must not outlive the test
            if first.returncode is None:
                _stop_group(first.pid, _popen_ended(first))
    assert first.returncode == 0


# -- review round 1 on up (Fable) -----------------------------------------------------------------


@pytest.fixture
def wildcard_port():
    """A program listening on 0.0.0.0, as Docker-published ports and most dev servers do."""
    with socket.socket() as blocker:
        blocker.bind(("0.0.0.0", 0))
        blocker.listen()
        yield blocker.getsockname()[1]


def test_a_wildcard_listener_counts_as_busy(tmp_path, monkeypatch, wildcard_port):
    free = _free_ports(1)[0]
    monkeypatch.setattr(server, "PORTS", (wildcard_port, free))
    assert server.in_use(wildcard_port) and not server.in_use(free) and not server.in_use(0)
    instance = _center(tmp_path)
    http_server = make_server(instance, None)
    try:
        assert instance.port == free
    finally:
        http_server.server_close()


def test_a_second_up_with_work_to_do_says_it_did_nothing(quiet_main, project_dir, capsys):
    from agentbreeder.center.instance import SingleInstance

    first = SingleInstance(server.center_directory())
    first.acquire()
    first.publish("http://127.0.0.1:8085")
    try:
        assert server.main(arguments(dir=project_dir, yes=True)) == 1
        captured = capsys.readouterr()
        assert "already running: http://127.0.0.1:8085" in captured.out
        assert "Nothing else was done" in captured.err
        first.withdraw()
        assert server.main(arguments()) == 0
        assert "starting or stopping" in capsys.readouterr().out
    finally:
        first.release()


def test_up_checks_for_a_running_console_before_docker(monkeypatch, tmp_path, capsys):
    from agentbreeder import doctor
    from agentbreeder.center.instance import SingleInstance
    from agentbreeder.commands import up

    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "center"))
    monkeypatch.setattr(doctor, "check_docker", lambda: pytest.fail("ran the Docker checks"))
    first = SingleInstance(server.center_directory())
    first.acquire()
    first.publish("http://127.0.0.1:8086")
    try:
        args = Namespace(dir=None, environment="dev", port=None, no_browser=True, yes=False)
        assert up.run(args) == 0
        assert "http://127.0.0.1:8086" in capsys.readouterr().out
    finally:
        first.release()


def test_up_reports_a_failed_exec(monkeypatch, tmp_path, capsys):
    from agentbreeder import doctor
    from agentbreeder.center import launch
    from agentbreeder.commands import up

    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "center"))
    monkeypatch.setattr(doctor, "check_docker", lambda: [])
    monkeypatch.setattr(doctor, "check_compose", lambda: [])

    def missing(path, argv):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(launch.os, "execv", missing)
    args = Namespace(dir=None, environment="dev", port=None, no_browser=True, yes=False)
    assert up.run(args) == 2
    assert "Cannot start the console" in capsys.readouterr().err


def test_the_server_refuses_a_different_installed_version(capsys):
    from agentbreeder.center import __main__ as entry

    assert entry.run(["--expect-version=0.0.0", "--no-browser"]) == 2
    assert "pip install -e ." in capsys.readouterr().err


@pytest.mark.parametrize("port", ["-1", "70000"])
def test_ports_out_of_range_are_refused(port):
    from agentbreeder.center import __main__ as entry
    from agentbreeder.commands import up

    with pytest.raises(SystemExit):
        entry.parse(["--port", port])
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        up._port(port)


def test_a_bad_remembered_port_falls_back_to_8080(tmp_path):
    instance = _center(tmp_path)
    instance.state.set_setting("port", "99999")
    assert server.port_candidates(instance, None)[0][0] == server.PORTS[0]


def test_a_broken_database_is_one_line(quiet_main, capsys):
    (server.center_directory() / "center.sqlite3").write_bytes(b"not a database" * 100)
    assert server.main(arguments()) == 2
    err = capsys.readouterr().err
    assert "Cannot open the console database" in err and "Traceback" not in err


def test_a_symlinked_lock_is_one_line(quiet_main, tmp_path, capsys):
    directory = server.center_directory()
    (directory / "up.lock").symlink_to(tmp_path / "elsewhere")
    assert server.main(arguments()) == 2
    assert "Cannot use the console directory" in capsys.readouterr().err


def test_the_login_temp_file_is_a_leftover(tmp_path):
    from agentbreeder.center.instance import remove_leftovers

    (tmp_path / ".login.html.tmp").write_text("token")
    assert remove_leftovers(tmp_path) == [".login.html.tmp"]


def test_a_stop_before_serving_still_shuts_down(quiet_main, monkeypatch):
    stopped = []
    monkeypatch.setattr(
        Center, "mint_login", lambda self: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    monkeypatch.setattr(jobs.Runner, "shutdown", lambda self, grace=5.0: stopped.append(1) or [])
    assert server.main(arguments()) == 0
    assert stopped == [1]


def test_redact_catches_json_escaped_and_url_passwords():
    import json as json_module

    value = 'se"cret\\value-123'
    line = f"payload {json_module.dumps({'k': value})} url https://user:hunter2pw@host/x"
    out = redact(line, [value])
    assert value not in out and json_module.dumps(value)[1:-1] not in out
    assert "hunter2pw" not in out and "[redacted]" in out
    assert redact("abc used", ["abc"]) == "abc used"  # under 4 characters: kept (shared rule)


def test_back_to_back_stop_signals_cannot_deadlock_the_flag():
    """The handler's flag takes no lock: re-entering it mid-set must not block."""
    import threading as threads

    assert type(server._stopping) is server._Flag
    assert not isinstance(server._stopping, threads.Event)  # its set() takes a lock
    server._stopping.clear()
    server._stopping.set()
    server._stopping.set()  # a threading.Event here could block forever if re-entered
    assert server._stop(15, None) is None
    server._stopping.clear()


# -- review round 2 on up (Fable) -----------------------------------------------------------------


def test_the_server_refuses_another_tree_at_the_same_version(capsys):
    from agentbreeder import __version__
    from agentbreeder.center import __main__ as entry

    argv = [f"--expect-version={__version__}", "--expect-package=/somewhere/else", "--no-browser"]
    assert entry.run(argv) == 2
    assert "not the same code" in capsys.readouterr().err


def test_a_port_that_times_out_counts_as_busy(monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(server.socket, "create_connection", timeout)
    assert server.in_use(8080)

    def refused(*args, **kwargs):
        raise ConnectionRefusedError()

    monkeypatch.setattr(server.socket, "create_connection", refused)
    assert not server.in_use(8080)


def test_a_stop_before_the_bind_is_quiet(quiet_main, monkeypatch, capsys):
    monkeypatch.setattr(
        server, "make_server", lambda center, port: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    assert server.main(arguments()) == 130
    assert "Traceback" not in capsys.readouterr().err


def test_remembering_the_port_is_best_effort(tmp_path, monkeypatch):
    import sqlite3

    instance = _center(tmp_path)

    def locked(self, key, value):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(CenterState, "set_setting", locked)
    http_server = make_server(instance, None)
    http_server.server_close()


def test_a_bad_dir_never_publishes_the_url(quiet_main, tmp_path):
    from agentbreeder.center.instance import LOCK_NAME

    assert server.main(arguments(dir=tmp_path / "missing")) == 2
    assert (server.center_directory() / LOCK_NAME).read_bytes() == b""


def test_used_by_route(center, cookie, project):
    path = f"/api/projects/{project['id']}/used-by"
    status, data, _ = api(center, "GET", path + "?ref=not%20a%20ref", cookie=cookie)
    assert status == 200 and data["data"] is None and data["error"]["code"]


def test_the_process_ignores_stop_signals_after_a_clean_stop(quiet_main, monkeypatch):
    import signal as signals

    saved = {n: signals.getsignal(n) for n in (signals.SIGTERM, signals.SIGHUP)}
    try:
        assert server.serve_process(arguments()) == 0
        assert signals.getsignal(signals.SIGTERM) is signals.SIG_IGN
        assert signals.getsignal(signals.SIGHUP) is signals.SIG_IGN
    finally:
        for number, handler in saved.items():
            signals.signal(number, handler)


def test_used_by_route_answers_files_and_deployments(center, cookie, project, tmp_path):
    path = f"/api/projects/{project['id']}/used-by?ref=runtime/adk@1.0.0"
    status, data, _ = api(center, "GET", path, cookie=cookie)
    assert status == 200 and data["data"]["deployments"]["error"] is None
    assert data["data"]["deployments"]["data"]["ref"] == "runtime/adk@1.0.0"
    # `used-by` exits 2 when a project file has an error but still prints its answer.
    (tmp_path / "used-by-errors").write_text("")
    center.runner.cache.clear()
    status, data, _ = api(center, "GET", path, cookie=cookie)
    assert data["data"]["deployments"]["error"] is None


# -- a cancel during the child's startup is never lost --------------------------------------------

LATE_HANDLER = """
import signal, sys, time
signal.signal(signal.SIGINT, signal.SIG_IGN)  # like a SIGINT swallowed while starting
time.sleep(0.5)
signal.signal(signal.SIGINT, signal.default_int_handler)
try:
    print("started", flush=True)
    time.sleep(30)
except KeyboardInterrupt:
    print("interrupted", flush=True)
    sys.exit(130)
"""


def _late_runner(tmp_path, source=LATE_HANDLER):
    script = tmp_path / "late.py"
    script.write_text(source)
    directory = tmp_path / "late-center"
    directory.mkdir()
    return Runner(CenterState(directory), directory, [sys.executable, str(script)])


def _ends(runner, job, limit):
    deadline = time.monotonic() + limit
    while runner.running():
        assert time.monotonic() < deadline, "the cancel was lost"
        time.sleep(0.02)
    return runner.state.job(job)


def test_a_cancel_before_the_first_line_waits_for_it(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "STARTUP_GRACE", 30.0)  # only the first line may release it
    runner = _late_runner(tmp_path)
    job = runner.start({"id": "p", "path": "/x", "environment": "dev"}, "cluster-down", {})
    time.sleep(0.2)  # the child is up but cannot take SIGINT yet (an immediate send is lost)
    assert runner.cancel(job) is True
    row = _ends(runner, job, 20)
    assert row["exit_code"] == 130 and [line["text"] for line in row["lines"]] == [
        "started",
        "interrupted",
    ]


def test_the_sigint_settles_after_the_first_line(tmp_path, monkeypatch):
    """Not right on the first line: a CLI goes from printing it into a blocking call, and a
    SIGINT landing in that gap surfaces only when the call returns."""
    monkeypatch.setattr(jobs, "STARTUP_GRACE", 30.0)
    runner = _late_runner(tmp_path)
    times = {}
    real_first = runner._first_output

    def first(identifier):
        times["line"] = time.monotonic()
        real_first(identifier)

    runner._first_output = first
    real_signal = jobs._signal

    def recording(process, number):
        times.setdefault("sigint", time.monotonic())
        real_signal(process, number)

    monkeypatch.setattr(jobs, "_signal", recording)
    job = runner.start({"id": "p", "path": "/x", "environment": "dev"}, "cluster-down", {})
    runner.cancel(job)
    assert _ends(runner, job, 20)["exit_code"] == 130
    assert times["sigint"] - times["line"] >= jobs.CANCEL_SETTLE * 0.9


SILENT = """
import pathlib, sys, time
try:
    pathlib.Path(sys.argv[1]).write_text("ready")
    time.sleep(30)
except KeyboardInterrupt:
    raise SystemExit(130)
"""


def test_a_silent_child_still_gets_the_cancel_after_the_startup_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "STARTUP_GRACE", 2.0)
    marker = tmp_path / "ready"
    script = tmp_path / "silent.py"
    script.write_text(SILENT)
    directory = tmp_path / "silent-center"
    directory.mkdir()
    runner = Runner(CenterState(directory), directory, [sys.executable, str(script), str(marker)])
    started = time.monotonic()
    job = runner.start({"id": "p", "path": "/x", "environment": "dev"}, "cluster-down", {})
    deadline = time.monotonic() + 20
    while not marker.exists():  # its handler is in place; it never prints
        assert time.monotonic() < deadline
        time.sleep(0.02)
    runner.cancel(job)
    assert _ends(runner, job, 20)["exit_code"] == 130
    assert time.monotonic() - started >= 2.0 * 0.9  # not before the grace


def test_cancel_sends_one_sigint_only(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "STARTUP_GRACE", 30.0)
    runner = _late_runner(tmp_path)
    job = runner.start({"id": "p", "path": "/x", "environment": "dev"}, "cluster-down", {})
    sent = []
    real = jobs._signal
    monkeypatch.setattr(
        jobs, "_signal", lambda process, number: sent.append(number) or real(process, number)
    )
    runner.cancel(job)
    runner.cancel(job)
    _ends(runner, job, 20)
    assert sent.count(jobs.signal.SIGINT) == 1


SLOW_RESTORE = """
import sys, time
try:
    print("started", flush=True)
    time.sleep(30)
except KeyboardInterrupt:
    time.sleep(1.5)  # a restore still running when shutdown comes
    sys.exit(130)
"""


def _count_sigints(monkeypatch) -> list:
    sent = []
    real = jobs._signal
    monkeypatch.setattr(
        jobs, "_signal", lambda process, number: sent.append(number) or real(process, number)
    )
    return sent


def test_shutdown_adds_no_second_sigint_after_a_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "STARTUP_GRACE", 30.0)
    runner = _late_runner(tmp_path, SLOW_RESTORE)
    job = runner.start({"id": "p", "path": "/x", "environment": "dev"}, "cluster-down", {})
    deadline = time.monotonic() + 20
    while not runner.state.job(job)["lines"]:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    sent = _count_sigints(monkeypatch)
    runner.cancel(job)
    time.sleep(jobs.CANCEL_SETTLE + 0.2)  # the SIGINT went; the restore is still running
    assert [entry["id"] for entry in runner.running()] == [job]
    runner.shutdown(grace=5.0)
    assert sent.count(jobs.signal.SIGINT) == 1
    assert runner.state.job(job)["exit_code"] == 130  # its restore was not cut short


def test_shutdown_during_a_deferred_cancel_sends_one_sigint(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "STARTUP_GRACE", 30.0)
    runner = _late_runner(tmp_path, SLOW_RESTORE)
    sent = _count_sigints(monkeypatch)
    job = runner.start({"id": "p", "path": "/x", "environment": "dev"}, "cluster-down", {})
    runner.cancel(job)  # deferred: no output yet
    runner.shutdown(grace=5.0)
    time.sleep(jobs.CANCEL_SETTLE + 0.3)  # a deferred timer, if any, would fire now
    assert sent.count(jobs.signal.SIGINT) <= 1


IGNORES_SIGINT = """
import signal, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
print("started", flush=True)
time.sleep(30)
"""


def test_a_cancel_that_does_not_end_the_job_can_be_escalated(center, cookie, project, monkeypatch):
    monkeypatch.setattr(jobs, "STOP_AFTER", 0.5)
    script = center.directory.parent / "stubborn.py"
    script.write_text(IGNORES_SIGINT)
    center.runner.prefix = [sys.executable, str(script)]
    job = _job_id(start(center, project, cookie, "build"))
    _started(center, job)
    assert api(center, "POST", f"/api/jobs/{job}/stop", {}, cookie)[1]["data"] == {
        "stopped": False  # no Cancel yet
    }
    api(center, "POST", f"/api/jobs/{job}/cancel", {}, cookie)
    _, row, _ = api(center, "GET", f"/api/jobs/{job}", cookie=cookie)
    assert row["data"]["cancelling"] is True and row["data"]["can_stop"] is False
    assert center.runner.running()[0]["cancelling"] is True
    time.sleep(0.6)
    _, row, _ = api(center, "GET", f"/api/jobs/{job}", cookie=cookie)
    assert row["data"]["can_stop"] is True
    assert api(center, "POST", f"/api/jobs/{job}/stop", {}, cookie)[1]["data"] == {"stopped": True}
    assert wait(center, job, cookie)["exit_code"] == -jobs.signal.SIGTERM


# -- test cleanup: a stuck console cannot outlive its test ----------------------------------------


def test_stop_group_kills_a_process_that_ignores_sigterm():
    import subprocess

    code = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(60)"
    )
    stubborn = subprocess.Popen(
        [sys.executable, "-c", code], start_new_session=True, stdout=subprocess.PIPE
    )
    try:
        assert stubborn.stdout.readline() == b"ready\n"  # its SIGTERM handler is in place
        started = time.monotonic()
        _stop_group(stubborn.pid, _popen_ended(stubborn), grace=0.5)
        assert stubborn.returncode == -9  # SIGKILL, after the ignored SIGTERM
        assert time.monotonic() - started < 5
    finally:
        if stubborn.poll() is None:
            stubborn.kill()
            stubborn.wait()
        stubborn.stdout.close()


def test_stop_group_is_a_no_op_for_an_ended_or_missing_process():
    import subprocess

    ended = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    ended.wait()
    _stop_group(ended.pid, _popen_ended(ended))
    _stop_group(None, lambda timeout: True)
    assert ended.returncode == 0


def test_stop_group_kills_and_reaps_a_forked_child_that_ignores_sigterm():
    import signal as signals

    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            signals.signal(signals.SIGTERM, signals.SIG_IGN)
            os.write(write, b"r")
            time.sleep(60)
        finally:
            os._exit(0)
    os.close(write)
    try:
        assert os.read(read, 1) == b"r"  # its SIGTERM handler is in place
        _stop_group(pid, _forked_ended(pid), grace=0.5)
        with pytest.raises(ChildProcessError):  # reaped
            os.waitpid(pid, os.WNOHANG)
    finally:
        os.close(read)
        try:
            os.kill(pid, signals.SIGKILL)
            os.waitpid(pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass


def test_stop_group_reaps_a_forked_child_that_already_exited():
    """A group left with only a zombie leader (macOS refuses to signal it): still reaped."""
    pid = os.fork()
    if pid == 0:
        os.setsid()
        os._exit(0)
    time.sleep(0.3)  # it has exited and is now a zombie
    _stop_group(pid, _forked_ended(pid))
    with pytest.raises(ChildProcessError):  # reaped
        os.waitpid(pid, os.WNOHANG)


# -- the guided path (#820): `next --json` as the guide view, and Create starter ------------------

GUIDES = Path(__file__).parent / "ui" / "fixtures" / "guide"


def _guide(tmp_path, name="services", edit=None):
    text = (GUIDES / f"{name}.json").read_text()
    if edit:
        data = json.loads(text)
        edit(data)
        text = json.dumps(data)
    (tmp_path / "guide.json").write_text(text)


def test_guide_view_runs_next_with_fixed_arguments_and_validates_it(
    center, project, cookie, tmp_path
):
    _guide(tmp_path)
    status, data, _ = api(center, "GET", f"/api/projects/{project['id']}/guide", cookie=cookie)
    assert status == 200 and data["error"] is None
    guide = data["data"]
    assert guide["project"] == project["path"] and guide["next"] == "services"
    assert [step["id"] for step in guide["steps"]][-1] == "try"
    argv = json.loads((tmp_path / "next-argv").read_text())
    assert argv == ["next", project["path"], "--environment", "dev", "--json"]


def test_guide_view_refuses_output_that_breaks_the_contract(center, project, cookie, tmp_path):
    _guide(tmp_path, edit=lambda data: data.update(complete=True))
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/guide", cookie=cookie)
    assert data["data"] is None and data["error"]["code"] == "GUIDE_INVALID"


def test_guide_view_refuses_another_projects_answer(center, project, cookie, tmp_path):
    other = json.loads((GUIDES / "services.json").read_text().replace("@PROJECT@", "/elsewhere"))
    (tmp_path / "guide.json").write_text(json.dumps(other))
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/guide", cookie=cookie)
    assert data["data"] is None and data["error"]["code"] == "GUIDE_INVALID"


def test_guide_view_keeps_the_last_good_answer_as_stale(center, project, cookie, tmp_path):
    _guide(tmp_path)
    path = f"/api/projects/{project['id']}/guide"
    assert api(center, "GET", path, cookie=cookie)[1]["error"] is None
    (tmp_path / "guide.json").unlink()
    _, data, _ = api(center, "GET", path, cookie=cookie)
    assert data["stale"] is True and data["error"]["code"] == "NEXT_FAILED"
    assert data["data"]["next"] == "services"


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir(mode=0o755)
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: path))
    return path


def test_create_starter_runs_new_agent_into_the_fixed_folder_and_registers_it(
    center, cookie, home, tmp_path
):
    status, data, _ = api(center, "POST", "/api/projects", {"starter": "hello"}, cookie)
    assert status == 200, data
    destination = home / "agentbreeder-projects" / "hello"
    assert data["data"]["path"] == str(destination.resolve())
    argv = json.loads((tmp_path / "new-argv").read_text())
    assert argv == [
        "new",
        "agent",
        "hello",
        "--model",
        "gemini",
        "--dir",
        str(destination),
        "--json",
    ]


@pytest.mark.parametrize(
    "name", ["", "Hello", "1abc", "a" * 32, "../x", "hello-", "-x", "a b", 7, None, ["hello"]]
)
def test_create_starter_refuses_a_bad_name_before_running_anything(
    center, cookie, home, tmp_path, name
):
    status, data, _ = api(center, "POST", "/api/projects", {"starter": name}, cookie)
    assert status == 400
    assert not (tmp_path / "new-argv").exists()
    assert not (home / "agentbreeder-projects").exists()


def test_create_starter_shows_new_agents_own_coded_error(center, cookie, home, tmp_path):
    (tmp_path / "new-refuse").write_text("")
    status, data, _ = api(center, "POST", "/api/projects", {"starter": "hello"}, cookie)
    assert status == 400 and data["error"]["code"] == "DIR_NOT_EMPTY"
    assert "\x1b" not in data["error"]["message"] and "is not empty" in data["error"]["message"]
    assert server.CenterState is not None and center.state.projects() == []


def test_create_starter_checks_the_folder_before_creating_anything(center, cookie, home, tmp_path):
    home.chmod(0o777)
    status, data, _ = api(center, "POST", "/api/projects", {"starter": "hello"}, cookie)
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"
    assert not (home / "agentbreeder-projects").exists()
    assert not (tmp_path / "new-argv").exists()


def test_the_starter_name_rule_is_new_agents():
    from agentbreeder import newagent

    assert server.STARTER_NAME.pattern == newagent.NAME_PATTERN


def test_the_browser_starter_name_rule_is_new_agents():
    from agentbreeder import newagent

    source = (server.STATIC / "lib" / "guide.js").read_text()
    assert f"const STARTER_NAME = /{newagent.NAME_PATTERN}/;" in source


def test_a_cached_answer_for_another_project_is_never_served_as_stale(
    center, project, cookie, tmp_path
):
    # The runner caches what `next` printed before the server checks it; the stale fallback must
    # be checked again, not passed through.
    other = (GUIDES / "services.json").read_text().replace("@PROJECT@", "/elsewhere")
    (tmp_path / "guide.json").write_text(other)
    path = f"/api/projects/{project['id']}/guide"
    assert api(center, "GET", path, cookie=cookie)[1]["error"]["code"] == "GUIDE_INVALID"
    (tmp_path / "guide.json").unlink()  # the next run fails: the runner falls back to its cache
    _, data, _ = api(center, "GET", path, cookie=cookie)
    assert data["data"] is None and data["error"]["code"] == "GUIDE_INVALID"
    assert "/elsewhere" not in json.dumps(data)


# -- the registry browser (#824) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "registry/show?ref=nope",
        "registry/show",
        "registry/diff?from=prompt/a@1.0.0&to=bad",
        "registry/used-by?ref=tool%2Fx%40latest",
    ],
)
def test_registry_routes_refuse_a_bad_ref(center, cookie, project, path):
    status, data, _ = api(center, "GET", f"/api/projects/{project['id']}/{path}", cookie=cookie)
    assert status == 400 and data["error"]["code"] == "REF_INVALID"
    assert not data["error"]["message"].startswith("[")


def test_registry_show_and_diff_pass_canonical_refs(center, cookie, project, monkeypatch):
    calls = []

    def run_cli(argv, **options):
        calls.append(argv)
        return {"data": {"argv": argv}, "error": None, "stale": False}

    monkeypatch.setattr(center.reads, "run_cli", run_cli)
    base = f"/api/projects/{project['id']}/registry"
    status, data, _ = api(center, "GET", base + "/show?ref=prompt%2Fp%401.0.0", cookie=cookie)
    assert status == 200
    assert data["data"]["argv"] == [
        "registry",
        "show",
        "prompt/p@1.0.0",
        "--project",
        project["path"],
        "--json",
    ]
    path = base + "/diff?from=prompt/p@1.0.0&to=prompt/p@1.0.1"
    status, data, _ = api(center, "GET", path, cookie=cookie)
    assert status == 200 and data["data"]["argv"][:4] == [
        "registry",
        "diff",
        "prompt/p@1.0.0",
        "prompt/p@1.0.1",
    ]
    # Two names: the CLI's own refusal comes back as the envelope's error.
    monkeypatch.setattr(
        center.reads,
        "run_cli",
        lambda argv, **options: {
            "data": None,
            "error": {"code": "REF_INVALID", "message": "m"},
            "stale": False,
        },
    )
    path = base + "/diff?from=prompt/p@1.0.0&to=prompt/q@1.0.1"
    status, data, _ = api(center, "GET", path, cookie=cookie)
    assert status == 200 and data["error"]["code"] == "REF_INVALID"


def test_registry_used_by_reads_every_registered_project_of_the_environment(
    center, cookie, project, tmp_path, monkeypatch
):
    other = tmp_path / "other"
    other.mkdir()
    (other / "agent.yaml").write_text("kind: agent\n")
    (other / "stack.yaml").write_text("kind: stack\n")
    status, _, _ = api(center, "POST", "/api/projects", {"path": str(other)}, cookie)
    assert status == 200
    calls = []
    real = center.runner.read

    def read(argv, **options):
        calls.append((argv, options))
        return real(argv, **options)

    monkeypatch.setattr(center.runner, "read", read)
    path = f"/api/projects/{project['id']}/registry/used-by?ref=tool/orders@1.0.0"
    status, data, _ = api(center, "GET", path, cookie=cookie)
    assert status == 200 and data["error"] is None
    assert data["data"]["ref"] == "tool/orders@1.0.0"
    argv, options = calls[-1]
    assert argv == [
        "registry",
        "used-by",
        "tool/orders@1.0.0",
        "--project",
        project["path"],
        "--project",
        str(other.resolve()),
        "--json",
    ]
    assert options == {"ok_codes": (0, 2)}


def test_the_registry_view_carries_roots_and_in_use(center, cookie, project):
    status, data, _ = api(center, "GET", f"/api/projects/{project['id']}/registry", cookie=cookie)
    assert status == 200
    view = data["data"]
    assert view["roots"][0]["kind"] == "project" and view["roots"][-1]["kind"] == "builtin"
    assert view["in_use"]["error"] is None and isinstance(view["in_use"]["data"]["refs"], dict)


# -- logs (#821, M5 console half): one snapshot read per request, never cached -------------------

DEPLOYMENT_ID = "0123456789abcdef0123456789abcdef"


def _logs(tmp_path, **fields):
    body = {
        "schema_version": 1,
        "source": "service",
        "target": "local",
        "name": "litellm",
        "lines": ["INFO: started", "ERROR: boom \\x1b[31m"],
        "levels": ["info", "error"],
        "format": "python",
        "level": None,
        "truncated": False,
        **fields,
    }
    (tmp_path / "logs.json").write_text(json.dumps(body))
    return body


def test_service_logs_run_services_logs_with_fixed_arguments(center, project, cookie, tmp_path):
    _logs(tmp_path)
    path = f"/api/projects/{project['id']}/logs/service/litellm?tail=1000&since=10m"
    status, data, _ = api(center, "GET", path, cookie=cookie)
    assert status == 200 and data["error"] is None, data
    assert data["data"]["lines"] == ["INFO: started", "ERROR: boom \\x1b[31m"]
    assert data["data"]["levels"] == ["info", "error"] and data["data"]["format"] == "python"
    argv = json.loads((tmp_path / "logs-argv").read_text())
    assert argv == [
        "services", "logs", project["path"], "litellm",
        "--tail", "1000", "--since", "10m", "--json",
    ]  # fmt: skip


def test_deployment_logs_run_logs_against_the_projects_stack(center, project, cookie, tmp_path):
    _logs(
        tmp_path,
        source="agent",
        target="kubernetes",
        name="demo",
        deployment=DEPLOYMENT_ID,
        previous=True,
        pod_reason=None,
    )
    path = f"/api/projects/{project['id']}/logs/deployment/{DEPLOYMENT_ID}?previous=1"
    status, data, _ = api(center, "GET", path, cookie=cookie)
    assert status == 200 and data["error"] is None, data
    assert data["data"]["source"] == "deployment" and data["data"]["previous"] is True
    argv = json.loads((tmp_path / "logs-argv").read_text())
    assert argv == [
        "logs", DEPLOYMENT_ID, "--stack", project["path"], "--environment", "dev",
        "--previous", "--tail", "200", "--json",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "suffix, code",
    [
        ("service/litellm?tail=0", "TAIL_INVALID"),
        ("service/litellm?tail=10001", "TAIL_INVALID"),
        ("service/litellm?tail=-5", "TAIL_INVALID"),
        ("service/litellm?tail=12abc", "TAIL_INVALID"),
        ("service/litellm?since=--follow", "SINCE_INVALID"),
        ("service/litellm?since=10d", "SINCE_INVALID"),
        ("service/litellm?previous=1", "PREVIOUS_INVALID"),
    ],
)
def test_log_reads_refuse_bad_options_before_running_anything(
    center, project, cookie, tmp_path, suffix, code
):
    status, data, _ = api(
        center, "GET", f"/api/projects/{project['id']}/logs/{suffix}", cookie=cookie
    )
    assert status == 400 and data["error"]["code"] == code
    assert not (tmp_path / "logs-argv").exists()


@pytest.mark.parametrize(
    "suffix",
    [
        "service/Lite",
        "service/a..b",
        "service/-x",
        "deployment/d1",
        "deployment/" + "A" * 32,
        "other/x",
    ],
)
def test_log_routes_take_only_a_service_name_or_a_deployment_id(
    center, project, cookie, tmp_path, suffix
):
    status, _, _ = api(
        center, "GET", f"/api/projects/{project['id']}/logs/{suffix}", cookie=cookie
    )
    assert status == 404
    assert not (tmp_path / "logs-argv").exists()


def test_a_log_read_needs_a_session(center, project, tmp_path):
    _logs(tmp_path)
    status, _, _ = api(center, "GET", f"/api/projects/{project['id']}/logs/service/litellm")
    assert status == 401
    assert not (tmp_path / "logs-argv").exists()


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "phoenix"},  # another service's answer
        {"source": "agent"},
        {"lines": "not a list"},
        {"lines": ["a", 3], "levels": [None, None]},
        {"levels": ["info"]},  # not one per line
        {"levels": ["info", "loud"]},
        {"format": 7},
    ],
)
def test_a_log_answer_that_breaks_the_contract_is_refused(
    center, project, cookie, tmp_path, fields
):
    _logs(tmp_path, **fields)
    path = f"/api/projects/{project['id']}/logs/service/litellm"
    _, data, _ = api(center, "GET", path, cookie=cookie)
    assert data["data"] is None and data["error"]["code"] == "LOGS_INVALID"


def test_a_log_answer_passes_only_known_keys(center, project, cookie, tmp_path):
    _logs(tmp_path, extra="/home/someone/secret")
    _, data, _ = api(
        center, "GET", f"/api/projects/{project['id']}/logs/service/litellm", cookie=cookie
    )
    assert "extra" not in data["data"] and "/home/someone" not in json.dumps(data)


def test_log_reads_are_never_cached(center, project, cookie, tmp_path):
    # A failed read shows its own coded error (LOG_UNAVAILABLE is the viewer's empty state), never
    # the lines an earlier read returned.
    _logs(tmp_path)
    path = f"/api/projects/{project['id']}/logs/service/litellm"
    assert api(center, "GET", path, cookie=cookie)[1]["error"] is None
    (tmp_path / "logs-error").write_text("[LOG_UNAVAILABLE] the pod's log is gone\n")
    _, data, _ = api(center, "GET", path, cookie=cookie)
    assert data["data"] is None and data["stale"] is False
    assert data["error"]["code"] == "LOG_UNAVAILABLE"
    assert "INFO: started" not in json.dumps(data)
    assert not center.runner.cache


def test_the_log_patterns_are_the_clis():
    from agentbreeder import runtime
    from agentbreeder.services import schema

    assert server.LOG_SINCE.pattern == runtime.SINCE.pattern
    assert server.MAX_LOG_TAIL == runtime.MAX_TAIL
    assert (
        server.SERVICE == f"(?P<service>{schema.SERVICE_NAME.removeprefix('^').removesuffix('$')})"
    )


def test_the_browser_log_patterns_are_the_clis():
    from agentbreeder import loglevels, runtime

    source = (server.STATIC / "lib" / "logs.js").read_text()
    assert f"export const SINCE = /^(?:{runtime.SINCE.pattern})$/;" in source
    assert f"export const LEVELS = {json.dumps(list(loglevels.LEVELS))};" in source
    assert f"export const LEVEL_CHOICES = {json.dumps(list(loglevels.CHOICES))};" in source
