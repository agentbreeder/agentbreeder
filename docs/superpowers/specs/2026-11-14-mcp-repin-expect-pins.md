# MCP repin without a TTY: `services pins` and `services up --repin --expect-pins` (#964)

Status: spec delta, r4 (2026-11-14). Docs only; lane A implements after the owner signs off.
Parent: #812 / #831 (M15). Plan of record: `docs/superpowers/plans/2026-11-11-console-epic.md`
(M15). This delta amends the services spec §7.7.1 (`2026-11-08-agent-services-design.md`) and the
console's MCP section (`docs/command-center.md`, "Pins").

## 1. Problem

`services up --repin` always needs a person at a terminal. The engine's `_pin`
(`services/engine.py`) sets `auto = False` whenever `repin` is set, and `confirm_pins` in
`commands/services.py` returns `False` without a TTY, so `--yes` never accepts a changed
definition or a dropped pin (`MCP_PINS_NOT_CONFIRMED`).

The changed definitions exist only after the relay's probe, which runs inside the running relay
(§7.7.1 phase 2). So nothing can show the diff before the run, and the console cannot offer a
repin. Today it shows the drift and names `agentbreeder services up DIR --environment ENV --repin`
to run in a terminal.

## 2. Goal

- A read, `agentbreeder services pins [DIR] [--json]`, that probes every relay of the stack and
  returns the pin diff in full, plus one digest over exactly what a repin would change.
- `services up --repin --expect-pins SHA256`, which repins without a TTY only when the definitions
  the relays serve at that moment, and the pins `stack.lock` holds, still produce that digest. On
  any difference it pins nothing.
- The console offers repin through its review: the definitions shown, a tick, and a start bound to
  the digest of what was shown.

Non-goals: changing the relay's runtime check (§9, R3/R4); a server's first pins (they keep the
existing flow); a TTL on digests; an audit-chain row (pins are recorded in `stack.lock`, as today).

## 3. The review document and its digest

One helper, `mcp_stack.pins_review(plan_entries, lock, probes) -> (document, details)`, builds
the review. Both the read and `up` call it, with probes taken by `_probe(entry,
set(entry.spec.tools))` (the declared set, as `_pin` does today, so `missing` is filtered the same
way). `mcp_stack.pins_review_digest(document)` is the only producer of the digest: the `sha256` hex
(64 characters) of the document's canonical JSON.

```json
{
  "v": 1,
  "stack": "<stack id>",
  "servers": {
    "<server>": {
      "identity": {"ref": "...", "origin": "...", "upstream": "...", "token_url": null},
      "pinned": {"<tool>": "sha256:<hex>"},
      "accept": {"<tool>": "sha256:<hex>"},
      "drop": ["<tool>"]
    }
  }
}
```

- **Canonical JSON** is the encoding `mcp_stack.tool_hash` uses: `sort_keys=True`,
  `separators=(",", ":")`, `ensure_ascii=False`, `allow_nan=False`, UTF-8.
- **`servers`** holds **every** `mcp:` entry, including those with nothing to change (`accept:
  {}`, `drop: []`). A server that drifts after the review breaks the digest too. A stack with no
  `mcp:` entries has `servers: {}`.
- **`identity`** is `McpEntry.identity()`: the version ref, origin, upstream origin and token host.
- **`pinned`** is the server's full current pin set (`lock_tools`). Binding the base means the
  digest applies only to the `stack.lock` that was reviewed: a pin written by another `up` in
  between changes it.
- **`accept`** is every tool a repin pins: `new` (served, never pinned) plus `changed` (pinned,
  live hash differs). Each value is the `tool_hash` of the **whole** live definition. The hash
  covers every field of the definition, so binding it binds the exact definition. The relay's
  parser refuses duplicate keys, NaN, Infinity and lone surrogates before hashing, and
  `valid_probe` refuses a probe whose `sha256` isn't `tool_hash(definition)`.
- **`drop`** is `missing ∩ pinned`: the pins a repin removes. A declared tool that is missing
  upstream and was never pinned is reported, not bound (nothing changes for it).
