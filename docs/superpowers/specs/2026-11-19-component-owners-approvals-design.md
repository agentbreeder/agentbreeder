# Component owners and approval states (#764): design

- Status: **r4, reviews converged**: the r1 architecture and `/security` reviews and the r2 and r3
  re-reviews (Claude Sonnet, read-only, each CHANGES REQUESTED) are folded in, and the r4 pass
  APPROVED with two wording fixes, also folded (§18). Awaiting owner sign-off on D1–D12 (§16). No code in
  this PR.
- Issue #764, part of epic #762 (release bundle and component lifecycle). Downstream consumers:
  #765 (registry graph), #767 (revocation that propagates), #768 (signed evidence export),
  #766 (RAG lineage) and #742 (team ownership; "a team can't attach another team's tool or MCP
  server without approval").
- Builds on (on `main` at b074b66e): the agent package of M13 (`package.py`, `release.py`,
  `deploy_ref.py`; spec `2026-11-13-agent-package-design.md`), `registry.py` (`KINDS`, `Ref`,
  `copy_and_hash`, `resolve`, `clean`, `usage`), `audit.py` (the hash-chained log: `append`,
  `verify`), `store.py` (the deploy state store, which this design does **not** change),
  `deployment.py` (`decide`, the second-person check), `deployer.py` (`Deployer`, verified OIDC
  deployers, `human`), `operator_config.py` (`TrustedIssuer.kind`, `read_private`) and
  `package_artifact.py` (`scan`, the leak patterns).
- **Nothing in this document has been built or run.** It describes intended behaviour. Claims
  about today's code cite the file and line on `main` at b074b66e.

## 1. Problem

The registry holds versions of nine kinds (`registry.py:42`), and `agent.lock` pins each one by
tree hash. Nothing says whether a version is fit to use:

- **Publishing is not approving.** `registry publish`, `import`, `fork` and `edit` write a
  version directory (`registry.py:637-708`, `registry.py:808`). None of them records who did it,
  in the audit log or anywhere else.
- **No owner.** No registry version, lock entry or operator setting names a team that answers
  for a tool, a prompt, an MCP server or a knowledge version.
- **No retirement.** A version found to be wrong (a leaking prompt, a vulnerable MCP server, a
  knowledge version built from data that must be withdrawn) cannot be marked as such. A later
  `build` locks it as readily as any other.
- **Approvals exist only for deploys.** `approve` and `reject` decide a pending deploy
  (`deployment.py:1172-1260`, `commands/approve.py`). Their checks (verified human, not the
  deployer, not the sole sponsor, policy) are the right shape, but they bind to a deploy
  revision in one state directory, not to a component.

#765 to #768 and #742 all need one fact this issue has to supply: **the lifecycle state of a
component version, who set it, when and why, as a record they can query and a chain of rows
they can export.**

## 2. Goal

Every component version has a lifecycle state, `approved`, `deprecated` or `revoked`, or none
(`unreviewed`). A state is set only through the CLI, by an identity verified against an issuer
the user's operator config trusts as human, and listed in that user's `components.yaml` as an
approver of the team owning the component (or, for `revoke` and `restore`, of a revoker team).
Both files are the user's own, so this is as strong as their write protection (§12.1). Each
change, and each refused authorization, is a row in a hash-chained **lifecycle log**, one per
user, read by every project, stack and environment of that user (§7).

Acceptance (each run on its own in the verification slice, §15 slice 5):

1. An approver of the owning team approves `tool/x@1.0.0`; `registry show` and `registry used-by`
   show `approved`, the approver and the time, and `registry lifecycle` shows the row.
2. An unverified user, a verified identity from a workload issuer, and a verified human outside
   the owning team are each refused, and each refusal is a row.
3. `deprecate` (with a reason and an optional replacement), `undeprecate` and `revoke` (with a
   reason) work the same way, and `build` names any deprecated or revoked version it locks.
4. A state binds to the exact tree that was reviewed: after the version's files change, the
   approval no longer applies to them.
5. The decision is seen from another stack and environment of the same user without being
   recorded again.

## 3. Scope and non-goals

In scope:

- The state model, its transitions and the effective-state rule (§5).
- Owners and approvers, in a new owner-only file, `components.yaml` (§6).
- The lifecycle log: its own SQLite file and hash chain, separate from every deploy state
  directory (§7).
- `registry approve|deprecate|undeprecate|revoke|restore` and the read views (`registry show`,
  `list`, `used-by`, a new `registry lifecycle`), with `--json` (§8).
- Warnings in `build` for deprecated and revoked versions it locks (§8.4).
- A read-only lifecycle badge in the console (§10).
- The interfaces #765, #767, #768 and #742 will call (§11).

Non-goals (each belongs to a sibling issue, which this design must serve):

- **Refusing anything.** No gate, `build`, `deploy` or `release` refuses a version because of its
  state in #764. Refusing revoked versions, flagging or disabling running deployments, and an
  opt-in "approved only" environment floor are #767 (D7).
