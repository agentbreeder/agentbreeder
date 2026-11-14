# M12 spec delta: tools, and the read-only console role (#828)

- Status: **r3, reviews converged** (architecture and security, Claude Sonnet, three rounds; §10). Reviews: §10. No code in this PR.
- Issue #828 (M12, epic #812). Plan of record: `docs/superpowers/plans/2026-11-11-console-epic.md`
  (M12, M14, M16). This delta decides what the plan left to "the M12 spec delta" and corrects the
  plan where the code differs (§2). Where this delta and console spec §4.3/§8.3 differ, this delta
  wins; the plan's M12 section is updated to point here.
- Builds on: `service/postgres-pgvector@1.1.0` (`roles`, `init.sh`), `runtime/adk@1.1.0`
  (`memory.py`, `agent.py`, `rag.py`, `config.py`), Northwind's `stack/db/commerce.sql`,
  `newagent.py` (starter stack), `services/schema.py` (`operator_only`), `redaction.py`,
  `registry edit` / `registry fork` / `agent set-ref` (#894, #904), and the security review
  findings S-04, S-06, S-22, S-32, S-33 (console spec §10.3).

## 1. What this delta decides

| | Question | Decision (section) |
|---|---|---|
| a | How the per-stack console role gets its rights | One fixed-name role `agentbreeder_console`, created by `postgres-pgvector@1.2.0` (`console_database`), granted **only on tables the database owner made**, by the owner's `init_sql` with plain guarded `GRANT`s. Agent-owned objects (session and knowledge tables, everything in the starter) are read with the agent's own credential and get no grant. So **no runtime-side `GRANT`**, no dedicated schema, never `ALTER DEFAULT PRIVILEGES`. S-32 by a `session_user` trigger (§3) |
| b | The `tool log` CLI contract | Reads `audit_events` (one row per tool call) joined to `turns`, read-only, windowed, LIMITed, projected in SQL, every field validated; `--content` adds arguments and error text (§4) |
| c | Tool fork → edit → set-ref → release | The #894/#904 commands unchanged, plus tool checks (allowed files, no shadowing, S-06 `ast`, no dependency files, S-33) and `registry show` tool fields (§5) |
| d | How #909 will constrain tool grants | Seams only: where a principal and a policy decision plug in later (§6) |

## 2. Code facts that change the plan

1. **`turns.tools` holds function names only** (`agent.py` `before_tool_callback` appends
   `tool.name`). It has no tool ref, version, arguments, status or latency, so the plan's "reads
   `turns.tools`" cannot produce the columns it lists. **The per-call record is `audit_events`:**
   one row per call, `action = 'tool:<function>'`, `detail = {args, ok, error, tool:
   "tool/<name>@<version>", …}` (`after_tool_callback`, `on_tool_error_callback`), `turn_id`
   equal to `turns.id` (the runtime runs ADK with `invocation_id=turn`).
2. **Gaps in that record (runtime ≤ 1.1.0):** the error path writes no `detail.tool`; no row
   records the **agent** (several agents can share one database: the stack binds one
   `DATABASE_URL` for the environment); no per-call **latency**; tool **results** are not stored
   anywhere in the database (only in the Phoenix span, `set_output`).
3. **Tools have no dependencies of their own.** `tool.yaml` has only `functions`; tool code runs
   with the runtime's hash-locked `runtime/requirements.lock`, installed in Docker with
   `--require-hashes --only-binary=:all:`. There is nothing to re-lock per tool today.
4. **Northwind's `tool/orders@1.0.0` is a project version** (`examples/northwind/registry/`), so
   `registry edit` applies to it directly; `registry fork` is only needed for built-in or
   `AGENTBREEDER_REGISTRY_PATH` tools.
5. **Two database layouts exist.** The starter (`newagent.py`) binds `DATABASE_URL` to the
   database owner (`postgres.databases.app`): the runtime creates and owns its tables. Northwind
   binds a least-privilege role (`postgres.roles.support_agent`) and its `commerce.sql`
   (`init_sql`, run as the owner on every `services up`) creates the runtime tables; the runtime
   then runs no DDL (`memory.owner_managed`). The runtime still creates and owns the knowledge
   tables (`rag.py`, in its own schema) and ADK's session tables (in `adk_sessions`, a schema
   `support_agent` owns) in both layouts.
6. `memory.owner_managed()` returns true whenever all four tables **exist**, including tables
   the runtime itself created on an earlier start, so today a runtime upgrade never migrates its
   own tables.
7. Northwind and the starter pin `runtime/adk@1.0.6`, not 1.1.0.
8. A service output can be `operator_only` (`services/schema.py`): never wired to another
   service, bound into an agent or used as the eval database. A secret template may name a
   non-`per` secret together with `{item}` (`_check_output_template`). Every stack secret is
   already in `redaction.redaction_values()`.

## 3. (a) The console role and how it gets its rights

### 3.1 The rule: never more rights than the object's owner already has

A role that runs a statement on a table runs that table's triggers, rules and row-level-security
policies, and every function they call, **with its own privileges**. So a role granted DML or even
SELECT on a table another role owns lends its privileges to that owner. In Northwind the agent role
`support_agent` owns `adk_sessions`, its own schema and every table ADK and `rag.py` create there;
a compromised agent could put a trigger or an RLS policy on them that, run as a more privileged
console role, deletes `turns`, reads `audit_events` or writes forged audit rows (security review,
§10 round 1, finding 1).

Hence the rule this delta uses for every console read and write:

| Objects | Owner | Credential the CLI child uses | Grants needed |
|---|---|---|---|
| `turns`, `audit_events`, `notes`, `tickets` in an **owner-managed** stack (Northwind) | the database owner (`init_sql`) | `agentbreeder_console` | the owner's `init_sql` grants them (§3.3) |
| Session tables (`adk_sessions`), knowledge tables (`kb_*`) | the agent's role (it creates them) | **the agent's own `DATABASE_URL`** | none |
| Everything in a **runtime-managed** stack (starter: the agent *is* the database owner) | the agent's role | **the agent's own `DATABASE_URL`** | none |

