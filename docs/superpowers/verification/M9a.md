# M9a verification: import and fork, CLI half (#825)

Status: **CLI half verified from `main`; the tool-import confirm is partial (a CLI note, no
confirm yet)** (23ceba6, after #887 and #894 merged, 2026-11-13,
macOS 13.7.8 x86_64, Docker Desktop engine 27.5.1). The results:
- A prompt imported from another directory resolved: `list` and `show` found it, and `verify`
  passed with it present. `build`
  locked it with the import's digest, and the built agent image carries its text.
- A built-in service forked into the project, and an edited copy published as a bump. A real
  `services up` then ran the edited version and served the edited file.
- A tool import printed the "code that runs inside agents" note and returned `runs_code: true`.
- The refusals tried all behaved as documented: `FORK_UNCHANGED`, `REF_NOT_FOUND`,
  `VERSION_EXISTS` and `DIGEST_MISMATCH`.
- The leak scan found 0 hits. Docker containers, volumes, images and networks matched the pre-run
  snapshot.

The **console half is open**: lane B builds the Import and Fork actions and the three-tier confirm,
so the acceptance line "a tool import asks for the code-execution confirm" is met only by the CLI's
stderr note here. The Chrome click-through is open too. One finding, not an acceptance failure:
#898 (`publish --json` reports `root_kind: "path"` for the project's own `./registry`).

This is on `main`, not in a published preview.

## What changed (CLI, lane C)

| PR | Merged | Content |
|---|---|---|
| #887 | d8fffcc | `registry import PATH [--as NAME] [--version X.Y.Z] [--project DIR] [--json]`: one staged copy that every check runs on, a rewrite of only the top-level `name:`/`version:` lines, `REF_CONFLICT` against the other search roots, and the stderr note (`runs_code`). Plus `--dry-run` and `--expect-digest` for `import` and `publish` (S-04) |
| #894 | 1463cf1 | `registry fork REF [--as NAME] [--version X.Y.Z]` (same flags as `import`, plus `FORK_UNCHANGED` and `REF_NOT_FOUND`) |

Both PRs had different-model review (Sonnet, read-only) until it converged. Their bodies carry the
review tables. The full suite was not rerun for this record. #894's merge-of-main head (4871a38)
passed 3038 tests, 34 skipped, on 3.13, per its PR body.

## Wheel from main (clean sandbox)

