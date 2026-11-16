# ruff: noqa: F811  (pytest fixtures imported from test_center)
"""Import by path in the console (#825, M9a console half): `registry import --json -- PATH`, a dry
run then the write bound to the reviewed digest (S-04), the code confirm for code-bearing kinds
(three-tier confirm), and the path checks: the server reads nothing at PATH, and the CLI refuses
anything that is not a registry version directory before it lists a file."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from test_center import api, center, cookie, project  # noqa: F401 (fixtures)

from agentbreeder import registry
from agentbreeder.center import registryimport
from agentbreeder.center.files import BadRequest

NORTHWIND = Path(registry.__file__).resolve().parent / "examples" / "northwind"
PROMPT = "prompt/northwind-support"
TOOL = "tool/orders"
MARKER = "private-file-marker-7f3a"  # the text of a file outside any version directory


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


@pytest.fixture
def elsewhere(project_dir, tmp_path) -> Path:
    """Another directory holding copies of the project's prompt and tool versions."""
    root = tmp_path / "elsewhere"
    for ref in (PROMPT, TOOL):
        shutil.copytree(project_dir / "registry" / ref / "1.0.0", root / ref.replace("/", "-"))
    return root


def post(center, project, cookie, **body):
    return api(center, "POST", f"/api/projects/{project['id']}/registry/import", body, cookie)


def version_dir(project_dir: Path, ref: str, version: str) -> Path:
    return project_dir / "registry" / ref / version


# -- importing ---------------------------------------------------------------------------------


def test_a_prompt_from_another_directory_is_reviewed_then_imported_and_resolves(
    center, project, cookie, project_dir, elsewhere
):
    source = str(elsewhere / "prompt-northwind-support")
    status, review, _ = post(center, project, cookie, path=source, version="1.1.0")
    assert status == 200 and review["error"] is None, review
    data = review["data"]
    assert data["dry_run"] is True and data["ref"] == f"{PROMPT}@1.1.0"
    assert data["runs_code"] is False and data["root_kind"] == "project"
    assert {f["path"] for f in data["files"] if f["type"] == "file"} >= {"manifest.yaml"}
    assert not version_dir(project_dir, PROMPT, "1.1.0").exists()
    status, done, _ = post(
        center, project, cookie, path=source, version="1.1.0", digest=data["sha256"]
    )
    assert status == 200 and done["error"] is None, done
    assert done["data"]["dry_run"] is False and "files" not in done["data"]
    assert done["data"]["sha256"] == data["sha256"]
    hit = registry.resolve(registry.Ref.parse(f"{PROMPT}@1.1.0"), registry.roots(project_dir))
    assert hit.path == version_dir(project_dir, PROMPT, "1.1.0")
    # A second import of the same version is the CLI's VERSION_EXISTS.
    _, again, _ = post(center, project, cookie, path=source, version="1.1.0")
    assert again["error"]["code"] == "VERSION_EXISTS", again


def test_an_import_under_another_name(center, project, cookie, project_dir, elsewhere):
    source = str(elsewhere / "prompt-northwind-support") + "/"  # a trailing slash is fine
    _, review, _ = post(center, project, cookie, path=source, name="support-copy")
    assert review["error"] is None, review
    assert review["data"]["ref"] == "prompt/support-copy@1.0.0"
    assert review["data"]["source_ref"] == f"{PROMPT}@1.0.0"
    _, done, _ = post(
        center, project, cookie, path=source, name="support-copy", digest=review["data"]["sha256"]
    )
    assert done["error"] is None, done
    assert version_dir(project_dir, "prompt/support-copy", "1.0.0").is_dir()


