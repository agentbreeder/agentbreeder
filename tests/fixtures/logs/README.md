# Log fixtures

Real output, read through `services logs --raw` (so redacted; the only `[redacted]` is Phoenix's
own; one edit: Phoenix's public Slack invite code is replaced with `INVITE` so it does not look
like a credential), from a Northwind run on macOS with Docker Desktop on 2026-11-12: `services up`, `release`
(61/61), three `invoke` calls, then a failing query and an unknown role through `psql` in the
Postgres container.

- `postgres.txt`: `service/postgres-pgvector@1.1.0`
- `litellm.txt`: `service/litellm@1.0.0`
- `phoenix.txt`: `service/phoenix@1.0.0`

`runtime/adk` printed nothing in that run, nor in a starter-agent run with its database stopped
before an `invoke` (`logs ID` returned no lines), so its fixture, `adk.txt`, is empty. Its patterns
come from the runtime's own messages (`runtime warm-up failed: …`, `turn record failed: …`,
`closing MCP sessions failed: …`) and Python's traceback and `warnings` formats.

Known limit, kept in the golden files: LiteLLM prints `query-engine <hash>` right after its
`WARNING` line with no blank line between, so it is grouped with that warning.
Level words in ANSI colour (a server writing to a terminal) are not read as headers; Docker
does not give containers a terminal unless asked to. A Postgres header counts only at the start
of a line or after the init script's progress dots, so a statement's own text cannot start a
record.

`*.levels` hold the expected level of each line (`-` for unclassified). Regenerate them with
`AGENTBREEDER_UPDATE_GOLDEN=1 pytest tests/test_loglevels.py`, then review the diff.
