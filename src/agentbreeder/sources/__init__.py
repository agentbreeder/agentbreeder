"""Remote registry sources (#829, spec `2026-11-11-remote-registry-sources-design.md`).

This module holds the lock-side rules and `locate`, with no network code: `locate` maps a
locked local `agent.lock`/`stack.lock` entry to the registry root it must be read from, and the
`RemoteSet` protocol is what `lock.materialise` asks of declared sources. Nothing imported here
opens a socket or runs a subprocess (the console's server loads it). The network side is in
`sources.git` (the hardened git fetcher), `sources.trust` (the user's trusted-source list),
`sources.cache` and `sources.remote` (the `RemoteSet` a build uses), imported only by commands
that may fetch.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from agentbreeder.registry import DIGEST, NAME, Ref, RegistryError, Root, _shown
from agentbreeder.sources.url import TYPES, SourceUrlError, canonical, subpath

REMOTE_PREFIX = "remote:"
COMMIT = re.compile(r"[0-9a-f]{40}")
# The one pin key per source type (spec §7), and the per-entry pin keys each type requires
# (an HTTP entry pins its archive; git and OCI entries carry no pin).
SOURCE_PINS = {"git": "commit", "oci": "digest", "http": "index_sha256"}
ENTRY_PINS = {"http": frozenset({"archive_sha256"})}
SOURCE_KEYS = frozenset({"type", "url", "pin", "path"})
NEWER = "written by a newer AgentBreeder; upgrade AgentBreeder"
# Kinds a remote source may provide in this version (spec §6.4, D5). `mcp` comes with the MCP
# slice (stack.yaml sources, pin-bound consent) and `agent` with M13; runtimes, services and
# skills stay local or built in.
REMOTE_KINDS = frozenset({"prompt", "eval", "knowledge", "tool"})
KIND_REFUSED = (
    "only prompt, eval, knowledge and tool versions come from remote sources; copy it into "
    "./registry with agentbreeder registry import"
)


class RemoteSet(Protocol):
    """The declared sources of one build, as `lock.materialise` uses them (`sources.remote`).

    `locked` is the ref's lock entry when it was locked from that source at the pin declared
    now, else None: only then may a version come from the cache, and the copy's hash is still
    checked against it. A first lock is always re-derived from the pinned commit."""

    def table(self) -> dict[str, dict]:
        """The lock's `sources` table: name → {type, url, pin[, path]}, in declared order."""
        ...

    def provides(self, name: str, ref: Ref, locked: dict | None) -> bool: ...

    def version(self, name: str, ref: Ref, locked: dict | None) -> Path:
        """A private directory holding the version's files (never a cache path to write)."""
        ...

    def remember(self, name: str, ref: Ref, digest: str, copy: Path) -> None:
        """Called with the hashed copy in the build context, so the cache stores those bytes."""
        ...

    def describe(self, name: str) -> str:
        """`source 'acme' (git https://host/repo @3f2a9c1)` for messages."""
        ...


def is_remote(label: object) -> bool:
    return isinstance(label, str) and label.startswith(REMOTE_PREFIX)


def remote_name(label: str) -> str:
    return label[len(REMOTE_PREFIX) :]


def _invalid(where: str, message: str) -> RegistryError:
    return RegistryError("LOCK_INVALID", f"{where}: {message}")


