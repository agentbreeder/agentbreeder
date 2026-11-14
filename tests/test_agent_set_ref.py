"""`agent set-ref OLD NEW [--dry-run | --expect-base SHA256]` (#826, M10; console §8.4)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from agentbreeder import cli, newagent, registry, setref
from agentbreeder.agentfile import load_agent_file
from agentbreeder.registry import Ref, RegistryError

OLD = "prompt/hello-prompt@0.1.0"
NEW = "prompt/hello-prompt@0.1.1"


@pytest.fixture(autouse=True)
def no_registry_path(monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)


@pytest.fixture
def starter(tmp_path) -> Path:
    """The starter project with prompt 0.1.1 published by `registry edit`."""
    plan = newagent.plan("hello", "gemini", tmp_path / "hello", today=date(2026, 11, 13))
    project = newagent.create(plan).directory
    registry.edit_version(Ref.parse(OLD), project, sets=[("prompt.md", b"Be brief.\n")])
    return project


def test_moves_the_prompt_and_keeps_every_other_byte(starter):
    path = starter / "agent.yaml"
    before = path.read_text()
    os.chmod(path, 0o640)
    result = setref.set_ref(starter, OLD, NEW)
    after = path.read_text()
    assert after == before.replace(f"prompt: {{ref: {OLD}}}", f"prompt: {{ref: {NEW}}}")
    assert load_agent_file(path).prompt.ref == NEW
    assert os.stat(path).st_mode & 0o777 == 0o640
    assert result["at"] == "prompt.ref" and result["old"] == OLD and result["new"] == NEW
    assert result["base_sha256"] == setref.digest(before.encode())
    assert result["sha256"] == setref.digest(after.encode())
    assert f"-prompt: {{ref: {OLD}}}" in result["diff"]
    assert f"+prompt: {{ref: {NEW}}}" in result["diff"]
    assert result["file"] == str(path.resolve()) and result["dry_run"] is False
    assert [p.name for p in starter.glob(".agent.yaml-*")] == []


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("\ufeff", None),  # a BOM before the first line
        ("crlf", None),
        ("quoted", None),
    ],
)
def test_bom_crlf_and_quoted_refs_are_kept(starter, text, kept):
    path = starter / "agent.yaml"
    original = path.read_text()
    if text == "crlf":
        source = original.replace("\n", "\r\n")
    elif text == "quoted":
        source = original.replace(f"prompt: {{ref: {OLD}}}", f'prompt: {{ref: "{OLD}"}}')
    else:
        source = text + original
    path.write_bytes(source.encode())
    setref.set_ref(starter, OLD, NEW)
    assert path.read_bytes() == source.replace(OLD, NEW).encode()


def test_a_ref_written_with_escapes_is_refused(starter):
    path = starter / "agent.yaml"
    escaped = OLD.replace("/", "\\/")
    path.write_text(
        path.read_text().replace(f"prompt: {{ref: {OLD}}}", f'prompt: {{ref: "{escaped}"}}')
    )
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, OLD, NEW)
    assert error.value.code == "REF_AMBIGUOUS" and "cannot replace exactly" in str(error.value)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_project_is_write_failed_not_a_traceback(starter, capsys):
    before = (starter / "agent.yaml").read_bytes()
    os.chmod(starter, 0o500)
    try:
        assert cli.main(["agent", "set-ref", OLD, NEW, "--project", str(starter)]) == 2
        assert "[WRITE_FAILED]" in capsys.readouterr().err
    finally:
        os.chmod(starter, 0o755)
    assert (starter / "agent.yaml").read_bytes() == before
    assert list(starter.glob(".agent.yaml-*")) == []


def test_a_failed_replace_leaves_no_temp_file(starter, monkeypatch):
    def failing(source, target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(setref.os, "replace", failing)
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, OLD, NEW)
    assert error.value.code == "WRITE_FAILED" and "No space left" in str(error.value)
    assert list(starter.glob(".agent.yaml-*")) == []


def test_dry_run_then_expect_base(starter):
    path = starter / "agent.yaml"
    before = path.read_bytes()
    preview = setref.set_ref(starter, OLD, NEW, dry_run=True)
    assert path.read_bytes() == before and preview["dry_run"] is True
    setref.set_ref(starter, OLD, NEW, expect_base=preview["base_sha256"])
    assert setref.digest(path.read_bytes()) == preview["sha256"]


def test_a_changed_file_fails_expect_base(starter):
    path = starter / "agent.yaml"
    preview = setref.set_ref(starter, OLD, NEW, dry_run=True)
    path.write_text(path.read_text() + "# a later edit\n")
    changed = path.read_bytes()
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, OLD, NEW, expect_base=preview["base_sha256"])
    assert error.value.code == "BASE_MISMATCH"
    assert path.read_bytes() == changed


def test_a_change_during_the_write_is_refused(starter, monkeypatch):
    path = starter / "agent.yaml"
    real = setref.read_project_file
    calls = []

    def racing(target, limit):
        calls.append(target)
        if len(calls) == 2:  # the re-check just before the rename
            target.write_text(target.read_text() + "# concurrent\n")
        return real(target, limit)

    monkeypatch.setattr(setref, "read_project_file", racing)
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, OLD, NEW)
    assert error.value.code == "BASE_MISMATCH"
    assert path.read_text().endswith("# concurrent\n") and OLD in path.read_text()
    assert [p.name for p in starter.glob(".agent.yaml-*")] == []


def test_moving_to_a_forked_name(starter):
    registry.fork_version(Ref.parse(OLD), starter, name="hello-terse")
    setref.set_ref(starter, OLD, "prompt/hello-terse@0.1.0")
    assert load_agent_file(starter / "agent.yaml").prompt.ref == "prompt/hello-terse@0.1.0"


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        (OLD, OLD, "REF_UNCHANGED"),
        (OLD, "eval/hello-evals@0.1.0", "REF_INVALID"),
        ("prompt/other@1.0.0", NEW, "REF_NOT_DECLARED"),
        (OLD, "prompt/hello-prompt@9.9.9", "REF_NOT_FOUND"),
        ("prompt/hello-prompt", NEW, "REF_UNPINNED"),
        (OLD, "not a ref", "REF_INVALID"),
    ],
)
def test_refusals_write_nothing(starter, old, new, code):
    before = (starter / "agent.yaml").read_bytes()
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, old, new)
    assert error.value.code == code
    assert (starter / "agent.yaml").read_bytes() == before


def test_a_second_occurrence_is_ambiguous(starter):
    path = starter / "agent.yaml"
    path.write_text(path.read_text() + f"# was {OLD}\n")
    before = path.read_bytes()
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, OLD, NEW)
    assert error.value.code == "REF_AMBIGUOUS" and "2 times" in str(error.value)
    assert path.read_bytes() == before


def test_a_longer_ref_is_not_a_match(starter):
    """`prompt/hello-prompt@0.1.0` inside `…@0.1.01` or `…-prompt@0.1.0x` is not an occurrence."""
    path = starter / "agent.yaml"
    path.write_text(path.read_text() + f"# see x{OLD} and {OLD}0 and {OLD}.1\n")
    setref.set_ref(starter, OLD, NEW)
    text = path.read_text()
    assert f"prompt: {{ref: {NEW}}}" in text and f"x{OLD} and {OLD}0 and {OLD}.1" in text


def test_a_ref_only_in_a_comment_is_not_declared(starter):
    path = starter / "agent.yaml"
    path.write_text(path.read_text() + "# prompt/old-prompt@1.0.0\n")
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, "prompt/old-prompt@1.0.0", NEW)
    assert error.value.code == "REF_NOT_DECLARED"


def test_a_description_mentioning_the_ref_is_ambiguous(starter):
    path = starter / "agent.yaml"
    text = path.read_text().replace("kind: agent\n", f"kind: agent\ndescription: uses {OLD}\n")
    path.write_text(text)
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, OLD, NEW)
    assert error.value.code == "REF_AMBIGUOUS"


def test_a_new_ref_already_declared_by_name_is_refused(starter):
    """Moving one tool onto a name the agent already uses would declare it twice."""
    path = starter / "agent.yaml"
    path.write_text(path.read_text() + "tools: [{ref: tool/a@1.0.0}, {ref: tool/b@1.0.0}]\n")
    before = path.read_bytes()
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, "tool/a@1.0.0", "tool/b@2.0.0")
    assert error.value.code == "REF_DUPLICATE" and "tools[1].ref" in str(error.value)
    assert path.read_bytes() == before


@pytest.mark.parametrize("content", [b"\xff\xfe", b"kind: agent\n", b"name: x\n", b"[\n"])
def test_an_invalid_agent_file_is_refused(starter, content):
    (starter / "agent.yaml").write_bytes(content)
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, OLD, NEW)
    assert error.value.code == "AGENT_INVALID"


def test_a_missing_or_linked_agent_file(starter, tmp_path):
    path = starter / "agent.yaml"
    target = tmp_path / "elsewhere.yaml"
    path.rename(target)
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, OLD, NEW)
    assert error.value.code == "AGENT_NOT_FOUND"
    path.symlink_to(target)
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, OLD, NEW)
    assert error.value.code == "ENTRY_UNSAFE"
    assert OLD in target.read_text()


def test_an_invalid_expect_base(starter):
    with pytest.raises(RegistryError) as error:
        setref.set_ref(starter, OLD, NEW, expect_base="sha256:abc")
    assert error.value.code == "DIGEST_INVALID"


# -- CLI ----------------------------------------------------------------------------------------


def test_cli_prints_the_diff_first_then_next(starter, capsys):
    assert cli.main(["agent", "set-ref", OLD, NEW, "--project", str(starter)]) == 0
    out = capsys.readouterr().out
    assert out.index(f"+prompt: {{ref: {NEW}}}") < out.index("Set prompt.ref to")
    assert "Next: " in out and " next " in out


def test_cli_dry_run_and_json(starter, capsys):
    before = (starter / "agent.yaml").read_bytes()
    assert cli.main(["agent", "set-ref", OLD, NEW, "--project", str(starter), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out and "--expect-base sha256:" in out
    assert "Next:" not in out
    args = ["agent", "set-ref", OLD, NEW, "--project", str(starter), "--json"]
    assert cli.main([*args, "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert (starter / "agent.yaml").read_bytes() == before
    assert cli.main([*args, "--expect-base", preview["base_sha256"]]) == 0
    written = json.loads(capsys.readouterr().out)
    assert written["sha256"] == preview["sha256"] and written["dry_run"] is False


def test_cli_errors_exit_2(starter, capsys):
    assert cli.main(["agent", "set-ref", OLD, "prompt/x@1.0.0", "--project", str(starter)]) == 2
    assert "[REF_NOT_FOUND]" in capsys.readouterr().err


def test_cli_bad_expect_base_is_a_usage_error(starter):
    with pytest.raises(SystemExit) as raised:
        cli.main(["agent", "set-ref", OLD, NEW, "--expect-base", "nope"])
    assert raised.value.code == 2


def test_cli_end_to_end(starter):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentbreeder",
            "agent",
            "set-ref",
            OLD,
            NEW,
            "--project",
            str(starter),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["new"] == NEW
    assert load_agent_file(starter / "agent.yaml").prompt.ref == NEW
