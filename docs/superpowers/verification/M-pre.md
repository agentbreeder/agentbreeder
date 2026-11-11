# M-pre verification: CLI commands split into modules (#813)

Status: **verified from `main`** (7852936, after #840 merged, 2026-11-11, macOS, Docker Desktop
engine 27.5.1). No behaviour change: every `--help` is byte-identical to the golden files on
Python 3.12 and 3.13 from the installed wheel, and the real-Docker quickstart passes.

## What changed

- Each command's parser setup and handler moved from `src/agentbreeder/cli.py` to
  `src/agentbreeder/commands/<name>.py` (23 modules; each defines `NAMES`, `register(commands)`,
  `run(args)`).
- `cli.py` (1168 → 244 lines) keeps `main`, the top-level parser, the ordered `COMMAND_MODULES`
  list (imported lazily, so command modules import `cli` without a cycle) and the shared helpers
  (`_message`, `_use_stack`, `_file_kind`, `stateful_parser`, `add_stack`, `run_stateful`,
  `_print_deployment`).
- `tests/test_cli_help_golden.py` with `tests/fixtures/cli_help/` (44 files, plus 7 `py313/`
  overrides where Python 3.13 wraps usage lines differently), committed before any code moved.
- `MANIFEST.in` ships the golden `.txt` fixtures in the sdist.

## Acceptance (from the issue)

| Check | Result |
|---|---|
| `agentbreeder --help` and every subcommand's `--help` byte-identical to before | 44/44 identical on 3.12 and on 3.13 (installed wheel, below) |
| Full test suite green | 1868 passed, 32 skipped on `main` (1856 on the branch at PR time; `main` gained #817's tests); coverage 97% with `--cov-branch` (`cli.py` 99%, one partial branch) |
| `cli.py` under 300 lines | 244 |

The golden files were captured from the pre-move code (e1b51f5, a branch commit squashed into
#840) and pass on 3.11, 3.12 and 3.13 there and after the move. Before merge, wheels built before
and after the move were each installed in a clean sandbox: both matched all 44 golden help files,
and they gave identical output and exit codes for `quickstart --dry-run` and six error paths (only
the sandbox path differed). Docker was not running then, so `--dry-run` exited 1 with FAIL Docker
on both; the passing run is below.

## Wheel from main (clean sandbox)

`main` at 7852936, no tracked changes. Wheel built with `python -m build --wheel` from the
checkout (6 s). Every command then ran under `env -i` with an empty `HOME`, `TMPDIR` inside the
sandbox, `PIP_NO_CACHE_DIR=1` and fresh venvs (stdlib `venv`, `pip install <wheel>`).

Docker: the empty `HOME` has no Docker Desktop context, and AgentBreeder refuses `DOCKER_HOST`
(only local contexts), so the script created a local context named `sandbox` in the sandbox `HOME`
pointing at the same engine socket.

| Step | Result | Time |
|---|---|---|
| Python 3.12 venv + install | `agentbreeder --version` → `3.0.0a2` | 7 s |
| 44 `--help` screens vs golden (3.12) | 0 differ | — |
| Python 3.13 venv + install | `3.0.0a2` | 7 s |
| 44 `--help` screens vs golden + `py313/` (3.13) | 0 differ | — |
| `agentbreeder quickstart --dry-run` | exit 0; PASS Docker, PASS Compose, valid manifest | 1 s |
| `agentbreeder quickstart` (real Docker) | exit 0; 5/5: build, gate + deploy + health, invoke, retry reused the deployment, container removed | 5 s |

The script passed three times; the figures are from the last run, which also listed the sandbox
`HOME`. The two earlier passing runs gave the same results (timings within 1 s). Two attempts
before them stopped at `quickstart`: the empty `HOME` had no Docker context yet, and a
`DOCKER_HOST` workaround was refused as designed. The script then gained its own context.

Error and empty-state paths, in the order run. The first six match the pre-merge comparison of
the wheels built before and after the move; the `deploy` row was only observed on `main`:

| Command | Exit | Last line |
|---|---|---|
| `validate /nonexistent.yaml` | 2 | `Validation failed: Cannot read manifest; …` |
| `status abc` | 2 | `[DEPLOYMENT_ERROR] No AgentBreeder state at <sandbox>/home/.agentbreeder/local; check --state-dir` |
| `list` | 0 | `No deployments` |
| `audit verify` | 2 | `[DEPLOYMENT_ERROR] No AgentBreeder state at …` |
| `secret set x --force` | 2 | `agentbreeder: error: --force needs --generate` |
| `services logs a b c` | 2 | `agentbreeder: error: logs takes [DIR] NAME` |
| `deploy …/http-agent/agent.yaml --local` | 2 | `[CONTEXT_ENVIRONMENT_UNKNOWN] Unknown or missing --environment …` |

From the `main` checkout (editable venv, Python 3.12.8): `AGENTBREEDER_DOCKER_TEST=1 pytest
tests/test_local_integration.py` (without `--cov`): 2 passed in 6.7 s; full suite as above.

## Leak scan

106 files in the sandbox `HOME`, `TMPDIR` and captured outputs, scanned for the values in
`~/.env` (6 values of 12 or more characters, compared without printing), `sk-…` and `AIza…` key
patterns, and the real home path: 0 key values, 0 key patterns. One real-home-path hit: the
`sandbox` Docker context's `meta.json`, written by the verification script itself (it holds the
engine socket path); no file AgentBreeder wrote contains it. The venvs and the wheel were not
scanned.

## Teardown

Sandbox `HOME` before deletion held 10 entries:

- 6 are the script's Docker context: `.docker/`, `.docker/config.json`, `.docker/contexts/`,
  `contexts/meta/`, `meta/<context-id>/` and its `meta.json`.
- 4 are AgentBreeder's default state: `.agentbreeder/`, `.agentbreeder/local/`,
  `local/deployments.sqlite3` and `local/worker.lock`. They were written by the last probe,
  `deploy … --local`, which records its denied gate attempt in the audit history (`deploy` creates
  state, as before the move). The earlier `status abc` and `audit verify` ran before it and found
  no state.

Quickstart keeps its own history in a temporary directory under the sandbox `TMPDIR`, not in
`HOME`.

Docker before and after the sandbox run, and after the integration test, by count: 7 containers,
114 images, 63 volumes (networks were not counted). Quickstart reported its container removed,
and the unchanged image count is consistent with its image being removed. The sandbox directory
(venvs, `HOME`, `TMPDIR`, quickstart history) was deleted.

## Review

Claude Fable (coder: Claude Opus), read-only, two rounds, both **APPROVED**; the full record is in
the #840 PR body. Round 1 compared the old and new parsers structurally on all 44 paths and ran 53
argv probes through the old and new `main()` (identical exit codes, stdout, stderr). Its three
small notes (a `NAMES` test, a narrower patch in the forward test, the sdist fixtures) were
applied and approved in round 2.

## Notes for later lanes

Adding a command touches `COMMAND_MODULES` in `cli.py`, the top-level golden file
(`agentbreeder.txt` and `py313/agentbreeder.txt`; regenerate with `AGENTBREEDER_UPDATE_GOLDEN=1`
on 3.12, then 3.13) and, for a command with `--stack --environment`, the name tuple in
`_use_stack`. Patch `Docker`/`load_operator_config` in the command module that uses them;
`cli.Docker` covers only the commands that go through `run_stateful`.
