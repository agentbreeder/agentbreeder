"""Real Docker: `Docker.logs` reads a container by ID, stdout and stderr merged, timestamped.

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_logs_docker.py   (without --cov)
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from agentbreeder.egress.boundary import SIDECAR_BASE
from agentbreeder.runtime import Docker

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)


def test_logs_merge_streams_by_container_id():
    docker = Docker()
    docker.authorize()
    name = f"agentbreeder-logs-test-{uuid.uuid4().hex[:8]}"
    script = "import sys; print('to-stdout', flush=True); print('to-stderr', file=sys.stderr)"
    container = docker.command(
        "container", "run", "--detach", "--name", name, SIDECAR_BASE, "python", "-c", script,
        timeout=300,
    )  # fmt: skip
    try:
        subprocess.run(["docker", "container", "wait", container], capture_output=True, timeout=60)
        info = {"Id": container}
        text, truncated = docker.logs(info, tail=10)
        lines = text.splitlines()
        assert not truncated and len(lines) == 2
        assert {line.split(" ", 1)[1] for line in lines} == {"to-stdout", "to-stderr"}
        assert all(line[:4].isdigit() and "T" in line.split(" ", 1)[0] for line in lines)
        only_last, _ = docker.logs(info, tail=1)
        assert len(only_last.splitlines()) == 1
    finally:
        docker.command("container", "rm", "--force", container)


SECRET = "seeded-marker-" + "q" * 20  # stored as a local secret; must never be printed
FORMS_SCRIPT = f"""
import base64, json, sys, time, urllib.parse
value = {SECRET!r}
print("literal", value, flush=True)
print("base64", base64.b64encode(value.encode()).decode(), flush=True)
print("urlquoted", urllib.parse.quote(value + "/x"), flush=True)
print("json", json.dumps({{"k": value + '"'}}), flush=True)
print("dsn postgresql://app:hunter2-password@db:5432/app", file=sys.stderr, flush=True)
print("paint \\x1b]52;c;cGF5bG9hZA==\\x07 done", flush=True)
print("x" * 16380 + value, flush=True)  # the value straddles Docker's 16 KiB partial boundary
while True:
    print("tick", value, flush=True)
    time.sleep(0.5)
"""


def test_logs_id_redacts_and_escapes_a_real_containers_output(tmp_path, capsys, monkeypatch):
    """M3's gap, closed end to end: a container that logs a stored secret in several encodings,
    read through `agentbreeder logs ID` and `--follow` (stdin EOF stops it and its child)."""
    import base64
    import json
    import sys
    import threading
    import urllib.parse

    from conftest import v2_manifest_data

    from agentbreeder import cli
    from agentbreeder.store import Store

    docker = Docker()
    docker.authorize()
    state = tmp_path / "state"
    identifier = uuid.uuid4().hex
    digest = "sha256:" + "e" * 64
    store = Store(state)
    try:
        store.create(identifier, v2_manifest_data(name="leaky"), digest, docker.context)
        store.transition(identifier, "ready", endpoint="http://127.0.0.1:1")
    finally:
        store.close()
    secrets = state / "secrets"
    secrets.mkdir(mode=0o700)
    (secrets / "token").write_text(SECRET)
    (secrets / "token").chmod(0o600)
    container = docker.command(
        "container", "run", "--detach", "--name", f"agentbreeder-{identifier}",
        "--label", f"io.agentbreeder.deployment={identifier}",
        "--label", f"io.agentbreeder.spec={digest}",
        SIDECAR_BASE, "python", "-c", FORMS_SCRIPT,
        timeout=300,
    )  # fmt: skip
    forms = [
        SECRET,
        base64.b64encode(SECRET.encode()).decode(),
        urllib.parse.quote(SECRET + "/x"),
        json.dumps(SECRET + '"')[1:-1],
        "hunter2-password",
    ]
    try:
        import time

        time.sleep(2)
        assert cli.main(["logs", identifier, "--state-dir", str(state), "--tail", "50"]) == 0
        out = capsys.readouterr().out
        assert "literal" in out and "dsn" in out and "tick" in out  # stdout and stderr
        for form in forms:
            assert form not in out, form
        assert "\x1b" not in out and "\\x1b]52" in out
        # --follow: stops when stdin ends, and its `docker logs` child is gone.
        read, write = os.pipe()
        monkeypatch.setattr(sys, "stdin", os.fdopen(read, "r"))
        timer = threading.Timer(2.0, os.close, args=(write,))
        timer.start()
        before = set(_docker_logs_children())
        try:
            argv = ["logs", identifier, "--state-dir", str(state), "--follow", "--tail", "5"]
            assert cli.main(argv) == 0
        finally:
            timer.cancel()
        followed = capsys.readouterr().out
        assert "tick" in followed and all(form not in followed for form in forms)
        assert set(_docker_logs_children()) <= before
    finally:
        docker.command("container", "rm", "--force", container)


def _docker_logs_children() -> list[str]:
    """PIDs of `docker ... container logs --follow` processes of this user."""
    result = subprocess.run(
        ["pgrep", "-u", str(os.getuid()), "-f", "container logs --timestamps"],
        capture_output=True,
        text=True,
    )
    return result.stdout.split()
