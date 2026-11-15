# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Repin from the console (#964, console half): `POST .../mcp/pins` runs `services pins --json`
(uncached, tracked, stdout capped), the server checks the answer against its own hashed review
and renders the text; a `services-up` start with `pins_digest` pops that review atomically and
adds `--repin --expect-pins` to the argv (spec 2026-11-14-mcp-repin-expect-pins.md §7)."""

from __future__ import annotations

import copy
import json
import shutil
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_center import (  # noqa: F401 (fixtures)
    DIGEST,
    api,
    center,
    cookie,
    fake_cli,
    project,
    start,
    wait,
)

from agentbreeder.center import jobs, pinsreview
from agentbreeder.center.files import BadRequest
from agentbreeder.center.jobs import CenterState, Runner
from agentbreeder.services import mcp_stack, plan

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_github_project"


@pytest.fixture
def project_dir(tmp_path) -> Path:
    """The M15 fixture: one `mcp:` server (github)."""
    target = tmp_path / "gh-reader"
    shutil.copytree(FIXTURE, target)
    return target


IDENTITY = {"ref": "mcp/github@1.0.0", "origin": "built-in", "upstream": "u", "token_url": None}
OLD = {"name": "get_me", "description": "Who am I", "inputSchema": {"type": "object"}}
NEW = {**OLD, "description": "Who am I, now with more text " + "é" * 120, "x": 1.0}
ADDED = {"name": "list_issues", "description": "List", "inputSchema": {"type": "object"}}
GONE = {"name": "old_tool", "description": "gone"}


def answer(previous: bool = True, origin: str = "built-in") -> dict:
    """A `services pins --json` answer built by the CLI's own `pins_review` (stub entries)."""
    entry = SimpleNamespace(identity=lambda: {**IDENTITY, "origin": origin}, origin=origin)
    lock = SimpleNamespace(
        mcp={
            "github": {
                "tools": {
                    "get_me": mcp_stack.tool_hash(OLD),
                    "old_tool": mcp_stack.tool_hash(GONE),
                }
            }
        }
    )
    probe = {
        "tools": {
            tool["name"]: {"sha256": mcp_stack.tool_hash(tool), "definition": tool}
            for tool in (NEW, ADDED)
        },
        "missing": ["old_tool", "never_served"],
    }
    old_pins = {"get_me": {"sha256": mcp_stack.tool_hash(OLD), "definition": OLD}}
    review = mcp_stack.pins_review(
        "stack-1",
        {"github": entry},
        lock,
        {"github": probe},
        {"github": old_pins if previous else {}},
    )
    result = {
        "schema_version": 1,
        "stack": "stack-1",
        "digest": mcp_stack.pins_review_digest(review.document),
        "review": review.document,
        "servers": review.details,
    }
    return json.loads(json.dumps(result))  # as printed and parsed


# -- the copies match the CLI ---------------------------------------------------------------------

SAMPLES = [
    OLD,
    NEW,
    {"a": "x" * 450, "b": ["\u202e", "\\u1234" * 40, 2**60, 1.5, None, True]},
    {"description": "\u21a9 " + 'é"\\' * 90, "nested": {"z": [{"k": "v" * 101}]}},
    {},
]


@pytest.mark.parametrize("value", SAMPLES)
def test_copies_equal_the_clis_functions(value):
    assert pinsreview.tool_hash(value) == mcp_stack.tool_hash(value)
    assert pinsreview.canonical(value) == mcp_stack.canonical(value)
    assert pinsreview.review_digest(value) == mcp_stack.pins_review_digest(value)
    assert pinsreview.review_lines(value) == plan.review_lines(value)
    for other in SAMPLES:
        assert pinsreview.review_diff(other, value) == plan.review_diff(other, value)
    assert pinsreview.review_diff(None, value) == plan.review_diff(None, value)


def test_constants_equal_the_clis():
    assert (pinsreview.WRAP, pinsreview.WRAP_MARK, pinsreview.DIFF_LINES) == (
        plan.WRAP,
        plan.WRAP_MARK,
        plan.DIFF_LINES,
    )
    assert pinsreview.REVIEW_VERSION == mcp_stack.REVIEW_VERSION
    big = {"lines": list(range(pinsreview.DIFF_LINES + 1))}
    assert pinsreview.review_diff({}, big) == plan.review_diff({}, big)
    assert pinsreview.review_diff({}, big)[0] == "(diff too large; the new definition in full:)"


