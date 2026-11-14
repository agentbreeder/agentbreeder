"""`agentbreeder mcp grant|revoke` (M15 #831): the stack.yaml edit, its refusals, `in_use`, the
CLI, the MCP fixture project and, offline (Docker faked), the grant -> `services up` -> relay
config sequence, including `services up` refusing a revoke an active deployment still holds.

The live acceptance run (a real deploy whose relay lists the granted tools) is
`test_fixture_live_grant_and_revoke`, behind AGENTBREEDER_DOCKER_TEST=1 and GITHUB_MCP_TOKEN.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_mcp_stack import RelayDocker, _active_grant, run_up, stack_dir, write_mcp
from test_mcp_stack import project as project  # noqa: F401  (the services-up fixture)
from test_mcp_stack import stack as mcp_stack_data
from test_services_engine import write_project

from agentbreeder import mcpgrant, registry
from agentbreeder.mcpgrant import GrantError, change
from agentbreeder.services.schema import ServiceError, load_stack
from agentbreeder.services.state import ROOT_ENV

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_github_project"
ROOT = Path(__file__).resolve().parents[1]

STACK = """\
# top comment
kind: stack
schema_version: 1
name: demo
services:
  gw:
    ref: service/litellm@1.0.0   # the gateway
mcp:
  github: {ref: mcp/github@1.0.0}
  other: {ref: mcp/other@1.0.0}
environment:
  gateway: gw
  models: [chat]
  ceilings: {budget_usd: 5, rpm: 60}
  agents:
    reader:
      sponsor: you@example.com   # sponsor comment
      review_by: "2027-05-12"
    writer:
      sponsor: you@example.com
      review_by: "2027-05-12"
      mcp:
        github:            # inner comment
          - get_me
          - list_issues
    flowy: {sponsor: you@example.com, review_by: "2027-05-12"}
