# ruff: noqa: F811  (pytest fixtures imported from test_center)
"""Fork in the registry browser (#825, M9a console half, part 2) through #963's fork path:
`registry fork REF [--as NAME] [--version X.Y.Z] --project DIR (--dry-run | --expect-digest D)
--json`, any kind but an agent package, with the code confirm for code-bearing kinds checked on
the server's own dry run. The real CLI over a copy of Northwind."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from test_center import api, center, cookie, project  # noqa: F401 (fixtures)

from agentbreeder import registry
from agentbreeder.center import edits

NORTHWIND = Path(registry.__file__).resolve().parent / "examples" / "northwind"
SERVICE = "service/postgres-pgvector@1.2.0"
PROMPT = "prompt/northwind-support@1.0.0"


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


def fork(center, project, cookie, **body):
    return api(center, "POST", f"/api/projects/{project['id']}/registry/fork", body, cookie)


def test_a_built_in_service_version_is_forked_into_an_editable_project_copy(
    center, project, cookie, project_dir
):
    builtin = registry.resolve(registry.Ref.parse(SERVICE), registry.roots(project_dir))
    assert registry.root_kind(builtin.root) == "builtin"
    status, review, _ = fork(center, project, cookie, ref=SERVICE, version="1.2.1")
    assert status == 200 and review["error"] is None, review
    data = review["data"]
    assert data["ref"] == "service/postgres-pgvector@1.2.1" and data["runs_code"] is True
    assert data["root_kind"] == "project" and data["source_ref"] == SERVICE
    target = project_dir / "registry" / "service" / "postgres-pgvector" / "1.2.1"
    assert not target.exists()
    status, refused, _ = fork(
        center, project, cookie, ref=SERVICE, version="1.2.1", digest=data["sha256"]
    )
    assert status == 400 and refused["error"]["code"] == "CODE_NOT_CONFIRMED"
    assert not target.exists()
    status, done, _ = fork(
        center, project, cookie,
        ref=SERVICE, version="1.2.1", digest=data["sha256"], code_confirmed=True,
    )  # fmt: skip
    assert status == 200 and done["error"] is None, done
    assert done["data"]["dry_run"] is False and "files" not in done["data"]
    assert (target / "manifest.yaml").is_file()
    # The copy is the project's own: it resolves from ./registry, and the built-in is unchanged.
    hit = registry.resolve(
        registry.Ref.parse("service/postgres-pgvector@1.2.1"), registry.roots(project_dir)
    )
    assert hit.path == target and registry.root_kind(hit.root, project_dir) == "project"
    assert builtin.path.is_dir()


def test_a_fork_under_another_name_keeps_the_version_and_needs_no_confirm(
    center, project, cookie, project_dir
):
    status, review, _ = fork(center, project, cookie, ref=PROMPT, **{"as": "support-copy"})
    assert status == 200 and review["error"] is None, review
    assert review["data"]["ref"] == "prompt/support-copy@1.0.0"
    assert review["data"]["runs_code"] is False
    _, done, _ = fork(
        center, project, cookie,
        ref=PROMPT, digest=review["data"]["sha256"], **{"as": "support-copy"},
    )  # fmt: skip
    assert done["error"] is None, done
    assert (project_dir / "registry" / "prompt" / "support-copy" / "1.0.0").is_dir()


def test_a_digest_not_from_a_review_writes_nothing(center, project, cookie, monkeypatch):
    writes = []
    monkeypatch.setattr(center.runner, "short_write", lambda *a, **k: writes.append(a))
    _, done, _ = fork(
        center, project, cookie,
        ref=PROMPT, version="1.9.0", digest="sha256:" + "0" * 64,
    )  # fmt: skip
    assert done["error"]["code"] == "DIGEST_MISMATCH", done
    assert writes == []


def test_an_answer_off_the_contract_is_bad_output(center, project, cookie, monkeypatch):
    bad = {"ref": PROMPT, "kind": "prompt"}  # missing fields
    monkeypatch.setattr(
        center.runner, "read", lambda *a, **k: {"data": bad, "error": None, "stale": False}
    )
    _, answer, _ = fork(center, project, cookie, ref=PROMPT, version="1.9.0")
    assert answer["error"]["code"] == "BAD_OUTPUT", answer
    assert "registry fork" in answer["error"]["message"]


def test_fork_argv_with_a_new_name():
    project = {"path": "/p"}
    request = edits.fork_request({"ref": PROMPT, "as": "copy"})
    assert edits.fork_argv(project, request) == [
        "registry", "fork", PROMPT, "--as", "copy", "--project", "/p", "--dry-run", "--json",
    ]  # fmt: skip
    digest = "sha256:" + "c" * 64
    both = edits.fork_request({"ref": PROMPT, "as": "copy", "version": "2.0.0", "digest": digest})
    assert edits.fork_argv(project, both) == [
        "registry", "fork", PROMPT, "--as", "copy", "--version", "2.0.0", "--project", "/p",
        "--expect-digest", digest, "--json",
    ]  # fmt: skip
    # The server's fresh dry run before a write uses the same request.
    assert edits.fork_argv(project, both, dry_run=True)[-2:] == ["--dry-run", "--json"]


def test_a_confirmed_code_fork_of_a_changed_source_writes_nothing(
    center, project, cookie, project_dir, tmp_path, monkeypatch
):
    """The confirm is for the reviewed bytes: a shared tool changed after a confirmed review is
    DIGEST_MISMATCH on the server's fresh dry run, and nothing is written."""
    shared = tmp_path / "shared"
    (shared / "tool").mkdir(parents=True)
    shutil.move(project_dir / "registry" / "tool" / "orders", shared / "tool" / "orders")
    monkeypatch.setenv(registry.PATH_ENV, str(shared))
    ref = "tool/orders@1.0.0"
    _, review, _ = fork(center, project, cookie, ref=ref, version="1.0.1")
    assert review["error"] is None and review["data"]["runs_code"] is True, review
    code = shared / "tool" / "orders" / "1.0.0" / "src" / "orders.py"
    code.write_text(code.read_text() + "\n# changed after the review\n")
    _, done, _ = fork(
        center, project, cookie,
        ref=ref, version="1.0.1", digest=review["data"]["sha256"], code_confirmed=True,
    )  # fmt: skip
    assert done["error"]["code"] == "DIGEST_MISMATCH", done
    assert not (project_dir / "registry" / "tool" / "orders").exists()