def test_strict_loads_refuses_duplicates_and_constants():
    assert pinsreview.loads('{"a": [1, {"b": 2}]}') == {"a": [1, {"b": 2}]}
    for text in ('{"a": 1, "a": 2}', '{"a": {"b": 1, "b": 1}}', '{"a": NaN}', "[Infinity]"):
        with pytest.raises(ValueError):
            pinsreview.loads(text)


# -- verify ---------------------------------------------------------------------------------------


def test_verify_renders_what_the_cli_would_show():
    data = answer()
    summary, problem = pinsreview.verify(data)
    assert problem is None
    assert summary["digest"] == data["digest"] and summary["stack"] == "stack-1"
    assert summary["changes"] is True
    [server] = summary["servers"]
    assert (server["name"], server["origin"], server["built_in"], server["state"]) == (
        "github",
        "built-in",
        True,
        "drift",
    )
    changed, new = server["tools"][1], server["tools"][0]
    assert new["change"] == "new" and new["lines"] == plan.review_lines(ADDED)
    assert changed["change"] == "changed"
    assert changed["lines"] == plan.review_diff(OLD, NEW)
    assert changed["pinned"] == mcp_stack.tool_hash(OLD)[7:23]
    assert any("1.0" in line for line in changed["lines"])  # never re-serialized as 1
    assert all(line.replace("\u21a9", "").isascii() for line in changed["lines"])  # é escaped
    assert server["drop"] == ["old_tool"] and server["missing"] == ["never_served"]
    assert server["same"] == []
    assert set(summary) == {"stack", "digest", "changes", "servers"}


def test_verify_without_a_previous_definition_shows_the_new_one_in_full():
    summary, _ = pinsreview.verify(answer(previous=False))
    lines = summary["servers"][0]["tools"][1]["lines"]
    assert lines[0] == "(previous definition unavailable; the new one in full:)"


def test_verify_a_server_with_nothing_to_change():
    data = answer()
    data["review"]["servers"]["github"].update(accept={}, drop=[])
    data["servers"]["github"].update(new={}, changed={}, drop=[], missing=[])
    data["digest"] = mcp_stack.pins_review_digest(data["review"])
    summary, problem = pinsreview.verify(data)
    assert problem is None and summary["changes"] is False
    assert summary["servers"][0]["state"] == "ok"
    assert summary["servers"][0]["same"] == ["get_me", "old_tool"]
    empty = {"schema_version": 1, "stack": "s", "review": {"v": 1, "stack": "s", "servers": {}}}
    empty |= {"servers": {}, "digest": mcp_stack.pins_review_digest(empty["review"])}
    assert pinsreview.verify(empty)[0]["servers"] == []


def _redigest(data):
    data["digest"] = mcp_stack.pins_review_digest(data["review"])


TAMPER = {
    "definition": lambda d: d["servers"]["github"]["changed"]["get_me"]["definition"].update(
        description="other"
    ),
    "new definition": lambda d: d["servers"]["github"]["new"]["list_issues"]["definition"].update(
        x=1
    ),
    "accept, redigested": lambda d: (
        d["review"]["servers"]["github"]["accept"].update(get_me="sha256:" + "0" * 64),
        _redigest(d),
    ),
    "digest": lambda d: d.update(digest="0" * 64),
    "digest case": lambda d: d.update(digest=d["digest"].upper()),
    "extra server shown": lambda d: d["servers"].update(other=d["servers"]["github"]),
    "drop shown": lambda d: d["servers"]["github"].update(drop=[]),
    "drop bound": lambda d: (d["review"]["servers"]["github"].update(drop=[]), _redigest(d)),
    "origin": lambda d: d["servers"]["github"].update(origin="project"),
    "previous": lambda d: d["servers"]["github"]["changed"]["get_me"].update(previous={"a": 1}),
    "changed pinned": lambda d: d["servers"]["github"]["changed"]["get_me"].update(
        pinned="sha256:" + "1" * 64
    ),
    "new shown as changed": lambda d: d["servers"]["github"]["changed"].update(
        list_issues=d["servers"]["github"]["new"].pop("list_issues")
    ),
    "schema true": lambda d: d.update(schema_version=True),
    "schema 2": lambda d: d.update(schema_version=2),
    "v true": lambda d: (d["review"].update(v=True), _redigest(d)),
    "review stack": lambda d: (d["review"].update(stack="other"), _redigest(d)),
    "review extra key": lambda d: (d["review"].update(extra=1), _redigest(d)),
    "server name": lambda d: (
        d["review"]["servers"].update({"Bad": d["review"]["servers"].pop("github")}),
        d["servers"].update({"Bad": d["servers"].pop("github")}),
        _redigest(d),
    ),
    "tool name": lambda d: d["servers"]["github"].update(missing=["../x"]),
    "lone surrogate": lambda d: d["servers"]["github"]["new"]["list_issues"]["definition"].update(
        description="\ud800"
    ),
    "not a dict": lambda d: d.update(servers=[]),
    "changed to its own pin": lambda d: (
        d["servers"]["github"]["changed"]["get_me"].update(
            sha256=d["review"]["servers"]["github"]["pinned"]["get_me"], definition=OLD
        ),
        d["review"]["servers"]["github"]["accept"].update(
            get_me=d["review"]["servers"]["github"]["pinned"]["get_me"]
        ),
        _redigest(d),
    ),
}


