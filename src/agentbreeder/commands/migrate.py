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


def _version(path) -> int | None:
    """The schema version before migrating; None when it cannot be read read-only."""
    try:
        db = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None
    try:
        return db.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        db.close()


def run(args) -> int:
    before = None

    def check() -> None:
        nonlocal before
        path = args.state_dir / "deployments.sqlite3"
        if path.is_file() and not path.is_symlink():
            before = _version(path)

    def action(store: Store) -> int:
        result = {
            "state_dir": str(store.directory),
            "schema_version": SCHEMA_VERSION,
            "previous_version": before,
            "migrated": before != SCHEMA_VERSION,
        }
        if args.json:
            print(json.dumps(result, indent=2))
        elif result["migrated"]:
            was = f"v{before}" if before is not None else "an unreadable version"
            print(f"Upgraded {store.directory} from {was} to schema v{SCHEMA_VERSION}")
        else:
            print(f"{store.directory} is already at schema v{SCHEMA_VERSION}")
        return 0

    return cli.run_stateful(args, action, existing=True, before=check, service=False)
