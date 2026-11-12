"""`agentbreeder list`: list deployments that are not removed (read-only, no IdP calls)."""

from __future__ import annotations

import json

from agentbreeder import cli
from agentbreeder.store import Store

NAMES = ("list",)


def register(commands) -> None:
    listing = cli.stateful_parser(
        commands, "list", "List deployments that are not removed (read-only, no IdP calls)"
    )
    cli.add_stack(listing, environment=True)


def run(args) -> int:
    def before() -> int | None:
        if not (args.state_dir / "deployments.sqlite3").exists():
            print("[]" if args.json else "No deployments")
            return 0
        return None

    # Read-only: no migration, no write lock, nothing created (guided §14.2).
    return cli.run_stateful(
        args, lambda store: _list(store, args), before=before, service=False, readonly=True
    )


def _list(store: Store, args) -> int:
    rows = [
        {
            **{
                key: row[key]
                for key in (
                    "id",
                    "name",
                    "state",
                    "endpoint",
                    "environment",
                    "compliance",
                    "error",
                )
            },
            "target": (manifest := store.manifest(row["id"])).get("target", "local"),
            "image": manifest.get("image"),
            "updated": row["updated"],
        }
        for row in store.active_all()
    ]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"{row['id']}  {row['name']}  {row['target']}  {row['state']}")
    return 0
