"""The Docker rerun for `runtime/adk@1.4.1` and `runtime/adk@1.5.1` (the opentelemetry dependency
update). Run on macOS x86_64 on 2026-11-20 (2 passed; record:
docs/superpowers/verification/1190-adk-otel-patch-docker.md). It needs Docker and the Northwind
stack up with real keys, so it is opt-in:

    cd src/agentbreeder/examples/northwind      # or a copy; then set AGENTBREEDER_NORTHWIND_DIR
    agentbreeder services up --environment dev --env-file <your key file>
    cd -
    AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_ADK_PATCH_TEST=1 \\
      python -m pytest -q -s tests/test_runtime_adk_otel_patch_docker.py

Each version runs on a copy of Northwind beside the Northwind directory (`--stack ../northwind`
is keyed by its path), with `runtime:` set to the new version: build, the image's installed
pins, conformance, and for 1.5.1 Northwind's 61 golden cases through `release` and one invoke.
1.4.1 gets build and conformance only. The ADK `call_llm` spans in Phoenix are checked by hand.
Passing is what would let a later change add either version to `agentspec.RUNTIMES_RUN`, with
the run recorded under docs/superpowers/verification/.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentbreeder import registry

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1"
    or os.getenv("AGENTBREEDER_ADK_PATCH_TEST") != "1",
    reason="Opt-in: needs Docker and the Northwind stack up with real keys",
)

EXAMPLES = Path(registry.__file__).parent / "examples"
NORTHWIND = Path(os.getenv("AGENTBREEDER_NORTHWIND_DIR") or EXAMPLES / "northwind").resolve()
STACK = ["--stack", f"../{NORTHWIND.name}", "--environment", "dev"]
NAME = "northwind-support"
EXPECTED = {"opentelemetry-api": "1.45.2", "opentelemetry-sdk": "1.45.2", "google-adk": "2.14.0"}
# The checks every `runtime/adk` image fails against protocol v1 (1.3.0 and 1.5.0 in
# docs/superpowers/verification/F2a-adk-baseline.md, owner decision D14; 1.4.0 the same in
# 1190-adk-otel-patch-docker.md). A dependency update may not add a failure, nor pass fewer
# checks than the base versions' 22.
ADK_V1_GAPS = {
    "sessions.busy",
    "traces.llm_span",
    "model_errors.text_logs",
    "model_errors.text_spans",
}
ADK_V1_PASSES = 22


def cli(*args: str, cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["agentbreeder", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout
    )


def _json(result: subprocess.CompletedProcess, ok=(0,)) -> dict:
    assert result.returncode in ok, result.stdout[-2000:] + result.stderr[-2000:]
    return json.loads(result.stdout)


def _port(version: str) -> Path:
    """A copy of Northwind on `runtime/adk@<version>`; its agent.lock is rewritten by build."""
    assert (NORTHWIND / "stack.yaml").is_file(), f"no Northwind directory at {NORTHWIND}"
    port = NORTHWIND.parent / f"northwind-adk-{version}"
    shutil.rmtree(port, ignore_errors=True)
    shutil.copytree(NORTHWIND, port, ignore=shutil.ignore_patterns("__pycache__", "deploy"))
    agent = port / "agent.yaml"
    text = agent.read_text()
    assert "runtime: runtime/adk@1.5.0\n" in text
    agent.write_text(
        text.replace("runtime: runtime/adk@1.5.0\n", f"runtime: runtime/adk@{version}\n")
    )
    return port


def _installed(image: str) -> dict[str, str]:
    out = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python", image, "-m", "pip", "list",
         "--format", "json"],
        capture_output=True, text=True, timeout=300, check=True,
    ).stdout  # fmt: skip
    return {row["name"].lower(): row["version"] for row in json.loads(out)}


def _build_and_check(version: str) -> tuple[Path, dict]:
    port = _port(version)
    built = _json(cli("build", "agent.yaml", *STACK, "--json", cwd=port))
    installed = _installed(built["image"])
    for name, pin in EXPECTED.items():
        assert installed.get(name) == pin, (name, installed.get(name))
    # `conformance` exits 1 for `incomplete` or `fail`; no check may fail beyond ADK's v1 gaps.
    report = _json(
        cli("conformance", built["image"], "--project", str(port), "--json", cwd=port), ok=(0, 1)
    )
    print(
        version, "conformance:", report["result"], {r["id"]: r["status"] for r in report["checks"]}
    )
    failed = {row["id"] for row in report["checks"] if row["status"] == "fail"}
    assert failed <= ADK_V1_GAPS, sorted(failed - ADK_V1_GAPS)
    assert report["counts"]["pass"] >= ADK_V1_PASSES, report["counts"]
    return port, built


def test_1_4_1_builds_with_the_new_pins_and_passes_conformance():
    port, _ = _build_and_check("1.4.1")
    shutil.rmtree(port, ignore_errors=True)


def test_1_5_1_releases_northwind_61_of_61_and_answers():
    port, _ = _build_and_check("1.5.1")
    result = cli("release", "agent.yaml", *STACK, "--json", cwd=port, timeout=3600)
    print(result.stdout[-6000:])
    assert result.returncode == 0, result.stderr[-4000:]  # the eval gate: 61/61
    answer = _json(cli("invoke", NAME, "Where is my latest order?", *STACK, "--customer",
                       "eval-cust-a", "--json", cwd=port))  # fmt: skip
    reply = answer["response"]
    print("invoke:", reply.get("tools"), reply.get("cost_usd"), reply.get("trace_id"))
    assert reply["runtime"] == "runtime/adk@1.5.1"