- A trigger or policy the agent plants on its own objects then runs with rights the agent already
  has: nothing is lent. Where the agent is the database owner (starter) there is no boundary to
  protect inside the database at all, and the docs say so.
- The console role is therefore **only granted on objects the database owner made**, which the
  agent cannot alter: the agent role does not own them and is not a member of the owner
  (`pg_has_role(agent, relowner, 'USAGE')` is false; in Northwind the owner is a member of
  `support_agent`, not the reverse), and it holds no TRIGGER privilege on them (checked below).
- `tool log` (M12) and M16's notes, `turns`/`tickets` deletes and audit rows use the console role
  on an owner-managed stack. M14's knowledge status and M16's session list/view/delete use the
  agent's credential in both layouts.
**Choosing the credential** (every M12/M14/M16 command, before any read of a data table):

1. Connect with the agent's credential (host form, §3.5; options `-c
   default_transaction_read_only=on -c search_path=pg_catalog,pg_temp`, connect timeout 5 s) and
   read only the catalog: the owner of `turns` and `audit_events` in the schemas the runtime's
   unqualified names resolve to (the agent role's own schema, then `public`). If neither table
   is found (a runtime-managed stack before its first turn), the answer is `TOOL_LOG_NO_TABLES`
   before any credential is chosen; steps 2–4 apply only when both are found. The schema found
   here (validated as an identifier, quoted with `%I`) is the one every later check and query
   uses; `public.` in this spec stands for it.
2. If the agent role owns both or is a member of their owner → **agent credential**
   (runtime-managed; in the starter that is the database owner's credential, which `release`
   already reads as `eval_database`; the docs say the CLI connects as the owner there).
3. Else if both are owned by the database owner (`relowner = datdba`) → **console credential**;
   without a `console` secret, `CONSOLE_ROLE_MISSING`. (Northwind's agent role has no SELECT on
   `turns` or `audit_events`, so it could not read them anyway.)
4. Else `CONSOLE_OBJECT_UNEXPECTED`.
5. **Never fall back.** A permission or login error with the chosen credential is a refusal
   (§4.4), never a retry with the other one.

**Console-role check** (defence in depth; grants are the boundary). In the same transaction as
each statement, as the console role, for every relation it touches: `relowner = datdba`;
`relkind = 'r'`; `relispartition` and `relhassubclass` false; `relrowsecurity` and
`relforcerowsecurity` false; no rule other than `_RETURN`; every non-internal trigger calls one
of the expected functions (`audit_events_append_only`, `audit_events_db_role`) owned by the
database owner; no role other than the owner holds TRIGGER on it (`aclexplode(relacl)`); and the
schema found in step 1 is owned by the database owner or `pg_database_owner` (a NULL `relacl`
means default privileges: no extra TRIGGER holder). Any miss is `CONSOLE_OBJECT_UNEXPECTED`,
naming the relation and trigger, and nothing runs. A project that adds its own trigger to these
tables in owner SQL is refused this way on purpose (fail closed); the docs say so. Tested by granting TRIGGER to the agent role and
planting a trigger, and by enabling RLS.

**This replaces the plan's "a new `runtime/adk` version that grants them access after creating its
tables".** The runtime grants nothing. Considered and rejected:

- **Runtime-side `GRANT` after table creation** (the plan's first option): the runtime would grant
  the console role rights on agent-owned objects, which is exactly the lending above.
- **A second console role for agent-owned objects only:** equivalent in rights to the agent's own
  credential, at the cost of another secret and runtime grant code. Using the agent's credential is
  simpler and lends nothing.
- `ALTER DEFAULT PRIVILEGES`: every future table its role creates would become readable (and
  lendable). Excluded by the plan.
- **A dedicated schema** for console-readable objects: the tables live where the runtime, ADK and
  owners already put them; moving them is a data migration for every existing stack, and ADK picks
  its own tables.
- **Grants from `init.sh` as superuser:** runs before the runtime creates its tables, and would need
  every agent's layout.

### 3.2 The role (`service/postgres-pgvector@1.2.0`)

- New param `console_database: {type: db_name}` (one database, like `roles_database`; M12, M14
  and M16 all use the one agent database), passed to `init` as `CONSOLE_DATABASE` in `env` (only
  scalar params reach a service's environment, `services/plan.py`). It must be a declared
  `databases` item (else `init` exits 3). Unset means no console role (the 1.1.0 behaviour); the
  starter leaves it unset (§3.1).
- New secret `console: {generate: token}` (one value) and output
  `console: {type: secret, port: pg, operator_only: true, template:
  "postgresql://agentbreeder_console:{secret.console}@{address}/{params.console_database}"}`.
  `operator_only`: never bound into an agent, wired to another service or used as
  `eval_database`.
- `init.sh`, after the `databases` loop and **before** `roles` and `init_sql` (so `init_sql` can
  grant to it): when `CONSOLE_DATABASE` is set, create or alter the login role
  **`agentbreeder_console`** with the attributes of every managed role (`NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS`, password on stdin), `CONNECTION LIMIT 10`, `GRANT
  CONNECT` on that database, and nothing else: no schema of its own, no membership in or of
  any role, no `CREATE`, no `TEMP` (`init.sh` already revokes all database rights from PUBLIC for
  every declared database). `agentbreeder_console` is reserved: `check_name` refuses it for
  `databases` and `roles`, and an existing database of that name fails closed.
- **Lifecycle both ways:** every run revokes `CONNECT` from `agentbreeder_console` on every other
  database; when `CONSOLE_DATABASE` is unset and the role exists, it runs `ALTER ROLE
  agentbreeder_console NOLOGIN`, revokes its `CONNECT` everywhere and ends its sessions
  (`pg_terminate_backend`), so a removed console credential stops working. The old secret file
  stays on disk (redacted) until the stack is purged. `CONSOLE_DATABASE` goes through
  `check_name` like every name. Grants an owner's `init_sql` made stay until that SQL revokes them
  (harmless without login).
- Fixed name: a stack is one Postgres cluster, so one role per stack; owners' `init_sql` names it.
- **One role, not a read role and a write role:** both secrets would sit in the same directory,
  readable by the same OS user. Readers instead connect with `options=-c
  default_transaction_read_only=on -c search_path=pg_catalog,pg_temp` (M12 `tool log`, M16 lists); M16
  writers set only the search path. Every query names its tables schema-qualified
  (`public.audit_events`).
- What the role can still see, documented: `pg_catalog` (names of roles, databases, schemas),
  EXECUTE on functions PUBLIC can run, `nextval` on the granted sequences (gaps in ids only).

### 3.3 Grants (owner-managed stacks)

Made by the database owner's `init_sql` (Northwind: `commerce.sql`), which creates these tables,
as plain idempotent `GRANT`s; one version carries every grant M12, M14 and M16 need. `init_sql`
runs as one transaction, and a `GRANT` to a missing role fails it, so owner SQL uses this guarded
form (the documented pattern, tested with `console_database` unset):

```sql
DO $$ BEGIN
  IF to_regrole('agentbreeder_console') IS NOT NULL THEN
    GRANT SELECT, DELETE ON public.turns, public.tickets TO agentbreeder_console;
    -- … the rest of the table below
  END IF;
