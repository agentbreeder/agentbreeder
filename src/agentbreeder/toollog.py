"""The tool execution log behind `agentbreeder tool log` (M12 spec delta §3.1, §3.4, §4).

The log is the runtime's `audit_events` rows (one per tool call, `action = 'tool:<function>'`)
joined to `turns` for the trace id. It is what the runtime **claims** it called: the agent's
database role can insert any row, so every value read here is treated as hostile (S-35) and
validated before it is shown.

Credential (§3.1), chosen from the catalog before any data table is read, never by fallback:

1. Connect with the agent's own credential (the host-form `agent-database` secret `services up`
   writes) and find `turns` and `audit_events` in the agent role's own schema, then `public`.
2. The agent role owns them (or is a member of their owner): read them with that credential
   (a runtime-managed stack, such as the starter).
3. The database owner made them: read them as `agentbreeder_console` (`console-database`), after
   checking in the same transaction that nothing on them could lend the console role's rights
   (owner, kind, partitions, row security, rules, triggers, TRIGGER grants, schema owner).
4. Anything else is refused.

Old runtimes (#922): `runtime/adk` 1.0.6 and 1.1.0 write a failed call twice, the failure and
then a second row claiming `ok: true`. Such a phantom row has no `detail.agent` (only 1.2.0
writes it) and follows a failed row without `detail.agent` for the same turn and function. Each
such failure is paired with the next unpaired success row (`_sql`); the paired rows are left
out and counted (`phantom_rows`). A real failure followed by a real success of one function in
one turn on an old runtime pairs the same way and is left out too; the docs say so.

Rows dated more than 5 minutes after the database's clock (an agent can write any `at`, even
`infinity`, which psycopg cannot load) are outside every window.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from agentbreeder import dbread, registry, safe_text
from agentbreeder.dbread import DbReadError, read_session
from agentbreeder.redaction import Redactor
from agentbreeder.strict_yaml import StrictYamlError, load_file

DEFAULT_SINCE = "24h"
MAX_HOURS = 720
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
ARGS_LIMIT = 16 * 1024  # bytes of `detail.args` (pg_column_size); larger is null
ERROR_LIMIT = 2000
TEXT_LIMIT = 2000  # one string inside `args`, after redaction
ARGS_DEPTH = 16  # nesting kept in `args`; deeper values become "…"
EXPECTED_TRIGGER_FUNCTIONS = ("audit_events_append_only", "audit_events_db_role")
DB_ROLE_TRIGGER = "audit_events_set_db_role"
# #922 pairing looks this far before --since, so a failure just outside the window still pairs
# with its phantom just inside it (the two rows are written within one tool call).
PAIRING_MARGIN = timedelta(minutes=5)

TOOL_NAME = re.compile(rf"tool/(?P<name>{registry.NAME.pattern})")
TOOL_REF = re.compile(rf"tool/{registry.NAME.pattern}@{registry.VERSION.pattern}")
AGENT = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
DB_ROLE = re.compile(r"[a-z][a-z0-9_]{0,62}")
FUNCTION = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
HEX = re.compile(r"[0-9a-f]{1,64}")
SCHEMA = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_RELATIVE = re.compile(r"(?P<n>[1-9][0-9]{0,5})(?P<unit>[smh])")
_ABSOLUTE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_UNITS = {"s": 1, "m": 60, "h": 3600}


ToolLogError = DbReadError  # an alias: one error type for the command and `dbread`
PREFIX = "TOOL_LOG"  # dbread's command-specific codes: TOOL_LOG_NO_TABLES, ...
NO_TABLES = "No calls recorded yet"


# -- arguments ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    name: str
    version: str | None

    @property
    def ref(self) -> str:
        return f"tool/{self.name}" + (f"@{self.version}" if self.version else "")

    def pattern(self) -> str:
        """`LIKE` pattern for `detail.tool`: the exact ref, or every version of the name."""
        if self.version:
            return _like_escape(self.ref)
        return _like_escape(f"tool/{self.name}@") + "%"


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_ref(text: str) -> Target:
    if TOOL_NAME.fullmatch(text or ""):
        return Target(text.removeprefix("tool/"), None)
    if TOOL_REF.fullmatch(text or ""):
        name, version = text.removeprefix("tool/").split("@")
        return Target(name, version)
    raise ToolLogError(
        "REF_INVALID",
        f"{safe_text.excerpt(text, 80)!r} is not tool/NAME or tool/NAME@X.Y.Z "
        "(MCP tools are not in this log yet)",
    )


def parse_since(text: str | None, now: datetime) -> datetime:
    value = text or DEFAULT_SINCE
    relative = _RELATIVE.fullmatch(value)
    if relative:
        since = now - timedelta(seconds=int(relative["n"]) * _UNITS[relative["unit"]])
    elif _ABSOLUTE.fullmatch(value):
        try:
            since = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            since = None
    else:
        since = None
    if since is None or since > now or now - since > timedelta(hours=MAX_HOURS):
        raise ToolLogError(
            "SINCE_INVALID",
            f"--since must look like 90s, 10m, 2h or 2026-11-11T09:00:00Z, at most {MAX_HOURS} "
            "hours back and not in the future",
        )
    return since


def check_limit(text: str | int) -> int:
    value = str(text)
    if not re.fullmatch(r"[0-9]{1,4}", value) or not 1 <= int(value) <= MAX_LIMIT:
        raise ToolLogError("LIMIT_INVALID", f"--limit must be 1 to {MAX_LIMIT}")
    return int(value)


def legacy_actions(target: Target, search: list[Path]) -> list[str]:
    """`tool:<function>` for every function the name's versions declare in the search roots:
    the failed rows of runtime ≤ 1.1.0 carry no `detail.tool`. Unreadable versions are skipped
    (a bare name no root has is not an error: its calls can outlive its versions)."""
    prefix = target.ref if target.version else f"tool/{target.name}@"
    names: set[str] = set()
    for row in registry.listing(search, prefix):
        if row["symlinked"] or (target.version and row["ref"] != target.ref):
            continue
        try:
            data = load_file(Path(row["path"]) / "tool.yaml")
        except (StrictYamlError, OSError):
            continue
        functions = data.get("functions") if isinstance(data, dict) else None
        for entry in functions if isinstance(functions, list) else []:
            name = entry.get("name") if isinstance(entry, dict) else None
            if isinstance(name, str) and FUNCTION.fullmatch(name):
                names.add(f"tool:{name}")
    return sorted(names)


# -- the database ------------------------------------------------------------------------------


@dataclass
class Query:
    target: Target
    since: datetime
    limit: int
    content: bool
    agent: str | None
    legacy: list[str]


ROW_NAMES = ("at", "action", "turn_id", "trace_id", "db_role", "tool", "ok", "agent")
ROW_NAMES += ("latency_ms", "args", "error", "phantoms")


@dataclass
class Result:
    credential: str
    schema: str
    db_role_verified: bool
    rows: list[dict] = field(default_factory=list)
    truncated: bool = False
    phantom_rows: int = 0


_LOCATE = """
SELECT c.relname::text, n.nspname::text, c.oid::bigint,
       pg_catalog.pg_has_role(current_user, c.relowner, 'USAGE') AS agent_owns,
       c.relowner = d.datdba AS owner_made,
       CASE WHEN n.nspname = current_user THEN 0 ELSE 1 END AS rank
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_database d ON d.datname = current_database()
WHERE c.relname IN ('turns', 'audit_events') AND n.nspname IN (current_user, 'public')
ORDER BY rank
"""


def locate(db) -> tuple[str, str]:
    """(schema, credential) per §3.1 steps 1–4, read from the catalog only."""
    found: dict[str, tuple] = {}
    for relname, schema, _oid, agent_owns, owner_made, _rank in db.execute(_LOCATE).fetchall():
        found.setdefault(relname, (schema, agent_owns, owner_made))  # search order: first wins
    if set(found) != {"turns", "audit_events"}:
        raise ToolLogError("TOOL_LOG_NO_TABLES", NO_TABLES)
    schemas = {entry[0] for entry in found.values()}
    if len(schemas) != 1:
        raise ToolLogError(
            "CONSOLE_OBJECT_UNEXPECTED", "turns and audit_events are in different schemas"
        )
    schema = schemas.pop()
    if not SCHEMA.fullmatch(schema):
        raise ToolLogError("CONSOLE_OBJECT_UNEXPECTED", "the log's schema has an unexpected name")
    if all(entry[1] for entry in found.values()):
        return schema, "agent"
    if all(entry[2] for entry in found.values()):
        return schema, "console"
    raise ToolLogError(
        "CONSOLE_OBJECT_UNEXPECTED",
        "turns and audit_events are owned neither by the agent's role nor by the database owner",
    )


_CONSOLE_CHECK = """
SELECT c.relname::text,
       c.relowner = d.datdba AS owner_ok,
       c.relkind = 'r' AS kind_ok,
       NOT c.relispartition AND NOT c.relhassubclass AS plain,
       NOT c.relrowsecurity AND NOT c.relforcerowsecurity AS no_rls,
       NOT EXISTS (SELECT 1 FROM pg_catalog.pg_rewrite r
                   WHERE r.ev_class = c.oid AND r.rulename <> '_RETURN') AS no_rules,
       (SELECT pg_catalog.array_agg(t.tgname::text ORDER BY t.tgname)
          FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
         WHERE t.tgrelid = c.oid AND NOT t.tgisinternal
           AND NOT (p.proname = ANY(%(functions)s) AND p.proowner = d.datdba)) AS bad_triggers,
       NOT EXISTS (SELECT 1 FROM pg_catalog.aclexplode(c.relacl) a
                   WHERE a.privilege_type = 'TRIGGER'
                     AND a.grantee <> c.relowner) AS no_trigger_acl,
       (n.nspowner = d.datdba OR n.nspowner = (SELECT oid FROM pg_catalog.pg_roles
                                                WHERE rolname = 'pg_database_owner')) AS schema_ok
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_database d ON d.datname = current_database()
WHERE n.nspname = %(schema)s AND c.relname IN ('turns', 'audit_events')
ORDER BY c.relname
"""

_CHECKS = {
    "owner_ok": "is not owned by the database owner",
    "kind_ok": "is not a plain table",
    "plain": "is partitioned or has child tables",
    "no_rls": "has row-level security enabled",
    "no_rules": "has a rule",
    "no_trigger_acl": "grants TRIGGER to a role other than its owner",
    "schema_ok": "is in a schema the database owner does not own",
}


def console_check(db, schema: str) -> None:
    """§3.1 defence in depth: nothing on the two relations may run code as the console role."""
    rows = db.execute(
        _CONSOLE_CHECK, {"schema": schema, "functions": list(EXPECTED_TRIGGER_FUNCTIONS)}
    ).fetchall()
    if len(rows) != 2:
        raise ToolLogError("CONSOLE_OBJECT_UNEXPECTED", "turns or audit_events changed")
    names = ("relname", "owner_ok", "kind_ok", "plain", "no_rls", "no_rules", "bad_triggers")
    names += ("no_trigger_acl", "schema_ok")
    for row in rows:
        values = dict(zip(names, row, strict=True))
        relation = f"{schema}.{values['relname']}"
        for check, problem in _CHECKS.items():
            if not values[check]:
                raise ToolLogError("CONSOLE_OBJECT_UNEXPECTED", f"{relation} {problem}")
        if values["bad_triggers"]:
            shown = ", ".join(safe_text.excerpt(t, 63) for t in values["bad_triggers"][:3])
            raise ToolLogError(
                "CONSOLE_OBJECT_UNEXPECTED",
                f"{relation} has a trigger the console role would run: {shown} (only "
                "audit_events_append_only and audit_events_db_role owned by the database owner "
                "are expected)",
            )


_DB_ROLE = """
SELECT a.atthasdef
FROM pg_catalog.pg_attribute a
JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s AND c.relname = 'audit_events' AND a.attname = 'db_role'
  AND a.attnum > 0 AND NOT a.attisdropped