- **Source:** `main` at 23ceba6 (after #894 and #896), exported with `git archive`, so no
  working-tree files.
- **Wheel:** built with `python -m build --wheel` from that export in a Python 3.12 build venv:
  `agentbreeder-3.0.0a2-py3-none-any.whl`, sha256
  `a09fe2130f63605523f07b207c6a13044e1aced5c84cda32502b5f5660f6101d`.
- **Sandbox:** every `agentbreeder` command ran under `env -i` with a fresh sandbox `HOME` (0700)
  and `TMPDIR` inside it, `PIP_NO_CACHE_DIR=1`, in a fresh Python 3.12 venv holding only the wheel
  (`agentbreeder --version` → `3.0.0a2`).
- **Docker:** AgentBreeder refuses `DOCKER_HOST`, so a context `sandbox` was created inside the
  sandbox `HOME`, pointing at the engine socket. The `docker` and `curl` checks ran from the host
  shell against the same engine.
- **Keys:** only the `GEMINI_API_KEY` line of `~/.env` was copied, by a script, into a 0600 file
  in the sandbox `HOME`, for the stack's LiteLLM. It was used by `services up`, never printed, and
  deleted at teardown.
- **Projects:** `new agent m9a-demo --model gemini` wrote `<project>`. A separate directory,
  `<shared>`, outside the project, held the versions to import.

Three scripts ran the steps: part A (import), part B (fork) and part C (stack and build). For each
step a script ran, stdout and stderr were saved to files, and the exit code and wall time to a step
log. Step names are the scripts' own; A02, B06 and B12 (scaffold and `show` calls) are not rows
below.

Saved by hand, outside the step log, with output only (no exit code or time): A13 (`build` without
`--stack`), B14 (the `1.0.3` publish), C04 (the `curl` body), C05 (the image grep) and C06 (`services
down`). Also saved: the lock before the build, `stack.yaml` before the edit, and the Docker snapshots
before and after.

Seen only in the terminal, not saved: the wheel build and its sha256, the macOS and Docker versions,
the `curl` status code, the `lock` diff and `stack.lock` check (both files are kept), the `docker
rmi`, the stray-container removal, the `lsof` check and the leak-scan counts.

## A: import a prompt from another directory

`registry new prompt team-voice --dir <shared>` scaffolded `<shared>/prompt-team-voice`, and its
`prompt.md` was replaced with one line ending "(M9a import check)".

| Step | Command | Exit | Time | Result |
|---|---|---|---|---|
| A01 | `new agent m9a-demo --model gemini --dir <project>` | 0 | 0.6 s | project with `agent.lock` |
| A03 | `registry import <shared>/prompt-team-voice --project <project> --dry-run --json` | 0 | 0.5 s | `prompt/team-voice@0.1.0`, `sha256:37512e0e…a7c`, `dry_run: true`, `runs_code: false`, `files[]` with the manifest and prompt text. Stderr: "Note: a prompt version steers the agent. Review it before an agent uses it." |
| A04 | same, `--expect-digest sha256:37512e0e…a7c --json` | 0 | 0.5 s | written to `<project>/registry/prompt/team-voice/0.1.0`, `root_kind: "project"`, same digest |
| A05 | `registry list prompt/ --project <project>` | 0 | 0.5 s | `prompt/m9a-demo-prompt@0.1.0` and `prompt/team-voice@0.1.0`, both in `<project>/registry` |
| A06 | `registry show prompt/team-voice@0.1.0 --project <project> --json` | 0 | 0.5 s | same digest, `root_kind: "project"` |
| A07 | `registry verify --project <project>` | 0 | 0.5 s | `OK: every registry version passed its safety and manifest checks` |
| A08 | the same import again | 2 | 0.5 s | `[VERSION_EXISTS] prompt/team-voice@0.1.0 is already published at …; bump the version` |
| A09 | `… --as house-voice --version 1.0.0` | 0 | 0.5 s | `prompt/house-voice@1.0.0 sha256:59a3e41b… <path> (from prompt/team-voice@0.1.0)` |
| A10 | edit `prompt.md` in `<shared>`, then `… --version 0.2.0 --expect-digest sha256:37512e0e…a7c` | 2 | 0.5 s | `[DIGEST_MISMATCH] prompt/team-voice@0.2.0 would be written as sha256:ba5eccbc…, not the reviewed sha256:37512e0e…: the source changed after the preview; review it again`. Nothing written |

**Resolvable.** `agent.yaml`'s prompt ref was changed from `prompt/m9a-demo-prompt@0.1.0` to
`prompt/team-voice@0.1.0`. After `services up` (part C), `build agent.yaml --stack <project>
--environment dev` exited 0 with `Lock: <project>/agent.lock (updated)`. The only lock change was
that entry: `prompt/team-voice@0.1.0` with `sha256:37512e0e1cf5153ee938551fd9fad077d36ae32fe2947a96ff29fed01694da7c`,
the import's digest. The built image (`sha256:0fedfdf6…6475`) holds
`/app/artifacts/prompt/team-voice/prompt.md` with the imported line. Without `--stack`, `build`
stopped with `[CONTEXT_ENVIRONMENT_UNKNOWN]` and pointed at `--stack`, as expected.

## B: fork a built-in service, edit a copy, publish a bump

| Step | Command | Exit | Time | Result |
|---|---|---|---|---|
| B01 | `registry fork service/echo@1.0.0 --project <project>` | 2 | 0.5 s | `[FORK_UNCHANGED] forking service/echo@1.0.0 unchanged would only copy it; pass --as NAME or --version X.Y.Z …` |
| B02 | `registry fork service/echo@9.9.9 --version 9.9.10 …` | 2 | 0.5 s | `[REF_NOT_FOUND] service/echo@9.9.9 not found in <project>/registry, <venv>/…/builtin_registry` |
| B03 | `registry fork service/echo@1.0.0 --version 1.0.1 … --dry-run --json` | 0 | 0.5 s | digest `sha256:310c56c7…6e28` |
| B04 | same, `--expect-digest sha256:310c56c7…6e28 --json` | 0 | 0.5 s | `service/echo@1.0.1`, `source_ref` `service/echo@1.0.0`, `source` the built-in version directory, `root_kind: "project"`, `runs_code: true`. Stderr: "Note: a service version is code that runs inside agents with their privileges. Review it before an agent uses it; it takes effect only after `release` passes." |
| B05 | `registry fork service/echo@1.0.0 --as m9a-echo …` | 0 | 0.7 s | `service/m9a-echo@1.0.0 sha256:530fd058… <path> (from service/echo@1.0.0)` |
| B07 | `registry diff service/echo@1.0.0 service/echo@1.0.1 …` | 0 | 0.5 s | only `manifest.yaml` differs: `name: "echo"` and `version: "1.0.1"` (the rewrite replaces only these two lines; the diff shows it writes both quoted) |
| B08 | copy the fork to `<work>/echo-edit`, replace `files/index.html`, set `version: "1.0.2"`, then `registry publish <work>/echo-edit --to <project>/registry --json` | 0 | 0.5 s | `service/echo@1.0.2`, `sha256:5e6f545a…51f5` (but `root_kind: "path"`: see #898) |
| B09 | `registry diff service/echo@1.0.1 service/echo@1.0.2 …` | 0 | 0.5 s | `files/index.html` (the edit) and the `version:` line |
| B10 | `registry list service/ …` | 0 | 0.5 s | `echo@1.0.1`, `echo@1.0.2`, `m9a-echo@1.0.0` in `<project>/registry`; built-in `echo@1.0.0` unchanged |
| B11 | `registry verify …` | 0 | 0.6 s | `OK …` and `Built-in content matches the shipped manifest (corruption check).` |
| B13 | `registry new tool m9a-lookup --dir <shared>`, then `registry import <shared>/tool-m9a-lookup --project <project> --json` | 0 | 0.5 s | `tool/m9a-lookup@0.1.0`, `runs_code: true`, and the "code that runs inside agents" note on stderr |

**Editable and used.** `stack.yaml` gained a service `echo: {ref: service/echo@1.0.2, with:
{label: m9a}}` beside the generated postgres and litellm.

## C: the stack

| Step | Command | Exit | Time | Result |
|---|---|---|---|---|
| C01 | `services up <project> --environment dev --env-file <keys> --yes` | 0 | 56.9 s | echo (init and bootstrap ran), postgres and litellm created |
| C02 | `services status <project> --json` | 0 | 0.7 s | all three `running` |
| C03 | `build agent.yaml --stack <project> --environment dev` | 0 | 134.2 s | see A, "Resolvable" |
| C04 | `curl http://127.0.0.1:<echo port>/index.html` (host shell) | not saved | not timed | `200` (terminal only), body `<!doctype html><title>m9a</title><p>forked and edited (M9a)</p>` |

`stack.lock` records `service/echo@1.0.2` with `sha256:5e6f545a…51f5` and `source: registry`, the
digest `publish` printed.

## Acceptance (from the issue)

| Check | Result |
|---|---|
| importing a prompt from another directory makes it resolvable | PASS: `list` and `show` found it (`verify` passed with it present), then `build` locked it with the import digest, and the image holds its text |
| forking a built-in service version gives an editable project copy | PASS: `fork … --version 1.0.1` wrote a project-owned version. Like every version it is immutable, so it was edited the documented way: copied out of the registry, edited, bumped and published as `1.0.2`, which ran under `services up` and served the edit |
| a tool import asks for the code-execution confirm | partial: CLI only. `import` prints the "code that runs inside agents" note and `runs_code: true`. The confirm dialog is the console half (lane B), not built yet |

Flows that need a model key: only `services up` (LiteLLM's config imports `GEMINI_API_KEY`). No
model call was made: `build` does not call the model, and `release` was not run.

## Browser click-through

Not run: the console half (Import and Fork actions, the three-tier confirm) is lane B and not
built yet.

## Leak scan and teardown

- **Leak scan:** 118 files (every saved output and the whole sandbox `HOME`, after the key file
  was deleted) were scanned for the Gemini key value, for the `AIza…`/`sk-…` patterns, and (saved
  outputs only) for the real home path. 0 hits for each (counts seen in the terminal, not saved).
- **Teardown:** `services down <project> --purge --yes` stopped the stack and removed its two
  volumes (`echo-data`, `postgres-data`) and its state directory. The agent image was removed with
  `docker rmi`.
- **What was left:** no container, volume or network named `m9a`, and nothing listening on the
  stack's ports. Docker containers, volumes, images and networks matched the snapshot taken before
  the run: 0 lines of difference each. The images the stack pulled were already present.
- **A stray container:** a first image check (`grep -r` over the whole image) ran past the shell
  timeout. It was stopped and its container removed by hand before the snapshot.

## Notes

- `publish` has no `--project`, so a version published into the project's own `./registry` reports
  `root_kind: "path"`. That happened with `--to <project>/registry` and with the default `--to`
  from the project directory (B14: a `1.0.3` copy that nothing used; only its output
  was saved). `import` and `fork` report `project` correctly. Follow-up issue: #898.
- The forked manifest quotes both `name:` and `version:`, even when only one changes (seen in the
  B07 diff; `docs/registry.md` says only that these two lines are replaced).
- Not run: `release` (the acceptance does not need a released agent) and Linux.
