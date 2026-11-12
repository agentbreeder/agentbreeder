# M0a verification: console backend foundation (#815)

Status: **verified from `main`** (da0b107, after #858 merged, 2026-11-12, macOS 13.7.8, Docker
Desktop engine 27.5.1). Every acceptance check that ran (all but the Chrome click-through) passed from a wheel built from `main` in a clean
sandbox; nothing was left running and no key or one-time link appeared in the output.

This is on `main`, not in a published preview.

## What changed (four PRs)

| PR | Merged | Content |
|---|---|---|
| #849 | 5892dfe | Route table with guards in a fixed order (host, API header, `Sec-Fetch-Site`, non-GET `Origin` and content type, session, then 404/405, body cap, handler; S-03, S-25), the `{data, error, stale}` envelope with the CLI's `[CODE]` only (S-17), per-route body caps with a real 413, static allow-list with `woff2`, project ownership checks (S-01) |
| #852 | 55acbbf | Lock scopes (project; machine-wide for cluster jobs and promote to kind; short writes per target; policy save exclusive both ways), several running jobs, `/api/activity`, cancel (one SIGINT) and shutdown (SIGINT, grace, SIGTERM to each process group; S-18), docs that console locks order console jobs only (S-11) |
| #857 | 8cd9f5a | `up` re-execs as `python -I -m agentbreeder.center` (same pid and terminal; version and package handshake), single-instance `flock` (a second `up` prints the URL), S-16 leftovers removed only by the lock holder, port 8080 by default with the first-run scan to 8099 and refusal once a password exists (S-29), `center.sqlite3` `schema_version` and migrations, the S-30 record of what is audited where |
| #858 | da0b107 | In-process read layer (registry list, agent.yaml, agent.lock, used-by's files part; verify as a tracked CLI child) with an allowlist import guard; cancel delivery (the SIGINT no sooner than 0.2 s after a job's first line; "Cancelling…" and a confirmed "Stop now") |

Every PR had a different-model review (Claude Fable, read-only) until it approved; the review
tables are in the PR bodies. The full suite on `main` (da0b107): 2433 passed, 33 skipped (opt-in live and
real-Docker tests), in 339 s. Coverage was not measured in that run.

## Wheel from main (clean sandbox)

`main` at da0b107, no tracked changes. Wheel built with `python -m build --wheel`
(`agentbreeder-3.0.0a2-py3-none-any.whl`). Every command ran under `env -i` with an empty `HOME`,
`TMPDIR` inside the sandbox, `PIP_NO_CACHE_DIR=1`, and fresh Python 3.12 venvs: one with the
wheel, one with `agentbreeder==3.0.0a2` from PyPI (to create a2 console state). AgentBreeder
refuses `DOCKER_HOST`, so the script created a local Docker context named `sandbox` in the sandbox
`HOME` pointing at the same engine socket.

The script is `run.sh` (sandbox, installs, leak scan, teardown) with `accept.py` (stdlib only,
driving the real console over HTTP). Install, acceptance, leak scan and teardown took about 25 s
in all; the acceptance itself took 6 s. Only the acceptance output (`accept.log`) was saved; the
install, leak-scan and teardown lines went to the terminal.

| Check (in the order run) | Result | Time |
|---|---|---|
| a2 state created by the published 3.0.0a2 `up` | tables `jobs`, `lines`, `projects`; 1 project, 1 job | 2.1 s |
| First-run port scan skips a busy 8080 (a `0.0.0.0` listener) | `http://127.0.0.1:8081` | 0.8 s |
| `python -I -m agentbreeder.center` in the process list | `… Python -I -m agentbreeder.center --expect-version=3.0.0a2 --expect-packag…` | 0.0 s |
| a2 `center.sqlite3` migrated, projects and history kept | `schema_version=2`, 1 project, 1 job | 0.0 s |
| A second `up` prints the running URL and exits | exit 0: "The AgentBreeder console is already running: http://127.0.0.1:8081" | 0.4 s |
| An oversized body gets 413 | a claimed 70 KiB body: 413; a real 2 MiB body: 413 | 0.0 s |
| Two projects run engine jobs at once | both `build` starts 200; their runs overlapped (each failed with exit 2 in 0.4 s) | 1.5 s |
| Two cluster jobs serialise machine-wide | `cluster-up` in A: 200; `cluster-down` in B while A runs: 409 `BUSY` | 0.0 s |
| With a password, a busy port is refused and named | exit 2: "127.0.0.1:8081 is in use by another program. … start with --port N." | 0.8 s |

The console then stopped with exit 0.

## Acceptance (from the issue, as amended by S-29)

| Check | Result |
|---|---|
| Existing UI and `tests/test_center.py` stay green | the full suite above |
| Two projects run engine jobs at once | PASS |
| Two cluster jobs serialise machine-wide | PASS |
| A second `up` exits with the running URL | PASS |
| First-run port scan picks 8081 when 8080 is taken | PASS |
| With a password, a busy port is refused and named | PASS (the password state is M2's; simulated by its `users` table) |
| An oversized body gets 413 | PASS |
| a2 state migrates `center.sqlite3`, keeping projects and job history | PASS (state created by the published 3.0.0a2 `up`) |
| `python -I` in the process list | PASS |
| Chrome click-through of the existing screens | not run (the Chrome extension was unavailable); open on #815 |

## Leak scan and teardown

`run.sh` checked that no `~/.env` value of 12 or more characters (6 values) and no one-time link
appeared in the acceptance output, that no key files were left under the sandbox `HOME`, that no
console process was left, and that ports 8080, 8081 and 8900 no longer answered. All of these
passed; their lines were not saved. No job reached container work (each stopped earlier with
`STACK_NOT_UP`, `CLUSTER_TOOL_MISSING` or a no-op `services down`).

## Notes

- The two `build` jobs belong to example projects whose services are not up, so each fails in
  about 0.4 s; the check is that their recorded start and end times overlap (the later start came
  before the earlier end), which one-job-per-project locking allows and a global lock would not.
- The cluster serialisation check starts `cluster up` in one project and `cluster down` in
  another while the first runs: the second gets `409 [BUSY]`. `kind` is not on the sandbox `PATH`,
  so the first job fails with `CLUSTER_TOOL_MISSING` (exit 2) about 0.4 s later; the 409 arrives
  while it is still running.
- The password check creates M2's `users` table by hand, because sign-in arrives in M2 (#818).
- M0a added no new screens, but `app.js` changed (cancel with "Cancelling…" and "Stop now",
  activity polling). Unit tests cover the server side (`cancelling`, `can_stop`,
  `/api/activity`); no test runs `app.js` itself, and the wheel acceptance does not touch it.
  The Playwright UI harness belongs to M0b.