- **First pins.** `pins_review` also returns `need_up`: the servers whose `pinned` is empty and
  whose `accept` is not. The read and `up` both refuse on it with one predicate (`MCP_PINS_NEED_UP`).
  A server that was never pinned and has nothing to accept (nothing served yet) is not `need_up`.
  It stays in the document with `pinned: {}`, `accept: {}`. First pins pending on one server block
  the repin of every other, so the operator runs a plain `services up` first.
- **Not bound:** the environment (pins live in `stack.lock`, per stack; the grant digest binds the
  environment), unchanged tools' definitions beyond their pinned hash, and timestamps. `v` lets a
  later format change refuse old digests.

## 4. The read: `services pins`

```
agentbreeder services pins [DIR] [--json]
```

- **Dispatch.** A new `services` subcommand with its own parser (`dir`, `--json`) and its own
  branch in `commands/services.py` `run()`. It has no `--environment`. It is added to `WRITES`, so
  it runs under the services write lock (S-11). `plan()` copies content-addressed files into the
  state dir, and the lock keeps that away from a concurrent `down --purge`. It also stops the probe
  from racing an `up` that is recreating a relay. A busy stack gives `SERVICES_BUSY`, as for
  `services plan`.
- **What it never changes:** `stack.lock`, any pins file, secrets, the admin token
  (`_probe(entry, ..., create=False)`), the ports table and the containers. It may copy
  content-addressed files into the state dir, as `services plan` does. Nothing is saved: a pending
  lock change from `plan("plain")` stays in memory.
