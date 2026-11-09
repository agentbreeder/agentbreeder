"""The built-in phoenix service's launcher and bootstrap (files/start.py, files/bootstrap.py)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

import agentbreeder

FILES = Path(agentbreeder.__file__).parent / "builtin_registry/service/phoenix/1.0.0/files"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


def load(name, monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)  # nothing written into the built-in
    spec = importlib.util.spec_from_file_location(f"phoenix_{name}", FILES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["start", "bootstrap"])
def test_executable_and_exec_form(name):
    path = FILES / f"{name}.py"
    assert os.access(path, os.X_OK)
    assert path.read_text().startswith("#!/usr/bin/python3.13\n")


# -- launcher -------------------------------------------------------------------------------------


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    module = load("start", monkeypatch)
    monkeypatch.setattr(module, "SECRETS", str(tmp_path))
    calls = []
    monkeypatch.setattr(module.os, "execve", lambda *args: calls.append(args))
    monkeypatch.setenv("PHOENIX_POSTGRES_HOST", "elsewhere")
    return module, tmp_path, calls


def write_all(secrets: Path, **overrides):
    values = {
        "secret": "s1-abc",
        "admin-secret": "a1-def",
        "admin-password": "pw",
        "database-url": "postgresql://phoenix:p@postgres:5432/phoenix\n",
        **overrides,
    }
    for name, value in values.items():
        if value is not None:
            (secrets / name).write_text(value)


def test_secrets_become_phoenix_env(launcher, capsys):
    module, secrets, calls = launcher
    write_all(secrets)
    (secrets / "unrelated").write_text("ignored")
    module.main()
    program, argv, environment = calls[0]
    assert program == sys.executable
    assert argv[1:] == ["-m", "phoenix.server.main", "serve"]
    assert environment["PHOENIX_SECRET"] == "s1-abc"
    assert environment["PHOENIX_ADMIN_SECRET"] == "a1-def"
    assert environment["PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD"] == "pw"
    assert (
        environment["PHOENIX_SQL_DATABASE_URL"] == "postgresql://phoenix:p@postgres:5432/phoenix"
    )
    assert "PHOENIX_POSTGRES_HOST" not in environment  # only the wired URL picks the database
    assert "ignored" not in environment.values()
    assert "s1-abc" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"admin-secret": None}, "admin-secret is missing"),
        ({"secret": ""}, "secret secret is empty or malformed"),
        ({"admin-password": "a\nb"}, "admin-password is empty or malformed"),
        ({"database-url": "a\0b"}, "database-url is empty or malformed"),
        ({"secret": "x" * 20000}, "secret secret is empty or malformed"),
    ],
)
def test_malformed_secrets_stop_without_echoing(launcher, overrides, message):
    module, secrets, calls = launcher
    write_all(secrets, **overrides)
    with pytest.raises(SystemExit) as stop:
        module.main()
    assert message in str(stop.value) and not calls
    assert "a\nb" not in str(stop.value)


def test_symlinked_secret_is_refused(launcher):
    module, secrets, calls = launcher
    write_all(secrets, secret=None)
    (secrets / "real").write_text("s1-abc")
    (secrets / "secret").symlink_to(secrets / "real")
    with pytest.raises(SystemExit, match="secret can't be read"):
        module.main()
    assert not calls


# -- bootstrap ------------------------------------------------------------------------------------


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    module = load("bootstrap", monkeypatch)
    secret = tmp_path / "admin-secret"
    secret.write_text("a1-admin\n")
    monkeypatch.setattr(module, "SECRET", str(secret))
    monkeypatch.setattr(module, "HOST", "phoenix")
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    requests = []
    answers = []

    def urlopen(request, timeout):
        requests.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Response(answer)

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    return module, secret, requests, answers


def minted(jwt=JWT) -> bytes:
    return json.dumps({"data": {"createSystemApiKey": {"jwt": jwt}}}).encode()


def http_error(code):
    return urllib.error.HTTPError("http://phoenix:6006/graphql", code, "x", {}, None)


def test_bootstrap_prints_only_the_key(bootstrap, capsys):
    module, _, requests, answers = bootstrap
    answers.append(minted())
    module.main()
    assert capsys.readouterr().out == JWT + "\n"
    request = requests[0]
    assert request.full_url == "http://phoenix:6006/graphql"
    assert request.get_header("Authorization") == "Bearer a1-admin"
    assert "createSystemApiKey" in json.loads(request.data)["query"]


def test_bootstrap_retries_until_phoenix_answers(bootstrap, capsys):
    module, _, requests, answers = bootstrap
    answers.extend([urllib.error.URLError("refused"), http_error(503), minted()])
    module.main()
    assert len(requests) == 3 and capsys.readouterr().out == JWT + "\n"


def test_bootstrap_gives_up_after_ten_tries(bootstrap):
    module, _, requests, answers = bootstrap
    answers.extend([urllib.error.URLError("refused")] * 10)
    with pytest.raises(SystemExit, match="did not answer"):
        module.main()
    assert len(requests) == 10


@pytest.mark.parametrize(
    "answer, message",
    [
        (http_error(401), r"refused the request \(HTTP 401\)"),
        (b'{"errors": [{"message": "secret a1-admin wrong"}]}', "had no system key"),
        (b"not json", "had no system key"),
        (minted("not-a-jwt"), "is not a JWT"),
        (minted("a.b.c\nX-Injected: 1"), "is not a JWT"),
    ],
)
def test_bootstrap_failures_never_echo(bootstrap, capsys, answer, message):
    module, _, _, answers = bootstrap
    answers.append(answer)
    with pytest.raises(SystemExit, match=message) as stop:
        module.main()
    assert "a1-admin" not in str(stop.value)
    assert capsys.readouterr().out == ""  # nothing that could be stored as the key


@pytest.mark.parametrize("content", ["", "a\nb", "é" * 3])
def test_bootstrap_refuses_a_malformed_admin_secret(bootstrap, content):
    module, secret, requests, _ = bootstrap
    secret.write_text(content)
    with pytest.raises(SystemExit, match="admin secret is empty or malformed"):
        module.main()
    assert not requests


def test_bootstrap_refuses_a_symlinked_admin_secret(bootstrap, tmp_path):
    module, secret, requests, _ = bootstrap
    secret.unlink()
    (tmp_path / "real").write_text("a1-admin")
    secret.symlink_to(tmp_path / "real")
    with pytest.raises(SystemExit, match="can't be read"):
        module.main()
    assert not requests


def test_bootstrap_needs_a_service_host(bootstrap, monkeypatch):
    module, _, requests, _ = bootstrap
    monkeypatch.setattr(module, "HOST", "evil.example.com:1/")
    with pytest.raises(SystemExit, match="not a service name"):
        module.main()
    assert not requests