# trailing comment
"""

OTHER = {
    "upstream": {"remote": {"url": "https://api.example.com/mcp"}},
    "auth": {"type": "bearer", "secret": "token"},
    "secrets": {"token": {"import": "OTHER_MCP_TOKEN"}},
    "tools": {
        "true": {"effect": "read"},
        "1.0": {"effect": "read"},
        "plain_tool": {"effect": "write"},
        "null": {"effect": "read"},
    },
}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    root = tmp_path / "proj"
    root.mkdir()
    (root / "stack.yaml").write_text(STACK)
    write_mcp(root, name="other", version="1.0.0", spec=OTHER)
    return root


def grants(root: Path) -> dict:
    data = yaml.safe_load((root / "stack.yaml").read_text())
    return {a: e.get("mcp") for a, e in data["environment"]["agents"].items()}


# -- the edit -----------------------------------------------------------------------------------


def test_grant_adds_one_line_and_keeps_everything_else(proj):
    before = (proj / "stack.yaml").read_text()
    result = change(proj, "grant", "reader", "github", ["get_me", "list_issues"])
    after = (proj / "stack.yaml").read_text()
    added = '      sponsor: you@example.com   # sponsor comment\n      review_by: "2027-05-12"\n'
    assert after == before.replace(added, added + "      mcp: {github: [get_me, list_issues]}\n")
    assert result["changed"] and result["added"] == ["get_me", "list_issues"]
    assert result["tools"] == [
        {"name": "get_me", "effect": "read"},
        {"name": "list_issues", "effect": "read"},
    ]
    assert result["base_sha256"] == mcpgrant.digest(before.encode())
    assert result["sha256"] == mcpgrant.digest(after.encode())
    assert "+      mcp: {github: [get_me, list_issues]}" in result["diff"]
    assert result["in_use"] == [] and result["dry_run"] is False
    load_stack(proj / "stack.yaml")


def test_grant_rewrites_a_block_grant_in_flow_style(proj):
    change(proj, "grant", "writer", "github", ["issue_read"])
    text = (proj / "stack.yaml").read_text()
    assert "      mcp: {github: [get_me, list_issues, issue_read]}\n    flowy:" in text
    assert "# inner comment" not in text  # inside the old value: dropped, shown in the diff
    assert text.startswith("# top comment") and text.endswith("# trailing comment\n")
    assert "# sponsor comment" in text and "# the gateway" in text


def test_grant_into_a_flow_style_agent(proj):
    change(proj, "grant", "flowy", "github", ["get_me"])
    text = (proj / "stack.yaml").read_text()
    assert (
        'flowy: {sponsor: you@example.com, review_by: "2027-05-12", mcp: {github: [get_me]}}'
        in text
    )


def test_a_second_server_is_appended_and_odd_tool_names_are_quoted(proj):
    change(proj, "grant", "writer", "other", ["true", "1.0", "null", "plain_tool"])
    text = (proj / "stack.yaml").read_text()
    assert 'other: ["true", "1.0", "null", plain_tool]' in text
    assert grants(proj)["writer"] == {
        "github": ["get_me", "list_issues"],
        "other": ["true", "1.0", "null", "plain_tool"],
    }
    load_stack(proj / "stack.yaml")


def test_revoke_narrows_then_drops_the_server(proj):
    result = change(proj, "revoke", "writer", "github", ["list_issues"])
    assert result["removed"] == ["list_issues"] and result["tools"][0]["name"] == "get_me"
    assert grants(proj)["writer"] == {"github": ["get_me"]}
    change(proj, "revoke", "writer", "github", ["get_me"])
    assert grants(proj)["writer"] == {}
    load_stack(proj / "stack.yaml")


def test_grant_and_revoke_are_idempotent_and_write_nothing(proj):
    path = proj / "stack.yaml"
    before = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    same = change(proj, "grant", "writer", "github", ["get_me", "get_me"])
    gone = change(proj, "revoke", "reader", "github", ["get_me"])
    assert not same["changed"] and same["added"] == [] and same["diff"] == ""
    assert not gone["changed"] and gone["removed"] == [] and gone["sha256"] == gone["base_sha256"]
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stamp


def test_revoke_may_name_a_tool_the_version_no_longer_declares(proj):
    text = (proj / "stack.yaml").read_text().replace("- list_issues", "- retired_tool")
    (proj / "stack.yaml").write_text(text)
    result = change(proj, "revoke", "writer", "github", ["retired_tool"])
    assert result["removed"] == ["retired_tool"]


def test_dry_run_then_expect_base(proj):
    path = proj / "stack.yaml"
    before = path.read_bytes()
    preview = change(proj, "grant", "reader", "github", ["get_me"], dry_run=True)
    assert preview["dry_run"] and preview["changed"] and path.read_bytes() == before
    done = change(
        proj, "grant", "reader", "github", ["get_me"], expect_base=preview["base_sha256"]
    )
    assert done["sha256"] == preview["sha256"] == mcpgrant.digest(path.read_bytes())
    with pytest.raises(GrantError) as error:
        change(
            proj, "grant", "reader", "github", ["list_issues"], expect_base=preview["base_sha256"]
        )
    assert error.value.code == "BASE_MISMATCH"


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("reader", "github", ["create_issue"]), "MCP_TOOL_UNDECLARED"),
        (("nobody", "github", ["get_me"]), "MCP_AGENT_UNKNOWN"),
        (("reader", "slack", ["get_me"]), "MCP_UNKNOWN"),
        (("reader", "github", ["bad tool"]), "MCP_TOOL_INVALID"),
        (("reader", "github", ["-x"]), "MCP_TOOL_INVALID"),
        (("reader", "github", []), "MCP_TOOL_INVALID"),
    ],
)
def test_refusals_write_nothing(proj, args, code):
    before = (proj / "stack.yaml").read_bytes()
    with pytest.raises(GrantError) as error:
        change(proj, "grant", *args)
    assert error.value.code == code
    assert (proj / "stack.yaml").read_bytes() == before


def test_bad_digest_missing_invalid_and_symlinked_stacks(proj, tmp_path):
    with pytest.raises(GrantError, match="DIGEST_INVALID"):
        change(proj, "grant", "reader", "github", ["get_me"], expect_base="sha256:abc")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(GrantError, match="STACK_MISSING"):
        change(empty, "grant", "reader", "github", ["get_me"])
    (empty / "stack.yaml").write_text("kind: stack\nschema_version: 1\n")
    with pytest.raises(GrantError, match="STACK_INVALID"):
        change(empty, "grant", "reader", "github", ["get_me"])
    (empty / "stack.yaml").write_bytes(b"\xff\xfe")
    with pytest.raises(GrantError, match="STACK_INVALID"):
        change(empty, "grant", "reader", "github", ["get_me"])
    (empty / "stack.yaml").unlink()
    (empty / "stack.yaml").symlink_to(proj / "stack.yaml")
    with pytest.raises(GrantError) as error:
        change(empty, "grant", "reader", "github", ["get_me"])
    assert error.value.code != "STACK_MISSING"
    assert "get_me" not in str(grants(proj)["reader"])


def test_an_unresolvable_server_version_is_refused(proj):
    text = (proj / "stack.yaml").read_text().replace("mcp/other@1.0.0", "mcp/other@9.9.9")
    (proj / "stack.yaml").write_text(text)
    with pytest.raises(GrantError, match="REF_NOT_FOUND"):
        change(proj, "grant", "reader", "other", ["plain_tool"])


def test_an_edit_that_would_change_more_is_refused(proj, monkeypatch):
    before = (proj / "stack.yaml").read_bytes()
    monkeypatch.setattr(
        mcpgrant, "_edit", lambda text, *a: text.replace("rpm: 60", "rpm: 61") + "\n"
    )
    with pytest.raises(GrantError, match="STACK_EDIT_AMBIGUOUS"):
        change(proj, "grant", "reader", "github", ["get_me"])
    monkeypatch.setattr(mcpgrant, "_edit", lambda text, *a: text + "\n: [\n")
    with pytest.raises(GrantError, match="STACK_EDIT_AMBIGUOUS"):
        change(proj, "grant", "reader", "github", ["get_me"])
    assert (proj / "stack.yaml").read_bytes() == before


def test_a_folded_scalar_before_the_insert(proj):
    text = (
        (proj / "stack.yaml")
        .read_text()
        .replace(
            '      review_by: "2027-05-12"\n    writer:',
            "      review_by: >-\n        2027-05-12\n    writer:",
        )
    )
    (proj / "stack.yaml").write_text(text)
    change(proj, "grant", "reader", "github", ["get_me"])
    assert (
        "        2027-05-12\n      mcp: {github: [get_me]}\n    writer:"
        in (proj / "stack.yaml").read_text()
    )


def test_crlf_files_keep_their_line_endings(proj):
    path = proj / "stack.yaml"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    change(proj, "grant", "reader", "github", ["get_me"])
    data = path.read_bytes()
    assert b"\r\n      mcp: {github: [get_me]}\r\n" in data
    assert data.count(b"\n") == data.count(b"\r\n")


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b"\r", b"\r      mcp: {github: [get_me]}\r"),  # CR only
        (b"\n", b"\n      mcp: {github: [get_me]}\n"),
    ],
)
def test_the_inserted_line_uses_its_own_line_break(proj, before, after):
    path = proj / "stack.yaml"
    path.write_bytes(
        path.read_bytes().replace(b"\n", before).replace(b"name: demo" + before, b"name: demo\r\n")
    )
    change(proj, "grant", "reader", "github", ["get_me"])
    assert after in path.read_bytes()


def test_line_end_at_the_end_of_the_file():
    assert mcpgrant._line_end("a: 1\r\nb: 2", 8) == (10, "\r\n")
    assert mcpgrant._line_end("a: 1", 0) == (4, "\n")
    assert mcpgrant._line_end("a: 1\rb: 2", 6) == (9, "\r")


def test_a_grant_that_would_pass_the_size_limit_is_refused(proj):
    path = proj / "stack.yaml"
    padded = path.read_text() + "#" * (mcpgrant.MAX_BYTES - path.stat().st_size - 10) + "\n"
    path.write_text(padded)
    with pytest.raises(GrantError, match="STACK_TOO_LARGE"):
        change(proj, "grant", "reader", "github", ["get_me"])
    assert path.read_text() == padded


def test_unknown_verb():
    with pytest.raises(ValueError):
        change(Path("."), "drop", "a", "b", ["c"])


def test_flow_quotes_what_yaml_would_read_differently():
    assert mcpgrant.flow({}) == "{}"
    assert mcpgrant.flow({"s": ["a", "yes", "1", "~", "x.y"]}) == '{s: [a, "yes", "1", "~", x.y]}'


def test_content_end_of_an_empty_block_collection():
    node = yaml.compose("a: []\n")
    assert mcpgrant._content_end(node) == node.value[-1][1].end_mark.index


def test_edit_refuses_shapes_it_cannot_place():
    with pytest.raises(GrantError, match="STACK_EDIT_AMBIGUOUS"):
        mcpgrant._child(yaml.compose("- a\n"), "environment", "the document")
    with pytest.raises(GrantError, match="STACK_EDIT_AMBIGUOUS"):
        mcpgrant._child(yaml.compose("a: 1\n"), "environment", "the document")


# -- in_use -------------------------------------------------------------------------------------


def test_revoke_reports_the_deployments_that_hold_the_tool(project):  # noqa: F811
    result, _ = run_up(project, RelayDocker())
    _active_grant(result, ["create_issue", "list_issues"])
    other = stack_dir(result) / "grants" / "dev" / "github" / ("0" * 64 + ".json")
    other.write_text(
        json.dumps({"deployment": "e" * 32, "agent": "someone-else", "tools": ["create_issue"]})
    )
    revoked = change(project, "revoke", "demo-agent", "github", ["create_issue"], dry_run=True)
    assert revoked["in_use"] == [
        {"deployment": "d" * 32, "environment": "dev", "tools": ["create_issue"]}
    ]
    granted = change(project, "grant", "demo-agent", "github", ["delete_repo"], dry_run=True)
    assert granted["in_use"] == []


def test_in_use_reports_a_malformed_grant_file(project, capsys):  # noqa: F811
    from agentbreeder import cli as cli_module

    result, _ = run_up(project, RelayDocker())
    directory = stack_dir(result) / "grants" / "dev" / "github"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ("1" * 64 + ".json")).write_text(json.dumps({"agent": "x", "tools": "all"}))
    revoked = change(project, "revoke", "demo-agent", "github", ["create_issue"], dry_run=True)
    assert revoked["in_use"] == [
        {"deployment": None, "environment": "dev", "tools": ["create_issue"]}
    ]
    args = ["mcp", "revoke", "demo-agent", "github", "create_issue", "--project", str(project)]
    assert cli_module.main(args) == 0
    out = capsys.readouterr().out
    assert "an unreadable grant file in dev for mcp.github" in out and "reconcile" in out


def test_in_use_is_empty_before_services_up(proj):
    assert mcpgrant.in_use(proj, "demo", "writer", "github", ["get_me"]) == []


# -- the offline acceptance sequence ------------------------------------------------------------


def _relay_agents(result) -> dict:
    compose = yaml.safe_load((stack_dir(result) / "compose.yaml").read_text())
    volumes = compose["services"]["mcp-github"]["volumes"]
    source = next(v["source"] for v in volumes if v["target"] == "/run/params/config")
    return json.loads(Path(source).read_text())["agents"]


def test_grant_then_up_reaches_the_relay_and_up_refuses_a_held_revoke(project):  # noqa: F811
    write_project(project, stack=mcp_stack_data(tools=("list_issues",)))
    docker = RelayDocker()
    result, _ = run_up(project, docker)
    assert _relay_agents(result) == {"demo-agent": ["list_issues"]}

    change(project, "grant", "demo-agent", "github", ["create_issue"])
    result, _ = run_up(project, docker)
    assert _relay_agents(result) == {"demo-agent": ["create_issue", "list_issues"]}

    _active_grant(result, ["create_issue", "list_issues"])  # a deployment holds both
    revoked = change(project, "revoke", "demo-agent", "github", ["create_issue"])
    assert revoked["in_use"][0]["tools"] == ["create_issue"]
    with pytest.raises(ServiceError, match="MCP_GRANT_IN_USE"):
        run_up(project, docker)

    for path in (stack_dir(result) / "grants" / "dev" / "github").glob("*.json"):
        path.unlink()  # what `remove` does to the deployment's grant file
    result, _ = run_up(project, docker)
    assert _relay_agents(result) == {"demo-agent": ["list_issues"]}


# -- the CLI ------------------------------------------------------------------------------------


def cli(*args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, "-m", "agentbreeder", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=120,
    )


@pytest.fixture
def cli_env(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != registry.PATH_ENV}
    env[ROOT_ENV] = str(tmp_path / "stacks")
    return env


def test_cli_grant_text_dry_run_and_json(proj, cli_env):
    dry = cli(
        "mcp",
        "grant",
        "reader",
        "github",
        "get_me",
        "--project",
        str(proj),
        "--dry-run",
        env=cli_env,
    )
    assert dry.returncode == 0, dry.stderr
    assert "+      mcp: {github: [get_me]}" in dry.stdout
    assert "dry run: nothing written" in dry.stdout and "--expect-base sha256:" in dry.stdout
    base = dry.stdout.split("--expect-base ")[1].split()[0]
    done = cli(
        "mcp", "grant", "reader", "github", "get_me", "--project", str(proj),
        "--expect-base", base, env=cli_env,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert "Granted get_me for reader on mcp.github; it now has: get_me (read)" in done.stdout
    assert "Next: " in done.stdout and "services up" in done.stdout
    assert "--environment ENV" in done.stdout
    again = cli("mcp", "grant", "reader", "github", "get_me", "--project", str(proj), env=cli_env)
    assert again.returncode == 0 and "Nothing to change: get_me already granted" in again.stdout
    out = cli(
        "mcp",
        "revoke",
        "reader",
        "github",
        "get_me",
        "--project",
        str(proj),
        "--json",
        env=cli_env,
    )
    assert out.returncode == 0, out.stderr
    body = json.loads(out.stdout)
    assert body["verb"] == "revoke" and body["removed"] == ["get_me"] and body["tools"] == []
    assert "Next:" not in out.stdout
    none = cli("mcp", "revoke", "reader", "github", "get_me", "--project", str(proj), env=cli_env)
    assert "Nothing to change: get_me not granted" in none.stdout


def test_cli_dry_run_revoke_text(proj, cli_env):
    out = cli(
        "mcp",
        "revoke",
        "writer",
        "github",
        "get_me",
        "--project",
        str(proj),
        "--dry-run",
        env=cli_env,
    )
    assert out.returncode == 0
    assert "Would revoke get_me for writer on mcp.github" in out.stdout


def test_cli_refusal_text_and_json(proj, cli_env):
    out = cli(
        "mcp", "grant", "reader", "github", "create_issue", "--project", str(proj), env=cli_env
    )
    assert out.returncode == 2 and out.stdout == ""
    assert "[MCP_TOOL_UNDECLARED]" in out.stderr
    out = cli(
        "mcp",
        "grant",
        "reader",
        "github",
        "create_issue",
        "--project",
        str(proj),
        "--json",
        env=cli_env,
    )
    assert out.returncode == 2
    assert json.loads(out.stdout)["error"]["code"] == "MCP_TOOL_UNDECLARED"
    bad = cli("mcp", "grant", "reader", "github", "x", "--expect-base", "nope", env=cli_env)
    assert bad.returncode == 2 and "sha256:<64 hex digits>" in bad.stderr


def test_cli_revoke_in_use_names_remove_then_up(project, cli_env):  # noqa: F811
    result, _ = run_up(project, RelayDocker())
    _active_grant(result, ["create_issue", "list_issues"])
    env = {**cli_env, ROOT_ENV: os.environ[ROOT_ENV]}
    out = cli(
        "mcp", "revoke", "demo-agent", "github", "create_issue", "--project", str(project), env=env
    )
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    use = next(
        i for i, line in enumerate(lines) if line.startswith("In use: deployment " + "d" * 32)
    )
    assert "remove " + "d" * 32 + " --local --stack" in lines[use + 1]
    assert lines[use + 2].startswith("Next: ") and "services up" in lines[use + 2]
    assert lines[use + 2].endswith("--environment dev")


# -- the fixture --------------------------------------------------------------------------------


def test_fixture_is_the_starter_plus_mcp_github(tmp_path, monkeypatch, cli_env):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    root = tmp_path / "fixture"
    shutil.copytree(FIXTURE, root)
    stack = load_stack(root / "stack.yaml")
    assert stack.mcp["github"].ref == "mcp/github@1.0.0"
    assert stack.environment.agents["gh-reader"].mcp == {}
    agent = yaml.safe_load((root / "agent.yaml").read_text())
    assert agent["mcp"] == [{"ref": "mcp/github@1.0.0", "tools": ["get_me", "list_issues"]}]
    valid = cli("validate", str(root / "agent.yaml"), env=cli_env)
    assert valid.returncode == 0, valid.stderr
    result = change(root, "grant", "gh-reader", "github", ["get_me", "list_issues"])
    assert [t["effect"] for t in result["tools"]] == ["read", "read"]
    assert load_stack(root / "stack.yaml").environment.agents["gh-reader"].mcp == {
        "github": ["get_me", "list_issues"]
    }


# -- live acceptance (deferred to the batched M15 verification) ---------------------------------

RELAY_LIST = r"""
import json, os, urllib.request
url = os.environ["AGENTBREEDER_MCP_GITHUB_URL"]
token = open(os.environ["AGENTBREEDER_MCP_GITHUB_TOKEN_FILE"]).read().strip()
head = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"}
def post(body, session=None):
    h = dict(head, **({"Mcp-Session-Id": session} if session else {}))
    request = urllib.request.Request(url, json.dumps(body).encode(), h)
    r = urllib.request.urlopen(request, timeout=60)
    text = r.read().decode()
    data = [l[5:] for l in text.splitlines() if l.startswith("data:")] or [text]
    return r.headers.get("Mcp-Session-Id"), json.loads(data[-1]) if data[-1].strip() else None
