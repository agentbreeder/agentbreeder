"""Registry browser reads: `registry show`, `diff` and `used-by` (#824, M8; S-27)."""

from __future__ import annotations

import json
import os
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
