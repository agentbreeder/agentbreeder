"""phoenix@1.1.0's bootstrap (files/bootstrap.py): the ingest key and the VIEWER read key (#925).

The real-image check is tests/test_phoenix_read_key_docker.py; here Phoenix is a fake behind the
script's opener.
"""

from __future__ import annotations

import email.message
import importlib.util
import io
import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

import agentbreeder

VERSION = Path(agentbreeder.__file__).parent / "builtin_registry/service/phoenix"
FILES = VERSION / "1.1.0/files"
INGEST = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeXN0ZW0ifQ.c2lnbmF0dXJl"
READ = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoiVklFV0VSIn0.c2lnbmF0dXJl"
SESSION = "eyJhbGciOiJIUzI1NiJ9.eyJzZXNzaW9uIjoxfQ.c2Vzc2lvbg"
ADMIN = "a1-admin-secret"
EMAIL = "agentbreeder-reader@agentbreeder.local"


def load(monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # nothing written into the built-in
    spec = importlib.util.spec_from_file_location("phoenix_bootstrap_110", FILES / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response(io.BytesIO):
    def __init__(self, body: bytes, status: int, headers):
        super().__init__(body)
        self.status = status
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def headers(*cookies: str):
    message = email.message.Message()
    for cookie in cookies:
        message["set-cookie"] = cookie
    return message


class FakePhoenix:
    """Answers the bootstrap's requests in order of route; records every request."""

    def __init__(self):
        self.requests = []
        self.users = [
            {"id": "VXNlcjox", "username": "admin", "role": "ADMIN"},
            {"id": "VXNlcjoy", "username": "system", "role": "SYSTEM"},
        ]
        self.pages = None  # optional list of pages for GET /v1/users
        self.overrides = {}  # (method, path prefix) -> answer (bytes, Exception or callable)
        self.first_failures = []  # raised by the first requests, in order

    def answer(self, method, path, request):
        for (m, prefix), answer in self.overrides.items():
            if m == method and path.startswith(prefix):
                return answer(request) if callable(answer) else answer
        if (method, path) == ("POST", "/graphql"):
            return {"data": {"createSystemApiKey": {"jwt": INGEST}}}
        if method == "GET" and path.startswith("/v1/users"):
            if self.pages is not None:
                return self.pages.pop(0)
            return {"data": self.users, "next_cursor": None}
        if method == "DELETE" and path.startswith("/v1/users/"):
            return (b"", 204, headers())
        if (method, path) == ("POST", "/v1/users"):
            return ({"data": {"id": "VXNlcjoz"}}, 201, headers())
        if (method, path) == ("POST", "/auth/login"):
            cookies = (
                f"phoenix-access-token={SESSION}; HttpOnly; Path=/",
                "phoenix-refresh-token=r.e.f; HttpOnly; Path=/",
            )
            return (b"", 204, headers(*cookies))
        if (method, path) == ("POST", "/v1/user/api_keys"):
            return (
                {"data": {"id": "QXBpS2V5OjE=", "name": "agentbreeder-read", "key": READ}},
                201,
            )
        if (method, path) == ("GET", "/auth/logout"):
            return urllib.error.HTTPError(path, 302, "Found", headers(), None)
        raise AssertionError(f"unexpected {method} {path}")

    def open(self, request, timeout):
        assert timeout == 10
        path = request.full_url.removeprefix("http://phoenix:6006")
        self.requests.append(request)
        if self.first_failures:
            raise self.first_failures.pop(0)
        answer = self.answer(request.get_method(), path, request)
        if isinstance(answer, Exception):
            raise answer
        if not isinstance(answer, tuple):
            answer = (answer,)
        body, status, extra = (*answer, *(200, headers())[len(answer) - 1 :])
        if isinstance(body, dict | list):
            body = json.dumps(body).encode()
        return Response(body, status, extra)

    def calls(self):
        return [
            (r.get_method(), r.full_url.removeprefix("http://phoenix:6006")) for r in self.requests
        ]

    def body(self, method, path):
        for request in self.requests:
            if (request.get_method(), request.full_url.endswith(path)) == (method, True):
                return json.loads(request.data)
        raise AssertionError(f"no {method} {path}")

    def bearer(self, method, path):
        for request in self.requests:
            if request.get_method() == method and request.full_url.endswith(path):
                return request.get_header("Authorization")
        raise AssertionError(f"no {method} {path}")


@pytest.fixture
def run(tmp_path, monkeypatch):
    module = load(monkeypatch)
    secret = tmp_path / "admin-secret"
    secret.write_text(ADMIN + "\n")
    monkeypatch.setattr(module, "SECRET", str(secret))
    monkeypatch.setattr(module, "HOST", "phoenix")
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    fake = FakePhoenix()
    monkeypatch.setattr(module._OPENER, "open", fake.open)
    return module, fake


def test_executable_and_exec_form():
    path = FILES / "bootstrap.py"
    assert os.access(path, os.X_OK)
    assert path.read_text().startswith("#!/usr/bin/python3.13\n")


def test_prints_both_keys_and_nothing_else(run, capsys):
    module, fake = run
    module.main()
    out = capsys.readouterr().out
    assert out == json.dumps({"ingest-key": INGEST, "read-key": READ}) + "\n"
    assert fake.calls() == [
        ("POST", "/graphql"),
        ("GET", "/v1/users?limit=100"),
        ("POST", "/v1/users"),
        ("POST", "/auth/login"),
        ("POST", "/v1/user/api_keys"),
        ("GET", "/auth/logout"),
    ]


def test_the_reader_is_a_viewer_with_a_random_login_used_once(run, capsys):
    module, fake = run
    module.main()
    created = fake.body("POST", "/v1/users")
    assert created["send_welcome_email"] is False
    user = created["user"]
    assert user["role"] == "VIEWER" and user["auth_method"] == "LOCAL"
    assert user["username"] == "agentbreeder-reader"
    login = fake.body("POST", "/auth/login")
    assert login["email"] == user["email"] and login["password"] == user["password"]
    assert len(user["password"]) >= 40
    assert user["password"] not in capsys.readouterr().out
    # Admin calls use the admin secret; the key is minted and the session ended as the reader.
    assert fake.bearer("POST", "/v1/users") == f"Bearer {ADMIN}"
    assert fake.bearer("POST", "/v1/user/api_keys") == f"Bearer {SESSION}"
    assert fake.bearer("GET", "/auth/logout") == f"Bearer {SESSION}"


def test_two_runs_use_different_logins(run, capsys):
    module, fake = run
    module.main()
    first = fake.body("POST", "/v1/users")["user"]["password"]
    fake.requests.clear()
    module.main()
    assert fake.body("POST", "/v1/users")["user"]["password"] != first


def test_an_existing_reader_is_deleted_first(run, capsys):
    module, fake = run
    fake.pages = [
        {"data": fake.users, "next_cursor": "VXNlcjo1"},
        {"data": [{"id": "VXNlcjo3", "username": "agentbreeder-reader", "email": EMAIL}]},
    ]
    module.main()
    calls = fake.calls()
    assert calls[1:4] == [
        ("GET", "/v1/users?limit=100"),
        ("GET", "/v1/users?limit=100&cursor=VXNlcjo1"),
        ("DELETE", "/v1/users/VXNlcjo3"),
    ]
    assert calls.index(("DELETE", "/v1/users/VXNlcjo3")) < calls.index(("POST", "/v1/users"))


def test_only_the_first_call_is_retried(run, capsys):
    module, fake = run
    fake.first_failures = [urllib.error.URLError("refused"), OSError("reset")]
    module.main()
    assert fake.calls()[:3] == [("POST", "/graphql")] * 3
    fake.requests.clear()
    fake.overrides[("POST", "/v1/users")] = urllib.error.HTTPError("u", 503, "x", headers(), None)
    with pytest.raises(SystemExit, match=r"refused creating the reader \(HTTP 503\)"):
        module.main()
    assert fake.calls().count(("POST", "/v1/users")) == 1


def error(code):
    return urllib.error.HTTPError("u", code, "x", headers(), io.BytesIO(ADMIN.encode()))


@pytest.mark.parametrize(
    "route, answer, message",
    [
        (("POST", "/v1/users"), error(409), r"refused creating the reader \(HTTP 409\)"),
        (("POST", "/auth/login"), error(401), r"refused the reader's login \(HTTP 401\)"),
        (("POST", "/auth/login"), (b"", 204, headers()), "had no access token"),
        (
            ("POST", "/auth/login"),
            (b"", 204, headers("phoenix-access-token=bad value; Path=/")),
            "had no access token",
        ),
        (("POST", "/v1/user/api_keys"), error(403), r"refused the read key \(HTTP 403\)"),
        (("POST", "/v1/user/api_keys"), ({"data": {}}, 201), "had no read key"),
        (("POST", "/v1/user/api_keys"), ({"data": {"key": "nope"}}, 201), "is not a JWT"),
        (("POST", "/v1/user/api_keys"), ({"data": {"key": "a.b.c\nX: y"}}, 201), "is not a JWT"),
        (("POST", "/v1/user/api_keys"), (b"not json", 201), "is not JSON"),
        (("POST", "/v1/user/api_keys"), (b"{}", 302), "with HTTP 302"),
        (("GET", "/v1/users"), ({"data": "x"},), "user listing is malformed"),
        (("GET", "/v1/users"), (b"[]",), "user listing is malformed"),
        (
            ("GET", "/v1/users"),
            ({"data": [{"username": "agentbreeder-reader", "email": EMAIL, "id": "../x"}]},),
            "user listing is malformed",
        ),
        (("GET", "/v1/users"), ({"data": [], "next_cursor": "a/../b?"},), "malformed"),
        (("DELETE", "/v1/users/"), error(403), r"refused deleting the old reader \(HTTP 403\)"),
        (
            ("GET", "/v1/users"),
            (
                {
                    "data": [
                        {"username": "agentbreeder-reader", "email": "me@corp", "id": "VXNlcjo5"}
                    ]
                },
            ),
            "user named agentbreeder-reader that AgentBreeder did not create",
        ),
        (("POST", "/graphql"), error(401), r"refused the system key \(HTTP 401\)"),
        (("POST", "/graphql"), ({"errors": [{"message": ADMIN}]},), "had no system key"),
    ],
)
def test_failures_print_nothing_and_never_echo(run, capsys, route, answer, message):
    module, fake = run
    if route == ("DELETE", "/v1/users/"):
        fake.users.append({"id": "VXNlcjo5", "username": "agentbreeder-reader", "email": EMAIL})
    fake.overrides[route] = answer
    with pytest.raises(SystemExit, match=message) as stop:
        module.main()
    created = [r for r in fake.requests if r.full_url.endswith("/v1/users") and r.data]
    login = json.loads(created[0].data)["user"]["password"] if created else "unused-login"
    for value in (ADMIN, login, SESSION, READ, INGEST):
        assert value not in str(stop.value)
    assert capsys.readouterr().out == ""  # nothing that could be stored as a key


@pytest.mark.parametrize(
    "answer", [error(500), error(401), ({}, 200), urllib.error.URLError("reset")]
)
def test_a_failed_logout_still_prints_the_keys(run, capsys, answer):
    module, fake = run
    fake.overrides[("GET", "/auth/logout")] = answer
    module.main()
    assert json.loads(capsys.readouterr().out) == {"ingest-key": INGEST, "read-key": READ}


def test_a_foreign_reader_is_never_deleted(run, capsys):
    module, fake = run
    fake.users.append({"id": "VXNlcjo5", "username": "agentbreeder-reader", "email": "me@corp"})
    with pytest.raises(SystemExit, match="did not create"):
        module.main()
    assert ("DELETE", "/v1/users/VXNlcjo5") not in fake.calls()
    assert ("POST", "/v1/users") not in fake.calls()


def test_too_many_user_pages(run, capsys, monkeypatch):
    module, fake = run
    monkeypatch.setattr(module, "MAX_PAGES", 2)
    fake.pages = [{"data": [], "next_cursor": "VXNlcjo1"}] * 2
    with pytest.raises(SystemExit, match="too many users"):
        module.main()


def test_oversized_answer(run, capsys, monkeypatch):
    module, fake = run
    monkeypatch.setattr(module, "BODY_LIMIT", 10)
    with pytest.raises(SystemExit, match="too large"):
        module.main()


@pytest.mark.parametrize("host", ["", "Phoenix", "a/b", "x" * 64])
def test_service_name_is_checked_first(run, monkeypatch, host):
    module, fake = run
    monkeypatch.setattr(module, "HOST", host)
    with pytest.raises(SystemExit, match="not a service name"):
        module.main()
    assert not fake.requests


@pytest.mark.parametrize("content", ["", "a\nb", "é" * 3])
def test_malformed_admin_secret(run, content):
    module, fake = run
    Path(module.SECRET).write_text(content)
    with pytest.raises(SystemExit, match="admin secret is empty or malformed"):
        module.main()
    assert not fake.requests


def test_redirects_are_never_followed(run):
    module, _ = run
    assert module._NoRedirect().redirect_request() is None
    handlers = module._OPENER.handlers
    assert any(type(h).__name__ == "_NoRedirect" for h in handlers)
    # ProxyHandler({}) keeps the environment's proxies out (it adds no handler of its own).
    assert not any(getattr(h, "proxies", None) for h in handlers)


def test_one_zero_zero_is_unchanged():
    # 1.0.x stays byte-for-byte (the append-only fixture checks hashes; this checks intent).
    old = (VERSION / "1.0.0/service.yaml").read_text()
    assert "writes_secret: ingest-key" in old and "read-key" not in old
