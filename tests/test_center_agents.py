# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Agents (#823, M7): the list across stacks (`list --all --json`, linked to registered projects),
an agent's release history from its project's audit chain, and what `stack.yaml` declares for it
(the identity strip until #909)."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest
from test_center import api, center, cookie, project, project_dir  # noqa: F401 (fixtures)

from agentbreeder.center import agents

NORTHWIND_STACK = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "agentbreeder"
    / "examples"
    / "northwind"
    / "stack.yaml"
)


def _row(seq, decision, *reasons, action="release"):
    return {
        "seq": seq,
        "at": f"2026-11-13T10:00:{seq:02d}Z",
        "action": action,
        "decision": decision,
        "actor": "local:owner",
        "actor_verified": False,
        "reasons": [{"code": code, "message": message} for code, message in reasons],
    }


IMAGE = "agentbreeder/support:abc"
PROMOTED = _row(
    7,
    "promoted",
    ("RELEASE_EVAL", "run-2 5/5"),
    ("RELEASE_IMAGE", IMAGE),
    ("RELEASE_MANIFEST", "f" * 64),
    ("RELEASE_THRESHOLD", "1.0"),
    ("RELEASE_EVALSET", "e" * 64),
)
EVAL_FAILED = _row(4, "eval_failed", ("RELEASE_EVAL", "run-1 3/5"), ("RELEASE_THRESHOLD", "0.8"))
# An eval run that crashed: the outcome holds only its error; the image is on `started`.
FAILED = _row(2, "failed", ("EVAL_RUN_FAILED", "the judge timed out"))
AUDIT = [
    PROMOTED,
    _row(6, "allow", action="deploy"),
    _row(5, "started", ("RELEASE_IMAGE", IMAGE)),
    EVAL_FAILED,
    _row(3, "started", ("RELEASE_IMAGE", "agentbreeder/support:early")),
    FAILED,
    _row(1, "started", ("RELEASE_IMAGE", "agentbreeder/support:first")),
    "garbage",
]

