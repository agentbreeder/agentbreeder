"""Registry browser reads: `registry show`, `diff` and `used-by` (#824, M8; S-27)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentbreeder import cli, registry
from agentbreeder.registry import Ref, RegistryError

PACKAGE = Path(registry.__file__).parent
NORTHWIND = PACKAGE / "examples" / "northwind"
BIDI = "‮"
ESC = "\x1b"


def make_version(root: Path, kind: str, name: str, version: str, files: dict) -> Path:
    path = root / kind / name / version
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text(
        f"kind: {kind}\nname: {name}\nversion: {version}\ndescription: test {name}\n"
    )
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")  # isolate from shipped
    project = tmp_path / "project"
    make_version(project / "registry", "prompt", "support", "1.0.0", {"prompt.md": "Be kind.\n"})
    return project


def _cli(*args: str, cwd: Path | None = None, env: dict | None = None):
    return subprocess.run(
        [sys.executable, "-m", "agentbreeder", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        timeout=120,
    )


# -- show ----------------------------------------------------------------------------------------


def test_show_returns_manifest_files_and_hash(project):
    view = registry.show(Ref.parse("prompt/support@1.0.0"), registry.roots(project), project)
    path = project / "registry/prompt/support/1.0.0"
    assert view["ref"] == "prompt/support@1.0.0"
    assert view["sha256"] == registry.copy_and_hash(path)
    assert view["root_kind"] == "project"
    assert view["description"] == "test support"
    assert view["manifest"]["version"] == "1.0.0"
    assert [f["path"] for f in view["files"]] == ["manifest.yaml", "prompt.md"]
    prompt = view["files"][1]
    assert prompt["text"] == "Be kind.\n"
    assert prompt["size"] == 9 and not prompt["binary"] and not prompt["stripped"]
    assert not view["omitted"]


def test_show_root_kinds(project, tmp_path, monkeypatch):
    extra = tmp_path / "shared"
    make_version(extra, "tool", "orders", "1.0.0", {"src/orders.py": "x = 1\n"})
    make_version(registry.BUILTIN_ROOT, "runtime", "adk", "1.0.0", {"Dockerfile": "FROM x\n"})
    monkeypatch.setenv(registry.PATH_ENV, str(extra))
    search = registry.roots(project)
    assert registry.show(Ref.parse("tool/orders@1.0.0"), search, project)["root_kind"] == "path"
    runtime = registry.show(Ref.parse("runtime/adk@1.0.0"), search, project)
    assert runtime["root_kind"] == "builtin"
    tool = registry.show(Ref.parse("tool/orders@1.0.0"), search, project)
    assert {"path": "src", "type": "dir", "stripped": False} in tool["files"]


def test_show_strips_control_and_bidi_in_names_content_and_manifest(project):
    version = make_version(
        project / "registry",
        "prompt",
        "tricky",
        "1.0.0",
        {
            f"evil{BIDI}txt.md": f"safe {ESC}[31mred{ESC}[0m {BIDI}reversed\ttab\nline\r\n",
            f"bell\x07{ESC}]0;title\x07.md": "ok\n",
        },
    )
    (version / "manifest.yaml").write_text(
        "kind: prompt\nname: tricky\nversion: 1.0.0\n"
        'description: "a\\u202eb\\x1b[2Jc"\nextra: {"k\\u2066": "v\\u2069"}\n'
    )
    view = registry.show(Ref.parse("prompt/tricky@1.0.0"), registry.roots(project), project)
    assert view["description"] == "ab[2Jc"
    assert view["manifest"]["extra"] == {"k": "v"}
    names = {f["path"]: f for f in view["files"]}
    assert set(names) == {"manifest.yaml", "eviltxt.md", "bell]0;title.md"}
    assert names["eviltxt.md"]["text"] == "safe [31mred[0m reversed\ttab\nline\n"
    assert names["eviltxt.md"]["stripped"] and names["bell]0;title.md"]["stripped"]
    dumped = json.dumps(view, ensure_ascii=False)
    for bad in (ESC, BIDI, "\x07", "\r", "⁦", "⁩"):
        assert bad not in dumped


def test_clean_keeps_newlines_only_in_text():
    assert registry.clean("a\nb\tc\x85d‏e") == ("a\nb\tcde", True)
    assert registry.clean("a\nb\tc", name=True) == ("abc", True)
    assert registry.clean("plain") == ("plain", False)


def test_show_binary_truncated_and_omitted(project, monkeypatch):
    make_version(
        project / "registry",
        "knowledge",
        "docs",
        "1.0.0",
        {
            "a.bin": b"\x00\x01\x02",
            "b.md": "é" * 10,  # 20 bytes of two-byte characters
            "c.md": "x" * 8,
            "0.bin": b"\xff\xfe not utf-8",
        },
    )
    monkeypatch.setattr(registry, "SHOW_FILE_LIMIT", 9)  # cuts one "é" in half
    monkeypatch.setattr(registry, "SHOW_TOTAL_LIMIT", 9 + 3 + 9)  # 0.bin, a.bin, b.md
    view = registry.show(Ref.parse("knowledge/docs@1.0.0"), registry.roots(project), project)
    files = {f["path"]: f for f in view["files"]}
    assert files["a.bin"]["binary"] and files["a.bin"]["text"] is None
    assert files["b.md"]["truncated"] and files["b.md"]["text"] == "é" * 4
    assert files["c.md"]["omitted"] and files["c.md"]["text"] is None
    assert files["0.bin"]["binary"]
    assert view["omitted"]
    assert files["b.md"]["size"] == 20


@pytest.mark.parametrize(
    "plant",
    [
        lambda v, tmp: os.symlink("/etc/hosts", v / "link"),
        lambda v, tmp: os.link(v / "prompt.md", tmp / "outside-hardlink"),
        lambda v, tmp: os.mkfifo(v / "fifo"),
    ],
    ids=["symlink", "hardlink", "fifo"],
)
def test_show_refuses_unsafe_entries(project, tmp_path, plant):
    plant(project / "registry/prompt/support/1.0.0", tmp_path)
    with pytest.raises(RegistryError) as raised:
        registry.show(Ref.parse("prompt/support@1.0.0"), registry.roots(project), project)
    assert raised.value.code == "ENTRY_UNSAFE"


def test_show_refuses_symlinked_version_and_oversize(project, tmp_path, monkeypatch):
    real = make_version(tmp_path / "elsewhere", "tool", "x", "1.0.0", {"a.py": ""})
    (project / "registry/tool/x").mkdir(parents=True)
    os.symlink(real, project / "registry/tool/x/1.0.0")
    with pytest.raises(RegistryError, match="symlink"):
        registry.show(Ref.parse("tool/x@1.0.0"), registry.roots(project), project)
    monkeypatch.setattr(registry, "MAX_FILES", 1)
    with pytest.raises(RegistryError, match="too large"):
        registry.show(Ref.parse("prompt/support@1.0.0"), registry.roots(project), project)


def test_read_refuses_a_file_swapped_after_the_check(project):
    version = project / "registry/prompt/support/1.0.0"
    info = (version / "manifest.yaml").lstat()  # a different inode than prompt.md
    with pytest.raises(RegistryError, match="changed while reading"):
        registry._read_one("prompt.md", version / "prompt.md", info)


def test_show_conflict_and_bad_refs(project, tmp_path, monkeypatch):
    extra = tmp_path / "shared"
    make_version(extra, "prompt", "support", "1.0.0", {"prompt.md": "Different.\n"})
    monkeypatch.setenv(registry.PATH_ENV, str(extra))
    with pytest.raises(RegistryError) as raised:
        registry.show(Ref.parse("prompt/support@1.0.0"), registry.roots(project), project)
    assert raised.value.code == "REF_CONFLICT"
    with pytest.raises(RegistryError) as raised:
        registry.show(Ref.parse("prompt/nope@1.0.0"), registry.roots(project), project)
    assert raised.value.code == "REF_NOT_FOUND"


def test_cli_show_text_and_errors(project, capsys, monkeypatch):
    monkeypatch.chdir(project)
    assert cli.main(["registry", "show", "prompt/support@1.0.0"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("prompt/support@1.0.0  sha256:")
    assert "--- prompt.md ---\nBe kind." in out and "Root: registry (project)" in out
    assert cli.main(["registry", "show", "prompt/support"]) == 2
    assert "REF_UNPINNED" in capsys.readouterr().err
    assert cli.main(["registry", "show", "prompt/../x@1.0.0"]) == 2
    assert "REF_INVALID" in capsys.readouterr().err


def test_cli_show_notes(project, capsys, monkeypatch):
    make_version(
        project / "registry",
        "tool",
        "t",
        "1.0.0",
        {"run.sh": "echo hi\n", "blob": b"\x00", f"n{BIDI}.md": "x\n"},
    )
    (project / "registry/tool/t/1.0.0/run.sh").chmod(0o755)
    monkeypatch.chdir(project)
    assert cli.main(["registry", "show", "tool/t@1.0.0"]) == 0
    out = capsys.readouterr().out
    assert "run.sh  8 B  (executable)" in out
    assert "blob  1 B  (binary, not shown)" in out
    assert "n.md  2 B  (control or bidi characters removed)" in out


# -- end to end ------------------------------------------------------------------------------


def test_e2e_show_strips_terminal_escapes(project, tmp_path):
    make_version(
        project / "registry",
        "prompt",
        "evil",
        "1.0.0",
        {f"x{ESC}[2J.md": f"{ESC}]52;c;aGk=\x07 hi {BIDI}\n"},
    )
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    done = _cli("registry", "show", "prompt/evil@1.0.0", cwd=project)
    assert done.returncode == 0, done.stderr
    assert ESC not in done.stdout and BIDI not in done.stdout and "\x07" not in done.stdout
    assert "x[2J.md" in done.stdout
    as_json = _cli("registry", "show", "prompt/evil@1.0.0", "--json", cwd=project)
    assert as_json.returncode == 0
    view = json.loads(as_json.stdout)
    assert [f["path"] for f in view["files"]] == ["manifest.yaml", "x[2J.md"]


def test_e2e_northwind_and_every_builtin_listed_and_shown():
    """Acceptance (part): Northwind's prompt, eval, tool, knowledge and service versions and
    every built-in are listed, and each one opens."""
    listed = _cli("registry", "list", "--project", str(NORTHWIND), "--json")
    assert listed.returncode == 0, listed.stderr
    rows = json.loads(listed.stdout)
    refs = {row["ref"] for row in rows}
    assert {"prompt/northwind-support@1.0.0", "prompt/northwind-support@1.0.1"} <= refs
    assert {"eval/northwind-golden@1.0.1", "knowledge/northwind-help@1.0.0"} <= refs
    assert {"tool/orders@1.0.0", "service/northwind-ops@1.0.0"} <= refs
    builtins = {
        f"{p.parent.parent.name}/{p.parent.name}@{p.name}"
        for p in registry.BUILTIN_ROOT.glob("*/*/*")
        if p.is_dir()
    }
    assert builtins and builtins <= refs
    search = registry.roots(NORTHWIND)
    for ref in sorted(refs):
        view = registry.show(Ref.parse(ref), search, NORTHWIND)
        assert view["ref"] == ref and view["sha256"].startswith("sha256:")
    shown = _cli(
        "registry", "show", "prompt/northwind-support@1.0.1", "--project", str(NORTHWIND), "--json"
    )
    assert shown.returncode == 0, shown.stderr
    view = json.loads(shown.stdout)
    assert view["root_kind"] == "project"
    lock = json.loads((NORTHWIND / "agent.lock").read_text())["entries"]
    assert view["sha256"] == lock["prompt/northwind-support@1.0.1"]["sha256"]


# -- diff ----------------------------------------------------------------------------------------


def _gnu_hunks(before: Path, after: Path, context: int = 3) -> str:
    """`diff -u` without its two header lines."""
    done = subprocess.run(
        ["diff", f"-U{context}", str(before), str(after)], capture_output=True, text=True
    )
    assert done.returncode in (0, 1), done.stderr
    return "".join(done.stdout.splitlines(keepends=True)[2:])


def _our_hunks(result: dict, path: str) -> str:
    change = next(c for c in result["files"] if c["path"] == path)
    text = registry.unified({**result, "files": [change]})
    return "".join(text.splitlines(keepends=True)[2:])


needs_diff = pytest.mark.skipif(shutil.which("diff") is None, reason="needs diff(1)")


@pytest.fixture
def versions(project):
    root = project / "registry"
    lines = [f"line {n}\n" for n in range(1, 30)]
    changed = list(lines)
    changed[4] = "line five\n"
    changed[20:22] = ["new a\n", "new b\n", "new c\n"]
    make_version(
        root,
        "tool",
        "t",
        "1.0.0",
        {
            "a.txt": "".join(lines),
            "gone.txt": "bye\n",
            "same.txt": "same\n",
            "tail.txt": "x\nno newline",
            "blob.bin": b"\x00\x01",
            "run.sh": "echo\n",
            "kind": "a file first\n",
        },
    )
    make_version(
        root,
        "tool",
        "t",
        "1.0.1",
        {
            "a.txt": "".join(changed),
            "new.txt": "hello\n",
            "same.txt": "same\n",
            "tail.txt": "x\nno newline either",
            "blob.bin": b"\x00\x02",
            "run.sh": "echo\n",
            "kind/inside.txt": "now a directory\n",
            "empty/.keep": "",
        },
    )
    (root / "tool/t/1.0.1/empty/.keep").unlink()  # leaves an empty directory
    (root / "tool/t/1.0.1/run.sh").chmod(0o755)
    return root / "tool/t"


@needs_diff
def test_diff_matches_diff_u_with_context(project, versions):
    search = registry.roots(project)
    for context in (0, 1, 3, 10):
        result = registry.diff(
            Ref.parse("tool/t@1.0.0"), Ref.parse("tool/t@1.0.1"), search, context, project
        )
        for name in ("a.txt", "tail.txt", "manifest.yaml"):
            expected = _gnu_hunks(versions / "1.0.0" / name, versions / "1.0.1" / name, context)
            assert _our_hunks(result, name) == expected, (name, context)


def test_diff_statuses_and_shape(project, versions):
    result = registry.diff(
        Ref.parse("tool/t@1.0.0"), Ref.parse("tool/t@1.0.1"), registry.roots(project)
    )
    assert result["from"]["ref"] == "tool/t@1.0.0" and result["to"]["ref"] == "tool/t@1.0.1"
    assert result["from"]["root_kind"] == "path"  # no project_dir given
    assert not result["identical"] and result["context"] == 3
    changes = [(c["path"], c["status"], c["type"]) for c in result["files"]]
    assert changes == [
        ("a.txt", "modified", "file"),
        ("blob.bin", "modified", "file"),
        ("empty", "added", "dir"),
        ("gone.txt", "removed", "file"),
        ("kind", "removed", "file"),  # a type change: removed, then added
        ("kind", "added", "dir"),
        ("kind/inside.txt", "added", "file"),
        ("manifest.yaml", "modified", "file"),
        ("new.txt", "added", "file"),
        ("run.sh", "modified", "file"),
        ("tail.txt", "modified", "file"),
    ]
    by_path = {c["path"]: c for c in result["files"]}
    assert by_path["blob.bin"]["binary"] and by_path["blob.bin"]["hunks"] == []
    assert by_path["run.sh"]["executable"] == {"from": False, "to": True}
    assert by_path["run.sh"]["hunks"] == []
    assert by_path["new.txt"]["from_sha256"] is None
    assert by_path["new.txt"]["hunks"][0]["lines"] == [{"op": "+", "text": "hello"}]
    tail = by_path["tail.txt"]["hunks"][0]["lines"]
    assert {"op": "-", "text": "no newline", "no_newline": True} in tail
    hunk = by_path["a.txt"]["hunks"][0]
    assert [hunk[k] for k in ("from_start", "from_count", "to_start", "to_count")] == [2, 7, 2, 7]
    text = registry.unified(result)
    assert "Binary files tool/t@1.0.0/blob.bin and tool/t@1.0.1/blob.bin differ" in text
    assert "old mode 100644\nnew mode 100755" in text
    assert "Only in tool/t@1.0.1: empty/" in text
    assert "--- /dev/null\n+++ tool/t@1.0.1/new.txt\n@@ -0,0 +1 @@\n+hello" in text
    assert "--- tool/t@1.0.0/gone.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye" in text
    assert "same.txt" not in text


def test_diff_same_version_is_identical(project):
    ref = Ref.parse("prompt/support@1.0.0")
    result = registry.diff(ref, ref, registry.roots(project), project_dir=project)
    assert result["identical"] and result["files"] == []
    assert registry.unified(result) == ""


def test_diff_is_ordered(project, versions):
    search = registry.roots(project)
    forward = registry.diff(Ref.parse("tool/t@1.0.0"), Ref.parse("tool/t@1.0.1"), search)
    back = registry.diff(Ref.parse("tool/t@1.0.1"), Ref.parse("tool/t@1.0.0"), search)
    assert {c["path"]: c["status"] for c in back["files"]}["new.txt"] == "removed"

    def texts(result, op):
        lines = result["files"][0]["hunks"][0]["lines"]
        return [line["text"] for line in lines if line["op"] == op]

    assert texts(forward, "-") == texts(back, "+") == ["line 5"]
    assert texts(forward, "+") == texts(back, "-") == ["line five"]


def test_diff_flags_changes_hidden_by_stripping_and_caps(project, monkeypatch):
    root = project / "registry"
    make_version(root, "prompt", "p", "1.0.0", {"p.md": "pay alice\n", "big.md": "a" * 40})
    make_version(root, "prompt", "p", "1.0.1", {"p.md": f"pay {BIDI}alice\n", "big.md": "b" * 40})
    monkeypatch.setattr(registry, "SHOW_FILE_LIMIT", 32)
    result = registry.diff(
        Ref.parse("prompt/p@1.0.0"), Ref.parse("prompt/p@1.0.1"), registry.roots(project)
    )
    by_path = {c["path"]: c for c in result["files"]}
    assert by_path["p.md"]["stripped"] and by_path["p.md"]["hunks"] == []
    assert by_path["big.md"]["truncated"] and by_path["big.md"]["hunks"] == []
    text = registry.unified(result)
    assert "p.md differ only in removed control or bidi characters" in text
    assert "big.md differ (too large to show)" in text
    assert BIDI not in json.dumps(result, ensure_ascii=False)


def test_diff_refuses_bad_input(project):
    search = registry.roots(project)
    ref = Ref.parse("prompt/support@1.0.0")
    with pytest.raises(RegistryError) as raised:
        registry.diff(ref, Ref.parse("prompt/x@1.0.0"), search)
    assert raised.value.code == "REF_NOT_FOUND"
    with pytest.raises(RegistryError) as raised:
        registry.diff(ref, ref, search, context=-1)
    assert raised.value.code == "DIFF_INVALID"


def test_cli_diff(project, versions, capsys, monkeypatch):
    monkeypatch.chdir(project)
    assert cli.main(["registry", "diff", "tool/t@1.0.0", "tool/t@1.0.1", "--context", "0"]) == 0
    assert "@@ -5 +5 @@\n-line 5\n+line five\n" in capsys.readouterr().out
    assert cli.main(["registry", "diff", "tool/t@1.0.0", "tool/t@1.0.1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["to"]["root_kind"] == "project"
    assert cli.main(["registry", "diff", "tool/t@1.0.0", "tool/t"]) == 2
    assert "REF_UNPINNED" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["registry", "diff", "tool/t@1.0.0", "tool/t@1.0.1", "--context", "x"])
    assert "must be 0 to 1000" in capsys.readouterr().err


@needs_diff
def test_e2e_northwind_prompt_diff_equals_diff_of_the_files():
    """Acceptance: the diff of prompt/northwind-support 1.0.0 -> 1.0.1 equals `diff` of the
    files."""
    args = ["prompt/northwind-support@1.0.0", "prompt/northwind-support@1.0.1"]
    done = _cli("registry", "diff", *args, "--project", str(NORTHWIND))
    assert done.returncode == 0, done.stderr
    versions = NORTHWIND / "registry/prompt/northwind-support"
    expected = ""
    for name in ("manifest.yaml", "prompt.md"):
        expected += f"--- prompt/northwind-support@1.0.0/{name}\n"
        expected += f"+++ prompt/northwind-support@1.0.1/{name}\n"
        expected += _gnu_hunks(versions / "1.0.0" / name, versions / "1.0.1" / name)
    assert done.stdout == expected
    as_json = _cli("registry", "diff", *args, "--project", str(NORTHWIND), "--json")
    result = json.loads(as_json.stdout)
    assert [c["path"] for c in result["files"]] == ["manifest.yaml", "prompt.md"]
