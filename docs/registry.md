# Registries and `agent.lock` (preview)

> Part of the `agent.yaml` build work (#725), preview. Registries, the lock file and
> `agentbreeder build` (see `agent-yaml.md`) work today; verified with `src/agentbreeder/examples/northwind`.

A registry is a directory of **immutable, versioned building blocks**: prompts, tools,
knowledge sources, eval sets, runtimes and local backing `service`s (preview, see `services.md`). You
reference each one as `kind/name@X.Y.Z`, for example `tool/orders@1.0.0`. Only exact versions
are accepted.

```
registry/
  prompt/support/1.0.0/manifest.yaml   # kind, name, version, description
  prompt/support/1.0.0/prompt.md
  tool/orders/1.0.0/manifest.yaml
  tool/orders/1.0.0/src/…
```

## Search roots

AgentBreeder looks for a ref in these places, in order:

1. the project's `./registry`;
2. each directory in `AGENTBREEDER_REGISTRY_PATH`;
3. the versions shipped with AgentBreeder.

If a ref exists in two roots with different content, that is an error (`REF_CONFLICT`). One root
never silently shadows another.

## Commands

```
agentbreeder registry new tool orders --dir .         # scaffold ./tool-orders to edit
agentbreeder registry publish ./tool-orders [--to registry] [--dry-run | --expect-digest SHA256] [--json]
#   prints ref, sha256, path
agentbreeder registry import ../shared/prompt-x [--as NAME] [--version X.Y.Z] [--project DIR] [--json]
agentbreeder registry list [tool/] [--json]
agentbreeder registry verify [--json]                 # exit 2 if any version is unsafe or inconsistent
agentbreeder registry show prompt/support@1.0.0 [--project DIR] [--json]
agentbreeder registry diff prompt/support@1.0.0 prompt/support@1.0.1 [--context N] [--json]
agentbreeder registry used-by tool/orders@1.0.0 [--project DIR ...] [--state-dir DIR] [--json]
```

`list`, `verify`, `show` and `diff` search the roots of `--project` (default `.`).

Nothing below a search root may be a symlink (the root itself may be). A symlinked, or dangling,
`<root>/<kind>` or `<root>/<kind>/<name>` directory makes every ref under it fail to resolve
(`ENTRY_UNSAFE`) in `build`, `release`, `services up`, `show` and `diff`, **even when a later
root has the version**: resolution fails closed rather than skipping to the next root. `verify`
reports each such directory as a problem, and `list` still lists the versions under it with
`"symlinked": true` (text output: `(symlinked directory: refused)`).

## Browsing versions (on `main`, not in a published preview)

`show`, `diff` and `used-by` only read. They are what the local console's registry pages will
call, so their `--json` output is the contract described here. Paths in it (`root`, `path`,
`project`, `state_dir`) are absolute.

**What every read refuses or cleans.** A version goes through the same checks as `verify`
(`REF_CONFLICT`, symlinks, hardlinks, devices, `MAX_FILES`/`MAX_BYTES`, the shipped content
manifest), and nothing below a search root may be a symlink: a symlinked `<root>/<kind>`,
`<root>/<kind>/<name>` or version directory is `ENTRY_UNSAFE`. Each file is opened without
following symlinks and must still be the file that was checked, with the size it had; an
unreadable entry (permissions, a file removed mid-read) is `ENTRY_UNSAFE`, never a partial
listing. Control characters (except tab and newline in content), Unicode bidi controls, line and
paragraph separators and lone surrogates are removed from file names, manifest values, content and
error messages before anything is printed; names also lose zero-width, word-joiner, BOM and tag
characters. So a version cannot rewrite your terminal or disguise a file name; the file is marked
`stripped`.

**`registry show REF`** prints one version: its hash, root, description, file list and the text
of its text files. Output is capped: at most 256 KiB of text per file and 2 MiB per version
(later files are listed but not shown). Binary files (a NUL byte or invalid UTF-8) are listed, not
shown. The hash and the manifest come from the same read as the content, so they describe exactly
the bytes shown.

`--json` prints:

```json
{
  "ref": "prompt/support@1.0.0", "kind": "prompt", "name": "support", "version": "1.0.0",
  "description": "…", "root": "/abs/project/registry", "root_kind": "project",
  "path": "/abs/project/registry/prompt/support/1.0.0", "sha256": "sha256:…",
  "manifest": {"kind": "prompt", "name": "support", "version": "1.0.0", "description": "…"},
  "omitted": false,
  "files": [
    {"path": "docs", "type": "dir", "stripped": false},
    {"path": "prompt.md", "type": "file", "size": 9, "executable": false, "sha256": "sha256:…",
     "binary": false, "truncated": false, "omitted": false, "text": "Be kind.\n", "stripped": false}
  ]
}
```

`root_kind` is `project` (`./registry`), `path` (`AGENTBREEDER_REGISTRY_PATH`) or `builtin`.
`sha256` is the version hash that `agent.lock` records; a file's `sha256` is of its bytes alone.
`text` is `null` for binary files and for files past the version's cap (`omitted: true`);
`truncated: true` means only the first 256 KiB are in `text`.

**`registry diff FROM TO`** shows what changes from the first version to the second (the order
matters) of one `kind/name` (two different names are `REF_INVALID`), file by file in path order,
as a unified diff with `--context N` lines around each change (default 3, at most 1000). Both
versions are read exactly as `show` reads them. Text changes are a valid unified diff (headers
name `FROM/path` and `TO/path`, `/dev/null` for an added or removed file). It is computed in
Python: the common start and end are skipped, lines that occur once on each side anchor the rest
(as `git diff --patience` does), and only what lies between anchors goes to difflib. So where
lines repeat, hunk placement can differ from what `diff(1)` prints (and `diff(1)`
implementations disagree on how an empty side is numbered at context 0). `Only in REF: dir/`
reports an empty directory. A change that cannot be shown as text is still reported: `Binary
files … differ`; `… differ (too large to show)` past the 256 KiB cap, or once the diff's budget
is spent (one budget for the whole diff: line comparisons, about two seconds, and 50,000 hunk
lines of output, so every later changed file is reported this way); `old mode`/`new mode` for the
executable bit;
and `… differ only in removed control or bidi characters` when the only difference is in
characters that are stripped for display. Line endings are compared after `\r` is stripped. The
exit status is 0 whether or not the versions differ, 2 on an error.

`--json` prints:

```json
{
  "from": {"ref": "prompt/support@1.0.0", "sha256": "sha256:…", "root": "/abs/…", "root_kind": "project"},
  "to":   {"ref": "prompt/support@1.0.1", "sha256": "sha256:…", "root": "/abs/…", "root_kind": "project"},
  "context": 3,
  "identical": false,
  "files": [
    {"path": "empty", "status": "added", "type": "dir", "stripped": false},
    {"path": "prompt.md", "status": "modified", "type": "file",
     "from_sha256": "sha256:…", "to_sha256": "sha256:…",
     "executable": {"from": false, "to": false},
     "binary": false, "truncated": false, "omitted": false, "too_large": false, "stripped": false,
     "hunks": [{"from_start": 1, "from_count": 2, "to_start": 1, "to_count": 2, "lines": [
       {"op": " ", "text": "Be kind."},
       {"op": "-", "text": "Be brief."},
       {"op": "+", "text": "Be brief", "no_newline": true}
     ]}]}
  ]
}
```

Only changed entries are listed. `status` is `added`, `removed` or `modified`; a path that
changes between file and directory appears as `removed` then `added`. On an added or removed file
the missing side's `from_sha256`/`to_sha256` and `executable` value is `null`. `hunks` is empty
when the change is not shown as text (see the flags; `too_large` means the diff's budget was
spent). `no_newline` appears only on a line with no newline at the end of the file.

**`registry used-by REF --project DIR [DIR ...]`** reports, for each project (default `.`, each
directory once), separately:

- **declared**: each field of `agent.yaml` or `stack.yaml` that `build` or `services up` reads a
  ref from (`runtime`, `prompt.ref`, `knowledge[i].ref`, `tools[i].ref`, `mcp[i].ref`,
  `evals.ref`; `services.NAME.ref`, `mcp.NAME.ref`) whose value is exactly the ref, never free
  text such as a description;
- **locked**: the `agent.lock` or `stack.lock` entry for the ref (hash and source root);
- **deployed**: recorded deployments of the project's agent in state `ready` whose image is the
  image in the project's build record, `deploy/<agent>.build.json` (written by `build` and
  `release`), when that record is for the current `agent.yaml` and `agent.lock` (their hashes
  match) and lists the ref;
