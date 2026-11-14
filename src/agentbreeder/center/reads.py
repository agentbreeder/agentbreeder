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
CHILD_TIMEOUT = 60  # `registry show` and `registry diff` (both capped in size and work)
CHILD_TTL = 60.0  # their cached answers are read again at least this often
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


MCP_IDENTITY = ("ref", "origin", "upstream", "token_url")  # `mcp_stack.McpEntry.identity`


def _load_stack(path: Path) -> tuple:
    """`(stack, None)` or `(None, problem)`: stack.yaml through `read_project_file` and the same
    model as `services` (`services.schema.Stack`). Values are never echoed in a problem."""
    from agentbreeder import strict_yaml
    from agentbreeder.services import schema

    try:
        raw = registry.read_project_file(path, schema.MAX_BYTES)
        if raw is None:
            return None, {"code": "NO_STACK_FILE", "message": "No stack.yaml"}
        return schema.Stack.model_validate(strict_yaml.load_text(raw.decode("utf-8"))), None
    except (registry.RegistryError, OSError) as error:
        return None, _problem(error)
    except (strict_yaml.StrictYamlError, UnicodeDecodeError) as error:
        return None, {"code": "STACK_INVALID", "message": f"stack.yaml: {error}"[:300]}
    except ValueError as error:  # pydantic's ValidationError; values are never echoed
        problems = schema._problems(error) if hasattr(error, "errors") else ""
        return None, {"code": "STACK_INVALID", "message": f"stack.yaml: {problems}"[:300]}


