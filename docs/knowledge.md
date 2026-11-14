# Knowledge (RAG) (on `main`, not in a published preview)

An agent's `knowledge:` entry (`agent.yaml`, at most one in `runtime/adk` 1.x) names a
`knowledge/NAME@X.Y.Z` registry version: a `manifest.yaml` (`embedding: {alias, dimensions}`)
and a `docs/` folder of `.md` and `.txt` files. This page covers changing the documents and
checking the index the runtime built from them (#830, M14).

## How the runtime indexes a version

- Each version gets its own pgvector table, `kb_<name>_<version>` with dashes and dots as
  underscores (`knowledge/northwind-help@1.0.0` → `kb_northwind_help_1_0_0`), in the database
  the stack's `DATABASE_URL` binding points at. The agent's role creates and owns it, so it lands
  in that role's own schema when one exists (Northwind: `support_agent`), else in `public` (the
  starter).
- Documents are split at blank lines into paragraph chunks; a paragraph starting with `#` is a
  heading and is not indexed, and a file's first line is the title of its chunks. A chunk's id
  is `<file stem>#<paragraph index>`.
- The index is built **at the agent's first knowledge search in each runtime process**: only new
  or changed chunks are embedded (by a content hash over the embedding alias, dimensions, title
  and text) and chunks whose paragraph disappeared are deleted. `release`'s eval turns search, so
  a released version has been indexed. Until then, or if embedding fails, search falls back to
  keyword (BM25) search over the same documents.
- A new version is a new table, embedded in full. The tables of older versions stay until you
  drop them.

A stack has **one database per environment** (the `DATABASE_URL` binding), shared by every agent
in it: two agents using the same knowledge version share its table.

## Adding or changing documents