- The dependency graph, blast radius and a durable record of each deployment's components
  (#765, §11).
- Signatures, and sharing decisions across users or machines (#768, D1).
- Team membership from IdP groups or roles (#742; `Deployer` carries no groups today,
  `deployer.py:41-48`). #764 names approvers explicitly.
- Recording who *published* a version (a sibling gap, §12.4).
- Changing the `agent.yaml` schema, its JSON Schema, the deploy manifest, `agent.lock`,
  `package.json`, the operator config, the deploy store (`store.py`) or the Cedar schema.
- Approving in the console.

## 4. Terms

- **Component version**: a registry version, `kind/name@X.Y.Z`, of any kind in `KINDS`
  (`registry.py:42`), including `agent` packages.
- **Tree hash**: `registry.copy_and_hash` of the version directory (`registry.py:268`), the value
  `agent.lock` records.
- **Subject**: a component version as one (ref, tree hash) pair. Two directories with the same
  ref and different hashes (another root, an edited copy, M9b's "same version, two hashes") are
  two subjects.
- **Key**: what a decision is recorded against: a subject `(ref, sha256)`, or a whole ref
  `(ref, "*")` (§5.2).
- **Team**: a name in `components.yaml` with a list of approvers (§6).
- **Approver**: a structured `{issuer, subject}` pair in a team, matched against a verified
  `Deployer` (§6.2).
- **Lifecycle log**: the hash-chained rows of §7, the only record of decisions.

## 5. States

### 5.1 The four states

| State | Meaning | Effect in #764 | Effect later |
|---|---|---|---|
| `unreviewed` | no decision recorded (the default) | none | #767 floor may refuse it where approval is required |
| `approved` | the owning team vouches for this exact tree | none | allowed under the #767 floor; #742 cross-team attach |
| `deprecated` | still usable; prefer the replacement | `build` warns (`COMPONENT_DEPRECATED`) | none planned beyond the warning |
| `revoked` | must not be used | `build` warns (`COMPONENT_REVOKED`) | #767: new deploys refused, running ones flagged or disabled |

`unreviewed` is never recorded; it is the absence of a decision. A fifth reader result,
`unavailable`, means the log could not be read (§7.4); it is never a state.

### 5.2 Keys: exact hash or whole ref

- `approve` always records against one subject: the ref **and** the tree hash the approver
  reviewed (§8.1). An approval never covers a tree nobody looked at.
- `deprecate`, `undeprecate`, `revoke` and `restore` record against the whole ref
  `(ref, "*")` by default, and against one subject with `--sha256`. Retiring a version should
  also catch copies of it that differ, and a revocation must be possible without a local copy
  (D4).
- The log is per user, not per project (§7.1), so a ref-wide decision applies to every project of
  that user that uses the ref. When two projects reuse a ref name for different trees, narrow with
  `--sha256`; the docs say so.

### 5.3 The state of a key, and the effective state

The state of a key is the result of replaying, in order, the allowing rows recorded for that key:

| Row | State of the key after it |
|---|---|
| `approved` | `approved` |
| `deprecated` | `deprecated` |
| `undeprecated` | back to what it was before the most recent `deprecated` (that is, `approved` or none) |
| `revoked` | `revoked` |
| `restored` | none (any earlier `approved` or `deprecated` of that key is dropped too; approve again) |

The **effective state** of a subject (ref R, hash H) is the most severe of the state of
`(R, H)` and the state of `(R, "*")`, in the order `revoked` > `deprecated` > `approved` >
`unreviewed`. So an approved tree whose ref is deprecated ref-wide reads `deprecated`, and an
exact approval cannot lift a ref-wide revocation. The result also carries the last approval of
`(R, H)` (who and when), shown as `approved_by` even when the effective state is more severe.

`lifecycle.effective(snapshot, ref, sha256) -> Effective | Unavailable` is a pure function over
a snapshot, and every reader (CLI, `build`, console, #765, #767, #768) calls it, so the rule is
written once. A snapshot is one read transaction that verifies the chain and the checkpoint (§7.4)
and replays the rows, so nothing can change between the check and the replay. A hash the log has
never seen gets the state of `(ref, "*")` only.

### 5.4 Transitions

Each verb is checked against the **state of the key it writes** and, for `approve`, against the
**effective state of the subject**, inside the write transaction (§7.3). The keys of a ref are the
ones the log holds: `(ref, "*")` and every `(ref, H)` with a row. The table:

| Verb | Allowed when | Who |
|---|---|---|
| `approve` (exact) | the effective state of the subject is `unreviewed` (otherwise `STATE_UNCHANGED` if it is `approved`, `IS_DEPRECATED` if a deprecation covers it, `IS_REVOKED` if a revocation covers it) | owner team |
| `deprecate` | the key's state is none or `approved` (`STATE_UNCHANGED` if already `deprecated`; `IS_REVOKED` if `revoked`) | owner team |
| `undeprecate` | the key's state is `deprecated` (otherwise `NOT_DEPRECATED`) | owner team |
| `revoke` | the key's state is not `revoked` (otherwise `STATE_UNCHANGED`) | owner team, or a revoker team |
| `restore` | the key's state is `revoked` (otherwise `NOT_REVOKED`), and the `(issuer, subject)` pair is not the one that recorded that revocation | a revoker team only |

`STATE_UNCHANGED` is a note (exit 0) and writes no row.

- Keys are independent; only the effective state combines them. So:
  - an exact `revoke` or `deprecate` under a ref-wide decision is allowed and changes nothing until
    the ref-wide decision is lifted;
  - a ref-wide `undeprecate` lifts only the ref-wide deprecation; exact keys keep their states;
  - a ref-wide `restore` leaves an exact `revoked` key revoked.
- A test table covers every verb on an exact key and on the ref-wide key, against every state of
  the other key.
- To approve a subject covered by a ref-wide deprecation, first `undeprecate` the ref
  (D6), then approve.
- `revoked` is terminal for owner teams. `restore` exists so that a mistaken or malicious
  revocation is undone through an audited, second-person row, never by editing the database (D5).
  It needs a second revoker, so `registry lifecycle --check-config` warns when the `revokers`
  teams hold fewer than two distinct approvers. Without one, the only recovery is a new version,
  and the docs say so.
- A test pins that no sequence of rows lowers an effective `revoked` except a `restore` of the
  key that holds it.
- `deprecate`, `revoke` and `restore` need `--reason`. `approve` and `undeprecate` take an
  optional one.
- `deprecate --replacement REF` names a successor. It must parse with `Ref.parse`, be of the same
  kind, differ from the deprecated ref, and not be effectively `revoked`
  (`REPLACEMENT_INVALID`). It is advice: nothing resolves it.

## 6. Owners and approvers

### 6.1 `components.yaml`

Owners and approvers live in their own file, not in the operator config and not in the
component (D2):

- Not in a component's `manifest.yaml`: the publisher would choose its own owner, inside the
  tree hash.
- Not in the operator config: its digest is the hash of the raw file (`operator_config.py:558`)
  and feeds every revision digest (`gate.py:478-500`), so editing an approver list would
  invalidate every pending deploy approval. And `--stack` points the operator config at a
  generated demo config (`cli.py:215-260`), which would have no teams.

The file is `~/.agentbreeder/components.yaml`, or the path in `AGENTBREEDER_COMPONENTS`. It is
read with `operator_config.read_private` (owner-only, size cap) and a strict pydantic model. No
`--stack`, `--environment` or project setting changes which file is read.

```yaml
teams:
  platform:
    approvers:
      - {issuer: "https://idp.example.com/realms/acme", subject: "4f9c…"}
      - {issuer: "https://idp.example.com/realms/acme", subject: "91aa…"}
  data:
    approvers:
      - {issuer: "https://idp.example.com/realms/acme", subject: "c2d0…"}
  security:
    approvers:
      - {issuer: "https://idp.example.com/realms/acme", subject: "7be1…"}
owners:                         # first match wins; a ref no rule matches has no owner
  - {match: "knowledge/*", team: data}
  - {match: "tool/billing-*", team: platform}
  - {match: "mcp/*", team: platform}
revokers: [security]            # teams that may revoke and restore any component (D10)
```

Rules:

- `teams`: at most 32; names match `^[a-z0-9][a-z0-9_-]{0,31}$`; each has 1 to 32 approvers.
- An approver is `{issuer, subject}`. `issuer` matches the `TrustedIssuer.issuer` pattern
  (`operator_config.py:330`) and must contain no `#`; `subject` is 1 to 255 characters with no
  control characters. Comparison is on the structured pair, exactly (case-sensitive, no
  trailing-slash normalisation), never on a `Deployer.label` string, whose `oidc:<issuer>#<subject>`
  form is ambiguous when either part contains `#`. Display names, emails and aliases
  (`Deployer.aliases`, `same_person`) are never used for team membership (#742 gap 7), and a test
  pins that.
- `owners`: at most 128 rules. `match` is `<kind>/<pattern>`, where `kind` is one kind from
  `KINDS` (no `*` for the kind), and `pattern` is the registry name grammar plus `*`, which
  matches one or more characters of `[a-z0-9-]`. A custom matcher compares the whole name; no
  `fnmatch` (`?`, `[` and case folding are not supported). `team` must be a key of `teams`.
- A rule that an earlier rule fully covers (`mcp/*` before `mcp/billing-*`) is a load error
  (`OWNER_RULE_SHADOWED`), so first-match order can never silently hide a rule.
- `revokers`: team names; default empty.
- The file is validated against the operator config's trusted issuers **when a decision is made**:
  the deciding identity must come from an issuer the operator config trusts with `kind: human`
  (§6.2). The file itself does not depend on the operator config, so it can be checked on its own
  (`registry lifecycle --check-config`).
- No file: every component is unowned and every decision attempt is refused
  (`COMPONENTS_UNCONFIGURED`). Every version reads `unreviewed`. Today's behaviour is unchanged.

### 6.2 Who may decide

A decision is allowed only when **all** of these hold. The first failure is the refusal code. They
run in the order of §8.1: steps 1 to 5, then the resolve and a non-binding check of steps 6 and 7
(so a doomed decision is refused before the prompt), then step 8, then the binding re-check of
steps 6 and 7 (§8.1 step 4). A presence error (`TTY_REQUIRED`, `CONFIRMATION_MISMATCH`) therefore
takes precedence over the binding re-check and writes no row. Steps 2 to 5 run **before** the component is resolved, shown or confirmed
(§8.1), so a caller without authority learns no hash and types nothing. Refusals at steps 2 to 7
are rows (§7.2); input errors (bad ref, reason, missing terminal, a mistyped confirmation, a ref
not found) are not, and §14 marks which codes write a row:

1. `components.yaml` loads (`COMPONENTS_UNCONFIGURED`, `COMPONENTS_INVALID`).
2. The deployer is verified (`DEPLOYER_UNVERIFIED`, as `deployment.py:1202-1203`), through the
   user's operator config (default path or `AGENTBREEDER_OPERATOR_CONFIG`), never a stack's demo
   config. There is no override under `enforcement: audit` (D3).
3. The issuer is `kind: human` (`CHECKER_NOT_HUMAN`, as `deployment.py:1204-1205`).
4. The component has an owner, except for `revoke` and `restore` by a revoker team, which need
   none (a security team must be able to revoke a component nobody owns) (`COMPONENT_UNOWNED`).
5. The `(issuer, subject)` pair is an approver of the owning team, or, for `revoke` and `restore`,
   of a team in `revokers` (`APPROVER_NOT_IN_TEAM`).
6. For `agent/` packages only, on `approve`: the approver's label is not the `actor` of any
   `release/promoted` row for that ref and hash in **any** state directory AgentBreeder can find:
   the default (`cli.DEFAULT_STATE`, added explicitly), every environment of the stack named by
   `--project`'s `stack.yaml` and each `--state-dir` given (both through
   `registry_deployed.state_dirs`, `registry_deployed.py:68-80`, which does not include the
   default) (`SELF_APPROVAL`). These are other files, so step 6 is re-read just before the write
   transaction, not inside it; only step 7 is binding inside it. Rows in a
   directory not read are not seen, so the check is only as complete as the directories read; the
   docs say so. Release rows record only the label (`deploy_ref.py:120` reads them through
   `store.audit_matching`), so this is label equality, not `same_person`; the docs say so. For
   other kinds, the publisher is not recorded, so a team member can approve what they published
   (§12.4).
7. The transition is allowed (§5.4).
8. **Presence.** Decisions need an interactive terminal and a typed confirmation (§8.1 step 3,
   which runs after steps 2 to 5 and before the record re-checks steps 6 and 7). There is no
   `--yes` (D12).

The deciding team and the owning team are both recorded (`COMPONENT_TEAM`, `COMPONENT_OWNER`,
with the matched rule as `COMPONENT_OWNER_RULE`), together with the sha256 of `components.yaml`
(`COMPONENTS_DIGEST`). A later edit to `owners` does not rewrite who decided what. The read views
show the owner at decision time and, if it now differs, the current owner.

Component decisions do not consult the PDP (no Cedar action, D9). Blockers (`store.blocker`) are
keyed by agent principals (`store.py:39-43`), not people, so there is nothing for them to check
here.

### 6.3 What #742 builds on this

`components.yaml`'s `teams` is the team list #742 needs. The schema reserves an `agents:` rule list
(same shape as `owners`, matched against agent names) and refuses it in #764
(`COMPONENTS_INVALID: agents is reserved`), so #742 can add agent ownership without a format
change. #742 then compares a component's owner with the agent's owner: a component owned by team T
in an agent owned by team U ≠ T must be effectively `approved`. #742 may also fill approvers from
IdP groups once `Deployer` carries verified group claims. Neither is in #764.

## 7. The lifecycle log

### 7.1 Where it lives

Decisions live in a dedicated SQLite file, `~/.agentbreeder/lifecycle/lifecycle.db` (directory
0700, file 0600; `AGENTBREEDER_LIFECYCLE_DIR` overrides the directory), not in any deploy state
directory (D1). Reasons:

- A deploy state directory exists per stack environment (`registry_deployed.state_dirs`,
  `registry_deployed.py:68-80`) plus the default (`cli.DEFAULT_STATE`, `cli.py:38`). A revocation recorded in one
  would be invisible to the others.
- `build`, `registry show` and `registry list` have no state directory today (`build.py` opens
  no `Store`; of the `registry` subcommands only `used-by` takes `--state-dir`). They can all read
  one well-known file.
- The deploy store keeps its schema (v4). Nothing in `store.py`, `registry_deployed.py`
  (`_STATE_SCHEMAS`, `registry_deployed.py:58`) or `migrate` changes, and no existing state needs a
  migration.

The file holds one table, `audit`, with exactly `audit.SCHEMA` (`audit.py:53-62`), and
`PRAGMA user_version = 1` plus `PRAGMA application_id` set to a fixed lifecycle value, so a deploy
store opened as a lifecycle log (or the reverse) is refused (`LIFECYCLE_WRONG_FILE`). It uses a
rollback journal (`journal_mode=DELETE`), checked on every open as the deploy state's reader does
(`registry_deployed.py:83-112` refuses WAL), so read-only readers need no `-shm` file. Rows are
written with `audit.append` and checked with `audit.verify` (`audit.py:141`), so the chain code is
shared and not duplicated.

**Checkpoint.** After each committed append, the writer replaces `lifecycle/head` (0600, atomic
rename) with the last row's `seq` and `hash`. A reader that finds `head` and a log that is shorter,
or whose row at that `seq` has another hash, or no log at all, returns `unavailable`
(`LIFECYCLE_ROLLED_BACK`). This catches a truncated tail or a deleted log when `head` survives. It
does not catch anyone who rewrites or deletes both, so it is not tamper-evidence (§12.1).

### 7.2 Rows

One row per attempt, through `audit.append` with an `AuditEvent` built like
`DeploymentService._event` (`deployment.py:115-125`):

- `action`: `component`. `decision`: `approved`, `deprecated`, `undeprecated`, `revoked`,
  `restored`, or `deny`.
- `attempt_id`: a fresh `uuid4` hex per row. `enforcement_mode`: the operator config's
  `enforcement` (it never relaxes a check here, §6.2).
- `actor`, `actor_verified`: the deployer's label and flag. `principal_snapshot`: for an allowing
  row, `{issuer, subject}` only (no aliases). A `deny` row for an unverified actor records only the
  label, the code and the ref.
- `operator_config_digest`: the digest of the operator config used to verify the deployer.
- `reasons`, as `{code, message}` items in the style of M13's `RELEASE_*`:
  `COMPONENT_REF`, `COMPONENT_SHA256` (`*` for ref-wide), `COMPONENT_PREVIOUS` (the key's state
  before), `COMPONENT_TEAM`, `COMPONENT_OWNER`, `COMPONENT_OWNER_RULE`, `COMPONENTS_DIGEST`,
  `COMPONENT_ROOT` (the root label of the copy that was hashed, §8.1), `COMPONENT_REASON`,
  `COMPONENT_REPLACEMENT`; on `deny`, the refusal code and message instead.
- `evidence_digest`: sha256 of the canonical decision (`ref`, `sha256`, decision, team, owner,
  actor pair, reason, replacement).
- `image_digest`: for `agent/` packages, the package's `image.id`; otherwise null.
  `revision_digest`: null.

There is no separate state table. The state is the replay of the allowing rows (§5.3), so there is
nothing outside the chain to edit or to drift (r1 security finding 1). Volumes are small (a team
records tens to thousands of decisions); a reader replays the whole log. An index table can be
added later as a cache that `verify` checks against the log, if a measured read is slow.

### 7.3 Writes and concurrency

Every append, allowing or `deny`, opens the log, takes an exclusive `flock` on `lifecycle/worker.lock`, non-blocking as
`store.lock` (`store.py:285-295`; busy gives `LIFECYCLE_BUSY`, "retry"), then, in one
`BEGIN IMMEDIATE` transaction: replays the log, checks §6.2 and §5.4 against that replay, and
appends the row. A competing writer cannot interleave, so the check and the write see the same
state. If the append fails, the transaction rolls back and nothing is recorded
(`AUDIT_WRITE_FAILED`, exit 1). A test runs two racing approvers.

`audit.verify` and the checkpoint check run before every append; a broken chain gives
`LIFECYCLE_CORRUPT` (exit 1) and records nothing. A `deny` row that cannot be appended
(`LIFECYCLE_BUSY`, `LIFECYCLE_CORRUPT`, `AUDIT_WRITE_FAILED`) is reported with that code and exit 1,
and the refusal still stands.

### 7.4 Reads

`lifecycle.open_readonly()` reads `head` **first**, then opens the file with `mode=ro`, after an
owner and mode check like `registry_deployed._open_checked` (`registry_deployed.py:90`), and
begins one read transaction. Because the writer replaces `head` only after its commit, a `head`
read first can never be ahead of the snapshot that follows, so a concurrent write cannot cause a
false rollback report. A busy database is retried for up to 2 seconds (`busy_timeout`) before it
reads as `unavailable`. It returns one of:

- **absent**: no log and no `head`. Every subject reads `unreviewed`. Readers print nothing extra.
  This is trusted only in the sense that nothing was ever recorded here; a user who deletes both
  files gets the same answer, so #767's floor must not read `absent` as "never revoked" beyond
  that.
- **ok**: a verified log.
- **unavailable(reason)**: unreadable, wrong owner or mode, wrong file, WAL journal, newer
  `user_version`, `audit.verify` failed, or the checkpoint does not match.

Readers never turn `unavailable` into `unreviewed`. `build` prints
`warning LIFECYCLE_UNAVAILABLE <reason>`; `registry show --json` gives
`"lifecycle": {"state": null, "unavailable": "<reason>"}`. #767 must treat `unavailable` as a
refusal under an approved-only floor and as "may be revoked" otherwise (§11).

Older AgentBreeder versions do not read the lifecycle log at all; they show no states and print no
warnings. That is stated in the docs; it does not matter in #764, where nothing is enforced.

## 8. CLI

### 8.1 Decisions

```
agentbreeder registry approve     REF [--sha256 H] [--reason TEXT]                    [--project DIR] [--state-dir D] [--json]
agentbreeder registry deprecate   REF  --reason TEXT [--replacement REF] [--sha256 H] [--project DIR] [--json]
agentbreeder registry undeprecate REF [--reason TEXT]                    [--sha256 H] [--project DIR] [--json]
agentbreeder registry revoke      REF  --reason TEXT [--unknown-ref]     [--sha256 H] [--project DIR] [--json]
agentbreeder registry restore     REF  --reason TEXT                     [--sha256 H] [--json]   (no known-ref check: it needs an existing revoked key)
```

They sit under `registry` (lane C's `commands/registry.py`), so they do not read as deploy
approvals; `approve` and `reject` stay deploy-only (D11). `--state-dir` exists only to find the
release row of an `agent/` package (§6.2 step 6), read-only. The verbs take **no `--stack`**:
`cli._use_stack` (`cli.py:215-260`) would point the operator config at the stack's generated demo
config, whose human issuer is a key `services up` made locally. A test pins that a decision always
verifies against the user's operator config.

`approve` steps:

0. **Authorize the identity** (§6.2 steps 1 to 5; the owner depends on the ref only).
1. **Resolve** the ref through the roots of `registry show` (project `./registry`,
   `AGENTBREEDER_REGISTRY_PATH`, built-in; `registry.resolve`, `registry.py:331`). Not found:
   `COMPONENT_NOT_FOUND`. If more than one root has the ref with different hashes, list them and
   require `--sha256` to choose (`COMPONENT_AMBIGUOUS`).
2. **Hash** with `copy_and_hash` into a private temp directory (its entry checks: regular files, no
   symlinks, size caps). The hash and the root label recorded are those of this copy. If
   `--sha256` is given and differs: `COMPONENT_HASH_MISMATCH`. For `agent/`, the package's release
   row must exist and match (`deploy_ref`'s evidence check: `PACKAGE_UNRELEASED_HERE`,
   `PACKAGE_CHANGED`).
3. **Show and confirm.** Print the ref, the root label (`builtin`, `registry`, a project-relative
   path, or `external:`, as M13 §12.3), the hash, the owner team and rule, and the effective state.
   If the non-binding check of §6.2 steps 6 and 7 already fails, refuse now, before the prompt.
   Then ask the approver to type the first 12 hex characters of the hash. Without a TTY:
   `TTY_REQUIRED` (exit 2). This makes the approver look at the hash they approve, and keeps a
   background process holding a cached token from deciding silently; it does not prove that a
   person is present (§12.3).
4. **Record** (§7.3): re-read step 6's release rows just before the transaction; inside it,
   re-check step 7 against the replayed log, then append.

`deprecate`, `undeprecate`, `revoke` and `restore` follow the same steps, with these changes:

- Ref-wide (no `--sha256`), steps 1 and 2 are replaced by a **known-ref check**: the ref must
  resolve in a root, or appear in `agent.lock` or `stack.lock` of `--project` (default `.`,
  through `registry.usage`), or already have a row in the log. Otherwise `REF_UNKNOWN`, unless
  `revoke --unknown-ref` (for a vulnerable version known only by its advisory). This stops a typo
  or a guessed future version from being revoked without anyone noticing.
- The prompt says "every copy of tool/x@1.0.0" and asks the approver to type the ref.
- With `--sha256`, a local copy, if one exists, must match.

Exit codes: 0 on a recorded decision or `STATE_UNCHANGED`; 2 on a refusal or bad input; 1 on a
log failure. `--json` returns the decision (`{ref, sha256, decision, previous, effective, team,
owner, actor, decided, reason, replacement, seq}`) or the error's `code`. Every message ends with a
`Next:` line from the shared hint helper.

### 8.2 Reads

All reads use `lifecycle.open_readonly` (§7.4) and need no state directory.

- `registry lifecycle [REF] [--decision D] [--team T] [--check-config] [--verify] [--json]`: the
  rows, newest first; `--verify` checks the chain and the checkpoint and prints the result; with a REF, its keys, their states and the effective state of each local copy found.
  `--check-config` validates `components.yaml` and prints each team and rule.
- `registry show REF` gains `lifecycle`: `{state, keys: [{sha256, state}], owner, owner_rule,
  approved_by, decided, reason, replacement}` for the resolved copy, or `{"state": "unreviewed",
  "owner": …}`, or the `unavailable` shape of §7.4.
- `registry list` gains a state column (`--json`: `lifecycle.state`).
- `registry used-by REF` gains the effective state of each locked or deployed hash it lists.
- `status --json` is unchanged; a deployment's components are #765's.
- Actor fields print the label. Aliases and claims are never printed.

### 8.3 Human output

```
$ agentbreeder registry approve tool/billing-lookup@1.2.0
tool/billing-lookup@1.2.0  registry  sha256:ab12cd34ef56…9f00
owner   platform (rule 2: tool/billing-*)
state   unreviewed
Type the first 12 characters of the hash to approve it for team platform: ab12cd34ef56
approved by oidc:https://idp.example.com/realms/acme#4f9c…  (lifecycle #231)
```

Values printed from component files, from `components.yaml` and from the log go through
`registry.clean` (`registry.py:1140`).

### 8.4 `build` warnings

After it writes `agent.lock`, `build` computes the effective state of each lock entry (ref and
recorded hash) and prints one line per deprecated or revoked entry:

```
warning COMPONENT_REVOKED     tool/billing-lookup@1.2.0 is revoked (team security): "advisory GHSA-…"
warning COMPONENT_DEPRECATED  prompt/support@2.0.0 is deprecated: use prompt/support@2.1.0
```

- Ref-wide decisions apply to every lock entry with that ref, whatever its source; exact decisions
  match only the recorded hash.
- `build --json` gains an additive `"lifecycle": [{ref, sha256, state}]` for those entries only.
  Unreviewed entries are not listed.
- With an unavailable log, `build` prints `LIFECYCLE_UNAVAILABLE` once.
- The build's result and exit code do not change. The output names no roadmap item.

## 9. Schema and digest changes

| Artifact | Change |
|---|---|
| `agent.yaml`, its JSON Schema, `agentfile.py` | none |
| Deploy manifest, `docs/manifest.schema.json` | none |
| `agent.lock` (v1 and v2), `package.json` | none |
| Registry `manifest.yaml` | none |
| Operator config, its digest, revision digests | none (owners are in `components.yaml`, §6.1) |
| Cedar schema, `policy.SCHEMA_VERSION` | none (D9) |
| Deploy store (`store.py` v4), `registry_deployed._STATE_SCHEMAS` | none |
| New files | `~/.agentbreeder/components.yaml` (user-written), `~/.agentbreeder/lifecycle/lifecycle.db` |
| `build --json`, `registry show|list|used-by --json` | additive keys |
| CLI help goldens | five new subcommands and `registry lifecycle` |

No revision digest changes and no pending deploy approval is invalidated by the upgrade or by
editing `components.yaml`.

## 10. Console impact

- **Read-only.** The registry version view and the `used-by` panel show a state badge
  (`approved`, `deprecated`, `revoked`; nothing for `unreviewed`; a neutral "lifecycle unavailable"
  note for §7.4's case), the owner team, and, on hover or tap, the actor, time, reason and
  replacement. The badge uses the status tokens of `brand.css` (#817) with a text label, never
  colour alone.
- **No decision buttons.** Decisions need a terminal (§8.1). The panel shows the exact command to
  copy, as the trust flow does (M9b §8.3).
- Data comes from `registry show --json` and `registry lifecycle --json`, which the console already
  runs as CLI children (`center/reads.py:394`). The console caches `show` by a registry listing
  signature; the cache key gains the lifecycle file's size and mtime, so a decision is seen without
  a restart.
- `deprecated` links the replacement to that version's view, when it resolves.

## 11. Interfaces for the sibling issues

| Consumer | What it calls or reads |
|---|---|
| #765 graph | `lifecycle.effective` for each node; `registry lifecycle --json`. #765 owns a durable `(ref, sha256)` list per deployment (today only a package deployment has one, through its lock copy; `registry_deployed.build_record` reads a project file that can go stale, `registry_deployed.py:233-272`) |
| #767 revocation | `lifecycle.effective` from the gate on the deployment's components (from #765's list); `revoked` → a non-overridable deny; the approved-only floor reads `approved`; `unavailable` is a refusal under the floor and "may be revoked" otherwise; reconcile flags or disables running deployments whose components are revoked |
| #768 evidence | the lifecycle rows for every component of a release or deployment, with their `seq` range and the chain head, so the export can cite and check them |
| #766 RAG lineage | `knowledge/` versions are components like any other; an index built from a revoked knowledge version is #766's and #767's to flag |
| #742 ownership | `teams` and `owners`; the reserved `agents:` rules; effective `approved` for cross-team attach (§6.3) |

## 12. Security

### 12.1 Trust model and claims

A decision is a row in the lifecycle log, written only after the checks of §6.2. What it gives:

- the identity that set a state was verified against an issuer the operator config trusts as
  `kind: human`, and was listed as an approver of the relevant team in `components.yaml` at that
  time (its digest is on the row);
- an approval names one exact tree; a change to any file in the version is a different subject;
- every attempt, allowed or refused, is a row, and the state is derived from the rows only.

What it does not give:

- Anyone who can write the lifecycle file can rewrite it. The chain detects corruption and edits
  that do not recompute later hashes; it does not detect a recomputed chain, and it does not
  detect a deleted tail (`audit.py:1-6`). The checkpoint (§7.1) adds a check for a truncated or
  deleted log while `head` survives; deleting or rewriting both goes unnoticed, and an un-revoked
  component then reads as never revoked. The docs never call decisions signed, tamper-proof or
  tamper-evident.
- `AGENTBREEDER_LIFECYCLE_DIR` and `AGENTBREEDER_COMPONENTS` redirect readers and writers to other
  files. Whoever sets the environment chooses the log, as with `AGENTBREEDER_OPERATOR_CONFIG`
  today. Console children inherit the owner's own environment (`center/jobs.py`), as in M9b §8.3;
  no code path derives either variable from project files, and a test pins that.
- Anyone who can write `components.yaml` can make themselves an approver, and anyone who can write
  the operator config can trust another issuer. Approver authorization is only as strong as the
  write protection of those two files (owner-only, checked on read). The digests on each row make
  such a change visible afterwards, not impossible.
- Decisions are per user and machine (D1). CI on another machine, and other users, do not see
  them until #768 provides a signed export.
- "Human" means the issuer is configured as a human issuer, not that a person was present.
- "Approved" says a team vouched for a tree. It does not say the tree is safe.

### 12.2 Binding to what was reviewed

- `approve` hashes a private copy, so a file changed between the hash and the record cannot be the
  one approved; the row holds the hash and root label of that copy. A test swaps the root between
  resolve and copy.
- The approver types part of the hash they approve.
- Ref-wide decisions apply only to `deprecate`, `undeprecate`, `revoke` and `restore`, which are
  meant to be broad, and need a known ref (§8.1).

### 12.3 Identity

- Approvers are exact `(issuer, subject)` pairs, never display names, labels or aliases.
- Workload issuers are refused, so CI cannot decide.
- `enforcement: audit` relaxes none of §6.2 (D3).
- A terminal and a typed confirmation are required (D12). A local process running as the user with
  a pseudo-terminal and a cached token can still drive the prompt; the docs say the confirmation
  raises the bar and does not prove presence.
- `restore` needs a revoker who did not record the revocation.
- Self-approval is refused for `agent/` packages by label, against the release rows of the state
  directories read (§6.2 step 6); a release recorded in a directory not read is not seen.

### 12.4 Known gap: the publisher is not recorded

`registry publish|import|fork|edit` write no row, so a team member can publish a tool and approve
it. The docs say so. Closing it means recording registry writes in the lifecycle log; that is a
follow-up under #762 (filed when this spec is approved), not built here.

### 12.5 Input handling

- REF and `--replacement` go through `Ref.parse` (`registry.py:78-104`); `--sha256` must match
  `^sha256:[a-f0-9]{64}$`.
- `--reason`: 1 to 500 characters after `registry.clean`; newlines are refused. It is scanned for
  the home directory and the `/Users/`, `/home/` and `~/` patterns of `package_artifact._LEAKS`
  (`package_artifact.py:64-70`; not its `..` pattern, which would refuse an advisory URL), and
  for a `scheme://user:password@host` URL (the rule
  `redaction.py` applies, `redaction.py:123`); a match gives `REASON_LEAK`. No general token
  scanner exists in the repo today, so other secrets are not detected; the docs say so.
  The docs say a reason is exported (#768) and must not hold secrets or customer data, and that it
  is untrusted text when tools or models read it.
- `components.yaml` is parsed strictly (unknown keys refused) with a size cap.
- Every printed value from a component, `components.yaml` or the log goes through `registry.clean`.

### 12.6 Failure

- A broken chain, an unreadable file or a file of the wrong kind records nothing and reads as
  `unavailable`, never as `unreviewed` (§7.4).
- An audit append failure rolls back (§7.3).

### 12.7 Privacy and volume

- Rows hold the label and, for allowing rows, the `{issuer, subject}` pair; no aliases or emails.
- Refused attempts each write one row. The file is local and owner-only; a user can only fill their
  own log. No rate limit in #764; a note in the docs.
- Nothing is sent anywhere.

## 13. Docs

- `docs/registry.md`: a "Lifecycle" section (states, keys, effective state, the commands, the
  claims of §12.1, the publisher gap of §12.4, per-user scope).
- A `components.yaml` reference (new page or a section of `docs/registry.md`).
- `docs/identity-gate.md`: component approvers versus deploy checkers.
- `docs/commands.md` and the CLI help goldens.
- A `changelog.d` fragment per slice ("on `main`, not in a published preview").

## 14. Messages and error codes

All exit 2 unless noted. Every message ends with a `Next:` line. `--json` errors carry `code`.
**Row** marks the codes that write a `deny` row to the lifecycle log (authorization and
transition refusals, §6.2); the others are input or local errors and write nothing.

| Code | When | Message (shape) |
|---|---|---|
| `COMPONENTS_UNCONFIGURED`, `COMPONENTS_INVALID`, `OWNER_RULE_SHADOWED` | `components.yaml` missing or invalid | which rule; Next: `registry lifecycle --check-config` |
| `DEPLOYER_UNVERIFIED` (row) | not signed in | `Component decisions need a verified identity.` Next: `agentbreeder login` |
| `CHECKER_NOT_HUMAN` (row) | workload issuer | `Only an identity from a human issuer can decide.` |
| `COMPONENT_UNOWNED` (row) | no owner rule matches | `tool/x@1.0.0 has no owner team.` Next: add an `owners` rule |
| `APPROVER_NOT_IN_TEAM` (row) | pair not in the team (or revokers) | `You are not an approver of team platform, which owns tool/x.` |
| `SELF_APPROVAL` (row) | `agent/`, approver released it | `The identity that released agent/support-bot@1.4.0 cannot approve it.` |
| `RESTORE_BY_REVOKER` (row) | `restore` by the revoking identity | `A different revoker must restore tool/x@1.0.0.` |
| `COMPONENT_NOT_FOUND`, `COMPONENT_AMBIGUOUS`, `COMPONENT_HASH_MISMATCH` | approve resolution | as §8.1 |
| `REF_UNKNOWN` | ref-wide, ref not known | `tool/x@9.9.9 is in no root, lock or decision here.` Next: `--unknown-ref` (revoke only) |
| `TTY_REQUIRED` | no terminal | `Component decisions need an interactive terminal.` |
| `CONFIRMATION_MISMATCH` | typed text differs | `Nothing was recorded.` |
| `IS_DEPRECATED`, `IS_REVOKED`, `NOT_DEPRECATED`, `NOT_REVOKED` (row) | transition refused (§5.4) | `tool/x@1.0.0 is revoked (by team security); a revoker can restore it.` |
| `STATE_UNCHANGED` (note, exit 0) | same state again | `tool/x@1.0.0 is already approved (by …, at …).` |
| `REASON_REQUIRED`, `REASON_INVALID`, `REASON_LEAK`, `REPLACEMENT_INVALID` | input | which rule |
| `PACKAGE_UNRELEASED_HERE`, `PACKAGE_CHANGED` | `agent/` evidence (M13) | as M13 §15 |
| `LIFECYCLE_BUSY` | lock held | `Another lifecycle decision is running; retry.` |
| `LIFECYCLE_CORRUPT`, `LIFECYCLE_WRONG_FILE`, `LIFECYCLE_ROLLED_BACK`, `AUDIT_WRITE_FAILED` (exit 1) | log failures | Next: `registry lifecycle --verify` |
| `COMPONENT_DEPRECATED`, `COMPONENT_REVOKED`, `LIFECYCLE_UNAVAILABLE` (warnings) | `build` | §8.4 |

## 15. Delivery slices

Each slice: tests first (pytest units, CLI end-to-end as a subprocess), docs and a `changelog.d`
fragment, the CLI help goldens on the Python versions available, the gate, a different-model
review, and owner approval. No slice needs Docker.

1. **Model, config and log.** `lifecycle.py` (keys, replay, `effective`, `open_readonly`, the
   append path on `audit.py`), the `components.yaml` model (strictness, approver pairs, the
   matcher, shadowing, reserved `agents:`), `registry lifecycle --check-config|--verify`. Tests:
   replay rules, the "never lowers revoked" property, a deploy store refused as a lifecycle file,
   two racing writers.
2. **Decisions** (lane C for `commands/registry.py`). The five verbs: resolve, hash, known-ref
   check, typed confirmation, authorize, record. Each refusal of §14 tested on its own with its
   row; tokens signed with a local JWKS file (`TrustedIssuer.jwks_file`) for a human and a workload
   issuer; the `agent/` self-approval check against M13's release row; the root-swap test.
3. **Reads** (lane C). `registry lifecycle`, `show`, `list`, `used-by`; `build` warnings and
   `build --json`; the `unavailable` paths.
4. **Console badge** (needs 3). Read-only badge, copyable command, the cache key; frontend review
   lenses.
5. **Verification** from a wheel built from `main` in a clean sandbox, recorded in
   `docs/superpowers/verification/764.md`: acceptance 1 to 5 with a locally generated issuer key
   and JWKS file. The docs then say exactly that: verified with a local JWKS issuer, not a live IdP.

Dependencies: M13 slices 1 to 3 (merged) for `agent/` packages. #765 and #767 start after slice 3;
#768 after slice 2. Slices touch no lane E file (`operator_config.py` is only imported, for
`read_private` and issuer lookup).

## 16. Owner decisions (open)

- **D1 Where decisions live.**
  - Recommended: one lifecycle log per user, `~/.agentbreeder/lifecycle/` (§7.1), read by every
    project, stack and environment.
  - Alternatives: each deploy state directory (decisions split across stacks and environments; a
    store schema change); a git-tracked file per component (shared, but anyone with push rights
    could write it, so it needs #768's signatures first).
- **D2 Where owners are declared.**
  - Recommended: `~/.agentbreeder/components.yaml` (§6.1).
  - Alternatives: the operator config (editing approvers would change every revision digest;
    absent under `--stack`); an `owner:` key in each version's `manifest.yaml` (chosen by the
    publisher).
- **D3 Unverified approvers.**
  - Recommended: never, in every enforcement mode.
  - Alternative: allow `os:` identities under `enforcement: audit`, marked unverified.
- **D4 Default key of deprecate and revoke.**
  - Recommended: the whole ref, `--sha256` to narrow, with the known-ref check.
  - Alternative: exact hash only, like approve.
- **D5 Undoing a revocation.**
  - Recommended: `restore` by a revoker team member other than the one who revoked.
  - Alternative: revoked is terminal; recovery is a new version.
- **D6 Undoing a deprecation.**
  - Recommended: `undeprecate` by the owner team.
  - Alternative: deprecated is terminal too.
- **D7 Where refusing starts.**
  - Recommended: #764 refuses nothing; #767 adds the revoked deny and the approved-only floor as one
    gate check.
  - Alternative: #764 adds the opt-in floor now.
- **D8 Built-in versions.**
  - Recommended: treated like any other version (unreviewed until a team approves them; a revoker
    can revoke them). No special switch.
  - Alternative: a switch that reads every built-in version as approved.
- **D9 Cedar.**
  - Recommended: no Cedar action in #764. A Cedar change alters `policy.SCHEMA_VERSION` and every
    revision digest (`policy/__init__.py:21`, `gate.py:478-500`); do it with #742's Cedar work.
  - Alternative: an `approve_component` action now.
- **D10 Revokers.**
  - Recommended: `revokers` teams may revoke and restore any component, owned or not, but not
    approve or deprecate.
  - Alternative: only the owner team may revoke.
- **D11 Command names.**
  - Recommended: `registry approve|deprecate|undeprecate|revoke|restore` and `registry lifecycle`.
  - Alternative: a top-level `component` group.
- **D12 Presence.**
  - Recommended: an interactive terminal and a typed hash prefix for every decision; no `--yes`.
  - Alternative: `--yes` with `--sha256` for scripted decisions by a human identity.

## 17. Cross-repo sync and files

- **OSS:** new `lifecycle.py` and `components.py` (the `components.yaml` model);
  `commands/registry.py`, `registry.py` (`show`, `list`), `registry_deployed.py` (`used-by` view)
  (lane C); `build.py` and `commands/build.py` (warnings); `center/reads.py` (cache key) and the
  badge; docs as §13.
- **Cloud (`agentbreeder-cloud`):** no code change expected; nothing there reads the registry
  kinds, the deploy store or a lifecycle file (to be re-checked by grep at slice 1). Its
  `CLAUDE.md` gets a note on `components.yaml` and the lifecycle log, in the slice 1 PR set.
- **Website:** nothing until a release; then `registry.mdx` and what-ships, only for what the
  verification slice ran.

## 18. Review log

- r1 (2026-11-19): draft from #762, #764, #765–#768, #742, the M13 spec and the code on `main` at
  b074b66e.
- r1 reviews (Claude Sonnet, read-only, against the code):
  - architecture: 13 findings, CHANGES REQUESTED;
  - `/security`: 15 findings, CHANGES REQUESTED.

  Spot-checked before folding: `store.lock` is a non-blocking `flock` (`store.py:285-295`);
  `audit.verify` covers only the `audit` table (`audit.py:141`); the operator config digest is the
  raw file hash (`operator_config.py:558`) and feeds `revision_digest`; `_STATE_SCHEMAS = (2, 3, 4)`
  (`registry_deployed.py:58`); `--stack` points the operator config at a generated one
  (`cli.py:215-260`); the migration lives in `Store.__init__`, not `commands/migrate.py`; `build`
  opens no store.
- r2 (2026-11-19) folds them:
  - **Where decisions live (arch 1, 2, 4; sec 4):** a per-user lifecycle log with its own chain
    replaces the deploy store table. No deploy store migration, no `_STATE_SCHEMAS` change; `build`
    and `show` read one well-known file. D1 restated.
  - **State outside the chain (sec 1):** no state table; state is the replay of the rows.
  - **Owners under `--stack`, and digest churn (arch 3, 13):** owners move to `components.yaml`,
    read from the user's home regardless of `--stack`; the operator config is unchanged. D2
    restated.
  - **Transitions and keys (sec 3, arch 7):** checks run on the key's state and on the effective
    state, inside the write transaction; `undeprecate` and `restore` verbs make D5 and D6
    reachable; a "never lowers revoked" property test.
  - **Ref-wide revoke of anything (sec 2):** the known-ref check and `--unknown-ref`; second-person
    `restore` (D5).
  - **Fail-open readers (sec 5):** `unavailable` is a distinct result, never `unreviewed`.
  - **Config write access and presence (sec 6):** stated in §12.1 and §12.3; a typed confirmation
    on a terminal, no `--yes` (D12).
  - **Owner globs (sec 7):** one kind per rule, a custom matcher, shadowed rules refused, the
    matched rule recorded.
  - **Self-approval (sec 8, arch 6):** label equality against any matching release row, stated as
    such.
  - **Concurrency (sec 9):** `LIFECYCLE_BUSY`; replay, check and append in one `BEGIN IMMEDIATE`.
  - **Privacy (sec 10):** label and `{issuer, subject}` only; no aliases stored or printed.
  - **Reasons and replacements (sec 11):** the URL-password rule added to the leak scan (no
    general token scanner exists, stated); a revoked replacement is refused.
  - **Label collisions (sec 12, arch 5):** approvers are structured pairs; `#` refused in issuers.
  - **Blockers and PDP (sec 13):** stated in §6.2 (blockers are keyed by agent principals).
  - **Root swap (sec 14):** recorded root and hash come from the copy; a test.
  - **Deployment components (arch 8):** owned by #765, stated in §11.
  - **#742 shape (arch 9):** reserved `agents:` rules; owner and deciding team recorded separately.
  - **`build` details (arch 10):** ref-wide decisions match any source; additive `--json`; no
    roadmap text in output.
  - **Slices (arch 11):** goldens, config tests, console cache key, Cloud note in slice 1; no lane E
    file changes.
  - **Smaller points (arch 12):** revokers need no owner; distinct refusal codes (`IS_REVOKED`)
    and warning codes (`COMPONENT_REVOKED`); D8 restated without a switch.
- r2 re-review (Claude Sonnet, read-only): 8 findings, CHANGES REQUESTED; no wrong citations, two
  imprecise ones. Spot-checked: `_use_stack` skips only `cluster` and `watch` (`cli.py:221`); the
  deploy state reader refuses WAL (`registry_deployed.py:83-112`).
- r3 (2026-11-19) folds them:
  - **`--stack` on the verbs (1):** removed; `--state-dir` only, and a test that decisions verify
    against the user's operator config.
  - **Rollback and deletion (2):** the `head` checkpoint and `LIFECYCLE_ROLLED_BACK`; §12.1 names
    tail deletion and deleting both files; `absent` is stated for #767.
  - **Restore deadlock (3):** `--check-config` warns under two revokers; recovery by new version
    documented; the restore check compares `(issuer, subject)` pairs.
  - **Authorization order and deny rows (4):** identity and team checks run before resolve, show
    and prompt; §14 marks the codes that write a row; input errors write none.
  - **Covered keys (5):** keys are the log's keys of the ref, independent; cross-key cases stated;
    `restored` drops earlier states; a cross-key test table.
  - **Journal mode and environment redirects (6):** `journal_mode=DELETE`, checked on open; the
    two variables stated in §12.1.
  - **§2 wording (7):** softened to what one user's files give.
  - **Leak scan (8):** the `..` pattern is excluded.
  - Snapshot is one verified read; an unseen hash gets the ref-wide state; `attempt_id` and
    `enforcement_mode` specified; the two imprecise citations replaced.
- r3 re-review (Claude Sonnet, read-only): no high findings, 4 medium and 2 low, CHANGES
  REQUESTED; citations matched `main`.
- r4 (2026-11-19) folds them:
  - **Self-approval by `--state-dir` choice (1):** every state directory found is read; the limit is
    stated in §6.2 and §12.3.
  - **Missing transition codes (2):** `NOT_DEPRECATED`, `NOT_REVOKED`; a repeat `deprecate` or
    `revoke` is `STATE_UNCHANGED` with no row.
  - **Checkpoint race and busy readers (3):** `head` is read before the read transaction; a 2 s
    busy timeout.
  - **Check order and deny-row appends (4):** §6.2 states the §8.1 order; a non-binding transition
    check before the prompt; every append, deny included, takes the lock and verifies first.
  - **`restore` synopsis (5)** and **`--verify` (6)** stated.
- r4 re-review (Claude Sonnet, read-only): APPROVED, no high findings, two medium wording fixes,
  folded: §6.2 step 6 names the default state directory explicitly (`state_dirs` does not
  include it; `cli.py:38`) and the stack from `--project`'s `stack.yaml`, and says step 6 is
  re-read before the transaction while only step 7 binds inside it; presence errors take
  precedence over the binding re-check and write no row.
