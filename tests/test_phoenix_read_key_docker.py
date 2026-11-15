"""Opt-in real-image check of phoenix@1.1.0's read key (#925), on one Phoenix container.

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_phoenix_read_key_docker.py

Phoenix runs with the built-in's hardening and env, on SQLite (no stack, no postgres), with
the 1.1.0 files mounted at /files. Its bootstrap runs inside that container (`docker exec`,
`AGENTBREEDER_SERVICE=localhost`), as `services up` runs it on the stack network. Checked:

- the bootstrap prints exactly {ingest-key, read-key}, both JWTs, nothing else;
- `traces`' reader (phoenix_traces.Phoenix) lists a deployment's trace with the read key;
- Phoenix refuses writes with the read key (REST project create, OTLP ingest, a GraphQL
  mutation), while the ingest key still writes;
- running the bootstrap again deletes the old reader, so its key stops working.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import agentbreeder
from agentbreeder import phoenix_traces, redaction
from agentbreeder.services.schema import load_service

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)

VERSION = Path(agentbreeder.__file__).parent / "builtin_registry/service/phoenix/1.1.0"
PROJECT = "agent925"
DEPLOYMENT = "925" * 10 + "ab"  # 32 hex digits, as deployment IDs are

# Runs inside the Phoenix image (it ships opentelemetry-proto): one root span for PROJECT,
# tagged with DEPLOYMENT, sent with the key read from stdin. Prints the HTTP status.
SEND_SPAN = """
import sys, time, urllib.request, urllib.error, os
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
key = sys.stdin.readline().strip()
request = ExportTraceServiceRequest()
spans = request.resource_spans.add()
spans.resource.attributes.append(
    KeyValue(key="openinference.project.name", value=AnyValue(string_value="PROJECT")))
span = spans.scope_spans.add().spans.add()
span.trace_id = os.urandom(16)
span.span_id = os.urandom(8)
span.name = "agent_run"
now = time.time_ns()
span.start_time_unix_nano = now - 5_000_000
span.end_time_unix_nano = now
span.attributes.append(
    KeyValue(key="openinference.span.kind", value=AnyValue(string_value="AGENT")))
span.attributes.append(
    KeyValue(key="agentbreeder.deployment_id", value=AnyValue(string_value="DEPLOYMENT")))
post = urllib.request.Request(
    "http://localhost:6006/v1/traces", data=request.SerializeToString(), method="POST",
    headers={"authorization": "Bearer " + key, "content-type": "application/x-protobuf"})
try:
    with urllib.request.urlopen(post, timeout=30) as answer:
        print(answer.status)
except urllib.error.HTTPError as error:
    print(error.code)
