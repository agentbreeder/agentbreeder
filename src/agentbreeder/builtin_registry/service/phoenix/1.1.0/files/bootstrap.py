#!/usr/bin/python3.13
"""Mint Phoenix's two keys and print them to stdout as one JSON object (and nothing else):

- `ingest-key`: a system API key, for the trace proxy (writes spans).
- `read-key`: a personal API key of the VIEWER user `agentbreeder-reader`, for AgentBreeder's
  read paths (`traces`). Phoenix refuses every write made with it.

Runs once Phoenix is healthy, on the stack network, as a one-shot with the admin secret mounted.
AgentBreeder stores each value as the secret of that name. A personal key can only be created
from that user's own session, so the user is (re)created with a random login secret that is used
to log in once and never stored; an existing `agentbreeder-reader` is deleted first, which
revokes its old keys (a user of that name with another email is refused, never deleted). Errors
never include a response body or any secret.
"""

import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote

HOST = os.environ.get("AGENTBREEDER_SERVICE", "")
SECRET = "/run/secrets/admin-secret"
JWT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
COOKIE_VALUE = re.compile(r"[A-Za-z0-9._~+/=-]{1,4096}")
GLOBAL_ID = re.compile(r"[A-Za-z0-9+/=_-]{1,200}")
QUERY = 'mutation { createSystemApiKey(input: {name: "agentbreeder-ingest"}) { jwt } }'
READER = "agentbreeder-reader"
READER_EMAIL = "agentbreeder-reader@agentbreeder.local"
ACCESS_COOKIE = "phoenix-access-token"


BODY_LIMIT = 1024 * 1024
MAX_PAGES = 50


def fail(message):
    sys.exit(f"phoenix bootstrap: {message}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # never resend a credential elsewhere
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def admin_secret():
    try:
        fd = os.open(SECRET, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        fail("the admin secret can't be read")
    with os.fdopen(fd, "rb") as handle:
        value = handle.read(4097).strip()
    if not value or len(value) > 4096 or not value.isascii() or b"\n" in value:
        fail("the admin secret is empty or malformed")
    return value.decode()


def call(method, path, bearer, body=None, what="the request", retry=False, accept=()):
    """(status, headers, parsed JSON or None). HTTP errors other than `accept` fail; with
    `retry`, connection errors and 5xx answers are retried (only for Phoenix's first call)."""
    request = urllib.request.Request(
        f"http://{HOST}:6006{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"authorization": f"Bearer {bearer}", "content-type": "application/json"},
        method=method,
    )
    for attempt in range(10 if retry else 1):
        try:
            with _OPENER.open(request, timeout=10) as response:
                status, headers = response.status, response.headers
                data = response.read(BODY_LIMIT + 1)
            break
        except urllib.error.HTTPError as error:
            if error.code in accept:
                return error.code, error.headers, None
            if error.code < 500 or not retry:
                fail(f"Phoenix refused {what} (HTTP {error.code})")
        except OSError:
            if not retry:
                fail(f"Phoenix did not answer {what}")
        time.sleep(1 + attempt)
    else:
        fail("Phoenix did not answer")
    if status not in (200, 201, 204):
        fail(f"Phoenix answered {what} with HTTP {status}")
    if len(data) > BODY_LIMIT:
        fail(f"Phoenix's answer to {what} is too large")
    if not data:
        return status, headers, None
    try:
        return status, headers, json.loads(data)
    except (ValueError, RecursionError):
        fail(f"Phoenix's answer to {what} is not JSON")


def mint_system(secret):
    _, _, data = call("POST", "/graphql", secret, {"query": QUERY}, "the system key", retry=True)
    try:
        jwt = data["data"]["createSystemApiKey"]["jwt"]
    except (KeyError, TypeError):
        fail("Phoenix's answer had no system key")
    if not isinstance(jwt, str) or len(jwt) > 4096 or not JWT.fullmatch(jwt):
        fail("Phoenix's system key is not a JWT")
    return jwt


def delete_reader(secret):
    """Delete an existing `agentbreeder-reader` (a re-run on the same data): revokes its keys."""
    cursor = None
    for _ in range(MAX_PAGES):
        path = "/v1/users?limit=100" + (f"&cursor={quote(cursor, safe='')}" if cursor else "")
        _, _, data = call("GET", path, secret, what="the user listing")
        users = data.get("data") if isinstance(data, dict) else None
        if not isinstance(users, list):
            fail("Phoenix's user listing is malformed")
        for user in users:
            if isinstance(user, dict) and user.get("username") == READER:
                if user.get("email") != READER_EMAIL:  # not one this bootstrap created
                    fail(f"Phoenix has a user named {READER} that AgentBreeder did not create")
                identifier = user.get("id")
                if not isinstance(identifier, str) or not GLOBAL_ID.fullmatch(identifier):
                    fail("Phoenix's user listing is malformed")
                call("DELETE", f"/v1/users/{identifier}", secret, what="deleting the old reader")
                return
        cursor = data.get("next_cursor")
        if not cursor:
            return
        if not isinstance(cursor, str) or not GLOBAL_ID.fullmatch(cursor):
            fail("Phoenix's user listing is malformed")
    fail("Phoenix has too many users to list")


def access_cookie(headers):
    for header in headers.get_all("set-cookie") or []:
        name, _, rest = header.partition("=")
        if name.strip() == ACCESS_COOKIE:
            value = rest.split(";", 1)[0].strip()
            if COOKIE_VALUE.fullmatch(value):
                return value
    fail("Phoenix's login answer had no access token")


def mint_reader(secret):
    delete_reader(secret)
    fresh = secrets.token_urlsafe(32)  # used once, to log in below; never stored
    user = {"email": READER_EMAIL, "username": READER, "role": "VIEWER", "auth_method": "LOCAL"}
    call(
        "POST",
        "/v1/users",
        secret,
        {"user": {**user, "password": fresh}, "send_welcome_email": False},
        "creating the reader",
    )
    login = {"email": READER_EMAIL, "password": fresh}
    _, headers, _ = call("POST", "/auth/login", secret, login, "the reader's login")
    session = access_cookie(headers)
    _, _, data = call(
        "POST",
        "/v1/user/api_keys",
        session,
        {"data": {"name": "agentbreeder-read", "description": "AgentBreeder read paths"}},
        "the read key",
    )
    try:
        key = data["data"]["key"]
    except (KeyError, TypeError):
        fail("Phoenix's answer had no read key")
    if not isinstance(key, str) or len(key) > 4096 or not JWT.fullmatch(key):
        fail("Phoenix's read key is not a JWT")
    # End the login session (browser sessions only; the key stays valid). Best effort: the key
    # is minted, and the session token expires on its own.
    try:
        call("GET", "/auth/logout", session, what="the reader's logout", accept=range(300, 600))
    except SystemExit:
        pass
    return key


def main():
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", HOST):
        fail("AGENTBREEDER_SERVICE is not a service name")
    secret = admin_secret()
    ingest = mint_system(secret)
    read = mint_reader(secret)
    sys.stdout.write(json.dumps({"ingest-key": ingest, "read-key": read}) + "\n")


if __name__ == "__main__":
    main()
