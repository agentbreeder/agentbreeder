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
    return data["session"]


@pytest.fixture
def project(center, project_dir, cookie):
    status, data, _ = api(center, "POST", "/api/projects", {"path": str(project_dir)}, cookie)
    assert status == 200
    return data


def wait(center, job, cookie):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        _, data, _ = api(center, "GET", f"/api/jobs/{job}", cookie=cookie)
        if data["state"] != "running":
            return data
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
    assert status == 200 and data["session"] and response.getheader("Set-Cookie") is None
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
    assert api(center, "POST", "/api/projects", big, cookie)[0] == 403


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
    assert "/tmp/fake-state" in data["rows"]["data"]["args"]


def test_failed_view_reports_the_error_then_last_known_data(center, project, cookie, tmp_path):
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/cluster", cookie=cookie)
    assert data == {"error": "[STACK_NOT_UP] nope"}
    runner = center.runner
    assert runner.read(["list"], key="k") == {"data": [{"id": "1"}]}
    stale = runner._stale("k", "busy")
    assert stale["stale"] is True and stale["data"] == [{"id": "1"}]
    assert "error" in runner.read(["garbage"])


def test_unknown_routes_and_projects(center, cookie):
    assert api(center, "GET", "/api/other", cookie=cookie)[0] == 404
    assert api(center, "POST", "/api/other", {}, cookie)[0] == 404
    assert api(center, "GET", "/api/projects/000000000000/services", cookie=cookie)[0] == 400
    assert api(center, "GET", "/api/jobs/" + "0" * 32, cookie=cookie)[0] == 404


def test_overview(center, cookie):
    status, data, _ = api(center, "GET", "/api/overview", cookie=cookie)
    assert status == 200 and data["job"] is None and "version" in data


# -- jobs -----------------------------------------------------------------------------------------


def test_services_job_needs_the_reviewed_grant(center, project, cookie):
    assert start(center, project, cookie, "services-up")[0] == 400
    assert start(center, project, cookie, "services-up", {"digest": "b" * 64})[0] == 400


def test_services_job_passes_keys_privately_and_redacts_them(center, project, cookie):
    status, data, _ = start(
        center, project, cookie, "services-up", {"digest": DIGEST}, {"GEMINI_API_KEY": KEY_VALUE}
    )
    assert status == 200
    job = wait(center, data["job"], cookie)
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
    assert status == 400 and "MCP" in data["error"]


def test_one_job_at_a_time_and_cancel(center, project, cookie):
    status, data, _ = start(center, project, cookie, "build")
    assert status == 200
    job = data["job"]
    deadline = time.monotonic() + 15
    while not center.state.job(job)["lines"]:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    busy = start(center, project, cookie, "release")
    assert busy[0] == 409 and busy[1]["job"] == job
    _, cancelled, _ = api(center, "POST", f"/api/jobs/{job}/cancel", {}, cookie)
    assert cancelled == {"cancelled": True}
    finished = wait(center, job, cookie)
    assert finished["state"] == "failed" and finished["exit_code"] == 130
    assert api(center, "POST", f"/api/jobs/{job}/cancel", {}, cookie)[1] == {"cancelled": False}


def test_job_exit_codes_and_line_limit(center, project, cookie):
    job = wait(center, start(center, project, cookie, "release")[1]["job"], cookie)
    assert job["state"] == "failed" and job["exit_code"] == 5
    assert all(len(line["text"]) <= jobs.MAX_LINE for line in job["lines"])
    job = wait(center, start(center, project, cookie, "promote")[1]["job"], cookie)
    assert job["state"] == "waiting_approval"
    data = api(center, "GET", f"/api/jobs/{job['id']}?after=999", cookie=cookie)[1]
    assert data["lines"] == []


def test_request_cannot_supply_an_env_file(center, project, cookie, tmp_path):
    options = {"digest": DIGEST, "env_file": str(tmp_path / "secrets.env")}
    job = wait(center, start(center, project, cookie, "services-up", options)[1]["job"], cookie)
    assert not any("envfile" in line["text"] for line in job["lines"])


def test_a_project_keeps_its_environment(center, project, cookie, project_dir):
    center.environment = "prod"
    status, data, _ = api(center, "POST", "/api/projects", {"path": str(project_dir)}, cookie)
    assert status == 400 and "registered for dev" in data["error"]


def test_key_values_are_quoted_for_the_env_parser():
    assert jobs._dotenv_quote("a b") == "'a b'"
    assert jobs._dotenv_quote("it's") == '"it\'s"'
    with pytest.raises(BadRequest):
        jobs._dotenv_quote("a'b\"c")


def test_odd_numbers_do_not_crash_the_server(center, cookie, project, project_dir):
    job = start(center, project, cookie, "services-down")[1]["job"]
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