- **recorded_not_running**: the same match in another state (`pending`, `starting`, `failed`,
  `removing`);
- **deployed_undetermined**: a deployment of the agent that cannot be matched, with the `reason`:
  no or unreadable build record, the record is older than `agent.yaml`/`agent.lock`, its image is
  not the record's (rebuilt since, built with another `--output`, or a cluster deployment named by
  registry digest), the record does not list the ref, or its recorded manifest is over the
  256 KiB cap (so its image is unknown). Never guessed.

`<agent>-candidate` deployments (release's candidates) count only when they run the record's
image, so an unrelated agent of that name is never reported. Only agent images are checked: a
`service` (or other stack) ref is declared and locked, never "deployed". For a `service` ref
the three deployment lists are empty and no deploy state is read (on `main`, not in a published
preview).

Deployments are read from `--state-dir` (repeatable; default the local state, as `status`) and,
for a project with a `stack.yaml`, from every environment `services up` wrote for it (what
`--stack DIR` uses), each directory once. State is only read: the directory and database must be
private and yours (as the store requires), the database must be in rollback-journal mode (a WAL
database is refused, since reading it would create files), it is opened with SQLite `mode=ro`,
`deployments` must be a table, query time and text columns are capped, and nothing is created or
migrated (a state from before schema 2 is reported; any read-write command migrates it). Of more
than 1000 deployments in one state directory, the newest 1000 are read and `STATE_TRUNCATED` is
reported. Project files are read only as regular, non-symlink, size-capped files (`agent.yaml` and
`stack.yaml` 64 KiB, what `build` and `services up` accept; locks and the build record 1 MiB);
lock hashes must be `sha256:` and 64 hex digits. The
state shown is the last recorded observation, not a live check (`status` and `reconcile`
re-check). The ref does not have to exist in a registry. Problems with one file or state
directory are reported in `errors` and the rest is still shown; the exit status is then 2.

`--json` prints:

```json
{
  "ref": "tool/orders@1.0.0",
  "projects": [{
    "project": "/abs/examples/northwind", "agent": "northwind-support", "stack": "northwind",
    "declared": [{"file": "agent.yaml", "name": "northwind-support", "at": "tools[2].ref"}],
    "locked": [{"file": "agent.lock", "sha256": "sha256:…", "source": "registry"}],
    "deployed": [{"deployment": "…", "name": "northwind-support", "state": "ready",
                  "target": "local", "environment": "dev", "endpoint": "http://127.0.0.1:…",
                  "image": "sha256:…", "state_dir": "/abs/…",
                  "build_record": "deploy/northwind-support.build.json"}],
    "recorded_not_running": [],
    "deployed_undetermined": [],
    "errors": [{"file": "stack.lock", "error": "[LOCK_INVALID] …"}]
  }]
}
```

`agent` and `stack` are the names `agent.yaml` (`kind: agent`) and `stack.yaml` declare, `null`
otherwise. `recorded_not_running` entries have the same fields as `deployed`;
`deployed_undetermined` entries have `reason` instead of `build_record`.

## Importing, and writing exactly what was reviewed (on `main`, not in a published preview)

**`registry import PATH [--as NAME] [--version X.Y.Z] [--project DIR]`** copies a version
directory from anywhere (another project, a shared folder, a download) into the project's
`./registry` as an immutable version. `--as` and `--version` import it under another name or
version: only the top-level `name:` and `version:` lines of the staged copy's `manifest.yaml` are
replaced (every other byte, comments included, is kept). A manifest that does not write them once
each as plain top-level lines (a flow mapping, a value on the next line) is refused
(`MANIFEST_INVALID`) rather than re-serialised. Without them the manifest is copied byte for byte.
It runs `publish`'s checks (below) and one more: if another search root (an
`AGENTBREEDER_REGISTRY_PATH` entry or the built-in registry) already has that ref with different
content, the import is refused (`REF_CONFLICT`), so the imported version always resolves.

