# `agentbreeder registry`

Author and inspect registry versions.

Implemented: `registry new|publish|list|verify` and, on `main` (not in a published preview), the read-only `registry show`, `registry diff` and `registry used-by`, plus `registry import PATH [--as NAME] [--version X.Y.Z]`, `registry fork REF [--as NAME] [--version X.Y.Z]` (copy a built-in or shared version into the project registry as a new version), `registry edit REF --set PATH FILE|--remove PATH [--bump patch|minor|major | --version X.Y.Z]` (change a project version's files and publish them as a new version) and `--dry-run`/`--expect-digest` on `publish`, `import`, `fork` and `edit` (see [`registry.md`](../registry.md)). To move `agent.yaml` to another version, see [`agent set-ref`](agent.md).

On `main`, not in a published preview: `list`, `show`, `diff` and `verify` also read `agent/` package versions, and `publish`, `import`, `fork`, `edit` and `new agent` refuse them (`PACKAGE_RELEASE_ONLY`, only `release` writes packages). See [Agent packages](../registry.md#agent-packages-in-progress-on-main-not-in-a-published-preview).

On `main`, not in a published preview: `publish`, `import`, `fork` and `edit` check a `tool/` version before writing it (allowed files only, no module that shadows the standard library, the runtime or its locked packages, every function resolving to a top-level `def` that parses; `TOOL_INVALID`), and `show` of a tool lists its functions with their `tool.yaml` parameters and Python signatures. See [Tool versions](../registry.md#editing-a-project-version-on-main-not-in-a-published-preview).

Flags: `agentbreeder registry --help`.