def test_a_tool_import_needs_the_code_confirm(center, project, cookie, project_dir, elsewhere):
    source = str(elsewhere / "tool-orders")
    _, review, _ = post(center, project, cookie, path=source, version="1.1.0")
    assert review["error"] is None and review["data"]["runs_code"] is True, review
    digest = review["data"]["sha256"]
    for confirmed in (None, False):
        extra = {} if confirmed is None else {"code_confirmed": confirmed}
        status, refused, _ = post(
            center, project, cookie, path=source, version="1.1.0", digest=digest, **extra
        )
        assert status == 400 and refused["error"]["code"] == "CODE_NOT_CONFIRMED", refused
        assert not version_dir(project_dir, TOOL, "1.1.0").exists()
    status, refused, _ = post(
        center, project, cookie, path=source, version="1.1.0", digest=digest, code_confirmed="1"
    )
    assert status == 400 and refused["error"]["code"] == "IMPORT_INVALID"
    status, done, _ = post(
        center, project, cookie, path=source, version="1.1.0", digest=digest, code_confirmed=True
    )
    assert status == 200 and done["error"] is None, done
    assert version_dir(project_dir, TOOL, "1.1.0").is_dir()


def test_an_import_writes_only_what_was_reviewed(center, project, cookie, project_dir, elsewhere):
    source = elsewhere / "prompt-northwind-support"
    _, review, _ = post(center, project, cookie, path=str(source), version="1.1.0")
    (source / "prompt.md").write_text("changed after the review\n")
    _, done, _ = post(
        center, project, cookie,
        path=str(source), version="1.1.0", digest=review["data"]["sha256"],
    )  # fmt: skip
    assert done["error"]["code"] == "DIGEST_MISMATCH", done
    assert not version_dir(project_dir, PROMPT, "1.1.0").exists()


def test_a_digest_not_from_a_review_writes_nothing(
    center, project, cookie, project_dir, elsewhere, monkeypatch
):
    writes = []
    real = center.runner.short_write
    monkeypatch.setattr(
        center.runner, "short_write", lambda *a, **k: writes.append(a) or real(*a, **k)
    )
    _, done, _ = post(
        center, project, cookie,
        path=str(elsewhere / "prompt-northwind-support"), version="1.1.0",
        digest="sha256:" + "0" * 64,
    )  # fmt: skip
    assert done["error"]["code"] == "DIGEST_MISMATCH", done
    assert writes == []  # refused on the fresh dry run, before the write lock
    assert not version_dir(project_dir, PROMPT, "1.1.0").exists()


# -- the path: nothing outside a registry version directory is read ----------------------------


def _private_dir(tmp_path: Path) -> Path:
    private = tmp_path / "private"
    private.mkdir()
    (private / "notes.txt").write_text(MARKER)
    return private


def _refused(center, project, cookie, path: str) -> dict:
    status, answer, _ = post(center, project, cookie, path=path)
    assert answer["data"] is None and answer["error"], answer
    assert MARKER not in json.dumps(answer)
    return answer["error"]


def test_a_directory_without_a_manifest_is_refused_unread(center, project, cookie, tmp_path):
    error = _refused(center, project, cookie, str(_private_dir(tmp_path)))
    assert error["code"] == "MANIFEST_INVALID"


def test_a_file_is_not_a_version_directory(center, project, cookie, tmp_path):
    error = _refused(center, project, cookie, str(_private_dir(tmp_path) / "notes.txt"))
    assert error["code"] == "ENTRY_UNSAFE"


def test_a_version_cannot_carry_a_link_to_a_file_outside_it(center, project, cookie, tmp_path):
    private = _private_dir(tmp_path)
    trick = tmp_path / "trick"
    trick.mkdir()
    (trick / "manifest.yaml").write_text("kind: prompt\nname: trick\nversion: 1.0.0\n")
    (trick / "leak").symlink_to(private / "notes.txt")
    assert _refused(center, project, cookie, str(trick))["code"] == "ENTRY_UNSAFE"
    (trick / "leak").unlink()
    os.link(private / "notes.txt", trick / "leak")
    assert _refused(center, project, cookie, str(trick))["code"] == "ENTRY_UNSAFE"


def test_a_linked_version_directory_and_the_filesystem_root_are_refused(
    center, project, cookie, project_dir, tmp_path
):
    link = tmp_path / "linked"
    link.symlink_to(project_dir / "registry" / PROMPT / "1.0.0")
    assert _refused(center, project, cookie, str(link))["code"] == "ENTRY_UNSAFE"
    assert _refused(center, project, cookie, "/")["code"] == "ENTRY_UNSAFE"


