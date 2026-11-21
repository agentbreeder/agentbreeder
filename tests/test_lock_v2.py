"""Remote registry sources, slice 1a (#829): the `Root` type, strict `agent.lock` v1/v2, the
canonical source URL form, `--locked` from the locked root only, and empty directories refused.
No network: nothing here fetches."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentbreeder import registry, release, sources
from agentbreeder.agentfile import load_agent_file
from agentbreeder.lock import LOCK_VERSION, Lock, materialise
from agentbreeder.registry import RegistryError, Root
from agentbreeder.services.lock import StackLock
from agentbreeder.sources.url import SourceUrlError, canonical, subpath

NORTHWIND = Path(registry.__file__).parent / "examples" / "northwind"
COMMIT = "3f2a9c1d0b7e4a5f6c8d9e0f1a2b3c4d5e6f7a8b"
DIGEST = "sha256:" + "9b" * 32
HASH = "sha256:" + "a" * 64
OCI = {"type": "oci", "url": "ghcr.io/a/r"}
E = "entries"


def make_version(root: Path, kind: str, name: str, version: str, files: dict) -> Path:
    path = root / kind / name / version
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text(f"kind: {kind}\nname: {name}\nversion: {version}\n")
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")
    project = tmp_path / "project"
    make_version(project / "registry", "tool", "orders", "1.0.0", {"src/orders.py": "x = 1\n"})
    return project


def run(project, tmp_path, lock, mode="plain", refs=("tool/orders@1.0.0",), name="ctx"):
    return materialise(
        refs=list(refs),
        paths=[],
        project_dir=project,
        search=registry.roots(project),
        lock=lock,
        context=tmp_path / name,
        mode=mode,
    )


def v2(entries: dict, sources_table: dict | None = None) -> dict:
    return {
        "lock_version": 2,
        "sources": sources_table
        if sources_table is not None
        else {
            "acme": {
                "type": "git",
                "url": "https://github.com/acme/agent-registry.git",
                "pin": {"commit": COMMIT},
                "path": "registry",
            }
        },
        "entries": entries,
    }


# -- canonical source URLs (spec §5) -------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, text, expected",
    [
        ("git", "https://github.com/acme/r.git", "https://github.com/acme/r.git"),
        ("git", "HTTPS://GitHub.COM./acme/r.git", "https://github.com/acme/r.git"),
        ("git", "https://github.com:443/acme/r", "https://github.com/acme/r"),
        ("git", "https://git.corp:8443/acme/r", "https://git.corp:8443/acme/r"),
        ("git", "ssh://git@github.com:22/acme/r.git", "ssh://git@github.com/acme/r.git"),
        ("git", "ssh://github.com/acme/r", "ssh://github.com/acme/r"),
        ("git", "git@github.com:acme/r.git", "ssh://git@github.com/acme/r.git"),
        ("git", "https://127.0.0.1:8443/r", "https://127.0.0.1:8443/r"),
        ("git", "https://[::1]:8443/r", "https://[::1]:8443/r"),
        ("http", "https://registry.example.org/ab/index.json", None),
        ("oci", "ghcr.io/acme/agent-registry", "ghcr.io/acme/agent-registry"),
        ("oci", "GHCR.io/acme/agent-registry", "ghcr.io/acme/agent-registry"),
        ("oci", "127.0.0.1:5001/acme/r", "127.0.0.1:5001/acme/r"),
    ],
)
def test_canonical_urls(kind, text, expected):
    assert canonical(kind, text) == (expected or text)


@pytest.mark.parametrize(
    "kind, text, rule",
    [
        ("git", "https://user:token@github.com/acme/r", "credentials"),
        ("git", "https://token@github.com/acme/r", "credentials"),
        ("git", "ssh://git:pw@github.com/acme/r", "password"),
        ("git", "https://github.com/acme/r?x=1", "query"),
        ("git", "https://github.com/acme/r#frag", "query"),
        ("git", "http://github.com/acme/r", "transport"),
        ("git", "file:///tmp/r", "transport"),
        ("git", "ext::sh -c touch% /tmp/pwned", "spaces"),
        ("git", "/tmp/r", "https://"),
        ("git", "./r", "https://"),
        ("git", "https://-oProxyCommand=x/r", "'-'"),
        ("git", "ssh://git@github.com/-oProxyCommand=x", "'-'"),
        ("git", "https://github.com/acme/../r", "'..'"),
        ("git", "https://github.com/acme//r", "non-empty"),
        ("git", "https://github.com/acme/r/", "non-empty"),
        ("git", "https://github.com/acme/%2e%2e/r", "'%'"),
        ("git", "https://github.com/~acme/r", "'~'"),
        ("git", "git@github.com:~acme/r", "'~'"),
        ("git", "https://github.com", "path"),
        ("git", "https://bücher.example/r", "non-ASCII"),
        ("git", "https://github.com:0/r", "port"),
        ("git", "https://github.com:99999/r", "port"),
        ("git", "https://exa_mple.com/r", "invalid host"),
        ("git", "https://github.com/acme/r\\x", "'\\'"),
        ("git", "https://github.com/acme/r\x1b[2J", "control"),
        ("git", "", "non-empty"),
        ("git", 3, "non-empty"),
        ("http", "ssh://git@host/index.json", "transport"),
        ("oci", "https://ghcr.io/acme/r", "scheme"),
        ("oci", "ghcr.io/acme/r:latest", "tag"),
        ("oci", "ghcr.io/acme/r@sha256:" + "0" * 64, "digest"),
        ("oci", "ghcr.io/Acme/r", "invalid path segment"),
        ("svn", "https://x/y", "unknown source type"),
        # security review r1: alternate IPv4 spellings, zone ids, non-ASCII ports, bare OCI
        ("git", "https://2130706433/a", "dotted quad"),
        ("git", "https://0x7f.1/a", "dotted quad"),
        ("git", "https://127.1/a", "dotted quad"),
        ("git", "https://010.1.1.1/a", "dotted quad"),
        ("git", "https://[fe80::1%eth0]/a", "zone"),
        ("git", "https://[fe80::1%25eth0]/a", "zone"),
        ("git", "https://h.com:\u00b2/a", "port"),
        ("git", "https://h.com:\u0664\u0664\u0663/a", "port"),
        ("git", "https://h.com:0443/a", "port"),
        ("oci", "acme/r", "registry host"),
    ],
)
def test_canonical_refuses(kind, text, rule):
    with pytest.raises(SourceUrlError, match=None) as error:
        canonical(kind, text)
    assert rule.lower() in str(error.value).lower()


@pytest.mark.parametrize("text", ["/abs", ":(top)x", "a/../b", "a//b", "", "a/-x", 3])
def test_subpath_refuses(text):
    with pytest.raises(SourceUrlError):
        subpath(text)


def test_subpath_accepts():
    assert subpath(".") == "." and subpath("registry/v1") == "registry/v1"


# -- strict agent.lock v1 / v2 (spec §7) ---------------------------------------------------------


def write(path: Path, body) -> Path:
    path.write_text(json.dumps(body) if not isinstance(body, str) else body)
    return path


@pytest.mark.parametrize(
    "body, message",
    [
        ({"lock_version": 1, "entries": {}, "extra": 1}, "upgrade"),
        ({"lock_version": 3, "entries": {}}, "upgrade"),
        ({"lock_version": True, "entries": {}}, "unsupported"),
        ({"lock_version": "1", "entries": {}}, "unsupported"),
        ({"lock_version": 1, "entries": {}, "sources": {}}, "unknown keys"),
        ({"lock_version": 1, E: {"t": {"sha256": HASH, "source": "x", "y": 1}}}, "keys"),
        ({"lock_version": 1, "entries": {"t": {"sha256": "abc", "source": "x"}}}, "malformed"),
        ({"lock_version": 1, "entries": {"t": {"sha256": HASH}}}, "malformed"),
        ({"lock_version": 1, "entries": {"t": {"sha256": HASH, "source": ""}}}, "malformed"),
        ({"lock_version": 1, E: {"t": {"sha256": HASH, "source": "x" * 600}}}, "malformed"),
        ({"lock_version": 1, "entries": {"t": "x"}}, "malformed"),
        (
            {"lock_version": 1, "entries": {"t": {"sha256": HASH, "source": "remote:acme"}}},
            "version 1",
        ),
        ({"lock_version": 2, "entries": {}}, "missing sources"),
        ({"lock_version": 1.0, "entries": {}}, "unsupported"),
        (v2({"t": {"sha256": HASH, "source": "remote:acme", "pin": {}}}), "unknown pin"),
        (
            v2(
                {"t": {"sha256": HASH, "source": "remote:web"}},
                {
                    "web": {
                        "type": "http",
                        "url": "https://h.org/i.json",
                        "pin": {"index_sha256": DIGEST},
                    }
                },
            ),
            "missing pin",
        ),
        ('{"lock_version": 1, "entries": {}, "entries": {}}', "not a valid lock"),
        ("[" * 100000, "too deeply nested"),
        (v2({"t": {"sha256": HASH, "source": "remote:other"}}), "not in sources"),
        (v2({"t": {"sha256": HASH, "source": "registry", "pin": {}}}), "local entry"),
        (
            v2({"t": {"sha256": HASH, "source": "remote:acme", "pin": {"archive_sha256": HASH}}}),
            "unknown pin",
        ),
        (
            v2(
                {},
                {"a": {"type": "git", "url": "https://github.com/a/r", "pin": {"commit": "main"}}},
            ),
            "malformed",
        ),
        (
            v2(
                {},
                {"a": {"type": "git", "url": "https://GitHub.com/a/r", "pin": {"commit": COMMIT}}},
            ),
            "canonical",
        ),
        (
            v2(
                {},
                {"a": {"type": "git", "url": "https://t@github.com/a", "pin": {"commit": COMMIT}}},
            ),
            "credentials",
        ),
        (v2({}, {"a": {"type": "svn", "url": "x", "pin": {}}}), "unknown type"),
        (v2({}, {"A!": {"type": "git"}}), "name"),
        (v2({}, {"a": []}), "object"),
        (
            v2(
                {},
                {
                    "a": {
                        "type": "git",
                        "url": "https://github.com/a/r",
                        "pin": {"commit": COMMIT},
                        "mirror": "x",
                    }
                },
            ),
            "upgrade",
        ),
        (
            v2(
                {},
                {"a": {**OCI, "pin": {"digest": DIGEST}, "path": "x"}},
            ),
            "only git",
        ),
        (
            v2(
                {},
                {
                    "a": {
                        "type": "git",
                        "url": "https://github.com/a/r",
                        "pin": {"commit": COMMIT},
                        "path": "../x",
                    }
                },
            ),
            "path",
        ),
        (
            v2({}, {"a": {"type": "oci", "url": "ghcr.io/a/r", "pin": {"commit": COMMIT}}}),
            "exactly 'digest'",
        ),
    ],
)
def test_lock_load_is_strict(tmp_path, body, message):
    with pytest.raises(RegistryError) as error:
        Lock.load(write(tmp_path / "agent.lock", body))
    assert error.value.code == "LOCK_INVALID"
    assert message in str(error.value)


def test_lock_v2_round_trips_byte_identical(tmp_path):
    body = v2(
        {
            "tool/orders@1.0.0": {"sha256": HASH, "source": "registry"},
            "tool/geo@1.2.0": {"sha256": HASH, "source": "remote:acme"},
            "prompt/greeter@2.0.0": {
                "sha256": HASH,
                "source": "remote:community",
                "pin": {"archive_sha256": HASH},
            },
        },
        {
            "acme": {
                "type": "git",
                "url": "https://github.com/acme/agent-registry.git",
                "pin": {"commit": COMMIT},
                "path": "registry",
            },
            "acme-images": {
                "type": "oci",
                "url": "ghcr.io/acme/agent-registry",
                "pin": {"digest": DIGEST},
            },
            "community": {
                "type": "http",
                "url": "https://registry.example.org/agentbreeder/index.json",
                "pin": {"index_sha256": DIGEST},
            },
        },
    )
    lock = Lock.load(write(tmp_path / "agent.lock", body))
    assert set(lock.sources) == {"acme", "acme-images", "community"}
    lock.save(tmp_path / "again.lock")
    first = (tmp_path / "again.lock").read_bytes()
    assert json.loads(first)["lock_version"] == 2
    assert list(json.loads(first)) == ["lock_version", "sources", "entries"]
    Lock.load(tmp_path / "again.lock").save(tmp_path / "third.lock")
    assert (tmp_path / "third.lock").read_bytes() == first


def test_empty_sources_table_is_still_v2(tmp_path):
    Lock(sources={}).save(tmp_path / "agent.lock")
    assert json.loads((tmp_path / "agent.lock").read_text()) == {
        "lock_version": 2,
        "sources": {},
        "entries": {},
    }
    Lock().save(tmp_path / "v1.lock")
    assert json.loads((tmp_path / "v1.lock").read_text()) == {"lock_version": 1, "entries": {}}


def test_northwind_locks_load_and_resave_byte_identical(tmp_path):
    """Golden (spec §11 slice 1a): every lock written before #829 is untouched."""
    for name, loader in (("agent.lock", Lock.load), ("stack.lock", StackLock.load)):
        original = (NORTHWIND / name).read_bytes()
        loader(NORTHWIND / name).save(tmp_path / name)
        assert (tmp_path / name).read_bytes() == original, name


