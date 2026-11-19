"""Remote registry sources, slice 1b (#829): git sources at a commit, the trusted-source list,
the cache, `agent.lock` v2 written from `registries:`, REF_CONFLICT across sources, `--offline`.

Every network test talks to git's own `git http-backend` behind a loopback HTTPS server
(`tests/git_source_support.py`), with a test certificate through GIT_SSL_CAINFO and a
`private: true` trust entry. No third-party network, no Docker."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agentbreeder import registry, sources
from agentbreeder.agentfile import AgentFileError, load_agent_file
from agentbreeder.lock import Lock, materialise
from agentbreeder.registry import RegistryError, Root
from agentbreeder.sources import git as git_source
from agentbreeder.sources import trust
from agentbreeder.sources.cache import Cache
from agentbreeder.sources.remote import Remotes
from agentbreeder.sources.url import SourceUrlError
from tests.git_source_support import (
    LINK,
    MODULE,
    BareRepo,
    GitServer,
    needs_git,
    trust_file,
)

pytestmark = needs_git

GEO = "tool/geo@1.0.0"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private HOME and cache, no trust from the environment, no project registry path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENTBREEDER_CACHE_DIR", str(home / ".agentbreeder" / "cache"))
    for name in (trust.ENV, registry.PATH_ENV, "GIT_SSL_CAINFO"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")
    return home


@pytest.fixture
def remote(tmp_path):
    """A bare repo `org/reg.git` with tool/geo@1.0.0 at two commits (first, second)."""
    repo = BareRepo(tmp_path / "srv" / "org" / "reg.git")
    repo.version("tool", "geo", "1.0.0", {"src/geo.py": "x = 1\n", "run.sh": "echo\n"})
    repo.first = repo.commit("one")
    repo.version("prompt", "hello", "1.0.0", {"prompt.md": "Hi.\n"})
    repo.second = repo.commit("two")
    return repo


@pytest.fixture
def server(tmp_path, remote, home, monkeypatch):
    srv = GitServer(tmp_path / "srv", tmp_path / "certs")
    monkeypatch.setenv("GIT_SSL_CAINFO", srv.ca)
    trust_file(home, {"url": f"https://127.0.0.1:{srv.port}/org", "private": True})
    yield srv
    srv.stop()


def record(server, commit, path="registry"):
    url = server.url("org/reg.git")
    data = {"type": "git", "url": url, "pin": {"commit": commit}}
    if path != ".":
        data["path"] = path
    return data


def project_dir(tmp_path) -> Path:
    project = tmp_path / "project"
    (project / "registry").mkdir(parents=True, exist_ok=True)
    return project


def build_refs(
    project, tmp_path, lock, table, *, mode="plain", refs=(GEO,), offline=False, name="ctx"
):
    context = tmp_path / name
    shutil.rmtree(context, ignore_errors=True)
    with Remotes(table, offline=offline) as remotes:
        return materialise(
            refs=list(refs),
            paths=[],
            project_dir=project,
            search=registry.roots(project),
            lock=lock,
            context=context,
            mode=mode,
            remotes=remotes,
        )


def local_hash(tmp_path, files: dict) -> str:
    root = tmp_path / "localcopy"
    shutil.rmtree(root, ignore_errors=True)
    path = root / "tool" / "geo" / "1.0.0"
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text("kind: tool\nname: geo\nversion: 1.0.0\n")
    for relative, content in files.items():
        (path / relative).parent.mkdir(parents=True, exist_ok=True)
        (path / relative).write_text(content)
        if relative.endswith(".sh"):
            (path / relative).chmod(0o755)
    return registry.copy_and_hash(path)


# -- the trusted-source list (spec §8.3) ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://GitHub.com/acme/", "https://github.com/acme"),
        ("https://github.com:443/acme", "https://github.com/acme"),
        ("ssh://git@github.com:22/acme", "ssh://git@github.com/acme"),
        ("git@github.com:acme", "ssh://git@github.com/acme"),
    ],
)
def test_trust_prefixes_are_canonical(text, expected):
    assert trust.prefix(text) == (expected, False)


def test_a_bare_host_needs_whole_host():
    with pytest.raises(SourceUrlError, match="whole host"):
        trust.prefix("https://github.com")
    assert trust.prefix("https://github.com/", whole_host=True) == ("https://github.com", True)


@pytest.mark.parametrize(
    ("entry", "url", "expected"),
    [
        ("https://github.com/acme", "https://github.com/acme/reg.git", True),
        ("https://github.com/acme", "https://github.com/acme", True),
        ("https://github.com/acme", "https://github.com/acme-evil/reg.git", False),
        ("https://github.com/acme", "https://github.com:8443/acme/reg.git", False),
        ("https://github.com/acme", "ssh://git@github.com/acme/reg.git", False),
        ("ssh://git@github.com/acme", "ssh://other@github.com/acme/reg.git", False),
        ("ssh://git@github.com/acme", "ssh://github.com/acme/reg.git", False),
        ("https://github.com", "https://github.com/anyone/reg.git", True),
        ("https://github.com/Acme", "https://github.com/acme/reg.git", False),
    ],
)
def test_trust_matches_whole_segments_and_exact_users(entry, url, expected):
    assert trust.matches(entry, url) is expected


def test_alias_spellings_fail_closed():
    """Documented for slice 1b (#1156 /security): two spellings of one address are two
    canonical strings, so trusting one never trusts the other."""
    v4, _ = trust.prefix("https://127.0.0.1/org")
    six, _ = trust.prefix("https://[::1]/org")
    assert not trust.matches(v4, "https://[::1]/org/reg.git")
    assert not trust.matches(six, "https://127.0.0.1/org/reg.git")
    # An IPv4-mapped IPv6 host is refused: Python 3.12.8 prints it ::ffff:7f00:1 and 3.11.16
    # and 3.13 print ::ffff:127.0.0.1, so it has no stable canonical form.
    for text in ("https://[::ffff:127.0.0.1]/org", "https://[::ffff:7f00:1]/org/r.git"):
        with pytest.raises(SourceUrlError, match="IPv4-mapped"):
            trust.prefix(text)
    with pytest.raises(SourceUrlError):
        trust.prefix("https://2130706433/org")  # a second spelling of 127.0.0.1


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "64:ff9b::a00:1",
        "2002:7f00:1::",
        "::7f00:1",
        "fec0::1",
    ],
)
def test_private_and_embedded_addresses_are_not_global(address):
    assert not trust._global(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "2606:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_are_global(address):
    assert trust._global(address)


def test_trust_file_must_be_private(home):
    path = trust_file(home, "https://github.com/acme")
    assert [e.prefix for e in trust.entries()] == ["https://github.com/acme"]
    path.chmod(0o644)
    with pytest.raises(RegistryError, match="SOURCE_TRUST_INVALID"):
        trust.entries()
    path.chmod(0o600)
    link = path.with_name("real.yaml")
    path.rename(link)
    path.symlink_to(link)
    with pytest.raises(RegistryError, match="SOURCE_TRUST_INVALID"):
        trust.entries()


@pytest.mark.parametrize(
    "body",
    [
        {"trusted": ["https://github.com"]},
        {"trusted": [{"url": "https://github.com/a", "private": "yes"}]},
        {"trusted": [{"url": "https://github.com/a", "extra": 1}]},
        {"trusted": ["http://github.com/a"]},
        {"other": []},
    ],
)
def test_trust_file_is_strict(home, body):
    path = home / ".agentbreeder" / "sources.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(body))
    path.chmod(0o600)
    with pytest.raises(RegistryError, match="SOURCE_TRUST_INVALID"):
        trust.entries()


def test_env_trust_is_prefix_only(home, monkeypatch):
    monkeypatch.setenv(trust.ENV, "https://github.com/acme, https://git.corp/x")
    assert [(e.prefix, e.origin) for e in trust.entries()] == [
        ("https://github.com/acme", "env"),
        ("https://git.corp/x", "env"),
    ]
    monkeypatch.setenv(trust.ENV, "https://github.com")
    with pytest.raises(RegistryError, match="whole host"):
        trust.entries()


def test_env_trust_cannot_allow_a_private_host(home, monkeypatch):
    monkeypatch.setenv(trust.ENV, "https://127.0.0.1/org")
    with pytest.raises(RegistryError) as raised:
        trust.check("acme", "https://127.0.0.1/org/reg.git")
    assert raised.value.code == "SOURCE_HOST_PRIVATE"
    assert "registry trust https://127.0.0.1/org --private" in str(raised.value)


def test_untrusted_hint_proposes_the_first_segment(home, monkeypatch):
    monkeypatch.setattr(sys, "stdin", open(os.devnull))
    with pytest.raises(RegistryError) as raised:
        trust.check("acme", "https://github.com/acme/agent-registry.git")
    message = str(raised.value)
    assert raised.value.code == "SOURCE_HOST_UNTRUSTED"
    assert "registry trust https://github.com/acme" in message
    assert f"in CI set {trust.ENV}=https://github.com/acme" in message


def test_no_code_path_sets_the_trust_variable():
    """Spec §8.3: AGENTBREEDER_REGISTRY_TRUST is set by whoever runs CI, never derived from a
    project: only `sources/trust.py` names it, and only to read it."""
    root = Path(registry.__file__).parent
    users = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "AGENTBREEDER_REGISTRY_TRUST" in path.read_text(encoding="utf-8")
    ]
    assert users == ["sources/trust.py"]
    text = (root / "sources" / "trust.py").read_text()
    assert "os.environ[ENV] =" not in text and "setdefault(ENV" not in text


# -- the git fetcher (spec §6.1) -----------------------------------------------------------------


def test_environment_drops_every_other_git_variable(monkeypatch, tmp_path):
    for name, value in {
        "GIT_SSH_COMMAND": "touch /tmp/pwned",
        "GIT_ALLOW_PROTOCOL": "file:ext",
        "GIT_CONFIG_PARAMETERS": "'core.fsmonitor'='touch /tmp/pwned'",
        "GIT_CONFIG_COUNT": "1",
        "GIT_DIR": str(tmp_path),
        "GIT_PROXY_COMMAND": "x",
        "GIT_ASKPASS": "x",
        "LD_PRELOAD": "x",
        "LC_ALL": "C",
        "HTTPS_PROXY": "http://proxy:3128",
    }.items():
        monkeypatch.setenv(name, value)
    env = git_source.environment(tmp_path)
    assert env["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_LITERAL_PATHSPECS"] == "1"
    assert env["GIT_CEILING_DIRECTORIES"] == str(tmp_path)
    for name in (
        "GIT_ALLOW_PROTOCOL",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_DIR",
        "GIT_PROXY_COMMAND",
        "GIT_ASKPASS",
        "LD_PRELOAD",
    ):
        assert name not in env
    assert env["LC_ALL"] == "C" and env["HTTPS_PROXY"] == "http://proxy:3128"


def test_stderr_is_redacted_and_one_line():
    text = git_source.clean_stderr(b"fatal: https://user:tok@host/x \x1b[31mred\x1b[0m\nnext")
    assert "tok" not in text and "\x1b" not in text and "\n" not in text
    assert "https://***@host/x" in text
    proxy = git_source.clean_stderr(b"via http://user:pa/ss@proxy:3128 failed")
    assert "pa/ss" not in proxy and "http://***@proxy:3128" in proxy


def test_fetch_one_commit_by_sha_without_hooks(server, remote, tmp_path):
    with git_source.Repo("acme") as repo:
        assert not (repo.dir / "hooks").exists()  # --template=: no hooks, not even samples
        repo.fetch(server.url("org/reg.git"), remote.first)  # not the branch tip
        assert repo.listing("registry") == {GEO}
        path = repo.extract("registry", registry.Ref.parse(GEO), tmp_path / "stage")
    assert (path / "src" / "geo.py").read_text() == "x = 1\n"
    assert os.stat(path / "run.sh").st_mode & 0o111
    assert not os.stat(path / "src" / "geo.py").st_mode & 0o111
    # Same version, same hash: from git and from a directory (spec §6.5).
    assert registry.copy_and_hash(path) == local_hash(
        tmp_path, {"src/geo.py": "x = 1\n", "run.sh": "echo\n"}
    )
    assert not repo.home.exists()


def test_a_v0_server_falls_back_to_a_full_fetch(tmp_path, remote, home, monkeypatch):
    srv = GitServer(tmp_path / "srv", tmp_path / "certs0", v0=True)
    monkeypatch.setenv("GIT_SSL_CAINFO", srv.ca)
    trust_file(home, {"url": f"https://127.0.0.1:{srv.port}/org", "private": True})
    try:
        with git_source.Repo("acme") as repo:
            repo.fetch(srv.url("org/reg.git"), remote.first)
            assert repo.listing("registry") == {GEO}
        with git_source.Repo("acme") as repo, pytest.raises(RegistryError) as raised:
            repo.fetch(srv.url("org/reg.git"), "0" * 40)
        assert raised.value.code == "SOURCE_COMMIT_MISSING"
    finally:
        srv.stop()


def test_a_missing_commit_is_source_commit_missing(server):
    with git_source.Repo("acme") as repo, pytest.raises(RegistryError) as raised:
        repo.fetch(server.url("org/reg.git"), "1" * 40)
    assert raised.value.code == "SOURCE_COMMIT_MISSING"
    assert "1111111" in str(raised.value)


@pytest.mark.parametrize(
    ("path", "mode", "data", "message"),
    [
        ("link", LINK, b"/etc/passwd", "symlinks are not allowed"),
        ("sub", MODULE, b"d" * 40, "submodules are not allowed"),
        ("big.bin", "100644", b"version https://git-lfs.github.com/spec/v1\noid x\n", "LFS"),
    ],
)
def test_unsafe_entries_are_refused(server, remote, tmp_path, path, mode, data, message):
    remote.put(f"registry/tool/geo/1.0.0/{path}", data, mode)
    commit = remote.commit("unsafe")
    with git_source.Repo("acme") as repo:
        repo.fetch(server.url("org/reg.git"), commit)
        with pytest.raises(RegistryError, match=message) as raised:
            repo.extract("registry", registry.Ref.parse(GEO), tmp_path / "stage")
    assert raised.value.code == "ENTRY_UNSAFE"


def test_interpreter_caches_are_skipped(server, remote, tmp_path):
    remote.put("registry/tool/geo/1.0.0/src/__pycache__/geo.cpython-312.pyc", b"\0")
    remote.put("registry/tool/geo/1.0.0/src/old.pyc", b"\0")
    commit = remote.commit("caches")
    with git_source.Repo("acme") as repo:
        repo.fetch(server.url("org/reg.git"), commit)
        path = repo.extract("registry", registry.Ref.parse(GEO), tmp_path / "stage")
    assert sorted(p.name for p in (path / "src").iterdir()) == ["geo.py"]


def test_a_version_over_the_size_limit_is_refused_before_reading(
    server, remote, tmp_path, monkeypatch
):
    monkeypatch.setattr(registry, "MAX_BYTES", 4)
    with git_source.Repo("acme") as repo:
        repo.fetch(server.url("org/reg.git"), remote.first)
        with pytest.raises(RegistryError, match="too large"):
            repo.extract("registry", registry.Ref.parse(GEO), tmp_path / "stage")


def test_redirects_are_not_followed(tmp_path, remote, home, monkeypatch):
    srv = GitServer(tmp_path / "srv", tmp_path / "certsr", redirect=True)
    monkeypatch.setenv("GIT_SSL_CAINFO", srv.ca)
    trust_file(home, {"url": f"https://127.0.0.1:{srv.port}/org", "private": True})
    try:
        with git_source.Repo("acme") as repo, pytest.raises(RegistryError) as raised:
            repo.fetch(srv.url("org/reg.git"), remote.first)
        assert raised.value.code == "SOURCE_UNREACHABLE"
        assert "redirects are not followed" in str(raised.value)
    finally:
        srv.stop()


def test_a_stopped_server_is_source_unreachable(server, remote):
    server.stop()
    with git_source.Repo("acme") as repo, pytest.raises(RegistryError) as raised:
        repo.fetch(server.url("org/reg.git"), remote.first)
    assert raised.value.code == "SOURCE_UNREACHABLE"
    assert "127.0.0.1" in str(raised.value) and "--offline" in str(raised.value)


def test_untrusted_and_private_hosts_are_never_contacted(server, remote, home):
    trust_file(home)  # nothing trusted
    with git_source.Repo("acme") as repo, pytest.raises(RegistryError) as raised:
        repo.fetch(server.url("org/reg.git"), remote.first)
    assert raised.value.code == "SOURCE_HOST_UNTRUSTED"
    trust_file(home, f"https://127.0.0.1:{server.port}/org")  # trusted, but not private: true
    with git_source.Repo("acme") as repo, pytest.raises(RegistryError) as raised:
        repo.fetch(server.url("org/reg.git"), remote.first)
    assert raised.value.code == "SOURCE_HOST_PRIVATE"
    assert server.requests == []


def test_the_download_cap_and_the_deadline_kill_the_fetch(server, remote, monkeypatch):
    monkeypatch.setattr(git_source, "DOWNLOAD_CAP", 1)
    with git_source.Repo("acme") as repo, pytest.raises(RegistryError) as raised:
        repo.fetch(server.url("org/reg.git"), remote.first)
    assert raised.value.code == "SOURCE_TOO_LARGE"
    monkeypatch.setattr(git_source, "DOWNLOAD_CAP", 10**9)
    monkeypatch.setattr(git_source, "FETCH_TIMEOUT", -1.0)
    monkeypatch.setattr(git_source, "POLL", 0.0001)
    with git_source.Repo("acme") as repo, pytest.raises(RegistryError) as raised:
        repo.fetch(server.url("org/reg.git"), remote.first)
    assert raised.value.code == "SOURCE_TIMEOUT"


def test_a_planted_project_repository_is_not_read(server, remote, tmp_path, monkeypatch):
    """A git repository around the caller (a planted `.git/config` with an fsmonitor command)
    is never discovered: git runs with --git-dir, a private cwd and GIT_CEILING_DIRECTORIES."""
    planted = tmp_path / "planted"
    subprocess.run([git_source.git_binary(), "init", "-q", str(planted)], check=True)
    marker = tmp_path / "pwned"
    with open(planted / ".git" / "config", "a") as handle:
        handle.write(f"[core]\n\tfsmonitor = touch {marker}\n\thooksPath = /nonexistent\n")
    monkeypatch.chdir(planted)
    with git_source.Repo("acme") as repo:
        repo.fetch(server.url("org/reg.git"), remote.first)
        repo.extract("registry", registry.Ref.parse(GEO), tmp_path / "stage")
    assert not marker.exists()


def test_a_host_name_is_fetched_through_the_checked_addresses(server, remote, home):
    """`localhost` resolves to ::1 and 127.0.0.1 here; the server listens on IPv4 only, so the
    fetch works only because every checked address is pinned (curl tries them in order)."""
    trust_file(home, {"url": f"https://localhost:{server.port}/org", "private": True})
    with git_source.Repo("acme") as repo:
        repo.fetch(f"https://localhost:{server.port}/org/reg.git", remote.first)
        assert repo.listing("registry") == {GEO}


def test_git_connects_to_the_address_that_was_checked(tmp_path, remote, home, monkeypatch):
    """DNS rebinding (spec §8.3): git is pinned with http.curloptResolve to the addresses the
    trust check saw. `registry.example.test` exists in no DNS, so the fetch can only succeed
    through the pin."""
    name = "registry.example.test"
    srv = GitServer(tmp_path / "srv", tmp_path / "certsn", names=(name, "localhost"))
    monkeypatch.setenv("GIT_SSL_CAINFO", srv.ca)
    monkeypatch.setattr(trust, "resolve", lambda host, port: ["127.0.0.1"])
    trust_file(home, {"url": f"https://{name}:{srv.port}/org", "private": True})
    try:
        with git_source.Repo("acme") as repo:
            repo.fetch(f"https://{name}:{srv.port}/org/reg.git", remote.first)
            assert repo.listing("registry") == {GEO}
        assert srv.requests
    finally:
        srv.stop()


def test_a_short_cat_file_answer_is_source_invalid(server, remote, tmp_path, monkeypatch):
    with git_source.Repo("acme") as repo:
        repo.fetch(server.url("org/reg.git"), remote.first)
        real = repo._run

        def short(args, **kwargs):
            result = real(args, **kwargs)
            if args[0] == "cat-file" and args[1] == "--batch":
                result.stdout = result.stdout[:10]
            return result

        monkeypatch.setattr(repo, "_run", short)
        with pytest.raises(RegistryError) as raised:
            repo.extract("registry", registry.Ref.parse(GEO), tmp_path / "stage")
    assert raised.value.code == "SOURCE_INVALID"


def test_a_tree_bomb_listing_is_cut_off(server, remote, monkeypatch):
    """`ls-tree -r` output is never read into memory past its cap (#829 /security round 1)."""
    monkeypatch.setattr(git_source, "LISTING_LIMIT", 10)
    with git_source.Repo("acme") as repo:
        repo.fetch(server.url("org/reg.git"), remote.first)
        with pytest.raises(RegistryError) as raised:
            repo.listing("registry")
    assert raised.value.code == "SOURCE_TOO_LARGE"


def test_a_huge_version_listing_is_entry_unsafe(server, remote, tmp_path, monkeypatch):
    monkeypatch.setattr(git_source, "VERSION_LISTING_LIMIT", 10)
    with git_source.Repo("acme") as repo:
        repo.fetch(server.url("org/reg.git"), remote.first)
        with pytest.raises(RegistryError, match="too large") as raised:
            repo.extract("registry", registry.Ref.parse(GEO), tmp_path / "stage")
    assert raised.value.code == "ENTRY_UNSAFE"


def test_a_noisy_server_is_stopped(server, remote, monkeypatch):
    monkeypatch.setattr(git_source, "STDERR_CAP", 0)
    monkeypatch.setattr(git_source, "POLL", 0.0001)
    with git_source.Repo("acme") as repo, pytest.raises(RegistryError) as raised:
        repo.fetch(server.url("org/reg.git"), "2" * 40)  # fails, with stderr
    assert raised.value.code in ("SOURCE_UNREACHABLE", "SOURCE_TOO_LARGE")


@pytest.mark.parametrize("names", [("A.py", "a.py"), ("caf\u00e9.py", "cafe\u0301.py")])
def test_names_that_fold_together_are_refused(server, remote, tmp_path, names):
    for name in names:
        remote.put(f"registry/tool/geo/1.0.0/{name}", b"x\n")
    commit = remote.commit("fold")
    with git_source.Repo("acme") as repo:
        repo.fetch(server.url("org/reg.git"), commit)
        with pytest.raises(RegistryError, match="case or Unicode") as raised:
            repo.extract("registry", registry.Ref.parse(GEO), tmp_path / "stage")
    assert raised.value.code == "ENTRY_UNSAFE"


@pytest.mark.parametrize("version", ["git version 2.36.9", "git version 1.8.0", "nonsense"])
def test_git_before_2_37_is_refused(monkeypatch, version):
    """`http.curloptResolve` (the DNS pin) came in git 2.37; older git would ignore it."""
    real = subprocess.run

    def fake(argv, **kwargs):
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, version.encode(), b"")
        return real(argv, **kwargs)

    monkeypatch.setattr(git_source.subprocess, "run", fake)
    with pytest.raises(RegistryError) as raised:
        git_source.Repo("acme")
    assert raised.value.code == "SOURCE_TOOL_MISSING" and "2.37" in str(raised.value)


def test_a_locked_refused_kind_from_a_source_is_refused(server, remote, tmp_path):
    """#829 /security round 1: a hand-written lock cannot take a runtime from a source under
    --locked, neither when the lock is read nor in materialise itself."""
    remote.version("runtime", "evil", "1.0.0", {"Dockerfile": "FROM scratch\n"})
    commit = remote.commit("runtime")
    project = project_dir(tmp_path)
    table = {"acme": record(server, commit)}
    entry = {"sha256": "sha256:" + "a" * 64, "source": "remote:acme"}
    body = {"lock_version": 2, "sources": table, "entries": {"runtime/evil@1.0.0": entry}}
    (project / "agent.lock").write_text(json.dumps(body))
    with pytest.raises(RegistryError) as raised:
        Lock.load(project / "agent.lock")
    assert raised.value.code == "REMOTE_KIND_REFUSED"
    lock = Lock({"runtime/evil@1.0.0": dict(entry)}, table)  # built without the parser
    with pytest.raises(RegistryError) as raised:
        build_refs(project, tmp_path, lock, table, mode="locked", refs=("runtime/evil@1.0.0",))
    assert raised.value.code == "REMOTE_KIND_REFUSED"
    assert server.requests == []


def test_a_source_path_without_versions_is_reported(server, remote, tmp_path, capsys):
    project = project_dir(tmp_path)
    with pytest.raises(RegistryError):
        build_refs(project, tmp_path, Lock(), {"acme": record(server, remote.first, "wrong")})
    assert "SOURCE_EMPTY" in capsys.readouterr().err


def test_missing_git_is_source_tool_missing(monkeypatch):
    monkeypatch.setattr(git_source.shutil, "which", lambda name: None)
    with pytest.raises(RegistryError) as raised:
        git_source.Repo("acme")
    assert raised.value.code == "SOURCE_TOOL_MISSING"


# -- the cache (spec §8.1) -----------------------------------------------------------------------


def test_the_cache_must_be_private(home):
    top = home / ".agentbreeder" / "cache"
    Cache()
    assert stat.S_IMODE(os.stat(top / "registries" / "v1").st_mode) == 0o700
    os.chmod(top / "registries" / "v1", 0o755)
    with pytest.raises(RegistryError) as raised:
        Cache()
    assert raised.value.code == "CACHE_UNSAFE"


def test_planted_listings_are_discarded(home):
    cache = Cache()
    rec = {"type": "git", "url": "https://h.example/o/r", "pin": {"commit": "a" * 40}}
    path = cache.dir / "listings" / f"{cache.listing_key(rec)}.json"
    path.write_text(json.dumps({"listing_version": 1, "refs": {"../x": None}}))
    assert cache.listing(rec) is None and not path.exists()
    cache.store_listing(rec, {GEO: None})
    assert cache.listing(rec) == {GEO: None}


# -- agent.lock v2 from registries: (spec §4.2, §7) ----------------------------------------------


def test_first_lock_writes_v2_and_fills_the_cache(server, remote, tmp_path):
    project = project_dir(tmp_path)
    table = {"acme": record(server, remote.first)}
    lock = Lock()
    entries, changed = build_refs(project, tmp_path, lock, table)
    assert changed and entries[0].source == "remote:acme"
    lock.save(project / "agent.lock")
    data = json.loads((project / "agent.lock").read_text())
    assert data["lock_version"] == 2 and data["sources"] == table
    assert data["entries"][GEO] == {"sha256": entries[0].sha256, "source": "remote:acme"}
    assert Lock.load(project / "agent.lock").sources == table
    assert Cache().tree(entries[0].sha256) is not None


def test_a_locked_build_survives_a_force_push_and_works_offline(server, remote, tmp_path):
    project = project_dir(tmp_path)
    table = {"acme": record(server, remote.second)}
    lock = Lock()
    build_refs(project, tmp_path, lock, table, refs=(GEO, "prompt/hello@1.0.0"))
    before = lock.body()
    # Force-push: main now points at a commit with different geo content; prune the old one.
    remote.version("tool", "geo", "1.0.0", {"src/geo.py": "x = 666\n", "run.sh": "echo\n"})
    remote.force(remote.commit("rewrite", parent=None))
    remote.prune()
    server.stop()  # and the network is gone: the cache serves the locked bytes
    entries, changed = build_refs(
        project,
        tmp_path,
        lock,
        table,
        mode="locked",
        refs=(GEO, "prompt/hello@1.0.0"),
        offline=True,
        name="ctx2",
    )
    assert not changed and lock.body() == before
    assert (entries[0].path / "src" / "geo.py").read_text() == "x = 1\n"


def test_a_cold_cache_after_a_force_push_fails_and_never_changes(server, remote, tmp_path, home):
    project = project_dir(tmp_path)
    table = {"acme": record(server, remote.first)}
    lock = Lock()
    build_refs(project, tmp_path, lock, table)
    shutil.rmtree(home / ".agentbreeder" / "cache")
    remote.version("tool", "geo", "1.0.0", {"src/geo.py": "x = 666\n"})
    remote.force(remote.commit("rewrite", parent=None))
    remote.prune()
    with pytest.raises(RegistryError) as raised:
        build_refs(project, tmp_path, lock, table, mode="locked", name="ctx2")
    assert raised.value.code == "SOURCE_COMMIT_MISSING"


def test_offline_first_lock_and_missing_listing(server, remote, tmp_path):
    project = project_dir(tmp_path)
    table = {"acme": record(server, remote.first)}
    with pytest.raises(RegistryError) as raised:
        build_refs(project, tmp_path, Lock(), table, offline=True)
    assert raised.value.code == "SOURCE_OFFLINE" and "conflicts" in str(raised.value)
    build_refs(project, tmp_path, Lock(), table)  # caches the listing
    with pytest.raises(RegistryError) as raised:
        build_refs(project, tmp_path, Lock(), table, offline=True, name="ctx2")
    assert raised.value.code == "SOURCE_OFFLINE" and "not locked yet" in str(raised.value)


def test_untrusted_source_is_refused_in_plain_and_locked_modes(server, remote, tmp_path, home):
    project = project_dir(tmp_path)
    table = {"acme": record(server, remote.first)}
    lock = Lock()
    build_refs(project, tmp_path, lock, table)
    trust_file(home)
    shutil.rmtree(home / ".agentbreeder" / "cache")
    for mode in ("plain", "locked"):
        with pytest.raises(RegistryError) as raised:
            build_refs(
                project,
                tmp_path,
                Lock(dict(lock.entries), lock.sources),
                table,
                mode=mode,
                name=f"ctx-{mode}",
            )
        assert raised.value.code == "SOURCE_HOST_UNTRUSTED", mode


def test_a_local_copy_with_other_content_is_ref_conflict(server, remote, tmp_path):
    project = project_dir(tmp_path)
    local = project / "registry" / "tool" / "geo" / "1.0.0"
    local.mkdir(parents=True)
    (local / "manifest.yaml").write_text("kind: tool\nname: geo\nversion: 1.0.0\n")
    (local / "x.py").write_text("other\n")
    table = {"acme": record(server, remote.first)}
    with pytest.raises(RegistryError) as raised:
        build_refs(project, tmp_path, Lock(), table)
    message = str(raised.value)
    assert raised.value.code == "REF_CONFLICT"
    assert "registry" in message and f"source 'acme' (git {server.url('org/reg.git')}" in message
    assert remote.first[:7] in message


def test_the_same_content_locally_is_no_conflict_and_local_wins(server, remote, tmp_path):
    project = project_dir(tmp_path)
    table = {"acme": record(server, remote.first)}
    lock = Lock()
    entries, _ = build_refs(project, tmp_path, lock, table)
    shutil.copytree(entries[0].path, project / "registry" / "tool" / "geo" / "1.0.0")
    entries, changed = build_refs(project, tmp_path, lock, table, name="ctx2")
    assert entries[0].source == "registry" and changed
    assert lock.entries[GEO]["source"] == "registry" and lock.sources == table


def test_two_sources_with_different_content_conflict(server, remote, tmp_path):
    other = BareRepo(tmp_path / "srv" / "org" / "other.git")
    other.version("tool", "geo", "1.0.0", {"src/geo.py": "x = 2\n"})
    commit = other.commit("o")
    project = project_dir(tmp_path)
    table = {
        "acme": record(server, remote.first),
        "beta": {
            "type": "git",
            "url": server.url("org/other.git"),
            "pin": {"commit": commit},
            "path": "registry",
        },
    }
    with pytest.raises(RegistryError) as raised:
        build_refs(project, tmp_path, Lock(), table)
    assert raised.value.code == "REF_CONFLICT"
    assert "source 'acme'" in str(raised.value) and "source 'beta'" in str(raised.value)


def test_a_pin_bump_that_changes_a_version_is_hash_mismatch(server, remote, tmp_path):
    project = project_dir(tmp_path)
    lock = Lock()
    build_refs(project, tmp_path, lock, {"acme": record(server, remote.first)})
    remote.version("tool", "geo", "1.0.0", {"src/geo.py": "x = 2\n", "run.sh": "echo\n"})
    bumped = remote.commit("republish")
    with pytest.raises(RegistryError) as raised:
        build_refs(project, tmp_path, lock, {"acme": record(server, bumped)}, name="ctx2")
    assert raised.value.code == "HASH_MISMATCH"
    assert f"acme@{remote.first[:7]}" in str(raised.value)
    assert f"acme@{bumped[:7]}" in str(raised.value)


def test_a_pin_bump_that_keeps_the_version_updates_sources(server, remote, tmp_path):
    project = project_dir(tmp_path)
    lock = Lock()
    build_refs(project, tmp_path, lock, {"acme": record(server, remote.first)})
    table = {"acme": record(server, remote.second)}
    _, changed = build_refs(project, tmp_path, lock, table, name="ctx2")
    assert changed and lock.sources == table


def test_locked_needs_registries_equal_to_sources(server, remote, tmp_path):
    project = project_dir(tmp_path)
    lock = Lock()
    build_refs(project, tmp_path, lock, {"acme": record(server, remote.first)})
    with pytest.raises(RegistryError) as raised:
        build_refs(
            project,
            tmp_path,
            lock,
            {"acme": record(server, remote.second)},
            mode="locked",
            name="ctx2",
        )
    assert raised.value.code == "LOCK_STALE"
    assert f"({remote.first[:7]} → {remote.second[:7]})" in str(raised.value)


def test_a_corrupt_cached_tree_is_deleted_and_fetched_again(server, remote, tmp_path, capsys):
    project = project_dir(tmp_path)
    table = {"acme": record(server, remote.first)}
    lock = Lock()
    entries, _ = build_refs(project, tmp_path, lock, table)
    tree = Cache().tree(entries[0].sha256)
    (tree / "src" / "geo.py").write_text("tampered\n")
    entries, _ = build_refs(project, tmp_path, lock, table, mode="locked", name="ctx2")
    assert "CACHE_CORRUPT" in capsys.readouterr().err
    assert (entries[0].path / "src" / "geo.py").read_text() == "x = 1\n"


def test_a_first_lock_never_reads_cached_trees(server, remote, tmp_path):
    """A planted, self-consistent tree under any hash cannot become a first lock: with the
    server down, a first lock fails even though the cache holds a tree."""
    project = project_dir(tmp_path)
    table = {"acme": record(server, remote.first)}
    build_refs(project, tmp_path, Lock(), table)  # fills trees/ and listings/
    server.stop()
    with pytest.raises(RegistryError) as raised:
        build_refs(project, tmp_path, Lock(), table, name="ctx2")
    assert raised.value.code == "SOURCE_UNREACHABLE"


def test_refused_kinds_only_in_a_source(server, remote, tmp_path):
    remote.version("runtime", "evil", "1.0.0", {"Dockerfile": "FROM scratch\n"})
    commit = remote.commit("runtime")
    project = project_dir(tmp_path)
    with pytest.raises(RegistryError) as raised:
        build_refs(
            project,
            tmp_path,
            Lock(),
            {"acme": record(server, commit)},
            refs=("runtime/evil@1.0.0",),
        )
    assert raised.value.code == "REMOTE_KIND_REFUSED"


def test_a_ref_nowhere_lists_the_sources(server, remote, tmp_path):
    project = project_dir(tmp_path)
    with pytest.raises(RegistryError) as raised:
        build_refs(
            project,
            tmp_path,
            Lock(),
            {"acme": record(server, remote.first)},
            refs=("tool/none@1.0.0",),
        )
    assert raised.value.code == "REF_NOT_FOUND" and "source 'acme'" in str(raised.value)


def test_a_project_without_registries_writes_byte_identical_v1(tmp_path, home):
    """Golden (spec §7, D3): no `registries:` → version 1, byte for byte as before #829."""
    project = project_dir(tmp_path)
    version = project / "registry" / "tool" / "orders" / "1.0.0"
    version.mkdir(parents=True)
    (version / "manifest.yaml").write_text("kind: tool\nname: orders\nversion: 1.0.0\n")
    lock = Lock()
    materialise(
        refs=["tool/orders@1.0.0"],
        paths=[],
        project_dir=project,
        search=registry.roots(project),
        lock=lock,
        context=tmp_path / "ctx",
    )
    lock.save(project / "agent.lock")
    digest = registry.copy_and_hash(version)
    expected = (
        '{\n  "lock_version": 1,\n  "entries": {\n    "tool/orders@1.0.0": {\n'
        f'      "sha256": "{digest}",\n      "source": "registry"\n'
        "    }\n  }\n}\n"
    )
    assert (project / "agent.lock").read_text() == expected


def test_northwind_relocks_byte_identical(tmp_path, home, monkeypatch):
    monkeypatch.setattr(
        registry, "BUILTIN_ROOT", Path(registry.__file__).parent / "builtin_registry"
    )
    northwind = Path(registry.__file__).parent / "examples" / "northwind"
    project = tmp_path / "northwind"
    shutil.copytree(northwind, project)
    committed = (project / "agent.lock").read_bytes()
    (project / "agent.lock").unlink()
    agent = load_agent_file(project / "agent.yaml")
    assert agent.sources_table() is None
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
    assert (project / "agent.lock").read_bytes() == committed


# -- registries: in agent.yaml (spec §5) ---------------------------------------------------------

AGENT = """\
kind: agent
schema_version: 1
name: helper
runtime: runtime/fake@1.0.0
model: {alias: m}
prompt: {ref: prompt/helper@1.0.0}
"""


def agent_with(tmp_path, registries) -> Path:
    path = tmp_path / "agent.yaml"
    path.write_text(AGENT + yaml.safe_dump({"registries": registries}))
    return path


def test_registries_become_the_lock_sources_table(tmp_path):
    agent = load_agent_file(
        agent_with(
            tmp_path,
            [
                {
                    "name": "acme",
                    "git": "https://GitHub.com:443/acme/reg.git",
                    "commit": "a" * 40,
                    "path": "registry",
                },
                {"name": "beta", "git": "git@github.com:beta/reg.git", "commit": "b" * 40},
            ],
        )
    )
    assert agent.sources_table() == {
        "acme": {
            "type": "git",
            "url": "https://github.com/acme/reg.git",
            "pin": {"commit": "a" * 40},
            "path": "registry",
        },
        "beta": {
            "type": "git",
            "url": "ssh://git@github.com/beta/reg.git",
            "pin": {"commit": "b" * 40},
        },
    }
    for name, record_ in agent.sources_table().items():
        sources.check_source("agent.yaml", name, record_)


@pytest.mark.parametrize(
    ("entry", "code"),
    [
        ({"name": "a", "git": "https://h.example/o/r", "commit": "main"}, "SOURCE_UNPINNED"),
        ({"name": "a", "git": "https://h.example/o/r", "commit": "a" * 7}, "SOURCE_UNPINNED"),
        ({"name": "a", "git": "http://h.example/o/r", "commit": "a" * 40}, "SOURCE_INVALID"),
        ({"name": "a", "git": "https://u:p@h.example/o/r", "commit": "a" * 40}, "SOURCE_INVALID"),
        ({"name": "a", "git": "file:///tmp/r", "commit": "a" * 40}, "SOURCE_INVALID"),
        (
            {"name": "a", "git": "https://h.example/o/r", "commit": "a" * 40, "path": "../x"},
            "SOURCE_INVALID",
        ),
        (
            {"name": "a", "git": "https://h.example/o/r", "commit": "a" * 40, "path": ":(glob)*"},
            "SOURCE_INVALID",
        ),
        ({"name": "a", "oci": "ghcr.io/o/r@sha256:" + "a" * 64}, "SOURCE_INVALID"),
        ({"name": "a", "http": "https://h.example/i.json"}, "SOURCE_INVALID"),
        ({"name": "A", "git": "https://h.example/o/r", "commit": "a" * 40}, "AGENT_INVALID"),
        (
            {"name": "a", "git": "https://h.example/o/r", "commit": "a" * 40, "x": 1},
            "AGENT_INVALID",
        ),
    ],
)
def test_registries_are_checked(tmp_path, entry, code):
    with pytest.raises(AgentFileError) as raised:
        load_agent_file(agent_with(tmp_path, [entry]))
    assert raised.value.code == code
    assert "u:p" not in str(raised.value)


def test_registry_names_are_unique(tmp_path):
    entry = {"name": "a", "git": "https://h.example/o/r", "commit": "a" * 40}
    with pytest.raises(AgentFileError, match="duplicate source name"):
        load_agent_file(agent_with(tmp_path, [entry, dict(entry)]))


# -- registry trust (CLI, as a subprocess) -------------------------------------------------------


def cli(home, *args, input=None):
    env = {**os.environ, "HOME": str(home)}
    env.pop(trust.ENV, None)
    return subprocess.run(
        [sys.executable, "-m", "agentbreeder", "registry", "trust", *args],
        env=env,
        input=input,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_registry_trust_cli(home):
    assert "No trusted registry sources." in cli(home, "--list").stdout
    refused = cli(home, "https://github.com/acme", input="")
    assert refused.returncode == 2 and "--yes" in refused.stderr
    added = cli(home, "https://GitHub.com/acme/", "--yes")
    assert added.returncode == 0, added.stderr
    path = home / ".agentbreeder" / "sources.yaml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert yaml.safe_load(path.read_text()) == {"trusted": ["https://github.com/acme"]}
    whole = cli(home, "https://gitlab.com", "--yes")
    assert whole.returncode == 2 and "whole host" in whole.stderr
    assert cli(home, "https://git.corp.example/x", "--private", "--yes").returncode == 0
    listed = json.loads(cli(home, "--list", "--json").stdout)
    assert listed == [
        {"url": "https://github.com/acme", "private": False, "whole_host": False},
        {"url": "https://git.corp.example/x", "private": True, "whole_host": False},
    ]
    assert cli(home, "https://github.com/acme", "--remove").returncode == 0
    assert cli(home, "https://github.com/acme", "--remove").returncode == 2
    bad = cli(home, "http://github.com/acme", "--yes")
    assert bad.returncode == 2 and "SOURCE_INVALID" in bad.stderr


# -- release's eval set from a source (spec §7 consumers) ----------------------------------------


def test_release_reads_a_remote_eval_set_from_the_lock(server, remote, tmp_path):
    from agentbreeder import release

    remote.version("eval", "golden", "1.0.0", {"cases.yaml": "cases: []\n"})
    commit = remote.commit("eval")
    project = project_dir(tmp_path)
    table = {"acme": record(server, commit)}
    lock = Lock()
    build_refs(project, tmp_path, lock, table, refs=("eval/golden@1.0.0",))
    lock.save(project / "agent.lock")

    class Agent:
        evals = type("E", (), {"ref": "eval/golden@1.0.0"})()

        @staticmethod
        def sources_table():
            return table

    seen = []
    monkey = release.evals.load_eval_set
    try:
        release.evals.load_eval_set = lambda path: seen.append(path) or "loaded"
        server.stop()  # from the cache, checked against the lock
        assert release._eval_set(Agent, project) == "loaded"
    finally:
        release.evals.load_eval_set = monkey

    class Moved(Agent):
        @staticmethod
        def sources_table():
            return {"acme": record(server, remote.first)}

    with pytest.raises(release.ReleaseError, match="LOCK_STALE"):
        release._eval_set(Moved, project)


def test_root_type_is_still_local_only():
    assert Root(Path("x"), "registry").source is None


# -- the agent.lock JSON Schema (docs/agent-lock.schema.json) ------------------------------------


def test_the_lock_schema_matches_the_lock_rules():
    from agentbreeder import lock as lock_module

    root = Path(registry.__file__).resolve().parents[2]
    schema = json.loads((root / "docs" / "agent-lock.schema.json").read_text())
    v1, v2 = schema["oneOf"]
    assert v1["properties"]["lock_version"]["const"] == lock_module.LOCK_VERSION
    assert v2["properties"]["lock_version"]["const"] == lock_module.LOCK_VERSION_SOURCES
    defs = schema["$defs"]
    assert set(defs["localEntry"]["properties"]) == set(lock_module.ENTRY_KEYS)
    assert set(defs["remoteEntry"]["properties"]) == set(lock_module.ENTRY_KEYS_V2)
    assert defs["localEntry"]["properties"]["source"]["maxLength"] == lock_module.SOURCE_LIMIT
    assert set(defs["source"]["properties"]) == set(sources.SOURCE_KEYS)
    assert defs["source"]["properties"]["type"]["enum"] == list(sources.url.TYPES)
    assert set(defs["source"]["properties"]["pin"]["properties"]) == set(
        sources.SOURCE_PINS.values()
    )
    assert set(defs["remoteEntry"]["properties"]["pin"]["properties"]) == set(
        sources.ENTRY_PINS["http"]
    )
    assert defs["sha256"]["pattern"] == f"^{registry.DIGEST.pattern}$"
    assert defs["sourceName"]["pattern"] == f"^{registry.NAME.pattern}$"


def test_registry_trust_in_process(home, monkeypatch, capsys):
    from agentbreeder import cli as cli_module

    monkeypatch.setattr(sys, "stdin", open(os.devnull))
    assert cli_module.main(["registry", "trust"]) == 2
    assert cli_module.main(["registry", "trust", "--list", "https://x.example/a"]) == 2
    assert cli_module.main(["registry", "trust", "https://x.example/a"]) == 2  # no TTY
    assert cli_module.main(["registry", "trust", "https://x.example/a", "--yes"]) == 0
    assert (
        cli_module.main(["registry", "trust", "https://x.example", "--whole-host", "--yes"]) == 0
    )
    monkeypatch.setenv(trust.ENV, "https://y.example/b")
    capsys.readouterr()
    assert cli_module.main(["registry", "trust", "--list"]) == 0
    listed = capsys.readouterr().out
    assert "https://x.example/a" in listed and "[whole_host]" in listed
    assert f"https://y.example/b  ({trust.ENV})" in listed
    assert cli_module.main(["registry", "trust", "https://x.example/a", "--remove"]) == 0
    assert cli_module.main(["registry", "trust", "file:///etc", "--yes"]) == 2
    assert "SOURCE_INVALID" in capsys.readouterr().err

    class Tty:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Tty())
    for answer, code in (("n", 1), ("y", 0)):
        monkeypatch.setattr("builtins.input", lambda prompt, a=answer: a)
        assert cli_module.main(["registry", "trust", "https://z.example/c", "--private"]) == code

    def eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli_module.main(["registry", "trust", "https://w.example/d"]) == 1
    assert trust.file_items(trust.sources_file())[-1] == {
        "url": "https://z.example/c",
        "private": True,
        "whole_host": False,
    }