# `list --all` prints marker/list-all.json; `audit list` prints marker/audit.json; `services env`
# names a state dir unless marker/no-state exists.
FAKE = textwrap.dedent(
    """
    import json, os, sys
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if argv[:2] == ["list", "--all"]:
        open(marker + "/list-argv", "w").write(json.dumps(argv))
        print(open(marker + "/list-all.json").read())
    elif argv[:2] == ["services", "env"]:
        if not os.path.exists(marker + "/no-state"):
            print("export AGENTBREEDER_STATE_DIR=/tmp/fake-state")
    elif argv[:2] == ["audit", "list"]:
        open(marker + "/audit-argv", "w").write(json.dumps(argv))
        print(open(marker + "/audit.json").read())
    elif argv[0] == "doctor":
        print("[]")
    else:
        print("ok")
    """
)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "fake_cli.py"
    path.write_text(FAKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    (tmp_path / "audit.json").write_text(json.dumps(AUDIT))
    return [sys.executable, str(path)]


AGENT_YAML = """\
kind: agent
schema_version: 1
name: support
runtime: runtime/adk@1.1.0
model: {alias: chat}
prompt: {path: prompt.md}
"""

# -- pure --------------------------------------------------------------------------------


def test_releases_pair_each_outcome_with_its_started_row():
    rows = agents.releases(AUDIT)
    assert [(r["seq"], r["decision"]) for r in rows] == [
        (7, "promoted"),
        (4, "eval_failed"),
        (2, "failed"),
    ]
    first = rows[0]
    assert first["score"] == {"run": "run-2", "passed": 5, "cases": 5}
    assert first["threshold"] == 1.0
    assert first["image"] == IMAGE and first["started"] == "2026-11-13T10:00:05Z"
    assert first["manifest"] == "f" * 64
    assert first["problem"] is None
    assert rows[1]["score"] == {"run": "run-1", "passed": 3, "cases": 5}
    assert rows[1]["image"] == "agentbreeder/support:early"  # from its `started` row
    assert rows[2]["score"] is None
    assert rows[2]["image"] == "agentbreeder/support:first"
    assert rows[2]["problem"] == {"code": "EVAL_RUN_FAILED", "message": "the judge timed out"}


def test_a_started_run_without_an_outcome_is_incomplete():
    rows = agents.releases(
        [
            _row(1, "started", ("RELEASE_IMAGE", "img:1")),  # candidate failed to start
            _row(2, "started", ("RELEASE_IMAGE", "img:2")),
            _row(3, "promoted", ("RELEASE_EVAL", "r 1/1")),
            _row(4, "started", ("RELEASE_IMAGE", "img:4")),  # still running
        ]
    )
    assert [(r["seq"], r["decision"], r["image"]) for r in rows] == [
        (4, "incomplete", "img:4"),
        (3, "promoted", "img:2"),
        (1, "incomplete", "img:1"),
    ]


def test_rollbacks_are_their_own_entries():
    rows = agents.releases(
        [
            _row(1, "started", ("RELEASE_IMAGE", "img:new")),
            _row(2, "promoted", ("RELEASE_EVAL", "r 1/1"), ("RELEASE_IMAGE", "img:new")),
            _row(3, "rolled_back", ("RELEASE_IMAGE", "img:old")),
            _row(4, "rollback_failed", ("RELEASE_IMAGE", "img:older")),
        ]
    )
    assert [(r["decision"], r["image"]) for r in rows] == [
        ("rollback_failed", "img:older"),
        ("rolled_back", "img:old"),
        ("promoted", "img:new"),
    ]


def test_releases_tolerate_malformed_rows():
    assert agents.releases(None) == []
    assert agents.releases({"rows": []}) == []
    odd = _row(1, "promoted", ("RELEASE_EVAL", "not a score"), ("RELEASE_THRESHOLD", "x"))
    odd["reasons"].append("junk")
    odd["actor"] = "a" * 1000
    no_seq = _row(2, "promoted")
    no_seq["seq"] = "2"
    [row] = agents.releases([odd, no_seq])
    assert row["score"] is None and row["threshold"] is None and len(row["actor"]) == 300


def test_rows_link_to_the_project_registered_for_their_path_and_environment(tmp_path):
    support = tmp_path / "support"
    support.mkdir()
    (tmp_path / "link").symlink_to(support)
    projects = [
        {"id": "aaaaaaaaaaaa", "path": str(support), "environment": "local"},
        {"id": "bbbbbbbbbbbb", "path": "/work/billing", "environment": "staging"},
        {"id": "cccccccccccc", "path": str(support), "environment": "local"},  # first one wins
    ]
    listing = {
        "schema_version": 1,
        "deployments": [
            {"id": "1", "name": "support", "project": f"{support}/", "environment": "local"},
            {"id": "2", "name": "link", "project": str(tmp_path / "link"), "environment": "local"},
            {"id": "3", "name": "billing", "project": "/work/billing", "environment": "local"},
            {"id": "4", "name": "other", "project": "relative/path", "environment": "local"},
            {"id": "5", "name": "default", "project": None, "stack": None, "environment": None},
            "junk",
        ],
        "skipped": [{"stack": "s", "environment": "e", "code": "STATE_BUSY", "reason": "r"}, 3],
    }
    linked = agents.link_projects(listing, projects)
    assert [r["console_project"] for r in linked["deployments"]] == [
        "aaaaaaaaaaaa",
        "aaaaaaaaaaaa",  # the same directory through a symlink
        None,  # registered for another environment
        None,
        None,
    ]
    assert linked["skipped"] == [listing["skipped"][0]]


@pytest.mark.parametrize(
    "listing",
    [
        [],
        {"deployments": [], "skipped": []},  # no schema_version
        {"schema_version": 2, "deployments": [], "skipped": []},
        {"schema_version": 1, "deployments": {}, "skipped": []},
        {"schema_version": 1, "deployments": [], "skipped": "x"},
    ],
)
def test_an_unexpected_listing_is_an_error_never_an_empty_list(listing):
    with pytest.raises(agents.BadListing):
        agents.link_projects(listing, [])


# -- through the server ------------------------------------------------------------------


def test_agents_lists_every_stack_and_marks_registered_projects(center, project, cookie, tmp_path):
    listing = {
        "schema_version": 1,
        "deployments": [
            {
                "id": "a" * 32,
                "name": "support",
                "stack": "proj-0123abcd",
                "project": project["path"],
                "environment": project["environment"],
                "target": "local",
                "state": "ready",
            },
            {"id": "b" * 32, "name": "x", "stack": "y-0123abcd", "project": "/elsewhere"},
        ],
        "skipped": [],
    }
    (tmp_path / "list-all.json").write_text(json.dumps(listing))
    status, data, _ = api(center, "GET", "/api/agents", cookie=cookie)
    assert status == 200 and data["error"] is None
    rows = data["data"]["deployments"]
    assert [r["console_project"] for r in rows] == [project["id"], None]
    assert json.loads((tmp_path / "list-argv").read_text()) == ["list", "--all", "--json"]


@pytest.mark.parametrize("answer", ["not json", "[]", '{"deployments": []}'])
def test_agents_reports_a_failed_or_unexpected_listing(center, cookie, tmp_path, answer):
    (tmp_path / "list-all.json").write_text(answer)
    status, data, _ = api(center, "GET", "/api/agents", cookie=cookie)
    assert status == 200 and data["data"] is None and data["error"]["code"] == "BAD_OUTPUT"


def test_agents_needs_a_session(center):
    assert api(center, "GET", "/api/agents")[0] == 401


def test_releases_view_reads_the_stack_audit_chain(center, project, cookie, tmp_path, project_dir):
    (project_dir / "agent.yaml").write_text(AGENT_YAML)
    status, data, _ = api(center, "GET", f"/api/projects/{project['id']}/releases", cookie=cookie)
    assert status == 200 and data["error"] is None
    assert data["data"]["agent"] == "support"
    assert data["data"]["truncated"] is False
    assert [r["seq"] for r in data["data"]["releases"]] == [7, 4, 2]
    argv = json.loads((tmp_path / "audit-argv").read_text())
    assert argv == ["audit", "list", "--state-dir", "/tmp/fake-state", "--limit", "1000", "--json"]


def test_releases_view_says_when_the_audit_read_was_cut(center, project, cookie, tmp_path):
    rows = [_row(seq, "allow", action="deploy") for seq in range(1000, 0, -1)]
    (tmp_path / "audit.json").write_text(json.dumps(rows))
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/releases", cookie=cookie)
    assert data["data"]["releases"] == [] and data["data"]["truncated"] is True
    assert data["data"]["agent"] is None  # the fixture's agent.yaml is a stub


def test_releases_view_without_services(center, project, cookie, tmp_path):
    (tmp_path / "no-state").write_text("")
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/releases", cookie=cookie)
    assert data["data"] is None and data["error"]["code"] == "STACK_NOT_UP"


def test_releases_view_passes_on_a_failed_audit_read(center, project, cookie, tmp_path):
    (tmp_path / "audit.json").write_text("not json")
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/releases", cookie=cookie)
    assert data["data"] is None and data["error"]["code"] == "BAD_OUTPUT"


def test_identity_view_shows_what_stack_yaml_declares(center, project, cookie, project_dir):
    (project_dir / "stack.yaml").write_text(NORTHWIND_STACK.read_text())
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/identity", cookie=cookie)
    assert data["error"] is None
    assert data["data"] == {
        "stack": "northwind",
        "agents": {
            "northwind-support": {
                "sponsor": "support-lead@example.com",
                "review_by": "2027-06-30",
                "candidate": True,
            }
        },
    }


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("kind: stack\n", "STACK_INVALID"),
        ("[not, a, mapping]\n", "STACK_INVALID"),
        (b"\xff\xfe", "STACK_INVALID"),
        (None, "NO_STACK_FILE"),
    ],
)
def test_identity_view_reports_a_bad_stack_file(
    center, project, cookie, project_dir, content, code
):
    path = project_dir / "stack.yaml"
    if content is None:
        path.unlink()
    elif isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/identity", cookie=cookie)
    assert data["data"] is None and data["error"]["code"] == code


def test_identity_view_never_echoes_invalid_values(center, project, cookie, project_dir):
    text = NORTHWIND_STACK.read_text().replace(
        "sponsor: support-lead@example.com", "sponsor: private-value-not-an-email"
    )
    (project_dir / "stack.yaml").write_text(text)
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/identity", cookie=cookie)
    assert data["error"]["code"] == "STACK_INVALID"
    assert "private-value" not in data["error"]["message"]
    assert "sponsor" in data["error"]["message"]


def test_identity_view_refuses_a_symlinked_stack_file(
    center, project, cookie, project_dir, tmp_path
):
    real = tmp_path / "real-stack.yaml"
    real.write_text(NORTHWIND_STACK.read_text())
    (project_dir / "stack.yaml").unlink()
    (project_dir / "stack.yaml").symlink_to(real)
    _, data, _ = api(center, "GET", f"/api/projects/{project['id']}/identity", cookie=cookie)
    assert data["data"] is None and data["error"]
