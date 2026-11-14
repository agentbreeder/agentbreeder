# `agentbreeder tool`

List a registry tool's recorded calls from the stack's database (read-only).

On `main`, not in a published preview.

```
agentbreeder tool log REF [--project DIR] [--environment ENV] [--since D] [--agent NAME]
                          [--limit N] [--content] [--json]
```

`REF` is `tool/NAME` (every version, including versions since removed or forked away) or
`tool/NAME@X.Y.Z`. MCP tool calls are not in this log. `--project` is the directory with
`stack.yaml` (default `.`); `--environment` picks whose stack state to use when `services up`
wrote more than one. `--since` takes `90s`, `10m`, `2h` or `2026-11-11T09:00:00Z` (default `24h`,
at most 720 hours back). `--limit` is 1 to 1000 (default 100). `--agent NAME` keeps only that
agent's calls; rows written before `runtime/adk@1.2.0` name no agent and are left out. Needs
`psycopg`: `pip install "agentbreeder[eval]"`.

## What it reads

The agent runtime writes one `audit_events` row per tool call (`action` `tool:<function>`); the
log joins it to `turns` for the trace id. Columns: time, agent, version, function, status,
latency, turn id, trace id and db role. A `Phoenix:` line gives the stack's Phoenix URL, where a
trace id can be looked up.

- **It is what the runtime claims it called.** The agent's database role can insert any row, so
  every value is validated before it is shown (a malformed field shows as `-`, `null` in JSON),
  and text is stripped of control and bidirectional characters.
- **Arguments only with `--content`.** `--content` adds each call's arguments (`null` over 16 KiB)
  and error text, after stack-secret redaction, and prints "Arguments are customer data." on
  stderr first. Without it, arguments are not even selected.
- **Results are not stored.** The runtime keeps tool results out of the database; the trace holds
  them.

## Which credential

The CLI reads the database bound as the agent's `DATABASE_URL` (a `service/postgres-pgvector`
`databases.NAME` or `roles.NAME` output), using the host-form secrets `services up` writes. It
first looks at the catalog with the agent's own credential, then:

- **The agent owns `turns` and `audit_events`** (a runtime-managed stack, such as the
  `new agent` starter, where the agent's credential is the database owner's): it reads them with
  that credential. `db_role_verified` is false there (the agent could drop the trigger), and
  `db_role` is `null` unless the table has the column.
- **The database owner made them** (an owner-managed stack, such as Northwind): it reads them as
  `agentbreeder_console`, which needs `service/postgres-pgvector@1.2.0` with `console_database`
  set and the owner's `init_sql` grants (Northwind's `stack/db/commerce.sql` shows the guarded
  `GRANT` block). In the same read-only transaction it refuses (`CONSOLE_OBJECT_UNEXPECTED`) if
  either table has a trigger other than `audit_events_append_only` or `audit_events_db_role`
  owned by the database owner, row-level security, a rule, partitions, a TRIGGER grant to
  another role, or a schema the owner does not own. A project that adds its own trigger to these
  tables is refused this way on purpose.

A login or permission error is reported, never retried with the other credential.

`db_role` is the database login that inserted the row, set by the owner's trigger; rows the
console role inserted are marked `(console)`. `db_role_verified` is true only when that trigger
is in force now; it says nothing about when older rows were written. It is attribution, not
tamper-evidence: the database owner can drop the trigger or rewrite rows.

## Rows from older runtimes

- `runtime/adk` 1.0.6 and 1.1.0 write a failed call twice: the failure, then a second row that
  claims `ok: true` (#922). Per turn and function, `tool log` pairs each such failure (no agent,
  no tool version) with the next success row that names no agent but names a tool version, leaves that row out, and
  counts it in `phantom_rows` with a note. Parallel failed calls of one function pair one to one.
  Pairing looks 5 minutes before `--since`, so a failure just outside the window still hides its
  phantom; the count covers the whole window, whatever `--limit` shows. On those runtimes, a
  real failure followed by a real success of the same function in the same turn looks the same
  and is left out too, and with parallel calls the row left out may be the real success rather
  than the phantom (the count is right; which row is ambiguous). Rows from `runtime/adk@1.2.0`
  (which name the agent) are never paired.
- Their failed rows carry no tool version: they are matched by function name (every function
  the name's versions declare in the project's registry roots) and shown with version `-`. A
  function name another tool shares would also match.
- They have no agent and no latency (`-`); a note counts them.

Rows dated more than 5 minutes after the database's clock (the agent can write any time) are
outside every window and not shown.
When the agent role owns `turns` and `audit_events` in its own schema, those are the tables read
(as the runtime itself would), and a note names the schema.

## JSON

`--json` prints one indented object: `schema_version`, `ref`, `environment`, `database`,
`credential` (`console` or `agent`), `schema`, `window` (`since`, `until`), `limit`, `truncated`,
`content`, `db_role_verified`, `phoenix_url`, `phantom_rows`, `notes` and `calls` (each `at`,
`agent`, `tool`, `function`, `status` (`ok`, `error` or `null`), `latency_ms`, `turn_id`,
`trace_id`, `db_role`, plus `args` and `error` with `--content`). No match is exit 0 with
`calls: []`.

## Errors (exit 2)

`[CODE] message` on stderr; with `--json` also `{"schema_version": 1, "error": {"code",
"message"}}` on stdout. Messages never include a connection string.

| Code | When |
|---|---|
| `REF_INVALID`, `SINCE_INVALID`, `LIMIT_INVALID` | a bad argument |
| `AGENT_NOT_FOUND` | `--agent` is not in the stack's `environment.agents` |
| `ENVIRONMENT_AMBIGUOUS` | several environments written and no `--environment` |
| `SERVICES_DOWN` | no stack state for the environment, or the database is unreachable |
| `SERVICES_STALE` | the state predates the host-form secrets: run `services up` |
| `TOOL_LOG_UNSUPPORTED` | `DATABASE_URL` is not bound to a postgres-pgvector database or role |
| `TOOL_LOG_NO_TABLES` | no turn recorded yet (or `agent_events` off) |
| `CONSOLE_ROLE_MISSING` | owner-made tables and no `console_database` |
| `CONSOLE_ROLE_NO_ACCESS` | the owner's `init_sql` has not granted the console role |
| `CONSOLE_OBJECT_UNEXPECTED` | the catalog check above failed |
| `DB_LOGIN_FAILED` | a credential was refused (the message says which): run `services up` |
| `TOOL_LOG_TIMEOUT` | the 5 s statement timeout: narrow `--since` (an agent writing huge rows can cause this) |
| `TOOL_LOG_NO_ACCESS`, `TOOL_LOG_FAILED` | another permission or database error, or a stored value that cannot be read |
| `DEPENDENCY_MISSING` | no `psycopg` |

Verified against real Postgres (`service/postgres-pgvector@1.2.0`, both layouts) and with an
agent built on `runtime/adk@1.2.0` driven by a scripted fake model gateway, not with a provider
model or the full Northwind run.

Flags: `agentbreeder tool log --help`.