"""

# §3.4: in force now = the trigger exists, is enabled, is exactly BEFORE INSERT FOR EACH ROW
# (tgtype ROW 1 | BEFORE 2 | INSERT 4), calls the owner's function, and no enabled BEFORE INSERT
# row trigger sorts after it; no WHEN condition; the column has no default and the table no rule.
_IN_FORCE = """
SELECT EXISTS (
         SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
          WHERE t.tgrelid = c.oid AND t.tgname = %(trigger)s AND t.tgenabled IN ('O', 'A')
            AND t.tgtype = 7 AND t.tgqual IS NULL
            AND p.proname = 'audit_events_db_role' AND p.proowner = d.datdba)
   AND NOT EXISTS (
         SELECT 1 FROM pg_catalog.pg_trigger t
          WHERE t.tgrelid = c.oid AND NOT t.tgisinternal AND t.tgenabled <> 'D'
            AND (t.tgtype & 71) = 7 AND t.tgname > %(trigger)s::name)
   AND NOT c.relhasrules
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_database d ON d.datname = current_database()
WHERE n.nspname = %(schema)s AND c.relname = 'audit_events'
"""


def db_role_state(db, schema: str, credential: str) -> tuple[bool, bool]:
    """(column present, trigger in force now). Only an owner-made table can be verified."""
    row = db.execute(_DB_ROLE, {"schema": schema}).fetchone()
    if row is None:
        return False, False
    if credential != "console" or row[0]:
        return True, False
    verified = db.execute(_IN_FORCE, {"schema": schema, "trigger": DB_ROLE_TRIGGER}).fetchone()
    return True, bool(verified and verified[0])


def _sql(psycopg, schema: str, has_db_role: bool, content: bool, agent: bool):
    """The narrow scan (one window of `tool:` rows, phantom pairing), then the projections of
    only the page of rows shown: `detail` is detoasted for the page, not the whole window.

    #922 pairing, per (turn, function) in id order: an old-runtime failure (`ok` false, no
    agent, no tool) opens one slot; an old-runtime success row (`ok` true, no agent, a tool)
    fills an open slot and is a phantom, else it is shown. With d = successes − failures so far,
    the unpaired successes are max(0, max d); a row is a phantom when that does not grow."""
    sql = psycopg.sql
    db_role = sql.SQL("a.db_role" if has_db_role else "NULL::text")
    extra = sql.SQL(  # the two limits are this module's int constants, never input
        f", CASE WHEN pg_catalog.pg_column_size(a.detail->'args') <= {int(ARGS_LIMIT)} "
        f"THEN (a.detail->'args')::text END AS args, "
        f"left(a.detail->>'error', {int(ERROR_LIMIT)}) AS error"
        if content
        else ", NULL::text AS args, NULL::text AS error"
    )
    agent_filter = sql.SQL("AND m.agent = %(agent)s" if agent else "")
    return sql.SQL(
        """
