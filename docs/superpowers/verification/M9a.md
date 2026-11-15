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


## Addendum: the console half (#976, #981)

Status: **verified from `main`; the console was driven by Playwright against a real stack and
recorded as screenshots, saved page text and the console API's answers, not video** (d123d4f,
2026-11-14 local time (2026-11-15 UTC), macOS 13.7.8 x86_64, Docker Desktop engine 27.5.1). This
closes the CLI half's open console item: the "this code runs in agents" confirm is now asked by
the console, and its server refuses without it. The Chrome click-through is still not run. The
results:
- **A prompt imported from another directory by path in the console resolves.** Registry ▸ Import
  reviewed and imported `prompt/team-voice@0.1.0` from a directory outside the project. After
  `agent set-ref` moved the agent to it, `build` locked it with the digest the console's review
  showed, and the built image holds its text.
- **A built-in service version forks into an editable project copy, under the next patch and
  under another name.** The console forked `service/postgres-pgvector@1.2.0` to `1.2.1`, and
  under another name (the console's "Fork under another name", the CLI's `--as`) to
  `service/m9c-pg@1.2.0`, which opened its new page. Both showed the code confirm before the fork;
  the first fork's button was recorded disabled until the box was ticked (the second's state was
  not recorded). The `1.2.1` copy was then changed with `registry edit` and published as
  `1.2.2`.
- **A tool import asks for the "this code runs in agents" confirm, and the server refuses without
  it.** Import stayed disabled until the box was ticked. A write request with `code_confirmed`
  removed was refused `400 CODE_NOT_CONFIRMED`, and nothing was written. With the confirm, it was
  imported. The server's refusal was tried for this tool import only, not for a fork.
- The leak scan found 0 hits. Teardown left nothing of this run (details below).

One finding, not an acceptance failure: #990 (`registry edit`'s `Next:` line suggests
`agent set-ref` for a service version, which then exits 2).

### What changed (console, lane B)

| PR | Merged | Content |
|---|---|---|
| #976 | f0606ef | Registry ▸ Import: review, then "Import as REF" with the reviewed digest; the three-tier confirm for tool, service, eval, MCP and runtime versions (`CODE_NOT_CONFIRMED`) and the "steers the agent" note for prompts and knowledge |
| #981 | d1cea3b | Fork any version from outside the project registry, under another name, with the same code confirm |

The full suite was not re-run for this addendum.

### Wheel and sandbox

- **Wheel:** from `main` at d123d4f, exported with `git archive` and built with
  `python -m build --wheel` (`agentbreeder-3.0.0a2-py3-none-any.whl`, sha256
  `812928ea8ab178bde654cbf1770f90370c18f2624245c56c8cefac8ebfef7f25`). The same wheel was used for
  the M6 record.
- **Sandbox:** as for the CLI half: `env -i`, a fresh sandbox `HOME` (0700) with `TMPDIR` inside
  it, `PIP_NO_CACHE_DIR=1`, a fresh Python 3.12 venv with only the wheel and `[eval]`, and a Docker
  context `sandbox` in the sandbox `HOME`. Only the `GEMINI_API_KEY` line of `~/.env` was copied, by
  a script, into a 0600 file for `services up`. It was never printed, and was deleted at teardown.
- **Project:** `new agent m9c --model gemini --dir <P>` (exit 0, 1.1 s). In the console it is
  `proj`. A separate directory `<S>`, outside the project, held `registry new prompt team-voice`
  (its `prompt.md` replaced by one line ending "(M9a console import check)") and
  `registry new tool m9c-lookup` (as scaffolded).
- `services up <P> --environment dev --env-file <key> --yes` (exit 0, 70.1 s) started postgres
  and litellm, for the build.

A wrapper saved each `agentbreeder` command's stdout, stderr, exit code, start time and wall time.
Times are UTC.

### The console (Playwright against the real stack)

`agentbreeder up --no-browser --port 8092` ran from the sandbox wheel. Playwright (Python,
`playwright==1.64.1`, system Chrome) used the one-time link and took full-page screenshots. It
also saved every `/registry/import` and `/registry/fork` request body with its status and coded
error. A one-time link works once, so `up` was started once per script, four times in all. The
first script stopped at the scope picker, because it looked for the agent's name (`m9c (dev)`),
but the console names the project after its directory (`proj (dev)`). That run had already added
the project, and nothing else. A second script stopped after part A: the "Move agent to this
version" card is not on the page of a prompt the agent does not use yet (below). The third script
ran parts B and C, and the fourth part D.

**Part A: import a prompt by path (02:49:24–02:49:33).** `#/registry/import`, the version
directory `<S>/prompt-team-voice`:

