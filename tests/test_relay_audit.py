"""MCP relay tool-call records imported into the hash-chained audit log (#1068)."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from agentbreeder import cli, relay_audit
from agentbreeder.store import Store

DEPLOYMENT = "d" * 32


def record(n: int = 1, **change) -> dict:
    return {
        "v": 1,
        "id": f"{n:032x}",
        "ts": 1_800_000_000.5,
        "relay": "1.0.2",
        "server": "github",
        "environment": "dev",
        "deployment": DEPLOYMENT,
        "agent": "gh-reader",
        "tool": "list_issues",
        "tool_sha256": None,
        "decision": "allow",
        "ms": 812,
        **change,
    }


@pytest.fixture
def stack(tmp_path):
    """A stack's layout: envs/dev/state (deploy state) and relay-audit/dev/ (the relay's dir)."""
    root = tmp_path / "northwind-0123abcd"
    state = root / "envs" / "dev" / "state"
    Store(state).close()
    audit = root / "relay-audit" / "dev"
    audit.mkdir(parents=True, mode=0o700)
    return state, audit


def write(audit, *records, server="github", raw: bytes = b""):
    with open(audit / f"{server}.jsonl", "ab") as handle:
        for item in records:
            handle.write(json.dumps(item).encode() + b"\n")
        handle.write(raw)


def rows(state, *extra) -> list[dict]:
    assert cli.main(["audit", "list", "--state-dir", str(state), "--json", *extra]) == 0
    captured = _capsys.readouterr()
    ERRORS.append(captured.err)
    return [r for r in json.loads(captured.out) if r["action"].startswith("mcp_")]


_capsys = None
ERRORS: list[str] = []


def _out() -> str:
    return _capsys.readouterr().out


@pytest.fixture(autouse=True)
def _capture(capsys):
    global _capsys
    _capsys = capsys


def test_relay_records_become_chained_audit_rows(stack, capsys):
    state, audit = stack
    write(
        audit,
        record(1),
        record(2, tool=None, tool_sha256="ab" * 8, decision="TOOL_NOT_GRANTED", ms=9),
    )
    refused, allowed = rows(state)
    assert allowed["action"] == "mcp_tool_call" and allowed["decision"] == "allow"
    assert allowed["deployment_id"] == DEPLOYMENT and allowed["actor"] == "gh-reader"
    assert allowed["actor_verified"] is False and allowed["reasons"] == []
    assert allowed["attempt_id"] == "mcp-" + "0" * 31 + "1"
    assert allowed["context"] == {
        "correlation_id": "0" * 31 + "1",
        "server": "github",
        "tool": "list_issues",
        "tool_sha256": None,
        "environment": "dev",
        "relay": "service/mcp-relay@1.0.2",
        "relay_at": "2027-01-15T08:00:00.500000+00:00",
        "ms": 812,
    }
    assert refused["decision"] == "deny"
    assert refused["reasons"][0]["code"] == "TOOL_NOT_GRANTED"
    assert refused["context"]["tool"] is None and refused["context"]["tool_sha256"] == "ab" * 8
    assert cli.main(["audit", "verify", "--state-dir", str(state), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["rows"] == 2 and report["incomplete_attempts"] == []


def test_import_is_exactly_once_and_follows_appends(stack):
    state, audit = stack
    write(audit, record(1))
    assert len(rows(state)) == 1
    assert len(rows(state)) == 1  # read again: nothing new
    write(audit, record(2), record(3))
    assert [r["context"]["correlation_id"][-1] for r in rows(state)] == ["3", "2", "1"]
    cursor = json.loads((state / relay_audit.CURSOR_FILE).read_text())
    assert cursor["github"]["offset"] == (audit / "github.jsonl").stat().st_size
    assert oct((state / relay_audit.CURSOR_FILE).stat().st_mode & 0o777) == "0o600"


def test_a_lost_cursor_or_replaced_file_never_duplicates_rows(stack):
    state, audit = stack
    write(audit, record(1), record(2))
    rows(state)
    (state / relay_audit.CURSOR_FILE).unlink()
    assert len(rows(state)) == 2
    (state / relay_audit.CURSOR_FILE).write_text("not json")
    assert len(rows(state)) == 2
    (audit / "github.jsonl").unlink()  # the file is recreated: the cursor's inode is stale
    write(audit, record(2), record(3))
    assert len(rows(state)) == 3


def test_a_partial_last_line_waits_for_its_newline(stack):
    state, audit = stack
    line = json.dumps(record(1)).encode()
    write(audit, raw=line[:40])
    assert rows(state) == []
    write(audit, raw=line[40:] + b"\n")
    assert len(rows(state)) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {**record(), "extra": 1},
        {k: v for k, v in record().items() if k != "ms"},
        record(id="Z" * 32),
        record(v=True),
        record(ts=float("inf")),
        record(server="gitlab"),  # not this file's server
        record(environment="prod"),  # not this state's environment
        record(deployment="short"),
        record(agent="Bad Name"),
        record(tool="../etc"),
        record(tool=None, tool_sha256=None, decision="TOOL_NOT_GRANTED"),
        record(tool=None, tool_sha256="ab" * 8),  # an ungranted tool is never allowed
        record(tool="ungranted", tool_sha256="ab" * 8, decision="TOOL_NOT_GRANTED"),
        record(tool_sha256="ab" * 8),
        record(decision="lowercase"),
        record(ms=-1),
        record(ms=True),
        record(relay="v1"),
        record(arguments={"q": "x"}),
        [1, 2],
    ],
)
def test_lines_not_in_the_relay_shape_are_refused_and_counted(stack, capsys, bad):
    state, audit = stack
    write(audit, bad, record(7))
    imported = rows(state)
    assert "were not relay records" in ERRORS[-1]
    invalid = [r for r in imported if r["action"] == "mcp_relay_record"]
    assert len(invalid) == 1 and invalid[0]["decision"] == "rejected"
    assert invalid[0]["reasons"][0]["code"] == "RELAY_RECORD_INVALID"
    assert [r["context"]["correlation_id"][-1] for r in imported if r not in invalid] == ["7"]
    assert len(rows(state)) == 2  # the rejection is recorded once


def test_garbage_and_oversized_lines_are_refused(stack):
    state, audit = stack
    write(audit, raw=b"\xff\xfe not json\n" + b"x" * 5000 + b"\n")
    write(audit, record(1))
    imported = rows(state)
    assert [r["action"] for r in imported] == ["mcp_relay_record", "mcp_tool_call"]
    assert "2 line(s)" in imported[0]["reasons"][0]["message"]


def test_one_line_longer_than_a_read_is_refused_not_stuck(stack, monkeypatch):
    state, audit = stack
    monkeypatch.setattr(relay_audit, "READ_MAX", 64)
    write(audit, raw=b"y" * 100)
    imported = rows(state)
    assert [r["action"] for r in imported] == ["mcp_relay_record"]


def test_only_owned_regular_files_named_for_a_server_are_read(stack, tmp_path):
    state, audit = stack
    elsewhere = tmp_path / "elsewhere.jsonl"
    elsewhere.write_text(json.dumps(record(1)) + "\n")
    (audit / "github.jsonl").symlink_to(elsewhere)
    (audit / "Bad-Name.jsonl").write_text(json.dumps(record(2)) + "\n")
    (audit / "notes.txt").write_text("x")
    (audit / "dir.jsonl").mkdir()
    assert rows(state) == []


def test_other_servers_have_their_own_files_and_cursors(stack):
    state, audit = stack
    write(audit, record(1))
    write(audit, record(2, server="linear"), server="linear")
    assert {r["context"]["server"] for r in rows(state)} == {"github", "linear"}
    assert set(json.loads((state / relay_audit.CURSOR_FILE).read_text())) == {"github", "linear"}


def test_state_outside_a_stack_imports_nothing(tmp_path):
    state = tmp_path / "plain-state"
    Store(state).close()
    assert relay_audit.location(state) is None
    assert cli.main(["audit", "list", "--state-dir", str(state), "--json"]) == 0
    assert json.loads(_out()) == []


def test_no_relay_directory_imports_nothing(stack):
    state, audit = stack
    os.rmdir(audit)
    assert rows(state) == []


def test_a_failed_import_warns_but_the_log_is_still_read(stack, capsys, monkeypatch):
    state, audit = stack
    write(audit, record(1))

    def busy(store):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(relay_audit, "import_records", busy)
    assert cli.main(["audit", "list", "--state-dir", str(state), "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    assert "could not be imported now" in captured.err


def test_a_cursor_that_cannot_be_written_is_harmless(stack, monkeypatch):
    state, audit = stack
    write(audit, record(1))

    def fail(*_):
        raise OSError("read-only")

    monkeypatch.setattr(relay_audit, "_write_cursor", fail)
    assert len(rows(state)) == 1
    assert len(rows(state)) == 1


def test_text_output_names_the_server_and_tool(stack, capsys):
    state, audit = stack
    write(
        audit,
        record(1),
        record(2, tool=None, tool_sha256="ab" * 8, decision="TOOL_NOT_GRANTED"),
    )
    assert cli.main(["audit", "list", "--state-dir", str(state)]) == 0
    out = capsys.readouterr().out
    assert "mcp_tool_call allow  actor=gh-reader (unverified) mcp=github/list_issues" in out
    assert "TOOL_NOT_GRANTED" in out and "mcp=github/ungranted:" + "ab" * 8 in out


def test_an_unknown_refusal_code_gets_a_generic_message():
    event = relay_audit.event(record(decision="SOMETHING_NEW"))
    assert event.reasons == [{"code": "SOMETHING_NEW", "message": "Refused by the relay"}]


def test_a_granted_tool_named_ungranted_is_imported_by_name(stack):
    state, audit = stack
    write(audit, record(1, tool="ungranted"))
    (row,) = rows(state)
    assert row["action"] == "mcp_tool_call" and row["context"]["tool"] == "ungranted"


def test_the_same_bad_lines_read_again_are_recorded_once(stack):
    state, audit = stack
    write(audit, raw=b"not json\n")
    write(audit, record(1))
    assert len(rows(state)) == 2
    (state / relay_audit.CURSOR_FILE).unlink()  # a lost cursor: everything is read again
    assert [r["action"] for r in rows(state)] == ["mcp_relay_record", "mcp_tool_call"]


def test_a_fifo_swapped_in_after_the_listing_does_not_hang(stack, monkeypatch):
    state, audit = stack
    os.mkfifo(audit / "github.jsonl")
    monkeypatch.setattr(
        relay_audit, "_files", lambda directory: [("github", audit / "github.jsonl")]
    )
    assert rows(state) == []
