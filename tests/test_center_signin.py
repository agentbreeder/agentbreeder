"""Console sign-in (M2 #818): the password store, the sign-in API, the backoff, setup (S-28),
the terminal password commands (S-15) and the security events (S-30)."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path

import pytest

from agentbreeder.center import auth, jobs, server
from agentbreeder.center.instance import SingleInstance
from agentbreeder.center.jobs import CenterState
from agentbreeder.center.server import Center, make_server

GOOD = "correct horse battery"
OTHER = "another long passphrase"


# -- helpers


@pytest.fixture
def center(tmp_path):
    directory = tmp_path / "center"
    directory.mkdir(mode=0o700)
    instance = Center(directory, prefix=[sys.executable, "-c", "raise SystemExit(1)"])
    http_server = make_server(instance, 0)
    thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    thread.start()
    yield instance
    http_server.shutdown()
    http_server.server_close()


def call(center, method, path, body=None, session=None, **headers):
    import http.client

    sent = {
        "Host": f"127.0.0.1:{center.port}",
        "X-AgentBreeder-Center": "1",
        **({"X-AgentBreeder-Session": session} if session else {}),
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        sent |= {"Content-Type": "application/json", "Origin": center.origin}
    sent |= {key.replace("_", "-"): value for key, value in headers.items()}
    connection = http.client.HTTPConnection("127.0.0.1", center.port, timeout=30)
    connection.request(method, path, body=data, headers=sent)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw or b"{}")


def link_session(center) -> str:
    url, _ = center.mint_login()
    status, data = call(center, "POST", "/api/session", {"token": url.split("#t=")[1]})
    assert status == 200 and data["data"]["kind"] == "link"
    return data["data"]["session"]


def with_password(center, password: str = GOOD) -> None:
    center.state.set_password(auth.EMAIL, auth.hash_password(password), "password-set", "terminal")


def sign_in(center, password: str = GOOD, email: str = auth.EMAIL):
    return call(center, "POST", "/api/session", {"email": email, "password": password})


@pytest.fixture
def counted(monkeypatch):
    """Counts scrypt runs (every verify and every hash)."""
    runs = []
    real = auth._derive

    def derive(*args, **kwargs):
        runs.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(auth, "_derive", derive)
    return runs


# -- the policy and the hash


@pytest.mark.parametrize(
    "password,message",
    [
        ("", "Enter a password"),
        ("short pass", "at least 12"),
        ("x" * (auth.MAX_LENGTH + 1), "at most"),
        ("Password1234", "too common"),
        ("QWERTYUIOPAS", "too common"),
        ("aaaaaaaaaaaaaaaa", "too common"),
        (" leading space pass", "start or end"),
        ("a password with\ttab", "control"),
        ("admin@localhost", "too common"),
    ],
)
def test_the_policy_refuses(password, message):
    with pytest.raises(auth.PolicyError, match=message):
        auth.check_policy(password)


def test_the_policy_accepts_a_long_uncommon_password():
    auth.check_policy(GOOD)
    auth.check_policy("ünïcödé passphrase")


def test_the_hash_stores_its_parameters_and_verifies_in_constant_time():
    row = auth.hash_password(GOOD)
    assert (row["n"], row["r"], row["p"]) == (2**15, 8, 1)
    assert row["maxmem"] == 64 * 1024 * 1024 and len(row["salt"]) == 16
    assert len(row["hash"]) == 32 and GOOD.encode() not in row["hash"]
    assert auth.verify(GOOD, row)
    assert not auth.verify(OTHER, row)
    assert auth.hash_password(GOOD)["salt"] != row["salt"]


def test_unknown_and_damaged_rows_still_cost_one_scrypt_run(counted):
    auth._dummy_row()  # made once; not part of the counted check
    counted.clear()
    assert not auth.verify(GOOD, None)
    assert len(counted) == 1
    row = auth.hash_password(GOOD)
    counted.clear()
    for damaged in (
        {**row, "n": 3},
        {**row, "n": 2**22},
        {**row, "r": 0},
        {**row, "maxmem": 2**40},
        {**row, "hash": b"short"},
        {key: value for key, value in row.items() if key != "p"},
        {**row, "p": 16},  # within each bound, but 16 times the default work
    ):
        assert not auth.verify(GOOD, damaged)
    assert len(counted) == 7
    # A row whose maxmem is too small for its own parameters falls back to the dummy too.
    counted.clear()
    assert not auth.verify(GOOD, {**row, "maxmem": 1024 * 1024})
    assert len(counted) == 2


def test_the_backoff_starts_after_five_failures_doubles_and_caps_at_five_minutes():
    backoff = auth.Backoff()
    for _ in range(5):
        assert backoff.wait(1000.0) == 0
        backoff.failed(1000.0)
    assert backoff.wait(1000.0) == 30  # the sixth attempt waits
    assert backoff.wait(1029.5) == 1 and backoff.wait(1030.0) == 0
    waits = []
    for _ in range(10):
        backoff.failed(2000.0)
        waits.append(backoff.wait(2000.0))
    assert waits[:4] == [60, 120, 240, 300]
    assert max(waits) == auth.MAX_WAIT == 300
    backoff.succeeded()
    assert backoff.wait(2000.0) == 0


def test_session_hours_falls_back_to_the_default():
    assert auth.session_hours("4") == 4
    assert auth.session_hours(None) == auth.session_hours("5") == auth.session_hours("x") == 8


# -- the store


def test_an_a2_database_migrates_keeping_projects_with_no_password(tmp_path):
    directory = tmp_path / "c"
    directory.mkdir()
    db = sqlite3.connect(directory / "center.sqlite3")
    db.execute(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL, "
        "environment TEXT NOT NULL, accepted_grant TEXT, created REAL NOT NULL)"
    )
    db.execute("INSERT INTO projects VALUES ('0123456789ab','demo','/x','dev',NULL,1.0)")
    db.commit()
    db.close()
    state = CenterState(directory)
    assert state.migrated_from == 0
    assert [p["name"] for p in state.projects()] == ["demo"]
    assert not state.has_password()
    assert state.credential_version() == 0


def test_setting_a_password_raises_the_version_and_records_no_secret(tmp_path):
    directory = tmp_path / "c"
    directory.mkdir()
    state = CenterState(directory)
    state.set_password(auth.EMAIL, auth.hash_password(GOOD), "password-set", "terminal")
    assert state.has_password() and state.credential_version() == 1
    assert not state.set_password(
        auth.EMAIL, auth.hash_password(OTHER), "password-set", "browser", only_if_none=True
    )
    assert state.credential_version() == 1 and auth.verify(GOOD, state.user(auth.EMAIL))
    state.end_all_sessions("browser")
    assert state.credential_version() == 2
    events = state.security_events()
    assert [(e["event"], e["outcome"], e["source"]) for e in events] == [
        ("sign-out-everywhere", "ok", "browser"),
        ("password-set", "ok", "terminal"),
    ]
    dump = "\n".join(state.db.iterdump())
    assert GOOD not in dump and OTHER not in dump


def test_security_events_keep_only_the_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "KEEP_SECURITY_EVENTS", 3)
    directory = tmp_path / "c"
    directory.mkdir()
    state = CenterState(directory)
    for number in range(6):
        state.record_event(f"e{number}", "ok", "browser")
    assert [e["event"] for e in state.security_events()] == ["e5", "e4", "e3"]


# -- the API: before sign-in, setup (S-28)


def test_the_auth_state_needs_no_session_and_says_only_whether_a_password_exists(center):
    status, data = call(center, "GET", "/api/auth")
    assert status == 200 and data["data"] == {"password_set": False, "email": auth.EMAIL}
    with_password(center)
    assert call(center, "GET", "/api/auth")[1]["data"]["password_set"] is True


def test_setup_without_a_session_is_401(center):
    status, data = call(center, "POST", "/api/setup", {"password": GOOD})
    assert status == 401 and data["error"]["code"] == "SIGN_IN"
    assert not center.state.has_password()


def test_setup_with_a_link_session_sets_the_password_and_ends_every_session(center):
    session = link_session(center)
    status, data = call(center, "GET", "/api/projects", session=session)
    assert status == 200
    status, data = call(center, "POST", "/api/setup", {"password": GOOD}, session)
    assert status == 200 and data["data"] == {"email": auth.EMAIL}
    assert center.state.has_password()
    # Setup, then sign-in: the link's session ends with the new credential version.
    assert call(center, "GET", "/api/projects", session=session)[0] == 401
    status, data = sign_in(center)
    assert status == 200 and data["data"]["kind"] == "password"
    assert data["data"]["setup"] is False


def test_setup_refuses_a_weak_password_and_stores_nothing(center):
    session = link_session(center)
    status, data = call(center, "POST", "/api/setup", {"password": "password1234"}, session)
    assert status == 400 and data["error"]["code"] == "PASSWORD_POLICY"
    assert "password1234" not in json.dumps(data)
    assert not center.state.has_password()
    assert call(center, "GET", "/api/projects", session=session)[0] == 200


def test_setup_once_a_password_exists_is_refused(center):
    with_password(center)
    session = link_session(center)
    status, data = call(center, "POST", "/api/setup", {"password": OTHER}, session)
    assert status == 409 and data["error"]["code"] == "PASSWORD_SET"
    assert auth.verify(GOOD, center.state.user(auth.EMAIL))


def test_setup_with_a_password_session_is_refused(center):
    """Only the terminal link may set the first password; a password session cannot exist
    without one, so this is checked on the session's kind directly."""
    session = link_session(center)
    center.sessions[server._hash(session)].kind = "password"
    status, data = call(center, "POST", "/api/setup", {"password": GOOD}, session)
    assert status == 401 and data["error"]["code"] == "LINK_REQUIRED"
    assert not center.state.has_password()