client = {"name": "m15", "version": "1"}
params = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": client}
session, _ = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
post({"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
_, listed = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session)
print(json.dumps(sorted(t["name"] for t in listed["result"]["tools"])))
"""


@pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1" or not os.getenv("GITHUB_MCP_TOKEN"),
    reason="needs Docker (AGENTBREEDER_DOCKER_TEST=1) and GITHUB_MCP_TOKEN",
)
def test_fixture_live_grant_and_revoke(tmp_path):  # pragma: no cover - live
    """#831 acceptance: granting a tool and updating services makes the agent list it; revoking
    removes it. The agent's view is the relay's `tools/list` with the deployment's own token,
    run inside the agent container."""
    env = {k: v for k, v in os.environ.items() if k != registry.PATH_ENV}
    env[ROOT_ENV] = str(tmp_path / "stacks")
    root = tmp_path / "gh"
    shutil.copytree(FIXTURE, root)
    env_file = tmp_path / "env"
    env_file.write_text(
        f"GEMINI_API_KEY={os.getenv('GEMINI_API_KEY', 'unused-no-model-call')}\n"
        f"GITHUB_MCP_TOKEN={os.environ['GITHUB_MCP_TOKEN']}\n"
    )
    env_file.chmod(0o600)
    stack = ["--stack", str(root), "--environment", "dev"]

    def run(*args, ok=True):
        result = subprocess.run(
            [sys.executable, "-m", "agentbreeder", *args],
            capture_output=True, text=True, env=env, timeout=1800,
        )  # fmt: skip
        if ok:
            assert result.returncode == 0, (args[:2], result.stderr[-1500:])
        return result

    def up():
        return run(
            "services",
            "up",
            str(root),
            "--environment",
            "dev",
            "--env-file",
            str(env_file),
            "--yes",
            ok=False,
        )

    def deploy():
        built = json.loads(run("build", str(root / "agent.yaml"), "--json", *stack).stdout)
        return run("deploy", built["manifest_path"], "--local", "--json", *stack, ok=False)

    def listed(deployment):
        name = next(
            c
            for c in subprocess.check_output(
                ["docker", "ps", "--format", "{{.Names}}"], text=True
            ).split()
            if deployment[:8] in c
        )
        out = subprocess.check_output(
            ["docker", "exec", name, "python3", "-c", RELAY_LIST], text=True
        )
        return json.loads(out.strip().splitlines()[-1])

    deployment = None
    try:
        assert up().returncode == 0
        refused = deploy()
        assert (
            refused.returncode != 0 and "MCP_TOOL_NOT_GRANTED" in refused.stdout + refused.stderr
        )

        run("mcp", "grant", "gh-reader", "github", "get_me", "list_issues", "--project", str(root))
        assert up().returncode == 0
        deployed = deploy()
        assert deployed.returncode == 0, deployed.stderr[-1500:]
        deployment = json.loads(deployed.stdout)["id"]
        assert listed(deployment) == ["get_me", "list_issues"]

        revoked = json.loads(
            run(
                "mcp",
                "revoke",
                "gh-reader",
                "github",
                "list_issues",
                "--project",
                str(root),
                "--json",
            ).stdout
        )
        assert revoked["in_use"][0]["deployment"] == deployment
        held = up()
        assert held.returncode != 0 and "MCP_GRANT_IN_USE" in held.stderr
        run("remove", deployment, "--local", "--stack", str(root))
        deployment = None
        assert up().returncode == 0
        refused = deploy()  # agent.yaml still requests list_issues
        assert "MCP_TOOL_NOT_GRANTED" in refused.stdout + refused.stderr
        text = (root / "agent.yaml").read_text().replace("[get_me, list_issues]", "[get_me]")
        (root / "agent.yaml").write_text(text)
        deployed = deploy()
        assert deployed.returncode == 0, deployed.stderr[-1500:]
        deployment = json.loads(deployed.stdout)["id"]
        assert listed(deployment) == ["get_me"]
    finally:
        if deployment:
            run("remove", deployment, "--local", "--stack", str(root), ok=False)
        run("services", "down", str(root), "--purge", "--yes", ok=False)


# -- in-process CLI (coverage of commands/mcp.py) -----------------------------------------------


def test_cli_main_in_process(proj, capsys):
    from agentbreeder import cli as cli_module

    args = ["mcp", "grant", "reader", "github", "get_me", "--project", str(proj)]
    assert cli_module.main([*args, "--dry-run"]) == 0
    assert "Would grant get_me" in capsys.readouterr().out
    assert cli_module.main(args) == 0
    assert "Granted get_me" in capsys.readouterr().out
    assert cli_module.main(args) == 0
    assert "Nothing to change" in capsys.readouterr().out
    assert cli_module.main([*args[:4], "create_issue", *args[5:], "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "MCP_TOOL_UNDECLARED"
    assert "[MCP_TOOL_UNDECLARED]" in captured.err
    assert cli_module.main(["mcp", "revoke", *args[2:], "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["removed"] == ["get_me"]


def test_cli_main_in_use_lines(project, capsys):  # noqa: F811
    from agentbreeder import cli as cli_module

    result, _ = run_up(project, RelayDocker())
    _active_grant(result, ["create_issue", "list_issues"])
    args = ["mcp", "revoke", "demo-agent", "github", "create_issue", "--project", str(project)]
    assert cli_module.main(args) == 0
    out = capsys.readouterr().out
    assert "In use: deployment " + "d" * 32 + " (dev) holds create_issue" in out
    assert out.rstrip().endswith("--environment dev")


def test_a_failed_write_is_reported(proj, monkeypatch):
    def fail(*_):
        raise registry.RegistryError("BASE_MISMATCH", "stack.yaml changed while it was rewritten")

    monkeypatch.setattr(mcpgrant, "_replace", fail)
    with pytest.raises(GrantError) as error:
        change(proj, "grant", "reader", "github", ["get_me"])
    assert error.value.code == "BASE_MISMATCH" and "[" not in error.value.message


def test_in_use_skips_a_symlinked_server_directory(project, tmp_path):  # noqa: F811
    result, _ = run_up(project, RelayDocker())
    _active_grant(result, ["create_issue"], server="elsewhere")
    real = stack_dir(result) / "grants" / "dev" / "elsewhere"
    link = stack_dir(result) / "grants" / "dev" / "github"
    shutil.rmtree(link, ignore_errors=True)
    link.symlink_to(real)
    assert mcpgrant.in_use(project, "demo", "demo-agent", "github", ["create_issue"]) == []


def test_cli_main_refuses_a_bad_expect_base(capsys):
    from agentbreeder import cli as cli_module

    with pytest.raises(SystemExit) as exit_:
        cli_module.main(["mcp", "grant", "a", "b", "c", "--expect-base", "nope"])
    assert exit_.value.code == 2 and "sha256:<64 hex digits>" in capsys.readouterr().err
