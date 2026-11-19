"""`agent.lock`: the exact registry content an agent was built from.

Semantics (spec §6, cargo-like):
- plain: entries matching the lock are verified; missing ones are added; unused ones pruned.
- update: the named refs (or all) are re-hashed and their new hashes accepted (path refs).
- locked: nothing may change; path refs are refused (CI reproducibility).
Every entry is hashed from the copy placed into the build context (registry.copy_and_hash).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from agentbreeder import registry
from agentbreeder import sources as remote
from agentbreeder.registry import Ref, RegistryError, copy_and_hash, read_manifest, resolve

LOCK_VERSION = 1  # written when agent.yaml (or stack.yaml) declares no `registries:`
LOCK_VERSION_SOURCES = 2  # written with a `sources` table (#829 spec §7)
LOCK_VERSIONS = (LOCK_VERSION, LOCK_VERSION_SOURCES)
MODES = ("plain", "update", "locked")
ENTRY_KEYS = frozenset({"sha256", "source"})
ENTRY_KEYS_V2 = ENTRY_KEYS | {"pin"}
SHA256 = registry.SHA256
SOURCE_LIMIT = registry.LOCK_SOURCE_LIMIT
NEWER = "written by a newer AgentBreeder; upgrade AgentBreeder"
BUILTIN_LABEL = registry.BUILTIN_LABEL


@dataclass
class Lock:
    """`entries` maps a lock key to {sha256, source[, pin]}. `sources` is None for a version 1
    lock and a dict (possibly empty) for version 2, written when `registries:` is declared."""

    entries: dict[str, dict] = field(default_factory=dict)
    sources: dict[str, dict] | None = None

    @classmethod
    def load(
        cls,
        path: Path,
        versions: tuple[int, ...] = LOCK_VERSIONS,
        extra_keys: tuple[str, ...] = (),
    ) -> Lock:
        lock, _ = cls.parse(path, versions, extra_keys)
        return lock

    @classmethod
    def parse(
        cls,
        path: Path,
        versions: tuple[int, ...] = LOCK_VERSIONS,
        extra_keys: tuple[str, ...] = (),
    ) -> tuple[Lock, dict]:
        """The lock and its raw JSON object, checked strictly (#829 spec §7): unknown top-level,
        entry, source or pin keys and unsupported versions are LOCK_INVALID. `extra_keys` are
        the top-level keys a caller reads itself (stack.lock's `imports` and `mcp`)."""
        if not path.exists():
            return cls(), {}
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise RegistryError("LOCK_INVALID", f"{path} is not a valid lock file") from error
        return cls.parse_bytes(raw, str(path), versions, extra_keys)

    @classmethod
    def parse_bytes(
        cls,
        raw: bytes,
        path: str,
        versions: tuple[int, ...] = LOCK_VERSIONS,
        extra_keys: tuple[str, ...] = (),
    ) -> tuple[Lock, dict]:
        """`parse` on bytes the caller already read (the console reads a lock through
        `registry.read_project_file`; `package` reads the bytes it packages). `path` names the
        file in messages."""
        try:
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
        except RecursionError as error:
            raise RegistryError("LOCK_INVALID", f"{path} is too deeply nested") from error
        except (UnicodeDecodeError, ValueError) as error:
            raise RegistryError("LOCK_INVALID", f"{path} is not a valid lock file") from error
        if not isinstance(data, dict):
            raise RegistryError("LOCK_INVALID", f"{path} is not a valid lock file")
        version = data.get("lock_version")
        if type(version) is not int or version not in versions:
            newer = isinstance(version, int) and version > max(versions)
            raise RegistryError(
                "LOCK_INVALID",
                f"{path}: unsupported lock_version" + (f"; {NEWER}" if newer else ""),
            )
        allowed = {"lock_version", "entries", *extra_keys}
        if version == LOCK_VERSION_SOURCES:
            allowed.add("sources")
        if unknown := sorted(set(data) - allowed):
            raise RegistryError("LOCK_INVALID", f"{path}: unknown keys {unknown}; {NEWER}")
        entries = data.get("entries")
        if not isinstance(entries, dict):
            raise RegistryError("LOCK_INVALID", f"{path}: missing entries")
        sources = None
        if version == LOCK_VERSION_SOURCES:
            sources = data.get("sources")
            if not isinstance(sources, dict):
                raise RegistryError("LOCK_INVALID", f"{path}: missing sources")
            for name, record in sources.items():
                remote.check_source(str(path), name, record)
        keys = ENTRY_KEYS if sources is None else ENTRY_KEYS_V2
        for key, entry in entries.items():
            if (
                not isinstance(entry, dict)
                or not ENTRY_KEYS <= set(entry)
                or not isinstance(entry.get("sha256"), str)
                or not SHA256.fullmatch(entry["sha256"])
                or not isinstance(entry.get("source"), str)
                or not entry["source"]
                or len(entry["source"]) > SOURCE_LIMIT
            ):
                raise RegistryError("LOCK_INVALID", f"{path}: the {key} entry is malformed")
            if unknown := sorted(set(entry) - keys):
                raise RegistryError(
                    "LOCK_INVALID", f"{path}: the {key} entry has unknown keys {unknown}; {NEWER}"
                )
            remote.check_entry_source(str(path), key, entry, sources)
        return cls(entries, sources), data

    def body(self) -> dict:
        """The JSON object `save` writes: version 1 byte for byte as before #829 when there is
        no `sources` table, version 2 with sorted `sources` otherwise."""
        if self.sources is None:
            return {"lock_version": LOCK_VERSION, "entries": dict(sorted(self.entries.items()))}
        return {
            "lock_version": LOCK_VERSION_SOURCES,
            "sources": dict(sorted(self.sources.items())),
            "entries": dict(sorted(self.entries.items())),
        }

    def save(self, path: Path) -> None:
        _write(path, self.body())


def _unique(pairs: list) -> dict:
    """A JSON object without duplicate keys (a duplicate could hide an entry from a review)."""
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate key")
    return result


def _write(path: Path, body: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class Materialised:
    key: str  # "prompt/x@1.0.0" or "path:./prompts/x.md"
    path: Path  # copy inside the build context
    sha256: str
    source: str


def materialise(
    *,
    refs: list[str],
    paths: list[str],
    project_dir: Path,
    search: list[Path | registry.Root],
    lock: Lock,
    context: Path,
    mode: str = "plain",
    update: set[str] | None = None,
    prune: bool = True,
    locked_paths: bool = False,
    lock_file: str = "agent.lock",
    update_command: str = "build --update",
    remotes: remote.RemoteSet | None = None,
) -> tuple[list[Materialised], bool]:
    """Copy every ref/path into `context`, hash the copies and reconcile with `lock`.

    Returns (materialised entries, whether the lock changed). Raises RegistryError on any
    mismatch the mode does not allow. `prune=False` leaves unused entries for the caller (a
    stack locks in two passes). `locked_paths=True` lets --locked accept path entries whose
    hash matches the lock (stack file params), instead of refusing every path.

    `remotes` (#829 slice 1b) are the sources `registries:` declares. With them the lock is
    version 2 and its `sources` table is theirs; without them it is version 1, byte for byte as
    before. A plain build searches ./registry, AGENTBREEDER_REGISTRY_PATH, each source in order,
    then the built-in registry, and a ref with different content in two of them is
    REF_CONFLICT. `--locked` reads each ref from its locked root only (D6).
    """
    if mode not in MODES:
        raise ValueError(mode)
    if mode == "locked" and paths and not locked_paths:
        raise RegistryError(
            "REF_UNPINNED", f"--locked refuses path refs: {', '.join(sorted(paths))}"
        )
    names = (lock_file, update_command)
    configured = registry.as_roots(search, project_dir)
    declared = remotes.table() if remotes is not None else None
    previous = lock.sources
    if mode == "locked" and declared != previous:
        raise RegistryError(
            "LOCK_STALE",
            f"{lock_file}: {remote.sources_difference(previous, declared)}; " + _refresh(*names),
        )
    # Sources whose pin and URL are unchanged since the lock: their locked entries may come
    # from the cache (checked against the lock's hash). A changed source is fetched again and
    # each of its entries must keep its hash (spec §7).
    stable = {n for n in declared or {} if previous is not None and previous.get(n) == declared[n]}
    changed = declared != previous
    lock.sources = None if declared is None else dict(declared)
    results: list[Materialised] = []
    seen: set[str] = set()
    for text in refs:
        ref = Ref.parse(text)
        key = str(ref)
        if key in seen:
            continue
        seen.add(key)
        locked = lock.entries.get(key)
        target = context / ref.kind / ref.name
        if mode == "locked" and locked:
            # #829 D6: load only from the locked root, which must be configured now; no
            # cross-root check, so a shadowing copy elsewhere is never read (or needed).
            if remote.is_remote(locked.get("source")):
                name = remote.locked_name(key, locked, remotes, lock_file, _refresh(*names))
                if ref.kind not in remote.REMOTE_KINDS:  # also refused when the lock is read
                    raise RegistryError(
                        "REMOTE_KIND_REFUSED",
                        f"{key} is locked from source '{name}': {remote.KIND_REFUSED}",
                    )
                version = remotes.version(name, ref, locked)
                read_manifest(version, ref)
                digest = copy_and_hash(version, target)
            else:
                root = remote.locate(
                    key, locked, configured, lock_file=lock_file, refresh=_refresh(*names)
                )
                digest = copy_and_hash(resolve(ref, [root.path]).path, target)
            source = locked["source"]
        else:
            if mode == "locked":  # not in the lock: refuse before reading (or fetching) anything
                raise RegistryError(
                    "LOCK_STALE", f"{key} is not in {lock_file}; {_refresh(*names)}"
                )
            path, source = _find(ref, configured, remotes, locked, stable)
            digest = copy_and_hash(path, target)
        if remote.is_remote(source):
            remotes.remember(remote.remote_name(source), ref, digest, target)
        old_pin = previous.get(remote.remote_name(source)) if previous else None
        changed |= _reconcile(
            lock, key, digest, source, locked, mode, update, names, remotes, old_pin
        )
        results.append(Materialised(key, target, digest, source))
    for text in paths:
        relative = Path(text)
        if relative.is_absolute() or ".." in relative.parts:
            raise RegistryError("ENTRY_UNSAFE", f"path refs must stay inside the project: {text}")
        source_path = project_dir / relative
        _no_symlinks(project_dir, relative, text)
        key = f"path:{relative.as_posix()}"
        if key in seen:
            continue
        seen.add(key)
        target = context / "paths" / relative.as_posix().replace("/", "__")
        digest = (
            copy_and_hash(source_path, target)
            if source_path.is_dir()
            else _copy_file(source_path, target)
        )
        changed |= _reconcile(
            lock, key, digest, relative.as_posix(), lock.entries.get(key), mode, update, names
        )
        results.append(Materialised(key, target, digest, relative.as_posix()))
    unused = sorted(set(lock.entries) - seen) if prune else []
    if unused:
        if mode == "locked":
            raise RegistryError(
                "LOCK_STALE",
                f"{lock_file} has entries the project no longer uses: {unused}; "
                + _refresh(lock_file, update_command),
            )
        for key in unused:
            del lock.entries[key]
        changed = True
    return results, changed


def _refresh(lock_file: str, update_command: str) -> str:
    """How to bring a stale lock up to date (G-F5): `release` and `build --locked` never
    re-lock, so the fix is a plain `build` (or `services up` for stack.lock)."""
    from agentbreeder import hints

    words = ["build"] if lock_file == "agent.lock" else update_command.split()[:-1]
    return f"run `{hints.command(*words)}` (without --locked) to update it"


def _find(
    ref: Ref,
    configured: list[registry.Root],
    remotes: remote.RemoteSet | None,
    locked: dict | None,
    stable: set[str],
) -> tuple[Path, str]:
    """The version directory and root label a plain build uses for `ref` (#829 spec §4.2).

    Search order: the local roots before the built-in registry, then each declared source,
    then the built-in registry. The first holder wins, but every holder is compared by hash:
    different content in two roots is REF_CONFLICT, so a source never silently shadows a local
    version and a local copy never silently shadows a source's. A source is only consulted
    for the kinds it may provide; a ref only a source holds, of a refused kind, is
    REMOTE_KIND_REFUSED."""
    local = [root for root in configured if root.label != registry.BUILTIN_LABEL]
    builtin = [root for root in configured if root.label == registry.BUILTIN_LABEL]
    found: list[tuple[str, Path]] = []
    for root in local:
        found += [(root.label, hit.path) for hit in registry.hits(ref, [root.path])]
    refused = []
    for name in remotes.table() if remotes is not None else ():
        label = remote.REMOTE_PREFIX + name
        mine = locked if locked and locked.get("source") == label and name in stable else None
        if not remotes.provides(name, ref, mine):
            continue
        if ref.kind not in remote.REMOTE_KINDS:
            refused.append(name)
            continue
        found.append((label, remotes.version(name, ref, mine)))
    for root in builtin:
        found += [(root.label, hit.path) for hit in registry.hits(ref, [root.path])]
    if not found:
        if refused:
            raise RegistryError(
                "REMOTE_KIND_REFUSED",
                f"{ref} is only in source '{refused[0]}': {remote.KIND_REFUSED}",
            )
        searched = [root.label for root in local + builtin]
        searched += [f"source '{n}'" for n in (remotes.table() if remotes else ())]
        raise RegistryError(
            "REF_NOT_FOUND", f"{ref} not found in {', '.join(map(registry._shown, searched))}"
        )
    if len(found) > 1:
        digests = [copy_and_hash(path) for _, path in found]
        for (label, _), digest in zip(found[1:], digests[1:], strict=True):
            if digest != digests[0]:
                first = _where(found[0][0], remotes)
                raise RegistryError(
                    "REF_CONFLICT",
                    f"{ref} differs between {first} ({digests[0][:15]}…) and "
                    f"{_where(label, remotes)} ({digest[:15]}…). Versions are immutable: delete "
                    "the local copy to use the source's, or publish yours as a new version",
                )
    label, path = found[0]
    read_manifest(path, ref)
    return path, label


def _where(label: str, remotes: remote.RemoteSet | None) -> str:
    if remote.is_remote(label) and remotes is not None:
        return remotes.describe(remote.remote_name(label))
    return registry._shown(label)


def _reconcile(
    lock, key, digest, source, locked, mode, update, names, remotes=None, old_pin=None
) -> bool:
    lock_file, update_command = names
    if locked is None:
        if mode == "locked":
            raise RegistryError(
                "LOCK_STALE", f"{key} is not in {lock_file}; {_refresh(lock_file, update_command)}"
            )
        lock.entries[key] = {"sha256": digest, "source": source}
        return True
    if locked.get("sha256") == digest:
        if locked.get("source") != source and mode != "locked":
            lock.entries[key] = {"sha256": digest, "source": source}
            return True
        return False
    # Only path refs (work in progress) may change hash. A published registry version is
    # immutable, so a different hash is tampering or a re-publish: never accept it.
    allowed = (
        key.startswith("path:")
        and mode == "update"
        and (update is None or not update or key in update)
    )
    if (
        not allowed
        and remote.is_remote(source)
        and locked.get("source") == source
        and old_pin is not None
        and remotes is not None
        and old_pin != remotes.table().get(remote.remote_name(source))
    ):
        name = remote.remote_name(source)
        raise RegistryError(
            "HASH_MISMATCH",
            f"{key} changed between {name}@{remote.short_pin(old_pin)} and "
            f"{name}@{remote.short_pin(remotes.table()[name])}. A published version must not "
            "change; ask the registry maintainers to publish a new version, or keep the old pin",
        )
    if not allowed:
        raise RegistryError(
            "HASH_MISMATCH",
            f"{key} ({source}): {lock_file} {locked.get('sha256')}, found {digest}. Published "
            "versions are immutable: publish a new version and reference it"
            + (f", or run {update_command} for a path ref" if key.startswith("path:") else ""),
        )
    lock.entries[key] = {"sha256": digest, "source": source}
    return True


def _no_symlinks(project_dir: Path, relative: Path, text: str) -> None:
    """Every component of a path ref must be a real directory or file inside the project: a
    symlinked folder (e.g. prompts -> ~/.ssh) would copy files from outside into the image."""
    current = project_dir
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RegistryError("ENTRY_UNSAFE", f"path refs may not go through symlinks: {text}")
    if not current.resolve().is_relative_to(project_dir.resolve()):
        raise RegistryError("ENTRY_UNSAFE", f"path refs must stay inside the project: {text}")


def _copy_file(source: Path, target: Path) -> str:
    """A single-file path ref (e.g. a prompt.md) is wrapped as a one-file directory."""
    if source.is_symlink() or not source.is_file():
        raise RegistryError("ENTRY_UNSAFE", f"{source} must be a regular file")
    staging = target.parent / (target.name + ".src")
    staging.mkdir(parents=True)
    (staging / source.name).write_bytes(source.read_bytes())
    try:
        return copy_and_hash(staging, target)
    finally:
        for child in staging.iterdir():
            child.unlink()
        staging.rmdir()
