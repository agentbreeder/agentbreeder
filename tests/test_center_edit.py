# ruff: noqa: F811  (pytest fixtures imported from test_center)
"""Prompt editing and "Move agent to this version" (#826, M10 console half): `registry edit` with
the text on stdin and `agent set-ref`, each a dry run then a write bound to the reviewed digest
(S-04), run through the real CLI against a starter project."""

from __future__ import annotations

import shutil
import sys
import threading
from datetime import date
from pathlib import Path

import pytest
from test_center import api, center, cookie, project  # noqa: F401 (fixtures)

from agentbreeder import newagent, registry
from agentbreeder.center import edits, jobs
from agentbreeder.center.files import BadRequest

OLD = "prompt/hello-prompt@0.1.0"
NEW = "prompt/hello-prompt@0.1.1"
EVAL_OLD = "eval/hello-evals@0.1.0"


@pytest.fixture(autouse=True)
def no_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


@pytest.fixture
def fake_cli():
    """The real CLI (the server's own default prefix)."""
    return [sys.executable, "-I", "-m", "agentbreeder"]


@pytest.fixture
def project_dir(tmp_path) -> Path:
    plan = newagent.plan("hello", "gemini", tmp_path / "hello", today=date(2026, 11, 13))
    return newagent.create(plan).directory


@pytest.fixture
def calls(center, monkeypatch):  # noqa: F811
    """Every CLI child's argv and stdin, recorded around the real runner."""
    seen: list[tuple[list[str], str | None]] = []
    real = center.runner.read

    def read(argv, *args, **kwargs):
        seen.append((list(argv), kwargs.get("input")))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(center.runner, "read", read)
    return seen


def edit(center, project, cookie, **body):  # noqa: F811
    path = f"/api/projects/{project['id']}/registry/edit"
    return api(center, "POST", path, body, cookie)


def set_ref(center, project, cookie, **body):  # noqa: F811
    path = f"/api/projects/{project['id']}/agent/set-ref"
    return api(center, "POST", path, body, cookie)


TEXT = "You are hello. Answer in one short sentence. ünïcode ✓ --json\n"


def test_review_then_save_publishes_exactly_the_reviewed_text(
    center,
    project,
    cookie,
    project_dir,
    calls,  # noqa: F811
):
    status, review, _ = edit(
        center, project, cookie, ref=OLD, path="prompt.md", text=TEXT, version="0.1.1"
    )
    assert status == 200 and review["error"] is None, review
    data = review["data"]
    assert data["dry_run"] is True and data["ref"] == NEW and data["source_ref"] == OLD
    assert not (project_dir / "registry/prompt/hello-prompt/0.1.1").exists()
    changed = {f["path"]: f for f in data["diff"]["files"]}
    added = [line["text"] for h in changed["prompt.md"]["hunks"] for line in h["lines"]]
    assert TEXT.rstrip("\n") in added
    # S-12: the text went on stdin, never in argv.
    argv, given = calls[-1]
    assert given == TEXT and not any("hello. Answer" in part for part in argv)
    assert argv[-2:] == ["--dry-run", "--json"] and argv[argv.index("--set") + 2] == "-"

    status, saved, _ = edit(
        center, project, cookie,
        ref=OLD, path="prompt.md", text=TEXT, version="0.1.1", digest=data["sha256"],
    )  # fmt: skip
    assert status == 200 and saved["error"] is None, saved
    assert saved["data"]["dry_run"] is False and saved["data"]["sha256"] == data["sha256"]
    written = project_dir / "registry/prompt/hello-prompt/0.1.1/prompt.md"
    assert written.read_text(encoding="utf-8") == TEXT
    argv, given = calls[-1]
    assert argv[argv.index("--expect-digest") + 1] == data["sha256"] and given == TEXT

    # The same save again (a second tab): the version exists, nothing changes.
    status, again, _ = edit(
        center, project, cookie,
        ref=OLD, path="prompt.md", text=TEXT, version="0.1.1", digest=data["sha256"],
    )  # fmt: skip
    assert status == 200 and again["error"]["code"] == "VERSION_EXISTS" and again["data"] is None


def test_a_save_with_other_text_than_reviewed_is_refused(center, project, cookie, project_dir):  # noqa: F811
    _, review, _ = edit(
        center, project, cookie, ref=OLD, path="prompt.md", text=TEXT, version="0.1.1"
    )
    _, saved, _ = edit(
        center, project, cookie,
        ref=OLD, path="prompt.md", text=TEXT + "more\n", version="0.1.1",
        digest=review["data"]["sha256"],
    )  # fmt: skip
    assert saved["error"]["code"] == "DIGEST_MISMATCH"
    assert not (project_dir / "registry/prompt/hello-prompt/0.1.1").exists()


