"""Opt-in live verification of the built-in MCP servers (#748; spec §7.5): a deployed agent calls
granted tools through the relay to the real upstream.

    AGENTBREEDER_LIVE_MCP=1 pytest tests/test_mcp_builtins_live.py -k playwright
    AGENTBREEDER_LIVE_MCP=1 GITHUB_MCP_TOKEN=... GITHUB_MCP_REPO=owner/repo \
        pytest tests/test_mcp_builtins_live.py -k github

GITHUB_MCP_REPO must have an issue #1 and a README that mentions a license; the token needs
read access to it (Metadata and Issues).

Everything runs through the CLI, as an operator would: `services up --yes` (postgres, a fake
OpenAI-compatible gateway that follows a scripted list of tool calls, the built-in's relay and,
for Playwright, its upstream service), `build`, a real `deploy` (identity gate, per-deployment
LLM key, relay token and grant file), `POST /invoke`, `remove` and `services down --purge`.

The model is scripted; the agent, runtime, relay and upstream are real. The test checks that the
agent got real upstream results, that the model was offered only granted tools, that the relay
logged each call as allowed, that the upstream credential never reaches the agent container, and
that `remove` revokes the deployment's relay grant.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml
from test_services_engine import FAKEGW, write_project

from agentbreeder import registry
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_LIVE_MCP") != "1", reason="Opt-in live MCP verification"
)
FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
PAGE = (
    "data:text/html,<title>Relay check</title><h1>Hello from the relay</h1>"
    "<button onclick=\"document.querySelector('h1').textContent='Clicked ok'\">Go</button>"
    "<input aria-label=Note>"
)
# A page that tries to call its own MCP server, past the relay's tool grant. A synchronous XHR,
# so the result is on the page before the navigation returns. Chromium's Private Network Access
# refuses it (a non-secure page reaching loopback or private addresses); this guards that.
# The markers are split so the page source echoed in tool results never matches them.
SELF_CALL = (
    "data:text/html,<p id=r></p><script>"
    "for (const u of ['http://localhost:8931/mcp','http://127.0.0.1:8931/mcp',"
    "'http://browser:8931/mcp']) { const x = new XMLHttpRequest();"
    " try { x.open('POST', u, false); x.setRequestHeader('Content-Type', 'text/plain');"
    " x.send('{}'); document.getElementById('r').append(' SELF-' + 'REACHED ' + u); }"
    " catch (e) { document.getElementById('r').append(' SELF-' + 'BLOCKED'); } }</script>"
)


def _github_case() -> dict:
    owner, _, repo = (os.getenv("GITHUB_MCP_REPO") or "/").partition("/")
    tools = ["get_me", "list_issues", "issue_read", "search_repositories", "get_file_contents"]
    where = {"owner": owner, "repo": repo}
    return {
        "mcp": {"github": {"ref": "mcp/github@1.0.0"}},
        "services": {},
        "grant": {"github": tools},
        "agent_mcp": [{"ref": "mcp/github@1.0.0", "tools": tools}],
        "script": [
            {"tool": "github_get_me", "arguments": {}},
            {"tool": "github_list_issues", "arguments": {**where, "state": "OPEN", "perPage": 3}},
            {
                "tool": "github_issue_read",
                "arguments": {**where, "method": "get", "issue_number": 1},
            },
            {
                "tool": "github_search_repositories",
                "arguments": {"query": f"repo:{owner}/{repo}", "minimal_output": True},
            },
            {"tool": "github_get_file_contents", "arguments": {**where, "path": "README.md"}},
        ],
        "offered": {f"github_{tool}" for tool in tools},
        # Real data from each call: the profile, the repo, issue 1 (JSON escaped inside the
        # tool result text) and the README.
        "expect": [f"{owner}/{repo}", "login", r"\"number\":1,", "license"],
        "errors": False,
        "credential": os.getenv("GITHUB_MCP_TOKEN"),
        "needs": ["GITHUB_MCP_TOKEN", "GITHUB_MCP_REPO"],
    }


def _playwright_case() -> dict:
    tools = [
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_close",
    ]
    return {
        "mcp": {"playwright": {"ref": "mcp/playwright@1.0.0"}},
        "services": {"browser": {"ref": "service/playwright-mcp@1.0.0"}},
        "grant": {"playwright": tools},
        "agent_mcp": [{"ref": "mcp/playwright@1.0.0", "tools": tools}],
        "script": [
            {"tool": "playwright_browser_navigate", "arguments": {"url": PAGE}},
            {"tool": "playwright_browser_snapshot", "arguments": {}},
            {
                "tool": "playwright_browser_click",
                "arguments": {"element": "Go button", "target": "e3"},
            },
            {
                "tool": "playwright_browser_type",
                "arguments": {"element": "Note textbox", "target": "e4", "text": "Typed ok"},
            },
            {"tool": "playwright_browser_snapshot", "arguments": {}},
            {"tool": "playwright_browser_navigate", "arguments": {"url": SELF_CALL}},
            {"tool": "playwright_browser_snapshot", "arguments": {}},
            {"tool": "playwright_browser_navigate", "arguments": {"url": "https://example.com/"}},
            {"tool": "playwright_browser_close", "arguments": {}},
        ],
        "offered": {f"playwright_{tool}" for tool in tools},
        # The page rendered, the click and the typing took effect; with no egress, outside
        # names don't resolve.
        "expect": [
            "Hello from the relay",
            "Clicked ok",
            "Typed ok",
            "SELF-BLOCKED SELF-BLOCKED SELF-BLOCKED",
            "ERR_NAME_NOT_RESOLVED",
        ],
        "never": ["SELF-REACHED"],
        "credential": None,
        "needs": [],
    }


CASES = {"github": _github_case, "playwright": _playwright_case}


def cli(*args: str, env: dict, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, "-m", "agentbreeder", *args],
        capture_output=True,
        text=True,
        timeout=1800,
        env=env,
    )
    if check and result.returncode:
        raise AssertionError(
            f"agentbreeder {args[0]} {args[1] if len(args) > 1 else ''} failed "
            f"({result.returncode}): {result.stderr[-1500:]}"
        )
    return result


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=600)
    if check and result.returncode:
        raise AssertionError(f"docker {args[0]} failed: {result.stderr[-800:]}")
    return result.stdout + (result.stderr if args[0] == "logs" else "")


def write_case(root: Path, case: dict, name: str) -> None:
    stack = {
        "kind": "stack",
        "schema_version": 1,
        "name": f"mcp-live-{name}",  # a distinct stack per case
        "services": {
            "gateway": {"ref": "service/fakegw@1.0.0", "with": {"config": "gw/config.yaml"}},
            "postgres": {
                "ref": "service/postgres-pgvector@1.1.0",
                "with": {"databases": ["agent"]},
            },
            **case["services"],
        },
        "mcp": case["mcp"],
        "environment": {
            "gateway": "gateway",
            "models": ["m1"],
            "ceilings": {"budget_usd": 1, "rpm": 60},
            "bindings": {"DATABASE_URL": {"ref": "postgres.databases.agent"}},
            "agents": {
                "demo-agent": {
                    "sponsor": "lead@example.com",
                    "review_by": "2027-06-30",
                    "mcp": case["grant"],
                }
            },
        },
    }
    gateway = {**FAKEGW, "command": ["python3", "/files/gw.py"]}
    write_project(root, stack=stack, gateway=gateway)
    files = root / "registry/service/fakegw/1.0.0/files"
    shutil.copyfile(FIXTURES / "fake_gateway.py", files / "gw.py")
    (files / "script.json").write_text(json.dumps(case["script"]))
    agent = {
        "kind": "agent",
        "schema_version": 1,
        "name": "demo-agent",
        "runtime": "runtime/adk@1.1.0",
        "model": {"alias": "m1", "reasoning": "none", "max_model_calls": 12},
        "prompt": {"path": "prompt.md"},
        "mcp": case["agent_mcp"],
        "agent_events": "none",
        "traces": "none",
    }
    (root / "agent.yaml").write_text(yaml.safe_dump(agent))
    (root / "prompt.md").write_text("You use the tools you are given.\n")


def invoke(endpoint: str) -> dict:
    body = json.dumps({"message": "Run the check", "session_id": "s1", "customer_id": "c1"})
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/invoke", body.encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())


@pytest.mark.parametrize("name", sorted(CASES))
def test_deployed_agent_calls_a_built_in_through_the_relay(name, tmp_path):
    case = CASES[name]()
    missing = [var for var in case["needs"] if not os.getenv(var)]
    if missing:
        pytest.skip(f"needs {', '.join(missing)}")
    env = {k: v for k, v in os.environ.items() if k != registry.PATH_ENV}
    env[ROOT_ENV] = str(tmp_path / "stacks")
    root = tmp_path / "project"
    root.mkdir()
    write_case(root, case, name)
    stack = ["--stack", str(root)]
    deployment = None
    server = next(iter(case["mcp"]))
    try:
        cli("services", "up", str(root), "--environment", "dev", "--yes", env=env)
        built = json.loads(
            cli(
                "build",
                str(root / "agent.yaml"),
                "--environment",
                "dev",
                "--json",
                *stack,
                env=env,
            ).stdout  # fmt: skip
        )
        deployed = json.loads(
            cli(
                "deploy",
                built["manifest_path"],
                "--local",
                "--environment",
                "dev",
                "--json",
                *stack,
                env=env,
            ).stdout  # fmt: skip
        )
        deployment = deployed["id"]
        assert deployed["state"] == "ready", deployed

        reply = invoke(deployed["endpoint"])
        assert reply["runtime"] == "runtime/adk@1.1.0"
        assert reply["tools"] == [step["tool"] for step in case["script"]], reply
        assert reply["mcp_unavailable"] == []
        for text in case["expect"]:
            assert text.lower() in reply["reply"].lower(), (text, reply["reply"][:2000])
        for text in case.get("never", []):
            assert text not in reply["reply"], reply["reply"][:2000]
        if case.get("errors") is False:
            assert '"iserror": true' not in reply["reply"].lower(), reply["reply"][:2000]

        containers = docker("ps", "--format", "{{.Names}}").split()
        gateway = next(
            c for c in containers if c.endswith("-gateway-1") and f"mcp-live-{name}" in c
        )
        offered = [
            set(json.loads(line)["tools"])
            for line in docker("exec", gateway, "cat", "/tmp/requests.jsonl").splitlines()
        ]
        assert offered and all(tools == case["offered"] for tools in offered)

        relay = next(
            c for c in containers if c.endswith(f"-mcp-{server}-1") and f"mcp-live-{name}" in c
        )
        decisions = [
            json.loads(line) for line in docker("logs", relay).splitlines() if line.startswith("{")
        ]
        calls = [d for d in decisions if d.get("method") == "tools/call"]
        assert len(calls) == len(case["script"]), calls
        assert all(d.get("decision") == "allow" for d in calls), calls
        # Measured relay latency, for the docs (`pytest -s` prints it).
        for method in ("initialize", "tools/list", "tools/call"):
            times = [d["ms"] for d in decisions if d.get("method") == method and "ms" in d]
            if times:
                print(f"{name} {method}: n={len(times)} max={max(times)}ms ms={times}")

        agent = next(c for c in containers if deployment[:8] in c)
        if case["credential"]:
            # Spec §7.6.11: the agent's and the relay's inspect output, both logs and the
            # generated Compose file.
            exposed = "".join(
                [docker("inspect", agent), docker("logs", agent), docker("inspect", relay),
                 docker("logs", relay)]
                + [p.read_text() for p in (tmp_path / "stacks").glob("*/compose.yaml")]
            )  # fmt: skip
            assert case["credential"] not in exposed
    finally:
        try:
            if deployment:
                cli("remove", deployment, "--local", *stack, env=env)
                grants = [
                    json.loads(path.read_text())
                    for path in (tmp_path / "stacks").glob(f"*/grants/dev/{server}/*.json")
                ]
                assert all(g.get("deployment") != deployment for g in grants), grants
        finally:
            cli("services", "down", str(root), "--purge", "--yes", env=env, check=False)
