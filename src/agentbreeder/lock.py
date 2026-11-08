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
from agentbreeder.registry import Ref, RegistryError, copy_and_hash, resolve

LOCK_VERSION = 1
MODES = ("plain", "update", "locked")


@dataclass
class Lock:
    entries: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Lock:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RegistryError("LOCK_INVALID", f"{path} is not a valid agent.lock") from error
        if not isinstance(data, dict) or data.get("lock_version") != LOCK_VERSION:
            raise RegistryError("LOCK_INVALID", f"{path}: unsupported lock_version")
        entries = data.get("entries")
        if not isinstance(entries, dict):
            raise RegistryError("LOCK_INVALID", f"{path}: missing entries")
        return cls(entries)

    def save(self, path: Path) -> None:
        body = {"lock_version": LOCK_VERSION, "entries": dict(sorted(self.entries.items()))}
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)


@dataclass(frozen=True)
class Materialised:
    key: str  # "prompt/x@1.0.0" or "path:./prompts/x.md"
    path: Path  # copy inside the build context
    sha256: str
    source: str


BUILTIN_LABEL = "builtin"


def _source_label(root: Path, project_dir: Path) -> str:
    # Built-in versions ship inside AgentBreeder; their install path differs per machine.
    if root.resolve() == registry.BUILTIN_ROOT.resolve():
        return BUILTIN_LABEL
    try:
        return os.path.relpath(root, project_dir)
    except ValueError:
        return str(root)


def materialise(
    *,
    refs: list[str],
    paths: list[str],
    project_dir: Path,
    search: list[Path],
    lock: Lock,
    context: Path,
    mode: str = "plain",
    update: set[str] | None = None,
    prune: bool = True,
    locked_paths: bool = False,
) -> tuple[list[Materialised], bool]:
    """Copy every ref/path into `context`, hash the copies and reconcile with `lock`.

    Returns (materialised entries, whether the lock changed). Raises RegistryError on any
    mismatch the mode does not allow. `prune=False` leaves unused entries for the caller (a
    stack locks in two passes). `locked_paths=True` lets --locked accept path entries whose
    hash matches the lock (stack file params), instead of refusing every path.
    """
    if mode not in MODES:
        raise ValueError(mode)
    if mode == "locked" and paths and not locked_paths:
        raise RegistryError(
            "REF_UNPINNED", f"--locked refuses path refs: {', '.join(sorted(paths))}"
        )
    results: list[Materialised] = []
    seen: set[str] = set()
    changed = False
    for text in refs:
        ref = Ref.parse(text)
        key = str(ref)
        if key in seen:
            continue
        seen.add(key)
        locked = lock.entries.get(key)
        hit = resolve(ref, search)
        if mode == "locked" and locked:
            # Load only from the locked source; never from a newly shadowing root.
            wanted = (
                registry.BUILTIN_ROOT
                if locked["source"] == BUILTIN_LABEL
                else project_dir / locked["source"]
            ).resolve()
            if hit.root.resolve() != wanted:
                hit = resolve(ref, [wanted])
        target = context / ref.kind / ref.name
        digest = copy_and_hash(hit.path, target)
        source = _source_label(hit.root, project_dir)
        changed |= _reconcile(lock, key, digest, source, locked, mode, update)
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
            lock, key, digest, relative.as_posix(), lock.entries.get(key), mode, update
        )
        results.append(Materialised(key, target, digest, relative.as_posix()))
    unused = sorted(set(lock.entries) - seen) if prune else []
    if unused:
        if mode == "locked":
            raise RegistryError(
                "LOCK_STALE", f"agent.lock has entries agent.yaml no longer uses: {unused}"
            )
        for key in unused:
            del lock.entries[key]
        changed = True
    return results, changed


def _reconcile(lock, key, digest, source, locked, mode, update) -> bool:
    if locked is None:
        if mode == "locked":
            raise RegistryError(
                "LOCK_STALE", f"{key} is not in agent.lock; run build without --locked"
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
    if not allowed:
        raise RegistryError(
            "HASH_MISMATCH",
            f"{key} ({source}): agent.lock {locked.get('sha256')}, found {digest}. Published "
            "versions are immutable: publish a new version and reference it"
            + (", or run build --update for a path ref" if key.startswith("path:") else ""),
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