- **Steps.**
  1. `docker.authorize()`, then `containers(state)`. A relay whose container state isn't
     `running` refuses the whole read: `MCP_RELAY_NOT_RUNNING` ("run `agentbreeder services up`
     first").
  2. `plan("plain")`. `lock_mcp` resets the in-memory pins to `{}` for a pending identity change,
     so that case and "no pins yet" look the same: an empty `pinned`.
  3. Probe every relay (`docker compose exec -T`, admin token on stdin). Any probe failure
     (`MCP_PROBE_FAILED`, `MCP_ADMIN_TOKEN_MISSING`) fails the read with no digest. A partial
     review has no digest, and `up --repin` would fail on the same probe. A relay running an older
     config of the same identity (for example a changed declared-tool set) can fail the probe's
     `declared` check. The message says `services up` fixes it. A probe failure on a server whose
     `pinned` is empty (no pins yet, or a pending identity change) is reported as
     `MCP_PINS_NEED_UP`.
  4. `pins_review(...)`. A non-empty `need_up` refuses the read (`MCP_PINS_NEED_UP`: run `services
     up` first). First pins keep the existing flow (built-in or `--allow-project-mcp`, trust on
     first use), and the read never offers definitions from a relay `up` would replace with a new
     identity. Otherwise it computes the digest.
- **The previous definition** (the left side of a diff) comes from the relay's pins file. It is
  display only. It is shown only when it hashes to the pinned hash, as `_pin` does today, so
  `pinned` binds it. Otherwise the review says "previous definition unavailable" and shows the new
  definition in full.
- **Human output** is a read-mode `pins_text`. Its header states the facts and asks no question.
  For each tool it shows the whole definition as ASCII-escaped sorted JSON (`plan._definition`),
  and a unified diff for a changed one. For display only, a string value longer than 100 columns
  (key or value) is hard-wrapped with a visible `↩` continuation marker (`plan.review_lines`),
  never inside a `\uXXXX` escape, so a one-character
  change inside a long description shows as a small hunk, not a whole-line replace. The digest is
  unaffected. It lists only `drop` as "this pin is removed", and other missing tools as "missing
  upstream, not pinned". When either side of a diff has more than 2,000 display lines, the
  definition is shown in full with "diff too large" instead, because `difflib` is quadratic in
  lines. The output ends with
  `Review digest: <hex>` and `agentbreeder services up DIR --environment ENV --repin
  --expect-pins <hex>` (`ENV` is a placeholder).
- **`--json`** prints:

  ```json
  {
    "schema_version": 1,
    "stack": "<id>",
    "digest": "<hex>",
    "review": { "...": "the §3 document, exactly as hashed" },
    "servers": {
      "<server>": {
        "origin": "built-in",
        "state": "ok | drift",
        "new":     {"<tool>": {"sha256": "...", "definition": {}}},
        "changed": {"<tool>": {"pinned": "...", "sha256": "...", "definition": {},
                               "previous": {}}},
        "missing": ["<tool>"],
        "drop":    ["<tool>"],
        "same":    ["<tool>"]
      }
    }
  }
  ```

  `previous` is `null` when unavailable. `state` follows `_mcp_status`: `drift` when a pinned tool
  changed or is missing upstream, else `ok`; tools only in `new` are listed but don't make it
  drift. `state`, `same` and `missing` are display only. A stack with no `mcp:` entries exits 0 with `servers: {}`.
  Errors use `cli.fail`: `[CODE] message` on stderr, the `{"schema_version", "error": {"code",
  "message"}}` envelope under `--json`, and exit 2.
- **Lock hold.** The read holds the write lock for up to 180 s per relay (the probe timeout). A
  stalled upstream can make the console's plan, starts and service controls, and a CLI `up`, get
  `SERVICES_BUSY` for that long. The lock is non-blocking (`LOCK_NB`), so nothing deadlocks.
  Accepted: bounded and fail closed.
- **Size.** The relay's probe client reads at most 8 MiB, and a larger probe fails as
  `MCP_PROBE_FAILED`. The CLI passes definitions through unchanged. The console has its own cap
  (§7).
- **Docs:** a section in `docs/commands/services.md`, a row in `docs/services.md` §MCP, the help
  golden and the command-docs line-3 test.

## 5. The apply: `services up --repin --expect-pins SHA256`

- **Flags**, checked at the top of `run()` and refused with `cli.fail` (exit 2, `[CODE]`, JSON
  envelope), like `CONFIRM_REQUIRED`:
  - `--expect-pins` must match `^[0-9a-f]{64}$` (`PINS_EXPECT_INVALID`);
  - it requires `--repin`, and is refused with `--locked`, which never pins
    (`PINS_EXPECT_NEEDS_REPIN`);
  - with `--update` it is allowed: an identity change from the update empties `pinned`, so step 2
    below refuses it when that server serves tools.
  - `--yes` still covers only phase 1 (the grant) and first pins of built-in or allowed servers.
- **Phase 1 is unchanged.** The grant digest (`--expect-grant`), `--allow-project-mcp` and
  `MCP_PROJECT_CONSENT` all run first. The pins digest never stands in for the grant.
- **Phase 2 with `--expect-pins` runs in three steps**, replacing today's per-server
  probe/confirm/save loop:
  1. **Probe all.** Probe every relay and build the review with `pins_review`. Any probe failure
     raises (as `--repin` already does; no `unverified` fallback).
  2. **Check.** If `need_up` (§3) is non-empty, raise `MCP_PINS_NEED_UP`. A repin never makes
     first pins, even with a digest computed by hand. Then,
     if `pins_review_digest(document) != expect_pins`, raise `PINS_CHANGED` ("the MCP tool
     definitions or pins differ from the ones reviewed; review them again").
  3. **Apply.** Apply each server's `accept` and `drop` to the lock, with the hashes and definitions
     from **these** probes (the ones just hashed). Save `stack.lock` once. Then write **every**
     server's pins file with `pins_document(server, pinned, probe, previous)`, as today, including
     servers with nothing to change. The lock is still saved before any pins file is served.
- **Nothing to change** (`accept` and `drop` empty everywhere, digest equal): success, no lock
  write, reported as `repinned: {}`.
- **No prompt.** With `--expect-pins`, `confirm_pins` is never called.
- **What a refusal leaves.** On `PINS_CHANGED` or `MCP_PINS_NEED_UP`, `stack.lock` and every pins
  file stay unchanged, and the relays keep hiding changed tools. **The rest of `up` has already
  run:** phase 2 needs running relays, so by then the grant is recorded, a pending lock change is
  saved, and services and relays are (re)started. Only the operator config is not yet rewritten.
  The CLI message says so, and the console re-reads both status and the review (§7.5).
- **Lock scope.** `up` holds the services write lock from before `Engine()` to the end. No other
  `services` write (including `services pins`) changes `stack.lock` between the check and the save.
  A hand edit of `stack.lock` in that window is outside the model, as for `--expect-grant`.
- **Output.** `reports` keeps its current shape per server (`state`, `pinned`, `changed`,
  `missing`). The `up --json` result's `mcp` block gains `"repinned": {"<server>": {"accept":
  [...], "drop": [...]}}` and `"pins_digest": "<hex>"`, so the job log records what was pinned and
  under which digest. The human output prints the same lines.
- **Without `--expect-pins`**, `--repin` is unchanged (a TTY prompt per server).

## 6. TOCTOU between probe and pin

| Window | What could change | Why nothing unreviewed is pinned |
|---|---|---|
| Read → `up` (seconds to hours) | Upstream definitions; `stack.lock` (another `up`); `stack.yaml` (identity) | `up` probes again and hashes what it will pin. A different definition, base pin set, identity or server set gives a different digest, and nothing is pinned (`PINS_CHANGED`). |
| `up` probe → `up` save | Upstream definitions | What is saved is the hash of the definition from the probe that matched, never a later fetch. The pins file holds that definition. `pins_document` keeps only definitions that hash to the locked hash. |
| Lock saved, pins file not yet written (crash) | — | The relay keeps its old pins file. The old hash no longer matches the live definition, so its check reports "changed" and hides the tool (fail closed). The next `up` rewrites the file. |
| After the save | Upstream definitions (a rug-pull) | Unchanged by this delta. The relay's live check hides a changed tool (`TOOL_DEFINITION_CHANGED`) and re-checks when the pins file changes. Residuals R3 and R4 (§9). |
| Relay identity change | The relay's upstream | `lock_mcp` resets `pinned` to `{}` for a changed identity, so `up` refuses (`MCP_PINS_NEED_UP`) or the digest differs. |
| Relay config change on the same identity (e.g. an upstream URL path) | What the read probed | The read may have probed the old config. `up` recreates the relay, probes again, and gives `PINS_CHANGED` if anything differs. |
| Concurrent `services pins` and `up` | — | Both take the services write lock, so they serialize. |

The digest is content-bound, not time-bound. A digest replayed later succeeds only when the live
definitions, the base pins and the identities are exactly those reviewed (R2).

## 7. Console review flow (lane B2 consumer)

The console server drives the CLI as child processes, as for `plan --json` and `services up`.

1. **Entry.** The MCP screen shows drift from `services status --json`, as today. "Review repin"
   replaces the terminal hint when some server is in `drift`. The terminal command stays shown as
   an alternative. On `MCP_PINS_NEED_UP` the screen says "Start services first, then review".
2. **Read.**
   - It is a POST behind the same token, Origin and Host checks as job starts. It makes relays
     contact upstreams with stack credentials, so a web page must not trigger it.
   - The server runs `services pins DIR --json` with `Runner.read(..., cache=False, track=True,
     timeout=60 + 180 × relays, max_bytes=2 MiB)`. A cached or `stale` answer, a timeout or any
     error means "no review". `SERVICES_BUSY` is shown as for the plan.
   - `track=True` makes a timeout or console shutdown stop the whole process group, including
     the `docker compose exec` grandchild that holds the CLI's write lock. An untracked read
     kills only the CLI.
   - `Runner.read` gains `max_bytes`. Today it buffers everything with `communicate()`. With the
     limit it reads stdout incrementally and stops the process group once past it
     (`PINS_REVIEW_TOO_LARGE`, naming the terminal command). `ensure_ascii` escaping (up to 6×)
     and `previous` (up to 2×) mean a 1 MiB definition with non-ASCII text can trip the cap;
     that fails closed to the terminal.
   - While the read runs, the screen shows it in progress and disables Start.
   - JSON is parsed with a duplicate-key-rejecting `object_pairs_hook`, as the relay does.
   - The server verifies before keeping anything. Any failure, including `ValueError`,
     `UnicodeEncodeError` or `RecursionError`, is "invalid review", not a 500:
     - every `definition` hashes (`tool_hash`) to its `sha256`;
     - every `servers[s].new/changed[t].sha256` equals `review.servers[s].accept[t]`, and the key
       sets match;
     - `servers[s].drop` equals `review.servers[s].drop`;
     - `servers[s].origin` equals `review.servers[s].identity.origin` (it decides the untrusted
       `effect` warning);
     - every `changed[t].pinned` equals `review.servers[s].pinned[t]`;
     - every non-null `previous` hashes to that pinned hash;
     - `pins_review_digest(review) == digest`.
   - It keeps the review and digest per project, in memory, under the project lock. A console
     restart loses it, and a later start then gives `PINS_REVIEW_STALE`.
3. **Show.**
   - The **server** renders the text with Python, with the same `plan.review_lines` the CLI uses
     (ASCII-escaped, sorted, long strings wrapped), plus the unified diff, "diff too large", or
     "previous definition unavailable". It sends those lines. `state`, `same` and `missing` are
     derived from `pinned`, `accept` and `drop`, except `missing`, which comes from the CLI JSON
     (display only).
   - The browser sets `textContent` only and never parses or re-serializes a definition.
     Re-serializing in JavaScript would change `1.0` to `1` and round integers above 2^53, so a
     changed hash could show as an empty diff.
   - It shows `drop` as "this pin is removed", each server's origin, and, for a server that isn't
     built in, the existing warning that its `effect` labels are untrusted.
   - A tick, "I reviewed these tool definitions", enables Repin. A new read clears the tick, which
     never carries over, as for the grant.
4. **Start.**
   - The browser posts `{"kind": "services-up", "options": {"digest": <grant digest>,
     "pins_digest": <hex>, "allow_project_mcp": [...]}}`. `OPTIONS["services-up"]` becomes
     `("digest", "allow_project_mcp", "pins_digest")`.
   - First, under the project lock and as one step, it pops the stored review and compares its
     digest with `pins_digest` (`PINS_REVIEW_STALE` on none or a mismatch). Popping consumes it,
     so two concurrent POSTs can't both pass, and a console digest is used once. A page can't post
     a digest the server never showed.
   - Then come the existing pre-launch checks (`recheck`, key validation, the preflight plan and
     the grant digest). Any refusal before the job is launched puts the popped review back, but
     only if no newer review has been stored meanwhile. Once the job is launched, it stays
     consumed.
   - `jobs.argv_for` checks `pins_digest` against `DIGEST` and appends `--repin --expect-pins P`
     together, after `--yes --expect-grant G`. The console never passes `--repin` without
     `--expect-pins`, since it has no TTY.
5. **Outcome.**
   - On `PINS_CHANGED` (the `[CODE]` from stderr, as for other job failures) the job reports: "the
     definitions changed since your review; pins unchanged, the rest of Start ran; review again".
     The screen re-reads status and offers a new review.
   - On success the job log shows `repinned` and `pins_digest`, and the screen re-reads `services
     status`.

## 8. Failure codes

| Code | Where | Meaning |
|---|---|---|
| `MCP_RELAY_NOT_RUNNING` | `services pins` | A relay of the stack isn't running. |
| `MCP_PINS_NEED_UP` | `services pins`, `up` | A server has no pins but has tools to pin (first pins, or a pending identity change); run `services up` without `--repin`. |
| `MCP_PROBE_FAILED`, `MCP_ADMIN_TOKEN_MISSING` | both | Existing; no digest. |
| `SERVICES_BUSY` | `services pins` | Existing; another services write holds the lock. |
| `PINS_EXPECT_INVALID` | `up` | `--expect-pins` isn't 64 lowercase hex characters. |
| `PINS_EXPECT_NEEDS_REPIN` | `up` | `--expect-pins` without `--repin`, or with `--locked`. |
| `PINS_CHANGED` | `up` | The live review's digest differs from `--expect-pins`; pins unchanged. |
| `PINS_REVIEW_TOO_LARGE`, `PINS_REVIEW_STALE` | console | §7. |
| `MCP_PINS_NOT_CONFIRMED` | `up` | Existing, unchanged: `--repin` without `--expect-pins` and no TTY. |

## 9. Security (S-35 delta)

| # | Concern | Finding | Disposition |
|---|---|---|---|
| 1 | Consent off the TTY | `--expect-pins` accepts only a digest of content the caller fetched. A script can chain `services pins --json` into `up --expect-pins` with no person looking, as `plan --json` chains into `--expect-grant`. The digest proves the definitions were fetched and are unchanged, not that anyone read them. This also covers `new` tools of a non-built-in server, which until now needed a TTY. `--yes` alone still never repins. | R1, owner decision D1. The docs say so plainly. |
| 2 | TOCTOU between probe and pin | §6. `up` pins the hashes from the probe it checked, all or nothing. | Mitigated. |
| 3 | Digest binds less than what is shown | It binds the full `tool_hash` of every accepted definition, every dropped pin, the base pins, the identities and the server set. Display only: `previous` (bound through `pinned`), `same`, unpinned missing tools. | Mitigated. |
| 4 | Display differs from the hash | The CLI and console text is Python's `ensure_ascii=True` sorted JSON, which round-trips to the hashed object except for the display-only wrapping of long strings (§4); real U+21A9 in data shows escaped as `\u21a9`, so a `↩` is always the marker. The browser never re-serializes (§7.3). Non-ASCII shows as `\uXXXX`, so look-alike, bidi and control characters (and NFC vs NFD) are visible. The cost is that non-English descriptions are hard to read. | Mitigated; cost accepted. |
| 5 | Partial repin | Probe all, check once, save once, then write pins files. A crash between save and file write fails closed (§6). | Mitigated. |
| 6 | Lost update on `stack.lock` | `pinned` is bound. The write lock covers check and save, and the read takes it too. | Mitigated (hand edits out of model). |
| 7 | Console posts an unseen digest | Only the server's own last review for that project is accepted, verified before display (hashes, `servers`↔`review`, digest), consumed on start and restored only on a pre-launch refusal. The read is a guarded POST. | Mitigated. |
| 8 | Read side effects | Write-locked; it never changes `stack.lock`, pins files, secrets, the admin token or containers (§4). | Mitigated. |
| 9 | Replay of an old digest | Content-bound (§6): a replay pins exactly what was reviewed, under the reviewed base. A digest someone declined in the console can still be applied from a CLI by a user with the same access. | R2, owner decision D2. No TTL. |
| 10 | Failed `up` side effects | On `PINS_CHANGED`, phase 1 already ran: grant recorded, services restarted (§5). Pins are unchanged. | Stated in the CLI message and the console job text. |
| 11 | Rug-pull after the pin | The relay's live check runs at most every `CHECK_AGE` (300 s) per session, and on every pins-file change. It is not atomic with the call. A call after an upstream change and before the next check reaches the upstream. | R3, existing; out of scope. |
| 12 | Pins are metadata, not behaviour | A pin binds the advertised definition, not what `tools/call` does. An upstream can serve the pinned definition and act differently. It can also tell the probe (no agent token, fresh handshake) from runtime traffic. And a description is upstream-authored text: it can be a prompt injection aimed at the reviewer or the agent. | R4, new, accepted: "pins are metadata integrity, not behaviour attestation". The docs say so. |
| 13 | Size / DoS | A hostile upstream can make the probe exceed 8 MiB, which fails closed (`MCP_PROBE_FAILED`) and blocks repin for that stack. A stalled probe holds the services lock for up to 180 s per relay. The diff is capped at 2,000 display lines per side. The console caps stdout at 2 MiB and stops the process group past it. | Accepted (fail closed, bounded). |
| 13a | A change hidden in a long line | Display wraps long strings at 100 columns with a marker, so a one-character change in a 50 KB description shows as a small hunk. | Mitigated (display only). |
| 14 | Project servers | `--allow-project-mcp` and the console tick stay required for non-built-in servers before any relay starts. | Unchanged. |
| 15 | Leakage | The review holds identities (ref, origin label, upstream origin, token host) and tool definitions, no tokens. The admin token goes on stdin only, and `valid_probe` never echoes upstream text into errors. | No change. |
| 16 | Audit | No audit-chain row, as for `--repin` today. The record is `stack.lock` (git) and the job log (`repinned`, `pins_digest`). | Accepted. |

## 10. Tests (lane A, Docker faked unless noted)

- `pins_review_digest`:
  - stable across key order;
  - changes when any accepted definition field, dropped pin, base pin, identity field or server
    changes;
  - `v` is part of it;
  - `1` vs `1.0` and an integer above 2^53 give different hashes.
- `services pins --json`:
  - diff, drop and `previous`;
  - `previous: null` when the pins file doesn't hash;
  - `state: ok` for a server whose pinned tools are all served unchanged;
  - a declared tool that is never served and never pinned is in `missing`, not `drop`, and the
    digest is the same before and after;
  - a stack with no `mcp:` entries exits 0 with `servers: {}`;
  - a relay that isn't running gives `MCP_RELAY_NOT_RUNNING`;
  - first pins, or a pending identity change, give `MCP_PINS_NEED_UP`;
  - a never-pinned server with nothing served (`accept: {}`) is not `need_up`, and another
    server's repin goes ahead;
  - a probe failure on one of two servers fails the read with no digest;
  - `stack.lock`, the pins files, the secrets and the admin token are byte-identical afterwards,
    and no token is created;
  - a held write lock gives `SERVICES_BUSY`.
- Human `services pins`:
  - the read-mode header asks no question;
  - only `drop` is listed as removed;
  - a one-character change inside a 50 KB description shows as a small wrapped hunk;
  - a definition over 2,000 display lines shows "diff too large".
- `up --repin --expect-pins`, with a scripted probe sequence:
  - the same probe: pins applied, `stack.lock` and every pins file updated, `repinned` reported;
  - one field of one definition changes between read and `up`: `PINS_CHANGED`, with `stack.lock`
    and every pins file byte-identical;
  - a second server drifts after the review: `PINS_CHANGED`, and the first server is not pinned
    either (all or nothing);
  - the base pins change: `PINS_CHANGED`;
  - a tool goes missing after the review: `PINS_CHANGED`;
  - an empty `pinned` with a hand-computed digest: `MCP_PINS_NEED_UP`;
  - nothing to change: success with `repinned: {}`, and no lock write;
  - with `--update` and an identity change: refused;
  - `confirm_pins` is never called;
  - `--yes` without `--expect-pins` still refuses a repin off a TTY (`MCP_PINS_NOT_CONFIRMED`).
- Flags: bad hex, uppercase, wrong length, no `--repin`, `--locked`; each with the JSON envelope.
- CLI help golden (`tests/test_cli_help_golden.py`) and the command-docs line-3 test, on 3.11,
  3.12 and 3.13.
- Console (lane B2):
  - server tests:
    - `max_bytes`: an incremental read, and the process group stopped past it;
    - `track=True`: a timeout stops the grandchild and releases the lock;
    - `origin`, `pinned` or `previous` not matching `review`;
    - a concurrent double POST: one start, one `PINS_REVIEW_STALE`; a preflight refusal keeps
      the review;
    - a duplicate key;
    - a tampered definition;
    - `servers` not matching `review`;
    - a digest mismatch;
    - `cache=False`, and a stale or timed-out read meaning no review;
    - `PINS_REVIEW_STALE`, including after a console restart and after a used digest;
    - the read refused without the token, or from another Origin;
    - argv: `--repin` only with `--expect-pins`;
  - JS tests: `textContent` rendering only, and the tick reset;
  - Playwright with a scripted CLI: review, tick, start, the `PINS_CHANGED` re-read; axe in light
    and dark.
- Live, in the batched M15 verification (`AGENTBREEDER_DOCKER_TEST=1`): the GitHub fixture with a
  fake upstream whose `get_me` description changes. Read and apply; then a change between read and
  apply is refused.

## 11. Owner decisions

- **D1.** Accept R1. A digest proves the content was fetched unchanged, not that a person read it
  (as `--expect-grant`). This includes new tools of non-built-in servers. Recommendation: accept.
- **D2.** Accept R2: no TTL or one-time use in the CLI. The console uses a digest once.
  Recommendation: accept; content binding means a replay pins only what was reviewed.
- **D3.** A new subcommand, `services pins` (recommended), or `services status --pins --json`.
  Status takes no lock and calls no `plan()`, so the subcommand keeps it that way.

## 12. Review log

Different-model review (Sonnet, read-only subagents; coder Opus).

| Round | Lens | Verdict | Findings and disposition |
|---|---|---|---|
| 1 | Architecture | Changes requested | (H) The read can't be "no file written": `plan()` copies into the state dir. Fixed: the read is in `WRITES`, and §4 lists what it never changes. (H) The console's `Runner.read` returns cached answers by default. Fixed: `cache=False`, a scaled timeout, and stale means no review. (M) `declared` must be passed to `valid_probe`: one shared `pins_review` helper. (M) `NEED_UP` is `lock_tools == {}` after plan. (M) Undefined behaviour for unchanged servers and first pins: every pins file is written, first pins are refused explicitly in `up`, and nothing-to-change is a no-op. (M) `pins_text` can't be reused as is: a read-mode header, and only `drop` listed as removed. (M) `servers` wasn't tied to `review`: the console now checks it. (L) Flag errors go through `cli.fail`. (L) Dispatch and running-relay detection spelled out. (L) Exact `OPTIONS` and argv edits; the review is cleared on start and lost on restart. (L) Wording of the identity row in §6. (Nit) Prose moved; test cases added. |
| 1 | Security | Changes requested (no Critical or High) | (M) "Writes no file": fixed as above, which also closes the `down --purge` race. (M) Browser re-serialization could hide a changed hash: the server renders with Python and the browser sets `textContent` only; tests for `1.0` and integers above 2^53. (M) `PINS_CHANGED` leaves the rest of `up` applied: stated in §5, §7.5 and §9. (L) R4 added (metadata, not behaviour; probe vs runtime; prompt injection in descriptions). (L) Size: a 64 KiB diff cap, a streaming 2 MiB console cap with a kill, and 8 MiB fail-closed accepted. (L) Review hygiene: a guarded POST, the project lock, cleared on start, a duplicate-key-rejecting parse, and errors are "invalid review". (L) A relay running an old config on the same identity: §6 row. (Nit) The fail-closed crash wording, D1 covering new tools on non-built-in servers, and an `--update` test. |
| 2 | Architecture and security | Changes requested (no High) | All round-1 fixes verified in the text and code. (M) The read and `up` used different `NEED_UP` predicates, so a never-pinned server with nothing to pin blocked every review: one `need_up` from `pins_review` (pinned empty and accept non-empty), with a test. (M) `Runner.read` buffers and kills only the CLI: `max_bytes` with an incremental read, and `track=True` so the process group (and the lock) goes. (L) `origin`, `changed.pinned` and `previous` are now checked against `review`; `state`, `same` and `missing` are derived. (L) The write lock is held up to 180 s per relay: accepted; Start is disabled during the read. (L) Review consumption: popped atomically at compare time, put back on a preflight refusal. (L) A change hidden in a long line: display wraps strings at 100 columns; the diff cap is 2,000 lines. (Nit) `state` defined as `_mcp_status`; the escape-expansion note; a stale relay config failing `declared` names `services up`. |
| 3 | Architecture and security | Approved | All 9 round-2 findings verified as resolved. Low and nit wording fixes folded in as r4: the §9 row 4 wrap exception; wrapping keys too and never inside an escape; restore only into an empty slot and on any pre-launch refusal; `missing` comes from the CLI JSON; a probe failure on an unpinned server reports `MCP_PINS_NEED_UP`; the `NEED_UP` wording in §8 and §5. |
