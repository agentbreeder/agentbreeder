"""`agentbreeder list`: list deployments that are not removed (read-only, no IdP calls).
`--all` lists every stack's deployments too (see `allstacks`)."""

from __future__ import annotations

import json
import sys

from agentbreeder import cli
from agentbreeder.safe_text import excerpt
from agentbreeder.store import Store

NAMES = ("list",)


def register(commands) -> None:
    listing = cli.stateful_parser(
        commands, "list", "List deployments that are not removed (read-only, no IdP calls)"
    )
    cli.add_stack(listing, environment=True)
    listing.add_argument(
        "--all",
        action="store_true",
        help=(
            "Also list every stack's deployments under ~/.agentbreeder/stacks "
            "(AGENTBREEDER_STACKS_DIR), read-only; stacks it cannot read are reported"
        ),
    )


def run(args) -> int:
    if args.all:
        return _all(args)

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
    from agentbreeder import allstacks

    rows = allstacks.rows(store)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"{row['id']}  {row['name']}  {row['target']}  {row['state']}")
    return 0


def _all(args) -> int:
    from agentbreeder import allstacks

    result = allstacks.collect(args.state_dir)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    if not result["deployments"]:
        print("No deployments")
    for row in result["deployments"]:
        where = row["stack"] or "default state"
        fields = [row["id"], row["name"], row["target"], row["state"], where]
        fields.append(row["environment"] or "-")
        print("  ".join(excerpt(field, 80) for field in fields))
    for problem in result["skipped"]:
        place = problem["stack"] or "stacks directory"
        if problem.get("environment"):
            place += f" ({problem['environment']})"
        print(
            f"skipped {excerpt(place, 100)}: [{problem['code']}] "
            f"{excerpt(problem['reason'], 300)}",
            file=sys.stderr,
        )
    return 0