END $$;
```


| Object | Privileges for `agentbreeder_console` | For |
|---|---|---|
| `public.turns` | SELECT, DELETE | M12 log; M16 views and `forget` |
| `public.audit_events` | SELECT; INSERT (`turn_id, session, customer, actor, action, detail`); USAGE on `audit_events_id_seq` | M12 log; M16 audit rows |
| `public.notes` | SELECT, INSERT, UPDATE, DELETE; USAGE on `notes_id_seq` | M16 |
| `public.tickets` | SELECT, DELETE | M16 `forget` |

Session and knowledge tables get no grant (§3.1). The plan's M16 list omits DELETE on `turns` and
`tickets`; console spec §8.3 defines "delete a customer" over notes, sessions, turns and tickets, so
both are added. `forget` leaves `audit_events` rows, which hold `customer` and tool arguments; M16
says so (S-19).

### 3.4 Telling console rows from runtime rows (S-32)

The console role can INSERT `audit_events` rows whose `actor`, `action` and `detail` look like the
runtime's. The database records who inserted, owned by the table's owner:

```sql
ALTER TABLE public.audit_events ADD COLUMN IF NOT EXISTS db_role TEXT;
CREATE OR REPLACE FUNCTION public.audit_events_db_role() RETURNS trigger LANGUAGE plpgsql
  SET search_path = pg_catalog AS $$
BEGIN NEW.db_role := session_user; RETURN NEW; END $$;
CREATE OR REPLACE TRIGGER audit_events_set_db_role BEFORE INSERT ON public.audit_events
  FOR EACH ROW EXECUTE FUNCTION public.audit_events_db_role();
