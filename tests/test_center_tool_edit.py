# ruff: noqa: F811  (pytest fixtures imported from test_center)
"""The tool editor and "Fork to edit" (#828, M12 console half, part 3): a tool's Python source is
edited through the real `registry edit` (the CLI's tool check runs on the review), a save needs
the code confirm, and a version outside the project registry is forked into it first with
`registry fork`, a dry run then a copy bound to the reviewed digest (S-04)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from test_center import api, center, cookie, project  # noqa: F401 (fixtures)

from agentbreeder import registry
from agentbreeder.center import edits
from agentbreeder.center.files import BadRequest

NORTHWIND = Path(registry.__file__).resolve().parent / "examples" / "northwind"
TOOL = "tool/orders@1.0.0"
SOURCE = "src/orders.py"


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


def post(center, project, cookie, route, **body):
    return api(center, "POST", f"/api/projects/{project['id']}/registry/{route}", body, cookie)


def source(project_dir: Path, version: str = "1.0.0") -> Path:
    return project_dir / "registry" / "tool" / "orders" / version / SOURCE


# -- editing a tool's code ---------------------------------------------------------------------


def test_a_tool_edit_is_reviewed_then_saved_only_with_the_code_confirm(
    center, project, cookie, project_dir
):
    text = source(project_dir).read_text() + "\n\ndef helper() -> int:\n    return 1\n"
    request = {"ref": TOOL, "path": SOURCE, "text": text, "version": "1.0.1"}
    status, review, _ = post(center, project, cookie, "edit", **request)
    assert status == 200 and review["error"] is None, review
    data = review["data"]
    assert data["dry_run"] is True and data["ref"] == "tool/orders@1.0.1"
    assert not source(project_dir, "1.0.1").exists()
    digest = data["sha256"]
    for confirmed in (None, False, "true"):
        extra = {} if confirmed is None else {"code_confirmed": confirmed}
        status, refused, _ = post(
            center, project, cookie, "edit", **request, digest=digest, **extra
        )
        assert status == 400 and refused["error"]["code"] == "CODE_NOT_CONFIRMED"
        assert not source(project_dir, "1.0.1").exists()
    status, saved, _ = post(
        center, project, cookie, "edit", **request, digest=digest, code_confirmed=True
    )
    assert status == 200 and saved["error"] is None, saved
    assert source(project_dir, "1.0.1").read_text() == text
    assert source(project_dir).read_text() != text  # 1.0.0 never changes


def test_the_clis_tool_check_refuses_a_broken_function_on_review(
    center, project, cookie, project_dir
):
    text = source(project_dir).read_text().replace("def lookup_orders(", "def renamed(")
    request = {"ref": TOOL, "path": SOURCE, "text": text, "version": "1.0.1"}
    status, review, _ = post(center, project, cookie, "edit", **request)
    assert status == 200 and review["data"] is None
    assert review["error"]["code"] == "TOOL_INVALID", review
    assert "lookup_orders" in review["error"]["message"]
    assert not source(project_dir, "1.0.1").exists()


@pytest.mark.parametrize(
    "path",
    [
        "tool.yaml",
        "requirements.txt",
        "src/notes.txt",
        "orders.py",
        "src/pkg/x.pyc",
        "src/",
        # Path tricks: none reaches the CLI as a source file.
        "src/../tool.yaml",
        "SRC/x.py",
        "src/x.PY",
        "src\\x.py",
        "src/x.py/",
        "src//x.py",
        "src/x.py\n",
        "/src/x.py",
        "src/__pycache__/x.py",
    ],  # fmt: skip
)
def test_only_python_under_src_is_edited(center, project, cookie, path):
    request = {"ref": TOOL, "path": path, "text": "x = 1\n", "version": "1.0.1"}
    status, refused, _ = post(center, project, cookie, "edit", **request)
    assert status == 400
    assert refused["error"]["code"] in ("EDIT_PATH", "EDIT_INVALID", "ENTRY_UNSAFE")


def test_the_tool_kind_matches_the_console_spec():
    tool = edits.KINDS["tool"]
    assert tool.code is True
    for good in ("src/orders.py", "src/pkg/mod.py"):
        assert tool.paths.fullmatch(good)
    for bad in ("tool.yaml", "orders.py", "src/x.txt", "src/x.py/y"):
        assert not tool.paths.fullmatch(bad), bad


# -- Fork to edit ------------------------------------------------------------------------------


@pytest.fixture
def shared(project_dir, tmp_path, monkeypatch) -> Path:
    """`tool/orders` only on a shared path root (AGENTBREEDER_REGISTRY_PATH), not the project."""
    root = tmp_path / "shared"
    (root / "tool").mkdir(parents=True)
    shutil.move(project_dir / "registry" / "tool" / "orders", root / "tool" / "orders")
    monkeypatch.setenv(registry.PATH_ENV, str(root))
    return root


def test_a_shared_tool_is_forked_after_a_review_then_edited(
    center, project, cookie, project_dir, shared
):
    text = "x = 1\n"
    status, edited, _ = post(
        center, project, cookie, "edit", ref=TOOL, path=SOURCE, text=text, version="1.0.1"
    )
    assert edited["error"]["code"] == "EDIT_NOT_PROJECT"  # never in place
    status, review, _ = post(center, project, cookie, "fork", ref=TOOL, version="1.0.1")
    assert status == 200 and review["error"] is None, review
    data = review["data"]
    assert data["dry_run"] is True and data["ref"] == "tool/orders@1.0.1"
    assert data["runs_code"] is True and data["root_kind"] == "project"
    assert {f["path"] for f in data["files"] if f["type"] == "file"} >= {SOURCE, "tool.yaml"}
    assert not source(project_dir, "1.0.1").exists()
    status, forked, _ = post(
        center, project, cookie, "fork", ref=TOOL, version="1.0.1", digest=data["sha256"]
    )
    assert status == 200 and forked["error"] is None, forked
    assert (
        source(project_dir, "1.0.1").read_text()
        == (shared / "tool/orders/1.0.0" / SOURCE).read_text()
    )
    assert not (shared / "tool/orders/1.0.1").exists()
    # The copy is the project's: it is edited like any project version.
    request = {"ref": "tool/orders@1.0.1", "path": SOURCE, "version": "1.0.2"}
    body = source(project_dir, "1.0.1").read_text() + "\n# reviewed\n"
    status, review, _ = post(center, project, cookie, "edit", **request, text=body)
    assert status == 200 and review["error"] is None, review


def test_a_fork_writes_only_what_was_reviewed(center, project, cookie, project_dir, shared):
    _, review, _ = post(center, project, cookie, "fork", ref=TOOL, version="1.0.1")
    source_file = shared / "tool/orders/1.0.0" / SOURCE
    source_file.write_text(source_file.read_text() + "\n# changed after the review\n")
    _, forked, _ = post(
        center, project, cookie, "fork",
        ref=TOOL, version="1.0.1", digest=review["data"]["sha256"],
    )  # fmt: skip
    assert forked["error"]["code"] == "DIGEST_MISMATCH", forked
    assert not source(project_dir, "1.0.1").exists()


@pytest.mark.parametrize(
    "body,code",
    [
        ({"ref": "service/postgres-pgvector@1.2.0"}, "EDIT_KIND"),
        ({"ref": "runtime/adk@1.2.0"}, "EDIT_KIND"),
        ({"ref": "tool/orders"}, "REF_INVALID"),
        ({"ref": None}, "REF_INVALID"),
        ({"version": "1.0.0"}, "FORK_UNCHANGED"),
        ({"version": "1.0"}, "VERSION_INVALID"),
        ({"version": "1.0.1 --as x"}, "VERSION_INVALID"),
        ({"version": None}, "VERSION_INVALID"),
        ({"digest": "sha256:xyz"}, "DIGEST_INVALID"),
        ({"digest": ["x"]}, "DIGEST_INVALID"),
    ],
)
def test_bad_fork_requests_never_reach_the_cli(center, project, cookie, body, code, monkeypatch):
    seen = []
    monkeypatch.setattr(center.runner, "read", lambda argv, *a, **k: seen.append(argv))
    status, refused, _ = post(
        center, project, cookie, "fork", **{"ref": TOOL, "version": "1.0.1", **body}
    )
    assert status == 400 and refused["error"]["code"] == code
    assert seen == []


def test_fork_argv_is_a_dry_run_or_bound_to_the_digest():
    project = {"path": "/p", "environment": "dev"}
    dry = edits.fork_request({"ref": TOOL, "version": "1.0.1"})
    assert edits.fork_argv(project, dry) == [
        "registry", "fork", TOOL, "--version", "1.0.1", "--project", "/p", "--dry-run", "--json",
    ]  # fmt: skip
    digest = "sha256:" + "a" * 64
    bound = edits.fork_request({"ref": TOOL, "version": "1.0.1", "digest": digest})
    assert edits.fork_argv(project, bound)[-3:] == ["--expect-digest", digest, "--json"]
    with pytest.raises(BadRequest) as refused:
        edits.fork_request(["x"])
    assert refused.value.code == "FORK_INVALID"


def test_a_fork_copies_code_without_the_code_confirm(center, project, cookie, project_dir, shared):
    """A fork copies a version and runs nothing, so it needs no confirm; editing the copy does."""
    _, review, _ = post(center, project, cookie, "fork", ref=TOOL, version="1.0.1")
    digest = review["data"]["sha256"]
    _, forked, _ = post(center, project, cookie, "fork", ref=TOOL, version="1.0.1", digest=digest)
    assert forked["error"] is None, forked
    text = source(project_dir, "1.0.1").read_text() + "\n# edited\n"
    request = {"ref": "tool/orders@1.0.1", "path": SOURCE, "text": text, "version": "1.0.2"}
    _, edit_review, _ = post(center, project, cookie, "edit", **request)
    status, refused, _ = post(
        center, project, cookie, "edit", **request, digest=edit_review["data"]["sha256"]
    )
    assert status == 400 and refused["error"]["code"] == "CODE_NOT_CONFIRMED"


def test_a_project_version_can_be_forked_too(center, project, cookie, project_dir):
    """Deliberate, as the CLI: the screen offers Fork only outside the project, but forking a
    project version just copies it to a new version the project owns."""
    _, review, _ = post(center, project, cookie, "fork", ref=TOOL, version="1.0.5")
    assert review["error"] is None and review["data"]["source_ref"] == TOOL, review
    _, forked, _ = post(
        center, project, cookie, "fork",
        ref=TOOL, version="1.0.5", digest=review["data"]["sha256"],
    )  # fmt: skip
    assert forked["error"] is None, forked
    assert source(project_dir, "1.0.5").read_text() == source(project_dir).read_text()
