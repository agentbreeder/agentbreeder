"""Remote registry sources (#829, spec `2026-11-11-remote-registry-sources-design.md`).

Slice 1a: the lock-side rules and `locate`, with no network code. `locate` maps a locked
`agent.lock`/`stack.lock` entry to the registry root it must be read from; a `remote:` entry
raises SOURCE_OFFLINE, because this version cannot fetch remote sources (slice 1b adds the git
fetcher and the cache). Nothing in this package opens a socket or runs a subprocess.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from agentbreeder.registry import DIGEST, NAME, RegistryError, Root, _shown
from agentbreeder.sources.url import TYPES, SourceUrlError, canonical, subpath

REMOTE_PREFIX = "remote:"
COMMIT = re.compile(r"[0-9a-f]{40}")
# The one pin key per source type (spec §7), and the per-entry pin keys each type requires
# (an HTTP entry pins its archive; git and OCI entries carry no pin).
SOURCE_PINS = {"git": "commit", "oci": "digest", "http": "index_sha256"}
ENTRY_PINS = {"http": frozenset({"archive_sha256"})}
SOURCE_KEYS = frozenset({"type", "url", "pin", "path"})
NEWER = "written by a newer AgentBreeder; upgrade AgentBreeder"


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
        return
    pin = entry["pin"]
    if not isinstance(pin, dict) or not required or set(pin) != required:
        raise _invalid(where, f"the {key} entry has an unknown pin; {NEWER}")
    if not all(isinstance(v, str) and DIGEST.fullmatch(v) for v in pin.values()):
        raise _invalid(where, f"the {key} entry is malformed (pin)")


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
    `remote:` label is dispatched before any path join and, in this version, is SOURCE_OFFLINE.
    """
    label = entry.get("source")
    if is_remote(label):
        name = remote_name(label)
        raise RegistryError(
            "SOURCE_OFFLINE",
            f"{key} is locked from source '{_shown(name)}', which is not in the cache; this "
            "version of AgentBreeder cannot fetch remote registry sources",
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