def _stack_lock_mcp(raw: bytes | None, name: str) -> dict:
    """stack.lock's `mcp` records, with the checks of `services.lock.StackLock.load` (and
    `lock.Lock.load` before it), on bytes already read safely. An absent lock (no `services up`
    yet) has none."""
    if raw is None:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise registry.RegistryError(
            "LOCK_INVALID", f"{name} is not a valid stack.lock"
        ) from error
    if not isinstance(data, dict) or data.get("lock_version") != lock.LOCK_VERSION:
        raise registry.RegistryError("LOCK_INVALID", f"{name}: unsupported lock_version")
    if not isinstance(data.get("entries"), dict):
        raise registry.RegistryError("LOCK_INVALID", f"{name}: missing entries")
    imports = data.get("imports", {})
    if not isinstance(imports, dict) or not all(
        isinstance(names, list) and all(isinstance(item, str) for item in names)
        for names in imports.values()
    ):
        raise registry.RegistryError("LOCK_INVALID", f"{name}: imports must map services to names")
    mcp = data.get("mcp", {})
    if not isinstance(mcp, dict) or not all(
        isinstance(entry, dict)
        and isinstance(entry.get("tools", {}), dict)
        and all(
            isinstance(tool, str) and isinstance(digest, str) and digest.startswith("sha256:")
            for tool, digest in entry.get("tools", {}).items()
        )
        and all(entry.get(key) is None or isinstance(entry.get(key), str) for key in MCP_IDENTITY)
        for entry in mcp.values()
    ):
        raise registry.RegistryError("LOCK_INVALID", f"{name}: mcp must map servers to pins")
    return mcp


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

    def stack_agents(self, project: str) -> dict:
        """What `stack.yaml` declares for each agent of its environment (`environment.agents`:
        sponsor, review date, whether release tests a candidate), parsed with the same model as
        `services` (`services.schema.Stack`) from bytes read through `read_project_file`. The
        identity strip shows this until #909 adds the principal to `list --json`."""
        path = Path(project) / "stack.yaml"

        def compute() -> dict:
            stack, problem = _load_stack(path)
            if problem is not None:
                return envelope(None, problem)
            agents = {
                name: {
                    "sponsor": entry.sponsor,
                    "review_by": entry.review_by,
                    "candidate": entry.candidate,
                }
                for name, entry in stack.environment.agents.items()
            }
            return envelope({"stack": stack.name, "agents": agents})

        return self._cached(("stack_agents", project), _file_signature([path]), compute)

    def stack_mcp(self, project: str) -> dict:
        """The MCP screen's files part (#831, M15): each `mcp:` server of `stack.yaml` with what
        `stack.lock` recorded for it (ref, origin, upstream origin, token host and the pinned tool
        hashes), and each agent's grants (`environment.agents.AGENT.mcp`). Parsed with the same
        model as `services`; the lock with `services.lock.StackLock.load`'s checks. Relay state
        and upstream drift come from `services status --json` (a CLI child), not from here."""
        directory = Path(project)
        paths = [directory / "stack.yaml", directory / "stack.lock"]

        def compute() -> dict:
            stack, problem = _load_stack(paths[0])
            if problem is not None:
                return envelope(None, problem)
            try:
                raw = registry.read_project_file(paths[1], registry.LOCK_FILE_LIMIT)
                recorded = _stack_lock_mcp(raw, paths[1].name)
            except (registry.RegistryError, OSError) as error:
                return envelope(None, _problem(error))
            servers = []
            for name, entry in sorted(stack.mcp.items()):
                locked = recorded.get(name)
                identity = (
                    {key: locked.get(key) for key in MCP_IDENTITY} if locked is not None else None
                )
                servers.append(
                    {
                        "name": name,
                        "ref": entry.ref,
                        "relay": f"mcp-{name}",
                        "locked": identity,
                        # A changed ref is pinned again (with consent) at the next `services up`.
                        # A changed origin or upstream is too, but needs the registry to see.
                        "lock_current": identity is not None and identity["ref"] == entry.ref,
                        "pins": dict(sorted((locked or {}).get("tools", {}).items()))
                        if identity is not None and identity["ref"] == entry.ref
                        else {},
                    }
                )
            agents = [
                {
                    "name": name,
                    "grants": {server: list(tools) for server, tools in sorted(entry.mcp.items())},
                }
                for name, entry in sorted(stack.environment.agents.items())
            ]
            return envelope({"stack": stack.name, "servers": servers, "agents": agents})

        return self._cached(("stack_mcp", project), _file_signature(paths), compute)

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

    def roots(self, project: str) -> list[dict]:
        """The search roots in resolve order, each with where it comes from (`registry
        show`'s `root_kind`: project, path or builtin) and whether it exists."""
        directory = Path(project)
        return [
            {
                "path": str(root),
                "kind": registry.root_kind(root, directory),
                "present": root.is_dir(),
            }
            for root in registry.roots(directory)
        ]

    def in_use(self, project: str) -> dict:
        """Every listed version this project declares or locks, as {ref: {declared: [file],
        locked: [file]}}: `registry.usage` per version (the same answer as `used-by`'s files
        part), read once per change of the project's files or registries."""
        directory = Path(project)
        search = registry.roots(directory)
        tree = _registry_signature(search)
        names = ("agent.yaml", "stack.yaml", "agent.lock", "stack.lock")
        files = _file_signature([directory / name for name in names])
        signature = None if tree is None else (tree, files)

        def compute() -> dict:
            used: dict[str, dict] = {}
            problems: list[dict] = []
            try:
                refs = sorted({row["ref"] for row in registry.listing(search)})
                for text in refs:
                    row = registry.usage(registry.Ref.parse(text), directory)
                    if row["errors"] and not problems:
                        problems = row["errors"]
                    declared = sorted({entry["file"] for entry in row["declared"]})
                    locked = sorted({entry["file"] for entry in row["locked"]})
                    if declared or locked:
                        used[text] = {"declared": declared, "locked": locked}
            except (registry.RegistryError, OSError, RecursionError) as error:
                return envelope(None, _problem(error))
            return envelope({"refs": used, "errors": problems})

        return self._cached(("in-use", project), signature, compute)

    # -- show and diff: CLI children, cached until the registries change -------------------------

    def show(self, project: str, ref: registry.Ref) -> dict:
        """`registry show REF --project PROJECT --json`. A child, not in process: it opens and
        hashes every file of the version. Cached by the registry listing's signature, for at
        most CHILD_TTL seconds (an edit inside a version changes no listing), and dropped when a
        job ends."""
        argv = ["registry", "show", str(ref), "--project", project, "--json"]
        return self._child(("show", project, str(ref)), project, argv)

    def diff(self, project: str, first: registry.Ref, second: registry.Ref) -> dict:
        """`registry diff FROM TO --project PROJECT --json`, as `show`."""
        argv = ["registry", "diff", str(first), str(second), "--project", project, "--json"]
        return self._child(("diff", project, str(first), str(second)), project, argv)

    def _child(self, key: tuple, project: str, argv: list[str]) -> dict:
        # The listing signature does not see an edit inside a version, which only verify finds;
        # the time window makes a cached answer read again at least every CHILD_TTL seconds.
        tree = _registry_signature(registry.roots(Path(project)))
        window = int(time.monotonic() // CHILD_TTL)
        signature = None if tree is None else (tree, window)

        def compute() -> dict:
            if self.run_cli is None:
                return envelope(None, {"code": "READ_UNAVAILABLE", "message": "No CLI"})
            # Uncached in the runner: a failed read must not come back as another version's
            # last good answer; this layer keeps only successful answers.
            return self.run_cli(argv, timeout=CHILD_TIMEOUT, cache=False)

        return self._cached(key, signature, compute)

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
