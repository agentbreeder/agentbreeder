"""Registry browser reads: `registry show`, `diff` and `used-by` (#824, M8; S-27)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentbreeder import cli, registry
from agentbreeder.registry import Ref, RegistryError

PACKAGE = Path(registry.__file__).parent
NORTHWIND = PACKAGE / "examples" / "northwind"
BIDI = chr(0x202E)
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
    for bad in (ESC, BIDI, "\x07", "\r", chr(0x2066), chr(0x2069)):
        assert bad not in dumped


def test_clean_keeps_newlines_only_in_text():
    assert registry.clean("a\nb\tc\x85d" + chr(0x200F) + "e") == ("a\nb\tcde", True)
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
        registry._read_one(f"prompt{ESC}.md", version / "prompt.md", info, hashlib.sha256(), 10)
    with pytest.raises(RegistryError) as raised:
        registry._read_one(f"x{ESC}[2J{BIDI}", version / "prompt.md", info, hashlib.sha256(), 10)
    assert ESC not in str(raised.value) and BIDI not in str(raised.value)


def test_shown_hash_is_the_hash_of_the_bytes_read(project):
    """The hash `show` prints comes from the same read as the content, framed exactly as
    `copy_and_hash` (so it equals `verify` and `agent.lock` hashes)."""
    version = make_version(
        project / "registry",
        "tool",
        "h",
        "1.0.0",
        {"run.sh": "echo\n", "src/a.py": "x = 1\n", "empty.txt": "", "b.bin": b"\x00" * 9},
    )
    (version / "run.sh").chmod(0o755)
    (version / "docs").mkdir()
    search = registry.roots(project)
    for text in ("tool/h@1.0.0", "prompt/support@1.0.0"):
        ref = Ref.parse(text)
        hit = registry.resolve(ref, search)
        assert registry.show(ref, search)["sha256"] == registry.copy_and_hash(hit.path)
        assert registry._read_version(hit.path)[1] == registry.copy_and_hash(hit.path)


def test_manifest_shown_is_parsed_from_the_bytes_read(project, monkeypatch):
    """`show` checks and returns the manifest from the same read, not a second open."""
    texts = []
    real = registry.read_manifest

    def spy(version_dir, ref=None, text=None):
        texts.append(text)
        return real(version_dir, ref, text)

    monkeypatch.setattr(registry, "read_manifest", spy)
    view = registry.show(Ref.parse("prompt/support@1.0.0"), registry.roots(project), project)
    manifest = (project / "registry/prompt/support/1.0.0/manifest.yaml").read_text()
    assert view["manifest"]["name"] == "support" and texts[-1] == manifest
    monkeypatch.setattr(registry, "read_manifest", real)
    big = make_version(project / "registry", "prompt", "big", "1.0.0", {})
    (big / "manifest.yaml").write_text(
        "kind: prompt\nname: big\nversion: 1.0.0\n# " + "x" * (64 * 1024) + "\n"
    )
    with pytest.raises(RegistryError, match="64 KiB"):
        registry.show(Ref.parse("prompt/big@1.0.0"), registry.roots(project), project)


# -- error messages are cleaned too (S-27) ---------------------------------------------------

EVIL = f"e{ESC}[2J{BIDI}\x07"


@pytest.mark.parametrize(
    "plant",
    [
        lambda v, tmp: os.symlink("/etc/hosts", v / EVIL),
        lambda v, tmp: os.link(v / "prompt.md", v / EVIL),
        lambda v, tmp: os.mkfifo(v / EVIL),
    ],
    ids=["symlink", "hardlink", "fifo"],
)
def test_errors_never_carry_escapes_from_names(project, tmp_path, plant, capsys, monkeypatch):
    plant(project / "registry/prompt/support/1.0.0", tmp_path)
    monkeypatch.chdir(project)
    for argv in (
        ["registry", "show", "prompt/support@1.0.0"],
        ["registry", "diff", "prompt/support@1.0.0", "prompt/support@1.0.0"],
        ["registry", "verify"],
    ):
        assert cli.main(argv) == 2
        captured = capsys.readouterr()
        for bad in (ESC, BIDI, "\x07"):
            assert bad not in captured.err and bad not in captured.out
        assert "e[2J" in captured.err


def test_resolve_and_manifest_errors_are_cleaned(project, tmp_path, monkeypatch):
    evil_root = tmp_path / f"root{ESC}]0;x\x07{BIDI}"
    evil_root.mkdir()
    monkeypatch.setenv(registry.PATH_ENV, str(evil_root))
    search = registry.roots(project)
    with pytest.raises(RegistryError) as not_found:
        registry.show(Ref.parse("prompt/none@1.0.0"), search, project)
    (evil_root / "prompt/support").mkdir(parents=True)
    os.symlink(project / "registry/prompt/support/1.0.0", evil_root / "prompt/support/1.0.0")
    with pytest.raises(RegistryError) as symlinked:
        registry.show(Ref.parse("prompt/support@1.0.0"), search, project)
    bad = make_version(evil_root, "prompt", "bad", "1.0.0", {})
    (bad / "manifest.yaml").write_text(f"kind: prompt\nname: bad\nversion: 1.0.0\n{ESC}: x\n")
    with pytest.raises(RegistryError) as unparsable:
        registry.show(Ref.parse("prompt/bad@1.0.0"), search, project)
    wrong = make_version(evil_root, "prompt", "wrong", "1.0.0", {})
    (wrong / "manifest.yaml").write_text("kind: prompt\nname: other\nversion: 1.0.0\n")
    with pytest.raises(RegistryError) as mismatched:
        registry.show(Ref.parse("prompt/wrong@1.0.0"), search, project)
    for raised in (not_found, symlinked, unparsable, mismatched):
        message = str(raised.value)
        assert ESC not in message and BIDI not in message and "\x07" not in message, message
    assert "root]0;x" in str(not_found.value)


def test_e2e_error_output_is_clean(project, tmp_path):
    os.symlink("/etc/hosts", project / "registry/prompt/support/1.0.0" / EVIL)
    done = _cli("registry", "show", "prompt/support@1.0.0", "--json", cwd=project)
    assert done.returncode == 2
    assert "ENTRY_UNSAFE" in done.stderr and "e[2J" in done.stderr
    for bad in (ESC, BIDI, "\x07"):
        assert bad not in done.stderr and bad not in done.stdout


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
    assert "--- prompt.md ---\nBe kind." in out
    assert f"Root: {(project / 'registry').resolve()} (project)" in out
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


def test_show_paths_are_absolute(project, monkeypatch):
    monkeypatch.chdir(project)
    view = registry.show(Ref.parse("prompt/support@1.0.0"), registry.roots(Path(".")), Path("."))
    assert view["root"] == str((project / "registry").resolve())
    assert view["path"] == str((project / "registry/prompt/support/1.0.0").resolve())
    assert view["root_kind"] == "project"


def test_lone_surrogates_and_invisible_characters_are_stripped(project, capsys, monkeypatch):
    """Review items 8 and 12: YAML can produce lone surrogates, which cannot be printed; names
    also lose zero-width, word-joiner, BOM and tag characters."""
    invisible = chr(0x200B) + chr(0x2060) + chr(0xFEFF) + chr(0xE0041)
    assert registry.clean("a" + chr(0xD800) + "b") == ("ab", True)
    assert registry.clean("a" + invisible + "b", name=True) == ("ab", True)
    version = make_version(project / "registry", "prompt", "s", "1.0.0", {f"n{invisible}.md": ""})
    (version / "manifest.yaml").write_text(
        'kind: prompt\nname: s\nversion: 1.0.0\ndescription: "x\\uD800y"\n'
    )
    monkeypatch.chdir(project)
    assert cli.main(["registry", "show", "prompt/s@1.0.0"]) == 0
    out = capsys.readouterr().out
    assert "Description: xy" in out and "  n.md  0 B" in out
    out.encode("utf-8")  # printable: no surrogate left
    assert cli.main(["registry", "show", "prompt/s@1.0.0", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["description"] == "xy"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_unreadable_entries_are_errors_not_tracebacks(project, capsys, monkeypatch):
    """Review item 9: permission errors (and ENOENT/ELOOP mid-read) become ENTRY_UNSAFE."""
    version = project / "registry/prompt/support/1.0.0"
    monkeypatch.chdir(project)
    (version / "prompt.md").chmod(0)
    try:
        assert cli.main(["registry", "show", "prompt/support@1.0.0"]) == 2
        assert "[ENTRY_UNSAFE] cannot read" in capsys.readouterr().err
    finally:
        (version / "prompt.md").chmod(0o644)
    (version / "sub").mkdir()
    (version / "sub").chmod(0)
    try:
        assert cli.main(["registry", "show", "prompt/support@1.0.0"]) == 2
        assert "[ENTRY_UNSAFE] cannot read" in capsys.readouterr().err
    finally:
        (version / "sub").chmod(0o755)


def test_read_errors_mid_read_are_wrapped(project, monkeypatch):
    def gone(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "prompt.md")

    monkeypatch.setattr(registry, "_read_one", gone)
    with pytest.raises(RegistryError) as raised:
        registry.show(Ref.parse("prompt/support@1.0.0"), registry.roots(project), project)
    assert raised.value.code == "ENTRY_UNSAFE" and "cannot read prompt.md" in str(raised.value)


@pytest.mark.parametrize("level", ["kind", "name"])
def test_symlinks_inside_a_root_are_refused(project, tmp_path, level):
    """Review item 10: a symlinked <root>/<kind> or <root>/<kind>/<name> would show files from
    outside the root under root_kind project."""
    elsewhere = tmp_path / "elsewhere"
    make_version(elsewhere, "tool", "x", "1.0.0", {"a.py": "secret = 1\n"})
    root = project / "registry"
    if level == "kind":
        os.symlink(elsewhere / "tool", root / "tool")
    else:
        (root / "tool").mkdir()
        os.symlink(elsewhere / "tool/x", root / "tool/x")
    search = registry.roots(project)
    for call in (
        lambda: registry.show(Ref.parse("tool/x@1.0.0"), search, project),
        lambda: registry.diff(Ref.parse("tool/x@1.0.0"), Ref.parse("tool/x@1.0.0"), search),
    ):
        with pytest.raises(RegistryError) as raised:
            call()
        assert raised.value.code == "ENTRY_UNSAFE" and "symlink" in str(raised.value)


def test_e2e_show_into_a_closed_pipe_exits_quietly(project):
    """Review item 15: `registry show … | head` must not end with a BrokenPipeError
    traceback."""
    make_version(project / "registry", "prompt", "big", "1.0.0", {"p.md": "line\n" * 60000})
    process = subprocess.Popen(
        [sys.executable, "-m", "agentbreeder", "registry", "show", "prompt/big@1.0.0"],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    process.stdout.readline()
    process.stdout.close()  # the reader leaves, like `head -1`
    _, err = process.communicate(timeout=60)
    assert process.returncode == 0
    assert b"Traceback" not in err and b"BrokenPipe" not in err


@pytest.mark.parametrize(
    "argv",
    [
        ["show", "prompt/support@1.0.0"],
        ["show", "prompt/support@1.0.0", "--json"],
        ["diff", "prompt/support@1.0.0", "prompt/support@1.0.0", "--json"],
        ["list"],
        ["used-by", "prompt/support@1.0.0", "--json"],
    ],
    ids=["show", "show-json", "diff", "list", "used-by"],
)
def test_e2e_small_output_into_a_closed_pipe_exits_quietly(project, tmp_path, argv):
    """Review round 2, item 4: `… | true` closes the pipe before small output is flushed."""
    process = subprocess.Popen(
        [sys.executable, "-m", "agentbreeder", "registry", *argv],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "AGENTBREEDER_STATE_DIR": str(tmp_path / "state")},
    )
    process.stdout.close()  # the reader is gone before anything is written
    _, err = process.communicate(timeout=60)
    assert process.returncode == 0, err
    assert b"BrokenPipe" not in err and b"Exception ignored" not in err


@pytest.mark.parametrize("level", ["kind", "name", "dangling"])
def test_verify_and_list_report_symlinked_directories(project, tmp_path, capsys, level):
    """Review round 2, item 3: verify and list apply resolve's rule. A symlinked or dangling
    ./registry/<kind> fails every ref of that kind, even one a later root has (fail closed)."""
    elsewhere = tmp_path / "elsewhere"
    make_version(elsewhere, "tool", "x", "1.0.0", {"a.py": ""})
    root = project / "registry"
    if level == "kind":
        os.symlink(elsewhere / "tool", root / "tool")
    elif level == "name":
        (root / "tool").mkdir()
        os.symlink(elsewhere / "tool/x", root / "tool/x")
    else:
        os.symlink(tmp_path / "missing", root / "tool")
    problems = registry.verify(registry.roots(project))
    assert [p["ref"] for p in problems] == ["tool/*" if level != "name" else "tool/x@*"]
    assert "must not be a symlink" in problems[0]["error"]
    rows = registry.listing(registry.roots(project))
    if level != "dangling":
        assert [r["symlinked"] for r in rows if r["ref"] == "tool/x@1.0.0"] == [True]
    assert [r["symlinked"] for r in rows if r["ref"] == "prompt/support@1.0.0"] == [False]
    assert cli.main(["registry", "verify", "--project", str(project)]) == 2
    assert "must not be a symlink" in capsys.readouterr().err
    if level != "dangling":
        assert cli.main(["registry", "list", "--project", str(project)]) == 0
        assert "tool/x@1.0.0" in capsys.readouterr().out.split("(symlinked directory: refused)")[0]
    # Fail closed: a later root with the version does not help.
    with pytest.raises(RegistryError, match="must not be a symlink"):
        registry.resolve(Ref.parse("tool/x@1.0.0"), [root, elsewhere])


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
        hit = registry.resolve(Ref.parse(ref), search)
        assert view["ref"] == ref and view["sha256"] == registry.copy_and_hash(hit.path)
    shown = _cli(
        "registry", "show", "prompt/northwind-support@1.0.1", "--project", str(NORTHWIND), "--json"
    )
    assert shown.returncode == 0, shown.stderr
    view = json.loads(shown.stdout)
    assert view["root_kind"] == "project"
    lock = json.loads((NORTHWIND / "agent.lock").read_text())["entries"]
    assert view["sha256"] == lock["prompt/northwind-support@1.0.1"]["sha256"]


# -- diff ----------------------------------------------------------------------------------------


def _diff1_hunks(before: Path, after: Path, context: int = 3) -> str:
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
def test_diff_hunks_match_diff1_where_lines_do_not_repeat(project, versions):
    search = registry.roots(project)
    for context in (1, 3, 10):  # context 0 empty-range numbering differs by platform
        result = registry.diff(
            Ref.parse("tool/t@1.0.0"), Ref.parse("tool/t@1.0.1"), search, context, project
        )
        for name in ("a.txt", "tail.txt", "manifest.yaml"):
            expected = _diff1_hunks(versions / "1.0.0" / name, versions / "1.0.1" / name, context)
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
    assert not any(c.get("too_large") for c in result["files"])
    assert result["from"]["root"] == str((project / "registry").resolve())
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
        registry.diff(ref, Ref.parse("prompt/support@9.0.0"), search)
    assert raised.value.code == "REF_NOT_FOUND"
    with pytest.raises(RegistryError) as raised:
        registry.diff(ref, ref, search, context=-1)
    assert raised.value.code == "DIFF_INVALID"
    # Review item 15: only versions of one kind/name are compared.
    for other in ("prompt/x@1.0.0", "tool/support@1.0.0"):
        with pytest.raises(RegistryError) as raised:
            registry.diff(ref, Ref.parse(other), search)
        assert raised.value.code == "REF_INVALID"


def test_diff_context_zero_numbering(project):
    """diff(1) implementations number an empty side differently at context 0 (GNU and BSD
    disagree), so this pins ours: the line before, as `diff -u` documents."""
    root = project / "registry"
    make_version(root, "prompt", "z", "1.0.0", {"p.md": "a\nb\nc\n"})
    make_version(root, "prompt", "z", "1.0.1", {"p.md": "a\nb\nnew\nc\n"})
    result = registry.diff(
        Ref.parse("prompt/z@1.0.0"), Ref.parse("prompt/z@1.0.1"), registry.roots(project), 0
    )
    assert "@@ -2,0 +3 @@\n+new\n" in registry.unified(result)


def _repetitive(size: int, insert_at: int | None) -> str:
    lines = ["a\n"] * (size // 2)
    if insert_at is not None:
        lines.insert(insert_at, "b\n")
    return "".join(lines)


@pytest.mark.parametrize(
    "before, after",
    [
        # One inserted line in 256 KiB of identical lines: trimmed to a tiny region.
        (_repetitive(256 * 1024 - 2, None), _repetitive(256 * 1024 - 2, 70000)),
        # No common prefix or suffix: over the limits, reported as too large.
        ("b\n" + _repetitive(256 * 1024 - 2, None), _repetitive(256 * 1024 - 2, None) + "b\n"),
    ],
    ids=["insert", "rotate"],
)
def test_diff_of_large_repetitive_files_is_fast(project, before, after):
    """Review item 1: SequenceMatcher without autojunk is quadratic in repeated lines; a
    256 KiB pair must finish quickly either way."""
    root = project / "registry"
    make_version(root, "prompt", "r", "1.0.0", {"p.md": before})
    make_version(root, "prompt", "r", "1.0.1", {"p.md": after})
    started = time.monotonic()
    result = registry.diff(
        Ref.parse("prompt/r@1.0.0"), Ref.parse("prompt/r@1.0.1"), registry.roots(project)
    )
    assert time.monotonic() - started < 10
    change = next(c for c in result["files"] if c["path"] == "p.md")
    assert change["too_large"] or change["hunks"]
    if change["too_large"]:
        assert "p.md differ (too large to show)" in registry.unified(result)
    elif before.startswith("a"):
        assert [line for line in change["hunks"][0]["lines"] if line["op"] != " "] == [
            {"op": "+", "text": "b"}
        ]
    else:  # "b" is the one line unique to both sides, so patience anchors on it: still valid
        changed = [line for hunk in change["hunks"] for line in hunk["lines"] if line["op"] != " "]
        assert len([x for x in changed if x["op"] == "-"]) == len(before.splitlines()) - 1


@pytest.mark.parametrize("values", [2, 10, 50, None])
def test_diff_budget_bounds_quadratic_inputs(values, monkeypatch):
    """Repeated lines and many small changes (recursion) are bounded by DIFF_BUDGET, not only
    the pathological identical-lines case. The wall-clock deadline is pinned out of the way so
    the comparison budget alone must bound it; the generous limit only catches minutes-long
    regressions on slow runners or under coverage."""
    monkeypatch.setattr(registry, "DIFF_SECONDS", 3600.0)
    lines = [f"{i % values if values else i}\n" for i in range(3000)]
    other = [line if i % 2 else "z\n" for i, line in enumerate(lines)]
    started = time.monotonic()
    registry._hunks(["x\n"] + lines, other + ["y\n"], 3, registry._Budget())
    assert time.monotonic() - started < 10


def test_diff_budget_is_charged(monkeypatch):
    lines = [f"{n}\n" for n in range(100)]
    assert registry._opcodes(["x\n"] + lines, lines + ["x\n"], registry._Budget()) is not None
    monkeypatch.setattr(registry, "DIFF_BUDGET", 50)
    assert registry._opcodes(["x\n"] + lines, lines + ["x\n"], registry._Budget()) is None
    same = ["a\n"] * 20  # 20 x 20 comparisons, no unique line to anchor on
    assert registry._opcodes(["x\n"] + same, same + ["y\n"], registry._Budget()) is None
    monkeypatch.setattr(registry, "DIFF_BUDGET", 10**9)
    monkeypatch.setattr(registry, "DIFF_SECONDS", -1.0)  # the wall-clock bound
    assert registry._opcodes(["x\n"] + same, same + ["y\n"], registry._Budget()) is None


def test_diff_output_is_bounded_across_files(project):
    """Review round 3, N6: whole-file adds and removes cost no comparisons, so output lines are
    budgeted too (DIFF_LINES, shared by the diff): 8 removed and 8 added files of ~255 KiB of
    empty lines once produced 4M hunk lines and a 330 MB JSON document."""
    root = project / "registry"
    blank = "\n" * (255 * 1024)
    make_version(root, "prompt", "wide", "2.0.0", {f"old{n}.md": blank for n in range(8)})
    make_version(root, "prompt", "wide", "2.0.1", {f"new{n}.md": blank for n in range(8)})
    started = time.monotonic()
    result = registry.diff(
        Ref.parse("prompt/wide@2.0.0"), Ref.parse("prompt/wide@2.0.1"), registry.roots(project)
    )
    text = json.dumps(result, indent=2)
    assert time.monotonic() - started < 10
    shown = sum(len(h["lines"]) for c in result["files"] for h in c.get("hunks", []))
    assert shown <= registry.DIFF_LINES
    assert len(text) < 20 * 1024 * 1024
    changes = [c for c in result["files"] if c["path"] != "manifest.yaml"]
    assert len(changes) == 16 and all(c["too_large"] for c in changes)
    assert "differ (too large to show)" in registry.unified(result)


def test_an_added_file_of_a_few_hundred_lines_is_shown(project):
    root = project / "registry"
    make_version(root, "prompt", "grow", "1.0.0", {"a.md": "a\n"})
    body = "".join(f"case {n}\n" for n in range(400))
    make_version(root, "prompt", "grow", "1.0.1", {"a.md": "a\n", "cases.md": body})
    result = registry.diff(
        Ref.parse("prompt/grow@1.0.0"), Ref.parse("prompt/grow@1.0.1"), registry.roots(project)
    )
    added = next(c for c in result["files"] if c["path"] == "cases.md")
    assert not added["too_large"]
    assert [line["text"] for line in added["hunks"][0]["lines"]] == body.splitlines()


def test_output_line_budget_spends_for_later_files(monkeypatch):
    monkeypatch.setattr(registry, "DIFF_LINES", 10)
    budget = registry._Budget()
    assert registry._hunks([], ["x\n"] * 6, 3, budget) is not None
    assert registry._hunks([], ["y\n"] * 6, 3, budget) is None  # 6 + 6 > 10
    assert registry._hunks([], ["z\n"], 3, budget) is None  # spent: later files too


def test_diff_budget_is_shared_by_all_files(project, monkeypatch):
    """Review round 2, item 1: many files each just under a per-file budget must not add up.
    One budget covers the whole diff; once spent, later changed files are too_large. The
    wall-clock deadline is pinned out of the way so the outcome depends on the comparison
    budget only (deterministic on slow runners and under coverage)."""
    monkeypatch.setattr(registry, "DIFF_SECONDS", 3600.0)
    root = project / "registry"
    same = "a\n" * 1698
    before = {f"f{n:03}.md": "a\n" * 1700 for n in range(45)}
    after = {f"f{n:03}.md": "b\n" + same + "c\n" for n in range(45)}
    make_version(root, "prompt", "many", "1.0.0", before)
    make_version(root, "prompt", "many", "1.0.1", after)
    started = time.monotonic()
    result = registry.diff(
        Ref.parse("prompt/many@1.0.0"), Ref.parse("prompt/many@1.0.1"), registry.roots(project)
    )
    assert time.monotonic() - started < 10
    changes = [c for c in result["files"] if c["path"].startswith("f")]
    assert len(changes) == 45
    assert not changes[0]["too_large"] and changes[0]["hunks"]
    assert all(c["too_large"] and c["hunks"] == [] for c in changes[-40:])


@pytest.mark.parametrize("case", ["blank-lines", "every-tenth"])
def test_plausible_files_are_diffed(case, monkeypatch):
    """Review round 2, item 2: lines unique to both sides anchor the diff (patience), so common
    files with repeated (e.g. blank) lines or many scattered edits are not too large. The
    wall-clock deadline is pinned out of the way so only the comparison budget decides."""
    monkeypatch.setattr(registry, "DIFF_SECONDS", 3600.0)
    if case == "blank-lines":
        before = [f"line {i}\n" if i % 3 else "\n" for i in range(6000)]
        after = list(before)
        after[1000], after[5000] = "edit one\n", "edit two\n"
        changed = 2
    else:
        before = [f"u{i}\n" for i in range(20000)]
        after = [f"changed {i}\n" if i % 10 == 0 else line for i, line in enumerate(before)]
        changed = 2000
    started = time.monotonic()
    hunks = registry._hunks(before, after, 3, registry._Budget())
    assert time.monotonic() - started < 10
    assert hunks is not None
    minus = [line for hunk in hunks for line in hunk["lines"] if line["op"] == "-"]
    plus = [line for hunk in hunks for line in hunk["lines"] if line["op"] == "+"]
    assert len(minus) == len(plus) == changed


def test_patience_anchors_keep_the_longest_ordered_run():
    a = ["x\n", "1\n", "2\n", "3\n", "y\n"]
    b = ["3\n", "1\n", "2\n", "z\n"]
    budget = registry._Budget()
    assert registry._unique_anchors(a, b, 0, len(a), 0, len(b), budget) == [(1, 1), (2, 2)]
    codes = registry._opcodes(a, b, budget)
    rebuilt = []
    for tag, i1, i2, j1, j2 in codes:  # applying the opcodes to `a` yields `b`
        rebuilt += a[i1:i2] if tag == "equal" else b[j1:j2]
    assert rebuilt == b


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
        expected += _diff1_hunks(versions / "1.0.0" / name, versions / "1.0.1" / name)
    assert done.stdout == expected
    as_json = _cli("registry", "diff", *args, "--project", str(NORTHWIND), "--json")
    result = json.loads(as_json.stdout)
    assert [c["path"] for c in result["files"]] == ["manifest.yaml", "prompt.md"]
