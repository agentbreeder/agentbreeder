# ruff: noqa: F811  (pytest fixtures imported from test_center)
"""Knowledge documents in the console (#830, M14 console half, part 2): a knowledge version's
documents (`docs/NAME.md` or `docs/NAME.txt`) are edited, added and removed through the real
`registry edit`, each a dry run then a write bound to the reviewed digest (S-04). A removal is
`--remove PATH` with nothing on stdin; only kinds with `remove` take it."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from test_center import api, center, cookie, project  # noqa: F401 (fixtures)

from agentbreeder import knowledge, registry
from agentbreeder.center import edits
from agentbreeder.center.files import BadRequest

NORTHWIND = Path(registry.__file__).resolve().parent / "examples" / "northwind"
REF = "knowledge/northwind-help@1.0.0"


@pytest.fixture(autouse=True)
def no_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


@pytest.fixture
def fake_cli():
    """The real CLI (the server's own default prefix)."""
    return [sys.executable, "-I", "-m", "agentbreeder"]


@pytest.fixture
def project_dir(tmp_path) -> Path:
    target = tmp_path / "northwind"
    shutil.copytree(NORTHWIND, target)
    return target


def post(center, project, cookie, **body):
    return api(center, "POST", f"/api/projects/{project['id']}/registry/edit", body, cookie)


def docs(project_dir: Path, version: str = "1.0.0") -> Path:
    return project_dir / "registry" / "knowledge" / "northwind-help" / version / "docs"


def review_then_save(center, project, cookie, **request) -> dict:
    status, review, _ = post(center, project, cookie, **request)
    assert status == 200 and review["error"] is None, review
    assert review["data"]["dry_run"] is True
    status, saved, _ = post(center, project, cookie, **request, digest=review["data"]["sha256"])
    assert status == 200 and saved["error"] is None, saved
    return review["data"]


def test_a_document_is_edited_after_a_review(center, project, cookie, project_dir):
    text = docs(project_dir).joinpath("refunds.md").read_text() + "\nRefunds take 5 days.\n"
    request = {"ref": REF, "path": "docs/refunds.md", "text": text, "version": "1.0.1"}
    status, review, _ = post(center, project, cookie, **request)
    assert status == 200 and review["error"] is None, review
    assert not docs(project_dir, "1.0.1").exists()  # a review writes nothing
    review_then_save(center, project, cookie, **request)
    assert docs(project_dir, "1.0.1").joinpath("refunds.md").read_text() == text
    assert docs(project_dir).joinpath("refunds.md").read_text() != text  # 1.0.0 never changes


def test_a_new_document_is_added(center, project, cookie, project_dir):
    text = "# Warranty\n\nHardware has a two-year warranty.\n"
    request = {"ref": REF, "path": "docs/warranty.txt", "text": text, "version": "1.1.0"}
    review = review_then_save(center, project, cookie, **request)
    assert [f["status"] for f in review["diff"]["files"] if f["path"] == "docs/warranty.txt"] == [
        "added"
    ]
    assert docs(project_dir, "1.1.0").joinpath("warranty.txt").read_text() == text
    # The CLI's expected index now holds the new document.
    hit = knowledge.versions("northwind-help", "1.1.0", registry.roots(project_dir))[0]
    assert knowledge.expected(hit).documents == 6


def test_a_document_is_removed_after_a_review(center, project, cookie, project_dir):
    request = {"ref": REF, "path": "docs/billing.md", "remove": True, "version": "1.0.1"}
    status, review, _ = post(center, project, cookie, **request)
    assert status == 200 and review["error"] is None, review
    removed = [f for f in review["data"]["diff"]["files"] if f["path"] == "docs/billing.md"]
    assert [f["status"] for f in removed] == ["removed"]
    assert not docs(project_dir, "1.0.1").exists()
    status, saved, _ = post(center, project, cookie, **request, digest=review["data"]["sha256"])
    assert status == 200 and saved["error"] is None, saved
    assert not docs(project_dir, "1.0.1").joinpath("billing.md").exists()
    assert docs(project_dir, "1.0.1").joinpath("refunds.md").exists()
    assert docs(project_dir).joinpath("billing.md").exists()  # 1.0.0 never changes


def test_a_removal_with_another_digest_is_refused(center, project, cookie, project_dir):
    request = {"ref": REF, "path": "docs/billing.md", "remove": True, "version": "1.0.1"}
    status, refused, _ = post(center, project, cookie, **request, digest="sha256:" + "0" * 64)
    assert status == 200 and refused["error"]["code"] == "DIGEST_MISMATCH", refused
    assert not docs(project_dir, "1.0.1").exists()


def test_removing_a_missing_document_is_the_clis_refusal(center, project, cookie, project_dir):
    request = {"ref": REF, "path": "docs/nope.md", "remove": True, "version": "1.0.1"}
    status, refused, _ = post(center, project, cookie, **request)
    assert status == 200 and refused["error"]["code"] == "EDIT_INVALID", refused
    assert "no such file" in refused["error"]["message"]


@pytest.mark.parametrize(
    "path",
    [
        "manifest.yaml",
        "docs/notes.pdf",
        "docs/sub/a.md",
        "docs/a.MD",
        "notes.md",
        "docs/",
        "docs/../manifest.yaml",
        "docs//a.md",
        "/docs/a.md",
        "docs/a.md\n",
        "docs\\a.md",
        "docs/a\x1bb.md",
        "docs/.hidden.md",
    ],
)
@pytest.mark.parametrize("remove", [False, True])
def test_only_documents_are_changed(center, project, cookie, path, remove):
    body = {"remove": True} if remove else {"text": "x\n"}
    status, refused, _ = post(center, project, cookie, ref=REF, path=path, version="1.0.1", **body)
    assert status == 400
    assert refused["error"]["code"] in ("EDIT_PATH", "EDIT_INVALID", "ENTRY_UNSAFE"), refused


@pytest.mark.parametrize(
    "body,code",
    [
        # Only kinds with `remove` take it.
        ({"ref": "prompt/northwind-support@1.0.0", "path": "prompt.md", "remove": True},
         "EDIT_REMOVE"),
        ({"ref": "tool/orders@1.0.0", "path": "src/orders.py", "remove": True}, "EDIT_REMOVE"),
        # A removal takes no text; remove is a boolean.
        ({"ref": REF, "path": "docs/billing.md", "remove": True, "text": ""}, "EDIT_INVALID"),
        ({"ref": REF, "path": "docs/billing.md", "remove": "true"}, "EDIT_INVALID"),
        ({"ref": REF, "path": "docs/billing.md", "remove": 1}, "EDIT_INVALID"),
        ({"ref": REF, "path": "docs/billing.md", "remove": False}, "EDIT_INVALID"),
    ],
)  # fmt: skip
def test_bad_removals_never_reach_the_cli(center, project, cookie, body, code, monkeypatch):
    def refuse(*args, **kwargs):  # pragma: no cover - a reached CLI fails the test
        raise AssertionError("the CLI was run")

    monkeypatch.setattr(center.runner, "read", refuse)
    monkeypatch.setattr(center.runner, "short_write", refuse)
    status, refused, _ = post(center, project, cookie, **body, version="1.0.1")
    assert status == 400 and refused["error"]["code"] == code


def test_a_removal_sends_nothing_on_stdin(center, project, cookie, monkeypatch):
    seen = []

    def read(argv, timeout=None, cache=True, input=None, **kwargs):  # noqa: A002
        seen.append(("read", argv, input))
        return {"data": {"ok": True}, "error": None, "stale": False}

    def short_write(kind, target, argv, timeout=None, project=None, input=None):  # noqa: A002
        seen.append((kind, target, argv, input))
        return {"data": {"ok": True}, "error": None, "stale": False}

    monkeypatch.setattr(center.runner, "read", read)
    monkeypatch.setattr(center.runner, "short_write", short_write)
    request = {"ref": REF, "path": "docs/billing.md", "remove": True, "version": "1.0.1"}
    digest = "sha256:" + "a" * 64
    post(center, project, cookie, **request)
    post(center, project, cookie, **request, digest=digest)
    tail = ["--version", "1.0.1", "--project", project["path"]]
    assert seen == [
        ("read", ["registry", "edit", REF, "--remove", "docs/billing.md", *tail, "--dry-run",
                  "--json"], None),
        (edits.EDIT_KIND, f"{project['id']}:knowledge/northwind-help",
         ["registry", "edit", REF, "--remove", "docs/billing.md", *tail, "--expect-digest",
          digest, "--json"], None),
    ]  # fmt: skip


def test_the_knowledge_kind_matches_the_console_spec():
    spec = edits.KINDS["knowledge"]
    assert spec.remove is True and spec.code is False
    assert not any(kind.remove for name, kind in edits.KINDS.items() if name != "knowledge")
    for good in ("docs/refunds.md", "docs/a-b.txt", "docs/v1.2_notes.md"):
        assert spec.paths.fullmatch(good), good
    for bad in (
        "docs/a.MD",
        "docs/a/b.md",
        "docs/a.pdf",
        "a.md",
        "manifest.yaml",
        "docs/.x.md",
        "docs/a b.md",
        "docs/a\nb.md",
        "docs/a\u202eb.md",
        "docs/" + "a" * 101 + ".md",
    ):
        assert not spec.paths.fullmatch(bad), bad


def test_edit_request_shapes():
    base = {"ref": REF, "path": "docs/a.md", "version": "1.0.1"}
    assert edits.edit_request({**base, "text": "x"})["remove"] is False
    removal = edits.edit_request({**base, "remove": True})
    assert removal["text"] is None and removal["remove"] is True
    with pytest.raises(BadRequest):
        edits.edit_request({**base, "remove": True, "text": None, "digest": "nope"})
