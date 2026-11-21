"""The `kind` lifecycle after K0 (#771, #770 §4.1): the provider interface and the tagged binding
must leave every `kind` path working. **Written, not run**: it needs Docker, `kind`, `kubectl`
and the Northwind stack up with real keys and released, so it is opt-in:

    cd src/agentbreeder/examples/northwind      # or a copy; then set AGENTBREEDER_NORTHWIND_DIR
    agentbreeder services up --environment dev --env-file ~/.env
    agentbreeder release agent.yaml --environment dev
    cd -
    AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_KIND_TEST=1 \\
      python -m pytest -q tests/test_kind_binding_lifecycle_docker.py

The tests run in file order: `cluster up`, `promote` (evals on the cluster skipped: this checks
the binding, not the agent), `status`, `deploy --cluster` of the promoted manifest (the
redeploy's binding check), `logs`, `remove`, `cluster down`. `cluster down` runs last even when
an earlier step fails.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agentbreeder import registry

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1" or os.getenv("AGENTBREEDER_KIND_TEST") != "1",
    reason="Opt-in: needs Docker, kind and the Northwind stack up and released",
)

EXAMPLES = Path(registry.__file__).parent / "examples"
NORTHWIND = Path(os.getenv("AGENTBREEDER_NORTHWIND_DIR") or EXAMPLES / "northwind").resolve()
STACK = ["--stack", ".", "--environment", "dev"]
STATE: dict = {}


def cli(*args: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["agentbreeder", *args], cwd=NORTHWIND, capture_output=True, text=True, timeout=timeout
    )


def _json(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    return json.loads(result.stdout)


@pytest.fixture(scope="module", autouse=True)
def cluster_lifecycle():
    assert (NORTHWIND / "stack.yaml").is_file(), f"no Northwind directory at {NORTHWIND}"
    _json(cli("cluster", "up", "--stack", ".", "--environment", "dev", "--yes", "--json"))
    yield
    down = cli("cluster", "down", "--stack", ".", "--json")
    assert down.returncode == 0, down.stderr[-2000:]


def test_promote_records_a_tagged_kind_binding(tmp_path):
    promoted = _json(
        cli("promote", "agent.yaml", "--to", "kubernetes", "--skip-cluster-evals",
            "--output", str(tmp_path), *STACK, "--json", timeout=3600)
    )  # fmt: skip
    STATE["id"] = promoted["cluster"]["id"]
    STATE["manifest"] = promoted["manifest"]
    status = _json(cli("status", STATE["id"], *STACK, "--json"))
    assert status["state"] == "ready"
    # K0: `kind|<context>|<uid>|<namespace>|<kubeconfig>`, recorded inline.
    tag, context, uid, namespace, kubeconfig = status["context"].split("|")
    assert (
        tag == "kind" and context and len(uid) == 36 and namespace and kubeconfig.startswith("/")
    )


def test_a_cluster_redeploy_keeps_the_binding():
    deployed = _json(cli("deploy", STATE["manifest"], "--cluster", *STACK, "--json"))
    assert deployed["id"] == STATE["id"] and deployed["state"] == "ready"


def test_logs_read_through_the_recorded_binding():
    result = cli("logs", STATE["id"], *STACK, "--tail", "5")
    assert result.returncode == 0, result.stderr[-2000:]


def test_remove_on_the_cluster():
    removed = _json(cli("remove", STATE["id"], "--cluster", *STACK, "--json"))
    assert removed["state"] == "removed"