""".replace('"PROJECT"', f'"{PROJECT}"').replace('"DEPLOYMENT"', f'"{DEPLOYMENT}"')


def docker(*args: str, check: bool = True, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=300, input=stdin
    )
    if check and result.returncode:
        raise AssertionError(f"docker {args[0]} failed: {result.stderr[-500:]}")
    return result.stdout


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def status(method: str, url: str, token: str, body: bytes | None = None, kind="json") -> int:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json" if kind == "json" else "application/x-protobuf",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


@pytest.fixture
def phoenix(tmp_path):
    spec = load_service(VERSION / "service.yaml")
    mounted = tmp_path / "secrets"
    mounted.mkdir()
    admin = "a1-" + secrets.token_urlsafe(32)
    (mounted / "admin-secret").write_text(admin)
    (mounted / "admin-secret").chmod(0o644)  # read by uid 65532 in the container
    name = f"ab925-phoenix-{secrets.token_hex(4)}"
    port = free_port()
    hidden = {  # passed by name, from the docker client's environment (not in the argv)
        "PHOENIX_SECRET": "s1-" + secrets.token_urlsafe(32),
        "PHOENIX_ADMIN_SECRET": admin,
        "PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD": secrets.token_urlsafe(16),
    }
    argv = ["run", "-d", "--name", name, "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL"]
    argv += ["--security-opt", "no-new-privileges", "--user", spec.user]
    argv += ["-p", f"127.0.0.1:{port}:6006"]
    argv += ["-v", f"{mounted}:/run/secrets:ro", "-v", f"{VERSION / 'files'}:/files:ro"]
    for key, value in spec.env.items():
        argv += ["-e", f"{key}={value}"]
    for key in hidden:
        argv += ["-e", key]
    argv += ["--entrypoint", "/usr/bin/python3.13", spec.image, "-m", "phoenix.server.main"]
    argv += ["serve"]
    started = subprocess.run(
        ["docker", *argv],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, **hidden},
    )
    if started.returncode:
        docker("rm", "--force", name, check=False)
        raise AssertionError(f"docker run failed: {started.stderr[-500:]}")
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 240
        while True:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=5) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                pass
            assert time.monotonic() < deadline, "Phoenix did not become healthy"
            time.sleep(2)
        yield name, base, admin
    finally:
        docker("rm", "--force", name, check=False)


def bootstrap(name: str) -> dict[str, str]:
    out = docker("exec", "-e", "AGENTBREEDER_SERVICE=localhost", name, "/files/bootstrap.py")
    keys = json.loads(out)
    assert sorted(keys) == ["ingest-key", "read-key"]
    assert all(value.count(".") == 2 for value in keys.values())
    assert out == json.dumps(keys) + "\n"  # nothing else on stdout
    return keys


def test_read_key_reads_traces_and_cannot_write(phoenix):
    name, base, admin = phoenix
    keys = bootstrap(name)
    ingest, read = keys["ingest-key"], keys["read-key"]
    assert ingest != read

    # A trace written with the ingest key (as the trace proxy does) ...
    sent = docker("exec", "-i", name, "/usr/bin/python3.13", "-c", SEND_SPAN, stdin=ingest + "\n")
    assert sent.strip() == "200"

    # ... is listed by traces' reader with the read key.
    reader = phoenix_traces.Phoenix(base, read, phoenix_traces.READ_KEY)
    deadline = time.monotonic() + 60
    while PROJECT not in reader.projects():
        assert time.monotonic() < deadline, "the project never appeared"
        time.sleep(1)
    created = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    row = {"id": DEPLOYMENT, "name": PROJECT, "state": "ready", "created": created}
    report = phoenix_traces.Report(base)
    show = phoenix_traces.Shown(redaction.Redactor([ingest, read, admin]))
    deadline = time.monotonic() + 60
    while not report.traces:
        assert time.monotonic() < deadline, "the trace was never listed"
        report = phoenix_traces.Report(base)
        reader.tagged.clear()
        phoenix_traces.for_deployment(reader, row, [row], reader.projects(), show, 5, None, report)
        time.sleep(1)
    (trace,) = report.traces
    assert trace["deployment_id"] == DEPLOYMENT and trace["attribution"] == "exact"

    # Writes with the read key are refused; the ingest key still writes.
    project = json.dumps({"name": "x925"}).encode()
    assert status("POST", f"{base}/v1/projects", read, project) == 403
    assert status("POST", f"{base}/v1/traces", read, b"", kind="protobuf") == 403
    mutation = json.dumps(
        {"query": 'mutation { createSystemApiKey(input: {name: "x"}) { jwt } }'}
    ).encode()
    request = urllib.request.Request(
        f"{base}/graphql",
        data=mutation,
        method="POST",
        headers={"authorization": f"Bearer {read}", "content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        answer = json.loads(response.read())
    assert answer["data"] is None and "Viewers cannot" in answer["errors"][0]["message"]
    assert status("POST", f"{base}/v1/projects", ingest, project) == 200

    # A second bootstrap (same data, e.g. moving a stack from 1.0.0) replaces the reader.
    again = bootstrap(name)
    assert status("GET", f"{base}/v1/projects", read, kind="json") == 401
    assert status("GET", f"{base}/v1/projects", again["read-key"]) == 200
    users = urllib.request.Request(
        f"{base}/v1/users", headers={"authorization": f"Bearer {admin}"}
    )
    with urllib.request.urlopen(users, timeout=30) as response:
        listed = json.loads(response.read())["data"]
    readers = [u for u in listed if u["username"] == "agentbreeder-reader"]
    assert [u["role"] for u in readers] == ["VIEWER"]
