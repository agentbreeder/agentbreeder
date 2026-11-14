"""`registry edit REF --set PATH FILE | --remove PATH` (#826, M10; console §8.4)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from agentbreeder import cli, newagent, registry
from agentbreeder.registry import Ref, RegistryError

PROMPT = Ref.parse("prompt/hello-prompt@0.1.0")
EVALS = Ref.parse("eval/hello-evals@0.1.0")


@pytest.fixture(autouse=True)
def no_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


@pytest.fixture
def starter(tmp_path) -> Path:
    plan = newagent.plan("hello", "gemini", tmp_path / "hello", today=date(2026, 11, 13))
    return newagent.create(plan).directory


def files_of(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def tree(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


# -- the acceptance path ---------------------------------------------------------------------


def test_editing_the_starter_prompt_publishes_0_1_1(starter):
    """M10's acceptance, CLI half: editing the starter's prompt saves 0.1.1; 0.1.0 is unchanged."""
    source = starter / "registry/prompt/hello-prompt/0.1.0"
    before = files_of(source)
    result = registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"Be brief.\n")])
    target = starter / "registry/prompt/hello-prompt/0.1.1"
    assert result["ref"] == "prompt/hello-prompt@0.1.1"
    assert result["source_ref"] == str(PROMPT) and result["root_kind"] == "project"
    assert result["path"] == str(target.resolve()) and result["dry_run"] is False
    assert files_of(source) == before  # immutable
    after = files_of(target)
    assert after["prompt.md"] == b"Be brief.\n"
    manifest = after["manifest.yaml"].decode()
    assert 'version: "0.1.1"' in manifest and "description: The hello agent" in manifest
    assert registry.resolve(Ref.parse(result["ref"]), registry.roots(starter)).path == target
    assert result["sha256"] == registry.copy_and_hash(target)
    assert list((starter / "registry").glob(".import-*")) == []


def test_the_result_carries_a_diff_from_the_source(starter):
    result = registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"Be brief.\n")])
    diff = result["diff"]
    assert diff["from"]["ref"] == str(PROMPT) and diff["to"]["ref"] == result["ref"]
    assert diff["to"]["sha256"] == result["sha256"]
    assert diff["from"]["sha256"] == registry.copy_and_hash(
        starter / "registry/prompt/hello-prompt/0.1.0"
    )
    changed = {entry["path"]: entry for entry in diff["files"]}
    assert set(changed) == {"manifest.yaml", "prompt.md"}
    lines = [line for hunk in changed["prompt.md"]["hunks"] for line in hunk["lines"]]
    assert {"op": "+", "text": "Be brief."} in lines
    text = registry.unified(diff)
    assert f"+++ {result['ref']}/prompt.md" in text and "+Be brief." in text


@pytest.mark.parametrize(
    ("bump", "version"), [("patch", "0.1.1"), ("minor", "0.2.0"), ("major", "1.0.0")]
)
def test_bump_parts(starter, bump, version):
    result = registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"x\n")], bump=bump)
    assert result["version"] == version


def test_an_exact_version(starter):
    result = registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"x\n")], version="0.3.7")
    assert result["ref"] == "prompt/hello-prompt@0.3.7"


@pytest.mark.parametrize(
    ("version", "part", "expected"),
    [("1.2.3", "patch", "1.2.4"), ("1.2.3", "minor", "1.3.0"), ("1.2.3", "major", "2.0.0")],
)
def test_bumped(version, part, expected):
    assert registry.bumped(version, part) == expected


def test_bumped_refuses_past_the_version_pattern():
    with pytest.raises(RegistryError) as error:
        registry.bumped("0.0.999999", "patch")
    assert error.value.code == "VERSION_INVALID"


# -- S-04: the bytes written are the bytes reviewed --------------------------------------------


def test_dry_run_writes_nothing_and_its_digest_is_what_a_write_gets(starter):
    before = tree(starter)
    preview = registry.edit_version(
        PROMPT, starter, sets=[("prompt.md", b"Be brief.\n")], dry_run=True
    )
    assert tree(starter) == before and preview["dry_run"] is True
    assert any(entry["path"] == "prompt.md" for entry in preview["files"])
    written = registry.edit_version(
        PROMPT, starter, sets=[("prompt.md", b"Be brief.\n")], expect_digest=preview["sha256"]
    )
    assert written["sha256"] == preview["sha256"]


def test_a_different_edit_from_the_reviewed_one_is_refused(starter):
    preview = registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"A\n")], dry_run=True)
    before = tree(starter)
    with pytest.raises(RegistryError) as error:
        registry.edit_version(
            PROMPT, starter, sets=[("prompt.md", b"B\n")], expect_digest=preview["sha256"]
        )
    assert error.value.code == "DIGEST_MISMATCH"
    assert tree(starter) == before


# -- conflicts ---------------------------------------------------------------------------------


def test_an_existing_bumped_version_is_version_exists(starter):
    registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"A\n")])
    with pytest.raises(RegistryError) as error:  # a second tab saving the same bump
        registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"B\n")])
    assert error.value.code == "VERSION_EXISTS"
    assert (starter / "registry/prompt/hello-prompt/0.1.1/prompt.md").read_bytes() == b"A\n"


def test_a_version_published_meanwhile_is_version_exists(starter, monkeypatch):
    """Two tabs: the other save lands between the existence check and the rename."""
    real = registry._check_target

    def racing(root, ref):
        target = real(root, ref)
        target.mkdir(parents=True)
        (target / "other").write_text("the other tab\n")
        return target

    monkeypatch.setattr(registry, "_check_target", racing)
    with pytest.raises(RegistryError) as error:
        registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"A\n")])
    assert error.value.code == "VERSION_EXISTS" and "meanwhile" in str(error.value)
    assert list((starter / "registry").glob(".import-*")) == []


def test_the_same_version_is_refused(starter):
    with pytest.raises(RegistryError) as error:
        registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"A\n")], version="0.1.0")
    assert error.value.code == "VERSION_EXISTS"


def test_a_builtin_version_must_be_forked_first(starter):
    with pytest.raises(RegistryError) as error:
        registry.edit_version(Ref.parse("service/echo@1.0.0"), starter, sets=[("x", b"y")])
    assert error.value.code == "EDIT_NOT_PROJECT"
    assert "registry fork service/echo@1.0.0 --version 1.0.1" in str(error.value)


def test_a_path_root_version_must_be_forked_first(starter, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shutil.copytree(starter / "registry/prompt", shared / "prompt")
    shutil.rmtree(starter / "registry/prompt")
    monkeypatch.setenv(registry.PATH_ENV, str(shared))
    with pytest.raises(RegistryError) as error:
        registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"A\n")])
    assert error.value.code == "EDIT_NOT_PROJECT" and "(path)" in str(error.value)


def test_a_missing_ref(starter):
    with pytest.raises(RegistryError) as error:
        registry.edit_version(Ref.parse("prompt/nope@1.0.0"), starter, sets=[("a", b"b")])
    assert error.value.code == "REF_NOT_FOUND"


# -- the edits ---------------------------------------------------------------------------------


def test_add_and_remove_files_in_subdirectories(starter):
    result = registry.edit_version(
        EVALS,
        starter,
        sets=[("extra/notes/readme.txt", b"notes\n")],
    )
    target = Path(result["path"])
    assert (target / "extra/notes/readme.txt").read_bytes() == b"notes\n"
    assert oct((target / "extra/notes/readme.txt").stat().st_mode & 0o777) == oct(0o644)
    removed = registry.edit_version(
        Ref.parse(result["ref"]), starter, removes=["extra/notes/readme.txt"]
    )
    assert not (Path(removed["path"]) / "extra/notes/readme.txt").exists()
    statuses = {entry["path"]: entry["status"] for entry in removed["diff"]["files"]}
    assert statuses["extra/notes/readme.txt"] == "removed"


def test_an_executable_file_stays_executable(starter):
    source = starter / "registry/prompt/hello-prompt/0.1.0"
    (source / "run.sh").write_text("#!/bin/sh\n")
    os.chmod(source / "run.sh", 0o755)
    result = registry.edit_version(PROMPT, starter, sets=[("run.sh", b"#!/bin/sh\necho\n")])
    assert os.stat(Path(result["path"]) / "run.sh").st_mode & 0o111


@pytest.mark.parametrize(
    ("sets", "removes", "code"),
    [
        ([], [], "EDIT_INVALID"),
        ([("manifest.yaml", b"kind: prompt\n")], [], "EDIT_INVALID"),
        ([], ["manifest.yaml"], "EDIT_INVALID"),
        ([("../escape.md", b"x")], [], "ENTRY_UNSAFE"),
        ([("/abs.md", b"x")], [], "ENTRY_UNSAFE"),
        ([("a//b.md", b"x")], [], "ENTRY_UNSAFE"),
        ([("a/./b.md", b"x")], [], "ENTRY_UNSAFE"),
        ([("__pycache__/x.py", b"x")], [], "EDIT_INVALID"),
        ([("x.pyc", b"x")], [], "EDIT_INVALID"),
        ([("prompt.md", b"x")], ["prompt.md"], "EDIT_INVALID"),
        ([], ["missing.md"], "EDIT_INVALID"),
        ([("prompt.md/x", b"x")], [], "EDIT_INVALID"),
        ([("prompt.md", b"You are the hello agent")], [], None),  # filled below
    ],
)
def test_invalid_edits_are_refused_and_write_nothing(starter, sets, removes, code):
    if code is None:  # unchanged bytes
        sets = [
            ("prompt.md", (starter / "registry/prompt/hello-prompt/0.1.0/prompt.md").read_bytes())
        ]
        code = "EDIT_UNCHANGED"
    before = tree(starter)
    with pytest.raises(RegistryError) as error:
        registry.edit_version(PROMPT, starter, sets=sets, removes=removes)
    assert error.value.code == code
    assert tree(starter) == before


def test_setting_a_directory_is_refused(starter):
    registry.edit_version(PROMPT, starter, sets=[("dir/a.md", b"a\n")])
    with pytest.raises(RegistryError) as error:
        registry.edit_version(
            Ref.parse("prompt/hello-prompt@0.1.1"), starter, sets=[("dir", b"x")]
        )
    assert error.value.code == "EDIT_INVALID"


def test_an_oversized_file_is_refused(starter, monkeypatch):
    monkeypatch.setattr(registry, "MAX_BYTES", 1000)
    with pytest.raises(RegistryError) as error:
        registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"x" * 1001)])
    assert error.value.code == "ENTRY_UNSAFE"


def test_a_broken_eval_set_is_refused(starter):
    """An eval edit must still load as `release` loads it (not JSON, missing fields)."""
    for content in (b"not json\n", b'{"id": "x"}\n', b"\n"):
        with pytest.raises(RegistryError) as error:
            registry.edit_version(EVALS, starter, sets=[("golden.jsonl", content)])
        assert error.value.code == "EVAL_INVALID"
    assert not (starter / "registry/eval/hello-evals/0.1.1").exists()


def test_an_eval_case_change_publishes(starter):
    case = {"id": "hours", "turns": ["When?"], "expect": {"must_match": ["10"]}, "reference": "r"}
    result = registry.edit_version(
        EVALS, starter, sets=[("golden.jsonl", (json.dumps(case) + "\n").encode())]
    )
    assert result["ref"] == "eval/hello-evals@0.1.1" and result["runs_code"] is True


def test_the_source_changing_its_declared_ref_while_staged_is_refused(starter, monkeypatch):
    real = registry.copy_and_hash

    def swap(source, destination=None):
        digest = real(source, destination)
        if destination is not None:
            manifest = destination / "manifest.yaml"
            manifest.write_text(manifest.read_text().replace("hello-prompt", "other"))
        return digest

    monkeypatch.setattr(registry, "copy_and_hash", swap)
    with pytest.raises(RegistryError) as error:
        registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"A\n")])
    assert error.value.code == "MANIFEST_INVALID"


# -- CLI ----------------------------------------------------------------------------------------


def test_cli_text_shows_the_diff_then_the_placed_line(starter, tmp_path, capsys):
    new = tmp_path / "prompt.md"
    new.write_text("Be brief.\n")
    code = cli.main(
        [
            "registry",
            "edit",
            str(PROMPT),
            "--set",
            "prompt.md",
            str(new),
            "--project",
            str(starter),
        ]
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert out.index("+Be brief.") < out.index("prompt/hello-prompt@0.1.1 sha256:")
    assert "(from prompt/hello-prompt@0.1.0)" in out
    assert (
        "Next: " in out
        and "agent set-ref prompt/hello-prompt@0.1.0 prompt/hello-prompt@0.1.1" in out
    )
    assert "steers the agent" in err


def test_cli_dry_run_then_expect_digest(starter, tmp_path, capsys):
    new = tmp_path / "prompt.md"
    new.write_text("Be brief.\n")
    base = ["registry", "edit", str(PROMPT), "--set", "prompt.md", str(new)]
    base += ["--project", str(starter)]
    assert cli.main([*base, "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True and "diff" in preview
    assert not (starter / "registry/prompt/hello-prompt/0.1.1").exists()
    assert cli.main([*base, "--dry-run"]) == 0
    text = capsys.readouterr().out
    assert "Would publish" in text and f"--expect-digest {preview['sha256']}" in text
    assert "Next:" not in text
    new.write_text("Changed after review.\n")
    assert cli.main([*base, "--expect-digest", preview["sha256"]]) == 2
    assert "DIGEST_MISMATCH" in capsys.readouterr().err


def test_cli_refuses_fifo_and_missing_inputs(starter, tmp_path, capsys):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    for source in (fifo, tmp_path / "missing", tmp_path):
        code = cli.main(
            [
                "registry",
                "edit",
                str(PROMPT),
                "--set",
                "prompt.md",
                str(source),
                "--project",
                str(starter),
            ]
        )
        assert code == 2
        assert "[EDIT_INVALID]" in capsys.readouterr().err


def test_cli_two_stdin_sets_are_refused(starter, capsys):
    code = cli.main(
        [
            "registry",
            "edit",
            str(PROMPT),
            "--set",
            "a.md",
            "-",
            "--set",
            "b.md",
            "-",
            "--project",
            str(starter),
        ]
    )
    assert code == 2 and "only one --set" in capsys.readouterr().err


def test_cli_bump_and_version_are_exclusive(starter, capsys):
    with pytest.raises(SystemExit):
        cli.main(["registry", "edit", str(PROMPT), "--bump", "minor", "--version", "1.0.0"])


def test_cli_end_to_end_reads_case_text_from_stdin(starter):
    """S-12: free text reaches the CLI by stdin (or a private file), never argv."""
    case = {"id": "hours", "turns": ["When?"], "expect": {}, "reference": "r"}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentbreeder",
            "registry",
            "edit",
            str(EVALS),
            "--set",
            "golden.jsonl",
            "-",
            "--bump",
            "minor",
            "--project",
            str(starter),
            "--json",
        ],
        input=(json.dumps(case) + "\n").encode(),
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["ref"] == "eval/hello-evals@0.2.0"
    assert b"code that runs inside agents" in completed.stderr
    written = (starter / "registry/eval/hello-evals/0.2.0/golden.jsonl").read_bytes()
    assert written == (json.dumps(case) + "\n").encode()


@pytest.mark.parametrize("spelling", ["Manifest.yaml", "MANIFEST.YAML"])
def test_another_spelling_of_the_manifest_is_refused(starter, spelling):
    before = tree(starter)
    with pytest.raises(RegistryError) as error:
        registry.edit_version(PROMPT, starter, sets=[(spelling, b"kind: prompt\n")])
    assert error.value.code == "EDIT_INVALID"
    assert tree(starter) == before


def test_a_lower_version_is_refused(starter):
    registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"A\n")], version="0.2.0")
    with pytest.raises(RegistryError) as error:
        registry.edit_version(
            Ref.parse("prompt/hello-prompt@0.2.0"),
            starter,
            sets=[("prompt.md", b"B\n")],
            version="0.1.5",
        )
    assert error.value.code == "VERSION_INVALID"


def test_removing_the_last_file_drops_its_empty_directories(starter):
    added = registry.edit_version(PROMPT, starter, sets=[("a/b/c.md", b"c\n")])
    removed = registry.edit_version(Ref.parse(added["ref"]), starter, removes=["a/b/c.md"])
    assert not (Path(removed["path"]) / "a").exists()
    assert registry.copy_and_hash(Path(removed["path"])) != registry.copy_and_hash(
        starter / "registry/prompt/hello-prompt/0.1.0"
    )  # only the version line differs from 0.1.0


def test_a_project_registry_also_on_the_path_is_still_the_project(starter, monkeypatch):
    monkeypatch.setenv(registry.PATH_ENV, str(starter / "registry"))
    result = registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"A\n")])
    assert result["root_kind"] == "project"


def test_the_samefile_check_catches_a_spelling_edit_path_lets_through(starter, monkeypatch):
    """Defence in depth for normalising file systems: even if the name check passed, a path
    that is the manifest on disk is refused."""
    probe = starter / "AGENT.YAML"
    if not probe.exists():
        pytest.skip("case-sensitive file system")
    monkeypatch.setattr(registry, "edit_path", lambda text: text)
    with pytest.raises(RegistryError) as error:
        registry.edit_version(PROMPT, starter, sets=[("MANIFEST.yaml", b"kind: prompt\n")])
    assert error.value.code == "EDIT_INVALID"


def test_removing_one_of_two_files_keeps_the_directory(starter):
    added = registry.edit_version(PROMPT, starter, sets=[("d/a.md", b"a\n"), ("d/b.md", b"b\n")])
    removed = registry.edit_version(Ref.parse(added["ref"]), starter, removes=["d/a.md"])
    assert (Path(removed["path"]) / "d/b.md").read_bytes() == b"b\n"


def test_invalid_bump_and_version(starter):
    with pytest.raises(RegistryError) as error:
        registry.bumped("1.0.0", "huge")
    assert error.value.code == "VERSION_INVALID"
    with pytest.raises(RegistryError) as error:
        registry.edit_version(PROMPT, starter, sets=[("prompt.md", b"A\n")], version="1.2")
    assert error.value.code == "VERSION_INVALID"


def test_an_unreadable_eval_set_never_names_the_staging_directory(starter, monkeypatch):
    from agentbreeder import evals

    def unreadable(directory):
        raise PermissionError(13, "Permission denied", str(directory / "golden.jsonl"))

    monkeypatch.setattr(evals, "load_eval_set", unreadable)
    with pytest.raises(RegistryError) as error:
        registry.edit_version(EVALS, starter, sets=[("golden.jsonl", b"{}\n")])
    assert error.value.code == "EVAL_INVALID" and "Permission denied" in str(error.value)
    assert ".import-" not in str(error.value) and "agentbreeder-import" not in str(error.value)


def test_cli_oversized_input_is_refused(starter, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(registry, "MAX_BYTES", 10)
    big = tmp_path / "big.md"
    big.write_bytes(b"x" * 11)
    base = ["registry", "edit", str(PROMPT), "--project", str(starter), "--set", "prompt.md"]
    assert cli.main([*base, str(big)]) == 2
    assert "[ENTRY_UNSAFE]" in capsys.readouterr().err

    class Stdin:
        buffer = __import__("io").BytesIO(b"y" * 11)

    monkeypatch.setattr(sys, "stdin", Stdin)
    assert cli.main([*base, "-"]) == 2
    assert "[ENTRY_UNSAFE]" in capsys.readouterr().err
