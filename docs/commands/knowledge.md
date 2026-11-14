# `agentbreeder knowledge`

Show the index status of knowledge versions (read-only).

On `main`, not in a published preview (#830): `knowledge status REF [--project DIR] [--environment ENV] [--json]` compares the documents of each version of `REF` (`knowledge/NAME` for every version in the project's search roots, or `knowledge/NAME@X.Y.Z`) with the table the runtime indexed them into, in the stack's database for that environment (one per environment: the `DATABASE_URL` binding). Per version it shows the documents and chunks expected and indexed, how many indexed chunks are fresh, stale, missing or extra, the oldest and newest index time, the embedding alias and dimensions, and a state (`not_indexed`, `fresh` or `stale`). It reads with the agent's own credential, in a read-only session, and never reads chunk text. Documents are added or changed with `registry edit` and indexed after the next `release`. Details, the `--json` shape and the error codes: [knowledge.md](../knowledge.md).

Exit 0 when the status was read (also for a version not indexed yet); 2 with `[CODE] message` on stderr (and with `--json` also `{"schema_version": 1, "error": {"code", "message"}}` on stdout) otherwise.

Flags: `agentbreeder knowledge status --help`.