def test_a_link_session_reply_says_setup_is_needed_in_first_run_state(center):
    url, _ = center.mint_login()
    status, data = call(center, "POST", "/api/session", {"token": url.split("#t=")[1]})
    assert data["data"]["setup"] is True and data["data"]["email"] == auth.EMAIL


def test_a_used_link_is_refused_with_403(center):
    url, _ = center.mint_login()
    token = url.split("#t=")[1]
    assert call(center, "POST", "/api/session", {"token": token})[0] == 200
    status, data = call(center, "POST", "/api/session", {"token": token})
    assert status == 403 and data["error"]["code"] == "LINK_USED"


# -- the API: sign-in and the backoff


def test_sign_in_mints_a_new_header_token_each_time_and_never_a_cookie(center):
    import http.client

    with_password(center)
    first = sign_in(center)[1]["data"]["session"]
    second = sign_in(center)[1]["data"]["session"]
    assert first != second
    assert call(center, "GET", "/api/projects", session=first)[0] == 200
    body = json.dumps({"email": auth.EMAIL, "password": GOOD}).encode()
    connection = http.client.HTTPConnection("127.0.0.1", center.port, timeout=30)
    connection.request(
        "POST",
        "/api/session",
        body=body,
        headers={
            "Host": f"127.0.0.1:{center.port}",
            "X-AgentBreeder-Center": "1",
            "Origin": center.origin,
            "Content-Type": "application/json",
        },
    )
    response = connection.getresponse()
    response.read()
    assert response.status == 200 and response.getheader("Set-Cookie") is None