def test_unchanged_text_is_refused(center, project, cookie, project_dir):  # noqa: F811
    text = (project_dir / "registry/prompt/hello-prompt/0.1.0/prompt.md").read_text()
    _, review, _ = edit(
        center, project, cookie, ref=OLD, path="prompt.md", text=text, version="0.1.1"
    )
    assert review["error"]["code"] == "EDIT_UNCHANGED"


def test_a_shared_version_is_not_edited_in_place(
    center,
    project,
    cookie,
    project_dir,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    """A version outside the project registry (here a shared path root) is forked first."""
    shared = tmp_path / "shared"
    shutil.copytree(project_dir / "registry", shared)
    shutil.rmtree(project_dir / "registry" / "prompt")
    monkeypatch.setenv(registry.PATH_ENV, str(shared))
    _, review, _ = edit(
        center, project, cookie, ref=OLD, path="prompt.md", text="x\n", version="0.1.1"
    )
    assert review["error"]["code"] == "EDIT_NOT_PROJECT"
    assert not (shared / "prompt/hello-prompt/0.1.1").exists()


@pytest.mark.parametrize(
    "body,code",
    [
        ({"ref": "tool/t@1.0.0"}, "EDIT_KIND"),
        ({"ref": EVAL_OLD, "path": "manifest.yaml"}, "EDIT_INVALID"),
        ({"ref": EVAL_OLD, "path": "fixtures.sql"}, "EDIT_PATH"),
        ({"ref": EVAL_OLD, "path": "more/golden.jsonl"}, "EDIT_PATH"),
        ({"ref": EVAL_OLD}, "EDIT_PATH"),  # prompt.md is not an eval cases file
        ({"ref": "prompt/hello-prompt"}, "REF_INVALID"),
        ({"ref": None}, "REF_INVALID"),
        ({"path": "../x"}, "ENTRY_UNSAFE"),
        ({"path": "manifest.yaml"}, "EDIT_INVALID"),
        ({"path": 3}, "EDIT_INVALID"),
        ({"text": None}, "EDIT_INVALID"),
        ({"text": "x" * (edits.MAX_TEXT + 1)}, "EDIT_TOO_LARGE"),
        ({"version": "1.0"}, "VERSION_INVALID"),
        ({"version": None}, "VERSION_INVALID"),
        ({"version": "0.1.1 --dry-run"}, "VERSION_INVALID"),
        ({"digest": "sha256:xyz"}, "DIGEST_INVALID"),
        ({"digest": 5}, "DIGEST_INVALID"),
    ],
)
def test_bad_edit_requests_never_reach_the_cli(center, project, cookie, calls, body, code):  # noqa: F811
    request = {"ref": OLD, "path": "prompt.md", "text": "x\n", "version": "0.1.1", **body}
    status, data, _ = edit(center, project, cookie, **request)
    assert status == 400 and data["error"]["code"] == code, data
    assert calls == []


EVALS = "registry/eval/hello-evals"


def test_an_eval_set_edit_is_reviewed_then_saved_like_a_prompt(
    center,
    project,
    cookie,
    project_dir,
    calls,  # noqa: F811
):
    """M10 PR 2: the case editor sends the rebuilt cases file; the CLI loads the edited set as
    `release` does and writes exactly the reviewed bytes."""
    original = (project_dir / EVALS / "0.1.0/golden.jsonl").read_text()
    added = (
        '{"id": "refund", "turns": ["Can I get a refund?"], "expect": {"must_match": ["30 days"]},'
        ' "reference": "Refunds are given within 30 days."}\n'
    )
    text = original + added
    _, review, _ = edit(
        center, project, cookie, ref=EVAL_OLD, path="golden.jsonl", text=text, version="0.1.1"
    )
    assert review["error"] is None, review
    assert review["data"]["ref"] == "eval/hello-evals@0.1.1"
    assert calls[-1][1] == text  # on stdin
    _, saved, _ = edit(
        center, project, cookie,
        ref=EVAL_OLD, path="golden.jsonl", text=text, version="0.1.1",
        digest=review["data"]["sha256"],
    )  # fmt: skip
    assert saved["error"] is None, saved
    assert (project_dir / EVALS / "0.1.1/golden.jsonl").read_text() == text
    assert (project_dir / EVALS / "0.1.0/golden.jsonl").read_text() == original


def test_a_broken_case_is_the_clis_eval_invalid_with_its_line(
    center,
    project,
    cookie,
    project_dir,  # noqa: F811
):
    original = (project_dir / EVALS / "0.1.0/golden.jsonl").read_text()
    text = original + '{"id": "no-reference", "turns": ["hi"]}\n'
    _, review, _ = edit(
        center, project, cookie, ref=EVAL_OLD, path="golden.jsonl", text=text, version="0.1.1"
    )
    assert review["data"] is None and review["error"]["code"] == "EVAL_INVALID"
    assert "golden.jsonl:3 needs id, turns, reference" in review["error"]["message"]
    assert not (project_dir / EVALS / "0.1.1").exists()


def test_a_code_kind_save_needs_the_code_confirm(monkeypatch):
    """The kind hook M12 uses for tools: a review needs no confirm, a save does."""
    import re

    code = edits.EditKind(re.compile(r"src/.+\.py"), "source files", code=True)
    monkeypatch.setitem(edits.KINDS, "tool", code)
    body = {"ref": "tool/t@1.0.0", "path": "src/t.py", "text": "x = 1\n", "version": "1.0.1"}
    assert edits.edit_request(body)["digest"] is None
    digest = "sha256:" + "a" * 64
    for confirmed in (None, False, "true", 1):
        with pytest.raises(BadRequest) as refused:
            edits.edit_request({**body, "digest": digest, "code_confirmed": confirmed})
        assert refused.value.code == "CODE_NOT_CONFIRMED"
    confirmed = edits.edit_request({**body, "digest": digest, "code_confirmed": True})
    assert confirmed["digest"] == digest
    with pytest.raises(BadRequest) as refused:
        edits.edit_request({**body, "path": "README.md"})
    assert refused.value.code == "EDIT_PATH"
    # A kind not in KINDS (EDITABLE follows KINDS at import) is refused before its paths.
    monkeypatch.delitem(edits.KINDS, "tool")
    with pytest.raises(BadRequest) as refused:
        edits.edit_request({**body, "ref": "service/s@1.0.0"})
    assert refused.value.code == "EDIT_KIND"


def test_edit_path_codes_match_the_cli():
    for text, code in (("../x", "ENTRY_UNSAFE"), ("manifest.yaml", "EDIT_INVALID")):
        with pytest.raises(registry.RegistryError) as raised:
            registry.edit_path(text)
        assert raised.value.code == code


def test_edit_body_must_be_an_object():
    with pytest.raises(BadRequest):
        edits.edit_request(["x"])
    with pytest.raises(BadRequest):
        edits.set_ref_request("x")


def test_an_edit_takes_the_editor_body_cap(center, project, cookie):  # noqa: F811
    text = "a" * (200 * 1024) + "\n"  # over the 64 KiB JSON cap, under the editor's
    _, review, _ = edit(
        center, project, cookie, ref=OLD, path="prompt.md", text=text, version="0.1.1"
    )
    assert review["error"] is None and review["data"]["dry_run"] is True


def test_move_agent_shows_the_diff_then_writes_the_reviewed_file(
    center,
    project,
    cookie,
    project_dir,
    calls,  # noqa: F811
):
    registry.edit_version(registry.Ref.parse(OLD), project_dir, sets=[("prompt.md", b"Hi.\n")])
    path = project_dir / "agent.yaml"
    before = path.read_text()
    status, plan, _ = set_ref(center, project, cookie, old=OLD, new=NEW)
    assert status == 200 and plan["error"] is None, plan
    assert plan["data"]["dry_run"] is True and path.read_text() == before
    assert f"+prompt: {{ref: {NEW}}}" in plan["data"]["diff"]
    assert calls[-1][0][-2:] == ["--dry-run", "--json"]

    path.write_text(before + "# edited meanwhile\n")
    _, stale, _ = set_ref(
        center, project, cookie, old=OLD, new=NEW, base=plan["data"]["base_sha256"]
    )
    assert stale["error"]["code"] == "BASE_MISMATCH"
    assert f"prompt: {{ref: {NEW}}}" not in path.read_text()

    _, plan, _ = set_ref(center, project, cookie, old=OLD, new=NEW)
    _, moved, _ = set_ref(
        center, project, cookie, old=OLD, new=NEW, base=plan["data"]["base_sha256"]
    )
    assert moved["error"] is None and moved["data"]["dry_run"] is False, moved
    assert f"prompt: {{ref: {NEW}}}" in path.read_text()
    argv = calls[-1][0]
    assert argv[argv.index("--expect-base") + 1] == plan["data"]["base_sha256"]


def test_move_agent_refusals_come_from_the_cli(center, project, cookie):  # noqa: F811
    _, missing, _ = set_ref(center, project, cookie, old=OLD, new="prompt/hello-prompt@9.9.9")
    assert missing["error"]["code"] == "REF_NOT_FOUND"


@pytest.mark.parametrize(
    "body,code",
    [
        ({"old": OLD, "new": OLD}, "REF_UNCHANGED"),
        ({"old": "x", "new": NEW}, "REF_INVALID"),
        ({"old": OLD, "new": "--project=/"}, "REF_INVALID"),
        ({"old": OLD, "new": NEW, "base": "abc"}, "DIGEST_INVALID"),
    ],
)
def test_bad_set_ref_requests_never_reach_the_cli(center, project, cookie, calls, body, code):  # noqa: F811
    status, data, _ = set_ref(center, project, cookie, **body)
    assert status == 400 and data["error"]["code"] == code
    assert calls == []


def test_a_moved_project_is_refused_before_any_write(center, project, cookie, project_dir, calls):  # noqa: F811
    moved = project_dir.with_name("elsewhere")
    project_dir.rename(moved)
    project_dir.symlink_to(moved)
    status, data, _ = edit(
        center, project, cookie, ref=OLD, path="prompt.md", text="x\n", version="0.1.1"
    )
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"
    status, data, _ = set_ref(center, project, cookie, old=OLD, new=NEW)
    assert status == 400 and data["error"]["code"] == "PROJECT_UNSAFE"
    assert calls == []


def test_writes_are_short_writes_with_stdin(center, project, cookie, monkeypatch):  # noqa: F811
    seen = []

    def short_write(kind, target, argv, timeout=None, project=None, input=None):  # noqa: A002
        seen.append((kind, target, project and project["id"], input))
        return {"data": {"ok": True}, "error": None, "stale": False}

    monkeypatch.setattr(center.runner, "short_write", short_write)
    digest = "sha256:" + "a" * 64
    edit(
        center,
        project,
        cookie,
        ref=OLD,
        path="prompt.md",
        text=TEXT,
        version="0.1.1",
        digest=digest,
    )
    set_ref(center, project, cookie, old=OLD, new=NEW, base=digest)
    assert seen == [
        (edits.EDIT_KIND, f"{project['id']}:prompt/hello-prompt", None, TEXT),
        (edits.SET_REF_KIND, project["id"], project["id"], None),
    ]


def test_set_ref_waits_for_a_running_job_of_its_project(center, project):  # noqa: F811
    """`agent set-ref` holds the project: it is refused while a build or release holds it."""
    runner = center.runner
    with runner.mutex:
        runner.held[f"project:{project['id']}"] = "job-1"
    try:
        with pytest.raises(jobs.WriteBusy):
            runner.short_write(edits.SET_REF_KIND, project["id"], ["list"], project=project)
        # A registry edit holds only its name: a running job does not block it.
        answer = runner.short_write(edits.EDIT_KIND, "x:prompt/p", ["doctor", "--json"])
        assert answer["error"] is None
    finally:
        with runner.mutex:
            runner.held.clear()


def test_two_saves_of_one_name_do_not_run_together(center):  # noqa: F811
    runner = center.runner
    gate, entered = threading.Event(), threading.Event()
    real = runner.read

    def slow(argv, *args, **kwargs):
        entered.set()
        gate.wait(5)
        return real(argv, *args, **kwargs)

    runner.read = slow
    try:
        first = threading.Thread(
            target=runner.short_write, args=(edits.EDIT_KIND, "p:prompt/a", ["doctor", "--json"])
        )
        first.start()
        assert entered.wait(5)
        with pytest.raises(jobs.WriteBusy):
            runner.short_write(edits.EDIT_KIND, "p:prompt/a", ["doctor", "--json"])
        gate.set()
        first.join(5)
    finally:
        runner.read = real


@pytest.mark.parametrize(
    "options,locked",
    [({"lock": "update"}, False), ({}, True), ({"lock": "yes"}, True), ({"other": 1}, True)],
)
def test_the_build_after_a_move_reaches_the_cli_without_locked(
    center, project, cookie, monkeypatch, options, locked
):
    """ "Build and update agent.lock" (#826): the `lock` option survives the server's option
    filter, and only `update` drops --locked."""
    seen = []

    def start(project, kind, options, keys=None, on_success=None):
        seen.append(jobs.argv_for(kind, project, options))
        return "0" * 32

    monkeypatch.setattr(center.runner, "start", start)
    path = f"/api/projects/{project['id']}/jobs"
    status, _, _ = api(center, "POST", path, {"kind": "build", "options": options}, cookie)
    assert status == 200 and seen and seen[0][0] == "build"
    assert ("--locked" in seen[0]) is locked