Both `publish` and `import` copy the source **once** into a private staging directory, and every
check runs on that copy: the entry checks, the manifest, the hash and the rename into place.
What is written is the staged copy, so a file changed during the copy cannot slip past the checks.
Errors name the source, never the staging directory. A write stages inside the target root
(`<root>/.import-*`, removed afterwards); a process killed mid-write can leave one behind, which
`list`, `verify` and resolution ignore and which is safe to delete.

**Preview, then write the reviewed bytes (S-04).** `--dry-run` stages and checks the version,
writes nothing, and prints its digest; with `--json` it also lists the staged files in the shape
`show` uses (`files[]`, text capped and cleaned the same way), including a rewritten manifest.
Passing that digest back as `--expect-digest sha256:…` writes the version only if the new staged
copy hashes the same; otherwise it fails with `DIGEST_MISMATCH` and writes nothing. A source
edited after the preview, a different `--as`/`--version`, or a changed executable bit all change
the digest. `--dry-run` and `--expect-digest` cannot be combined.

```
agentbreeder registry import ../shared/tool-orders --version 1.1.0 --dry-run --json
agentbreeder registry import ../shared/tool-orders --version 1.1.0 --expect-digest sha256:…
```

`import --json` prints (indented, as `show`; `publish --json` prints the same object on one line):