WITH calls AS (
  SELECT a.id, a.at, left(a.action, 80) AS action, left(a.turn_id, 64) AS turn_id,
         left(a.detail->>'tool', 200) AS tool,
         CASE WHEN jsonb_typeof(a.detail->'ok') = 'boolean'
              THEN (a.detail->>'ok')::boolean END AS ok,
         left(a.detail->>'agent', 64) AS agent,
         a.detail ? 'agent' AS has_agent
  FROM {audit} a
  WHERE a.at >= %(scan_since)s
    AND a.at <= pg_catalog.clock_timestamp() + interval '5 minutes'
    AND a.action LIKE 'tool:%%' AND a.detail->'mcp' IS NULL
), steps AS (
  SELECT c.*,
         CASE WHEN c.turn_id IS NULL OR c.has_agent THEN 0
              WHEN c.ok IS TRUE AND c.tool IS NOT NULL THEN 1
              WHEN c.ok IS FALSE AND c.tool IS NULL THEN -1
              ELSE 0 END AS step
  FROM calls c
), running AS (
  SELECT s.*, sum(s.step) OVER (PARTITION BY s.turn_id, s.action ORDER BY s.id
                                ROWS UNBOUNDED PRECEDING) AS d
  FROM steps s
), paired AS (
  SELECT r.*,
         (r.step = 1 AND greatest(0, max(r.d) OVER w)
            = greatest(0, coalesce(max(r.d) OVER (w ROWS BETWEEN UNBOUNDED PRECEDING
                                                  AND 1 PRECEDING), 0))) AS phantom
  FROM running r
  WINDOW w AS (PARTITION BY r.turn_id, r.action ORDER BY r.id)
), matched AS (
  SELECT m.id, m.at, m.agent, m.phantom FROM paired m
  WHERE m.at >= %(since)s
    AND (m.tool LIKE %(pattern)s ESCAPE '\\'
         OR (m.tool IS NULL AND m.action = ANY(%(legacy)s)))
), page AS (
  SELECT m.id, m.at FROM matched m
  WHERE NOT m.phantom {agent_filter}
  ORDER BY m.at DESC, m.id DESC
  LIMIT %(limit)s
), counted AS (
  SELECT count(*) AS phantoms FROM matched WHERE phantom
)
SELECT a.id, a.at, left(a.action, 80) AS action, left(a.turn_id, 64) AS turn_id,
       left(t.trace_id, 64) AS trace_id, left({db_role}, 64) AS db_role,
       left(a.detail->>'tool', 200) AS tool,
       CASE WHEN jsonb_typeof(a.detail->'ok') = 'boolean'
            THEN (a.detail->>'ok')::boolean END AS ok,
       left(a.detail->>'agent', 64) AS agent,
       CASE WHEN jsonb_typeof(a.detail->'latency_ms') = 'number'
             AND length(a.detail->>'latency_ms') <= 12
            THEN (a.detail->>'latency_ms')::numeric END AS latency_ms
       {extra},
       c.phantoms