| Step | Result |
|---|---|
| **Review import** | "Review: prompt/team-voice@0.1.0", "Imports 2 files into this project's registry as prompt/team-voice@0.1.0."; From `<S>/prompt-team-voice`; Into "project", `<P>/registry/prompt/team-voice/0.1.0`; Version hash `sha256:9ef23efa…7bfbb`; both files' text; "A prompt version steers the agent: review it before an agent uses it." No code confirm |
| **Import as prompt/team-voice@0.1.0** | "Imported prompt/team-voice@0.1.0 into this project's registry. An agent uses it only after its agent.yaml references it and a release passes." and a link, Open prompt/team-voice |
| Open prompt/team-voice | the version list: `0.1.0`, latest, Root project, Verify ✓ Passed, "not used"; the files; Edit 0.1.0 |

The two requests: the review `{path}` and the write `{path, digest: sha256:9ef23efa…}`. Both were
answered 200 with `prompt/team-voice@0.1.0`.

**Resolves (CLI, 02:51:42–02:55:20).**

| Step | Command | Exit | Time | Result |
|---|---|---|---|---|
| A1 | `registry list --project <P>` | 0 | 1.9 s | `prompt/team-voice@0.1.0` and `tool/m9c-lookup@0.1.0` in `<P>/registry` |
| A2 | `registry verify --project <P>` | 0 | 2.6 s | "OK: every registry version passed its safety and manifest checks", and the built-in corruption check |
| A3 | `agent set-ref --project <P> prompt/m9c-prompt@0.1.0 prompt/team-voice@0.1.0` | 0 | 1.4 s | the diff: `-prompt: {ref: prompt/m9c-prompt@0.1.0}` / `+prompt: {ref: prompt/team-voice@0.1.0}` |
| A4 | `build <P>/agent.yaml --stack <P> --environment dev` | 0 | 184.7 s | "Lock: … agent.lock (updated)". The lock now pins `prompt/team-voice@0.1.0` at `sha256:9ef23efa…7bfbb`, the digest of the console's review, where it had pinned `prompt/m9c-prompt@0.1.0` |
| A5 | `docker run --rm --entrypoint sh <image> -c 'grep -r …'` (host shell) | not saved | not timed (output only) | `/app/artifacts/prompt/team-voice/prompt.md`, holding the imported line |

Afterwards, the console's page for `prompt/team-voice` (part D) showed "declared in agent.yaml;
locked in agent.lock" and "The project's agent.yaml uses this version."

The move was made with the CLI's `agent set-ref`, not in the console. The console's "Move agent to
this version" card shows on a version of a name the agent already uses (as M10 used it, from
`hello-prompt@0.1.0` to `0.1.1`). It is not on another name's page, so it did not show for
`team-voice` (the second script stopped waiting for it).

**Part B: import a tool: the confirm, and the server's refusal (02:50:32–02:50:45).**
`#/registry/import`, `<S>/tool-m9c-lookup`:

| Step | Result |
|---|---|
| **Review import** | "Review: tool/m9c-lookup@0.1.0", "Imports 3 files …" (`manifest.yaml`, `src/tool_m9c_lookup.py`, `tool.yaml`); the fieldset **"This code runs in agents"**: "A tool version is code. Building and releasing an agent that uses it runs this code as you, with the stack's model key and database. The eval gate is a quality gate, not a security review."; the box "I reviewed this code and trust it to run in agents", unticked. **Import as tool/m9c-lookup@0.1.0 was disabled** |
| Tick the box | the button enabled |
| Click Import, with Playwright removing `code_confirmed` from the write request on its way out | the request `{path, digest}` was answered **400 `CODE_NOT_CONFIRMED`** "Confirm that you reviewed this code: it runs in agents"; the page said "Tick the box to confirm you reviewed this code, then import." and kept the review. Nothing was written: the next write of the same version succeeded, where an existing version is `VERSION_EXISTS` |
| Click Import again, unmodified | `{path, digest, code_confirmed: true}`, 200: "Imported tool/m9c-lookup@0.1.0 into this project's registry. …" |

The refusal comes from the server's own check, not the browser's: the page had the box ticked,
and only the request was changed.

**Part C: fork a built-in service version (02:50:48–02:51:14).**
`#/registry/services/postgres-pgvector`, where the versions are `1.2.0` (latest), `1.1.0` and
`1.0.0`, all built in. The starter's `stack.yaml` uses `1.1.0`.

