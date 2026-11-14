# M15 CLI: `agentbreeder mcp grant|revoke` (#831)

Status: built on `feat/831-mcp-grant` (2026-11-13). Plan of record: the console epic plan, M15.
This note covers the CLI half and the fixture. The console half (allowing `services-up` for MCP
stacks, plus the grants/pins/relay status screens) belongs to lane B and gets its own S-34 delta.

## Problem

An agent's MCP tool grant lives in `stack.yaml` (`environment.agents.<agent>.mcp: {server:
[tools]}`). Today the only way to change it is to edit YAML by hand, so the console has nothing to
call. M15 needs a command that the console can preview, then apply exactly.

## Scope

- `mcp grant|revoke AGENT SERVER TOOL… [--project DIR] [--dry-run | --expect-base SHA256] [--json]`.
  The coordinator's brief said `environments.<env>`, but a stack has one `environment:` block, so
  the command takes no `--environment`.
- A fixture: `tests/fixtures/mcp_github_project`, the `new agent` starter plus `mcp/github@1.0.0`.
  The agent asks for `get_me` and `list_issues` (read-only), and the stack grants nothing at first.
- Out of scope: adding agents or `mcp:` entries; changing `agent.yaml`; talking to relays or
  deployments; the console.

## Design

- **Validation.** It loads `stack.yaml` with `read_project_file`, which refuses a symlink, a FIFO
  or an oversize file, and validates it with the `Stack` model. AGENT and SERVER must exist. Tool
  names must match `MCP_TOOL`. `grant` resolves the server's version in the project's registry
  roots and refuses tools that `mcp.yaml` doesn't declare; `services up`'s own check stays in place.
- **The edit.** `yaml.compose` gives the node marks. The old `mcp:` value is replaced from the end
  of its key to the end of its last scalar; otherwise one `mcp:` line is added at the agent's key
  indent, or `, mcp: {…}` goes into a flow mapping. A scalar YAML would read as anything but that
  string (`true`, `1.0`, `null`) is JSON-quoted. The result must parse to the old document with
  only that grant changed and validate as a `Stack`, or nothing is written
  (`STACK_EDIT_AMBIGUOUS`). The write is set-ref's `_replace`: a temp file in the same directory,
  fsync, a re-check of the base bytes, then `os.replace`.
- **Idempotent.** A no-op returns `changed: false` and writes nothing.
- **`in_use`** (revoke only). It reads the stack's grant files under
  `<stacks>/<id>/grants/<env>/<server>/` and lists the deployments of AGENT that hold a revoked
  tool. The coordinator chose to write and warn rather than refuse: `services up` stays the
  enforcement point (`MCP_GRANT_IN_USE`). A malformed grant file in that server's directory (which `services up`
  also refuses on) is listed with `deployment: null`.
- **Limits.** The new file must stay within the 64 KiB `stack.yaml` limit (`STACK_TOO_LARGE`), so a
  grant can't leave a file that later commands refuse to read. LF or CRLF line endings are kept.

## S-34 security delta (grant edits)

| # | Concern | Finding | Disposition |
|---|---|---|---|
| 1 | Widening a grant without consent | Writing `stack.yaml` changes nothing that runs. A widened grant reaches a relay only through `services up`, whose phase-1 consent shows each agent's tools and their effect and needs `--yes` or a person when the grant changed. `deploy` uses only tools that `agent.yaml` requests *and* the stack grants. | No new path past consent. |
| 2 | Granting tools the server never declared | Refused (`MCP_TOOL_UNDECLARED`), the same set `services up` and the relay allow. | Mitigated. |
| 3 | Narrowing under a live deployment | The file is written, `in_use` names the deployments, and `services up` refuses until they are removed. The relay keeps intersecting with the last `up`'s config, so a running agent is never silently widened or narrowed by the edit itself. | Accepted (coordinator, 2026-11-13). |
| 4 | Lost update / TOCTOU on `stack.yaml` | `--expect-base` binds a write to the reviewed bytes, and `_replace` re-checks the base just before the rename. An editor that doesn't take the console's project lock can still race in the last window (as for set-ref, S-04). | Accepted, same as set-ref. |
| 5 | Symlink or special-file tricks | `read_project_file` refuses a symlinked, FIFO or oversize `stack.yaml`. The temp file is created by `mkstemp` in the project directory. Grant-file reads skip symlinked directories and non-matching names, and only read 0444 JSON. | Mitigated. |
| 6 | YAML injection through names | Names are pattern-checked before use. The written value is generated, and the whole file is re-parsed and compared to the expected document, so no other key can change. | Mitigated. |
| 7 | Terminal escapes in output | Messages go through `registry.clean`. Deployment and environment names from grant files are shown through `_shown`. | Mitigated. |
| 8 | Audit | Not audited, as `agent set-ref` (S-30): the record is the file and git. Grant changes that take effect are visible in `services up`'s consent and in the relay's logs. | Accepted. |
| 9 | Secrets | The command never reads tokens or credentials. Grant files hold none. | n/a |

## Acceptance

- Offline, with Docker faked:
  - a grant, then `services up`, puts the tool in the relay's config;
  - a revoke held by a deployment is reported, and `services up` refuses with `MCP_GRANT_IN_USE`;
  - after the grant file is removed (what `remove` does), `services up` narrows the relay.
- Live, behind `AGENTBREEDER_DOCKER_TEST=1` and `GITHUB_MCP_TOKEN` (`test_fixture_live_grant_and_revoke`;
  deferred to the batched M15 verification, since Docker was down when this was built):
  - deploy is refused before the grant;
  - after the grant, `services up` and deploy, the relay's `tools/list` with the deployment's token
    lists both tools;
  - after the revoke, `services up` refuses; after remove, `services up` and a redeploy that asks
    only for `get_me`, it lists only `get_me`.

## Review log

Different-model review (Sonnet, read-only subagents; coder Opus).

| Round | Lens | Verdict | Findings and disposition |
|---|---|---|---|
| 1 | Security and correctness | Changes requested | (M) A grant could push `stack.yaml` past 64 KiB, after which every command refuses it: fixed (`STACK_TOO_LARGE`). (L) A block-scalar last value gave a false `STACK_EDIT_AMBIGUOUS`: fixed (`_text_end`). (L) CRLF files got an LF line: fixed. (L) Malformed grant files were missing from `in_use` while `services up` refuses on them: fixed (`deployment: null`). (L) Escapes from the user's own `--project` path in the `Next:` line: declined, since it is self-supplied and stripping would change the path shown. (Nit) A clearer message for a bare `mcp:`: declined, since it is the schema's error. |
| 1 | Tests, docs, integration | Approved | (L) The folded-scalar test accepted either outcome: pinned. (L) Fixture validate now runs with an isolated env. (L) A fixture comment that live runs need a future `review_by`: added. The live test was checked against build/deploy/up output, the ADK env vars and the relay protocol. |
| 2 | Re-review of the fixes | Approved | (L) A file with mixed line endings could get a stray CRLF: fixed (each line's own break, `_line_end`). (L) `in_use` flagged files the engine accepts: now only non-list `tools`, matching `_check_in_use`. (L) The `reconcile` hint could be wrong for a current deployment: now "reconcile, or remove its deployment". |
