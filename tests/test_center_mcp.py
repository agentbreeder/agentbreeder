# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""The MCP screen's files part (#831, M15 console half, part 1): `GET .../mcp` reads stack.yaml
and stack.lock in process and returns each server, its lock record and pins, and each agent's
grants. Relay state comes from the `services` view (`services status --json`), tested there."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_center import api, center, cookie, fake_cli, project, project_dir  # noqa: F401

from agentbreeder.center import reads
from agentbreeder.registry import RegistryError
from agentbreeder.services import mcp_stack
from agentbreeder.services.lock import StackLock

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_github_project" / "stack.yaml"
GRANTED = FIXTURE.read_text().replace(
    "      candidate: true            # release tests a candidate before production\n",
    "      candidate: true            # release tests a candidate before production\n"
    "      mcp: {github: [get_me, list_issues]}\n",
)
PIN = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
RECORD = {
    "ref": "mcp/github@1.0.0",
    "origin": "built-in",
    "upstream": "https://api.githubcopilot.com:443",
    "token_url": None,
}


def _lock(project_dir: Path, mcp: dict) -> None:
    StackLock(entries={}, imports={}, mcp=mcp).save(project_dir / "stack.lock")


def _view(center, project, cookie) -> dict:
    status, data, _ = api(center, "GET", f"/api/projects/{project['id']}/mcp", cookie=cookie)
    assert status == 200
    return data


def test_lists_servers_pins_and_grants(center, project, cookie, project_dir):
    (project_dir / "stack.yaml").write_text(GRANTED)
    _lock(project_dir, {"github": {**RECORD, "tools": {"list_issues": OTHER, "get_me": PIN}}})
    data = _view(center, project, cookie)
    assert data["error"] is None
    assert data["data"] == {
        "stack": "gh-reader",
        "servers": [
            {
                "name": "github",
                "ref": "mcp/github@1.0.0",
                "relay": "mcp-github",
                "locked": RECORD,
                "lock_current": True,
                "pins": {"get_me": PIN, "list_issues": OTHER},
            }
        ],
        "agents": [{"name": "gh-reader", "grants": {"github": ["get_me", "list_issues"]}}],
    }


def test_before_the_first_services_up_nothing_is_pinned(center, project, cookie, project_dir):
    (project_dir / "stack.yaml").write_text(FIXTURE.read_text())
    (project_dir / "stack.lock").unlink(missing_ok=True)
    data = _view(center, project, cookie)["data"]
    assert data["servers"][0]["locked"] is None
    assert data["servers"][0]["lock_current"] is False
    assert data["servers"][0]["pins"] == {}
    assert data["agents"] == [{"name": "gh-reader", "grants": {}}]


def test_pins_of_another_version_are_not_shown(center, project, cookie, project_dir):
    """A changed ref is pinned again at the next `services up`: the old pins don't apply."""
    (project_dir / "stack.yaml").write_text(GRANTED)
    old = {**RECORD, "ref": "mcp/github@0.9.0", "tools": {"get_me": PIN}}
    _lock(project_dir, {"github": old})
    server = _view(center, project, cookie)["data"]["servers"][0]
    assert server["locked"]["ref"] == "mcp/github@0.9.0"
    assert server["lock_current"] is False and server["pins"] == {}


def test_the_relay_is_named_as_the_cli_names_it(center, project, cookie, project_dir):
    text = GRANTED.replace(
        "  github: {ref: mcp/github@1.0.0}", "  git_hub: {ref: mcp/github@1.0.0}"
    )
    (project_dir / "stack.yaml").write_text(text.replace("mcp: {github:", "mcp: {git_hub:"))
    server = _view(center, project, cookie)["data"]["servers"][0]
    assert server["relay"] == mcp_stack.relay_name("git_hub") == "mcp-git-hub"


def test_a_stack_without_mcp_servers(center, project, cookie, project_dir):
    text = GRANTED.replace("mcp:\n  github: {ref: mcp/github@1.0.0}\n", "").replace(
        "      mcp: {github: [get_me, list_issues]}\n", ""
    )
    (project_dir / "stack.yaml").write_text(text)
    data = _view(center, project, cookie)["data"]
    assert data["servers"] == [] and data["agents"][0]["grants"] == {}


