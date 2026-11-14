# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Grant and revoke from the console (#831, M15 console half, part 2): `POST .../mcp/grant` runs
the real `mcp grant|revoke --json` over a copy of `tests/fixtures/mcp_github_project`, a dry run
first, then the write bound to the reviewed digest of stack.yaml. `GET .../mcp/tools` lists what
the server's version declares, from `registry show --json`."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from test_center import api, center, cookie, project  # noqa: F401 (fixtures)

from agentbreeder import registry
from agentbreeder.center import jobs, mcpgrants
from agentbreeder.services import schema

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_github_project"
DIGEST = "sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def no_registry_path(monkeypatch, tmp_path):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    # `in_use` reads the stack's grant files under AGENTBREEDER_HOME: none for a fresh copy.
    monkeypatch.setenv("AGENTBREEDER_HOME", str(tmp_path / "home"))


@pytest.fixture
def fake_cli():
    """The real CLI (the server's own default prefix)."""
    return [sys.executable, "-I", "-m", "agentbreeder"]


@pytest.fixture
def project_dir(tmp_path) -> Path:
    target = tmp_path / "gh-reader"
    shutil.copytree(FIXTURE, target)
    return target


@pytest.fixture
def calls(center, monkeypatch):
    """Every CLI child's argv, recorded around the real runner."""
    seen: list[list[str]] = []
    real = center.runner.read

    def read(argv, *args, **kwargs):
        seen.append(list(argv))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(center.runner, "read", read)
    return seen


def grant(center, project, cookie, **body):
    return api(center, "POST", f"/api/projects/{project['id']}/mcp/grant", body, cookie)


def test_grant_shows_the_diff_then_writes_the_reviewed_file(
    center, project, cookie, project_dir, calls
):
    path = project_dir / "stack.yaml"
    before = path.read_text()
    body = {"verb": "grant", "agent": "gh-reader", "server": "github", "tools": ["get_me"]}
    status, plan, _ = grant(center, project, cookie, **body)
    assert status == 200 and plan["error"] is None, plan
    data = plan["data"]
    assert data["dry_run"] is True and data["changed"] is True and path.read_text() == before
    assert data["moved"] == ["get_me"]
    assert data["tools"] == [{"name": "get_me", "effect": "read"}]
    assert "+      mcp: {github: [get_me]}" in data["diff"]
    assert data["in_use"] == [] and "file" not in data
    assert calls[-1][:2] == ["mcp", "grant"] and "--dry-run" in calls[-1]
    assert calls[-1][calls[-1].index("--") + 1 :] == ["gh-reader", "github", "get_me"]

    path.write_text(before + "# edited meanwhile\n")
    _, stale, _ = grant(center, project, cookie, **body, base=data["base_sha256"])
    assert stale["error"]["code"] == "BASE_MISMATCH"
    assert "get_me" not in path.read_text()

    _, plan, _ = grant(center, project, cookie, **body)
    _, done, _ = grant(center, project, cookie, **body, base=plan["data"]["base_sha256"])
    assert done["error"] is None and done["data"]["dry_run"] is False, done
    assert "mcp: {github: [get_me]}" in path.read_text()
    assert path.read_text().startswith(before.split("\n")[0])  # the rest kept, comments too
    argv = calls[-1]
    assert argv[argv.index("--expect-base") + 1] == plan["data"]["base_sha256"]


def test_revoke_drops_the_server_with_its_last_tool(center, project, cookie, project_dir):
    body = {"agent": "gh-reader", "server": "github"}
    _, plan, _ = grant(
        center, project, cookie, verb="grant", tools=["get_me", "list_issues"], **body
    )
    grant(center, project, cookie, verb="grant", tools=["get_me", "list_issues"], **body,
          base=plan["data"]["base_sha256"])  # fmt: skip
    _, plan, _ = grant(
        center, project, cookie, verb="revoke", tools=["get_me", "list_issues"], **body
    )
    assert plan["data"]["moved"] == ["get_me", "list_issues"] and plan["data"]["tools"] == []
    _, done, _ = grant(center, project, cookie, verb="revoke", tools=["get_me", "list_issues"],
                       **body, base=plan["data"]["base_sha256"])  # fmt: skip
    assert done["error"] is None
    assert "mcp: {github" not in (project_dir / "stack.yaml").read_text()