Documents are files in a registry version, so they change through `registry edit` (see
[registry.md](registry.md#editing-a-project-version-on-main-not-in-a-published-preview)), which
publishes a new version, then move the agent to it and release:

```
agentbreeder registry edit knowledge/northwind-help@1.0.0 --set docs/pens.md ./pens.md   # -> 1.0.1
agentbreeder registry edit knowledge/northwind-help@1.0.1 --remove docs/old.md           # -> 1.0.2
agentbreeder agent set-ref knowledge/northwind-help@1.0.0 knowledge/northwind-help@1.0.2
agentbreeder build && agentbreeder release
agentbreeder knowledge status knowledge/northwind-help@1.0.2
```

Only `docs/*.md` and `docs/*.txt` are read (UTF-8; a link or a folder with those names is
refused by `knowledge status`). A built-in version is forked into the project first
(`registry fork`). Documents land in a git-tracked registry: do not add customer data.

## `knowledge status`

**`agentbreeder knowledge status REF [--project DIR] [--environment ENV] [--json]`** compares
each version's documents with its table, read-only.

- `REF` is `knowledge/NAME` (every version of NAME in the project's search roots, oldest first)
  or `knowledge/NAME@X.Y.Z`. `--project DIR` (default `.`) is the directory with `stack.yaml`
  and `registry/`; `--environment ENV` is whose stack state to use (default: the only one
  `services up` wrote).
- It connects with the **agent's own credential** (the `agent-database` secret `services up`
  writes), never the console role and with no grant: the tables belong to the agent's role, so
  reading them lends nothing (M12 delta §3.1). Where the agent's role is the database owner (the
  starter), that is the owner's credential. The session is read-only with short timeouts.
- It finds the table the way the runtime's unqualified name resolves on the default search path:
  the agent role's own schema, then `public`. That table must be an ordinary table the agent's
  role owns (or a role it belongs to). If neither schema has one (a role whose `search_path` was
  changed), the one table of that name the role owns in another schema is used, with a note; two
  or more are refused as ambiguous.
- It reads the catalog (the table's schema, owner and embedding dimensions) and each row's id,
  hash and `updated` time (only when that column is `timestamptz`), at most 50,000 rows. **Chunk text is never read.** Values from the
  database are only compared and counted.
- Per version: the documents and chunks the registry holds against those indexed; how many
  indexed chunks are `fresh` (same hash), `stale` (changed), `missing` (not indexed yet) or
  `extra` (no longer in the documents, or not a well-formed row); the oldest and newest `updated`;
  the embedding alias and dimensions from the manifest and the table's own dimensions.
- `state` is `not_indexed` (no table yet: it is built at the agent's first search), `fresh`
  (every chunk matches and the dimensions agree) or `stale` (anything else; the runtime
  re-indexes at its next process start and first search, which `release` does).

Text output:

```
knowledge/northwind-help (database support, environment dev)
  1.0.0      fresh        support_agent.kb_northwind_help_1_0_0
    chunks 21 indexed of 21 (fresh 21, stale 0, missing 0, extra 0); documents 5 of 5
    updated 2026-11-13T09:12:03Z .. 2026-11-13T09:12:04Z
    embedding support-embedding, 768 dimensions
```

`--json`:

```json
{
  "schema_version": 1,
  "ref": "knowledge/northwind-help",
  "environment": "dev",
  "database": "support",
  "credential": "agent",
  "versions": [
    {
      "ref": "knowledge/northwind-help@1.0.0",
      "table": "kb_northwind_help_1_0_0",
      "schema": "support_agent",
      "state": "fresh",
      "documents": {"expected": 5, "indexed": 5},
      "chunks": {"expected": 21, "indexed": 21, "fresh": 21, "stale": 0, "missing": 0, "extra": 0},
      "updated": {"oldest": "2026-11-13T09:12:03Z", "newest": "2026-11-13T09:12:04Z"},
      "embedding": {"alias": "support-embedding", "dimensions": 768, "table_dimensions": 768},
      "notes": []
    }
  ]
}
```

`schema` and `table_dimensions` are `null` for a version not indexed yet; the `updated` times are
`null` when no row has a valid timestamp.

### Refusals (exit 2)

`[CODE] message` on stderr; with `--json` also `{"schema_version": 1, "error": {"code",
"message"}}` on stdout.

| Code | When |
|---|---|
| `REF_INVALID` | `REF` is not `knowledge/NAME` or `knowledge/NAME@X.Y.Z` |
| `REF_NOT_FOUND`, `REF_CONFLICT`, `MANIFEST_INVALID`, `ENTRY_UNSAFE` | as for every registry read |
| `KNOWLEDGE_INVALID` | `docs/` or a document is a link, a document is a folder or other non-file, not UTF-8 or over 10 MiB, or the manifest's `embedding` is malformed (`dimensions` must be 1 to 16,000) |
| `KNOWLEDGE_OBJECT_UNEXPECTED` | the table on the default search path is not an ordinary table owned by the agent's role (or a role it belongs to), or a schema name has a quote, dot, space or control character |
| `KNOWLEDGE_OBJECT_AMBIGUOUS` | no table on the default path, and the agent's role owns one in several other schemas |
| `KNOWLEDGE_TOO_LARGE` | the table has more than 50,000 rows, or the documents make more than 50,000 chunks, more than 2,000 files or more than 50 MiB of text |
| `SERVICES_DOWN` | `services up` wrote no state for the environment, or the database is not reachable |
| `ENVIRONMENT_AMBIGUOUS` | several environments are written and no `--environment` |
| `SERVICES_STALE` | the stack state predates the `agent-database` secret: run `services up` |
| `KNOWLEDGE_STATUS_UNSUPPORTED` | `DATABASE_URL` is not bound to a `service/postgres-pgvector` `databases.NAME` or `roles.NAME` output |
| `DB_LOGIN_FAILED` | the agent's credential was refused: run `services up` to refresh it |
| `KNOWLEDGE_STATUS_NO_ACCESS`, `KNOWLEDGE_STATUS_TIMEOUT`, `KNOWLEDGE_STATUS_FAILED` | the database refused the read, a query ran over 5 s, or another database error |
| `DEPENDENCY_MISSING` | no `psycopg`: `pip install "agentbreeder[eval]"` |

These stack and database refusals are shared with `tool log` (the `dbread` module). Messages are
fixed per error class and never include the connection string. A stack file that does not load
gives `services`' own codes.
