# Docker rerun: `runtime/adk@1.4.1` and `runtime/adk@1.5.1` (#1190, PR #1194)

Status: **passed, on macOS 13.7.8 x86_64 only (2026-11-20).** Both new versions build with the
new pins, their conformance reports are identical check for check to their base versions
(1.4.0 and 1.5.0, run in the same session), Northwind passes 61/61 on 1.5.1, and the Phoenix
span tree and attribute keys are unchanged. arm64 and `kind` were not run.

What this does **not** change: neither version is added to `agentspec.RUNTIMES_RUN` or
`VERIFIED`, and the starter, Northwind and the console's `STARTER_RUNTIME` are not repinned. The
owner accepted the google-adk 2.12.0 → 2.14.0 move and the legacy telemetry schema opt-in on
2026-11-20 (below); adding the versions to `RUNTIMES_RUN` and any repin are #1198.

## Setup

| | |
|---|---|
| Host | macOS 13.7.8 x86_64, Docker Desktop engine 27.5.1 (4 CPUs, 7.66 GiB), Resource Saver off; nothing else on the engine |
| Wheel | this branch merged with `origin/main` at `02e99275` (before commit), exported with `git stash create` + `git archive` (tracked files only), `pip wheel --no-deps`: `agentbreeder-3.0.0a3-py3-none-any.whl`, sha256 `d8d657cc…55f8` |
| Python | 3.12.8, a fresh venv with `[dev,eval]` |
| Sandbox | `env -i` with a fresh `HOME` and `TMPDIR`, `USER=demo`, `PIP_NO_CACHE_DIR=1`, a Docker context created in the sandbox `HOME` on the current engine's socket |
| Keys | a 0600 file with only `GEMINI_API_KEY` and `TAVILY_API_KEY` from `~/.env`; deleted at the end |
| Project | `agentbreeder example northwind` into the sandbox, unchanged (`runtime/adk@1.5.0`); each other version is a copy beside it with only the `runtime:` line changed. Northwind in the repo is not repinned |
| Stack | Northwind's `stack.yaml` (`service/litellm@1.0.0`, `phoenix@1.0.0`, `postgres-pgvector@1.2.0`), `services up --environment dev`, 23:10–23:23 UTC (image pulls) |

## Results

| Step (UTC) | What | Result |
|---|---|---|
| 23:23–23:27 | Baseline: build Northwind on `runtime/adk@1.5.0`, `conformance` | `sha256:8cbde02a…`; `fail` (exit 1): **22 pass, 4 fail, 8 not run, 5 not applicable**; the four F2a gaps |
| 23:27–23:29 | Baseline: the same on `runtime/adk@1.4.0` | `sha256:c9b7771f…`; the same counts and the same four |
| 23:29–23:37 | `AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_ADK_PATCH_TEST=1 pytest -q -s tests/test_runtime_adk_otel_patch_docker.py` | **2 passed** in 490 s |
| | └ 1.4.1: build, installed pins, conformance | `sha256:e0710e67…`; google-adk 2.14.0, opentelemetry-api and -sdk 1.45.2 in the image; 22/4/8/5 |
| | └ 1.5.1: build, pins, conformance, `release`, one invoke | `sha256:b5d84e12…`; pins as above; 22/4/8/5; **61/61** (`eval/northwind-golden@1.0.1`, mean correctness 1.0, p50 3,457 ms, no flaky); `agent/northwind-support@0.1.0`; invoke "Where is my latest order?" → `lookup_orders`, `runtime/adk@1.5.1`, $0.0036855 |
| 23:41–23:47 | Each of the four images deployed alone (`deploy --local`), one identical invoke, memory, `remove` | all four 200 with `lookup_orders`; memory below |

The four failing checks on every version are the ones `F2a-adk-baseline.md` recorded for
1.3.0 and 1.5.0 (`sessions.busy`, `traces.llm_span`, `model_errors.text_logs`,
`model_errors.text_spans`). All 39 check statuses of 1.4.1 equal 1.4.0's, and 1.5.1's equal
1.5.0's.

### Image size and memory

| Version | Image | `docker images` size | Content size (inspect) | Idle, 30 s after deploy | After one invoke |
|---|---|---|---|---|---|
| 1.4.0 | `c9b7771f` | 858 MB | 195,225,862 B | 275.7 MiB | 290.3 MiB |
| 1.4.1 | `e0710e67` | 860 MB | 195,858,575 B | 276.8 MiB | 291.4 MiB |
| 1.5.0 | `8cbde02a` | 857 MB | 195,143,542 B | 276.4 MiB | 291.0 MiB |
| 1.5.1 | `b5d84e12` | 861 MB | 195,981,442 B | 277.5 MiB | 292.2 MiB |

