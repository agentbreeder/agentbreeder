"""Built-in registry versions are append-only across releases (spec criterion 6).

Locks such as Northwind's `runtime/adk@1.2.0` pin a built-in version by hash, so a release that
changes any existing version's bytes or exec bits breaks every project locked to it. New versions
may be added: record them in tests/fixtures/builtin_hashes.json in the same change.
"""

import json
from pathlib import Path

from agentbreeder import registry

FIXTURE = Path(__file__).parent / "fixtures" / "builtin_hashes.json"


def _current() -> dict[str, str]:
    return {
        row["ref"]: registry.copy_and_hash(Path(row["path"]))
        for row in registry.listing([registry.BUILTIN_ROOT])
    }


def test_existing_builtin_versions_never_change():
    recorded = json.loads(FIXTURE.read_text())
    current = _current()
    missing = sorted(set(recorded) - set(current))
    changed = sorted(ref for ref in recorded if ref in current and current[ref] != recorded[ref])
    assert not missing, f"built-in versions removed: {missing}"
    assert not changed, f"published built-in versions changed (publish a new version): {changed}"


def test_every_builtin_version_is_recorded():
    unrecorded = sorted(set(_current()) - set(json.loads(FIXTURE.read_text())))
    assert not unrecorded, f"record new built-in versions in {FIXTURE.name}: {unrecorded}"


def test_shipped_examples_lock_to_recorded_builtins():
    recorded = json.loads(FIXTURE.read_text())
    examples = Path(registry.__file__).parent / "examples"
    checked = 0
    locks = [*examples.glob("*/agent.lock"), *examples.glob("*/stack.lock")]
    for lock in sorted(locks):
        for ref, entry in json.loads(lock.read_text())["entries"].items():
            if entry.get("source") == "builtin":
                assert recorded.get(ref) == entry["sha256"], f"{lock}: {ref}"
                checked += 1
    assert checked, "no shipped example locks a built-in version; this test checks nothing"
