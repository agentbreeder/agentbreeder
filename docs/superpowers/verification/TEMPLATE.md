# <Milestone> verification: <title> (#<issue>)

<!--
Copy this file to docs/superpowers/verification/<milestone>.md (for example M0b.md) and fill it
from a wheel built from `main` in a clean sandbox, as the epic plan's "How every milestone is run"
step 4 asks (docs/superpowers/plans/2026-11-11-console-epic.md). M0a.md is a filled example.

Rules:
- Record only what ran. A check that did not run says "not run" and why; one that ran in part
  says "partial" and what was left out. Never round a partial result up.
- Never paste a provider key, a one-time link, a session token or a home-directory path. Write
  `~` or `$SANDBOX` instead of a real home path.
- Delete these comments when the record is filled in.
-->

Status: **<verified from `main` | partial | not run>** (<commit>, after #<PR> merged, <date>,
<OS and version>, <Docker engine version>). <One or two sentences: which acceptance checks
passed, which did not run, and whether anything was left running or leaked.>

This is on `main`, not in a published preview.

## What changed (<n> PRs)

| PR | Merged | Content |
|---|---|---|
| #<PR> | <short sha> | <what the PR added, with the spec and security IDs it closes> |

<How each PR was reviewed (different-model review, which model, read-only) and where the review
tables are. The full suite on `main` (<sha>): <passed>, <skipped> (<why skipped>), in <time>.
Coverage: <measured value, or "not measured in that run">.>

## Wheel from main (clean sandbox)

`main` at <sha>, no tracked changes. Wheel built with `python -m build --wheel`
(`<wheel file name>`). <How the sandbox was set up: every command under `env -i`, a fresh `HOME`,
`TMPDIR` inside the sandbox, `PIP_NO_CACHE_DIR=1`, fresh venvs (Python version), any published
version installed for comparison. AgentBreeder refuses `DOCKER_HOST`, so a Docker context was
created inside the sandbox `HOME` pointing at the engine socket.>

<The scripts used (names and what each does), the total time, and which outputs were saved.>

| Check (in the order run) | Result | Time |
|---|---|---|
| <check> | <observed output, quoted or summarised> | <s> |

## Acceptance (from the issue)

One row per acceptance line in the milestone issue, in the issue's words.

| Check | Result |
|---|---|
| <acceptance line> | <PASS | FAIL | partial: … | not run: …> |

Flows that need a model key run here, not in CI: <which ran, with which provider and model, or
"none in this milestone">.

## Browser click-through

<Claude in Chrome (or "not run" and why): the path clicked, the browser and version, what was
seen at each step. The GIF is attached to #<issue>, not committed, and was checked for home paths
and keys before upload.>

## Leak scan and teardown

<What was scanned (every saved output, job log, server log), for what: no `~/.env` value of 12 or
more characters (<n> values) and no one-time link. Key files left under the sandbox `HOME`.
Processes and ports left. Docker containers, networks, volumes and images compared with the
snapshot taken before the run. State each result; say which lines were not saved.>

## Notes

- <Anything a reader needs to interpret a result: simulated state, a check that passed for an
  incidental reason, a gap that belongs to a later milestone and its issue.>
