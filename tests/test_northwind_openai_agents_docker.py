"""The OpenAI Agents SDK acceptance on a real stack (#1083 F4, multi-framework spec §15 and the
§16.1 items marked F4). Run on 2026-11-20 (docs/superpowers/verification/F4-openai-agents.md); it
needs Docker, the Northwind stack up with real keys, and (for the last test) a `kind` cluster, so
it is opt-in:

    cd src/agentbreeder/examples/northwind      # or a copy; then set AGENTBREEDER_NORTHWIND_DIR
    agentbreeder services up --environment dev --env-file ~/.env
    cd -
    AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_OPENAI_AGENTS=1 \\
      python -m pytest -q tests/test_northwind_openai_agents_docker.py
    # and, with `agentbreeder cluster up` done:
    AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_OPENAI_AGENTS=1 AGENTBREEDER_KIND_TEST=1 \\
      python -m pytest -q tests/test_northwind_openai_agents_docker.py -k kind

`--stack ../northwind` is keyed by the Northwind directory's path, so the tests run beside the
directory `services up` ran in: the port is copied next to it when it is not there already.
The tests run in file order: the step-cap check deploys and removes its own capped build before
`release` deploys the real one, so `invoke` is never ambiguous.

Passing these is necessary, not sufficient, for calling the OpenAI Agents SDK supported: the run is
recorded in docs/superpowers/verification/ with its evidence (spec §15), Phoenix and the
console's Traces view are checked by hand, and the arm64 install is a separate run on an arm64
host.
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
    or os.getenv("AGENTBREEDER_NORTHWIND_OPENAI_AGENTS") != "1",
    reason="Opt-in: needs Docker and the Northwind stack up with real keys",
)

EXAMPLES = Path(registry.__file__).parent / "examples"
NAME = "northwind-support-openai-agents"
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


def _port(directory_name: str = "northwind-openai-agents") -> Path:
    port = NORTHWIND.parent / directory_name
    if not port.exists():
        shutil.copytree(
            EXAMPLES / "northwind-openai-agents",
            port,
            ignore=shutil.ignore_patterns("__pycache__"),
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
    capped = _port("northwind-openai-agents-capped")
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
    """§15 step 3: each Northwind tool runs through the SDK, with the reply's identity."""
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
        assert reply["framework"] == "openai-agents"
        assert reply["runtime"] == "runtime/openai-agents@1.0.0"
        if tool not in reply.get("tools", []):
            missed.append(tool)
    # The model chooses; a miss is reported for a person to re-prompt, and fails the run.
    assert not missed, f"no call to {missed}"


def test_the_deployed_agent_has_no_openai_variable(work):
    """§16.1 (F4), in part: the SDK with no OPENAI_API_KEY at all. The released container's
    environment has no OPENAI_* variable, so the SDK's own exporter has no key (and the
    instrumentor replaces it). The full check, that nothing left the deploy network, is the
    conformance egress check, which waits for the network log (F1b)."""
    label = "label=org.agentbreeder.runtime=runtime/openai-agents@1.0.0"
    ids = subprocess.run(
        ["docker", "ps", "-q", "--filter", label], capture_output=True, text=True, check=True
    ).stdout.split()
    assert ids, "no running runtime/openai-agents container (run after release)"
    for container in ids:
        env = json.loads(
            subprocess.run(
                ["docker", "inspect", "--format", "{{json .Config.Env}}", container],
                capture_output=True,
                text=True,
                check=True,
            ).stdout  # fmt: skip
        )
        names = [item.split("=", 1)[0] for item in env]
        print(container, sorted(n for n in names if n.startswith("AGENTBREEDER_")))
        assert not [name for name in names if name.startswith("OPENAI_")], names


@pytest.mark.skipif(os.getenv("AGENTBREEDER_KIND_TEST") != "1", reason="Opt-in: needs kind")
def test_promote_to_kind(work):
    """§15 step 6 and §16.1's Kubernetes /tmp: the release promoted to `kind` (promote runs the
    evals on the cluster unless told not to). The one invoke on `kind` afterwards is by hand
    (`agentbreeder forward`, then a POST to /invoke)."""
    promoted = _json(cli("promote", "agent.yaml", "--to", "kubernetes", *STACK, "--json", cwd=work,
                         timeout=3600))  # fmt: skip
    print("promoted:", json.dumps(promoted)[:2000])
