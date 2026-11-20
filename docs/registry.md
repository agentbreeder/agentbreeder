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
3. each git source `agent.yaml` declares under `registries:` (on `main`, see
   [Remote registry sources](#remote-registry-sources-git-on-main-not-in-a-published-preview));
4. the versions shipped with AgentBreeder.

If a ref exists in two roots with different content, that is an error (`REF_CONFLICT`). One root
never silently shadows another.

## Commands

```
agentbreeder registry new tool orders --dir .         # scaffold ./tool-orders to edit
agentbreeder registry new skill refund-policy [--scripts] --dir .   # ./skill-refund-policy (--scripts: a script skill)
agentbreeder registry publish ./tool-orders [--to registry] [--dry-run | --expect-digest SHA256] [--json]
#   prints ref, sha256, path
agentbreeder registry import ../shared/prompt-x [--as NAME] [--version X.Y.Z] [--project DIR] [--json]
agentbreeder registry fork service/litellm@1.0.0 [--as NAME] [--version X.Y.Z] [--project DIR] [--json]
agentbreeder registry edit prompt/support@1.0.0 --set prompt.md FILE [--remove PATH] [--bump patch|minor|major | --version X.Y.Z] [--project DIR] [--dry-run | --expect-digest SHA256] [--json]
agentbreeder agent set-ref prompt/support@1.0.0 prompt/support@1.0.1 [--project DIR] [--dry-run | --expect-base SHA256] [--json]
agentbreeder registry list [tool/] [--json]
agentbreeder registry runtimes [--project DIR] [--json]   # runtime versions and what each supports
agentbreeder registry verify [--json]                 # exit 2 if any version is unsafe or inconsistent
agentbreeder registry show prompt/support@1.0.0 [--project DIR] [--json]
agentbreeder registry diff prompt/support@1.0.0 prompt/support@1.0.1 [--context N] [--json]
agentbreeder registry used-by tool/orders@1.0.0 [--project DIR ...] [--state-dir DIR] [--json]
```

`list`, `runtimes`, `verify`, `show` and `diff` search the roots of `--project` (default `.`).

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

A `tool/` version also has `functions` and `functions_error` (on `main`, not in a published
preview):

```json
"functions": [
  {"name": "lookup_orders", "module": "orders", "function": "lookup_orders",
   "description": "Look up the current customer's Northwind orders: …",
   "parameters": {"type": "object", "properties": {"order_id": {"type": "string", …}, …}},
   "signature": "lookup_orders(order_id: str='', status: str='') -> str",
   "signature_error": null}
],
"functions_error": null
```

`name`, `module`, `function`, `description` and `parameters` come from `tool.yaml`
(`parameters` is what the model sees); they are untrusted text, cleaned like every other value,
zero-width, word-joiner, BOM and tag characters are also removed (they would hide text from a
reader that the model still gets), and the terminal output prints each as one capped line. `signature` is the Python signature
read with `ast` from the shown bytes of the module (`src/<module>/__init__.py`, else
`src/<module>.py`, as `import` finds them), without the first parameter (the runtime passes its `rt` there; the
model never sets it), at most 1 000 characters; tool code is never imported. When it cannot be
read, `signature` is `null` and `signature_error` is `too_large` (over 256 KiB, or past the
version's text cap, or parameters of more than 2 000 syntax nodes), `syntax_error` (not UTF-8, does not parse, or too deeply nested) or
`not_found` (no such module, or no top-level `def`/`async def` with that name). A `tool.yaml`
that cannot be read gives `functions: []` and `functions_error` (`[TOOL_INVALID] …`). The view
is cached in-process by the version hash.

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
and `… differ only in removed control, bidi or invisible characters` when the only difference is in
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
A `skill/` diff (on `main`, not in a published preview) adds `"skill": {"description_changed",
"scripts_changed"}`, decided from the two manifests and the changed paths, not from the hunks:
the description is sent on every model call of every agent that pins the skill, and a scripts
change is a code change.

**`registry used-by REF --project DIR [DIR ...]`** reports, for each project (default `.`, each
directory once), separately:

- **declared**: each field of `agent.yaml` or `stack.yaml` that `build` or `services up` reads a
  ref from (`runtime`, `prompt.ref`, `knowledge[i].ref`, `tools[i].ref`, `mcp[i].ref`,
  `evals.ref`, `skills[i].ref`, and on `main` `subagents[i].prompt.ref`,
  `subagents[i].tools[j].ref`, `subagents[i].mcp[j].ref`; `services.NAME.ref`, `mcp.NAME.ref`)
  whose value is exactly the ref, never free text such as a description;
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

`root_kind` is as in `show`. For `publish` (which has no `--project`; on `main`, not in a published
preview, #898) it is `project` when `--to` resolves to `DIR/registry` and DIR holds an `agent.yaml`
or `stack.yaml` file, as for `import`, `fork` and `edit`; so `publish DIR` run from a project
directory, with the default `--to registry`, reports `project`. Any other `--to` is `path`, and
the built-in root is `builtin`. The field only describes the root: the write is the same either
way.

## Forking a built-in or shared version (on `main`, not in a published preview)

Versions are immutable, and the built-in registry and `AGENTBREEDER_REGISTRY_PATH` roots are not
the project's to change. **`registry fork REF [--as NAME] [--version X.Y.Z] [--project DIR]`**
resolves `REF` in the project's search roots (built-in, path roots or the project itself) and copies
that version into the project's `./registry` as a new version the project owns. To change it
later, copy the fork, edit the copy, bump its `version:` and `publish` it (a version is never
edited in place).

```
agentbreeder registry fork service/echo@1.0.0 --version 1.0.1
agentbreeder registry fork service/echo@1.0.0 --as my-echo     # service/my-echo@1.0.0
```

A fork needs `--as`, `--version` or both. Forking a ref unchanged is refused (`FORK_UNCHANGED`):
an identical project copy adds nothing, and once edited it would conflict with the original
(`REF_CONFLICT`) rather than be a new version. The rest is `import` with the resolved version
directory as `PATH`: the same staging, checks, rename of the top-level `name:`/`version:` lines,
`REF_CONFLICT` check (forking to a version another root already has with different content is
refused), `--dry-run`/`--expect-digest`, stderr note and output. `--json` prints the `import`
object (indented); `source_ref` is `REF` and `source` is the resolved directory. If the staged copy
declares a ref other than `REF` (the source changed after it was resolved), nothing is written
(`MANIFEST_INVALID`). A missing `REF` is `REF_NOT_FOUND`.

## Editing a project version (on `main`, not in a published preview)

**`registry edit REF --set PATH FILE ... --remove PATH ... [--bump patch|minor|major | --version
X.Y.Z] [--project DIR]`** changes a version the project owns and publishes the result as a new
version; `REF` itself never changes. `REF` must resolve to the project's `./registry`: a built-in
or `AGENTBREEDER_REGISTRY_PATH` version is refused (`EDIT_NOT_PROJECT`) with the `registry fork`
command to run first.

```
agentbreeder registry edit prompt/hello-prompt@0.1.0 --set prompt.md ./prompt.md   # -> 0.1.1
agentbreeder registry edit eval/hello-evals@0.1.0 --set golden.jsonl - --bump minor < cases.jsonl
agentbreeder agent set-ref prompt/hello-prompt@0.1.0 prompt/hello-prompt@0.1.1
```

- The version is copied into a private 0700 staging directory (in the registry root when
  writing), the changes are applied there, `version:` is raised (`--bump`, default `patch`, or
  exactly `--version`; lower than `REF`'s is `VERSION_INVALID`, equal is `VERSION_EXISTS`) and
  the copy goes through `publish`'s checks and one atomic rename. If the new version already exists, or appears meanwhile (another tab or terminal), nothing is written
  (`VERSION_EXISTS`); edit again with another version.
- `--set PATH FILE` replaces or adds the file `PATH` (relative, `/`-separated, missing directories
  are created) with `FILE`'s bytes; an existing file keeps its executable bit, a new one is not
  executable. `FILE` must be a regular file; `-` reads stdin (once per command). `--remove PATH`
  deletes a file, and the directories it leaves empty. `manifest.yaml` (in any letter case) is
  not edited this way (the edit rewrites only its `version:` line, quoting it, as a fork does), and interpreter caches are refused (`EDIT_INVALID`). An edit
  that changes no byte is `EDIT_UNCHANGED`.
- A `knowledge` edit adds, replaces or removes documents under `docs/`; the runtime indexes the
  new version after the next `release`, and `knowledge status` shows it (see [knowledge.md](knowledge.md)).
- Text you type (a prompt, an eval case) goes in a file or on stdin, never as an argument, which
  other local users can read in `ps`. (The console's editors, not built yet, are to pass it the
  same way.)
- An `eval` edit must still load as `release` loads it (JSON lines with `id`, `turns` and
  `reference`; 1 to the case limit), or nothing is written (`EVAL_INVALID`). Other kinds get the
  checks every version gets.
- The output is the diff from `REF` to the new version first (`registry diff`'s format), then
  `publish`'s `REF SHA256 PATH (from REF)` line, a stderr note (code that runs inside agents, or
  text that steers the agent) and a `Next:` line that depends on where the project declares
  `REF` (#990). When `agent.yaml` declares it and `set-ref`'s own dry run passes, the line is
  the `agent set-ref REF NEW` command (with `--project DIR` when DIR is not `.`). Otherwise it
  names the field to change by hand: `in agent.yaml, change prompt.ref from REF to NEW (agent
  set-ref refuses: CODE)`, or `in stack.yaml, change services.postgres.ref from REF to NEW`,
  since no command edits `stack.yaml`. When neither file declares it, the line says which file
  can reference the new version (`agent.yaml` for runtime, prompt, eval, knowledge and tool
  versions, `stack.yaml` for services, either for `mcp`); there is no line when a file could
  not be read. `--dry-run`
  writes nothing and prints the `--expect-digest` to pass; a later call with it refuses unless the
  staged copy has exactly that digest (`DIGEST_MISMATCH`), so what is written is what was
  reviewed. `--json` prints `import`'s object (indented) plus `diff`, the `registry diff --json`
  object from `REF` to the new version (`from` and `to` carry `ref` and `sha256` only).

**Tool versions are checked before they are written** (on `main`, not in a published preview;
#828, M12). `publish`, `import`, `fork` and `edit` refuse a `tool/` version with
`TOOL_INVALID`, and nothing is written, unless:

- It holds only `manifest.yaml`, `tool.yaml`, `README*` and `src/**/*.py`, each name under `src/`
  (directories and module stems) made of lower-case letters, digits and `_`, and each a regular
  file (symlinks and special files are refused as for every version). This is an allowlist, so
  `*.pth`, `*.so`, wheels, `requirements*.txt`, `pyproject.toml` and `setup.py` are refused too:
  tools have no dependencies of their own; they use the runtime's locked packages.
- No top-level name in `src/` (a module or package) shadows a standard-library module, a runtime
  module (`agent`, `config`, `memory`, `tools`, …), a package a built-in `runtime/adk` lock
  installs (`psycopg`, `yaml`, `pydantic`, `google`, `httpx`, `opentelemetry`, …), `pip` and its
  build tools, a dunder name, or a name Python or pytest imports by itself (`sitecustomize`,
  `usercustomize`, `conftest`, `pytest`, `_pytest`); and no name is both `src/<name>.py` and
  `src/<name>/`. A tool's `src/` goes first on `sys.path`, so such a file would
  replace that module for the whole agent. `runtime/adk@1.2.0` makes the authoritative check at
  load time against the image's real `sys.path`, which also catches two tools of one agent with
  the same module.
- `tool.yaml` loads as strict YAML (no duplicate keys, anchors, or more than 64 levels of `[ ]`
  or `{ }`); it has at most 64 `functions[]`, each with a valid `name`, `module` and `function`
  (empty means `name`) and an object `parameters` (empty means no parameters), with no name
  twice, read as the runtime reads them; each `module` resolves to `src/<module>/__init__.py`
  or `src/<module>.py`, at most 256 KiB of strict
  UTF-8 that parses with `ast.parse` and has a top-level `def` or `async def` named `function`.
  The code is parsed, never imported. `build` keeps its own checks. A tool with no functions
  yet is accepted, so a placeholder can be edited; the runtime refuses it at load.

These checks make a tool version well-formed, not safe: tool code runs inside the agent with its
privileges. The boundaries are your review of the diff, the eval gate at `release` (a quality
gate, not a security review) and, later, per-agent tool policy (#909). `registry new tool NAME`
writes a tool that passes them: one function `echo_<name>` in `src/tool_<name>.py`.

**`agent set-ref OLD NEW [--project DIR] [--dry-run | --expect-base SHA256] [--json]`** moves the
project's agent to another version. It is an exact-string replacement in `DIR/agent.yaml`, so
comments and formatting stay. It refuses unless `OLD` is the value of a ref field `build` reads
(`REF_NOT_DECLARED`) and occurs in the file as a whole ref exactly as often as it is declared (a
ref the agent shares with its sub-agents moves in every field at once, on `main`; a comment or
description naming it too is `REF_AMBIGUOUS`; `…@1.0.10` does not contain `…@1.0.1`), `NEW` is the same kind
(`REF_INVALID`), resolves in the project's search roots (`REF_NOT_FOUND`), is not another field's
kind/name (`REF_DUPLICATE`), and the rewritten file still validates with every other ref unchanged.
`NEW` may have another name (a fork made with `--as`). The diff is printed first; the file is
replaced atomically, keeping its mode. `--dry-run` writes nothing and prints `--expect-base`, the
digest of the file it read; with it, a file changed since the preview is refused (`BASE_MISMATCH`),
as is a change found just before the rename (best effort: an editor or git writing at that
instant is not locked out). A write that fails (a read-only directory, a full disk) is
`WRITE_FAILED` and leaves no `.agent.yaml-*.tmp` file. Every refusal exits 2 with `[CODE] message`
on stderr, also with `--json` (as the `registry` commands). `--json` prints `{file, old, new, at, base_sha256,
sha256, diff, dry_run}`. Then build and release (`agentbreeder next` prints the commands);
`build` adds the new version to `agent.lock` and drops the old one.

**What records an edit.** `registry edit` and `agent set-ref` change only the project's files:
the new version directory and `agent.yaml`. Neither writes an audit-chain row; the record is the
files and your git history if you commit them.

## Runtime versions (on `main`, not in a published preview)

A `runtime/` version is the agent server: a fixed `Dockerfile`, a `runtime/` directory with a
hash-locked `requirements.lock`, and a `manifest.yaml`. Since #1083 F1 that manifest and one
generated file are the only place `build`, `new agent` and the registry commands take framework
facts from. Nothing in the engine branches on a framework's name. Two runtimes ship:
`runtime/adk` and `runtime/langgraph@1.0.0` (#1083 F3, the first on the runtime kit; run end
to end and verified on macOS x86_64 only, for the starter and one tool with Gemini, see
[runtimes/langgraph.md](runtimes/langgraph.md)).

**Manifest keys** (beside `port`, `health_path`, `secrets` and `environment`):

| Key | Meaning |
|---|---|
| `protocol` | The agent protocol version the runtime serves. This AgentBreeder knows `1`; any other number is `PROTOCOL_UNSUPPORTED`. A manifest **without** this key is a legacy version (below) |
| `profile` | `v1` or `kit`. Required with `protocol`. `build` writes the deploy manifest's `runtime` field only for `kit` (see [manifest.md](manifest.md)) |
| `kit` | The runtime kit version (`X.Y.Z`) the runtime vendors. Required with `profile: kit`, refused otherwise |
| `framework` | A short lower-case name (`[a-z][a-z0-9-]`, at most 32). Required with `protocol` |
| `display_name` | The framework's name as shown to people: one printable line, at most 100 characters. Required with `protocol` |
| `capabilities` | What the runtime supports: `mcp`, `knowledge`, `notes`, `subagents`, `skills`, `skill_scripts`. `build` refuses an `agent.yaml` part whose capability is missing |
| `limits` | `{knowledge, tools, mcp_servers}`: how many of each the runtime takes, at most `agent.yaml`'s own ceilings (5, 50 and 10). A part left out keeps the limit every runtime had before: 1, 50 and 10 |
| `reserved_tool_names` | Up to 100 tool names the runtime keeps for itself. `build` refuses an MCP tool with one of these names (`MCP_TOOL_RESERVED`), and a function tool named like one (`TOOL_NAME_RESERVED`) |
| `model_api` | `openai-chat` or `anthropic-messages`. Read and checked; nothing uses it yet |
| `notes` | An optional one-line note, at most 300 characters |
| `docs` | An optional relative path to the runtime's docs page |

An invalid key is `ARTIFACT_INVALID` and names the key only (`invalid limits.tools`), never its
value. So is one of `profile`, `kit`, `display_name`, `limits`, `reserved_tool_names`,
`model_api`, `notes` or `docs` in a manifest without `protocol` (`limits needs protocol`): it
is not read as a legacy version with those keys dropped.

**Legacy versions.** Published versions are append-only, so the `runtime/adk` versions that
predate these keys (1.0.4 to 1.4.0) are not edited. A manifest without `protocol` gets one
profile held in the engine, `LEGACY_ADK`: display name "Google ADK", protocol 1, profile `v1`,
capabilities `knowledge` and `notes` plus whatever the manifest lists (`mcp` from 1.1.0), one
knowledge set, 50 tools, 10 MCP servers, and the five MCP tool names ADK skips
(`transfer_to_agent`, `adk_request_credential`, `adk_request_confirmation`, `adk_request_input`,
`set_model_response`). For these versions the reserved names are checked against an MCP
server's own tool names only, as before; a runtime that declares `protocol` also has them
checked against every name the model would see (a function tool's name and `<server>_<tool>`).
`runtime/adk@1.5.0` is the first version that declares `protocol` (1, profile `v1`): it lists
`knowledge`, `notes`, `mcp`, `subagents` and `skills`, the limits 1, 50 and 10, and seven
reserved names (the five above plus `load_skill` and `load_skill_resource`, its own skill
tools), and ships `runtime/modules.json`. Checked by unit tests and its runtime tests only.
A test builds Northwind and the `new agent` starter on `runtime/adk` 1.2.0, 1.3.0 and 1.4.0 and
compares the build context, the manifests and the deploy template byte for byte with digests
taken before this change, and checks that the refusals they gave are still given.

One refusal moved: `agent.yaml` used to refuse a second `knowledge:` entry itself
(`AGENT_INVALID`). It now allows up to 5, and `build` refuses more than the pinned runtime takes
(`RUNTIME_LIMIT`: `runtime/adk@1.3.0 takes at most 1 knowledge: entry (limits.knowledge); the
agent has 2`). `agentbreeder validate` checks the file's shape only, so it no longer refuses a
second entry; `build` does.

**`runtime/modules.json`.** A runtime that declares `protocol` must ship this generated file
(`ARTIFACT_INVALID` without it): the runtime's own top-level module names, and the import names
of every package in its lock.

```json
{"schema_version": 1, "modules": ["kit", "server"], "imports": {"httpx": ["httpx"]}}
```

`python scripts/runtime_modules.py VERSION_DIR --site-packages DIR` writes it from the lock
installed into `DIR`, and a repository test fails when a built-in version's file does not list
exactly its `runtime/` modules and its lock's packages. `build` refuses a tool function whose
module is one of those names (`TOOL_MODULE_SHADOWS`), and `registry publish|import|fork|edit` refuse a
`tool/` version that would shadow a name from any built-in runtime's file (`TOOL_INVALID`).
Legacy versions have no file: their names are in `LEGACY_ADK`, and those checks are unchanged
for them.

**`agentbreeder registry runtimes [--project DIR] [--json]`** prints the engine's resolved view,
so a reader such as the local console needs no knowledge of profiles:

```json
{
  "schema_version": 1,
  "runtimes": [
    {
      "ref": "runtime/adk@1.4.0", "name": "adk", "version": "1.4.0", "root_kind": "builtin",
      "display_name": "Google ADK", "framework": "google-adk", "protocol": 1, "profile": "v1",
      "capabilities": ["knowledge", "mcp", "notes"],
      "limits": {"knowledge": 1, "mcp_servers": 10, "tools": 50},
      "reserved_tool_names": ["adk_request_confirmation", "…"],
      "verified": true, "usable": true
    }
  ]
}
```

- `kit`, `notes` and `docs` appear only when the manifest has them.
- `verified` is true for a runtime version that has been run end to end. Today that is
  `runtime/adk@1.3.0` (Northwind 61/61, #1095), `runtime/adk@1.4.0` (the starter and the
  3.0.0a3 public-registry runs), and, on `main`, `runtime/adk@1.5.0` (Northwind 61/61, the
  sub-agents and skills acceptance runs, #1058 and #1059, and a fresh run from a wheel through
  the CLI and the console) and `runtime/langgraph@1.0.0` (Northwind 61/61 locally and on `kind`,
  and the starter and one tool from a wheel through the CLI and the console, macOS x86_64 only;
  see [the record](superpowers/verification/verify-langgraph-adk150.md)). It is about the version,
  not a `new agent` combination: a plain ADK spec is verified only on `runtime/adk@1.4.0`, so a
  spec or `--runtime` that pins `1.3.0` or `1.5.0` without sub-agents or skills still has
  `verified: false` in `new agent`'s output, and the console's form starts on `1.4.0` even though
  `1.5.0` is newer and labelled run end to end.
- A version the engine cannot use is still listed, with `usable: false`, a `code` and a
  `reason` and without the profile fields: an invalid manifest or a missing `modules.json`
  (`ARTIFACT_INVALID`), an unknown protocol (`PROTOCOL_UNSUPPORTED`), a version under a
  symlinked directory or with a link or non-regular file inside (`ENTRY_UNSAFE`), one with a
  file that cannot be read (`ENTRY_UNREADABLE`), a directory whose name is not an `X.Y.Z`
  version (`REF_UNPINNED`) or one with different content in two roots
  (`REF_CONFLICT`). Each version is hashed as `build` would copy it before its manifest is
  read, so nothing is read from outside the version.
- Versions run end to end come first, then by name, newest version first.
- The conformance result the console design mentions is not part of this output; there is no
  `agentbreeder conformance` command yet.

## Skills (on `main`, not in a published preview)

A `skill` version is an [Agent Skills](https://agentskills.io)-style folder: instructions the
model loads when it needs them (#1059). You can author, publish, verify, show, diff, lock and
find them (`used-by`). `runtime/adk@1.5.0` loads skills without scripts (instruction packs; see
[agent-yaml.md](agent-yaml.md#runtimeadk150); run end to end once with two instruction packs on
Docker and Gemini, `docs/superpowers/verification/1059-skills-a.md`); `build` refuses
`skills:` in `agent.yaml` on an earlier runtime (`RUNTIME_NO_SKILLS`) and a skill with scripts on
every runtime (`RUNTIME_NO_SKILL_SCRIPTS`).

```
registry/skill/refund-policy/1.2.0/
  manifest.yaml     # kind: skill, name, version, description, [scripts]
  SKILL.md          # front matter (name, description) + Markdown body
  references/       # optional: flat .md or .txt files
  scripts/          # script skills only; every file declared in manifest.yaml
```

- **Instruction pack or script skill.** A version with no `scripts/` and no `scripts:` key is an
  instruction pack: text that steers the agent. **A version with any `scripts/` entry, or a
  `scripts:` key, is code**, like a tool: `list` marks it `(code)` (`"runs_code": true` in
  `--json`, where a skill row also has `"code"`: `text`, `code` for scripts, or `unknown` for a
  version that cannot be read or holds an entry a skill may not, treated as code), `import` and `fork` print the code notice, and the console asks the "this code runs"
  confirm. The decision comes from the version's files, never the kind alone, and fails closed:
  a version whose manifest cannot be read counts as code. Nothing runs a script yet.
- **`SKILL.md`** is UTF-8, at most 32 KiB, and starts with a `---` front matter block, read with
  the same strict YAML rules as `agent.yaml`. The front matter has exactly `name` and
  `description`, both equal to the manifest's, and optionally `license` and `compatibility`. Any
  other key is refused (`SKILL_FIELD_UNSUPPORTED`), including `metadata` and `allowed-tools`,
  which some skill tools use to add tools or state.
- **The description is in every turn** of every agent that pins the skill, so treat it as part
  of the prompt: one line of 1 to 1024 characters, with no line break, control, bidi or invisible
  character. The same rule applies to script descriptions, every string in a script's
  `parameters` (which may use only `type`, `properties`, `required`, `description`, `enum` and
  `items`), and `license` and `compatibility` (at most 500 characters).
- **The name** is kebab-case (`refund-policy`); a script skill's name is at most 24 characters,
  so each script's tool name, `skill_<name with _>__<script>`, fits.
- **`references/`** holds at most 20 flat files named like `eu-rules.md` (`.md` or `.txt`, UTF-8,
  at most 64 KiB each).
- **`scripts:`** (script skills) declares 1 to 10 scripts, each `{name, path, description,
  parameters}`: `name` matches `[a-z][a-z0-9_]{0,23}`, `path` is `scripts/<module>.py` (at most
  64 KiB), and `parameters` is a JSON Schema object. A file in `scripts/` that is not declared, or
  a declared one that is missing, is `SKILL_UNDECLARED_SCRIPT`.
- **Nothing else** may be in the version (no `assets/`, no README): `SKILL_INVALID`.

`publish`, `import`, `fork`, `edit` and `verify` run these checks. `show` adds `skill` (the
front matter, body, reference paths, and each script with its tool name) or, for a version that
breaks the rules, `skill: null` and `skill_error`. The lock hashes the whole version, `scripts/`
included. The console's registry browser does not list skills yet.

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

Changes on `main`, not in a published preview (#829 slice 1a, the groundwork for remote
registry sources):

- **`--locked` reads only the locked root.** `build --locked`, `services up --locked` and
  `release` (for the eval set) read each locked ref only from the root its entry names, with
  no check across the other roots, so a copy of the same version in another root is neither
  read nor a `REF_CONFLICT`. The named root must be configured now: `registry` (the project's
  `./registry`), an `AGENTBREEDER_REGISTRY_PATH` entry (recorded relative to the project) or
  `builtin`. Any other `source` (for example an absolute path you never configured) is
  `LOCK_STALE`; run a plain `build` to re-lock. A plain build still resolves across all roots
  and refuses a `REF_CONFLICT`.
- **Strict lock files.** `agent.lock` and `stack.lock` refuse unknown top-level or entry keys,
  a malformed `sha256` (`sha256:` and 64 hex digits) and an empty `source` (`LOCK_INVALID`); a
  newer `lock_version` says to upgrade AgentBreeder. A local registry root whose label would
  start with `remote:` is `REGISTRY_ROOT_INVALID`.
- **Lock format version 2.** Written when `agent.yaml` declares `registries:` (see
  [Remote registry sources](#remote-registry-sources-git-on-main-not-in-a-published-preview)):
  version 2 adds a `sources` table (name → `type`, canonical `url`, `pin`, and a git `path`),
  `remote:<name>` entry sources and an optional per-entry `pin`. Without `registries:` the lock
  is version 1, byte for byte as before, and removing `registries:` writes version 1 again.
  `stack.lock` stays version 1. JSON Schema: [`agent-lock.schema.json`](agent-lock.schema.json).
  Design: `docs/superpowers/specs/2026-11-11-remote-registry-sources-design.md`.
- **No empty directories in a version.** `registry publish`, `import`, `fork` and `edit` refuse
  a version with an empty directory, and `registry verify` reports one (`ENTRY_UNSAFE`, "add a
  .keep file"): git cannot store an empty directory, so the same version would hash differently
  from a directory and from a remote copy. No shipped version has one.

## Remote registry sources (git, on `main`, not in a published preview)

#829 slice 1b. An `agent.yaml` can name **git repositories** that hold registry versions, each
pinned to a full commit SHA. `build` fetches only the refs it needs, hashes the copy exactly as
for a local version, and records the source and its pin in `agent.lock` (version 2). OCI and
HTTP index sources are not supported yet (`SOURCE_INVALID`).

What has run: the test suite against git's own `git http-backend` behind a loopback HTTPS server
on macOS (Python 3.11, 3.12 and 3.13, git 2.39). It has not been run against GitHub or any other
hosted git server, over ssh, or behind a proxy.

```yaml
registries:
  - name: acme                      # a label for messages and agent.lock
    git: https://github.com/acme/agent-registry.git
    commit: 3f2a9c1d0b7e4a5f6c8d9e0f1a2b3c4d5e6f7a8b   # a full SHA; branches and tags are refused
    path: registry                  # optional: the directory holding kind/name/version
```

- **Search order.** `./registry`, each `AGENTBREEDER_REGISTRY_PATH` entry, each source in the
  order declared, then the built-in registry. A ref with different content in two of them (a
  local copy and a source, or two sources) is `REF_CONFLICT`, naming both and their hashes: a
  source never silently shadows a local version, and a local copy never silently shadows a
  source's. Same content is fine; the first wins.
- **Kinds.** Sources may provide `prompt`, `eval`, `knowledge` and `tool` versions. A
  `runtime`, `service`, `skill` or `mcp` version found only in a source, or locked from one in
  `agent.lock`, is `REMOTE_KIND_REFUSED`
  (copy it into `./registry` with `registry import`). Remote `mcp` versions come with a later
  slice (consent bound to the pin).
- **URLs.** `https://host[:port]/path` or `ssh://[user@]host[:port]/path` (scp-style
  `user@host:path` is accepted). `http://`, `file://`, local paths and other transports, a user
  name or token in an `https` URL, a query or fragment, `%`, `~`, `..` and a host or segment
  starting with `-` are `SOURCE_INVALID`. Private repositories use your own git credentials
  (credential helper, ssh agent), never a token in the URL.
- **Pins.** `commit` must be a full 40-character SHA (`SOURCE_UNPINNED` otherwise; find it with
  `git ls-remote URL BRANCH`). SHA-256 repositories are not supported.

### Trusting a source

A build contacts only hosts **you** trusted, in every mode (plain, `--locked`, `release`), so a
project cannot make your machine contact a host with your credentials. (A version already in the
cache is used without contacting anyone; its hash is checked against `agent.lock`.) The list
is yours, in `~/.agentbreeder/sources.yaml` (mode 0600), never in the project:

```bash
agentbreeder registry trust https://github.com/acme          # an organization prefix
agentbreeder registry trust https://git.corp.example/acme --private   # an internal host
agentbreeder registry trust --list
agentbreeder registry trust https://github.com/acme --remove
```

- An entry matches the canonical source URL on whole path segments (`https://github.com/acme`
  matches `https://github.com/acme/agent-registry.git`, not `https://github.com/acme-evil/x`);
  the scheme, port and ssh user must match exactly. A bare host (`https://github.com`) trusts
  every repository there, including an attacker's, and needs `--whole-host`.
- A host that resolves to a loopback, private, link-local or unique-local address (including an
  IPv4 address mapped into IPv6) needs `--private` (`SOURCE_HOST_PRIVATE`). Git over https is
  pinned to the addresses that were checked, which needs git 2.37 or newer
  (`SOURCE_TOOL_MISSING` otherwise). Over ssh, and behind a proxy, this check is best
  effort (ssh and the proxy resolve the host themselves); the prefix check still holds.
- **Two spellings of one address are two sources.** Trust matching compares canonical strings,
  so a trust entry for one spelling never trusts another (it fails closed). Spellings that
  would name an IPv4 host a second way are refused outright: an IPv4-mapped IPv6 host such as
  `[::ffff:127.0.0.1]`, and decimal or hex IPv4 such as `2130706433` (`SOURCE_INVALID`).
- Without a terminal, `registry trust` needs `--yes`. In CI, set
  `AGENTBREEDER_REGISTRY_TRUST=https://github.com/acme` (comma-separated prefix entries only; a
  bare or private host needs the file).
- This limits which hosts a project can make you contact. It is not publisher verification: a
  trusted organization can still serve harmful code, and tools are code that runs inside your
  agent.

### Fetching, the cache and `--offline`

- Each source is fetched at most once per build, at the pinned commit, into a fresh private
  bare repository that is deleted afterwards: no git repository or git config is ever kept.
  Files are read from git objects without a checkout. Symlinks, submodules and Git LFS files in
  a version are `ENTRY_UNSAFE`; `__pycache__` and `*.pyc` are skipped.
- git runs with an allow-listed environment (every other `GIT_*` variable is dropped), from an
  empty private directory, with redirects, hooks, submodules and every protocol except https and
  ssh switched off. A fetch is limited to 120 s (`SOURCE_TIMEOUT`) and 200 MB
  (`SOURCE_TOO_LARGE`).
- The cache is `~/.agentbreeder/cache/registries/v1` (or `AGENTBREEDER_CACHE_DIR`), which must
  be yours and mode 0700 (`CACHE_UNSAFE`). It holds extracted versions by tree hash and each
  source's list of versions at a pin.
- A ref **already in `agent.lock`** may come from the cache, and the copy's hash must equal the
  lock; a cached copy that does not match is deleted and fetched again (`CACHE_CORRUPT`). A
  **first lock** is always fetched from the pinned commit, never from the cache.
- A plain build needs the network once per source per pin, to read the source's list of
  versions for the conflict check, even if no ref ends up coming from it. Remove a source you no
  longer use.
- `build --offline` never uses the network: a ref not in the cache, or a first lock, is
  `SOURCE_OFFLINE`. Run one online build first. `registry fetch` (to fill the cache in CI) is
  not in this version.
- `build --locked` reads each locked ref only from its locked source, and `registries:` must
  equal the lock's `sources` exactly (`LOCK_STALE`, naming the difference).

### What protects you, and when

| Moment | What protects you | What does not |
|---|---|---|
| First lock of a ref | the pin you chose; your trusted list; the entry checks; never from the cache | nothing vouches that the commit is benign |
| Later builds, `--locked` | the tree hash in `agent.lock`, the source pin, your trusted list | |
| The remote changes (push, force-push) | the pin and the lock: content is never read from a branch | a deleted commit makes a cold-cache build fail (`SOURCE_COMMIT_MISSING`), not change |
| A pin bump in `agent.yaml` | every entry from that source is fetched again and must keep its hash (`HASH_MISMATCH` names both pins) | |

Git pins are SHA-1 commit ids; the content check after that is SHA-256 over the copied tree.
For refs already in `agent.lock`, a locked build uses exactly the recorded bytes; a changed
remote cannot change it. Hashes detect a change after the first lock; they do not vouch for the
first lock.

### Messages

| Code | Meaning and remedy |
|---|---|
| `SOURCE_HOST_UNTRUSTED` | you have not trusted the URL; the message proposes `registry trust <prefix>` (and the CI variable without a terminal) |
| `SOURCE_HOST_PRIVATE` | the host resolves to a private address; `registry trust <prefix> --private` if it is yours |
| `SOURCE_AUTH_FAILED` | git authentication failed; check `git ls-remote URL` in the same shell |
| `SOURCE_UNREACHABLE` | DNS, TCP, TLS, a redirect, or another fetch failure; `--offline` if already cached |
| `SOURCE_COMMIT_MISSING` | the pinned commit is gone (deleted branch or force-push); pin a commit that exists |
| `SOURCE_OFFLINE` | `--offline` and not in the cache, or a first lock |
| `SOURCE_TIMEOUT`, `SOURCE_TOO_LARGE` | the 120 s or 200 MB fetch limit |
| `SOURCE_TOOL_MISSING` | `git` is not installed |
| `SOURCE_INVALID`, `SOURCE_UNPINNED` | the `registries:` entry breaks a rule above |
| `SOURCE_TRUST_INVALID` | `sources.yaml` or `AGENTBREEDER_REGISTRY_TRUST` is malformed, or the file is not 0600 |
| `REMOTE_KIND_REFUSED` | a runtime, service, skill or mcp version only a source holds |
| `REF_CONFLICT` | the same version differs between two roots; delete the local copy or publish a new version |
| `CACHE_UNSAFE`, `CACHE_CORRUPT` | the cache directory's owner or mode; a cached copy that did not match the lock (a warning) |

`build` prints one line per remote ref: `tool/geo@1.2.0 <- acme @3f2a9c1`, and `build.json`
gains the `sources` table. Not in this version: `registry fetch`, `registry pin`,
`registry cache list|prune`, `--offline` on `release` and `eval`, remote `mcp` versions,
`registries:` in `stack.yaml`, OCI and HTTP index sources, and the console's source states.

## Agent packages (in progress, on `main`, not in a published preview)

M13 (#763) adds an `agent` registry kind: a released agent kept as an immutable version at
`./registry/agent/<name>/<X.Y.Z>/`. So far there are the package format, a deterministic packer,
read-only views, `release`, which writes a package for every image it releases (see
[`agent-yaml.md`](agent-yaml.md#eval-and-release)), and `deploy agent/<name>@X.Y.Z --local`,
which deploys one on local Docker (see [`commands/deploy.md`](commands/deploy.md)). Cluster
deploys of a package come in a later slice. The design is in `docs/superpowers/specs/2026-11-13-agent-package-design.md`.

- `agent/<name>@X.Y.Z` refs parse, but no `agent.yaml` or `stack.yaml` field takes one, a lock
  cannot hold one, and `build` never copies one into an image.
- Only `release` writes `agent/` versions. `registry publish`, `import`, `fork`, `edit` and
  `registry new agent` refuse with `[PACKAGE_RELEASE_ONLY] agent/ versions are written by
  release`. A copy made by hand (`cp`) is still possible; deploy will check it against the
  release record.
- `registry list` shows `agent/` rows. `registry verify` also checks that each one is a valid
  package: exactly the four files below, a strict `package.json` naming that version, and an
  `agent.yaml` whose hash matches the one in `package.json`.
- `registry show agent/<name>@X.Y.Z` adds a `Package:` summary (runtime, image, evals, release
  time, models, MCP servers); `--json` adds `package` (the `package.json` fields, cleaned) and
  `package_error` (null, or why the version is not a valid package). An invalid package is
  shown, not refused, with the reason.
- `registry diff` compares two versions of one package like any other version.
- `registry used-by agent/…` lists the deployments linked to that package (by `deploy agent/…`
  or by the `release` that wrote it), from the state's `package` column. One whose tree hash
  differs from the project's copy, or whose project has no copy, is listed as undetermined.

A package holds exactly four files:

| File | Contents |
|---|---|
| `manifest.yaml` | `kind: agent`, name, version, description |
| `package.json` | the release record: agent name, version and runtime; the image ID and platform; hashes of the built `agent.yaml` and `agent.lock`; the eval summary (set ref and hash, threshold, retries, run ID, pass count, environment); the deploy template; release time and AgentBreeder version |
| `agent.yaml` | a byte copy of the released `agent.yaml` |
| `agent.lock` | the released lock. Every `source` label other than `builtin`, `registry`, `remote:<name>` or a project-relative path without `..` is replaced with `external:`, so no home directory or user name is recorded. This copy is a record only and is never read as a lock |

`package.json` is strict: at most 64 KiB, `package_version` 1, and unknown keys or a newer
`package_version` are refused (`PACKAGE_INVALID`, "written by a newer AgentBreeder? upgrade").
The deploy template holds only `port`, `health_path`, `aliases`, `budget_usd`, `rpm` and
`mcp_access`. It has no field for an identity, secrets, policy, capabilities, requirements, a
target, an image or a gateway, so a package cannot grant itself any of them. A package holds no eval
case text, model output, logs, secrets or registry references.

A package is a record, not a credential. It is not signed. Deploying one depends on the
release row in your state directory's audit log, not on anything in the package: deploy refuses a
package this state directory did not release, or one that changed after release. That row names
the package, its tree hash and a hash of the project path (never the path itself).

Commit `./registry/agent/` with the rest of the project registry, so the team sees what was
released. AgentBreeder never overwrites a version. If git removes or changes a package while its
image is still in production, the next `release` of that image does not reuse it: it evaluates the
image again and writes a new version.

### Deterministic archives

The packer writes one registry version as a `tar+gzip` rooted at `<kind>/<name>/<X.Y.Z>/`.
Entries are in sorted order with mtime 0, uid and gid 0, no owner names, and modes 0644 or 0755
only; the gzip header has mtime 0 and no file name. It applies the same entry checks as hashing:
regular files and directories only, no symlinks or hardlinks, and the `MAX_FILES` and size
limits. Extracting the archive and hashing the version gives the same tree hash as the source.
The tar bytes are the same on every machine, but the gzip bytes can differ between zlib builds, so
compare tree hashes, not archive digests.

### OCI artifact (library only, in progress)

`package_artifact.py` builds and pushes a package as an OCI artifact (#763, M13 slice 4a). No
command calls it yet: `deploy agent/<name>@X.Y.Z --cluster` still refuses with
`PACKAGE_TARGET_UNSUPPORTED`, and the push has run only against an in-process test registry, not
against kind's `registry:2`.

- The artifact is one OCI image manifest with `artifactType`
  `application/vnd.agentbreeder.agent.v1`, the OCI empty config, and exactly one layer of type
  `application/vnd.agentbreeder.agent.layer.v1.tar+gzip`: the deterministic archive above, rooted
  at `agent/<name>/<X.Y.Z>/`. It may name the pushed image's manifest as its `subject`.
- It is pushed to `<registry>/<name>` with the tag `v<X.Y.Z>.agent`. The registry must be
  `127.0.0.1[:port]/path` exactly; `localhost`, any other address or a look-alike name is refused
  (`REGISTRY_NOT_LOCAL`). The push uses plain HTTP with no credentials and no proxy, follows no
  redirect, and refuses an upload location on another host.
- After the push the tag is read back and compared byte for byte (`REGISTRY_MISMATCH`). If the
  registry refuses a manifest with a `subject`, the artifact is pushed without one and is found by
  tag.
- Before the push the packed layer is checked: it holds the four package files and nothing else,
  and no file contains the home directory, a `/Users/` or `/home/` path, a `~/` path or a `..`
  path segment (`PACKAGE_LEAK`, which names the file and not the text). The user name alone is
  not checked.
- A tag can be moved by anyone who can reach the registry, so a reader must extract the layer and
  compare its tree hash, not trust the tag. No command reads the artifact yet.
