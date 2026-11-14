"""`agentbreeder knowledge status REF [--project DIR] [--environment ENV] [--json]` (#830, M14):
per knowledge version, the documents and chunks the registry holds against what the runtime
indexed in the stack's database (one database per environment: the `DATABASE_URL` binding),
the freshness of that index, and the embedding alias and dimensions. Read-only, with the agent's
own credential (M12 delta §3.1). The contract is docs/commands/knowledge.md.

Errors are `[CODE] message` on stderr (with `--json` also `{"schema_version": 1, "error": {...}}`
on stdout), exit 2.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

from agentbreeder import knowledge, registry, safe_text
from agentbreeder.knowledge import KnowledgeError

NAMES = ("knowledge",)


def register(commands) -> None:
    parser = commands.add_parser(
        "knowledge", help="Show the index status of knowledge versions (read-only)"
    )
    sub = parser.add_subparsers(dest="knowledge_command", required=True)
    show = sub.add_parser(
        "status", help="Compare a knowledge version's documents with the runtime's index"
    )
    show.add_argument("ref", metavar="REF", help="knowledge/NAME or knowledge/NAME@X.Y.Z")
    show.add_argument(
        "--project",
        type=Path,
        default=Path("."),
        metavar="DIR",
        help="The directory with stack.yaml and registry/ (default: .)",
    )
    show.add_argument(
        "--environment", help="The stack environment (default: the only one services up wrote)"
    )
    show.add_argument("--json", action="store_true")


PREFIX = "KNOWLEDGE_STATUS"


@contextmanager
def _database(project: Path, environment: str | None):
    """Yield (environment, database name, query) inside one read-only session with the agent's
    own credential, through the shared `dbread` module (M12 §4.1: the environment, the
    `agent-database` secret, read-only options and timeouts, fixed SQLSTATE messages). The
    knowledge tables belong to the agent's role, so its credential reads them (M12 §3.1)."""
    from agentbreeder import dbread

    try:
        stack = dbread.load_stack(project)
        target = dbread.resolve(stack, project, environment, prefix=PREFIX)
        psycopg = dbread.psycopg_module("knowledge status")
        with dbread.read_session(
            psycopg, target.agent_url, "agent", PREFIX, timeout="try again"
        ) as db:

            def query(sql: str, params: dict) -> list[tuple]:
                return db.execute(sql, params).fetchall()

            yield target.environment, target.database, query
    except dbread.DbReadError as error:
        raise KnowledgeError(error.code, _message(error)) from None


def _message(error) -> str:
    return str(error).removeprefix(f"[{error.code}] ")


def run(args) -> int:
    try:
        name, version = knowledge.parse_ref(args.ref)
        hits = knowledge.versions(name, version, registry.roots(args.project))
        wanted = [knowledge.expected(hit) for hit in hits]
        with _database(args.project, args.environment) as (environment, database, query):
            results = [knowledge.status(want, query) for want in wanted]
    except (KnowledgeError, registry.RegistryError) as error:
        message = str(error).removeprefix(f"[{error.code}] ")
        return _fail(args, error.code, message)
    shown = f"{knowledge.KIND}/{name}" + (f"@{version}" if version else "")
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": knowledge.SCHEMA_VERSION,
                    "ref": shown,
                    "environment": environment,
                    "database": database,
                    "credential": "agent",
                    "versions": [result.as_json() for result in results],
                },
                indent=2,
            )
        )
        return 0
    print(f"{shown} (database {database}, environment {environment})")
    for result in results:
        for line in _lines(result):
            print(line)
    return 0


def _fail(args, code: str, message: str) -> int:
    message = safe_text.excerpt(message, 600)
    print(f"[{code}] {message}", file=sys.stderr)
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": knowledge.SCHEMA_VERSION,
                    "error": {"code": code, "message": message},
                }
            )
        )
    return 2


def _lines(result: knowledge.Status) -> list[str]:
    view = result.as_json()
    chunks, documents, embedding = view["chunks"], view["documents"], view["embedding"]
    where = f"{view['schema']}.{view['table']}" if view["schema"] else view["table"]
    head = f"  {result.expected.version:<10} {view['state']:<12} {where}"
    lines = [head]
    if view["schema"] is not None:
        lines.append(
            f"    chunks {chunks['indexed']} indexed of {chunks['expected']} "
            f"(fresh {chunks['fresh']}, stale {chunks['stale']}, missing {chunks['missing']}, "
            f"extra {chunks['extra']}); "
            f"documents {documents['indexed']} of {documents['expected']}"
        )
        lines.append(
            f"    updated {view['updated']['oldest'] or '-'} .. {view['updated']['newest'] or '-'}"
        )
    else:
        lines.append(
            f"    chunks 0 indexed of {chunks['expected']}; documents 0 of {documents['expected']}"
        )
    table_dimensions = embedding["table_dimensions"]
    lines.append(
        f"    embedding {safe_text.visible(safe_text.excerpt(embedding['alias'] or '-', 80))}, "
        f"{embedding['dimensions']} dimensions"
        + (
            f" (table {table_dimensions})"
            if table_dimensions not in (None, embedding["dimensions"])
            else ""
        )
    )
    lines.extend(f"    note: {note}" for note in view["notes"])
    return lines