def check_source(where: str, name: object, data: object) -> None:
    """One `sources` record of a v2 lock, strictly (spec §7): the lock is project-controlled
    input, so its URLs pass the §5 rules again and must already be canonical."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise _invalid(where, f"source name {_shown(name)} is invalid")
    if not isinstance(data, dict):
        raise _invalid(where, f"source {name} must be an object")
    if unknown := sorted(set(data) - SOURCE_KEYS):
        raise _invalid(where, f"source {name} has unknown keys {unknown}; {NEWER}")
    kind = data.get("type")
    if kind not in TYPES:
        raise _invalid(where, f"source {name} has an unknown type {_shown(kind)}; {NEWER}")
    url = data.get("url")
    try:
        if canonical(kind, url) != url:
            raise SourceUrlError("the URL is not in canonical form")
        if "path" in data:
            if kind != "git":
                raise SourceUrlError("only git sources take a path")
            if subpath(data["path"]) != data["path"]:
                raise SourceUrlError("path is not in canonical form")
    except SourceUrlError as error:
        raise _invalid(where, f"source {name}: {error}") from None
    pin = data.get("pin")
    key = SOURCE_PINS[kind]
    if not isinstance(pin, dict) or set(pin) != {key}:
        raise _invalid(where, f"source {name}: pin must hold exactly {key!r}; {NEWER}")
    pattern = COMMIT if kind == "git" else DIGEST
    if not isinstance(pin[key], str) or not pattern.fullmatch(pin[key]):
        raise _invalid(where, f"source {name}: pin {key} is malformed")


def check_entry_source(where: str, key: str, entry: dict, sources: dict | None) -> None:
    """A lock entry's `source` label and optional `pin` (spec §7): a `remote:` label is valid
    only in a v2 lock naming a declared source, and only remote entries carry a `pin`."""
    label = entry.get("source")
    if not is_remote(label):
        if "pin" in entry:
            raise _invalid(where, f"the {key} entry is malformed (a pin on a local entry)")
        return
    if sources is None:
        raise _invalid(where, f"the {key} entry names a remote source in a version 1 lock")
    name = remote_name(label)
    if name not in sources:
        raise _invalid(where, f"the {key} entry names source {_shown(name)}, not in sources")
    required = ENTRY_PINS.get(sources[name]["type"], frozenset())
    if "pin" not in entry:
        if required:
            raise _invalid(where, f"the {key} entry is malformed (missing pin)")
    else:
        pin = entry["pin"]
        if not isinstance(pin, dict) or not required or set(pin) != required:
            raise _invalid(where, f"the {key} entry has an unknown pin; {NEWER}")
        if not all(isinstance(v, str) and DIGEST.fullmatch(v) for v in pin.values()):
            raise _invalid(where, f"the {key} entry is malformed (pin)")
    # Last, after the shape checks: a hand-written lock cannot take a refused kind (a runtime,
    # service, skill or mcp version) from a source, in any reader (spec §6.4).
    if key.split("/", 1)[0] not in REMOTE_KINDS:
        raise RegistryError(
            "REMOTE_KIND_REFUSED",
            f"{where}: the {key} entry is locked from a source; " + KIND_REFUSED,
        )


def locate(
    key: str,
    entry: dict,
    roots: Sequence[Root],
    *,
    lock_file: str = "agent.lock",
    refresh: str = "run a plain build (without --locked) to update it",
) -> Root:
    """The registry root a locked entry must be read from (spec §4.3, D6).

    Only that root is read: no cross-root conflict check, so `--locked` never needs another
    root (or, later, another source). The root must be configured now: a local label must equal
    the label of one of `roots` (`registry`, an AGENTBREEDER_REGISTRY_PATH entry, `builtin`),
    else LOCK_STALE, so a planted lock cannot point a build at an arbitrary directory. A
    `remote:` label is dispatched before any path join: callers read those through the declared
    sources (`RemoteSet.version`), so here it is SOURCE_OFFLINE.
    """
    label = entry.get("source")
    if is_remote(label):
        name = remote_name(label)
        raise RegistryError(
            "SOURCE_OFFLINE",
            f"{key} is locked from source '{_shown(name)}', which this command cannot read; "
            "read it through the sources agent.yaml declares",
        )
    for root in roots:
        if root.label == label:
            return root
    configured = ", ".join(_shown(root.label) for root in roots) or "none"
    raise RegistryError(
        "LOCK_STALE",
        f"{lock_file} locks {key} from {_shown(label)}, which is not a configured registry root "
        f"(configured: {configured}); {refresh}",
    )


def short_pin(record: dict | None) -> str:
    """The first 7 characters of a source record's pin (display only)."""
    if not isinstance(record, dict) or not isinstance(record.get("pin"), dict):
        return "?"
    value = next(iter(record["pin"].values()), "")
    return str(value).removeprefix("sha256:")[:7]


def sources_difference(locked: dict | None, declared: dict | None) -> str:
    """Why `registries:` and the lock's `sources` differ, for `--locked`'s LOCK_STALE."""
    if declared is None:
        return "agent.yaml declares no registries: but the lock has sources"
    if locked is None:
        return "agent.yaml declares registries: but the lock has no sources"
    parts = []
    for name in sorted(set(locked) | set(declared)):
        if name not in declared:
            parts.append(f"source '{name}' is locked but no longer declared")
        elif name not in locked:
            parts.append(f"source '{name}' is declared but not locked")
        elif locked[name] != declared[name]:
            before, after = short_pin(locked[name]), short_pin(declared[name])
            what = "pin" if before != after else "URL or path"
            parts.append(f"registries: {name} {what} differs from the lock ({before} → {after})")
    return "; ".join(parts)


def locked_name(
    key: str, entry: dict, remotes: RemoteSet | None, lock_file: str, refresh: str
) -> str:
    """The source a `remote:` lock entry names, which must be declared now (else LOCK_STALE)."""
    name = remote_name(entry["source"])
    if remotes is None or name not in remotes.table():
        raise RegistryError(
            "LOCK_STALE",
            f"{lock_file} locks {key} from source '{_shown(name)}', which agent.yaml does not "
            f"declare; {refresh}",
        )
    return name