BAD_LOCKS = [
    "not json",
    json.dumps({"lock_version": 999, "entries": {}}),
    json.dumps({"lock_version": 1, "mcp": {}}),
    json.dumps({"lock_version": 1, "entries": [], "mcp": {}}),
    json.dumps({"lock_version": 1, "entries": {}, "imports": 3}),
    json.dumps({"lock_version": 1, "entries": {}, "imports": {"postgres": "X"}}),
    json.dumps({"lock_version": 1, "entries": {}, "imports": {"postgres": [3]}}),
    json.dumps({"lock_version": 1, "entries": {}, "mcp": []}),
    json.dumps({"lock_version": 1, "entries": {}, "mcp": {"github": {"tools": {"a": "md5"}}}}),
    json.dumps({"lock_version": 1, "entries": {}, "mcp": {"github": {"tools": []}}}),
]


@pytest.mark.parametrize("lock", BAD_LOCKS)
def test_a_bad_stack_lock_is_an_error(center, project, cookie, project_dir, lock):
    (project_dir / "stack.yaml").write_text(GRANTED)
    (project_dir / "stack.lock").write_text(lock)
    data = _view(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == "LOCK_INVALID"


def test_a_lock_record_with_a_non_text_identity_is_an_error(center, project, cookie, project_dir):
    """Stricter than `StackLock.load` (whose `lock_mcp` would repin): never shown as text."""
    (project_dir / "stack.yaml").write_text(GRANTED)
    lock = {"lock_version": 1, "entries": {}, "mcp": {"github": {"ref": 3, "tools": {}}}}
    (project_dir / "stack.lock").write_text(json.dumps(lock))
    data = _view(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == "LOCK_INVALID"


def test_a_symlinked_stack_lock_is_refused(center, project, cookie, project_dir, tmp_path):
    (project_dir / "stack.yaml").write_text(GRANTED)
    real = tmp_path / "real.lock"
    StackLock(entries={}, imports={}, mcp={"github": {**RECORD, "tools": {}}}).save(real)
    (project_dir / "stack.lock").unlink(missing_ok=True)
    (project_dir / "stack.lock").symlink_to(real)
    data = _view(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == "ENTRY_UNSAFE"


@pytest.mark.parametrize(
    ("content", "code"), [("kind: stack\n", "STACK_INVALID"), (None, "NO_STACK_FILE")]
)
def test_a_bad_stack_file_is_an_error(center, project, cookie, project_dir, content, code):
    path = project_dir / "stack.yaml"
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    data = _view(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == code


def test_the_answer_follows_edits_of_either_file(center, project, cookie, project_dir):
    (project_dir / "stack.yaml").write_text(GRANTED)
    _lock(project_dir, {"github": {**RECORD, "tools": {"get_me": PIN}}})
    assert _view(center, project, cookie)["data"]["servers"][0]["pins"] == {"get_me": PIN}
    _lock(project_dir, {"github": {**RECORD, "tools": {"get_me": OTHER}}})
    assert _view(center, project, cookie)["data"]["servers"][0]["pins"] == {"get_me": OTHER}
    (project_dir / "stack.yaml").write_text(GRANTED.replace("[get_me, list_issues]", "[get_me]"))
    agents = _view(center, project, cookie)["data"]["agents"]
    assert agents[0]["grants"] == {"github": ["get_me"]}


@pytest.mark.parametrize("bad", BAD_LOCKS)
def test_the_lock_checks_refuse_what_the_cli_refuses(tmp_path, bad):
    path = tmp_path / "stack.lock"
    path.write_text(bad)
    with pytest.raises(RegistryError) as cli:
        StackLock.load(path)
    with pytest.raises(RegistryError) as ours:
        reads._stack_lock_mcp(path.read_bytes(), "stack.lock")
    assert cli.value.code == ours.value.code == "LOCK_INVALID"


def test_the_lock_checks_match_the_cli(tmp_path):
    """`_stack_lock_mcp` accepts what `StackLock.save` writes, with the CLI's records."""
    path = tmp_path / "stack.lock"
    mcp = {"github": {**RECORD, "tools": {"get_me": PIN}}}
    StackLock(entries={}, imports={"postgres": ["X"]}, mcp=mcp).save(path)
    assert reads._stack_lock_mcp(path.read_bytes(), "stack.lock") == StackLock.load(path).mcp
    fields = SimpleNamespace(ref="r", origin="o", upstream_origin="u", token_url_host=None)
    assert reads.MCP_IDENTITY == tuple(mcp_stack.McpEntry.identity(fields))
