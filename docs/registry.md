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
agentbreeder registry publish ./tool-orders [--to registry] [--json]   # prints ref, sha256, path
agentbreeder registry list [tool/] [--json]
agentbreeder registry verify [--json]                 # exit 2 if any version is unsafe or inconsistent
agentbreeder registry show prompt/support@1.0.0 [--project DIR] [--json]
```

`list`, `verify` and `show` search the roots of `--project` (default `.`).

## Browsing versions (on `main`, not in a published preview)

`show`, `diff` and `used-by` only read. They are what the local console's registry pages will
call, so their `--json` output is the contract described here.

**`registry show REF`** prints one version: its hash, root, description, file list and the text
of its text files. The version goes through the same checks as `verify` (`REF_CONFLICT`,
symlinks, hardlinks, devices, `MAX_FILES`/`MAX_BYTES`, the shipped content manifest); each file
is opened without following symlinks and must still be the file that was checked. Output is
capped: at most 256 KiB of text per file and 2 MiB per version (later files are listed but not
shown). Binary files (a NUL byte or invalid UTF-8) are listed, not shown. Control characters
(except tab and newline in content) and Unicode bidi controls are removed from file names,
manifest values and content before anything is printed, so a version cannot rewrite your terminal
or disguise a file name; the file is marked `stripped`.

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

## Publishing and integrity

- **Versions are immutable.** `publish` refuses to overwrite an existing version
  (`VERSION_EXISTS`).
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