@pytest.mark.parametrize("name", sorted(TAMPER))
def test_verify_refuses_an_inconsistent_answer(name):
    data = copy.deepcopy(answer())
    TAMPER[name](data)
    assert pinsreview.verify(data) == (None, pinsreview.INVALID)


@pytest.mark.parametrize("data", [None, [], "x", {"schema_version": 1}, {"schema_version": "1"}])
def test_verify_refuses_other_shapes(data):
    assert pinsreview.verify(data) == (None, pinsreview.INVALID)


def test_verify_survives_deep_nesting():
    data = answer()
    deep: list = []
    for _ in range(5000):
        deep = [deep]
    data["servers"]["github"]["new"]["list_issues"]["definition"]["deep"] = deep
    assert pinsreview.verify(data) == (None, pinsreview.INVALID)


# -- the capped read ------------------------------------------------------------------------------


@pytest.fixture
def runner(tmp_path):
    directory = tmp_path / "c"
    directory.mkdir(mode=0o700)
    return Runner(CenterState(directory), directory, prefix=[sys.executable, "-c"])


def test_a_capped_read_answers_small_json(runner):
    script = "import json; print(json.dumps({'a': 1}))"
    result = runner.read([script], cache=False, track=True, max_bytes=1024)
    assert result == {"data": {"a": 1}, "error": None, "stale": False}
    assert runner.children == [] or all(child.poll() is not None for child in runner.children)


def test_a_capped_read_stops_past_the_cap(runner):
    script = "import sys, time\nwhile True:\n    sys.stdout.write('x' * 65536); sys.stdout.flush()"
    result = runner.read([script], cache=False, track=True, max_bytes=200_000, timeout=30)
    assert result["data"] is None and result["error"]["code"] == "OUTPUT_TOO_LARGE"


def test_a_capped_read_times_out_and_stops_the_group(runner, tmp_path):
    marker = tmp_path / "grandchild"
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)"
    )
    result = runner.read([script], cache=False, track=True, max_bytes=1024, timeout=2)
    assert result["error"]["code"] == "TIMEOUT"
    pid = int(marker.read_text())
    import os
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("the grandchild survived the timeout")


def test_a_capped_read_keeps_the_cli_error_and_refuses_bad_bytes(runner):
    script = "import sys; sys.stderr.write('[SERVICES_BUSY] busy\\n'); sys.exit(2)"
    result = runner.read([script], cache=False, track=True, max_bytes=1024)
    assert result["error"]["code"] == "SERVICES_BUSY"
    script = 'import sys; sys.stdout.buffer.write(b\'{"a": "\\xff"}\')'
    result = runner.read([script], cache=False, track=True, max_bytes=1024, loads=pinsreview.loads)
    assert result["data"] == {"a": "\udcff"}  # a lone surrogate: no UTF-8 encode accepts it
    with pytest.raises(UnicodeEncodeError):
        pinsreview.tool_hash(result["data"])
    script = 'print(\'{"a": 1, "a": 2}\')'
    result = runner.read([script], cache=False, track=True, max_bytes=1024, loads=pinsreview.loads)
    assert result["error"]["code"] == "BAD_OUTPUT"