def test_a_missing_path_is_refused(center, project, cookie, tmp_path):
    assert _refused(center, project, cookie, str(tmp_path / "nothing"))["code"] == "ENTRY_UNSAFE"


@pytest.mark.parametrize(
    "body,code",
    [
        ({"path": "relative/dir"}, "PATH_INVALID"),
        ({"path": "-rf"}, "PATH_INVALID"),
        ({"path": "--json"}, "PATH_INVALID"),
        ({"path": "~/dir"}, "PATH_INVALID"),
        ({"path": ""}, "PATH_INVALID"),
        ({"path": None}, "PATH_INVALID"),
        ({"path": ["/x"]}, "PATH_INVALID"),
        ({"path": "/x\n--as y"}, "PATH_INVALID"),
        ({"path": "/x\x00y"}, "PATH_INVALID"),
        ({"path": "/x‮y"}, "PATH_INVALID"),
        ({"path": "/x​y"}, "PATH_INVALID"),
        ({"path": "/" + "a" * 4096}, "PATH_INVALID"),
        ({"name": "Bad Name"}, "NAME_INVALID"),
        ({"name": "-x"}, "NAME_INVALID"),
        ({"name": 3}, "NAME_INVALID"),
        ({"version": "1.0"}, "VERSION_INVALID"),
        ({"version": "1.0.0 --as x"}, "VERSION_INVALID"),
        ({"digest": "sha256:xyz"}, "DIGEST_INVALID"),
        ({"digest": ["x"]}, "DIGEST_INVALID"),
        ({"code_confirmed": "true"}, "IMPORT_INVALID"),
    ],
)
def test_bad_import_requests_never_reach_the_cli(center, project, cookie, body, code, monkeypatch):
    seen = []
    monkeypatch.setattr(center.runner, "read", lambda argv, *a, **k: seen.append(argv))
    monkeypatch.setattr(center.runner, "short_write", lambda *a, **k: seen.append(a))
    status, refused, _ = post(center, project, cookie, **{"path": "/tmp/v", **body})
    assert status == 400 and refused["error"]["code"] == code, refused
    assert seen == []


def test_a_request_that_is_not_an_object_is_refused():
    with pytest.raises(BadRequest) as caught:
        registryimport.import_request(["/x"])
    assert caught.value.code == "IMPORT_INVALID"


def test_import_argv_puts_the_path_last_after_a_double_dash():
    project = {"path": "/p"}
    request = registryimport.import_request({"path": "/a/b/", "name": "x", "version": "1.2.3"})
    assert registryimport.import_argv(project, request, dry_run=True) == [
        "registry", "import", "--project", "/p", "--as", "x", "--version", "1.2.3",
        "--dry-run", "--json", "--", "/a/b",
    ]  # fmt: skip
    digest = "sha256:" + "a" * 64
    bound = registryimport.import_request({"path": "/a", "digest": digest})
    assert registryimport.import_argv(project, bound, dry_run=False) == [
        "registry", "import", "--project", "/p", "--expect-digest", digest, "--json", "--", "/a",
    ]  # fmt: skip


# -- the answer check --------------------------------------------------------------------------


def _answer(**changes) -> dict:
    data = {
        "ref": "prompt/p@1.0.0", "kind": "prompt", "name": "p", "version": "1.0.0",
        "source_ref": "prompt/p@1.0.0", "sha256": "sha256:" + "b" * 64,
        "source": "/s", "root": "/r", "root_kind": "project", "path": "/r/prompt/p/1.0.0",
        "dry_run": True, "runs_code": False,
        "files": [{"path": "manifest.yaml", "type": "file", "size": 3, "text": "a\n"}],
    }  # fmt: skip
    data.update(changes)
    return data


def test_a_good_answer_passes():
    assert registryimport.import_answer(_answer(), dry_run=True)["files"][0]["text"] == "a\n"
    written = _answer(dry_run=False)
    del written["files"]
    assert "files" not in registryimport.import_answer(written, dry_run=False)