def test_a_project_without_registries_locks_byte_identical(tmp_path, monkeypatch):
    """Golden: re-locking Northwind from scratch (plain mode, no `registries:`) writes its
    committed agent.lock byte for byte, as version 1."""
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = tmp_path / "northwind"
    shutil.copytree(NORTHWIND, project)
    agent = load_agent_file(project / "agent.yaml")
    lock = Lock()
    materialise(
        refs=agent.refs(),
        paths=agent.paths(),
        project_dir=project,
        search=registry.roots(project),
        lock=lock,
        context=tmp_path / "ctx",
    )
    lock.save(project / "agent.lock")
    assert (project / "agent.lock").read_bytes() == (NORTHWIND / "agent.lock").read_bytes()


def test_stack_lock_stays_version_1(tmp_path):
    with pytest.raises(RegistryError) as error:
        StackLock.load(write(tmp_path / "stack.lock", v2({})))
    assert error.value.code == "LOCK_INVALID"
    path = write(tmp_path / "ok.lock", {"lock_version": 1, "entries": {}, "imports": {}})
    assert StackLock.load(path).imports == {}
    with pytest.raises(RegistryError, match="upgrade"):
        Lock.load(path)  # agent.lock has no `imports`


# -- roots and --locked (spec §4.1, §4.3, D6) ----------------------------------------------------


