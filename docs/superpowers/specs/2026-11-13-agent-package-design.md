# Agent package (M13, #763): design

- Status: **r7, reviews converged**: r1 reviews (architecture and `/security`) and the r2–r7
  re-reviews (Claude Sonnet, read-only) folded in; the r7 pass APPROVED (§17). Awaiting owner
  sign-off on D1–D3, D5 and D6 (§14). Then owner sign-off on the open decisions (§14).
- Epic #812, milestone M13 (#763, which absorbs the release-bundle part of #762), lane A. Plan of
  record: `docs/superpowers/plans/2026-11-11-console-epic.md` r3 §M13 (wins where this spec
  differs). Depends on M3 (#819, merged) and M9b (#829; spec merged as #842, code not started).
- Builds on: `src/agentbreeder/release.py`, `promote.py`, `evidence.py`, `build.py`, `lock.py`,
  `registry.py`, `deployment.py`, `store.py`; `docs/superpowers/specs/2026-11-07-agent-yaml-build-design.md`
  §6 and §11; the M9b spec `docs/superpowers/specs/2026-11-11-remote-registry-sources-design.md`
  §6.2, §6.4, §7 and §15.
- Later consumer: M19 (#835, A2A), which references agents through the `agent` kind defined here.
- **Nothing in this document has been run.** It describes intended behaviour. Claims about
  today's code cite the file and line on `main` at 90f117c.

## 1. Problem

`agentbreeder release` proves an image: it builds `--locked`, evaluates a candidate and promotes
the exact evaluated image. What it proved is scattered, and only partly durable:

- the image ID is in the deploy manifest under `deploy/` and in the audit row
  (`release.py:265-286`, reasons `RELEASE_IMAGE`, `RELEASE_MANIFEST`, `RELEASE_THRESHOLD`,
  `RELEASE_EVALSET`, `RELEASE_EVAL`);
- `agent.lock` and `build.json` are in the working tree, and the next build changes them
  (`build.py:399-411`);
- the eval report is a file under `deploy/`, named by run ID (`release.py:261-263`);
- the release journal keeps only the previous production manifest (`release.py:336-353`).

Nothing names "the thing that was released". No handle exists that a person, the console,
`promote` or another agent (A2A, M19) can use to deploy or call exactly that release. `promote`
derives the released image from whatever the local production deployment runs
(`promote.py:153-158`). After a later plain `deploy`, a promote cannot reach an earlier release.

## 2. Goal

`release` writes an agent package, `agent/<name>@X.Y.Z`. It is a registry version of a new kind,
`agent`, and records the image, the lock, the eval result and the environment-neutral half of the
deploy manifest. AgentBreeder never overwrites it.

`deploy agent/<name>@X.Y.Z` deploys exactly that image through the ordinary identity gate. On a
local `kind` cluster it pushes the image and an OCI artifact of the package side by side to the
loopback registry, and it runs the same digest.

Acceptance (from #763, run on its own):

1. A release writes a package.
2. Deploying it by ref on kind reproduces the same digest (defined in §8.3).
3. The local-target behaviour is documented (§7.4).

## 3. Scope and non-goals

In scope (M13):

- The `agent` kind in the registry (`KINDS`, `registry.py:41`) and the package format (§5).
- `release` writes the package into the project's `./registry`. The audited `release/promoted`
  row carries the package ref, its tree hash and the image (§6).
- `deploy agent/<name>@X.Y.Z` for the local target and for a local `kind` cluster (§7, §8).
- A durable link from each deployment to the package it came from: a store column and an audit
  reason (§7.3).
- An OCI artifact of the package, pushed beside the image to the loopback registry by
  `deploy --cluster` (§8).
- Read-only views of `agent/` versions: `registry list|show|diff|used-by|verify`.
- The lock shape that an `agent/` reference will use, recorded as an M9b contract change for M19
  to implement (§9).
- What M19 needs from the package (§10).

Non-goals (M13):

- Deploying a package that was not released from this state directory. That includes another
  machine, a teammate's package pulled with git, and an M9b remote source. The format allows
  these cases, but M13 refuses them (D5). This defers M9b §6.4: see §9.
- Owners, approval states, revocation, the dependency graph, RAG lineage and signed evidence
  export (the rest of #762: #764–#768). The package is designed so these can attach to it.
- Signatures or provenance. A package records what this machine's release recorded. It does not
  show who made it.
- Pushing to any registry other than the operator-configured loopback one.
- Replacing `promote` or `release --rollback` (§11, D6).
- An `agent.yaml` field that references another agent (M19).
- Cloud and production targets; Windows.

## 4. Terms

- **Image ID**: the local Docker image ID, `sha256:…`. It is what `build` returns and what the
  local target runs (`build.py:374-375`, `manifest.py:169`).
- **Registry reference**: `host/repo@sha256:<manifest digest>`. It is what the Kubernetes target
  runs (`promote.py:90-116`).
- **Tree hash**: `registry.copy_and_hash` of a version directory (`registry.py:262`). It is the
  hash used in `agent.lock`.
- **Deploy template**: the environment-neutral half of a deploy manifest (§5.4).
- **Release row**: the audited `release/promoted` event (`release.py:314`, `release.py:387`).

## 5. Package format

### 5.1 Where it lives

The package lives at `./registry/agent/<name>/<X.Y.Z>/` in the project and is written with
`registry.publish` (`registry.py:348-372`). `publish` stages the version, renames it into place
and refuses an existing version (`VERSION_EXISTS`).

A package is an ordinary registry version, so today's code applies to it:

- the entry checks: regular files only, no symlinks, UTF-8 names, `MAX_FILES`, `MAX_BYTES`;
- the tree hash.

The project registry is tracked in git, and committing the package shows the team what was
released (D2). AgentBreeder never overwrites a version. git can still change the directory, and
deploy then refuses it (§7.2 step 3).

### 5.2 Files

```
registry/agent/support-bot/1.4.0/
  manifest.yaml        kind: agent, name, version, description (the registry manifest)
  package.json         the record below (strict schema, at most 64 KiB)
  agent.yaml           byte copy of the released agent.yaml
  agent.lock           the released agent.lock, source labels redacted (§12.3); a record, never a lock input
```

A package contains no eval cases, model outputs, judge reasons, logs, secrets, registry references
or environment-specific values (§12.2).

### 5.3 `package.json`

```json
{
  "package_version": 1,
  "agent": {"name": "support-bot", "version": "1.4.0", "runtime": "runtime/adk@1.1.0"},
  "image": {"id": "sha256:6f1c…", "platform": "linux/arm64"},
  "inputs": {
    "agent_yaml_sha256": "sha256:…",
    "lock_sha256": "sha256:…",
    "lock_version": 1,
    "sources": {}
  },
  "evals": {
    "ref": "eval/support-smoke@1.0.0",
    "set_sha256": "sha256:…",
    "threshold": 1.0,
    "retries": 1,
    "run_id": "…",
    "passed": 12,
    "cases": 12,
    "environment": "dev"
  },
  "deploy": {
    "port": 8080,
    "health_path": "/health",
    "aliases": ["chat-default"],
    "budget_usd": null,
    "rpm": null,
    "mcp_access": []
  },
  "released": {"at": "2026-11-20T18:04:11Z", "agentbreeder": "3.0.0a3.dev0"}
}
```

Rules:

- **Strict schema** (pydantic, `extra="forbid"`, like `manifest.py`). Unknown keys, or a
  `package_version` other than 1, give `PACKAGE_INVALID` ("written by a newer AgentBreeder?
  upgrade"). New fields bump `package_version`.
- `image.id` matches `^sha256:[a-f0-9]{64}$`.
- `agent.name` and `agent.version` must equal the ref's name and version, and the ref must equal
  `manifest.yaml`'s name and version (`PACKAGE_INVALID`).
- `inputs.lock_sha256` is the hash of the lock as `build` wrote it (the value in `build.json`,
  `build.py:404-406`), not of the redacted copy in the package. When the lock is v2,
  `inputs.sources` is the M9b `build.json` `sources` table (redacted URLs and pins); otherwise it
  is `{}`.
- `evals` holds the summary already in the release row (`release.py:265-286`), plus `retries` and
  the environment the evals ran in. Per-case results stay in the eval report under `deploy/`.
- `deploy` is the deploy template, described in §5.4.
- `released.at` is the time the release started, fixed before the package is staged, so the
  package bytes do not depend on when the files are written.
- There are no registry references. Pushes are deployment events and are recorded in the audit log
  (§8). This keeps the package environment-neutral, and the pushed artifact holds exactly the
  published tree.

### 5.4 The deploy template, and why there is no manifest

The image is environment-neutral: `build` writes `agent.config.json` from the agent alone
(`build.py:366-367`), and every environment runs the same image ID. The deploy manifest is
environment-specific: identity, secret bindings and the gateway come from `environments.<env>` in
the operator config (`build.py:262-281`).

`build._manifest` is split into two functions:

- `template(agent, serving, aliases) -> dict` returns only the fields derived from the agent and
  its runtime: `port`, `health_path`, `aliases`, `budget_usd`, `rpm` and `mcp_access`.
- `bind(template, image, operator, env, identity, name) -> Manifest` fills `identity`, `secrets`
  and `model_access.gateway` from the operator config only. It sets `target` and `image` from the
  deploy path, never from the template. It validates the result with `Manifest.model_validate`.

The template schema has no field for `identity`, `secrets`, `policy`, `capabilities`, `requires`,
`target`, `image` or a gateway. A package therefore cannot grant itself an identity, a secret, a
policy or a target.

Tests:

- `build` keeps writing manifests byte-identical to today's (pinned for Northwind).
- A forged `package.json` with an extra template key is refused.
- `bind` takes `identity`, `secrets` and `model_access.gateway` only from the operator config,
  and `target` and `image` only from the deploy path.

### 5.5 Versions

`agent.yaml` has no version field (`agentfile.py:80-95`). M13 adds `release --version X.Y.Z`,
which is parsed with `Ref.parse`, so only exact `X.Y.Z` reaches a path or a tag
(`registry.py:42-44`, `registry.py:88-89`).

The version is resolved once, **after `build`**, when the image ID is known. That happens at
today's idempotent check (`release.py:180-186`), before the preflight (`release.py:197-209`) and
the `release/started` row (`release.py:210-211`). The same value is written to that row and used
for the package:

1. If a `release/promoted` row with a package matches this image and production manifest, and
   passes today's evidence checks (`evidence.released`, `evidence.py:23-67`: threshold not
   lowered, same locked eval set), release prints that ref and exits 0 (`PACKAGE_EXISTS`, a
   note). A `--version` that differs is named in the note. Labelling a released image again is
   not a release (D1). The package directory must also still exist, with `copy_and_hash` equal to
   `RELEASE_PACKAGE_SHA256`. The registry is tracked in git, so a branch switch or `git clean`
   can remove it. If the checks fail, the release continues as a new version, and a test pins
   this. If the checks pass and the production deployment's `package` column is NULL (a crash
   between the `promoted` row and `link_package`), this path calls `link_package` to repair it. Like today's
   check, this applies only while that image is the active, ready production
   (`release.py:181`). After a later plain `deploy` or `remove`, releasing the same image again
   writes a new version.
   `evidence.released` gains a `require_package` flag. `release` sets it, and rows without
   `RELEASE_PACKAGE` (from before M13) are then **skipped**, not raised on. The loop goes on to
   look for a packaged row (`evidence.py:42-67`); if there is none, the image does not count as
   released here (§6). `promote`
   leaves it unset and keeps accepting old rows.
2. Otherwise the version is `--version`, or the next patch above every taken version (`0.1.0` if
   none is taken).
3. **Taken** means:
   - a directory exists at `./registry/agent/<name>/<X.Y.Z>`, or
   - a `release/started` row in this state's audit log names it.

   `release` already records `release/started` before the candidate deploy (`release.py:210-211`).
   That row gains `RELEASE_PACKAGE`, so it reserves the version even if the release then fails,
   is interrupted, or has its directory deleted. The audit log is the only durable record; the
   journal is not used for this.

   A taken version gives `VERSION_EXISTS` (exit 2), and the `Next:` line names the next free
   version. No eval run is wasted.

   This guarantee holds per (project, state directory). `_ReleaseLock` serialises releases in one
   state directory (`release.py:162`). Two projects with separate stacks have separate registries
   and separate audit logs, and are told apart by `RELEASE_PROJECT` (§7.1).

Tests:
- `--version` lower than an existing version is accepted if free.
- A deleted directory whose version has a `started` row stays taken.
- A version taken by a failed release stays taken.

## 6. `release` writes the package

The package is published **before** production changes, and only the release row makes it
deployable. Changes to `release.release` (`release.py:139-393`), in order:

1. After `build`, resolve the version (§5.5) and fix `released.at`. Record the version as
   `RELEASE_PACKAGE` on the `release/started` row, which reserves it.
2. Publish the package after the eval gate passes and after the `PRODUCTION_CHANGED` check
   (`release.py:326-335`), and **before** `journal.write_text` (`release.py:344`). A failure here
   must not leave a stale "previous" for rollback, as the comment at `release.py:328-329`
   requires. On the G-F3 path, publish after its own production-changed check
   (`release.py:303-313`), before its row. The steps:
   1. Recompute the sha256 of the project's `agent.yaml` and `agent.lock`, and compare them with
      `build.json` (`build.py:399-406`). Evals take minutes, and an edit made meanwhile would
      give a package that does not match the evaluated image. On a mismatch, exit with
      `PACKAGE_INPUTS_CHANGED` and leave production unchanged.
   2. Assemble the package in a private (0700) staging directory.
   3. Validate `package.json` against its schema.
   4. Compute the tree hash `H`.
   5. `registry.publish` the staged tree. Check that the hash `publish` returns equals `H`
      (`PACKAGE_PUBLISH_MISMATCH` otherwise).
3. Then the code runs in its existing order: the journal (`release.py:344`), removal of the old
   production, the production deploy (`release.py:355-380`), and the release row
   (`service.record("release", "promoted", …)`, `release.py:387`, inside `defer_interrupt`). The
   row gains `RELEASE_PACKAGE` (the ref), `RELEASE_PACKAGE_SHA256` (`H`) and `RELEASE_PROJECT`, next
   to the existing `RELEASE_IMAGE`, `RELEASE_MANIFEST`, `RELEASE_THRESHOLD`, `RELEASE_EVALSET`
   and `RELEASE_EVAL`. `RELEASE_PROJECT` is the sha256 of the resolved project path; the raw
   path is never recorded (§7.1). One row binds the package, the image and the eval evidence,
   so there is no separate "packaged" event.

   The production deploy does **not** pass `package=`, so a failed deploy never links to a
   package that is then deleted. Right after the `promoted` row, in the same `defer_interrupt`
   block, release links the ready production deployment with `store.link_package`
   (§7.3), audited as `deploy/package_linked`. Only `promoted` rows carry
   `RELEASE_PACKAGE_SHA256`. `failed` and `eval_failed` rows carry neither package reason.
4. **Cleanup.** Every path out of `release` after step 2.5 runs a `finally` block. It applies
   whenever this run published a directory and recorded no `promoted` row: a failed production
   deploy restored, a deny, an interrupt, `REMOVE_FAILED`, or any exception. The block deletes
   the directory, but only if it still hashes to `H`. The deletion runs under
   `defer_interrupt`, so a second Ctrl-C cannot leave a half-deleted tree. The version stays
   reserved by its `started` row (§5.5).

   One exception: on `AuditUnavailable` or `AuditIncomplete` (`release.py:367-368`), the
   `promoted` row may or may not exist, so the directory is **kept**. The message
   (`PACKAGE_KEPT_UNVERIFIED`) says so and names `audit verify`. Deploy then works only if the row exists (§7.2 step 3). A later
   release finds the version taken.
5. **Crash between steps 2 and 3.** No `finally` runs, so the published directory has no
   `promoted` row and is not deployable (§7.2 step 3). The next release never adopts it. It picks
   the next free version, and an explicit `--version` naming the directory gives
   `VERSION_EXISTS`, whose message says the directory was "never released (an interrupted
   release); delete it if you do not need it".

The G-F3 path records a release without replacing production (`release.py:301-323`). It
publishes in step 2 the same way, adds the same three package reasons to its row at
`release.py:314`, and links production in the same way. That row and the publish move into one `defer_interrupt()` block; today the
row is recorded outside one.

Releases from before M13 have rows without `RELEASE_PACKAGE`. Because `release` calls
`evidence.released(require_package=True)` (§5.5), such a row does not end the release as
"already in production". The release takes the G-F3 path: it evaluates the same image as a
candidate, records a new row with a package, and does not replace production. Old rows are never
edited.

`release --json` gains `"package": {"ref", "sha256", "path"}`.

## 7. `deploy agent/<name>@X.Y.Z`

### 7.1 Command

```
agentbreeder deploy agent/support-bot@1.4.0 [--project DIR] [--environment ENV] (--local | --cluster) [--stack …] [--state-dir …] [--json]
```

- **Ref or file.** The positional argument is a package ref when it fully matches
  `agent/<name>@X.Y.Z`. Anything else is a manifest path, as today. To deploy a file literally
  named like a ref, write `./agent/…`. Any other registry kind
  (`tool/x@1.0.0`) gives `REF_NOT_DEPLOYABLE`.
- **`--project DIR`** is new and applies to `deploy` only. It defaults to the current directory,
  locates `./registry`, and is the `near` path for the CLI's `--stack` hint
  (`cli.py:127-162`). `--stack`, `--environment` and `--state-dir` keep their meaning.
- **State.** With a stack, the state directory is the stack's own. Without one it is the shared
  default (`~/.agentbreeder/local`, `cli.py:35`), so projects with the same agent name share
  release rows and therefore version numbers. Rows record `RELEASE_PROJECT` (a hash, §6).
  Deploying a package from another project with the same ref gives `PACKAGE_OTHER_PROJECT`
  rather than a misleading `PACKAGE_CHANGED`. The hash is of the resolved project path, so moving
  the project or using a `git worktree` of it also gives `PACKAGE_OTHER_PROJECT`. The docs say so,
  and the remedy is a new release from the new location.
- `before()` only parses the ref; it never touches the store. Resolution, hashing, evidence and
  binding run in `action`, where the store is open (`commands/deploy.py:25-46`).

### 7.2 Steps

1. **Resolve** only from the project's own `./registry` root. `AGENTBREEDER_REGISTRY_PATH`, the
   built-in registry and M9b sources are never searched for `agent/` refs, so the origin of a
   package is fixed by construction rather than checked afterwards. A ref found only elsewhere
   gives `PACKAGE_NOT_HERE`, naming where it was found.
2. **Hash** the version with `copy_and_hash` into a private temp dir. Load `package.json` and
   `manifest.yaml` from that copy, never from the registry path, so the files cannot change
   between the check and their use.
3. **Evidence.** Collect the `release/promoted` rows (`store.audit_matching("release",
   "promoted")`, as `evidence.released` reads them, `evidence.py:42`) whose `RELEASE_PACKAGE`
   equals the ref.
   - None: `PACKAGE_UNRELEASED_HERE`.
   - `RELEASE_PROJECT` differs from this project's hash: `PACKAGE_OTHER_PROJECT`.
   - More than one, with different contents: `PACKAGE_AMBIGUOUS`. Given §5.5, this needs a forged
     or corrupted log.
   - Otherwise, all of these must hold:
     - `RELEASE_PACKAGE_SHA256` equals the hash from step 2 (else `PACKAGE_CHANGED`);
     - `RELEASE_IMAGE` equals `image.id`;
     - `RELEASE_THRESHOLD`, compared as a float (`evidence.py:46`), equals `evals.threshold`;
     - `RELEASE_EVALSET` equals `evals.set_sha256`;
     - `RELEASE_EVAL` (`<run_id> <passed>/<cases>`) equals `evals.run_id`, `evals.passed` and
       `evals.cases`.

   Any other mismatch gives `PACKAGE_EVIDENCE_MISMATCH`. Before trusting the rows, deploy runs
   `store.verify_audit()` (`store.py:312`), which detects corruption only (§12.1). If verification
   fails, deploy refuses with `AUDIT_CORRUPT` (exit 1) and the `Next:` line `audit verify`. It
   walks the whole chain, so its cost grows with the log. That is acceptable locally, and slice 3
   records the time on a large log.
4. **Image.**
   - Local target: the image is `image.id`. If `runtime.image_present(image.id)` is false, the
     result is `PACKAGE_IMAGE_MISSING` (§7.4).
   - Cluster: the local Docker engine must hold `image.id`, checked with `Docker.image_present`.
     `Kubernetes.image_present` always returns true (`kubernetes.py:214-215`), so it is not used.
     Then push it (§8.2). The image is the digest reference that the push returns.
5. **Bind** the template to the environment with `bind(template, image, …)` (§5.4). The identity
   is `environments.<env>.agents.<name>.identity`, as for `build` (`build.py:321-326`).
6. **Gate and deploy** with `service.preflight` and `service.deploy(..., package=ref_and_hash)`
   (§7.3). The gate is unchanged: a deny exits 2 and a required approval exits 3. Approvals are
   keyed on the bound manifest's revision, so an approval from a plain deploy of the same manifest
   also applies here, and the reverse.
7. **Name conflicts** behave as for a plain deploy, by manifest digest (`deployment.py:256`).
   - A running deployment of the same name with a different spec gives `NAME_CONFLICT`. Its
     `Next:` line names `remove ID` and, for production, `release --rollback`.
   - A bound manifest equal to the running one takes today's equal-digest path. It does not
     return early: it reuses the record, calls `store.update_binding` and redeploys
     (`deployment.py:336-352`). `update_binding` gains `package=`, which sets the column only
     while it is NULL, in the same `store.transaction` as `transition`. The `DEPLOY_PACKAGE`
     reason is on that attempt's allow event. A column that already names a different package
     is not overwritten: the deploy goes ahead and prints a note. Tests cover the NULL and
     non-NULL cases, and a record in `starting` and in `failed`.
   - `deploy agent/…` never removes a running deployment.

### 7.3 Deployment-to-package link

`DeploymentService.deploy` gains an optional keyword, `package: tuple[str, str] | None`
(ref, tree hash). It also gains `push_event=`, described in §8.2. `package` has these effects:

- the store gets a nullable `package` column, and `SCHEMA_VERSION` goes from 3 to 4
  (`store.py:18`):
  - `agentbreeder migrate` reports the v3 → v4 upgrade, which the writable `Store()` performs
    (`commands/migrate.py`);
  - the version lists at `store.py:136` (`(0, 1, 2, SCHEMA_VERSION)`) and `store.py:213-218`
    (`(1, 2)`) gain **3**, the previous version, so a v3 state raises `StateNeedsMigration` rather
    than "unsupported";
  - a writable open migrates in place, as it does for v0–v2 today (`store.py:133-163`). Every
    version below 4, v0–v3, gets the `package` column before `user_version=4` is set.
    Any command that opens the store writable therefore upgrades it on its first M13 run, for
    example `deploy`, `release`, `remove`, `approve`, `promote`, `status`
    (`commands/status.py:24`), `quickstart`, `services` and the egress host. `migrate` does the
    same and reports it.
    Commands that open it read-only, `list` (`commands/list.py:29`), `logs`
    (`commands/logs.py:247`), `invoke` and `watch`, raise `StateNeedsMigration` with the migrate
    hint until then;
  - a 3.0.0a2 CLI refuses a v4 state. The CHANGELOG warns that the first writable command, `status`
    included, upgrades the state, and that a2 cannot read it afterwards;
  - tests: `list` on a v3 state raises `StateNeedsMigration`; `status` and `deploy` on a v3 state
    migrate it to v4 and keep every row;
  - `store.create` and `update_binding` gain `package=`;
  - `registry_deployed._STATE_SCHEMAS` gains 4 (`registry_deployed.py:55`);
  - `Store.get` selects every column, so `status --json` shows `"package": null` for deployments
    without one, which a compatibility test pins;
- the allow event gets a `DEPLOY_PACKAGE` reason (`ref sha256:…`), well under the 50-reason cap
  (`deployment.py:150`). A test pins the cap for this event and for the release row with its new
  reasons;
- `status --json` gains `package` for a deployment that came from one;
- `store.link_package(id, ref, hash)` sets the column and records `deploy/package_linked`, in one
  transaction. `release` uses it (§6 step 3). It may replace an earlier link, for example a
  re-release of the same production image under a stricter threshold. The event then names the
  previous package, so the link always shows production's newest release. `deploy agent/…`
  never replaces a link (§7.2 step 7);
- `registry used-by agent/…@…` lists active (not removed) deployments from the column through
  `registry_deployed.py`, which still reads only the `deployments` table. Removed rows keep their
  column, for history.

M19 resolves "which release does this deployment run" from the column, without scanning audit
rows.

### 7.4 The local target (acceptance 3)

- A local package's `image.id` is an image on the Docker engine where it was released. No
  registry holds it. On any other machine, deploy refuses the package at step 3 (no evidence) or
  at step 4 (no image).
- `docker image prune` and Docker Desktop's clean-up can remove a released image that no
  container uses. The package stays in `./registry` as a record but cannot be deployed:
  `PACKAGE_IMAGE_MISSING`, with "released images cannot be rebuilt bit for bit" and `Next:`
  `release` (a new version). Rebuilding is not offered, because only the released ID was
  evaluated. The docs say to keep released images.
- Evidence is for the release environment (`evals.environment`). Deploying a package to another
  environment passes that environment's identity gate, policy and approvals, but its evals did
  not run there. `deploy` prints this as a note, and `promote` remains the command that evaluates
  in the new place (D6).

## 8. Cluster: push, OCI artifact, same digest

### 8.1 Where pushes go

`deploy agent/… --cluster` pushes only to `environments.<env>.kubernetes.registry`
(`operator_config.py:106`). The host must be exactly the IP literal `127.0.0.1`, giving
`REGISTRY_NOT_LOCAL` otherwise. `::1` cannot be written in that field, because its pattern has no
brackets. The field always has a path (`(/…)+`), and the host is the part before the first `/`. A parse
test covers `127.0.0.1:5001/agents` (accepted), and `127.0.0.1.evil.com/x`, `localhost:5001/x`
and `127.0.0.2:5001/x` (all refused). kind's registry is `127.0.0.1:5001` (`cluster.py:32-33`).

This is stricter than `promote.registry_image_id`, which also accepts the name `localhost`
(`promote.py:74-75`). Tightening `promote` is a follow-up issue, not M13. Pushes use no
credentials, no proxy, and plain HTTP to loopback only.

### 8.2 What is pushed

On every `deploy --cluster`, steps 1–3 of §7.2 and the local-image check of step 4 run first.
Then:

1. **Image.** Push `image.id` with `promote.push_image` (`promote.py:90-116`) to
   `<registry>/<name>`, tag `v<X.Y.Z>`, and accept it with the same predicate
   (`promote.py:106`). Pushing an image the registry already holds is cheap. A registry recreated
   by `cluster down`/`up` therefore needs nothing special: the image is pushed again.
2. **Artifact.** Push the package to the same repository:
   - an OCI image manifest (`application/vnd.oci.image.manifest.v1+json`) with `artifactType:
     application/vnd.agentbreeder.agent.v1`;
   - `config`: the OCI empty descriptor;
   - exactly one layer, `application/vnd.agentbreeder.agent.layer.v1.tar+gzip`, a
     deterministic tar of the package at `agent/<name>/<X.Y.Z>/…`, made by the packer M9b
     specifies for `registry pack` (M9b §6.2), capped at `MAX_BYTES`;
   - `subject`: the image manifest's descriptor (OCI 1.1 referrers), subject to the slice-0
     spike;
   - tag `v<X.Y.Z>.agent`.

   The layer is the published tree, so a reader that extracts it and runs `copy_and_hash` gets the
   package's tree hash. That is the value an M19 lock entry pins (§9). An agent artifact uses its
   own `artifactType`, distinct from M9b's `application/vnd.agentbreeder.registry.v1`, so a
   registry source and one released agent cannot be confused.
3. **Audit.** `bind` needs the pushed digest, so the push happens before the gate (§7.2 steps
   4–6). The push is therefore recorded as its own event, `service.record("deploy", "pushed", …)`,
   with `DEPLOY_PUSHED` (the image reference), `DEPLOY_ARTIFACT` (the artifact's manifest digest)
   and `DEPLOY_PACKAGE`. `service.record` returns that event's own attempt ID
   (`deployment.py:147-150`). `DeploymentService.deploy` allocates its attempt ID later
   (`deployment.py:195`), so the push event cannot carry it. Instead the push event's ID goes into
   `deploy` as `push_event=`, and every event that deploy writes (deny, approval, allow, abort)
   records it as a `DEPLOY_PUSH_EVENT` reason. `preflight` writes no audit events
   (`deployment.py:153-158`), so it takes no ID. The `attempt_started` event has no reasons and
   stays as it is. A test checks that the events correlate. The push event is recorded whether
   the gate then allows, denies or holds for approval. A push to a loopback registry is not a
   deployment. If
   recording the push fails (`AuditUnavailable`), deploy stops before the gate. The pushed image
   stays in the loopback registry and is not rolled back.

**Tags can change** (`registry:2` allows re-tagging). Nothing trusts a tag: deploy uses the
digest reference that the push returns, and a reader that finds the artifact by tag checks the
tree hash before it uses the contents.

**Unverified:** whether kind's pinned `registry:2` (`cluster.py:38-39`) accepts and serves an OCI
1.1 manifest with `artifactType` and `subject`. Slice 0 (§13) answers this with `oras`. If
`subject` is refused, the artifact is pushed without it and found by tag. Docs claim only what
the spike shows.

### 8.3 Same digest (acceptance 2)

"Same digest" means all three of these hold for one deploy:

1. The registry holds the released image: `push_image`'s predicate accepts it. The registry's
   config digest equals `image.id` with the classic image store, or the pushed manifest digest
   equals it with the containerd image store (`promote.py:103-107`).
2. The deployment's manifest image is the digest reference that the push returned.
3. The running pod's `status.containerStatuses[].imageID` ends in `@<digest>`, where
   `<digest>` is the digest of the pushed reference. Only the part after `@` is compared, because
   the node may name a different registry host. When the pushed reference is an index (the case
   `registry_image_id` handles at `promote.py:77-85`), `<digest>` is the index digest the
   deployment named. Whether kind reports that digest or the platform image's digest is a
   slice-0 spike question; the rule is fixed from the spike's result before slice 4.

   Today `Kubernetes.pod()` and `_pod_view` (`kubernetes.py:277-318`, `kubernetes.py:498-535`) do
   not read `imageID`. M13 adds it there as a new key. `logs` and other callers ignore it, which a
   test pins. If check 3 fails after the allow event:
   - the deployment is removed through `service.remove`, which audits its own removal;
   - `service.record("deploy", "failed", …)` is recorded with `REGISTRY_MISMATCH` and both
     digests;
   - the command exits 1.

Check 3 is new. Slice 4 tests it on kind, together with:
- a second `deploy --cluster` of the same ref, which must give the same reference;
- a run after `cluster down` and `cluster up`.

### 8.4 No cluster evals

`deploy` deploys; it does not promote. `promote` keeps running the eval set against the cluster
(`promote.py:240-275`), and it may later accept `agent/<name>@X.Y.Z` (D6).

## 9. The `agent` kind and the lock

- `KINDS` gains `agent` (`registry.py:41`), and `REF` follows from it. Lane C owns
  `registry.py`, so this line lands through lane C or with the coordinator's agreement. Tests pin
  the following:
  - every existing ref (for example `runtime/adk@1.1.0`) parses as before;
  - the `REF_INVALID` text lists the new kind;
  - `scaffold` refuses `agent` (`PACKAGE_RELEASE_ONLY`);
  - `listing` and `verify` include `agent/` rows;
  - the `WRONG_FILE_KIND` message for `agent.yaml` (`commands/deploy.py:28-32`) is unchanged.
- **`agent.yaml` cannot reference an `agent/` version in M13.** No field takes one, and a ref of
  the wrong kind in an existing field is already refused (`agentfile.py:119`).
- `build` and `materialise` never copy an `agent/` version into a build context.
- `registry publish` refuses an `agent/` directory (`PACKAGE_RELEASE_ONLY`), so only `release`
  writes packages. A copy made with `cp` is still possible, and deploy refuses it at §7.2 step 3.
- **Lock entry for M19 (an M9b contract change, implemented in M19).** An `agent/` reference
  will be a lock v2 entry:

  ```json
  "agent/support-bot@1.4.0": {"sha256": "sha256:<tree>", "source": "registry",
                               "pin": {"image": "sha256:<image id>"}}
  ```

  M9b §7 refuses unknown `pin` keys and ties `pin` to source types. `pin.image` on a local
  (`registry`) entry is therefore a change to M9b's lock schema. M19 makes it, or M9b slice 1a
  allows it if it lands later. On every build, `pin.image` is checked against the package's
  `image.id`. When M19 also reaches the agent through a registry reference, it checks the digest
  rule of §8.3. An `agent/` entry forces lock v2, because v1 entries have no `pin`. There is no
  v3.
- **Remote `agent` versions defer M9b §6.4.** M9b §6.4, under owner decision D5 of 2026-11-11,
  allows `agent` from remotes. It asks M13 to define what a remote package pins, where its image
  is pulled from, and the consent before it is deployed or called. M13 answers part of this:
  - what is pinned: the tree hash, plus `pin.image`;
  - where the image comes from: an image ID is local only, so a remote package's image must be
    pulled from a registry under M9b §8.3 trust.

  Deploying or calling a remote package is refused until M19 designs consent. This needs the
  owner's sign-off (D5) and an M9b plan-delta line.
- Registry views:
  - `registry list` shows `agent/` rows.
  - `registry show agent/…@…` renders `package.json` as cleaned fields (`registry.clean`,
    `registry.py:524`), with `agent.yaml` and `agent.lock` shown as files.
  - `registry diff` works between two package versions.
  - `registry used-by` reads the store column (§7.3).

## 10. What M19 (A2A) needs

The package gives M19:

- a stable name and version (`agent/<name>@X.Y.Z`) and its tree hash, to pin;
- the image ID, plus the deployment-to-package column (§7.3), to tell whether a deployment runs
  the pinned release;
- the runtime, MCP access and model aliases, to describe the agent's reach to a caller;
- `inputs.sources`, showing where the agent's tools came from;
- `description` from `manifest.yaml`, whose source is `agent.yaml`.

Not in M13:

- Agent Card fields (skills, input and output modes). If M19 needs them in the package, it bumps
  `package_version` to 2.
- Endpoints, which belong to a deployment.
- Caller authentication.
- Consent before deploying or calling an agent from another source.

Name collisions with deployment names: a release's candidate runs as `<name>-candidate`
(`release.py:214-218`, `agentfile.py:20`). M19 must refer to packages by ref, never by deployment
name.

## 11. Relationship to `promote` and rollback

- `promote` is unchanged: it promotes the image of the current local production with its own
  release evidence and runs cluster evals (`promote.py:129-260`). It may later accept
  `agent/<name>@X.Y.Z` instead of an `agent.yaml` (D6).
- `release --rollback` stays journal-based (`release.py:559-632`). Rolling back to a package is
  `remove`, then `deploy agent/<name>@<previous>`. M13 documents that and does not change
  rollback.

## 12. Security

### 12.1 Trust model

The package is a **record**, not a credential. Deploying it is allowed because this state
directory's audit log has a release row binding this ref, this tree hash, this image ID and this
eval evidence (§7.2 step 3). The identity gate, policy and approvals then apply to the bound
manifest, as for any deploy.

What this stops:

- packages that did not come through this state directory's `release`: a planted
  `./registry/agent/…` in a cloned project, a teammate's package, a hand-made or copied
  directory;
- a package edited after release.

What it does not stop: anyone who can write the state directory can write the audit log. The hash
chain and `verify_audit` detect corruption, but a database owner can recompute them (the
CLAUDE.md claim rule). The image's content is protected only by its content-addressed ID, not by
the package. The docs never call packages signed, verified or tamper-proof. They say "deploy
refuses a package this state directory did not release, or one that changed after release".

Eval evidence holds for the release environment only (§7.4).

### 12.2 Content

- The package is assembled from an allow-list of fields (§5.3) plus two byte copies. By schema,
  `agent.yaml` holds no secrets; bindings live in the operator config (`agentfile.py:1-7`).
- It contains no eval case text, model output, judge reasons, candidate logs or customer data.
  These can hold customer content, and the package is tracked in git and pushed.
- It contains no environment values: identity, bindings, gateway, registry references.
- The template cannot express identity, secrets, policy, capabilities, requirements, target or
  image (§5.4). Binding a package to an environment therefore never widens what that
  environment's operator config allows. The gate still checks `mcp_access` and the aliases
  against the environment's policy and gateway.

### 12.3 Source labels in the lock copy

`agent.lock` `source` labels for `AGENTBREEDER_REGISTRY_PATH` roots are relative paths that can
climb out of the project (`../../…`), or absolute paths (`lock.py:61-68`). Either form can name a
home directory and a user.

In the package's copy, every label that is not `builtin`, `registry`, `remote:<name>` or a
project-relative path without `..` becomes `external:`, keeping `sha256`. `external:` is not a
path, and the packaged lock is never read as a lock input (a test pins that `Lock.load` is never
called on it). `inputs.lock_sha256` keeps the hash of the original.

A test checks every package file, and the tar pushed in §8.2, for these and fails if any appear:
absolute paths, `..` segments, `~`, `/Users/`, `/home/`, the current home directory, and the
user name.

### 12.4 Parsing

- `package.json` is read with a 64 KiB cap and the strict schema.
- `manifest.yaml` is read through `registry.read_manifest` (64 KiB, `registry.py:295-298`).
- Every printed value goes through `registry.clean`.
- Ref, name and version are parsed with `Ref.parse`, whose version check rejects `1.2`, `1..2`
  and leading zeros (`registry.py:42-44`, `registry.py:88-89`).
- Agent names already fit the OCI repository grammar (`agentfile.py:20`: lowercase, digits,
  hyphen, at most 53 characters). A test pins this.

### 12.5 Registry push

As §8.1: loopback IP literals only, no proxy, no credentials, and the digest re-read and compared.
A push sends the image (as `promote` does today) and the package, which by §12.2 has no secrets.
The tar is leak-scanned before push.

A gate-denied or approval-pending `deploy --cluster` has still written the image and the
artifact to the loopback registry (§8.2 step 3). That is accepted, because the registry is local
and the image is the released one, but it is stated so that no one reads a deny as "nothing was
written".

### 12.6 Supply chain

Tool code in the image came from the registries the lock names, and M9b's trust and pin rules
cover remote ones. The package adds no new fetch.

## 13. Delivery slices

Each slice gets:

- tests first: pytest unit tests plus CLI end-to-end tests run as a subprocess, with real Docker
  behind `AGENTBREEDER_DOCKER_TEST=1`;
- `docs/` and CHANGELOG [Unreleased] ("on `main`, not in a published preview");
- the gate, a different-model review, and owner approval.

0. **Spike** (no PR to `main`; the result is recorded in this spec before slice 4). Push an OCI
   1.1 artifact with `artifactType` and `subject` to kind's pinned `registry:2` using `oras`.
   Read it back by tag and, if supported, by referrers. Also read a pod's `imageID` for a
   digest-referenced image. This decides the open points in §8.2 and §8.3. It needs the
   coordinator's `cluster up` grant.
1. **Format and kind.**
   - `agent` in `KINDS` (with lane C).
   - `package.py`: schema, assemble, load, label redaction.
   - `registry list|show|diff|verify` for `agent/`; `publish` and `scaffold` refusals.
   - `build._manifest` split into `template` and `bind`, with the byte-identical Northwind test.
2. **`release` writes the package** (local target).
   - Version resolution after build; `RELEASE_PACKAGE` on `started`; publish before the journal;
     the three release-row reasons; `link_package`.
   - Cleanup on failure; G-F3 and pre-M13 rows; the crash path; `--json`.
   - A Northwind release writes `agent/northwind@0.1.0`.
3. **`deploy agent/…` local.**
   - Ref parsing, `--project`, resolve, hash, evidence, bind, image check, gate.
   - Store v4 `package` column, `DEPLOY_PACKAGE`, `status --json`, `registry used-by`.
   - Each refusal in §15 tested on its own.
4. **kind.**
   - `deploy agent/… --cluster`: image push, artifact push, the three checks of §8.3, and a
     second deploy and a `cluster down`/`up` run.
   - Acceptance 2 on kind.
5. **Verification** from a wheel built from `main` in a clean sandbox, recorded in
   `docs/superpowers/verification/M13.md`:
   - the acceptance script and timings;
   - teardown to zero;
   - a leak scan over the package, the artifact pulled back from the registry, the audit rows and
     `status --json`, for paths, user names, eval case text and token-like strings.

   It needs the coordinator's `cluster up` grant and enough Docker disk.

Sequencing:

- Slices 1–3 need no M9b code.
- Slice 4 needs an OCI push client. If M9b slice 5 (`sources/oci.py`) has not landed, M13 writes
  the push half in `sources/oci.py` with lane C's agreement, and M9b's pull half reuses it (M9b
  §15).
- `inputs.sources` stays `{}` until M9b slice 1a writes v2 locks.

## 14. Owner decisions (open)

- **D1 Version source.**
  - Recommended: `release --version X.Y.Z`, defaulting to the next patch. While an image is
    production, releasing it again gives its existing package.
  - Alternative: a `version:` field in `agent.yaml`. It is reviewable in the diff, but it changes
    the authoring schema and needs a bump commit per release.
- **D2 Location.**
  - Recommended: the project's `./registry`, tracked in git.
  - Alternative: the private state directory. Nothing to commit, but teammates and `registry`
    views cannot see it.
- **D3 Source labels in the lock copy.**
  - Recommended: replace them with `external:` (§12.3).
  - Alternative: refuse to release when the lock names a path outside the project.
- **D5 Packages from elsewhere.**
  - Recommended: refused in M13, deferring M9b §6.4 (owner D5, 2026-11-11) to M19 together with
    consent and image-registry trust.
  - Alternative: allow them with an explicit `--trust-package`, after a re-run of the eval set.
- **D6 Cluster evals.**
  - Recommended: `deploy` runs none; `promote` keeps them and may accept a package ref later.
  - Alternative: `deploy --cluster` of a package runs its eval set, as `promote` does.

(r1's D4, the artifact tree, is decided in r2: the package holds no registry references, so the
artifact and the published tree are the same.)

## 15. Messages and error codes

All exit 2 unless noted. Every message ends with a `Next:` line from the shared hint helper with
the launcher prefix (guided path §4). `--json` errors carry `code`.

| Code | When | Message (shape) |
|---|---|---|
| `VERSION_EXISTS` | release, version taken (directory or audit) | `agent/support-bot@1.4.0 is taken.` (+ "never released (an interrupted release); delete it to reuse the version" when it has no release row) Next: `release agent.yaml --version 1.4.1` |
| `PACKAGE_EXISTS` (note, exit 0) | release, this image is already packaged | `This image was released as agent/support-bot@1.4.0.` |
| `PACKAGE_INVALID` | schema, size, `package_version`, name/version mismatch | which field; "written by a newer AgentBreeder? upgrade" |
| `REF_NOT_DEPLOYABLE` | `deploy tool/x@1.0.0` | `only agent/ versions can be deployed by ref` |
| `PACKAGE_NOT_HERE` | found only outside `./registry` | `agent/… is not in this project's ./registry (found in <root or source>); deploy works only on packages released from this project.` |
| `PACKAGE_UNRELEASED_HERE` | no release row in this state | `agent/support-bot@1.4.0 was not released from this state directory (--state-dir / --stack?). Packages from elsewhere cannot be deployed yet.` Next: `release agent.yaml` |
| `PACKAGE_CHANGED` | tree hash differs from the release row | `agent/support-bot@1.4.0 changed after it was released (sha256:ab12… → sha256:9f00…). A released package must not change: restore it from git, or release a new version.` |
| `PACKAGE_OTHER_PROJECT` | release row from another project (shared state) | `agent/support-bot@1.4.0 in this state was released from a different project with the same agent name.` Next: deploy from that project, or use `--stack` |
| `PACKAGE_INPUTS_CHANGED` (exit 1) | release, `agent.yaml`/`agent.lock` edited during the evals | `agent.yaml changed while the evals ran; nothing was published and production is unchanged.` Next: `release agent.yaml` |
| `PACKAGE_PUBLISH_MISMATCH` (exit 1) | release, the hash from `publish` differs from the staged one | `the published copy of agent/… differs from the staged one; it was removed and production is unchanged.` |
| `PACKAGE_KEPT_UNVERIFIED` (exit 1) | release, audit unavailable after publish; printed alongside the underlying `AUDIT_WRITE_FAILED` (`deployment.py:38-54`), which keeps its code | `agent/… was published but the audit log could not be confirmed; it is kept.` Next: `audit verify` |
| `AUDIT_CORRUPT` (exit 1) | deploy by ref, `verify_audit` fails | `the audit log failed verification; packages cannot be checked.` Next: `audit verify` |
| `PACKAGE_EVIDENCE_MISMATCH`, `PACKAGE_AMBIGUOUS` | §7.2 step 3 | which field differed; "the audit log may be corrupt" with `audit verify` |
| `PACKAGE_IMAGE_MISSING` (exit 1) | local image gone | `image sha256:6f1c… of agent/support-bot@1.4.0 is not in this Docker engine (pruned?). Released images cannot be rebuilt bit for bit.` Next: `release agent.yaml` |
| `PACKAGE_RELEASE_ONLY` | `registry publish` or `scaffold` for `agent` | `agent/ versions are written by release` |
| `REGISTRY_NOT_LOCAL`, `REGISTRY_MISMATCH`, `REGISTRY_UNREADABLE` (exit 1) | §8 | as `promote`, plus the pod `imageID` case |

## 16. Cross-repo sync and files

- **OSS** (lane A unless noted):
  - new `package.py`;
  - `release.py` and `commands/release.py` (`--version`, the package, row reasons);
  - `build.py` (`template`/`bind`);
  - `commands/deploy.py` (ref path, `--project`);
  - `deployment.py` (`deploy(package=, push_event=)`, `DEPLOY_*` reasons, the `deploy/pushed`
    event, pod `imageID` check);
  - `kubernetes.py` (`imageID` in `pod()` / `_pod_view`);
  - `evidence.py` (`require_package`);
  - `store.py` (schema v4, `create`/`update_binding`/`link_package`) and `commands/migrate.py`
    (v3 → v4);
  - `commands/status.py` (`package` in `--json`);
  - **lane C:** `commands/registry.py` (`list|show|diff|used-by|verify` for `agent/`);
  - `promote.py` (push helper shared, no behaviour change);
  - **lane C:** `registry.py` (`KINDS`, refusals, `show` rendering), `registry_deployed.py`
    (schema 4, `used-by` column), and `sources/oci.py` push half (if M9b slice 5 has not landed);
  - docs: `docs/registry.md` (the `agent` kind, package format, local-target behaviour, claims
    from §12.1), `docs/agent-yaml.md` (Eval and release: packages), `docs/local-deployment.md`
    (deploy by ref), CHANGELOG.
  - No change to the `agent.yaml` JSON Schema unless D1 picks a `version:` field. No change to the
    `agent.lock` v1 or v2 shape in M13; the `pin.image` entry is M19's, as an M9b contract change
    (§9).
- **Cloud (`agentbreeder-cloud`):** a grep on 2026-11-13 for `lock_version`, `agent.lock`,
  `agentbreeder release`, `build.json` and `KINDS` found nothing, so no code change is needed.
  Its `CLAUDE.md` should record the `agent` kind and that `package.json` is strict
  (`package_version` 1, unknown keys refused), for any future reader.
- **Website:** nothing until release R (#836). Then `registry.mdx` and what-ships, only for what
  the M13 verification ran.
- **Plan delta** (console-epic plan, at M13 start): M13 touches `registry.py`,
  `registry_deployed.py` and possibly `sources/oci.py` (lane C), and `store.py` schema v4. M9b's
  plan delta gains "remote `agent` versions deferred to M19 (M13 D5)" and "`pin.image` for M19".

## 17. Review log

- r1 (2026-11-13): draft from #763, #762, plan §M13, the M9b spec and the code on `main` at
  90f117c.
- r1 reviews (Claude Sonnet, read-only, against the code):
  - architecture: 18 findings, CHANGES REQUESTED;
  - `/security`: 14 findings, CHANGES REQUESTED.

  Spot-checked before folding:
  - `_source_label` returns `os.path.relpath` (`lock.py:61-68`);
  - `Ref.parse` already enforces `VERSION` (`registry.py:88-89`);
  - `deploy()` takes no reasons (`deployment.py:178`);
  - `registry_deployed._STATE_SCHEMAS = (2, 3)`; `store.SCHEMA_VERSION = 3`;
  - reasons are capped at 50 (`deployment.py:150`);
  - the G-F3 row at `release.py:314` sits outside `defer_interrupt`;
  - `publish` refuses an existing target (`registry.py:358-361`);
  - several r1 line citations were wrong and have been corrected.
- r2 (2026-11-13) folds them:
  - **Artifact self-reference / environment neutrality (arch 1, 4; sec 9):** `pushed[]` is
    removed from the package; pushes are audit events at `deploy --cluster`; the artifact is the
    published tree; D4 is decided.
  - **Crash recovery (arch 2, 11; sec 1):** publish before production; check the hash `publish`
    returns; the release row carries the package; cleanup on failure; never adopt an orphan;
    G-F3 inside `defer_interrupt`; `released.at` fixed early.
  - **Evidence binding (sec 2, 4):** the row binds ref, hash, image, threshold and eval set; all
    rows are checked for consistency; `verify_audit`; name/version consistency; a state-directory
    hint in the message.
  - **Deployment link (arch 3):** a `package=` parameter, store v4 column, `DEPLOY_PACKAGE`,
    `used-by` from the column; `deployment.py`/`store.py` listed.
  - **Same digest (arch 5, 6; sec 5):** `push_image`'s predicate; the pod `imageID` check; push
    on every cluster deploy; loopback IP literals only; `localhost` in `promote` is a follow-up.
  - **Version timing (arch 7):** after build; audit rows count as taken.
  - **CLI (arch 8):** ref versus path precedence; `--project`; resolution in `action`.
  - **M9b consistency (arch 9; sec 12):** `pin.image` stated as an M9b contract change; the M9b
    §6.4 deferral made explicit for D5; resolve from `./registry` only.
  - **Redaction (arch 10; sec 8):** non-path `external:`; `..` labels covered; a broader leak test.
  - **Template (sec 7):** `bind` fields and negative tests.
  - **Wording (sec 3, 13):** "AgentBreeder never overwrites"; trust text narrowed.
  - **Tags (arch 17; sec 6):** stated as mutable; digests only.
  - **Smaller items:** the 50-reason cap test (arch 13; sec 13); M19 notes (arch 14); `KINDS`
    tests (arch 15); sequencing (arch 16); leak-scan scope (sec 10, 14); citations (arch 18).
- r2 re-review (Claude Sonnet, both lenses, read-only): 14 findings (1 High, 7 Medium, 6 Low),
  CHANGES REQUESTED. Most r1 folds were confirmed against the code. Partly resolved: crash
  recovery, version timing, the deployment link, same digest. Spot-checked before folding:
  - the journal is one file per agent, rewritten each release (`release.py:94-95`, `:344`);
  - `release/started` is recorded before the candidate deploy (`release.py:210-211`);
  - `pod()` returns no `imageID` (`kubernetes.py:277-318`);
  - `Kubernetes.image_present` returns true (`kubernetes.py:214-215`);
  - the store version lists are at `store.py:136` and `:213`;
  - the registry field's pattern has no brackets (`operator_config.py:106`).
- r3 (2026-11-13) folds them:
  - versions are reserved by `RELEASE_PACKAGE` on the `release/started` row; the journal claim is
    dropped; evidence reads `promoted` rows only (1);
  - publish after `PRODUCTION_CHANGED`; cleanup in a `finally` on every exit; the directory is
    kept on `AuditUnavailable`/`AuditIncomplete` (2);
  - `agent.yaml`/`agent.lock` re-hashed against `build.json` before publishing,
    `PACKAGE_INPUTS_CHANGED` (3);
  - `PACKAGE_EXISTS` goes through `evidence.released`, whose `require_package` covers pre-M13 rows
    (4, 5);
  - release passes `package=`; the idempotent path back-fills a NULL link (6);
  - store v4 migration and compatibility (7);
  - `imageID` added to `pod()`, compared by digest suffix, index case referred to the spike, and
    the audit on failure (8);
  - the image is resolved before bind, with a Docker presence check before a push (9);
  - the threshold compared as a float, and `RELEASE_EVAL` checked (10);
  - `RELEASE_PROJECT` hash and `PACKAGE_OTHER_PROJECT` for a shared state (11);
  - `127.0.0.1` only, with a parse test (12);
  - version tests (13);
  - the field count in `bind` (14).
- r3 re-review (Claude Sonnet, both lenses, read-only): no High, 6 Medium, 8 Low; CHANGES
  REQUESTED. Nine r2 folds were confirmed resolved. Spot-checked before folding:
  - an equal-digest deploy goes on through `update_binding` and redeploys
    (`deployment.py:336-352`);
  - `store.py:136` and `:213` list `(0, 1, 2, SCHEMA_VERSION)` and `(1, 2)`;
  - the journal is written after `PRODUCTION_CHANGED`, with the comment at `release.py:328-329`.
- r4 (2026-11-13) folds them:
  - §6 order: publish before the journal, then the existing order (M1);
  - release links production after the `promoted` row with `link_package`, never through the
    deploy, so there are no dangling links; `used-by` lists active deployments only (M2, M6);
  - step 7 rewritten for the equal-digest flow, with `update_binding(package=)` (M3);
  - the store lists gain 3, with v3 tests (M4);
  - version resolved before preflight; the per-(project, state) guarantee; `PACKAGE_EXISTS` only
    while production (M5);
  - `imageID` added as a new key (M6);
  - Lows: `PACKAGE_PUBLISH_MISMATCH`, `PACKAGE_KEPT_UNVERIFIED` and `AUDIT_CORRUPT` added; cleanup
    under `defer_interrupt`; three G-F3 reasons; the cap test covers the release row; the
    registry host parsed out of a path; pre-M13 rows skipped, not raised on.
- r4 re-review (Claude Sonnet, read-only): no High, 2 Medium, 7 Low; CHANGES REQUESTED, with the
  next pass expected to approve. All r3 folds confirmed except M4. Spot-checked: the writable
  `Store.__init__` migrates v0–v2 in place (`store.py:133-163`).
- r5 (2026-11-13) folds them:
  - v3 migrates in place on a writable open; read-only opens raise; the test line is corrected
    (Medium 1);
  - `PACKAGE_EXISTS` requires the directory intact (Medium 2);
  - Lows: the push is its own audit event before the gate; release's `link_package` may replace
    an older link with an audited event; the slice 2 text; files in §16; `RELEASE_PROJECT` on a
    moved project or worktree; `PACKAGE_KEPT_UNVERIFIED` beside `AUDIT_WRITE_FAILED`; the cost of
    `verify_audit`.
- r5 re-review (Claude Sonnet, read-only): 1 Medium, 5 Low; CHANGES REQUESTED, approval expected
  after the Medium. Medium 2 was confirmed resolved, and the `link_package` and push orders were
  confirmed coherent. Spot-checked: `status` opens the store writable
  (`commands/status.py:24`); `list` and `logs` open it read-only.
- r6 (2026-11-13) folds them:
  - the commands that migrate and the commands that raise are named correctly, with the tests
    matching; the CHANGELOG warning includes `status` (Medium);
  - every version below 4 gets the column;
  - "back-fill" dropped from §16; the `PACKAGE_EXISTS` path repairs a NULL link;
  - a push before a deny is stated in §12.5; an audit failure after a push stops the deploy;
  - push correlation (replaced in r7);
  - the `release.py:367-368` citation.
- r6 re-review (Claude Sonnet, read-only): 1 Medium, 3 Low; CHANGES REQUESTED. Every other r6 fold
  was confirmed against the code. The Medium: `deploy()` allocates its attempt ID itself
  (`deployment.py:195`), after the push, so the push event cannot carry it.
- r7 (2026-11-13) folds them:
  - the push event's own ID is passed into `deploy` (`preflight` does not audit) as `push_event=` and recorded
    as `DEPLOY_PUSH_EVENT`, with a correlation test;
  - "any writable command, for example …";
  - `migrate`'s wording.
- r7 re-review (Claude Sonnet, read-only): **APPROVED**, no High or Medium. Four Lows applied:
  `push_event=` named in §16 and §7.3; `attempt_started` unchanged; §8.2 wording. Converged.
  Open for implementation: the slice-0 spike (§13) before slice 4.