@pytest.mark.parametrize(
    "changes",
    [
        {"files": [{"path": "a.md", "type": "file", "text": "x‮y"}]},  # bidi override
        {"files": [{"path": "a.md", "type": "file", "text": "x\x1b[31m"}]},  # terminal escape
        {"files": [{"path": "a​.md", "type": "file", "text": "x"}]},  # invisible in a name
        {"files": [{"path": "a.md", "type": "link"}]},
        {"files": [{"path": "a.md", "type": "file", "text": "x" * (registryimport.MAX_TEXT + 1)}]},
        {"files": [{"path": "a.md", "type": "file", "size": -1}]},
        {"files": [{"path": "a.md", "type": "file", "binary": "no"}]},
        {"files": "nope"},
        {"files": None},
        {"runs_code": True},  # a prompt never runs code
        {"kind": "tool"},  # not the ref's kind
        {"ref": "prompt/p"},
        {"sha256": "nope"},
        {"source": "relative"},
        {"root_kind": "elsewhere"},
        {"dry_run": False},
    ],
)
def test_an_answer_off_the_contract_is_refused(changes):
    with pytest.raises(registryimport.BadOutput):
        registryimport.import_answer(_answer(**changes), dry_run=True)


def test_a_write_answer_never_lists_files():
    with pytest.raises(registryimport.BadOutput):
        registryimport.import_answer(_answer(dry_run=False), dry_run=False)


def test_an_answer_off_the_contract_reaches_the_browser_as_bad_output(
    center, project, cookie, monkeypatch
):
    bad = _answer(files=[{"path": "a.md", "type": "file", "text": "‮"}])
    monkeypatch.setattr(
        center.runner, "read", lambda *a, **k: {"data": bad, "error": None, "stale": False}
    )
    status, answer, _ = post(center, project, cookie, path="/tmp/v")
    assert answer["data"] is None and answer["error"]["code"] == "BAD_OUTPUT", answer
    assert "‮" not in json.dumps(answer, ensure_ascii=False)


def test_the_module_keeps_the_clis_code_kinds():
    assert registry.CODE_KINDS == {"tool", "service", "eval", "mcp", "runtime"}


def test_a_manifest_changed_to_a_code_kind_after_the_review_writes_nothing(
    center, project, cookie, project_dir, elsewhere
):
    """The review of a prompt cannot be used to write a tool without the confirm: the manifest is
    part of the digest, so the fresh dry run refuses before the confirm check or the write."""
    source = elsewhere / "prompt-northwind-support"
    _, review, _ = post(center, project, cookie, path=str(source), version="1.1.0")
    assert review["data"]["runs_code"] is False
    manifest = source / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("kind: prompt", "kind: tool"))
    _, done, _ = post(
        center, project, cookie,
        path=str(source), version="1.1.0", digest=review["data"]["sha256"],
    )  # fmt: skip
    assert done["data"] is None and done["error"], done
    assert not (project_dir / "registry" / "tool" / "northwind-support").exists()
    assert not version_dir(project_dir, PROMPT, "1.1.0").exists()


def test_control_and_bidi_characters_in_a_file_are_cleaned_by_the_cli(
    center, project, cookie, elsewhere
):
    source = elsewhere / "prompt-northwind-support"
    (source / "prompt.md").write_text("safe ‮evil\x1b[31m text\n")
    _, review, _ = post(center, project, cookie, path=str(source), version="1.1.0")
    assert review["error"] is None, review
    shown = next(f for f in review["data"]["files"] if f["path"] == "prompt.md")
    assert shown["stripped"] is True
    assert "‮" not in shown["text"] and "\x1b" not in shown["text"]


def test_a_file_entry_keeps_only_the_show_fields():
    data = _answer(
        files=[{"path": "a.md", "type": "file", "text": "x", "extra": "<b>", "sha256": None}]
    )
    (entry,) = registryimport.import_answer(data, dry_run=True)["files"]
    assert "extra" not in entry
    with pytest.raises(registryimport.BadOutput):
        registryimport.import_answer(
            _answer(files=[{"path": "a.md", "type": "file", "sha256": "x"}]), dry_run=True
        )