FROM counted c
LEFT JOIN (page p JOIN {audit} a ON a.id = p.id LEFT JOIN {turns} t ON t.id = a.turn_id) ON true
ORDER BY p.at DESC NULLS LAST, p.id DESC
"""
    ).format(
        db_role=db_role,
        extra=extra,
        audit=sql.Identifier(schema, "audit_events"),
        turns=sql.Identifier(schema, "turns"),
        agent_filter=agent_filter,
    )


def fetch(agent_url: str, console_url: str | None, query: Query) -> Result:
    psycopg = dbread.psycopg_module("tool log")
    hints = {"no_tables": NO_TABLES, "timeout": "narrow --since"}
    with read_session(psycopg, agent_url, "agent", PREFIX, **hints) as db:
        schema, credential = locate(db)
    if credential == "console" and console_url is None:
        raise ToolLogError(
            "CONSOLE_ROLE_MISSING",
            "The log tables are owned by the database owner; reading them needs "
            "service/postgres-pgvector@1.2.0 with console_database set (and the owner's "
            "grants), then `agentbreeder services up --update`",
        )
    url = console_url if credential == "console" else agent_url
    with read_session(psycopg, url, credential, PREFIX, **hints) as db:
        if credential == "console":
            console_check(db, schema)
        has_db_role, verified = db_role_state(db, schema, credential)
        statement = _sql(psycopg, schema, has_db_role, query.content, bool(query.agent))
        params = {
            "scan_since": query.since - PAIRING_MARGIN,
            "since": query.since,
            "pattern": query.target.pattern(),
            "legacy": query.legacy,
            "limit": query.limit + 1,
            "agent": query.agent,
        }
        rows = db.execute(statement, params).fetchall()
    result = Result(credential, schema, verified)
    calls = [row for row in rows if row[0] is not None]  # no page rows: one row of counts
    result.truncated = len(calls) > query.limit
    for row in calls[: query.limit]:
        result.rows.append(dict(zip(ROW_NAMES, row[1:], strict=True)))
    if rows and not query.agent:
        result.phantom_rows = int(rows[0][-1] or 0)
    return result


# -- shaping hostile rows (S-35) ---------------------------------------------------------------


def _match(pattern: re.Pattern, value) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _at(value) -> str | None:
    if not isinstance(value, datetime):
        return None
    value = value.astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _latency(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    if value != int(value) or not 0 <= value <= 86_400_000:
        return None
    return int(value)


def _clean(value, redact: Redactor, depth: int = 0):
    if depth >= ARGS_DEPTH:
        return "…"
    if isinstance(value, str):
        return safe_text.excerpt(redact(value), TEXT_LIMIT)
    if isinstance(value, dict):
        return {
            safe_text.excerpt(redact(str(k)), 200): _clean(v, redact, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_clean(v, redact, depth + 1) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _args(text, redact: Redactor):
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return _clean(value, redact) if isinstance(value, dict) else None


def shape(row: dict, content: bool, redact: Redactor) -> dict:
    """One call as shown: every field validated, else null."""
    action = row.get("action")
    function = action.removeprefix("tool:") if isinstance(action, str) else None
    ok = row.get("ok")
    call = {
        "at": _at(row.get("at")),
        "agent": _match(AGENT, row.get("agent")),
        "tool": _match(TOOL_REF, row.get("tool")),
        "function": _match(FUNCTION, function),
        "status": ("ok" if ok else "error") if isinstance(ok, bool) else None,
        "latency_ms": _latency(row.get("latency_ms")),
        "turn_id": _match(HEX, row.get("turn_id")),
        "trace_id": _match(HEX, row.get("trace_id")),
        "db_role": _match(DB_ROLE, row.get("db_role")),
    }
    if content:
        error = row.get("error")
        call["args"] = _args(row.get("args"), redact)
        call["error"] = (
            safe_text.excerpt(redact(error), ERROR_LIMIT) if isinstance(error, str) else None
        )
    return call