def test_nothing_to_change_is_not_a_refusal(center, project, cookie):
    _, plan, _ = grant(
        center,
        project,
        cookie,
        verb="revoke",
        agent="gh-reader",
        server="github",
        tools=["get_me"],
    )
    assert (
        plan["error"] is None and plan["data"]["changed"] is False and plan["data"]["diff"] == ""
    )


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"agent": "nobody", "server": "github", "tools": ["get_me"]}, "MCP_AGENT_UNKNOWN"),
        ({"agent": "gh-reader", "server": "slack", "tools": ["get_me"]}, "MCP_UNKNOWN"),
        (
            {"agent": "gh-reader", "server": "github", "tools": ["delete_repo"]},
            "MCP_TOOL_UNDECLARED",
        ),
    ],
)
def test_refusals_come_from_the_cli(center, project, cookie, body, code):
    _, data, _ = grant(center, project, cookie, verb="grant", **body)
    assert data["data"] is None and data["error"]["code"] == code


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"verb": "add"}, "MCP_GRANT_INVALID"),
        ({"agent": "--project=/"}, "MCP_GRANT_INVALID"),
        ({"agent": "Gh"}, "MCP_GRANT_INVALID"),
        ({"server": "-x"}, "MCP_GRANT_INVALID"),
        ({"tools": []}, "MCP_TOOL_INVALID"),
        ({"tools": "get_me"}, "MCP_TOOL_INVALID"),
        ({"tools": ["--dry-run"]}, "MCP_TOOL_INVALID"),
        ({"tools": ["a b"]}, "MCP_TOOL_INVALID"),
        ({"tools": [f"t{i}" for i in range(101)]}, "MCP_TOOL_INVALID"),
        ({"base": "abc"}, "DIGEST_INVALID"),
    ],
)
def test_bad_requests_never_reach_the_cli(center, project, cookie, calls, body, code):
    base = {"verb": "grant", "agent": "gh-reader", "server": "github", "tools": ["get_me"]}
    status, data, _ = grant(center, project, cookie, **(base | body))
    assert status == 400 and data["error"]["code"] == code
    assert calls == []


def test_a_moved_project_is_refused_before_any_write(center, project, cookie, project_dir, calls):
    moved = project_dir.with_name("elsewhere")
    project_dir.rename(moved)
    project_dir.symlink_to(moved)
    body = {"verb": "grant", "agent": "gh-reader", "server": "github", "tools": ["get_me"]}
    status, data, _ = grant(center, project, cookie, **body)
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"
    assert calls == []


def test_the_write_is_a_short_write_holding_the_project(center, project, cookie, monkeypatch):
    seen = []

    def short_write(kind, target, argv, timeout=None, project=None, input=None):  # noqa: A002
        seen.append((kind, target, project and project["id"], argv[:2]))
        return {"data": None, "error": {"code": "X_Y", "message": "m"}, "stale": False}

    monkeypatch.setattr(center.runner, "short_write", short_write)
    body = {"verb": "revoke", "agent": "gh-reader", "server": "github", "tools": ["get_me"]}
    grant(center, project, cookie, **body, base=DIGEST)
    assert seen == [(mcpgrants.KIND, project["id"], project["id"], ["mcp", "revoke"])]


def test_the_write_waits_for_a_running_job_of_its_project(center, project, cookie):
    runner = center.runner
    with runner.mutex:
        runner.held[f"project:{project['id']}"] = "job-1"
    try:
        body = {"verb": "grant", "agent": "gh-reader", "server": "github", "tools": ["get_me"]}
        status, data, _ = grant(center, project, cookie, **body, base=DIGEST)
        assert status == 409 or data["error"]["code"] == "WRITE_BUSY", data
    finally:
        with runner.mutex:
            runner.held.clear()


def test_declared_tools_come_from_the_versions_mcp_yaml(center, project, cookie):
    path = f"/api/projects/{project['id']}/mcp/tools?ref=mcp/github@1.0.0"
    _, data, _ = api(center, "GET", path, cookie=cookie)
    assert data["error"] is None, data
    assert data["data"] == [
        {"name": name, "effect": "read"}
        for name in sorted(
            ["get_me", "list_issues", "issue_read", "search_repositories", "get_file_contents"]
        )
    ]