def test_email_is_case_insensitive(center):
    with_password(center)
    assert sign_in(center, email=" Admin@LocalHost ")[0] == 200


def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(center, counted):
    with_password(center)
    auth._dummy_row()
    counted.clear()
    wrong = sign_in(center, OTHER)
    unknown = sign_in(center, GOOD, email="someone@else")
    assert (
        wrong
        == unknown
        == (
            403,
            {
                "data": None,
                "error": {"code": "BAD_CREDENTIALS", "message": "Wrong email or password"},
                "stale": False,
            },
        )
    )
    assert len(counted) == 2  # one scrypt run each


def test_sign_in_refuses_a_missing_or_oversized_field_before_hashing(center, counted):
    with_password(center)
    counted.clear()
    assert call(center, "POST", "/api/session", {"email": auth.EMAIL})[0] == 400
    big = "x" * (auth.MAX_LENGTH + 1)
    assert sign_in(center, big)[0] == 400
    assert counted == []
    assert center.backoff.failures == 0


def test_the_sixth_wrong_password_waits_and_no_hash_runs_while_waiting(
    center, counted, monkeypatch
):
    with_password(center)
    now = [10_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    for _ in range(5):
        assert sign_in(center, OTHER)[0] == 403
    counted.clear()
    status, data = sign_in(center, OTHER)
    assert status == 429 and data["error"]["code"] == "BACKOFF"
    assert data["data"] == {"retry_after": 30}
    assert "press Enter in the terminal running `agentbreeder up`" in data["error"]["message"]
    # Even the right password waits, and nothing is hashed while the wait runs.
    assert sign_in(center, GOOD)[0] == 429
    assert counted == []
    # The terminal link is never subject to it (S-14).
    assert link_session(center)
    now[0] += 30
    assert sign_in(center, OTHER)[0] == 403
    assert sign_in(center, GOOD)[1]["data"]["retry_after"] == 60
    now[0] += 60
    assert sign_in(center, GOOD)[0] == 200
    assert center.backoff.failures == 0


def test_the_wait_never_exceeds_five_minutes(center, monkeypatch):
    with_password(center)
    now = [50_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    seen = []
    for _ in range(14):
        status, data = sign_in(center, OTHER)
        if status == 429:
            seen.append(data["data"]["retry_after"])
            now[0] += data["data"]["retry_after"]
            assert sign_in(center, OTHER)[0] == 403
    assert seen and max(seen) == 300 and all(wait <= 300 for wait in seen)


def test_password_checks_are_single_flight(center):
    with_password(center)
    center.hashing.acquire()
    try:
        status, data = sign_in(center)
    finally:
        center.hashing.release()
    assert status == 429 and data["error"]["code"] == "SIGN_IN_BUSY"
    assert sign_in(center)[0] == 200


def test_sign_in_events_are_recorded_without_secrets(center, monkeypatch):
    with_password(center)
    sign_in(center, OTHER)
    sign_in(center)
    events = [(e["event"], e["outcome"]) for e in center.state.security_events()]
    assert events[:2] == [("sign-in", "ok"), ("sign-in", "failed")]
    dump = "\n".join(center.state.db.iterdump())
    assert OTHER not in dump and GOOD not in dump


# -- the API: Settings


def test_a_change_needs_the_current_password(center):
    with_password(center)
    session = sign_in(center)[1]["data"]["session"]
    status, data = call(center, "POST", "/api/password", {"password": OTHER}, session)
    assert status == 400 and data["error"]["code"] == "CURRENT_REQUIRED"
    status, data = call(
        center, "POST", "/api/password", {"current": "not it at all", "password": OTHER}, session
    )
    assert status == 403 and data["error"]["code"] == "WRONG_PASSWORD"
    assert auth.verify(GOOD, center.state.user(auth.EMAIL))
    # A link session (the terminal link, once a password exists) needs it too.
    link = link_session(center)
    status, _ = call(center, "POST", "/api/password", {"password": OTHER}, link)
    assert status == 400
    assert auth.verify(GOOD, center.state.user(auth.EMAIL))


def test_a_change_refuses_a_weak_new_password_before_checking_the_current_one(center, counted):
    with_password(center)
    session = sign_in(center)[1]["data"]["session"]
    counted.clear()
    status, data = call(
        center, "POST", "/api/password", {"current": GOOD, "password": "short"}, session
    )
    assert status == 400 and data["error"]["code"] == "PASSWORD_POLICY"
    assert counted == []


def test_a_change_ends_other_sessions_and_keeps_this_tab_signed_in(center):
    with_password(center)
    mine = sign_in(center)[1]["data"]["session"]
    other = sign_in(center)[1]["data"]["session"]
    status, data = call(
        center, "POST", "/api/password", {"current": GOOD, "password": OTHER}, mine
    )
    assert status == 200
    fresh = data["data"]["session"]
    assert call(center, "GET", "/api/projects", session=other)[0] == 401
    assert call(center, "GET", "/api/projects", session=mine)[0] == 401
    assert call(center, "GET", "/api/projects", session=fresh)[0] == 200
    assert sign_in(center, GOOD)[0] == 403 and sign_in(center, OTHER)[0] == 200


def test_a_wrong_current_password_counts_toward_the_backoff(center, monkeypatch):
    with_password(center)
    monkeypatch.setattr(auth.time, "time", lambda: 1000.0)
    session = sign_in(center)[1]["data"]["session"]
    for _ in range(5):
        call(
            center,
            "POST",
            "/api/password",
            {"current": "wrong wrong wrong", "password": OTHER},
            session,
        )
    status, data = call(
        center, "POST", "/api/password", {"current": GOOD, "password": OTHER}, session
    )
    assert status == 429 and data["error"]["code"] == "BACKOFF"


def test_sign_out_ends_only_this_tab(center):
    with_password(center)
    mine = sign_in(center)[1]["data"]["session"]
    other = sign_in(center)[1]["data"]["session"]
    assert call(center, "POST", "/api/signout", {}, mine)[0] == 200
    assert call(center, "GET", "/api/projects", session=mine)[0] == 401
    assert call(center, "GET", "/api/projects", session=other)[0] == 200


def test_sign_out_everywhere_ends_every_session(center):
    with_password(center)
    mine = sign_in(center)[1]["data"]["session"]
    other = sign_in(center)[1]["data"]["session"]
    link = link_session(center)
    assert call(center, "POST", "/api/signout-everywhere", {}, mine)[0] == 200
    for session in (mine, other, link):
        assert call(center, "GET", "/api/projects", session=session)[0] == 401
    assert center.state.security_events()[0]["event"] == "sign-out-everywhere"


def test_settings_read_and_save_the_session_length(center):
    with_password(center)
    session = sign_in(center)[1]["data"]["session"]
    status, data = call(center, "GET", "/api/settings", session=session)
    assert status == 200
    settings = data["data"]
    assert settings["session_hours"] == 8 and settings["session_choices"] == [1, 4, 8, 24]
    assert settings["password_set"] is True and settings["idle_minutes"] == 60
    assert settings["events"][0]["event"] == "sign-in"
    assert set(settings["events"][0]) == {"at", "event", "outcome", "source"}
    status, data = call(center, "POST", "/api/settings", {"session_hours": 4}, session)
    assert status == 200 and data["data"]["session_hours"] == 4
    for bad in (5, "4", True, None, 1.0):
        status, data = call(center, "POST", "/api/settings", {"session_hours": bad}, session)
        assert status == 400 and data["error"]["code"] == "SESSION_HOURS"
    assert center.state.setting("session_hours") == "4"


def test_the_session_length_applies_to_open_sessions(center, monkeypatch):
    with_password(center)
    session = sign_in(center)[1]["data"]["session"]
    entry = center.sessions[server._hash(session)]
    entry.created -= 2 * 3600  # signed in two hours ago, active since
    assert center.session_valid(session)
    center.state.set_setting("session_hours", "1")
    assert not center.session_valid(session)


def test_polls_do_not_refresh_the_idle_timer(center):
    with_password(center)
    session = sign_in(center)[1]["data"]["session"]
    entry = center.sessions[server._hash(session)]
    entry.last -= 600
    before = entry.last
    assert call(center, "GET", "/api/activity", session=session)[0] == 200
    assert entry.last == before
    assert call(center, "GET", "/api/projects", session=session)[0] == 200
    assert entry.last > before


def test_a_restart_signs_everyone_out(center, tmp_path):
    with_password(center)
    session = sign_in(center)[1]["data"]["session"]
    again = Center(center.directory)
    assert not again.session_valid(session)


# -- the terminal: --set-password and --reset-password (S-15, S-28)


def password_args(**overrides) -> Namespace:
    return Namespace(
        **{
            "dir": None,
            "environment": "dev",
            "port": None,
            "no_browser": True,
            "yes": False,
            "link": False,
            "set_password": False,
            "reset_password": False,
            **overrides,
        }
    )


@pytest.fixture
def center_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "center-cli"
    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(path))
    return path


def feed(monkeypatch, text: str, tty: bool = False) -> None:
    stream = io.StringIO(text)
    stream.isatty = lambda: tty  # type: ignore[method-assign]
    monkeypatch.setattr(sys, "stdin", stream)


def test_set_password_reads_one_stdin_line_and_never_echoes_it(center_dir, monkeypatch, capsys):
    feed(monkeypatch, GOOD + "\n")
    assert server.main(password_args(set_password=True)) == 0
    out, err = capsys.readouterr()
    assert GOOD not in out + err and "set for admin@localhost" in out
    state = CenterState(center_dir)
    assert auth.verify(GOOD, state.user(auth.EMAIL))
    event = state.security_events()[0]
    assert (event["event"], event["source"]) == ("password-set", "terminal")


def test_set_password_refuses_when_one_exists(center_dir, monkeypatch, capsys):
    feed(monkeypatch, GOOD + "\n")
    assert server.main(password_args(set_password=True)) == 0
    feed(monkeypatch, OTHER + "\n")
    assert server.main(password_args(set_password=True)) == 2
    assert "--reset-password" in capsys.readouterr().err
    assert auth.verify(GOOD, CenterState(center_dir).user(auth.EMAIL))


@pytest.mark.parametrize("text", ["", "short\n", "password1234\r\n"])
def test_set_password_refuses_a_weak_or_missing_password(center_dir, monkeypatch, capsys, text):
    feed(monkeypatch, text)
    assert server.main(password_args(set_password=True)) == 2
    out, err = capsys.readouterr()
    assert "Password not set" in err
    if text.strip():
        assert text.strip() not in out + err
    assert not CenterState(center_dir).has_password()


def test_a_tty_reads_twice_with_getpass(center_dir, monkeypatch, capsys):
    feed(monkeypatch, "", tty=True)
    answers = iter([GOOD, GOOD])
    prompts = []

    def fake(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(server.getpass, "getpass", fake)
    assert server.main(password_args(set_password=True)) == 0
    assert len(prompts) == 2 and "admin@localhost" in prompts[0]
    feed(monkeypatch, "", tty=True)
    answers = iter([OTHER, OTHER + "x"])
    assert server.main(password_args(reset_password=True)) == 2
    assert "differ" in capsys.readouterr().err
    assert auth.verify(GOOD, CenterState(center_dir).user(auth.EMAIL))


def test_password_commands_take_no_project_yes_or_link(center_dir, capsys, tmp_path):
    for extra in ({"dir": tmp_path}, {"yes": True}, {"link": True}):
        assert server.main(password_args(set_password=True, **extra)) == 2
    assert "take no project" in capsys.readouterr().err


def test_reset_while_the_console_runs_ends_its_open_sessions(center, monkeypatch, capsys):
    """Acceptance: `--reset-password` in another process, beside a running console."""
    with_password(center)
    session = sign_in(center)[1]["data"]["session"]
    assert call(center, "GET", "/api/projects", session=session)[0] == 200
    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(center.directory))
    # The running console holds the single-instance lock; the reset must not need it.
    instance = SingleInstance(center.directory)
    instance.acquire()
    try:
        feed(monkeypatch, OTHER + "\n")
        assert server.main(password_args(reset_password=True)) == 0
    finally:
        instance.release()
    assert "Every open console session has ended" in capsys.readouterr().out
    assert call(center, "GET", "/api/projects", session=session)[0] == 401
    assert sign_in(center, OTHER)[0] == 200
    assert center.state.security_events()[1]["event"] == "password-reset"


def test_reset_with_no_password_yet_sets_one(center_dir, monkeypatch):
    feed(monkeypatch, GOOD + "\n")
    assert server.main(password_args(reset_password=True)) == 0
    assert CenterState(center_dir).has_password()


def test_an_older_schema_is_not_migrated_under_a_running_console(center_dir, monkeypatch, capsys):
    center_dir.mkdir(mode=0o700)
    with monkeypatch.context() as patch:  # an older database: schema 3, no users table
        patch.setattr(jobs, "SCHEMA_VERSION", 3)
        CenterState(center_dir).db.close()
    instance = SingleInstance(center_dir)
    instance.acquire()
    try:
        feed(monkeypatch, GOOD + "\n")
        assert server.main(password_args(set_password=True)) == 2
    finally:
        instance.release()
    assert "another AgentBreeder version is running" in capsys.readouterr().err
    db = sqlite3.connect(center_dir / "center.sqlite3")
    assert db.execute("SELECT version FROM schema_version").fetchone() == (3,)
    db.close()
    # With no console running it migrates and sets the password.
    feed(monkeypatch, GOOD + "\n")
    assert server.main(password_args(set_password=True)) == 0
    assert CenterState(center_dir).has_password()


def test_a_newer_schema_is_refused(center_dir, monkeypatch, capsys):
    center_dir.mkdir(mode=0o700)
    db = sqlite3.connect(center_dir / "center.sqlite3")
    db.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    db.execute("INSERT INTO schema_version VALUES (999)")
    db.commit()
    db.close()
    feed(monkeypatch, GOOD + "\n")
    assert server.main(password_args(set_password=True)) == 2
    assert "newer than this AgentBreeder" in capsys.readouterr().err


def test_the_flags_parse_and_set_and_reset_are_exclusive(capsys):
    from agentbreeder.center.__main__ import parse

    assert parse(["--set-password"]).set_password
    assert parse(["--reset-password"]).reset_password
    assert parse(["--link"]).link
    with pytest.raises(SystemExit):
        parse(["--set-password", "--reset-password"])
    capsys.readouterr()


def test_up_passes_password_flags_and_skips_the_docker_checks(monkeypatch, tmp_path):
    from agentbreeder import doctor
    from agentbreeder.center import launch
    from agentbreeder.commands import up

    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "center"))
    monkeypatch.setattr(
        doctor, "check_docker", lambda: (_ for _ in ()).throw(AssertionError("no docker"))
    )
    seen = []

    def fake_exec(path, argv):
        seen.append(argv)
        raise SystemExit(0)

    monkeypatch.setattr(launch.os, "execv", fake_exec)
    with pytest.raises(SystemExit):
        up.run(password_args(reset_password=True))
    assert seen[0][-1] == "--reset-password"


