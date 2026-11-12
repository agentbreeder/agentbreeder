"""In-process reads of a project's files (spec §8.1 #3): registry versions, `agent.lock` and
`agent.yaml`.

The server calls the same functions as the CLI's `--json` commands (`registry list`, `registry
verify`), but only modules that read files: `registry`, `lock`, `agentfile`. Anything that
touches deployment state, a stack database, secrets, Docker, the operator config or the audit
chain stays a CLI child (`jobs.Runner.read`); a guard test checks that the server process never
loads those modules. Answers are cached by the (path, mtime, size) of what they read and dropped
when a job or write ends. `registry verify` hashes every version, so it runs off the request
thread: a request gets the last answer (marked stale) or "verifying" until it is ready.

`used_by` gives what `registry used-by` reads from files (declared in `agent.yaml` or
`stack.yaml`, locked in `agent.lock` or `stack.lock`; `registry.usage`). Which deployments run a
version reads local deploy state (`registry_deployed`), so it stays a CLI child.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

from agentbreeder import agentfile, lock, registry

MAX_TREE_ENTRIES = 20000  # a registry larger than this is not cached by signature


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


def _signature(paths: list[Path]) -> tuple:
    """What a read depends on: each path's (mtime, size), and for a directory every entry under
    it. None when a tree is too large to sign cheaply (the answer is then not cached)."""
    parts: list = []
    count = 0
    for path in paths:
        try:
            info = os.stat(path)
        except OSError:
            parts.append((str(path), None))
            continue
        parts.append((str(path), info.st_mtime_ns, info.st_size))
        if not os.path.isdir(path):
            continue
        for directory, names, files in os.walk(path):
            names.sort()
            for name in sorted(files) + names:
                count += 1
                if count > MAX_TREE_ENTRIES:
                    return None
                try:
                    entry = os.lstat(os.path.join(directory, name))
                except OSError:
                    continue
                parts.append((directory, name, entry.st_mtime_ns, entry.st_size))
    return tuple(parts)


def _lock_entries(raw: bytes | None, name: str) -> dict:
    """The checks of `lock.Lock.load`, on bytes already read safely."""
    if raw is None:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
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
    def __init__(self):
        self.mutex = threading.Lock()
        self.cache: dict[tuple, tuple[tuple, dict]] = {}  # (kind, project) -> (signature, answer)
        self.verifying: set[str] = set()

    def invalidate(self) -> None:
        with self.mutex:
            self.cache.clear()

    def _cached(self, key: tuple, paths: list[Path], compute: Callable[[], dict]) -> dict:
        signature = _signature(paths)
        with self.mutex:
            hit = self.cache.get(key)
            if signature is not None and hit and hit[0] == signature:
                return hit[1]
        answer = compute()
        if signature is not None and answer["error"] is None:
            with self.mutex:
                self.cache[key] = (signature, answer)
        return answer

    # -- the reads -------------------------------------------------------------------------------

    def registry_versions(self, project: str) -> dict:
        """The same rows as `agentbreeder registry list --project PROJECT --json`."""
        search = registry.roots(Path(project))

        def compute() -> dict:
            try:
                return envelope(registry.listing(search))
            except (registry.RegistryError, OSError) as error:
                return envelope(None, _problem(error))

        return self._cached(("versions", project), search, compute)

    def registry_problems(self, project: str) -> dict:
        """The same rows as `agentbreeder registry verify --project PROJECT --json`, computed on
        a background thread: until it is ready, the last answer marked stale, or VERIFYING."""
        search = registry.roots(Path(project))
        signature = _signature(search)
        key = ("problems", project)
        with self.mutex:
            hit = self.cache.get(key)
            if (
                hit
                and hit[0] == signature
                and (signature is not None or project in self.verifying)
            ):
                return hit[1]
            if project not in self.verifying:
                self.verifying.add(project)
                threading.Thread(
                    target=self._verify, args=(project, search, signature), daemon=True
                ).start()
        if hit:
            return {**hit[1], "stale": True}
        return envelope(None, {"code": "VERIFYING", "message": "Verifying the registries"})

    def _verify(self, project: str, search: list[Path], signature) -> None:
        try:
            answer = envelope(registry.verify(search))
        except (registry.RegistryError, OSError) as error:
            answer = envelope(None, _problem(error))
        with self.mutex:
            self.verifying.discard(project)
            # Kept even when it failed: verify reruns when the tree changes or on invalidate,
            # not on every request.
            self.cache[("problems", project)] = (signature, answer)

    def agent(self, project: str) -> dict:
        """`agent.yaml` as the build reads it (`agentfile.load_agent_file`). First checked with
        `registry.read_project_file` (a regular file, not a link or FIFO, opened non-blocking,
        size-capped), so a planted file cannot hang or bloat the long-running server."""
        path = Path(project) / "agent.yaml"

        def compute() -> dict:
            try:
                if registry.read_project_file(path, registry.USAGE_FILE_LIMIT) is None:
                    return envelope(None, {"code": "NO_AGENT_FILE", "message": "No agent.yaml"})
                return envelope(agentfile.load_agent_file(path).model_dump(mode="json"))
            except (agentfile.AgentFileError, registry.RegistryError, OSError) as error:
                return envelope(None, _problem(error))

        return self._cached(("agent", project), [path], compute)

    def used_by(self, project: str, ref: str) -> dict:
        """Where PROJECT declares and locks REF: `registry.usage`, the in-process half of
        `agentbreeder registry used-by REF --project PROJECT --json`."""
        directory = Path(project)
        try:
            parsed = registry.Ref.parse(ref)
        except registry.RegistryError as error:
            return envelope(None, _problem(error))
        paths = [directory / name for name in ("agent.yaml", "stack.yaml", "agent.lock")]
        paths.append(directory / "stack.lock")

        def compute() -> dict:
            try:
                return envelope(registry.usage(parsed, directory))
            except (registry.RegistryError, OSError) as error:
                return envelope(None, _problem(error))

        return self._cached(("used-by", project, str(parsed)), paths, compute)

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

        return self._cached(("lock", project), [path], compute)