```json
{
  "ref": "tool/orders@1.1.0", "kind": "tool", "name": "orders", "version": "1.1.0",
  "source_ref": "tool/orders@1.0.0", "sha256": "sha256:…",
  "source": "/abs/shared/tool-orders", "root": "/abs/project/registry", "root_kind": "project",
  "path": "/abs/project/registry/tool/orders/1.1.0",
  "dry_run": true, "runs_code": true, "files": [ … ]
}
```

`source_ref` is the ref the source's manifest declares; `files` is present only for `--dry-run`.
Paths are absolute. `runs_code` is true for `tool`, `service`, `eval`, `mcp` and `runtime`: these
versions are code that runs inside agents (or is the agent server) with their privileges. `import`
prints a note to stderr saying so, or, for `prompt` and `knowledge`, that the text steers the
agent. The note is a reminder, not a security boundary: an imported version takes effect only when
an `agent.yaml` references it and `release` passes. Text output is `REF SHA256 PATH` (with
`(from SOURCE_REF)` after a rename), as `publish` prints; a dry run prefixes `Would import` and
prints the `--expect-digest` to pass. Errors are `[CODE] message` on stderr, exit 2.

## Publishing and integrity

- **Versions are immutable.** `publish` and `import` refuse to overwrite an existing version
  (`VERSION_EXISTS`), also when another command published it between the check and the final
  rename. They refuse a symlinked `<root>/<kind>` or `<root>/<kind>/<name>` directory
  (`ENTRY_UNSAFE`), so nothing is written outside the root; `import` also refuses when another
  search root has a symlinked directory on the ref's path, since the ref would not resolve. Any
  other write failure (permissions, a full disk) is `ENTRY_UNSAFE` naming the target.
  A source directory that contains the target root (for example `publish . --to registry` from a
  project) is refused (`ENTRY_UNSAFE`): publish a version directory, not the project.
- **What a version may contain.** Only regular files and directories. Symlinks, hardlinks,
  FIFOs and devices are rejected (`ENTRY_UNSAFE`), as are `..` paths and non-UTF-8 names.
- **Versions shipped with AgentBreeder** (the built-in registry and the shipped examples) may be
  hardlinked, because installers such as uv hardlink installed files from their cache. They are
  compared with the package's `_content.json` instead: same bytes, same executable bit, no extra
  or missing files (`CONTENT_MISMATCH`; `CONTENT_MANIFEST_MISSING` if the manifest is unreadable).
  This is a corruption check, not provenance: anyone who can write the install can rewrite the
  manifest too. `publish` refuses these trees (`REGISTRY_READ_ONLY`).
- **What the hash covers.** Each version is hashed (`sha256:…`) over its file paths, contents and
  executable bits. At build time the version is copied first and the copy is hashed, so the hash
  matches exactly what is built.

## `agent.lock`

The lock records the hash and source root of every ref an agent uses. It behaves like cargo's
lock file:

| Mode | Behaviour |
|---|---|
| plain | verifies the entries it already has, adds new refs, prunes refs the agent no longer uses |
| `--update [ref]` | accepts new hashes for path refs only (`{path: ./prompts/dev.md}`) while you iterate; a published version with a changed hash is always `HASH_MISMATCH` |
| `--locked` (CI) | refuses any change (`LOCK_STALE`) and any path ref (`REF_UNPINNED`), and loads each ref only from its locked source |

A changed byte in a locked version fails with `HASH_MISMATCH`. Hashes detect tampering after a
version is first locked; they don't vouch for that first lock.