@pytest.mark.parametrize("ref", ["prompt/x@1.0.0", "mcp/github", "--json", ""])
def test_declared_tools_need_an_exact_mcp_ref(center, project, cookie, ref):
    status, data, _ = api(
        center, "GET", f"/api/projects/{project['id']}/mcp/tools?ref={ref}", cookie=cookie
    )
    assert status == 400 and data["error"]["code"] in ("REF_INVALID", "REF_UNPINNED")


# -- the pure checks ------------------------------------------------------------------------------

REQUEST = {"verb": "revoke", "agent": "a", "server": "github", "tools": ["t"], "base": None}
ANSWER = {
    "schema_version": 1,
    "file": "/x/stack.yaml",
    "verb": "revoke",
    "agent": "a",
    "server": "github",
    "ref": "mcp/github@1.0.0",
    "removed": ["t"],
    "tools": [{"name": "u", "effect": "read"}, {"name": "v", "effect": None}],
    "changed": True,
    "base_sha256": DIGEST,
    "sha256": "sha256:" + "b" * 64,
    "diff": "--- a\n+++ b\n",
    "dry_run": True,
    "in_use": [
        {"deployment": "d" * 32, "environment": "dev", "tools": ["t"]},
        {"deployment": None, "environment": "dev", "tools": ["t"]},
    ],
}


def test_a_good_answer_keeps_only_known_keys():
    shaped, problem = mcpgrants.grant_answer(ANSWER, REQUEST)
    assert problem is None
    assert "file" not in shaped and shaped["moved"] == ["t"] and len(shaped["in_use"]) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"verb": "grant"},
        {"agent": "b"},
        {"server": "other"},
        {"dry_run": False},
        {"changed": "yes"},
        {"removed": ["--x", 1]},
        {"tools": [{"name": "u", "effect": "delete"}]},
        {"tools": [{"name": "<b>"}]},
        {"diff": 3},
        {"base_sha256": "abc"},
        {"ref": "prompt/x@1.0.0"},
        {"ref": "nope"},
        {"in_use": [{"deployment": "../x", "environment": "dev", "tools": ["t"]}]},
        {"in_use": [{"deployment": None, "environment": "a b", "tools": ["t"]}]},
        {"in_use": "x"},
    ],
)
def test_a_changed_answer_is_invalid(change):
    shaped, problem = mcpgrants.grant_answer(ANSWER | change, REQUEST)
    assert shaped is None and problem["code"] == "MCP_GRANT_INVALID"


def test_the_patterns_match_the_clis():
    assert mcpgrants.TOOL.pattern == schema.MCP_TOOL.strip("^$")
    assert mcpgrants.SERVER.pattern == schema.MCP_SERVER.strip("^$")
    # `StackEnvironment.names` checks agent names with this literal pattern.
    source = Path(schema.__file__).read_text()
    assert f'r"^{mcpgrants.AGENT.pattern}$"' in source


def test_the_lock_timeout_kind_is_a_short_write():
    assert mcpgrants.KIND not in jobs.SHORT_TIMEOUTS  # the default short timeout


@pytest.mark.parametrize(
    "show",
    [
        None,
        {"kind": "tool", "files": []},
        {"kind": "mcp", "files": []},
        {"kind": "mcp", "files": [{"path": "mcp.yaml", "text": "x", "truncated": True}]},
        {"kind": "mcp", "files": [{"path": "mcp.yaml", "text": "tools: [a]"}]},
        {"kind": "mcp", "files": [{"path": "mcp.yaml", "text": "tools: {a: {effect: rm}}"}]},
        {"kind": "mcp", "files": [{"path": "mcp.yaml", "text": "tools: {'a b': {effect: read}}"}]},
        {"kind": "mcp", "files": [{"path": "mcp.yaml", "text": "a: &x 1\nb: *x"}]},
    ],
)
def test_unreadable_declared_tools(show):
    rows, problem = mcpgrants.declared_tools(show)
    assert rows is None and problem["code"] == "MCP_TOOLS_UNREADABLE"
