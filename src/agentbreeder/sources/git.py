"""Git sources at a commit (#829 spec §6.1): a hardened `git` CLI fetcher.

Each fetch runs in a fresh private bare repository (`mkdtemp`, 0700) that is deleted afterwards,
so no git repository and no git `config` persists in the cache. Versions are read without a
checkout: `ls-tree` lists a version's entries and `cat-file --batch` reads its blobs, which are
written `O_EXCL | O_NOFOLLOW` into a private staging directory. Only regular files (modes 100644
and 100755) are read: symlinks, submodules and Git LFS pointer files are ENTRY_UNSAFE.

Every invocation: argv only (no shell), cwd an empty private directory, `--git-dir` always,
`GIT_CEILING_DIRECTORIES` set, the environment rebuilt from an allow-list (every other `GIT_*`
variable dropped) and the `-c` overrides in `CONFIG`, which win over system and global config.
The user's own credential helpers and ssh config still apply (that is how private repositories
work). The trust check (`sources.trust.check`) runs before every network call, and git over
https is pinned to the checked address (`http.curloptResolve`).
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path

from agentbreeder import registry
from agentbreeder.registry import KINDS, NAME, VERSION, Ref, RegistryError, _shown
from agentbreeder.sources import trust

FETCH_TIMEOUT = 120.0  # seconds per source, every fetch attempt together
LOCAL_TIMEOUT = 60.0  # ls-tree and cat-file read local objects only
DOWNLOAD_CAP = 200 * 1024 * 1024  # bytes in the temporary repository
POLL = 0.25
STDERR_LIMIT = 300
LISTING_LIMIT = 16 * 1024 * 1024  # `ls-tree` of a whole source (tree bombs stop here)
VERSION_LISTING_LIMIT = 4 * 1024 * 1024  # `ls-tree` of one version (MAX_FILES entries)
LOCAL_OUTPUT_LIMIT = 1024 * 1024  # any other local git command
STDERR_CAP = 1024 * 1024
MIN_GIT = (2, 37)  # http.curloptResolve (the DNS pin) is ignored by older git
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"

CONFIG = (
    "protocol.allow=never",
    "protocol.https.allow=always",
    "protocol.ssh.allow=always",
    "http.followRedirects=false",
    "http.sslVerify=true",
    "credential.interactive=false",
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "core.sshCommand=ssh -o BatchMode=yes",
    "fetch.fsckObjects=true",
    "transfer.fsckObjects=true",
    "submodule.recurse=false",
    "fetch.recurseSubmodules=false",
    "gc.auto=0",
    "maintenance.auto=false",
    "init.templateDir=",
)
ENV_ALLOW = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "TMPDIR",
        "SSH_AUTH_SOCK",
        "SSL_CERT_FILE",
        "GIT_SSL_CAINFO",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
        "no_proxy",
    }
)
_REFUSED_SHA = (
    "not our ref",
    "unadvertised object",
    "no such remote ref",
    "couldn't find remote ref",
)
# Matched against stderr with git's own messages in English (environment() sets LC_MESSAGES=C):
# "Authentication failed for", "no such remote ref", "unadvertised object" and "couldn't find
# remote ref" are gettext-translated otherwise. "not our ref" is the server's upload-pack text.
_AUTH = (
    "authentication failed",
    # git >= 2.47 honours credential.interactive=false: no prompt, then this die() in
    # credential_fill(). Older git ignores the key and prints "terminal prompts disabled".
    "unable to get password from user",
    "permission denied (publickey",
    "could not read username",
    "could not read password",
    "returned error: 401",
    "returned error: 403",
    "terminal prompts disabled",
)
# Everything between `://` and the last `@` before whitespace: a proxy password may hold `/`.
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)\S*@")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def environment(home: Path | None = None) -> dict[str, str]:
    """The child environment: the allow-list (spec §6.1) plus fixed git settings."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key in ENV_ALLOW or key.startswith("LC_")
    }
    # Git's messages in English, so the stderr markers above match in any locale. LC_ALL would
    # override LC_MESSAGES, so its value moves to LC_CTYPE (the character set it chose).
    lc_all = env.pop("LC_ALL", None)
    if lc_all and "LC_CTYPE" not in env:
        env["LC_CTYPE"] = lc_all
    env.update(
        {
            "LC_MESSAGES": "C",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LITERAL_PATHSPECS": "1",
            "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
        }
    )
    if home is not None:
        env["GIT_CEILING_DIRECTORIES"] = str(home)
    return env


