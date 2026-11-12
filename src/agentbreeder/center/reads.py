"""Reads of a project's files for the console (spec §8.1 #3): registry versions and their
verification, `agent.yaml`, `agent.lock` and where a version is declared and locked.

In process, with the same functions as the CLI's `--json` commands and only modules that read
files (`registry`, `lock`, `agentfile`): `registry list`'s rows (directory listings only), the
lock and agent file (each read first through `registry.read_project_file`: a regular file, not a
link or FIFO, opened non-blocking and size-capped) and `registry used-by`'s files part
(`registry.usage`). Each answer is cached by a signature of what it reads (inode, ctime, mtime and
size) and dropped when a job or write ends; a write racing a read cannot store an answer older
than the drop (a generation counter).

`registry verify` opens and hashes every file of every version, so it runs as a CLI child with a
timeout on a background thread (`run_cli`), never in this process: a planted FIFO or a huge tree
can only time out that child. Until it answers, a request gets the last answer marked stale, or
VERIFYING; a job's end keeps the last answer (stale) and starts a new run.

Which deployments run a version reads deploy state (`registry_deployed`), so `used-by`'s
deployments part is a CLI child too (server.py). A guard test keeps the modules this process may
load to an allowlist.

Out of scope (spec §10.1, processes running as the owner are trusted): a same-user process
swapping `agent.yaml` or `agent.lock` between the check and `agentfile`/`registry.usage`'s own
read; the project directory and its files are the owner's (S-01).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from agentbreeder import agentfile, lock, registry

MAX_TREE_ENTRIES = 20000  # registry directories signed per request; larger is not cached
MAX_CACHED = 256  # answers kept (distinct used-by refs grow it)
VERIFY_TIMEOUT = 300
VERIFY_TTL = 60.0  # verify reruns at least this often: the listing signature does not see
# edits inside a version, which only verify (hashing every file) does


def envelope(data=None, problem: dict | None = None, stale: bool = False) -> dict:
    return {"data": data, "error": problem, "stale": stale}


def _problem(error: Exception) -> dict:
    code = getattr(error, "code", None) or "READ_FAILED"
    text = str(error)
    prefix = f"[{code}] "
    return {
        "code": code,
        "message": (text[len(prefix) :] if text.startswith(prefix) else text)[:300],
    }


def _stat(path: str, follow: bool = True) -> tuple:
    try:
        info = os.stat(path) if follow else os.lstat(path)
    except OSError:
        return (path, None)
    return (path, info.st_ino, info.st_ctime_ns, info.st_mtime_ns, info.st_size)


def _file_signature(paths: list[Path]) -> tuple:
    return tuple(_stat(str(path), follow=False) for path in paths)


def _registry_signature(roots: list[Path]) -> tuple | None:
    """What `registry.listing` depends on: each root, its kind directories, their name
    directories and the version names under them. Kind and name directories are followed when
    they are links, as `listing` globs through them. None above MAX_TREE_ENTRIES."""
    parts: list = []
    count = 0
    for root in roots:
        parts.append(_stat(str(root)))
        for kind in registry.KINDS:
            kind_dir = os.path.join(root, kind)
            parts.append(_stat(kind_dir, follow=False) + _stat(kind_dir)[1:])
            try:
                names = sorted(os.listdir(kind_dir))
            except OSError:
                continue
            for name in names:
                name_dir = os.path.join(kind_dir, name)
                count += 1
                if count > MAX_TREE_ENTRIES:
                    return None
                parts.append(_stat(name_dir, follow=False) + _stat(name_dir)[1:])
                try:
                    parts.append((name_dir, tuple(sorted(os.listdir(name_dir)))))
                except OSError:
                    continue
    return tuple(parts)


def _lock_entries(raw: bytes | None, name: str) -> dict:
    """The checks of `lock.Lock.load`, on bytes already read safely."""
    if raw is None:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise registry.RegistryError(
            "LOCK_INVALID", f"{name} is not a valid agent.lock"
        ) from error
    if not isinstance(data, dict) or data.get("lock_version") != lock.LOCK_VERSION:
        raise registry.RegistryError("LOCK_INVALID", f"{name}: unsupported lock_version")
    entries = data.get("entries")
    if not isinstance(entries, dict):
        raise registry.RegistryError("LOCK_INVALID", f"{name}: missing entries")
    return entries


class Reads:
    def __init__(self, run_cli: Callable[..., dict] | None = None):
        """`run_cli(argv, timeout=...)` returns a CLI read's envelope (`jobs.Runner.read`)."""
        self.run_cli = run_cli
        self.mutex = threading.Lock()
        self.generation = 0  # bumped when a job or write ends
        self.cache: dict[tuple, tuple] = {}  # key -> (signature, answer)
        self.problems: dict[str, tuple] = {}  # project -> (signature, generation, at, answer)
        self.verifying: set[str] = set()

    def invalidate(self) -> None:
        with self.mutex:
            self.generation += 1
            self.cache.clear()  # verify answers are kept, shown stale until the rerun

    def _cached(self, key: tuple, signature: tuple | None, compute: Callable[[], dict]) -> dict:
        with self.mutex:
            hit = self.cache.get(key)
            if signature is not None and hit and hit[0] == signature:
                return hit[1]
            generation = self.generation
        answer = compute()
        if signature is not None and answer["error"] is None:
            with self.mutex:
                if generation == self.generation:  # no job ended while this read ran
                    while len(self.cache) >= MAX_CACHED:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[key] = (signature, answer)
        return answer

    # -- in process ------------------------------------------------------------------------------

    def registry_versions(self, project: str) -> dict:
        """The same rows as `agentbreeder registry list --project PROJECT --json`."""
        search = registry.roots(Path(project))

        def compute() -> dict:
            try:
                return envelope(registry.listing(search))
            except (registry.RegistryError, OSError) as error:
                return envelope(None, _problem(error))

        return self._cached(("versions", project), _registry_signature(search), compute)

    def agent(self, project: str) -> dict:
        """`agent.yaml` through `agentfile.load_agent_file`, after `read_project_file` checked it
        (a symlinked `agent.yaml` is refused here, as `registry used-by` refuses it)."""
        path = Path(project) / "agent.yaml"

        def compute() -> dict:
            try:
                if registry.read_project_file(path, registry.USAGE_FILE_LIMIT) is None:
                    return envelope(None, {"code": "NO_AGENT_FILE", "message": "No agent.yaml"})
                return envelope(agentfile.load_agent_file(path).model_dump(mode="json"))
            except (agentfile.AgentFileError, registry.RegistryError, OSError) as error:
                return envelope(None, _problem(error))

        return self._cached(("agent", project), _file_signature([path]), compute)

    def lock(self, project: str) -> dict:
        """`agent.lock`'s entries, parsed as `lock.Lock.load` does, from bytes read with
        `registry.read_project_file` (regular file, non-blocking, at most 1 MiB); an absent lock
        is an empty one."""
        path = Path(project) / "agent.lock"

        def compute() -> dict:
            try:
                raw = registry.read_project_file(path, registry.LOCK_FILE_LIMIT)
                return envelope(_lock_entries(raw, path.name))
            except (registry.RegistryError, OSError) as error:
                return envelope(None, _problem(error))

        return self._cached(("lock", project), _file_signature([path]), compute)

    def used_by(self, project: str, ref: str) -> dict:
        """Where PROJECT declares and locks REF: `registry.usage`, the in-process half of
        `agentbreeder registry used-by REF --project PROJECT --json`."""
        directory = Path(project)
        try:
            parsed = registry.Ref.parse(ref)
        except registry.RegistryError as error:
            return envelope(None, _problem(error))
        names = ("agent.yaml", "stack.yaml", "agent.lock", "stack.lock")
        signature = _file_signature([directory / name for name in names])

        def compute() -> dict:
            try:
                return envelope(registry.usage(parsed, directory))
            except (registry.RegistryError, OSError, RecursionError) as error:
                return envelope(None, _problem(error))

        return self._cached(("used-by", project, str(parsed)), signature, compute)

    # -- verify: a CLI child off the request thread ----------------------------------------------

    def registry_problems(self, project: str) -> dict:
        """The same rows as `agentbreeder registry verify --project PROJECT --json`, from a CLI
        child on a background thread: until it answers, the last answer marked stale, or
        VERIFYING."""
        signature = _registry_signature(registry.roots(Path(project)))
        now = time.monotonic()
        with self.mutex:
            entry = self.problems.get(project)
            fresh = (
                entry is not None
                and entry[1] == self.generation
                and entry[0] == signature
                and now - entry[2] < VERIFY_TTL
            )
            if fresh:
                return entry[3]
            if project not in self.verifying:
                self.verifying.add(project)
                threading.Thread(
                    target=self._verify,
                    args=(project, signature, self.generation),
                    daemon=True,
                ).start()
        if entry is not None:
            return {**entry[3], "stale": True}
        return envelope(None, {"code": "VERIFYING", "message": "Verifying the registries"})

    def _verify(self, project: str, signature, generation: int) -> None:
        answer = envelope(None, {"code": "VERIFY_FAILED", "message": "Verification failed"})
        try:
            if self.run_cli is None:
                answer = envelope(None, {"code": "VERIFY_UNAVAILABLE", "message": "No CLI"})
            else:
                answer = self.run_cli(
                    ["registry", "verify", "--project", project, "--json"],
                    timeout=VERIFY_TIMEOUT,
                    ok_codes=(0, 2),  # it exits 2 after printing the problems it found
                    cache=False,
                    track=True,  # stopped with the console, even when blocked on a file
                )
                answer = {**answer, "stale": False}
        except Exception as error:  # a verify failure must never leave VERIFYING for good
            answer = envelope(None, {"code": "VERIFY_FAILED", "message": type(error).__name__})
        finally:
            with self.mutex:
                self.verifying.discard(project)
                # Kept even when it failed: verify reruns when the listing changes, on a job's
                # end, or after VERIFY_TTL, not on every request.
                self.problems[project] = (signature, generation, time.monotonic(), answer)
