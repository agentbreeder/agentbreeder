"""`audit list`: keyset paging with `--before SEQ` and each row's `agent` (#824)."""

from __future__ import annotations

import json

import pytest

from agentbreeder import cli
from agentbreeder.audit import AuditEvent
from agentbreeder.store import Store

LIVE = "a" * 32
GONE = "b" * 32


def _event(attempt: str, action: str = "deploy", decision: str = "allow") -> AuditEvent:
    return AuditEvent(attempt, action, decision, "os:me", False, "enforce")


@pytest.fixture
def state(tmp_path):
    """Seven rows: 1 has no deployment, 2–4 are `support`, 5–7 a removed `support-candidate`."""
    directory = tmp_path / "state"
    store = Store(directory)
    store.audit(_event("x", "release", "started"))
    store.create(LIVE, {"name": "support", "image": "i"}, "d", "ctx", audit=_event("p"))
    store.audit(AuditEvent("p", "transition", "ready", "os:me", False, "enforce", LIVE))
    store.audit(AuditEvent("p", "transition", "ok", "os:me", False, "enforce", LIVE))
    store.create(GONE, {"name": "support-candidate", "image": "j"}, "d", "ctx", audit=_event("c"))
    store.audit(AuditEvent("c", "transition", "ready", "os:me", False, "enforce", GONE))
    store.transition(
        GONE,
        "removed",
        audit=AuditEvent("c", "transition", "removed", "os:me", False, "enforce", GONE),
    )
    store.close()
    return ["--state-dir", str(directory)]


def _list(capsys, state, *extra) -> list[dict]:
    assert cli.main(["audit", "list", *state, "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def test_before_pages_newest_first_without_gaps(state, capsys):
    first = _list(capsys, state, "--limit", "3")
    assert [row["seq"] for row in first] == [7, 6, 5]
    second = _list(capsys, state, "--limit", "3", "--before", str(first[-1]["seq"]))
    assert [row["seq"] for row in second] == [4, 3, 2]
    last = _list(capsys, state, "--limit", "3", "--before", "2")
    assert [row["seq"] for row in last] == [1]
    assert _list(capsys, state, "--before", "1") == []


def test_agent_names_every_deployment_removed_included(state, capsys):
    rows = {row["seq"]: row for row in _list(capsys, state)}
    assert rows[1]["agent"] is None and rows[1]["deployment_id"] is None
    assert {rows[seq]["agent"] for seq in (2, 3, 4)} == {"support"}
    assert {rows[seq]["agent"] for seq in (5, 6, 7)} == {"support-candidate"}


def test_json_keeps_every_column(state, capsys):
    row = _list(capsys, state, "--limit", "1")[0]
    assert {
        "seq",
        "at",
        "attempt_id",
        "deployment_id",
        "enforcement_mode",
        "reasons",
        "hash",
    } <= set(row)


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc", "", " 3", "9" * 19])
def test_before_must_be_a_positive_seq(state, capsys, value):
    assert cli.main(["audit", "list", *state, "--json", "--before", value]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[BEFORE_INVALID]") and captured.out == ""


def test_before_refuses_attempt(state, capsys):
    assert cli.main(["audit", "list", *state, "--attempt", "p", "--before", "3"]) == 2
    assert "[BEFORE_INVALID]" in capsys.readouterr().err


def test_text_output_honours_before(state, capsys):
    assert cli.main(["audit", "list", *state, "--before", "3"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [int(line.split()[0]) for line in lines] == [2, 1]