def clean_stderr(text: bytes | str) -> str:
    """Git's stderr for a message: decoded, URL userinfo redacted, control characters
    stripped, one line, bounded."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = _URL_USERINFO.sub(r"\1***@", text)
    text = " ".join(_CONTROL.sub(" ", text).split())
    return registry.clean(text[:STDERR_LIMIT])[0]


def git_binary() -> str:
    found = shutil.which("git")
    if found is None:
        raise RegistryError(
            "SOURCE_TOOL_MISSING", "git is not installed; install git to use git registry sources"
        )
    return found


def _check_version(binary: str) -> None:
    """git 2.37 or newer: older git silently ignores `http.curloptResolve`, so the checked
    address would not be pinned (spec §8.3)."""
    try:
        text = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            timeout=10,
            check=False,
            env=environment(None),
        ).stdout.decode("ascii", "replace")
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    found = re.match(r"git version (\d+)\.(\d+)", text)
    if found is None or (int(found[1]), int(found[2])) < MIN_GIT:
        raise RegistryError(
            "SOURCE_TOOL_MISSING",
            "git registry sources need git 2.37 or newer (found: "
            f"{clean_stderr(text) or 'unknown'})",
        )


def _size(path: Path) -> int:
    total = 0
    for directory, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                pass
    return total


class Repo:
    """One fresh private bare repository, deleted on `close`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.git = git_binary()
        self.home = Path(tempfile.mkdtemp(prefix="agentbreeder-git-"))
        os.chmod(self.home, 0o700)
        self.cwd = self.home / "cwd"
        self.cwd.mkdir(mode=0o700)
        self.dir = self.home / "repo.git"
        self.commit: str | None = None
        self._calls = 0
        try:
            _check_version(self.git)
            self._run(["init", "--quiet", "--bare", "--template=", str(self.dir)], git_dir=False)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)

    def __enter__(self) -> Repo:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _argv(self, args: list[str], extra: tuple[str, ...] = (), git_dir: bool = True) -> list:
        argv = [self.git]
        if git_dir:
            argv.append(f"--git-dir={self.dir}")
        for item in (*CONFIG, *extra):
            argv += ["-c", item]
        return argv + args

    def _spawn(
        self,
        args: list[str],
        *,
        extra: tuple[str, ...] = (),
        input: bytes | None = None,
        git_dir: bool = True,
        limit: int,
        deadline: float,
        watch: bool = False,
    ) -> tuple[int, bytes, bytes, str | None]:
        """Run git in its own process group with stdout and stderr in files in the private
        temporary directory, never in memory. The group is killed when stdout passes `limit`,
        stderr passes STDERR_CAP, the deadline passes or (`watch`) the repository passes the
        download cap. Returns (exit code, stdout, stderr head, reason it was killed or None)."""
        self._calls += 1
        out_path = self.home / f"out-{self._calls}"
        err_path = self.home / f"err-{self._calls}"
        in_path = self.home / f"in-{self._calls}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        for path in (out_path, err_path, in_path):
            os.close(os.open(path, flags, 0o600))
        if input is not None:
            in_path.write_bytes(input)
        try:
            with (
                open(in_path, "rb") as in_file,
                open(out_path, "wb") as out_file,
                open(err_path, "wb") as err_file,
            ):
                process = subprocess.Popen(
                    self._argv(args, extra, git_dir=git_dir),
                    cwd=self.cwd,
                    env=environment(self.home),
                    stdin=in_file,
                    stdout=out_file,
                    stderr=err_file,
                    start_new_session=True,
                )
            reason = None
            while True:
                try:
                    process.wait(timeout=POLL)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() > deadline:
                        reason = "SOURCE_TIMEOUT"
                    elif os.path.getsize(out_path) > limit:
                        reason = "OUTPUT"
                    elif os.path.getsize(err_path) > STDERR_CAP:
                        reason = "OUTPUT"
                    elif watch and _size(self.dir) > DOWNLOAD_CAP:
                        reason = "SOURCE_TOO_LARGE"
                    if reason:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):  # exited (macOS: EPERM)
                            pass
                        process.wait()
                        break
            if reason is None and os.path.getsize(out_path) > limit:
                reason = "OUTPUT"
            if reason is None and watch and _size(self.dir) > DOWNLOAD_CAP:
                reason = "SOURCE_TOO_LARGE"
            with open(err_path, "rb") as handle:
                err = handle.read(STDERR_CAP)
            out = b"" if reason else out_path.read_bytes()
            return process.returncode, out, err, reason
        finally:
            for path in (out_path, err_path, in_path):
                path.unlink(missing_ok=True)

    def _run(
        self,
        args: list[str],
        *,
        input: bytes | None = None,
        git_dir: bool = True,
        check: bool = True,
        limit: int = LOCAL_OUTPUT_LIMIT,
    ) -> subprocess.CompletedProcess:
        """A local-only git command (no network): bounded in time and in output."""
        code, out, err, reason = self._spawn(
            args,
            input=input,
            git_dir=git_dir,
            limit=limit,
            deadline=time.monotonic() + LOCAL_TIMEOUT,
        )
        if reason == "SOURCE_TIMEOUT":
            raise RegistryError(
                "SOURCE_TIMEOUT", f"source '{_shown(self.name)}': git {args[0]} timed out"
            )
        if reason == "OUTPUT":
            raise RegistryError(
                "SOURCE_TOO_LARGE",
                f"source '{_shown(self.name)}': git {args[0]} output passed its limit "
                "(too many entries)",
            )
        if check and code != 0:
            raise RegistryError(
                "SOURCE_INVALID",
                f"source '{_shown(self.name)}': git {args[0]} failed: {clean_stderr(err)}",
            )
        return subprocess.CompletedProcess(args, code, out, err)

    def _network(self, args: list[str], checked: trust.Checked, deadline: float) -> tuple:
        """A fetch, in its own process group: killed when the repository passes the download
        cap or the deadline passes (spec §6.1). Returns (exit code, stderr)."""
        extra: tuple[str, ...] = ()
        if checked.url.startswith("https://") and checked.addresses != (checked.host,):
            # Pin a host name to the addresses that passed the check (DNS rebinding); curl
            # tries them in order. An IP literal host needs no DNS, so nothing to pin.
            pinned = ",".join(f"[{a}]" if ":" in a else a for a in checked.addresses)
            extra = (f"http.curloptResolve={checked.host}:{checked.port}:{pinned}",)
        code, _, stderr, reason = self._spawn(
            args, extra=extra, limit=STDERR_CAP, deadline=deadline, watch=True
        )
        if reason == "SOURCE_TIMEOUT":
            raise RegistryError(
                reason,
                f"source '{_shown(self.name)}' (git, {checked.host}): the fetch took longer "
                f"than {int(FETCH_TIMEOUT)} s",
            )
        if reason == "SOURCE_TOO_LARGE":
            raise RegistryError(
                "SOURCE_TOO_LARGE",
                f"source '{_shown(self.name)}' (git, {checked.host}): the fetch passed the "
                f"{DOWNLOAD_CAP // (1024 * 1024)} MB download cap",
            )
        if reason == "OUTPUT":
            raise RegistryError(
                "SOURCE_UNREACHABLE",
                f"source '{_shown(self.name)}' (git, {checked.host}): the server sent too much "
                "progress output; the fetch was stopped",
            )
        return code, stderr

    def fetch(self, url: str, commit: str) -> None:
        """Fetch exactly `commit` from `url` (canonical), or SOURCE_* errors (spec §6.1, §12).

        First `fetch --depth 1` by SHA; if the server refuses that (protocol v0 servers without
        allowReachableSHA1InWant), once a full fetch of branches and tags, then the commit must
        be present (SOURCE_COMMIT_MISSING). The trust check runs before each network call."""
        deadline = time.monotonic() + FETCH_TIMEOUT
        base = ["fetch", "--quiet", "--no-tags", "--no-write-fetch-head"]
        base.append("--no-recurse-submodules")
        checked = trust.check(self.name, url)
        code, stderr = self._network([*base, "--depth", "1", "--", url, commit], checked, deadline)
        if code != 0:
            text = clean_stderr(stderr)
            if not any(marker in text.lower() for marker in _REFUSED_SHA):
                raise self._failure(url, checked, text)
            checked = trust.check(self.name, url)
            refspecs = ["+refs/heads/*:refs/ab/heads/*", "+refs/tags/*:refs/ab/tags/*"]
            code, stderr = self._network([*base, "--", url, *refspecs], checked, deadline)
            if code != 0:
                raise self._failure(url, checked, clean_stderr(stderr))
        present = self._run(["cat-file", "-e", f"{commit}^{{commit}}"], check=False)
        if present.returncode != 0:
            raise RegistryError(
                "SOURCE_COMMIT_MISSING",
                f"source '{_shown(self.name)}': commit {commit[:7]} is no longer on {url} "
                "(deleted branch or force-push). Machines that already fetched it still build "
                f"(--offline). Pin a commit that exists (find one with: git ls-remote {url})",
            )
        self.commit = commit

    def _failure(self, url: str, checked: trust.Checked, text: str) -> RegistryError:
        lowered = text.lower()
        where = f"source '{_shown(self.name)}' (git, {checked.host})"
        if any(marker in lowered for marker in _AUTH):
            return RegistryError(
                "SOURCE_AUTH_FAILED",
                f"{where}: authentication failed. AgentBreeder uses your own git credentials; "
                f"check that 'git ls-remote {url}' works in this shell",
            )
        if "returned error: 30" in lowered:
            text = "the server redirected; redirects are not followed, use the final URL"
        return RegistryError(
            "SOURCE_UNREACHABLE",
            f"{where}: could not fetch ({text}). Already fetched? Re-run with --offline. "
            "Otherwise check the network or VPN. No longer used? Remove it from agent.yaml.",
        )

    def _prefix(self, path: str) -> str:
        return "" if path == "." else path + "/"

    def listing(self, path: str) -> set[str]:
        """The `kind/name@version` refs present at the fetched commit under `path`, from tree
        names only (nothing is read, hashed or checked until a build asks for a version)."""
        assert self.commit is not None
        args = ["ls-tree", "-r", "-d", "-z", "--name-only", "--full-tree", self.commit]
        if path != ".":
            args += ["--", path]
        out = self._run(args, limit=LISTING_LIMIT).stdout
        prefix = self._prefix(path)
        refs = set()
        for raw in out.split(b"\0"):
            name = raw.decode("utf-8", "replace")
            if not name.startswith(prefix):
                continue
            parts = name[len(prefix) :].split("/")
            if (
                len(parts) == 3
                and parts[0] in KINDS
                and NAME.fullmatch(parts[1])
                and VERSION.fullmatch(parts[2])
            ):
                refs.add(f"{parts[0]}/{parts[1]}@{parts[2]}")
        return refs

    def extract(self, path: str, ref: Ref, staging: Path) -> Path:
        """Write `ref`'s files at the fetched commit into `staging`/kind/name/version and return
        that directory. Refuses symlinks, submodules, LFS pointers, unsafe names and versions
        over registry.MAX_FILES / MAX_BYTES before any blob is read."""
        assert self.commit is not None
        version = f"{self._prefix(path)}{ref.kind}/{ref.name}/{ref.version}"
        args = ["ls-tree", "-r", "-z", "-l", "--full-tree", self.commit, "--", version]
        try:
            out = self._run(args, limit=VERSION_LISTING_LIMIT).stdout
        except RegistryError as error:
            if error.code != "SOURCE_TOO_LARGE":
                raise
            raise RegistryError("ENTRY_UNSAFE", f"{ref}: registry version is too large") from None
        files: list[tuple[str, str, bool, int]] = []
        seen: set[str] = set()  # names folded as a case-insensitive filesystem would
        total = 0
        for record in out.split(b"\0"):
            if not record:
                continue
            meta, _, raw_name = record.partition(b"\t")
            try:
                name = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                raise RegistryError("ENTRY_UNSAFE", f"{ref}: a file name is not UTF-8") from None
            mode, kind, sha, size = meta.decode("ascii", "replace").split()
            if not name.startswith(version + "/"):
                raise RegistryError("REF_NOT_FOUND", f"{ref} is not a directory in the source")
            relative = name[len(version) + 1 :]
            if mode == "120000":
                raise RegistryError(
                    "ENTRY_UNSAFE", f"symlinks are not allowed: {_shown(relative)}"
                )
            if mode == "160000" or kind == "commit":
                raise RegistryError(
                    "ENTRY_UNSAFE", f"submodules are not allowed: {_shown(relative)}"
                )
            if mode not in ("100644", "100755") or kind != "blob":
                raise RegistryError(
                    "ENTRY_UNSAFE", f"only files and directories: {_shown(relative)}"
                )
            parts = relative.split("/")
            for part in parts:
                if part in ("", ".", "..") or "\\" in part:
                    raise RegistryError("ENTRY_UNSAFE", f"unsafe name: {_shown(relative)}")
                registry._safe_name(part)
            if "__pycache__" in parts[:-1] or parts[-1].endswith((".pyc", ".pyo")):
                continue  # interpreter caches are never part of a version (registry._entries)
            folded = unicodedata.normalize("NFC", relative).casefold()
            if folded in seen:
                raise RegistryError(
                    "ENTRY_UNSAFE",
                    f"two files differ only by case or Unicode form: {_shown(relative)}",
                )
            seen.add(folded)
            total += int(size)
            files.append((relative, sha, mode == "100755", int(size)))
            if len(files) > registry.MAX_FILES or total > registry.MAX_BYTES:
                raise RegistryError("ENTRY_UNSAFE", f"{ref}: registry version is too large")
        if not files:
            raise RegistryError("REF_NOT_FOUND", f"{ref} is not in source '{_shown(self.name)}'")
        target = staging / ref.kind / ref.name / ref.version
        target.mkdir(parents=True, mode=0o700)
        batch = self._run(
            ["cat-file", "--batch"],
            input=b"".join(f"{sha}\n".encode() for _, sha, _, _ in files),
            limit=total + 128 * len(files),
        ).stdout
        offset = 0
        for relative, sha, executable, size in files:
            end = batch.find(b"\n", offset)
            header = batch[offset:end].split() if end >= 0 else []
            if header != [sha.encode(), b"blob", str(size).encode()]:
                raise RegistryError("SOURCE_INVALID", f"{ref}: git returned an unexpected object")
            content = batch[end + 1 : end + 1 + size]
            offset = end + 2 + size
            if content.startswith(LFS_POINTER):
                raise RegistryError(
                    "ENTRY_UNSAFE",
                    f"LFS files are not supported in registry versions: {_shown(relative)}",
                )
            try:
                _write(target, relative, content, executable)
            except OSError as error:
                raise RegistryError(
                    "ENTRY_UNSAFE",
                    f"cannot write {_shown(relative)}: {error.strerror or error}",
                ) from None
        return target


def _write(root: Path, relative: str, content: bytes, executable: bool) -> None:
    """Create parent directories (never through a link) and the file with O_EXCL|O_NOFOLLOW."""
    current = root
    parts = relative.split("/")
    for part in parts[:-1]:
        current = current / part
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            if not stat.S_ISDIR(os.lstat(current).st_mode):
                raise RegistryError(
                    "ENTRY_UNSAFE", f"a file and a directory share a name: {_shown(relative)}"
                ) from None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(current / parts[-1], flags, 0o755 if executable else 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
