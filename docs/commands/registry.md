# `agentbreeder registry`

Author and inspect registry versions.

Implemented: `registry new|publish|list|verify` and, on `main` (not in a published preview), the read-only `registry show`, `registry diff` and `registry used-by`, plus `registry import PATH [--as NAME] [--version X.Y.Z]`, `registry fork REF [--as NAME] [--version X.Y.Z]` (copy a built-in or shared version into the project registry as a new version) and `--dry-run`/`--expect-digest` on `publish`, `import` and `fork` (see [`registry.md`](../registry.md)).

Flags: `agentbreeder registry --help`.