Memory is `docker stats --no-stream` against the deployment's 512 MiB limit; one sample each.
The patch versions add about 1 MB to the image and about 1 MiB of memory.

### Phoenix

Read from Phoenix's Postgres database (project `northwind-support`), for the four identical
invokes above:

- **Span tree, the same on all four:** `agent_turn` (AGENT), `invocation`, `invoke_agent
  northwind_support` (AGENT), `retrieve_knowledge` (RETRIEVER), **two `call_llm` (CHAIN)** and
  **two `generate_content openai/support-model` (LLM)** for the two model calls, and **exactly one
  `execute_tool lookup_orders` (TOOL)** for the one tool call. So ADK 2.14 runs on the legacy
  schema (`ADK_TELEMETRY_SCHEMA_VERSION_OPT_IN=1`): the v2 schema has no `call_llm` span.
- **Attribute keys, per span name:** 1.4.1's set equals 1.4.0's and 1.5.1's equals 1.5.0's, so the
  update adds no attribute, content or other.
- **Content:** Northwind has `traces: otlp` and no guardrails, so prompt and reply text are in
  `input.value`/`output.value` and ADK's `gcp.vertex.agent.llm_request`/`llm_response`, as on the
  base versions. The case where redaction requires no text (a guardrail-blocked turn, #1106) was not
  exercised live here; its runtime tests pass on 1.5.1.
- **Secrets:** across all 584 spans, 0 contain the Gemini key's value and 0 match `sk-` followed
  by 20 or more key characters.

## The Docker test's conformance assertion (fixed in this PR)

As written, `tests/test_runtime_adk_otel_patch_docker.py` asserted that no conformance check
fails. Every ADK image fails the four v1 gaps F2a recorded (owner decision D14), so it would have
failed on 1.4.0 and 1.5.0 too. It now asserts that the failures are a subset of those four
(`ADK_V1_GAPS`); the run above used that file. After the run, the review added a second assertion,
at least the base versions' 22 passing checks (`ADK_V1_PASSES`), which the run's reports meet
(22) but which has not itself been run on Docker. The per-check equality with the base versions
was checked by hand from this run's reports.

## Spend

LiteLLM's spend log, read before the stack was purged: **$0.26011** over 262 calls
(`support-model` 117 calls $0.19726, `support-judge` 61 calls $0.06250, `support-embedding`
84 calls $0.00035). Conformance runs use their own fakes and spend nothing.

## Leak scan and teardown

- The sandbox (`HOME`, work directories, `TMPDIR`, logs) and this worktree's `tests/` and `docs/`
  were scanned for both key values: **0 files**. The key file was deleted.
- Removed: every deployment (`remove --local`; `list` empty), `services down --purge --yes` (its
  volume, state and two service images), the sandbox Docker context, then by ID the 41 images
  missing from the pre-run snapshot (23:02:46 UTC): this run's builds and layers, the conformance
  fakes, and the pulled `python`, `pgvector/pgvector`, `arizephoenix/phoenix` and
  `ghcr.io/berriai/litellm-database`. Nothing was pruned.
- After: the snapshot's 5 images (same IDs), 1 volume, 3 networks, 0 containers, default bridge
  ID unchanged, build cache 99 entries / 14.09 GB as before.

## Not run

- arm64 (no host) and `kind`.
- A guardrail-blocked turn with Presidio on 1.5.1 (the #1106 live check was run on 1.5.0 only).
- The console.

## Owner decisions (2026-11-20)

Accepted: google-adk 2.12.0 → 2.14.0, and keeping `ADK_TELEMETRY_SCHEMA_VERSION_OPT_IN=1`. Follow-ups:
#1197 (move to ADK's v2 telemetry schema before 2027-03-31) and #1198 (add 1.4.1 and 1.5.1 to
`RUNTIMES_RUN`; decide on repinning the starter, Northwind and `STARTER_RUNTIME`).

### Recommendation as made

1. **google-adk 2.12.0 → 2.14.0.** Nothing in this run moved with it: conformance, Northwind's 61
   cases, the span tree and memory match 1.5.0. Recommend accepting it.
2. **The legacy span schema opt-in.** It works: `call_llm` is present and the keys are unchanged.
   Recommend keeping it set, with a follow-up before 2027-03-31 to move the span tree and
   redaction to ADK's v2 schema in a later runtime version.
3. If both are accepted, a follow-up change may add `runtime/adk@1.4.1` and `@1.5.1` to
   `RUNTIMES_RUN` (macOS x86_64 only) and decide whether to repin the starter, Northwind and
   `STARTER_RUNTIME`. 1.4.1's run covered build, conformance and one invoke, not Northwind's evals.
