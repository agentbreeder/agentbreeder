"""The CrewAI acceptance on a real stack (#1090, #1083 F6, multi-framework spec §15 and the §16.1
items marked F6). Run on 2026-11-20 on macOS x86_64 (every test passed; the record is
docs/superpowers/verification/F6-crewai.md). It needs Docker, the Northwind stack up with real
keys, and (for the last test) a `kind` cluster, so it is opt-in:

    cd src/agentbreeder/examples/northwind      # or a copy; then set AGENTBREEDER_NORTHWIND_DIR
    agentbreeder services up --environment dev --env-file ~/.env
    cd -
    AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_CREWAI=1 \\
      python -m pytest -q tests/test_northwind_crewai_docker.py
    # and, with `agentbreeder cluster up` done:
    AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_CREWAI=1 AGENTBREEDER_KIND_TEST=1 \\
      python -m pytest -q tests/test_northwind_crewai_docker.py -k kind

`--stack ../northwind` is keyed by the Northwind directory's path, so the tests run beside the
directory `services up` ran in: the port is copied next to it when it is not there already.
The tests run in file order: the step-cap check deploys and removes its own capped build before
`release` deploys the real one, so `invoke` is never ambiguous.

Passing these is necessary, not sufficient, for calling CrewAI supported: the run is recorded in
docs/superpowers/verification/ with its evidence (spec §15), Phoenix, the console's Traces view,
cost against LiteLLM's spend log and the live coded errors are checked by hand, and the arm64
install is a separate run on an arm64 host.
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
    or os.getenv("AGENTBREEDER_NORTHWIND_CREWAI") != "1",
    reason="Opt-in: needs Docker and the Northwind stack up with real keys",
)

EXAMPLES = Path(registry.__file__).parent / "examples"
NAME = "northwind-support-crewai"
RUNTIME = "runtime/crewai@1.0.0"
NORTHWIND = Path(os.getenv("AGENTBREEDER_NORTHWIND_DIR") or EXAMPLES / "northwind").resolve()
STACK = ["--stack", "../northwind", "--environment", "dev"]
# Each Northwind tool function and a message that should make the model call it (§15.3), as
# CUSTOMER: the eval customer whose paid orders `release` resets (fixtures.sql), so this runs after
# `release` (a pending request from an earlier run makes the model decline again). NW-90002: the
# golden set files a request on NW-90004 but never on NW-90002. Northwind's
# prompt keeps web search for third-party questions and calls cancel_order only after a "yes" in
# the same conversation, so that tool gets two messages in one session.
CUSTOMER = "eval-cust-a"
TOOL_PROMPTS = {
    "lookup_orders": "Where is my latest order?",
    "search_knowledge_base": "How do I sync my Northwind Notes to a new tablet? Search the help.",
    "web_search": "How do I export my notes from Evernote? Search the web for the steps.",
    "escalate_to_human": "I want to talk to a human right now about a billing error.",
    # A new note each run: a note the customer already has is not saved again.
    "save_customer_note": f"Please remember that my desk number is {os.getpid()}.",
    "cancel_order": ("Please cancel my order NW-90002.", "Yes, please file the cancellation."),
    "get_product_catalog": "What products do you sell?",
}


def cli(*args: str, cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    # D13 (a): the port reads Northwind's registry through the registry path.
    env = {**os.environ, registry.PATH_ENV: str(NORTHWIND / "registry")}
    return subprocess.run(
        ["agentbreeder", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
    )


def _json(result: subprocess.CompletedProcess, ok=(0,)) -> dict:
    assert result.returncode in ok, result.stdout[-2000:] + result.stderr[-2000:]
    return json.loads(result.stdout)


def _port(directory_name: str = "northwind-crewai") -> Path:
    port = NORTHWIND.parent / directory_name
    if not port.exists():
        shutil.copytree(
            EXAMPLES / "northwind-crewai", port, ignore=shutil.ignore_patterns("__pycache__")
        )
    return port


@pytest.fixture(scope="module")
def work():
    assert (NORTHWIND / "stack.yaml").is_file(), f"no Northwind directory at {NORTHWIND}"
    return _port()


def test_build_and_conformance(work):
    """§15 step 1, and the §16.1 item that the image installs its lock on this host."""
    built = _json(cli("build", "agent.yaml", *STACK, "--json", cwd=work))
    # `conformance` exits 1 for `incomplete` (the kit-profile checks are not built yet).
    report = _json(cli("conformance", built["image"], "--project", str(work), "--json", cwd=work),
                   ok=(0, 1))  # fmt: skip
    print("conformance:", report["result"], {r["id"]: r["status"] for r in report["checks"]})
    failed = [row for row in report["checks"] if row["status"] == "fail"]
    assert not failed, failed


def test_step_cap_on_real_gemini():
    """§16.1: the proxy's step-cap rule on a real Gemini alias. With one model call allowed, a
    question that needs a tool makes Gemini call one despite `tool_choice: none` (#1111); the
    turn must then fail MODEL_STEP_LIMIT, with no tool run. A 200 means Gemini honoured it this
    time: the reply must then list no tool. Runs before `release`, and removes its deployment."""
    capped = _port("northwind-crewai-capped")
    path = capped / "agent.yaml"
    path.write_text(path.read_text().replace("max_model_calls: 4", "max_model_calls: 1"))
    built = _json(cli("build", "agent.yaml", *STACK, "--json", cwd=capped))
    deployed = _json(
        cli("deploy", built["manifest_path"], "--local", *STACK, "--json", cwd=capped)
    )
    try:
        result = cli("invoke", NAME, TOOL_PROMPTS["lookup_orders"], *STACK, "--json", cwd=capped)
        print("step cap:", result.returncode, result.stdout[-1500:], result.stderr[-1000:])
        if result.returncode == 0:
            assert json.loads(result.stdout)["response"]["tools"] == []
        else:
            assert "MODEL_STEP_LIMIT" in result.stdout + result.stderr
    finally:
        cli("remove", str(deployed["id"]), "--local", *STACK, cwd=capped)
        shutil.rmtree(capped, ignore_errors=True)


def test_release_passes_61_of_61(work):
    """§15 step 2: Northwind's 61 golden cases through `release`, real gateway and model, on the
    `service/litellm` image the stack runs (§16.1). Its exit code is the eval gate."""
    result = cli("release", "agent.yaml", *STACK, "--json", cwd=work, timeout=3600)
    print(result.stdout[-6000:])
    assert result.returncode == 0, result.stderr[-4000:]


def test_one_invoke_per_northwind_tool(work):
    """§15 step 3: each Northwind tool runs through CrewAI, with the reply's identity."""
    missed = []
    for tool, messages in TOOL_PROMPTS.items():
        session = f"tools-{tool}-{os.getpid()}"
        for message in (messages,) if isinstance(messages, str) else messages:
            answer = _json(
                cli("invoke", NAME, message, *STACK, "--customer", CUSTOMER, "--session", session,
                    "--json", cwd=work)
            )  # fmt: skip
        reply = answer["response"]
        print(tool, "->", reply.get("tools"), reply.get("cost_usd"), reply.get("trace_id"))
        assert reply["framework"] == "crewai" and reply["runtime"] == RUNTIME
        if tool not in reply.get("tools", []):
            missed.append(tool)
    # The model chooses; a miss is reported for a person to re-prompt, and fails the run.
    assert not missed, f"no call to {missed}"


def _containers() -> list[str]:
    label = f"label=org.agentbreeder.runtime={RUNTIME}"
    ids = subprocess.run(
        ["docker", "ps", "-q", "--filter", label], capture_output=True, text=True, check=True
    ).stdout.split()
    assert ids, f"no running {RUNTIME} container (run after release)"
    return ids


def test_no_file_in_the_container_keeps_a_turns_text(work):
    """§8.5, §13.7 and §16.1 (F6) on the real image: after a turn with a unique marker, no file
    under the container's HOME (/tmp) holds it, CrewAI's task-output database does not exist,
    and neither does its credentials key. Also: no CREWAI_* or OPENAI_* variable but the
    runtime's own switches, and the marker is not in the container's log."""
    marker = f"ZZ-F6-MARKER-{os.getpid()}"
    message = f"Hello, my reference is {marker}."
    session = f"marker-{os.getpid()}"
    answer = _json(cli("invoke", NAME, message, *STACK, "--customer", CUSTOMER, "--session",
                       session, "--json", cwd=work))  # fmt: skip
    assert answer["response"]["runtime"] == RUNTIME
    for container in _containers():
        found = subprocess.run(
            ["docker", "exec", container, "grep", "-rlF", marker, "/tmp"],
            capture_output=True, text=True,
        )  # fmt: skip
        assert found.returncode == 1 and not found.stdout, found.stdout  # 1: no match
        files = subprocess.run(
            ["docker", "exec", container, "find", "/tmp", "-name", "*.db", "-o", "-name",
             "secret.key", "-o", "-name", "tokens.enc"],
            capture_output=True, text=True, check=True,
        ).stdout.split()  # fmt: skip
        assert files == [], files
        env = json.loads(
            subprocess.run(
                ["docker", "inspect", "--format", "{{json .Config.Env}}", container],
                capture_output=True, text=True, check=True,
            ).stdout
        )  # fmt: skip
        names = [item.split("=", 1)[0] for item in env]
        assert not [n for n in names if n.startswith(("CREWAI_", "OPENAI_"))], names
        logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True)
        assert marker not in logs.stdout + logs.stderr
        assert "╭" not in logs.stdout + logs.stderr  # no CrewAI console panel


@pytest.mark.skipif(os.getenv("AGENTBREEDER_KIND_TEST") != "1", reason="Opt-in: needs kind")
def test_promote_to_kind(work):
    """§15 step 6 and §16.1's Kubernetes /tmp: the release promoted to `kind` (promote runs the
    evals on the cluster unless told not to). The one invoke on `kind` afterwards is by hand
    (`agentbreeder forward`, then a POST to /invoke)."""
    promoted = _json(cli("promote", "agent.yaml", "--to", "kubernetes", *STACK, "--json", cwd=work,
                         timeout=3600))  # fmt: skip
    print("promoted:", json.dumps(promoted)[:2000])