def test_a_capped_read_is_bounded_when_a_process_left_the_group(runner):
    """A grandchild in its own session keeps the pipes open: the read still ends near its
    timeout (that pipe is left to its daemon thread, never closed under it)."""
    import time

    script = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],"
        " start_new_session=True)\n"
        "time.sleep(60)"
    )
    began = time.monotonic()
    result = runner.read([script], cache=False, track=True, max_bytes=1024, timeout=2)
    assert result["error"]["code"] == "TIMEOUT"
    assert time.monotonic() - began < 8


def test_a_capped_read_drains_a_stderr_flood_and_takes_exactly_its_cap(runner):
    script = (
        "import json, sys\n"
        "sys.stderr.write('e' * 1_000_000); sys.stderr.flush()\n"
        "sys.stdout.write(json.dumps('x' * 1014))"  # 1016 bytes with the quotes
    )
    result = runner.read([script], cache=False, track=True, max_bytes=1016, timeout=30)
    assert result["error"] is None and len(result["data"]) == 1014
    result = runner.read([script], cache=False, track=True, max_bytes=1015, timeout=30)
    assert result["error"]["code"] == "OUTPUT_TOO_LARGE"


def test_a_capped_read_needs_track(runner):
    with pytest.raises(ValueError):
        runner.read(["print(1)"], max_bytes=10)


def test_a_capped_read_sends_its_input(runner):
    script = "import json, sys; print(json.dumps(sys.stdin.read()))"
    result = runner.read([script], cache=False, track=True, max_bytes=1024, input="héllo")
    assert result["data"] == "héllo"


# -- argv -----------------------------------------------------------------------------------------


def test_argv_appends_repin_with_the_digest_only():
    project = {"path": "/p", "environment": "dev"}
    base = jobs.argv_for("services-up", project, {"digest": "a" * 64})
    assert "--repin" not in base
    argv = jobs.argv_for("services-up", project, {"digest": "a" * 64, "pins_digest": "b" * 64})
    grant = argv.index("--expect-grant")
    assert argv[grant : grant + 5] == [
        "--expect-grant",
        "a" * 64,
        "--repin",
        "--expect-pins",
        "b" * 64,
    ]
    for bad in ("B" * 64, "b" * 63, "", 1, "--repin"):
        with pytest.raises(BadRequest):
            jobs.argv_for("services-up", project, {"digest": "a" * 64, "pins_digest": bad})


# -- the server: read, keep, consume --------------------------------------------------------------


@pytest.fixture
def pins_cli(center, monkeypatch):
    """`services pins` answered by a script; every other read runs as usual."""
    seen: list[dict] = []
    script = {"result": {"data": answer(), "error": None, "stale": False}}
    real = center.runner.read

    def read(argv, *args, **kwargs):
        if list(argv[:2]) == ["services", "pins"]:
            seen.append({"argv": list(argv), **kwargs})
            return script["result"]
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(center.runner, "read", read)
    return SimpleNamespace(seen=seen, script=script)


def review(center, project, cookie, body=None):
    return api(center, "POST", f"/api/projects/{project['id']}/mcp/pins", body or {}, cookie)


