"""`agentbreeder migrate`: upgrade the local state's schema (and finish an interrupted write).

It only opens the existing state for writing, which migrates it and lets SQLite roll back a hot
journal; it calls no identity provider and touches no container. Read-only commands (`list`)
name it when they find older state. `status` is not a substitute: it re-verifies at the IdP.
"""

from __future__ import annotations

import json
import sqlite3
from urllib.parse import quote

from agentbreeder import cli
from agentbreeder.store import SCHEMA_VERSION, Store

NAMES = ("migrate",)


def register(commands) -> None:
    migrate = cli.stateful_parser(
        commands, "migrate", "Upgrade the local state to this version's schema (no IdP calls)"
    )
    cli.add_stack(migrate, environment=True)


def _version(path) -> tuple[int | None, bool]:
    """(the schema version before migrating, or None when it cannot be read read-only; whether
    that is because another writer holds the database, not because of a hot journal)."""
    try:
        db = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None, False
    try:
        return db.execute("PRAGMA user_version").fetchone()[0], False
    except sqlite3.Error as error:
        return None, "locked" in str(error)
    finally:
        db.close()


def _hot_journal(directory) -> bool:
    try:
        return (directory / "deployments.sqlite3-journal").stat().st_size > 0
    except OSError:
        return False


def run(args) -> int:
    before = None
    interrupted = False

    def check() -> None:
        nonlocal before, interrupted
        path = args.state_dir / "deployments.sqlite3"
        if path.is_file() and not path.is_symlink():
            before, locked = _version(path)
            # A hot rollback journal hides the version from a read-only reader; opening the
            # state for writing below rolls it back. A held lock is not an interrupted write.
            interrupted = before is None and not locked and _hot_journal(args.state_dir)

    def action(store: Store) -> int:
        result = {
            "state_dir": str(store.directory),
            "schema_version": SCHEMA_VERSION,
            "previous_version": before,
            "migrated": before != SCHEMA_VERSION,
            "interrupted_write": interrupted,
        }
        if args.json:
            print(json.dumps(result, indent=2))
        elif interrupted:
            print(
                f"Recovered an interrupted write in {store.directory}; it is now at schema "
                f"v{SCHEMA_VERSION}"
            )
        elif result["migrated"]:
            was = f"v{before}" if before is not None else "an unreadable version"
            print(f"Upgraded {store.directory} from {was} to schema v{SCHEMA_VERSION}")
        else:
            print(f"{store.directory} is already at schema v{SCHEMA_VERSION}")
        return 0

    return cli.run_stateful(args, action, existing=True, before=check, service=False)
