"""#1025 O1 §12.5 on the pinned LiteLLM image: the 1.2.0 launcher's environment keeps the
provider SDKs away from cloud metadata services (real Docker; `AGENTBREEDER_DOCKER_TEST=1`).

Fake listeners sit at 169.254.169.254 (EC2 IMDS, GCE metadata) and 169.254.170.2 (ECS
container credentials) on an internal test network. In the pinned image, boto3's default
credential chain and google-auth's `default()` run twice: first with the container's raw
environment, which must reach a listener (so the harness can see a request), then with the
environment the launcher builds (`environment_for`), which must send nothing.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml

from agentbreeder import registry

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENTBREEDER_DOCKER_TEST") != "1", reason="needs Docker"
)

V12 = registry.BUILTIN_ROOT / "service/litellm/1.2.0"
IMAGE = yaml.safe_load((V12 / "service.yaml").read_text())["image"]
LISTENERS = {"imds": "169.254.169.254", "ecs": "169.254.170.2"}

LISTEN = """
import http.server, json, sys
class H(http.server.BaseHTTPRequestHandler):
    def _any(self):
        print("HIT", self.command, self.path, flush=True)
        body = json.dumps({"AccessKeyId": "AKIAFAKE", "SecretAccessKey": "s", "Token": "t",
                           "Expiration": "2099-01-01T00:00:00Z"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Metadata-Flavor", "Google")
        self.end_headers()
        self.wfile.write(body)
    do_GET = do_PUT = do_POST = _any
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("0.0.0.0", 80), H).serve_forever()
"""

PROBE = """
import importlib.util, json, os, subprocess, sys
spec = importlib.util.spec_from_file_location("start", "/files/start.py")
start = importlib.util.module_from_spec(spec); spec.loader.exec_module(start)
CHAIN = '''
import sys
try:
    import boto3
    c = boto3.Session().get_credentials()
    print("aws", None if c is None else "found")
except Exception as e:
    print("aws error", type(e).__name__)
try:
    import google.auth
    google.auth.default()
    print("gcp found")
except Exception as e:
    print("gcp error", type(e).__name__)
'''
mode = sys.argv[1]
env = dict(os.environ) if mode == "raw" else start.environment_for(os.environ, {})
out = subprocess.run([sys.executable, "-c", CHAIN], env=env, capture_output=True, text=True,
                     timeout=120)
print(mode, out.stdout.strip().replace("\\n", "; "), flush=True)
"""


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def _hits(names: list[str]) -> int:
    return sum(_docker("logs", name).stdout.count("HIT ") for name in names)


def test_the_launcher_environment_sends_nothing_to_metadata_services(tmp_path: Path):
    tag = uuid.uuid4().hex[:8]
    network = f"ab-o1-meta-{tag}"
    names = [f"ab-o1-{kind}-{tag}" for kind in LISTENERS]
    _docker("pull", "-q", IMAGE)
    _docker("network", "create", "--internal", "--subnet", "169.254.0.0/16", network)
    try:
        for name, address in zip(names, LISTENERS.values(), strict=True):
            _docker(
                "run", "-d", "--name", name, "--network", network, "--ip", address,
                "--entrypoint", "/app/.venv/bin/python3", IMAGE, "-c", LISTEN,
            )  # fmt: skip
        probe = [
            "run", "--rm", "--network", network, "--read-only", "--tmpfs", "/tmp",
            "--user", "65532:65532",
            "-v", f"{V12 / 'files'}:/files:ro",
            "-e", "HOME=/tmp", "-e", "PATH=/app/.venv/bin:/usr/bin:/bin",
            # Ambient container credentials and a metadata host an orchestrator could set.
            "-e", "AWS_CONTAINER_CREDENTIALS_FULL_URI=http://169.254.170.2/creds",
            "-e", "AWS_REGION=us-east-1",
            "--entrypoint", "/app/.venv/bin/python3", IMAGE, "-c", PROBE,
        ]  # fmt: skip
        raw = _docker(*probe, "raw")
        raw_hits = _hits(names)
        for name in names:  # each listener saw the raw environment's request
            assert _hits([name]) > 0, f"{name} saw no request from the raw env: {raw.stdout}"
        assert "aws found" in raw.stdout and "gcp found" in raw.stdout
        launched = _docker(*probe, "launcher")
        assert _hits(names) == raw_hits, f"metadata reached from the launcher env: {launched}"
        assert "aws None" in launched.stdout  # no credentials found, and none fetched
        assert "gcp error" in launched.stdout
        print(raw.stdout.strip(), "|", launched.stdout.strip(), "| raw hits", raw_hits)
    finally:
        _docker("rm", "-f", *names, check=False)
        _docker("network", "rm", network, check=False)