def test_the_cli_end_to_end_sets_a_password_from_stdin(tmp_path):
    """`python -m agentbreeder up --set-password`, a real subprocess (it re-execs `-I`)."""
    env = {**os.environ, "AGENTBREEDER_CENTER_DIR": str(tmp_path / "center")}
    env.pop("PYTHONPATH", None)
    first = subprocess.run(
        [sys.executable, "-m", "agentbreeder", "up", "--set-password"],
        input=GOOD + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert first.returncode == 0, first.stderr
    assert GOOD not in first.stdout + first.stderr
    second = subprocess.run(
        [sys.executable, "-m", "agentbreeder", "up", "--set-password"],
        input=OTHER + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert second.returncode == 2 and "already set" in second.stderr
    assert OTHER not in second.stdout + second.stderr
    state = CenterState(tmp_path / "center")
    assert auth.verify(GOOD, state.user(auth.EMAIL))


# -- start: the sign-in page once a password exists


@pytest.fixture
def quiet_main(monkeypatch, center_dir):
    from agentbreeder import doctor

    monkeypatch.setattr(doctor, "check_docker", lambda: [])
    monkeypatch.setattr(doctor, "check_compose", lambda: [])
    monkeypatch.setattr(
        server.ThreadingHTTPServer,
        "serve_forever",
        lambda self: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    feed(monkeypatch, "")


def _set(center_dir: Path) -> None:
    center_dir.mkdir(mode=0o700, exist_ok=True)
    CenterState(center_dir).set_password(
        auth.EMAIL, auth.hash_password(GOOD), "password-set", "terminal"
    )


def test_once_a_password_exists_up_opens_the_sign_in_page_and_mints_no_link(
    quiet_main, center_dir, monkeypatch, capsys
):
    _set(center_dir)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    opened = []
    monkeypatch.setattr(server.webbrowser, "open", opened.append)
    assert server.main(password_args(port=0, no_browser=False)) == 0
    out = capsys.readouterr().out
    assert "#t=" not in out and "Sign in as admin@localhost at http://127.0.0.1:" in out
    assert opened and opened[0].startswith("http://127.0.0.1:") and opened[0].endswith("/")
    assert not (center_dir / "login.html").exists()


def test_up_link_prints_a_link_even_with_a_password(quiet_main, center_dir, monkeypatch, capsys):
    _set(center_dir)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert server.main(password_args(port=0, link=True)) == 0
    out = capsys.readouterr().out
    assert "One-time link, valid 5 minutes: http://127.0.0.1:" in out and "#t=" in out


def test_a_second_up_with_link_says_to_press_enter_in_the_first(capsys):
    assert server._already_running(password_args(link=True), "http://127.0.0.1:8080") == 0
    assert "press Enter in the terminal running it" in capsys.readouterr().out


def test_enter_makes_a_link_shown_by_the_same_rule(center, monkeypatch, capsys):
    feed(monkeypatch, "\n")
    server._watch_terminal(center)
    out = capsys.readouterr().out
    assert "#t=" not in out and "login.html" in out
    assert (center.directory / "login.html").stat().st_mode & 0o077 == 0
    assert time.time()  # the link it wrote is the current one
    token = (center.directory / "login.html").read_text().split("#t=")[1].split('"')[0]
    assert call(center, "POST", "/api/session", {"token": token})[0] == 200


def test_a_backoff_refusal_is_recorded_once_per_wait(center, monkeypatch):
    with_password(center)
    monkeypatch.setattr(auth.time, "time", lambda: 7000.0)
    for _ in range(5):
        sign_in(center, OTHER)
    for _ in range(20):
        assert sign_in(center, OTHER)[0] == 429
    outcomes = [e["outcome"] for e in center.state.security_events(100)]
    assert outcomes.count("backoff") == 1 and outcomes.count("failed") == 5


def test_a_reset_during_the_check_leaves_an_ended_session(center, monkeypatch):
    """The session is minted under the version read before the check: a reset that commits
    while scrypt runs ends it at once."""
    with_password(center)
    real = auth.verify

    def verify_then_reset(password, row):
        good = real(password, row)
        center.state.set_password(
            auth.EMAIL, auth.hash_password(OTHER), "password-reset", "terminal"
        )
        return good

    monkeypatch.setattr(auth, "verify", verify_then_reset)
    status, data = sign_in(center)
    assert status == 200
    assert call(center, "GET", "/api/projects", session=data["data"]["session"])[0] == 401


def test_a_reset_from_the_terminal_clears_the_wait(center, monkeypatch):
    with_password(center)
    monkeypatch.setattr(auth.time, "time", lambda: 9000.0)
    for _ in range(5):
        sign_in(center, OTHER)
    assert sign_in(center, GOOD)[0] == 429
    center.state.set_password(auth.EMAIL, auth.hash_password(OTHER), "password-reset", "terminal")
    assert sign_in(center, OTHER)[0] == 200


def test_a_second_wait_after_a_success_is_recorded_too(center, monkeypatch):
    with_password(center)
    now = [3000.0]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    for _round in range(2):
        for _ in range(5):
            sign_in(center, OTHER)
        assert sign_in(center, GOOD)[0] == 429
        now[0] += 30
        assert sign_in(center, GOOD)[0] == 200
    outcomes = [e["outcome"] for e in center.state.security_events(100)]
    assert outcomes.count("backoff") == 2


def test_a_change_with_an_oversized_current_or_no_password_is_refused(center):
    session = link_session(center)
    status, data = call(
        center, "POST", "/api/password", {"current": GOOD, "password": OTHER}, session
    )
    assert status == 409 and data["error"]["code"] == "NO_PASSWORD"
    with_password(center)
    session = sign_in(center)[1]["data"]["session"]
    big = "x" * (auth.MAX_LENGTH + 1)
    status, data = call(
        center, "POST", "/api/password", {"current": big, "password": OTHER}, session
    )
    assert status == 403 and data["error"]["code"] == "WRONG_PASSWORD"
    assert center.backoff.failures == 0  # refused before any hashing


def test_setup_loses_a_race_with_the_terminal(center, monkeypatch):
    session = link_session(center)
    real = center.state.set_password

    def terminal_first(*args, **kwargs):
        real(auth.EMAIL, auth.hash_password(OTHER), "password-set", "terminal")
        return real(*args, **kwargs)

    monkeypatch.setattr(center.state, "set_password", terminal_first)
    status, data = call(center, "POST", "/api/setup", {"password": GOOD}, session)
    assert status == 409 and data["error"]["code"] == "PASSWORD_SET"
    assert auth.verify(OTHER, center.state.user(auth.EMAIL))