def test_root_labels(project, tmp_path):
    roots = registry.configured_roots(project)
    assert [r.label for r in roots] == ["registry", "builtin"]
    assert all(r.source is None and r.kinds == frozenset(registry.KINDS) for r in roots)
    planted = project / "remote:acme"
    with pytest.raises(RegistryError) as error:
        registry.root_label(planted, project)
    assert error.value.code == "REGISTRY_ROOT_INVALID"
    assert registry.root_label(tmp_path / "x", None) == str(tmp_path / "x")


def test_locked_ignores_a_conflicting_copy_in_another_root(project, tmp_path, monkeypatch):
    """D6: --locked reads only the locked root. A plain build sees the conflict; --locked does
    not need (or read) the other root."""
    lock = Lock()
    run(project, tmp_path, lock)
    shared = tmp_path / "shared"
    make_version(shared, "tool", "orders", "1.0.0", {"src/orders.py": "x = 2\n"})
    monkeypatch.setenv(registry.PATH_ENV, str(shared))
    with pytest.raises(RegistryError) as error:
        run(project, tmp_path, lock, name="plain")
    assert error.value.code == "REF_CONFLICT"
    entries, changed = run(project, tmp_path, lock, mode="locked", name="locked")
    assert not changed and entries[0].source == "registry"
    assert (entries[0].path / "src/orders.py").read_text() == "x = 1\n"


