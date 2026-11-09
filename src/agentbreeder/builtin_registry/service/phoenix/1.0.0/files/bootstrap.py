#!/usr/bin/python3.13
"""Mint the trace-ingest key: a Phoenix system API key, printed to stdout (and nothing else).

Runs once Phoenix is healthy, on the stack network, as a one-shot with the admin secret mounted.
AgentBreeder stores what this prints as the `ingest-key` secret. Errors never include the
response body or any secret.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HOST = os.environ.get("AGENTBREEDER_SERVICE", "")
SECRET = "/run/secrets/admin-secret"
JWT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
QUERY = 'mutation { createSystemApiKey(input: {name: "agentbreeder-ingest"}) { jwt } }'


def fail(message):
    sys.exit(f"phoenix bootstrap: {message}")


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


def mint(secret):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", HOST):
        fail("AGENTBREEDER_SERVICE is not a service name")
    request = urllib.request.Request(
        f"http://{HOST}:6006/graphql",
        data=json.dumps({"query": QUERY}).encode(),
        headers={"authorization": f"Bearer {secret}", "content-type": "application/json"},
        method="POST",
    )
    for attempt in range(10):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read(65536)
            break
        except urllib.error.HTTPError as error:
            if error.code < 500:
                fail(f"Phoenix refused the request (HTTP {error.code})")
        except OSError:
            pass
        time.sleep(1 + attempt)
    else:
        fail("Phoenix did not answer")
    try:
        jwt = json.loads(body)["data"]["createSystemApiKey"]["jwt"]
    except (ValueError, KeyError, TypeError):
        fail("Phoenix's answer had no system key")
    if not isinstance(jwt, str) or len(jwt) > 4096 or not JWT.fullmatch(jwt):
        fail("Phoenix's system key is not a JWT")
    return jwt


def main():
    sys.stdout.write(mint(admin_secret()) + "\n")


if __name__ == "__main__":
    main()
