# `agentbreeder next`

Show where this project is on the guided path and the command to run next.

`next [DIR] [--environment ENV] [--output DIR] [--json]` shows where a project is on the guided path and the one command to run next, reading only (state opened read-only, `DIR`'s stack state, one `compose ps`, no key values); text exits 1 while steps remain, `--json` prints the [`next.md`](../next.md) contract and exits 0 (on `main`, not in a published preview).

`next` offers `new agent` for a directory without `agent.yaml` (on `main`, not in a published preview).

[`your-first-agent.md`](../your-first-agent.md) walks from an empty directory to a released starter agent and an `invoke` reply with the steps and commands `next` uses; a test keeps the page, `guide.py` and `guide_checks.py` in step (on `main`, not in a published preview).

Flags: `agentbreeder next --help`.
