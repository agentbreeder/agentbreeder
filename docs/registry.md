# Registries and `agent.lock` (preview)

> Part of the `agent.yaml` build work (#725), preview. Registries, the lock file and
> `agentbreeder build` (see `agent-yaml.md`) work today; verified with `examples/northwind`.

A registry is a directory of **immutable, versioned building blocks**: prompts, tools,
knowledge sources, eval sets and runtimes. You reference each one as `kind/name@X.Y.Z`, for
example `tool/orders@1.0.0`. Only exact versions are accepted.

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
```

## Publishing and integrity

- **Versions are immutable.** `publish` refuses to overwrite an existing version
  (`VERSION_EXISTS`).
- **What a version may contain.** Only regular files and directories. Symlinks, hardlinks,
  FIFOs and devices are rejected (`ENTRY_UNSAFE`), as are `..` paths and non-UTF-8 names.
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