| Step | Result |
|---|---|
| The **Fork** card on `1.2.0` | **Fork into this project as 1.2.1** |
| Review | "Review: fork 1.2.0 → 1.2.1", "Copies 3 files into this project's registry as service/postgres-pgvector@1.2.1; only the manifest's version line changes." (`files/init.sh`, `manifest.yaml`, `service.yaml`); the "This code runs in agents" fieldset (for a fork: "Forking copies this service version unchanged into a version the project owns. …"); **Fork as 1.2.1 disabled** until the box was ticked |
| Tick, **Fork as 1.2.1** | the write `{ref: service/postgres-pgvector@1.2.0, version: 1.2.1, digest, code_confirmed: true}`, 200, `service/postgres-pgvector@1.2.1`. The script's screenshot came 2 s before this answer, so the page's own message after the fork was not captured; the new version is in part D and the CLI list |
| Fork under another name `m9c-pg`, review | "Review: fork 1.2.0 → service/m9c-pg@1.2.0", "… only the manifest's name line changes."; the same confirm fieldset and box (the button's state was not recorded) |
| Tick, **Fork as service/m9c-pg@1.2.0** | 200, `service/m9c-pg@1.2.0`, and the console opened `#/registry/services/m9c-pg`: `1.2.0`, latest, Root project, ✓ Passed |

**Editable (CLI, 02:55:25–02:55:33).**

| Step | Command | Exit | Time | Result |
|---|---|---|---|---|
| C1 | `registry edit service/postgres-pgvector@1.2.1 --set files/init.sh <edited copy> --project <P>` | 0 | 0.7 s | the diff of `files/init.sh` (one added comment line) and the manifest's version line; `service/postgres-pgvector@1.2.2 sha256:72db6f56…dcc2 … (from service/postgres-pgvector@1.2.1)` |
| C2 | `registry list service/postgres-pgvector --project <P>` | 0 | 0.9 s | `1.2.1` and `1.2.2` in `<P>/registry`; `1.0.0`, `1.1.0` and `1.2.0` built in |
| C3 | `agent set-ref --project <P> --dry-run service/postgres-pgvector@1.2.1 service/postgres-pgvector@1.2.2`: the arguments of the `Next:` line C1 printed, with `--project` and `--dry-run` added | **2** | 2.3 s | `[REF_NOT_DECLARED] agent.yaml does not declare service/postgres-pgvector@1.2.1 in a ref field`: services are referenced from `stack.yaml`. Filed as #990 |

The forked service was not used by `services up` here. The CLI half already ran an edited fork
under `services up` (B08 and C01 above).

**Part D: the registry after (02:56:01–02:56:29).**

| Page | Result |
|---|---|
| `#/registry/prompts/team-voice` | `0.1.0`, project, ✓ Passed, "declared in agent.yaml; locked in agent.lock" |
| `#/registry/services/postgres-pgvector` | `1.2.2` latest and `1.2.1`, Root project, ✓ Passed; `1.2.0`, `1.1.0` and `1.0.0` built in |
| `#/registry/tools/m9c-lookup` | `0.1.0`, project, ✓ Passed; Functions: `echo_m9c_lookup(text: str) -> str` |

The screenshots (15, plus 2 where the first and second scripts stopped) are not committed or attached to #825,
because they show the sandbox's absolute paths.

### Acceptance, the console half

| Check | Result |
|---|---|
| Importing a prompt from another directory by path in the console, and it resolves | **PASS** (part A; then A3 to A5: the lock pins the review's digest and the image holds the text) |
| Forking a built-in service version into an editable project copy | **PASS** (part C: `1.2.1` and `service/m9c-pg@1.2.0`; C1 edited `1.2.1` into `1.2.2`) |
| Forking one under `--as NAME` (the console's "Fork under another name") | **PASS** (part C: `service/m9c-pg@1.2.0`, its page opened) |
| Importing a tool asks for the "this code runs in agents" confirm, and the server refuses without it | **PASS** (part B: the button disabled until ticked; the write without `code_confirmed` refused `400 CODE_NOT_CONFIRMED`; the refusal was tried for the tool import only) |
| Chrome click-through, GIF attached to #825 | **not run** (Playwright screenshots instead; nothing attached, because they show sandbox paths) |

A model key was used only by `services up`. No model call was made: `build` does not call the
model, and `release` was not run.

### Leak scan and teardown

- **Leak scan:** before teardown, a stdlib script searched 122 files: every saved output (the
  console's `up` output, the Playwright logs and the request log included), the console's state
  directory, the logs of the stack's 2 containers and `<P>/deploy`. It looked for the literal,
  base64 and URL-quoted forms of every `~/.env` value of 12 or more characters (6) and of every
  stored secret in the 8 files under the sandbox's `~/.agentbreeder` `secrets` paths, and for
  `/Users/`. **0 hits.** The counts were printed to a saved file.
- **Teardown:** the console was stopped; `services down <P> --purge --yes` (exit 0, 8.7 s) removed
  the stack, its volume and its state; the agent image A4 built was removed with `docker rmi`;
  the key file was deleted.
- **What was left:** nothing of this run. Compared with the snapshot taken before the run, the
  lists differed only by another session's objects: a stack `kbhelper-7631f4a8` (two containers,
  created 02:50:00 and 02:50:10, its volume and three networks) and one agent container with its
  image (created 02:56:08 and 02:55:36). Their names and labels are not this run's (`m9c-f5ddd98c`), and they were left alone.