```

- **`session_user`, the login role,** not `current_user`: a role that is a member of another (the
  Northwind owner is a member of `support_agent`) can `SET ROLE` and change `current_user`, but not
  the login role without superuser.
- **A trigger, not a `DEFAULT` with a column-excluding grant:** it overrides any value whatever the
  inserter's grants (Northwind's `support_agent` holds table-level INSERT), for `INSERT`, `COPY`
  and `ON CONFLICT`; only the table owner can drop or disable it.
- **Nullable, no default:** `ADD COLUMN … DEFAULT` would stamp every existing row with the
  migrating role. Older rows stay NULL: "unknown (before this change)".
- **Who runs it:** the owner's `init_sql` (Northwind's `commerce.sql`), at `services up`.
- **The reader checks it is in force now:** `db_role_verified` is true only when, on an
  owner-made `audit_events` (§3.1 step 3), `audit_events_set_db_role` exists, is enabled
  (`tgenabled` `O` or `A`), is `BEFORE INSERT FOR EACH ROW` (`tgtype`), calls
  `audit_events_db_role` owned by the database owner, no enabled `BEFORE INSERT` trigger sorts
  after it, and `db_role` has no column default and the table no rule. It describes the table now,
  not when older rows were written; output and docs say "trigger in force now".
- **Only owner-managed stacks get it.** In a runtime-managed stack (starter) the agent owns
  `audit_events` and could drop the trigger, so 1.2.0 adds neither column nor trigger there;
  `db_role` is `null` and `db_role_verified` false. S-32 is met on owner-managed stacks only; the
  console spec's S-32 resolution and M16's acceptance record that.
- **What it is and is not:** attribution of the database login, not tamper-evidence. The owner can
  drop the trigger or rewrite rows; a restore with `session_replication_role = replica` skips it.
  All agents of a stack share one role, so `db_role` names a role, not an agent; and the agent role
  can INSERT rows for any tool, so the execution log is what the runtime **claims** it called.
- Console-inserted rows (`db_role = agentbreeder_console`) are marked as such in every list.

### 3.5 The credentials (S-22)

- **Host-form URLs (lane A, a prerequisite of M12).** Today `services up` writes agent bindings
  in `agents` scope (`postgres:5432`) and only `eval_database` in host scope
  (`services/operator.py:164-171`); the `console` output is written nowhere, and computing a host
  URL otherwise means `StackEngine.plan()`, which locks and allocates ports. So `services up`
  also writes, beside `eval-database` in the environment's deploy state: `agent-database` (the
  `DATABASE_URL` binding in host form) and, when `console_database` is set, `console-database`
  (the console output in host form). CLI children read them with `secret_files` as `release`
  reads `eval-database`. This touches `services/operator.py` (lane A) and goes through the
  coordinator; M12's CLI work depends on it.
- The three names (`eval-database`, `agent-database`, `console-database`) are reserved: `build`
  and `deploy` refuse an `agent.yaml` `secrets:` reference to them (`SECRET_RESERVED`), so a
  manifest cannot mount them into an agent and bypass `operator_only` (part of the lane-A change,
  with a test; `eval-database` has this exposure today).
- `services down --purge` and `remove` delete them like the other deploy-state secrets; evidence
  and bundle collection never copy them unredacted (test beside the redaction one).
- Both are covered by `redaction_values()` (URL and bare password; a test checks the new files),
  read only by CLI children, never by the console server process (the §8.1 #3 guard test forbids
  `services.state` and `secret_files`), never shown.
- Connection errors are reported by SQLSTATE class and a fixed message, never with the DSN or
  psycopg's conninfo text.
- `services env` and `services status` never print `operator_only` outputs (tests, §8).
- Postgres can echo a failed `ALTER ROLE … PASSWORD` statement in its server log; `services logs`
  already redacts stack secrets, and a test covers this one.

### 3.6 `runtime/adk@1.2.0`

From 1.1.0 (so Northwind and the starter, on 1.0.6, also get MCP support):

- **No grants and no migration** (§3.1, §3.4). §2.6 (`owner_managed` by existence) therefore needs
  no change in M12: the runtime never alters its tables.
- **Shadowing check at load (authoritative, §5):** before `sys.path.insert(0, src)`, for each
  top-level name in a tool's `src/`, refuse if `importlib.util.find_spec(name)` resolves on the
  original `sys.path` (a snapshot taken once, before the first tool's `src/` is inserted), if it is in `sys.stdlib_module_names` or `sys.builtin_module_names`, if it
  matches `__*__`, or if another tool of the agent has the same top-level name.
- The record additions in §4.5.
- Its manifest declares `memory_schema: 2` (1 = every earlier version, implied), as M16 requires.

### 3.7 Versions, relock and upgrade

- **Runtime jump:** Northwind and the starter move from 1.0.6 to 1.2.0, skipping 1.1.0. The pinned
  requirements are identical; 1.0.6 → 1.1.0 changes `agent.py`, `server.py`, `telemetry.py` and
  adds `mcp_client.py`, `redaction.py` (MCP support). "Northwind 61/61 on 1.2.0" is an explicit
  gate.
- **Starter** (`newagent.py`): `RUNTIME = "runtime/adk@1.2.0"`; the stack stays on
  `service/postgres-pgvector@1.1.0` (no console role needed, §3.1).
- **Northwind:** `stack.yaml` moves to 1.2.0 with `console_database: support`; `commerce.sql` gains the
  §3.3 grants and the §3.4 migration; `agent.yaml` moves to `runtime/adk@1.2.0`; both locks are
  regenerated; 61/61 must still pass.
- **An existing owner-managed stack** edits its `stack.yaml` ref, adds `console_database` and the
  guarded grants to its `init_sql`, and runs `services up --update`: the changed `init_sql` hash
  is a `path:` lock entry, which needs `--update`; the new service ref is a new lock key, which
  plain `services up` accepts too (`--locked` refuses both). The changed service spec may
  recreate the postgres container; its volume persists. `init` is idempotent. Runtime: `agent set-ref runtime/adk@… runtime/adk@1.2.0`,
  `build`, `release`.
- **Mixed versions:** runtime ≤ 1.1.0 inserts by column name, unaffected by the new column and
  trigger; it just lacks the §4.5 fields.

## 4. (b) `agentbreeder tool log`

### 4.1 Synopsis

```
agentbreeder tool log REF [--project DIR] [--environment ENV] [--since D] [--agent NAME]
                          [--limit N] [--content] [--json]
```

- `REF` is `tool/NAME` (every version) or `tool/NAME@X.Y.Z`. Another kind is `REF_INVALID`
  (MCP tools are M15's).
- `--project DIR` (default `.`): the directory with `stack.yaml`.
- `--environment ENV`: whose stack state (secrets, host ports) to use. Default: the only
  environment the stack has written; several and none given is `ENVIRONMENT_AMBIGUOUS`; none
  written is `SERVICES_DOWN`.
- **Which database and credential.** The database is the one `environment.bindings.DATABASE_URL`
  points at (`postgres.databases.X` → X, `postgres.roles.R` → `roles_database`; anything else is
  `TOOL_LOG_UNSUPPORTED`). The credential is chosen by §3.1's procedure: the console role for
  owner-made tables, the agent's own credential for runtime-managed ones, never a fallback. Both
  come from the host-form secrets `services up` writes in the deploy state (§3.5, a lane-A
  prerequisite); a stack whose state predates them is `SERVICES_STALE` ("run `services up`").
- `--since D`: `90s`, `10m`, `2h` or `2026-11-11T09:00:00Z` (`runtime.SINCE`, as `logs --since`);
  default `24h`, at most 720 hours back (`SINCE_INVALID`). The window ends now.
- `--agent NAME`: only rows whose `detail.agent` is NAME (runtime ≥ 1.2.0 rows; older rows have
  no agent and are left out, which `notes` says). NAME must be in `environment.agents`
  (`AGENT_NOT_FOUND`).
- `--limit N`: default 100, 1–1000 (`LIMIT_INVALID`).
- `--content`: adds `args` and `error`. Prints a stderr note first: "Arguments are customer data."
- Needs `psycopg`, which is in the `[eval]` extra: without it, `DEPENDENCY_MISSING` with the
  `pip install "agentbreeder[eval]"` hint (as `EVAL_DEPENDENCY`).
- Deferred: `--customer ID` (M16, with its customer-data handling).

### 4.2 Query

Connection options `-c default_transaction_read_only=on -c search_path=pg_catalog,pg_temp`
(for either credential), connect timeout 5 s, then in the transaction `SET LOCAL statement_timeout = '5s'`, `SET LOCAL lock_timeout = '1s'`,
`SET LOCAL idle_in_transaction_session_timeout = '10s'`. Projection happens **in SQL**, so a huge
or hostile `detail` never reaches the client and arguments are not selected without `--content`:

```sql
SELECT a.at, left(a.action, 80) AS action, left(a.turn_id, 64) AS turn_id,
       left(t.trace_id, 64) AS trace_id,
       left(a.db_role, 64) AS db_role,       -- NULL::text when the column is absent (below)
       left(a.detail->>'tool', 200) AS tool,
       CASE WHEN jsonb_typeof(a.detail->'ok') = 'boolean'
            THEN (a.detail->>'ok')::boolean END AS ok,
       left(a.detail->>'agent', 64) AS agent,
       CASE WHEN jsonb_typeof(a.detail->'latency_ms') = 'number'
             AND length(a.detail->>'latency_ms') <= 12
            THEN (a.detail->>'latency_ms')::numeric END AS latency_ms
       -- with --content only:
       -- , CASE WHEN pg_column_size(a.detail->'args') <= 16384 THEN a.detail->'args' END AS args
       -- , left(a.detail->>'error', 2000) AS error
