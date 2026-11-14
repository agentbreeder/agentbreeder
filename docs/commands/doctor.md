# `agentbreeder doctor`

Check local Docker prerequisites without changes.

Implemented: `doctor`.

`doctor` also reports whether Compose v2 supports `up --wait` (a warning, not a failure). It also prints an INFO line when it finds 2.x state in `~/.agentbreeder` (names such as `state.json`, `workspace.yaml`, `registry/`; listed, never read); 3.x does not read or change it.

`doctor` also checks, as advice only (never a failure), that the `agentbreeder` on `PATH` is this version: none is expected with npx or uvx (uv's temporary environment is skipped, so a stale install behind it is found), and a stale one from another install is named with the command to use instead.

Flags: `agentbreeder doctor --help`.