def test_an_off_contract_write_answer_says_it_may_have_been_written(
    center, project, cookie, monkeypatch
):
    review = _answer(ref="prompt/p@1.0.0")
    written = {**review, "dry_run": False, "files": []}  # a write never lists files
    monkeypatch.setattr(
        center.runner, "read", lambda *a, **k: {"data": review, "error": None, "stale": False}
    )
    monkeypatch.setattr(
        center.runner, "short_write",
        lambda *a, **k: {"data": written, "error": None, "stale": False},
    )  # fmt: skip
    _, answer, _ = post(center, project, cookie, path="/tmp/v", digest=review["sha256"])
    assert answer["error"]["code"] == "BAD_OUTPUT", answer
    assert "may have been written" in answer["error"]["message"]


# -- skills: code decided from the listed files (#1059, skills spec §5.2) -------------------------

_PACK_MANIFEST = 'kind: skill\nname: s\nversion: 1.0.0\ndescription: "d"\n'


def _skill_answer(files, runs_code):
    return _answer(
        ref="skill/s@1.0.0", kind="skill", name="s", source_ref="skill/s@1.0.0",
        path="/r/skill/s/1.0.0", runs_code=runs_code, files=files,
    )  # fmt: skip


def _manifest(text):
    return {"path": "manifest.yaml", "type": "file", "text": text}


def test_a_skill_pack_answer_passes_without_code():
    answer = _skill_answer([_manifest(_PACK_MANIFEST)], runs_code=False)
    assert registryimport.import_answer(answer, dry_run=True)["runs_code"] is False


def test_a_skill_answer_may_always_say_code():
    answer = _skill_answer([_manifest(_PACK_MANIFEST)], runs_code=True)
    assert registryimport.import_answer(answer, dry_run=True)["runs_code"] is True


@pytest.mark.parametrize(
    "files",
    [
        [_manifest(_PACK_MANIFEST), {"path": "scripts", "type": "dir"}],
        [_manifest(_PACK_MANIFEST), {"path": "scripts/x.py", "type": "file", "text": "x"}],
        [_manifest(_PACK_MANIFEST + "scripts: []\n")],
        [_manifest("kind: [")],  # unreadable manifest: fail closed
        [{"path": "manifest.yaml", "type": "file", "text": None}],
        [],
    ],
)
def test_a_skill_answer_hiding_code_is_refused(files):
    with pytest.raises(registryimport.BadOutput):
        registryimport.import_answer(_skill_answer(files, runs_code=False), dry_run=True)


def test_a_skill_write_answer_needs_a_bool():
    written = _skill_answer(None, runs_code="no")
    del written["files"]
    written["dry_run"] = False
    with pytest.raises(registryimport.BadOutput):
        registryimport.import_answer(written, dry_run=False)


def test_the_code_confirm_follows_a_skills_files(center, project, cookie, project_dir, tmp_path):
    """The console asks the code question for a script skill and not for an instruction pack
    (skills spec §5.4), decided from the staged files."""
    from test_registry_skill import make_skill, script_skill

    pack = str(make_skill(tmp_path / "pack"))
    _, review, _ = post(center, project, cookie, path=pack)
    assert review["error"] is None and review["data"]["runs_code"] is False, review
    _, done, _ = post(center, project, cookie, path=pack, digest=review["data"]["sha256"])
    assert done["error"] is None, done

    scripted = str(script_skill(tmp_path / "scripted", name="calc"))
    _, review, _ = post(center, project, cookie, path=scripted)
    assert review["error"] is None and review["data"]["runs_code"] is True, review
    digest = review["data"]["sha256"]
    status, refused, _ = post(center, project, cookie, path=scripted, digest=digest)
    assert status == 400 and refused["error"]["code"] == "CODE_NOT_CONFIRMED", refused
    _, done, _ = post(center, project, cookie, path=scripted, digest=digest, code_confirmed=True)
    assert done["error"] is None, done
    assert version_dir(project_dir, "skill/calc", "1.0.0").is_dir()
