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
    elif argv[0] == "registry":
        print("[]")
    elif argv[0] == "list":
        print(json.dumps([{{"id": "1"}}]))
    elif argv[:2] == ["audit", "list"] or argv[:2] == ["audit", "verify"]:
        print(json.dumps({{"args": argv}}))
    elif argv[0] == "doctor":
        print("[]")
    elif argv[0] == "cluster":
        sys.stderr.write("[STACK_NOT_UP] nope\\n")
        sys.exit(2)
    elif argv[0] == "build":
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
    assert response.status == 200 and b"Command Center" in raw
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


@pytest.mark.parametrize("view", ["services", "registry", "deployments", "audit", "cluster"])
def test_views(center, project, cookie, view):
    status, data, _ = api(center, "GET", f"/api/projects/{project['id']}/{view}", cookie=cookie)
    assert status == 200 and data


def test_audit_reads_the_stack_state_dir(center, project, cookie):
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/audit", cookie=cookie)
    assert "/tmp/fake-state" in data["data"]["rows"]["data"]["args"]


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
    assert status == 200 and data["data"]["job"] is None and "version" in data["data"]


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
    assert "echo ***" in text and "b64 ***" in text
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


def test_ui_never_uses_inner_html():
    source = (server.STATIC / "app.js").read_text()
    assert "innerHTML" not in source and "insertAdjacentHTML" not in source


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


def test_busy_default_port_falls_back(tmp_path, monkeypatch):
    with socket.socket() as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        monkeypatch.setattr(server, "DEFAULT_PORT", blocker.getsockname()[1])
        directory = tmp_path / "c"
        directory.mkdir()
        instance = Center(directory)
        http_server = make_server(instance, None)
        try:
            assert instance.port != blocker.getsockname()[1]
        finally:
            http_server.server_close()


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
    connection.sock.shutdown(socket.SHUT_WR)
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

    def slow(*args, **kwargs):
        raise jobs.subprocess.TimeoutExpired("x", 1)

    monkeypatch.setattr(jobs.subprocess, "run", slow)
    assert runner.read(["list"])["error"]["code"] == "TIMEOUT"


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
    assert center.runner.current is None


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