def test_the_read_is_uncached_tracked_capped_and_strict(center, project, cookie, pins_cli):
    status, data, _ = review(center, project, cookie)
    assert status == 200 and data["error"] is None
    assert data["data"]["digest"] == answer()["digest"]
    [call] = pins_cli.seen
    assert call["argv"] == ["services", "pins", project["path"], "--json"]
    assert call["cache"] is False and call["track"] is True
    assert call["max_bytes"] == 2 * 1024 * 1024 and call["loads"] is pinsreview.loads
    assert call["timeout"] == 240  # one `mcp:` server: 60 + 180 x 1
    assert center.pins_reviews[project["id"]] == answer()["digest"]
    assert review(center, project, cookie, {"x": 1})[0] == 400


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ({"data": None, "error": {"code": "SERVICES_BUSY", "message": "m"}}, "SERVICES_BUSY"),
        (
            {"data": None, "error": {"code": "OUTPUT_TOO_LARGE", "message": "m"}},
            "PINS_REVIEW_TOO_LARGE",
        ),
        (
            {"data": None, "error": {"code": "MCP_PINS_NEED_UP", "message": "m"}},
            "MCP_PINS_NEED_UP",
        ),
        ({"data": {"schema_version": 1}, "error": None}, "PINS_REVIEW_INVALID"),
        (
            {"data": answer(), "error": {"code": "TIMEOUT", "message": "m"}, "stale": True},
            "TIMEOUT",
        ),
    ],
)
def test_a_failed_read_keeps_no_review(center, project, cookie, pins_cli, result, code):
    review(center, project, cookie)
    assert project["id"] in center.pins_reviews
    pins_cli.script["result"] = {"stale": False, **result}
    status, data, _ = review(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == code
    assert project["id"] not in center.pins_reviews
    if code == "PINS_REVIEW_TOO_LARGE":
        assert f"agentbreeder services pins {project['path']}" in data["error"]["message"]


def test_invalid_utf8_through_the_real_runner_is_an_invalid_review(center, project, cookie):
    """The real capped read (no stub): a byte that isn't UTF-8 inside a definition fails the
    check (`PINS_REVIEW_INVALID`), never a 500, and keeps no review."""
    text = json.dumps(answer()).encode().replace(b"Who am I", b"Who am \xff", 1)
    script = f"import sys; sys.stdout.buffer.write({text!r})"
    center.runner.prefix = [sys.executable, "-c", script]
    status, data, _ = review(center, project, cookie)
    assert status == 200 and data["error"]["code"] == "PINS_REVIEW_INVALID"
    assert project["id"] not in center.pins_reviews


def _repin(center, project, cookie, pins):
    return start(center, project, cookie, "services-up", {"digest": DIGEST, "pins_digest": pins})


def test_a_start_uses_the_review_once(center, project, cookie, pins_cli):
    digest = answer()["digest"]
    status, data, _ = _repin(center, project, cookie, digest)
    assert status == 400 and data["error"]["code"] == "PINS_REVIEW_STALE"  # never reviewed
    review(center, project, cookie)
    status, data, _ = _repin(center, project, cookie, "0" * 64)
    assert data["error"]["code"] == "PINS_REVIEW_STALE"  # a digest the server never showed
    assert project["id"] not in center.pins_reviews  # ...consumes it
    review(center, project, cookie)
    status, data, _ = _repin(center, project, cookie, digest)
    assert status == 200, data
    job = wait(center, data["data"]["job"], cookie)
    texts = [line["text"] for line in job["lines"]]
    argv = json.loads(next(text for text in texts if text.startswith("argv "))[5:])
    assert argv[argv.index("--expect-grant") :][:5] == [
        "--expect-grant",
        DIGEST,
        "--repin",
        "--expect-pins",
        digest,
    ]
    status, data, _ = _repin(center, project, cookie, digest)
    assert data["error"]["code"] == "PINS_REVIEW_STALE"  # consumed by the launched job
    assert _repin(center, project, cookie, "x")[1]["error"]["code"] == "PINS_EXPECT_INVALID"


def test_a_start_without_pins_digest_never_repins(center, project, cookie, pins_cli):
    review(center, project, cookie)
    status, data, _ = start(center, project, cookie, "services-up", {"digest": DIGEST})
    job = wait(center, data["data"]["job"], cookie)
    texts = [line["text"] for line in job["lines"]]
    assert "--repin" not in next(text for text in texts if text.startswith("argv "))
    assert project["id"] in center.pins_reviews  # untouched


def test_a_pre_launch_refusal_restores_the_review(center, project, cookie, pins_cli):
    digest = answer()["digest"]
    review(center, project, cookie)
    status, data, _ = start(
        center, project, cookie, "services-up", {"digest": "b" * 64, "pins_digest": digest}
    )
    assert data["error"]["code"] == "GRANT_CHANGED"
    assert center.pins_reviews[project["id"]] == digest  # put back


def test_a_restore_never_replaces_a_newer_review(center, project):
    center.pins_reviews[project["id"]] = "n" * 64
    center._restore_pins_review(project, "o" * 64)
    assert center.pins_reviews[project["id"]] == "n" * 64


def test_two_concurrent_starts_cannot_both_take_the_review(center, project):
    center.pins_reviews[project["id"]] = "a" * 64
    taken, refused = [], []
    barrier = threading.Barrier(8)

    def take():
        barrier.wait()
        try:
            taken.append(center._take_pins_review(project, "a" * 64))
        except BadRequest:
            refused.append(1)

    threads = [threading.Thread(target=take) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(taken) == 1 and len(refused) == 7