FROM public.audit_events a LEFT JOIN public.turns t ON t.id = a.turn_id
WHERE a.at >= %(since)s AND a.action LIKE 'tool:%%'
  AND (a.detail->>'tool' LIKE %(ref_pattern)s ESCAPE '\'
       OR (a.detail->>'tool' IS NULL AND a.detail->'mcp' IS NULL
           AND a.action = ANY(%(legacy_actions)s)))
ORDER BY a.at DESC LIMIT %(limit_plus_one)s
```

- `ref_pattern` is the exact ref for a version, or `tool/NAME@%` (name escaped) for every version,
  so calls from versions since removed or forked away still match.
- `legacy_actions` are `tool:<function>` for every function the name's versions in the project's
  search roots declare: the error rows of runtime ≤ 1.1.0, which carry no `detail.tool`. MCP rows
  (`detail.mcp`) are excluded. Such rows are reported with `tool: null`; a function name another
  tool shares would also match, which the docs say.
- `audit_events_at` serves the window; the LIMIT bounds the rows. One extra row tells `truncated`.
- A timeout (SQLSTATE `57014`) is `TOOL_LOG_TIMEOUT`. `->>` detoasts each row's whole `detail`,
  so an agent writing huge rows can make the log time out: a denial of the log only (an agent
  could as well not log), documented.
- `db_role` is selected only when `pg_attribute` shows the column on `audit_events` (checked in
  the same transaction); otherwise `NULL::text AS db_role` (runtime-managed stacks, or an owner
  that has not yet applied §3.4).

**Every value from the database is hostile** (the agent role can INSERT `audit_events` and
`turns`; S-35). Before output, in Python:

- Shapes are validated, else the field is `null` (text `-`): `tool` matches
  `tool/<name>@<semver>`; `agent` matches the `agent.yaml` name pattern
  (`[a-z0-9]([a-z0-9-]*[a-z0-9])?`, at most 63); `db_role` matches `init.sh`'s role names
  (`[a-z][a-z0-9_]{0,62}`); `function` (from
  `action`) matches `[A-Za-z_][A-Za-z0-9_]{0,63}`; `turn_id` and `trace_id` are hex up to 64; `ok`
  is a JSON boolean; `latency_ms` an integer 0–86 400 000.
- With `--content`, `args` and `error` go through `redaction_values()` (URL and bare forms) and
  `safe_text` (control and bidi characters stripped, capped) before text or JSON output.
- A test feeds forged rows (escape sequences, OSC 52, bidi, wrong types, megabyte `detail`) and
  checks the output is clean and bounded.

### 4.3 Output

Text: a header `tool/orders, last 24h, newest first (database support)` then columns **time,
agent, version, function, status, latency, turn id, trace id, db role**; `-` for unknowns;
console-inserted rows marked `(console)`; a last line when truncated (`… more; narrow --since or
raise --limit`). A `Phoenix: URL` line when the stack has a `service/phoenix` host URL, so the
trace id can be looked up there (a deep link needs Phoenix's project id; a follow-up).

`--json` follows the `logs`/`usage` family: `schema_version`, indented, and on a refusal the
same `{"schema_version": 1, "error": {"code", "message"}}` on stdout as well as `[CODE] message`
on stderr, exit 2.

```json
{
  "schema_version": 1,
  "ref": "tool/orders",
  "environment": "dev",
  "database": "support",
  "credential": "console",
  "window": {"since": "2026-11-12T17:00:00Z", "until": "2026-11-13T17:00:00Z"},
  "limit": 100,
  "truncated": false,
  "content": false,
  "db_role_verified": true,
  "phoenix_url": "http://127.0.0.1:6006",
  "notes": ["2 rows from before runtime/adk 1.2.0 have no agent or latency"],
  "calls": [
    {
      "at": "2026-11-13T16:02:11.123Z",
      "agent": "northwind-support",
      "tool": "tool/orders@1.0.0",
      "function": "lookup_orders",
      "status": "ok",
      "latency_ms": 41,
      "turn_id": "9f1c…",
      "trace_id": "4bf9…",
      "db_role": "support_agent"
    }
  ]
}
```

- `credential` is `console` or `agent` (§3.1). `db_role_verified` per §3.4 ("trigger in force
  now"; false in a runtime-managed stack or when the trigger is missing or altered).
- `status` is `ok` or `error` (`detail.ok`), or `null` when `ok` is not a boolean. `agent`,
  `tool`, `latency_ms`, `trace_id`, `db_role` may be `null`.
- With `--content`, each call also has `args` (object, or `null` when over 16 KiB) and `error`
  (string or null).
- **Results are not in the log.** The runtime does not store tool results, and 1.2.0 does not
  start to (that would add customer data at rest); the trace (Phoenix) holds them. This replaces
  the plan's "arguments and results only with `--content`".
- The log is what the runtime **claims** it called (§3.4).
- Exit 0 with `calls: []` when nothing matched.

**In the console:** `--content` is an explicit per-view action ("Show arguments", behind the
customer-data marker), never persisted, cached, logged, or put in a URL.

### 4.4 Refusals (exit 2)

| Code | When | Console shows |
|---|---|---|
| `CONSOLE_ROLE_MISSING` | an owner-managed database and the stack has no `console` output (service < 1.2.0 or `console_database` unset) | "Execution log needs `service/postgres-pgvector@1.2.0` with `console_database: DB`" |
| `CONSOLE_ROLE_NO_ACCESS` | SQLSTATE `42501` on `audit_events` or `turns` | "The database owner's `init_sql` has not granted the console role: add the grants (docs) and run `services up --update`" |
| `DB_LOGIN_FAILED` | SQLSTATE `28P01` / `28000` with either credential (the message names which: console or agent) | "Run `services up` to refresh the stack's credentials" |
| `SERVICES_STALE` | the deploy state lacks the host-form secrets (§3.5) | "Run `services up`" |
| `CONSOLE_OBJECT_UNEXPECTED` | the §3.1 catalog check fails | the message |
| `TOOL_LOG_NO_TABLES` | SQLSTATE `42P01` (no turn recorded yet, or `agent_events` off) | "No calls recorded yet" |
| `TOOL_LOG_TIMEOUT` | SQLSTATE `57014` | "Narrow --since" |
| `SERVICES_DOWN` | the database is unreachable, or no state written | "Start services" |
| `DEPENDENCY_MISSING` | no `psycopg` | the install hint |
| `REF_INVALID`, `SINCE_INVALID`, `LIMIT_INVALID`, `AGENT_NOT_FOUND`, `ENVIRONMENT_AMBIGUOUS`, `TOOL_LOG_UNSUPPORTED` | as above | the message |

Messages never include the DSN or psycopg's conninfo (§3.5). A bare `tool/NAME` that no root has
is not refused (its calls can outlive its versions); `legacy_actions` is then empty.

### 4.5 Runtime record additions (`runtime/adk@1.2.0`, all inside `detail`, no column changes)

- `detail.tool` on the error path too (registry tools; MCP calls keep `detail.mcp`).
- `detail.agent`: the agent name from `agent.config.json`.
- `detail.latency_ms`: monotonic time from `before_tool_callback` to the after/error callback.
- `turns.tools` is unchanged (names only); nothing reads it for the log.

## 5. (c) Changing a tool: fork → edit → set-ref → release

On Northwind (project-owned `tool/orders@1.0.0`) the flow starts at step 2. A built-in or
`AGENTBREEDER_REGISTRY_PATH` tool starts at step 1; `registry edit` refuses it with
`EDIT_NOT_PROJECT` and prints that command.

```
# 1. Only for a tool the project does not own
agentbreeder registry fork tool/web-search@1.0.0 --version 1.0.1 --project DIR --dry-run --json
agentbreeder registry fork tool/web-search@1.0.0 --version 1.0.1 --project DIR --expect-digest sha256:…
# 2. Edit: review first, then write exactly what was reviewed (S-04)
agentbreeder registry edit tool/orders@1.0.0 --set src/orders.py - --project DIR --dry-run --json < orders.py
agentbreeder registry edit tool/orders@1.0.0 --set src/orders.py - --project DIR --expect-digest sha256:… --json < orders.py
#    -> tool/orders@1.0.1   (after step 1, edit the fork, e.g. web-search@1.0.1 -> 1.0.2;
#       a fork alone is enough when only the name or version changes)
# 3. Move the agent: review, then write against the reviewed agent.yaml
agentbreeder agent set-ref tool/orders@1.0.0 tool/orders@1.0.1 --project DIR --dry-run --json
agentbreeder agent set-ref tool/orders@1.0.0 tool/orders@1.0.1 --project DIR --expect-base sha256:… --json
# 4. Build and release (the eval gate)
agentbreeder build DIR && agentbreeder release DIR …   # `agentbreeder next` prints the exact commands
```

The console runs the same children: code from the editor goes to stdin (one file) or a 0600 temp
file per file, never argv; the dry run's digest is passed back as `--expect-digest` /
`--expect-base` (S-04, as #904 documents). The three-tier confirm says "this code runs in agents"
and "the eval gate is a quality gate, not a security review" (§8.6).

**What M12 adds to `registry` (lane C):**

- **Allowed files, not a denylist (`TOOL_INVALID`, nothing written):** a tool version (edited,
  imported or forked) holds only `manifest.yaml`, `tool.yaml`, `README*` and `src/**/*.py` whose
  path parts match `[a-z0-9_]+`, each a regular file (checked with `lstat`: no symlinks or special
  files), except `src/sitecustomize.py`, `src/usercustomize.py` and `src/conftest.py`, which are
  refused by name. Everything else is refused too, which covers `*.pth`, `*.so`, wheels,
  `requirements*.txt`, `pyproject.toml` and `setup.py`. Compatibility: every built-in and example
  tool today fits (checked: only `manifest.yaml`, `tool.yaml` and `src/*.py`); a test runs the
  rule over all of them.
- **No shadowing:** a tool's `src/` goes first on `sys.path` (`runtime/tools.py:108`) and today
  only the declared module names are checked against `RESERVED` (`tools.py:114`). So every
  top-level name in `src/` (a file stem or package directory) must not be a stdlib module, a
  runtime module, or a top-level import name of a package in a built-in `runtime/adk`
  `requirements.lock` (`psycopg`, `yaml`, `pydantic`, `google`, `httpx`, `opentelemetry`, …;
  a list kept beside the locks, with a test that fails when a lock gains a package it lacks).
  `registry` checks this early; `runtime/adk@1.2.0` enforces it at load with `find_spec` on the
  real image's `sys.path`, which also covers packages outside the lock (`pip`, `setuptools`,
  `pkg_resources`, `_distutils_hack`, `wheel`, namespace packages) and two tools of one agent
  sharing a name (authoritative check, §3.6).
- **Code checks (S-06):** the staged `tool.yaml` still loads; every `functions[].module` resolves
  to a file under `src/`; each such file is at most 256 KiB, a regular file opened without
  following symlinks, decodes as strict UTF-8, and parses with `ast.parse` (never imported);
  `SyntaxError`, `ValueError`, `RecursionError` and `MemoryError` are a refusal, not a crash; the
  module has a top-level `def` or `async def` named `functions[].function`. `build` keeps its own
  checks; this only fails early.
- **No dependency edits (S-33):** tools declare no dependencies (§2.3), so there is no re-lock
  and nothing is resolved on the host; the allowed-files rule refuses dependency files ("tools use
  the runtime's locked packages"). If tool dependencies are added later, re-lock is wheels-only
  and hash-only on the host or runs in a container, and an sdist-only dependency is refused (S-33
  stands for that feature).
- None of this stops tool code from doing harm when it runs: the boundaries are review, the eval
  gate (a quality gate) and, later, #909's policy.
- **`registry show tool/…@X --json` gains `functions`:** `[{name, module, function, description,
  parameters, signature}]`, `parameters` from `tool.yaml` (what the model sees), `signature` the
  Python signature text from `ast` (for example `lookup_orders(order_id: str = "", status: str =
  "")`), or `null` with `signature_error` (`too_large`, `syntax_error`, `not_found`). The signature
  text is capped at 1 000 characters. The console server imports `registry` (file reads only,
  §8.1 #3), so this runs in-process with no import of tool code: the size cap bounds the work,
  `RecursionError` and `MemoryError` are caught around the parse, and the result is cached by
  version digest plus checker version (versions are immutable).
- `description` and `parameters` come from `tool.yaml` and are untrusted: the CLI prints them
  through `safe_text.excerpt`, and the console renders them as text.
- A test module whose top level raises proves nothing is imported (S-06).

**Tool detail in the console** (lane B): docs and functions from `registry show`; source files
(the existing files view); "Dependencies: none of its own; the runtime's locked packages
(`runtime/adk@X`)" with
a link to the runtime; "Grants: available to agents that reference it" with the used-by list
(MCP grants are M15's); the execution log tab from `tool log --json`.

## 6. (d) The #909 seam (identity principal and tool RBAC/ABAC)

#909 (spec by lane A2) gives every agent a principal at creation and a default-deny policy
granting only its declared tools. This delta does not design it; it keeps these seams open:

1. **Tool availability stays "referenced in `agent.yaml`, pinned in `agent.lock`"** in M12. #909
   adds a policy decision over the same fact. `agent set-ref` is the point where the declared set
   changes, and `release` the point where it takes effect, so a check "may principal P use
   `tool/orders@1.0.1`?" belongs in `build`/`release` (deploy-time gate) and at call time
   (in-process tools ask the host decision service, per #909). Nothing in M12 decides tool access
   in the console role.
2. **`detail.agent` is a name today.** #909 adds `detail.principal` (the principal id) beside it;
   `tool log --json` will gain a `principal` field (nullable, additive). The JSON shape above
   stays compatible.
3. **`db_role` attributes a database login, not an agent.** All agents of a stack share one
   `DATABASE_URL` role today. If #909 gives each principal its own database role, `db_role`
   becomes per-agent attribution with no schema change, and §3.1's rule (the console reads
   agent-owned objects with that agent's own credential) carries over per principal.
4. **The console role is not an agent principal** and is never granted tool use: it is an
   operator credential (`operator_only`). Who may read the execution log or edit tool code is
   people RBAC (#742/#775), after this epic.
5. **Denied calls:** when #909 denies a tool call at runtime, the runtime writes the same
   `tool:<function>` audit row with `ok: false` and a fixed `error` code, so `tool log` lists
   denials with no contract change.

## 7. Plan delta (applied to `2026-11-11-console-epic.md`)

- M12 Data: the role and grant rule (§3.1–§3.3; the runtime grants nothing, replacing "a new
  `runtime/adk` version that grants them access"), the S-32 trigger (§3.4), and the added DELETE
  on `turns`/`tickets`.
- M12 CLI: `tool log` reads `audit_events` joined to `turns` (§4); `--content` adds arguments
  and error text, not results; new `registry` tool checks and `show` functions (§5).
- M12 Console: Tool detail shows "no dependencies of its own; the runtime's locked packages", not
  "hash-locked dependencies" (§2.3).
- M12 lanes and dependencies: lane C owns `commands/tool.py`, the `registry` checks, the
  postgres-pgvector and runtime versions, stack templates and `commerce.sql` (lane D folded into
  C); lane A writes the host-form `agent-database`/`console-database` secrets at `services up`
  (§3.5), a prerequisite of M12's CLI, added to M12's `Depends`.
- M12 Console: the execution log is unavailable only on an owner-managed stack without the
  console role.
- M12 acceptance: "editing Northwind's `tool/orders` (project-owned; a built-in tool is forked
  first)" replaces "forking Northwind's `tool/orders`"; the fork step is covered by a CLI
  end-to-end test with a path-root tool.
- M16 Data: adds DELETE on `turns` and `tickets` to the list M12 ships; session tables are
  listed and deleted with the agent's own credential (§3.1), not through console-role grants.
- M14 Data: knowledge tables are read with the agent's own credential (§3.1); no grant. M14 still
  depends on M12 (the host-form secrets and the credential procedure).
- M16 Data: `forget` and session deletes span two credentials; the M16 spec fixes their order
  (intent audit row, deletes, completion row), idempotent retry and partial-failure reporting,
  and notes that a delete with the agent's credential can race the running agent's own writes.
  S-32 holds on owner-managed stacks only.

## 8. Tests and verification (M12 implementation PRs)

- `init.sh` (real Docker, `AGENTBREEDER_DOCKER_TEST=1`): the role exists only with
  `console_database`; it can connect only there (`has_database_privilege(…, 'postgres',
  'CONNECT')` and every other database false); it cannot create anything, use `TEMP`, read
  commerce tables or session/knowledge tables, or `SET ROLE`; connection limit 10; a re-run is
  idempotent; unsetting `console_database` makes the role `NOLOGIN` and revokes `CONNECT`;
  `agentbreeder_console` is refused as a database or role name; the password never appears in
  `services logs` output (redacted).
- Owner SQL: Northwind's new `commerce.sql` applies with `console_database` unset (guarded
  grants) and set.
- Runtime (its `runtime_tests`, real Postgres): no GRANT or DDL beyond 1.1.0's; error rows carry
  `detail.tool`, `agent`, `latency_ms`; a tool whose `src/` shadows an installed package (in or
  outside the lock) or another tool's module is refused at load.
- Owner SQL (Northwind, real Postgres): `db_role` set by the trigger to the login role for
  runtime and console inserts, a forged value overridden for `INSERT`, `COPY` and `ON CONFLICT`;
  old rows stay NULL; the "in force now" check false after disabling the trigger, adding a later
  `BEFORE INSERT` trigger or a column default; grants stay when `console_database` is later
  unset.
- Privilege-lending: a trigger and an RLS policy planted by the agent role on its own session
  table do not run as `agentbreeder_console` in any M12/M14/M16 path (they run as the agent);
  granting the agent TRIGGER on an owner table and planting a trigger, or enabling RLS, gives
  `CONSOLE_OBJECT_UNEXPECTED`.
- Credential choice: starter → agent (the owner), Northwind → console, tables in the agent's own
  schema → agent with that schema, no tables yet → `TOOL_LOG_NO_TABLES`, mixed ownership →
  `CONSOLE_OBJECT_UNEXPECTED`; `SECRET_RESERVED` for manifest references to the three reserved
  secrets; a permission or login error never retries with the other
  credential; the new host-form secrets are redacted.
- `tool log`: unit tests for argument parsing, shape validation and output; CLI end-to-end
  against a fixture database (every refusal code, `--content` off by default and args not
  selected, redaction applied, forged rows, a megabyte `detail` and a 5 MB `ok` field, an
  `audit_events` without `db_role`, truncation, legacy rows, MCP
  rows excluded, `--environment` choice); the console server never reads the console secret or
  the agent's `DATABASE_URL`; `services env`/`status` never print `operator_only` outputs; error
  text never contains the DSN.
- `registry`: `TOOL_INVALID` cases (disallowed files, shadowing names, oversize, invalid UTF-8,
  syntax error, deep nesting, symlinked module), the never-imported module test, `show`
  functions.
- Verification (`docs/superpowers/verification/M12.md`): Northwind on the new versions passes
  61/61; the §5 flow on `tool/orders`; `tool log` lists the new calls without arguments by
  default; an upgrade of a 1.1.0 Northwind stack with `services up --update`.

## 9. Open questions for the owner

None blocking. Two defaults chosen here, to confirm or change at review:

1. Tool results stay out of the database (§4.3); the trace holds them.
2. The trace link is a trace id plus the Phoenix URL until a deep link is built.

## 10. Review log

| Round | Reviewer | Lens | Result |
|---|---|---|---|
| 1 | Claude Sonnet (read-only) | architecture | CHANGES REQUESTED: 1 blocking (a list param cannot reach `init.sh` with one non-`per` secret), 6 major (unguarded `GRANT` in owner SQL, one-way role lifecycle, no hook for ADK's session tables, grant failures not isolated, MCP rows matched by the legacy branch, no `--environment`), 10 minor/nit. All applied: `console_database` scalar; guarded `DO` block; revoke/`NOLOGIN` on removal; runtime grants removed entirely (§3.1), which retires the session-hook and isolation findings; MCP excluded and `LIKE` for bare names; `--environment` and host addresses; `logs`-family JSON with `schema_version`; refusal codes for missing tables, failed login and `psycopg`; `--update` semantics; runtime jump note; fork wording; `--customer` deferred; plan delta for dependencies and lanes |
| 1 | Claude Sonnet (read-only) | security | CHANGES REQUESTED: 2 high (console grants on agent-owned objects lend its privileges through agent-planted triggers/RLS; DB fields printed raw without `--content`), 3 medium-high/medium (whole `detail` fetched; S-32 attribution gaps and `current_user`; tool `src/` can shadow locked packages, denylist too narrow), 2 medium/low (S-22 tests; role hardening). All applied: §3.1 ownership rule (agent-owned objects read with the agent's own credential, no runtime grants), field validation and `safe_text` on every value, SQL projection with caps and timeouts and `CONNECTION LIMIT`, `session_user` with `search_path`-pinned trigger function and a reader check that it is in force, allowlisted tool files and shadowing checks, parse hardening, credential tests, schema-qualified reads with `search_path=pg_catalog` |
| 2 | Claude Sonnet (read-only) | architecture | CHANGES REQUESTED: 1 blocking (host-form agent and console URLs are not in stack state; only `eval-database` is, `services/operator.py:169-171`), 3 major (credential choice circular; `db_role` absent on older tables; agent credential weakens S-32 and splits `forget`), minor plan staleness. Applied: lane-A host-form secrets as an M12 prerequisite (§3.5, plan `Depends`/`Lanes`); a catalog-first credential procedure with no fallback (§3.1); `pg_attribute` check; trigger only on owner-managed stacks and no runtime migration (§3.4, §3.6); M16 two-credential constraints; `CONNECTION LIMIT 10`; plan wording |
| 2 | Claude Sonnet (read-only) | security | CHANGES REQUESTED: 1 medium-high (ownership alone does not stop TRIGGER grants or RLS on owner tables), medium (SQL still returned unbounded values; agent-credential rules), low (in-force check, role removal, allowlist text, shadowing outside the lock). Applied: full console-role catalog check (TRIGGER ACL, RLS, partitions, trigger functions, `public` owner), `pg_temp` last; typed and capped projections; credential procedure and no-fallback; stricter in-force check worded "trigger in force now"; `pg_terminate_backend` and `check_name`; explicit refusals and `lstat`; load-time `find_spec` shadowing; real name patterns |
| 3 | Claude Sonnet (read-only) | architecture | **APPROVED.** Minor notes applied: reserved deploy-state secret names (`SECRET_RESERVED`), the no-tables case before choosing a credential, plan lane labels for M14/M16, the fail-closed message for project triggers |
| 3 | Claude Sonnet (read-only) | security | **APPROVED.** Low notes applied: the schema found in step 1 used in all checks and queries, secret lifecycle and evidence collection, a `sys.path` snapshot for `find_spec`, NULL `relacl`, connection-limit text aligned |