@pytest.mark.parametrize("label", ["../outside", "/etc", "elsewhere", "registry/../../x"])
def test_locked_refuses_a_root_that_is_not_configured(project, tmp_path, label):
    """A planted lock cannot point --locked at an arbitrary directory (spec §4.3)."""
    outside = tmp_path / "outside"
    make_version(outside, "tool", "orders", "1.0.0", {"src/orders.py": "x = 1\n"})
    lock = Lock()
    run(project, tmp_path, lock)
    lock.entries["tool/orders@1.0.0"]["source"] = label
    with pytest.raises(RegistryError) as error:
        run(project, tmp_path, lock, mode="locked", name="l")
    assert error.value.code == "LOCK_STALE"
    assert "not a configured registry root" in str(error.value)


def test_locked_accepts_a_configured_env_root(project, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    make_version(shared, "prompt", "p", "1.0.0", {"prompt.md": "hi"})
    monkeypatch.setenv(registry.PATH_ENV, str(shared))
    lock = Lock()
    run(project, tmp_path, lock, refs=["prompt/p@1.0.0"])
    assert lock.entries["prompt/p@1.0.0"]["source"] == "../shared"
    _, changed = run(project, tmp_path, lock, mode="locked", refs=["prompt/p@1.0.0"], name="b")
    assert not changed
    monkeypatch.delenv(registry.PATH_ENV)
    with pytest.raises(RegistryError) as error:  # no longer configured
        run(project, tmp_path, lock, mode="locked", refs=["prompt/p@1.0.0"], name="c")
    assert error.value.code == "LOCK_STALE"


def test_locked_remote_entry_without_registries_is_stale(project, tmp_path):
    # Slice 1b: a lock with sources and an agent.yaml without `registries:` is LOCK_STALE under
    # --locked, before anything is read (it was SOURCE_OFFLINE in slice 1a).
    lock = Lock(
        entries={"tool/orders@1.0.0": {"sha256": HASH, "source": "remote:acme"}},
        sources=v2({})["sources"],
    )
    with pytest.raises(RegistryError) as error:
        run(project, tmp_path, lock, mode="locked")
    assert error.value.code == "LOCK_STALE" and "declares no registries" in str(error.value)
    assert not (tmp_path / "ctx").exists()


def test_locate_dispatches_remote_before_any_path_join(tmp_path):
    roots = [Root(tmp_path, "remote:acme")]  # even a root labelled that way is never used
    with pytest.raises(RegistryError) as error:
        sources.locate("tool/x@1.0.0", {"source": "remote:acme"}, roots)
    assert error.value.code == "SOURCE_OFFLINE"


def test_plain_build_without_registries_writes_v1_again(project, tmp_path):
    # Spec §7: removing `registries:` writes v1 again (slice 1a kept a v2 lock v2).
    lock = Lock(sources={})
    _, changed = run(project, tmp_path, lock)
    lock.save(project / "agent.lock")
    assert changed
    assert json.loads((project / "agent.lock").read_text())["lock_version"] == 1
    assert Lock.load(project / "agent.lock").sources is None


# -- release's eval set (spec §7 consumers) ------------------------------------------------------


class _Agent:
    def __init__(self, ref):
        self.evals = type("E", (), {"ref": ref})()


def test_release_eval_set_reads_only_the_locked_root(project, tmp_path, monkeypatch):
    make_version(
        project / "registry",
        "eval",
        "golden",
        "1.0.0",
        {"cases.yaml": "cases: []\n"},
    )
    lock = Lock()
    run(project, tmp_path, lock, refs=["eval/golden@1.0.0"])
    lock.save(project / "agent.lock")
    shared = tmp_path / "shared"
    make_version(shared, "eval", "golden", "1.0.0", {"cases.yaml": "cases: [x]\n"})
    monkeypatch.setenv(registry.PATH_ENV, str(shared))
    seen = {}
    monkeypatch.setattr(release.evals, "load_eval_set", lambda path: seen.setdefault("p", path))
    release._eval_set(_Agent("eval/golden@1.0.0"), project)  # no REF_CONFLICT
    lock.entries["eval/golden@1.0.0"]["source"] = "../shared"
    lock.save(project / "agent.lock")
    # Now read from the env root it names (configured), whose bytes differ from the lock.
    with pytest.raises(release.ReleaseError) as error:
        release._eval_set(_Agent("eval/golden@1.0.0"), project)
    assert error.value.code == "HASH_MISMATCH"


def test_release_eval_set_refuses_an_unconfigured_root(project, tmp_path):
    make_version(project / "registry", "eval", "golden", "1.0.0", {"cases.yaml": "cases: []\n"})
    lock = Lock()
    run(project, tmp_path, lock, refs=["eval/golden@1.0.0"])
    lock.entries["eval/golden@1.0.0"]["source"] = "/etc"
    lock.save(project / "agent.lock")
    with pytest.raises(RegistryError) as error:
        release._eval_set(_Agent("eval/golden@1.0.0"), project)
    assert error.value.code == "LOCK_STALE" and "build" in str(error.value)


# -- empty directories (D7, spec §6.5) -----------------------------------------------------------


def test_publish_and_verify_refuse_empty_directories(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    source = make_version(tmp_path / "src", "prompt", "p", "1.0.0", {"prompt.md": "hi"})
    (source / "assets").mkdir()
    with pytest.raises(RegistryError) as error:
        registry.publish(source, tmp_path / "registry")
    assert error.value.code == "ENTRY_UNSAFE" and ".keep" in str(error.value)
    (source / "assets" / ".keep").write_text("")
    registry.publish(source, tmp_path / "registry")
    placed = tmp_path / "registry/prompt/p/1.0.0"
    assert registry.verify([tmp_path / "registry"]) == []
    (placed / "assets" / ".keep").unlink()
    problems = registry.verify([tmp_path / "registry"])
    assert problems and "empty directory assets/" in problems[0]["error"]


def test_no_shipped_version_has_an_empty_directory():
    for root in (registry.BUILTIN_ROOT, NORTHWIND / "registry"):
        for version in root.glob("*/*/*"):
            registry.check_no_empty_dirs(version)


def test_cli_publish_refuses_an_empty_directory(tmp_path):
    """End to end: `python -m agentbreeder registry publish` exits 2 with the remedy."""
    source = make_version(tmp_path / "src", "prompt", "p", "1.0.0", {"prompt.md": "hi"})
    (source / "empty").mkdir()
    done = subprocess.run(
        [sys.executable, "-m", "agentbreeder", "registry", "publish", str(source), "--to", "reg"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert done.returncode == 2, done.stderr
    assert "ENTRY_UNSAFE" in done.stderr and "add a .keep file" in done.stderr
    assert not (tmp_path / "reg/prompt/p/1.0.0").exists()


def test_cli_registry_used_by_reports_a_newer_lock(tmp_path):
    """End to end: a lock from a newer AgentBreeder is reported, not a traceback."""
    project = tmp_path / "p"
    project.mkdir()
    write(project / "agent.lock", {"lock_version": 3, "entries": {}})
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentbreeder",
            "registry",
            "used-by",
            "tool/orders@1.0.0",
            "--project",
            str(project),
            "--json",
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert "Traceback" not in done.stderr
    assert "upgrade AgentBreeder" in done.stdout + done.stderr


def test_lock_version_constant_is_unchanged():
    assert LOCK_VERSION == 1
