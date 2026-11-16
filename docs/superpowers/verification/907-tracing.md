# #907 verification: tracing in the CLI and the console

Status: **verified from `main`, with two defects filed** (2b4e4cf, 2026-11-15, macOS 13.7.8
x86_64, Docker Desktop engine 27.5.1). The issue's acceptance was run against a real stack:
- **A Try it turn on Northwind shows its trace with the tool calls.** The turn's Trace spans panel
  read the trace from the stack's Phoenix. It listed `execute_tool escalate_to_human` and
  `execute_tool save_customer_note`, but this first read, 2 s after the reply, was partial (6 of
  11 spans; #1010). The agent's Traces tab showed the same trace: its list row was still partial,
  and its Trace spans read 2 s later had the full 11-span tree with the same tool calls.
  `agentbreeder traces` gave the same 11-span tree.
- **An eval case's trace is reachable from the release report.** The path was: agent detail,
  **Releases**, then **Eval report** (the run of 61 cases), then a case's **Trace spans**. It read
  that case's trace, with its `lookup_orders` tool call. The trace belongs to the release's
  candidate, which `release` had already removed. `traces --trace` read the same trace.

Two defects were found and filed:
- **#1009:** a trace's token total counts every model call twice.
- **#1010:** a trace read in the first seconds after a reply can be partial (no root span). It is
  then labelled "Matched by time: this runtime does not record its deployment".

The leak scan found 0 hits. Docker was torn down to the pre-run snapshot.

This is on `main`, not in a published preview.

This is a new record, not an M7.md addendum. #907 spans the Try it panel (M7), the agent detail
and the eval report viewer (M8/M10), so it is recorded on its own.

## What changed (4 PRs)

| PR | Merged | Content |
|---|---|---|
| #931 | fd83bc6 | CLI: `agentbreeder traces`, agent traces per deployment from the stack's Phoenix |
| #940 | 6c92dde | Console: Try it shows a turn's trace as a span tree |
| #999 | 96b4f28 | Console: Traces tab on the agent detail, and eval case traces |
| #1008 | 2b4e4cf | (the `main` this ran on; no tracing change) |

Each PR's own different-model review table is in its PR body. The full suite and coverage were
not re-run for this record. The macOS and Docker engine versions were read from the terminal at
the start (`sw_vers`, `docker info`) and were not saved.

## Wheel from main (clean sandbox)

- **Source:** `main` at 2b4e4cf, exported with `git archive` (no working-tree files).
- **Wheel:** `python -m build --wheel` in a Python 3.12 build venv (6 s):
  `agentbreeder-3.0.0a2-py3-none-any.whl`, sha256
  `c4c38f3bc2fa15288b8d579fbccab7cdac4370c723cca9aa5fb1a5621c724251`. The same wheel was used
  for the #906 and M17 records.
- **Sandbox:** every `agentbreeder` command ran under `env -i`, with a fresh 0700 `HOME`,
  `TMPDIR` inside it and `PIP_NO_CACHE_DIR=1`. It used a fresh Python 3.12 venv holding only the
  wheel with `[eval]` (`--version` → `3.0.0a2`; `doctor` exit 0, 0.8 s). A Docker context
  `sandbox` was created in the sandbox `HOME`, because AgentBreeder refuses `DOCKER_HOST`.
- **Keys:** a script copied only the `GEMINI_API_KEY` and `TAVILY_API_KEY` lines of `~/.env`
  into a 0600 file in the sandbox `HOME`. Northwind's tools proxy needs the Tavily key. The file
  was never printed and was deleted after the run.
- **Console:** `agentbreeder up --no-browser` in the sandbox, driven by Playwright (Chrome) at
  1280x900, with full-page screenshots. The one-time link works once, so each script started
  its own `up`.

A wrapper saved each command's stdout, stderr, exit code, start time (UTC) and wall time.

## Steps

| Step | Command | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `example northwind <NW>` (20:14:00) | 0 | 0.9 s | |
| 2 | `services up <NW> --environment dev --env-file <keys> --yes` (20:14:22) | 0 | 331.5 s | postgres, litellm, phoenix, ops and proxy created |
| 3 | `release <NW>/agent.yaml --stack <NW> --environment dev` (20:20:00) | **0** | 191.9 s | `Released sha256:5aeee5ee…3bfe: 61/61 evals passed`, first attempt; run `20261115-122017-fbd4f1` |
| 4 | console: Try it, Traces tab, Releases, eval report (below) | | | |
| 5 | `traces --stack <NW> --environment dev --last 5` (20:24:49) | 0 | 2.0 s | one deployment `c841f364…` (`exact`): 1 trace, `d8d25d53…`, `19019 ms ok`, tools `escalate_to_human, save_customer_note, (merged tools)` |
| 6 | the same with `--json` | 0 | 2.1 s | |
| 7 | `traces --trace d8d25d53… --stack <NW> --environment dev` | 0 | 1.8 s | 11-span tree below |
| 8 | `traces --trace 4f9b0ba7… --stack <NW> --environment dev` (an eval case) | 0 | 1.9 s | deployment `3788f514…` (`exact`, the removed candidate), session `eval-20261115-122017-fbd4f1-…-r2`, tool `lookup_orders` |

Step 7's tree (`traces` text output; every name is from Phoenix, no message content):

```
agent_turn [AGENT] 19019 ms
  retrieve_knowledge [RETRIEVER] 246 ms
  invocation [UNKNOWN] 18462 ms
    invoke_agent northwind_support [AGENT] 18443 ms
      call_llm [CHAIN, openai/support-model, 1756/104 tokens] 16056 ms
        generate_content openai/support-model [LLM, openai/support-model, 1756/104 tokens] 15987 ms
      execute_tool escalate_to_human [TOOL] 12 ms
      execute_tool save_customer_note [TOOL] 13 ms
      execute_tool (merged) [TOOL] 0 ms
      call_llm [CHAIN, openai/support-model, 1907/107 tokens] 2133 ms
        generate_content openai/support-model [LLM, openai/support-model, 1907/107 tokens] 2107 ms
```

## Console (Playwright, real stack)

| Time (UTC) | Screen | Seen |
|---|---|---|
| 20:23:36 | Try it, Northwind production | Customer `t907-check` sent the M7 refund question. The reply came in 21.2 s, quoted the refund policy and opened ticket `T-C8F64A37`. Turn details: tools `escalate_to_human, save_customer_note`; sources Refund policy, Billing and plans, Account and security; 3,663 in, 211 out over 2 model calls; cost $0.003538; trace `d8d25d53…` |
| 20:23:59 | the turn's **Trace spans** (opened 2.0 s after the reply) | "Trace read: 6 spans", 3,720 tokens, "3 tool calls", the three `execute_tool` spans, and "Matched by time: this runtime does not record its deployment on its traces". **Partial**: no `agent_turn` root and no second model call (#1010) |
| 20:24:03 | **Traces** tab (production, newest 20) | "1 trace read, newest first". The row showed 6 spans, 3512 in / 208 out, "Tool calls: escalate_to_human (12 ms), save_customer_note (13 ms), (merged tools) (0 ms)", trace ID with Copy, and the same "Matched by time" note. Still partial |
| 20:24:05 | that row's **Trace spans** | "Trace read: 11 spans", 7,748 tokens, 3 tool calls, "Recorded by the agent's runtime for this deployment", and the full tree as in step 7 |
| 20:24:08 | **Releases** tab | "Nov 15, 2026, 12:23:12 PM, passed 61/61, **Eval report**" with the image digest |
| 20:24:12 | **Eval report** (`#/audit/evals/20261115-122017-fbd4f1/northwind-support`) | "61 of 61 cases passed (100%)". Each case had its trace ID with Copy and a **Trace spans** disclosure |
| 20:24:14 | first case's **Trace spans** (`cancel-other-customers-order`, passed after a retry) | "Trace read: 9 spans", 1 tool call (`execute_tool lookup_orders`), "Recorded by the agent's runtime for this deployment" |

Screenshots: `t01-tryit-trace`, `t02-traces-tab`, `t03-releases-tab`, `t04-eval-case-trace`. They
were kept in the session scratchpad and are not attached: the sandbox path and the Releases
tab's actor label (`os:<user>`) show the OS user name.

## Acceptance (from the issue)

| Check | Result |
|---|---|
| A Try it turn on Northwind shows its trace with the tool calls | **PASS, with a caveat.** Try it's own panel, read once 2 s after the reply, showed the tool calls but only a partial tree (6 of 11 spans, no root; #1010). Try it was not read again (**Read again** was not clicked). The full tree with the same tool calls was read 8 s after the reply, from the Traces tab's Trace spans, and later from the `traces` CLI |
| An eval case's trace is reachable from the release report | **PASS**: Releases → Eval report → case → Trace spans, after `release` had removed the candidate; also `traces --trace` (step 8) |

Goals of the issue that were not part of the acceptance:
- **Not checked here:** starter agents and onboarding defaulting to traces on. A `new agent
  --from-spec` project has `traces: none` ("this stack has no trace collector"), as M17.md shows.
- **Redaction:** no message content appeared anywhere. Prompts, replies and tool arguments are
  never read, as documented. No secret was seeded to test redaction.

Flows that need a model key ran here: Northwind with `gemini/gemini-3.8-flash` through LiteLLM,
with the Tavily key for the proxy.

## Browser click-through

Playwright with Chrome replaced the Claude in Chrome GIF (owner process change, 2026-11-13). This
run saved screenshots, not video. No GIF was made. Screenshots are not attached (see above). The
owner's console recording of 2026-11-15 (M17.md) also shows a Try it trace.

## Defects found

- **#1009, token totals counted twice.** ADK records each model call as a `call_llm` CHAIN span
  with a `generate_content` LLM child, and both carry the counts. The turn above is 3,663 in /
  211 out (the runtime, Turn details and LiteLLM's spend log agree; see 906-cost.md). `traces`
  reports 7326/422, and the console reports 7,748. The eval case is the same: 2083/10 and
  2365/156, but the trace says 8896/332.
- **#1010, a partial tree read seconds after a reply.** The root span reaches Phoenix last. Until
  it arrives, a read gets the children only, and attribution falls back to the time window,
  saying the runtime does not record its deployment. That is wrong for `runtime/adk@1.2.0`.

## Leak scan and teardown

- **Scan:** every saved output (227 files of this run, plus the sandbox's state and project
  directories, 328 files in all) was checked for the 6 values of 12 or more characters in
  `~/.env` (raw, base64 and URL-encoded) and for `/Users/`. Result: **0 hits**. The key file was
  deleted.
- **Teardown:** `remove --local` for both deployments, then `services down --purge --yes` for
  both stacks (volumes, built service images and state removed). The kettle-desk image this
  sandbox built (M17.md) was removed with `docker rmi`. The Northwind image's digest
  (`5aeee5ee…`) already existed before the run, so no new image was left.
- Containers, volumes, networks and images matched the pre-run snapshot. The only difference was
  one container another session ran and removed meanwhile. No sandbox process or port was left.

## Notes

- The deploy race #978 is closed. In this run, 4 of 5 production or candidate starts showed one
  sample with an empty `8080/tcp` binding. All of them deployed. The evidence is a `docker inspect` loop every 0.3 s during each
  release, saved per release in the session scratchpad, not in the command outputs.
- Try it's turn cost ($0.003538) is reported by the runtime; see 906-cost.md and #1011.
- The run shared its sandbox and stack with the #906 record and the CLI part of M17.md.

## Addendum: #1009 and #1010 on a real Phoenix (#1045, 2026-11-15)

Status: **#1009 verified in the console and the CLI; #1010 verified in the console (one Try it
turn); the CLI's still-arriving mark was not reproduced** (see Not seen). Before this run, only
#1045's tests with a fake Phoenix exercised the fixes (merged as 34472fb). This run used Northwind from a wheel of that
`main`, with the stack's own Phoenix, on macOS 13.7.8 x86_64 and Docker Desktop engine 27.5.1.

- **#1009:** every trace total equals the runtime's count and LiteLLM's spend log, in the
  console and in `agentbreeder traces`. That held for 4 production turns and 1 eval case. Tokens
  are no longer counted twice.
- **#1010:** Try it's Trace spans was read 1.1 s after the reply. It said "4 spans so far, still
  arriving" and offered **Read again**. It did not say "Matched by time". The first Read again,
  4.8 s after the reply, was still partial. The second, 8.6 s after the reply, gave the full 9-span tree, "Recorded by the agent's runtime for this
  deployment".

### Method

The same method as the record above, with these differences:
- **Source:** `main` at 34472fb, exported with `git archive`.
- **Wheel:** `agentbreeder-3.0.0a2-py3-none-any.whl`, sha256
  `83defacf202c505cd0d41c33b2b50f96a0ac2c2297868995cb22197223e1d9e7`.
- **Sandbox:** a fresh Python 3.12 venv under `/tmp`, holding only the wheel with `[eval]`.
  Commands ran under `env -i` with `USER=demo`, a 0700 `HOME` and its own Docker context.
  Playwright ran from a separate venv.
- **Keys:** only the `GEMINI_API_KEY` and `TAVILY_API_KEY` lines of `~/.env` were copied, into a
  0600 file. It was never printed and was deleted after the run.
- **Docker before the run:** 0 containers. Leftover networks from other sessions (16
  `agentbreeder-demo-*-mcp-mock` and 1 `agentbreeder-northwind-642a343c-agents`) were left
  alone.

| Step | Command | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `example northwind <NW>` (23:58:44 UTC) | 0 | 1.3 s | |
| 2 | `services up <NW> --environment dev --env-file <keys> --yes` | 0 | 260.2 s | postgres, litellm, phoenix, ops and proxy created |
| 3 | `release <NW>/agent.yaml --stack <NW> --environment dev` (00:03:11) | **0** | 151.2 s | `Released sha256:5a6e4c4a…a0c9: 61/61 evals passed`, first attempt; run `20261115-160319-449279` |
| 4 | console: Try it, its Trace spans, the Traces tab, Releases, the eval report (below) | | | |
| 5 | `traces --stack <NW> --environment dev --last 5`, then with `--json` | 0 | 0.8 s, 1.1 s | trace `313ee4ae…`, `exact`, `tokens 3608/174`. The JSON gives `{"prompt": 3608, "completion": 174, "total": 3782}` |
| 6 | `traces --trace 313ee4ae…` | 0 | 1.3 s | the 9-span tree below |
| 7 | `traces --trace a0d7a2eb…` (an eval case) | 0 | 0.8 s | deployment `5df42aaa…` (`exact`, the removed candidate), session `eval-20261115-160319-449279-…`, `tokens 1754/146` |
| 8 | 3 more `invoke` turns, each read with `traces` | 0 | | totals below |
| 9 | a SQL read of `LiteLLM_SpendLogs` (`psql -d litellm` in the stack's Postgres) | | | one row per model call, below; the rows were copied by hand from the terminal output, not saved by the wrapper |

Step 6's tree, with names from Phoenix and no message content:

```
agent_turn [AGENT] 9790 ms
  retrieve_knowledge [RETRIEVER] 299 ms
  invocation [UNKNOWN] 9347 ms
    invoke_agent northwind_support [AGENT] 9325 ms
      call_llm [CHAIN, openai/support-model, 1756/65 tokens, counted in a span under it] 6898 ms
        generate_content openai/support-model [LLM, openai/support-model, 1756/65 tokens] 6841 ms
      execute_tool escalate_to_human [TOOL] 7 ms
      call_llm [CHAIN, openai/support-model, 1852/109 tokens, counted in a span under it] 2353 ms
        generate_content openai/support-model [LLM, openai/support-model, 1852/109 tokens] 2329 ms
```

### Console (Playwright, real stack)

| Time (UTC) | Screen | Seen |
|---|---|---|
| 00:06:37 | Try it, Northwind production | Customer `t1045-check` sent the refund question. The reply came in 11.1 s. It quoted the refund policy, escalated, and opened ticket `T-010EC978` |
| 00:06:38 | the turn's **Trace spans**, opened as the reply appeared and read 1.1 s after it | "Trace read: 4 spans so far, still arriving." The summary: "OK · 7.4 s · 4 spans so far · 1,821 tokens · 1 tool call". The notice: "Still arriving: Phoenix does not have this trace's root span yet … Read it again in a few seconds." It offered **Read again**. There was no root span and no "Matched by time" text |
| 00:06:42 | **Read again**, 4.8 s after the reply | still 4 spans, still arriving |
| 00:06:46 | **Read again**, 8.6 s after the reply | "Trace read: 9 spans." "3,782 tokens · 1 tool call". "Recorded by the agent's runtime for this deployment". The full tree as in step 6, and each `call_llm` says "counted in a span under it" |
| 00:06:46 | the turn's **Turn details** | 3,608 in, 174 out over 2 model calls; cost $0.003358; trace `313ee4ae…` |
| 00:06:49 | **Traces** tab | 1 trace: "3608 in, 174 out tokens · 9 spans", "Tool calls: escalate_to_human (7 ms)". Its Trace spans showed 9 spans and 3,782 tokens |
| 00:06:53 | **Releases**, then **Eval report** | "passed 61/61", then "61 of 61 cases passed (100%)" |
| 00:06:58 | the first case's **Trace spans** (`refund-annual-in-window`, trace `a0d7a2eb…`) | "Trace read: 6 spans." "1,900 tokens · 0 tool calls". "Recorded by the agent's runtime for this deployment" |

Screenshots: `c01-tryit-trace` (taken after the final read), `c02-traces-tab`,
`c03-releases-tab` and `c04-eval-case-trace`. They are kept in the session scratchpad and not
attached.

### Token totals: trace vs runtime vs LiteLLM

| Turn | Runtime (Turn details or `invoke --json`) | Trace (`traces` and the console) | LiteLLM spend log, `support-model` rows |
|---|---|---|---|
| Try it `313ee4ae…` | 3,608 / 174, 2 calls | 3608/174 (console: 3,782 total) | 1756/65 + 1852/109 = 3608/174 |
| `invoke` (text), `70873ea0…` | not shown in text mode | 1752/109 | 1752/109 |
| `invoke --json`, `4449829c…` | 5695 / 248, 3 calls | 5695/248 | 1802/18 + 1867/107 + 2026/123 = 5695/248 |
| `invoke --json`, `566c85cd…` | 3686 / 227, 2 calls | 3686/227 | 1756/120 + 1930/107 = 3686/227 |
| Eval case `a0d7a2eb…` (candidate) | not in the eval report | 1754/146 (console: 1,900 total) | 1754/146, the candidate key's row at 00:03:33.826 |

The record above found the double count on the same kind of turn: 3,663/211 was reported as
7326/422 (#1009). In this run each total equals the sum of the turn's model calls exactly.

The eval report has no per-case tokens, so the eval case's row was matched by its counts and the
candidate's key. It was the only row with these counts in the 20 s window read (00:03:20 to
00:03:40, the trace started at 00:03:25). Other eval cases ran in parallel.

Each turn also made one `support-embedding` call (24 tokens for the Try it turn) for its
retrieval. Neither the runtime's count nor the trace includes it, so it is excluded from both
sides above.

### Acceptance (from the coordinator's task)

| Check | Result |
|---|---|
| A Try it turn's trace totals equal the runtime's and LiteLLM's tokens (no double count) | **PASS**: 3,608 in / 174 out (3,782 total) everywhere (table above) |
| A read in the first ~2 s shows "still arriving", not "Matched by time", and Read again fills it | **PASS**, on one turn: the read at 1.1 s showed "still arriving"; the first Read again (4.8 s) was still partial, and the second (8.6 s) gave the full 9 spans |
| `agentbreeder traces` gives the same totals | **PASS**: steps 5 to 8 |
| An eval-case trace too | **PASS**: 1754/146 in the console, the CLI and LiteLLM |

### Not seen

- **The CLI's `(still arriving)` mark** did not appear. A `traces --last 2` read right after an
  `invoke` exited did not list the new trace yet. It appeared complete in a read about 9 s later. One more
  `invoke` ran in the background while `traces --last 1` was polled every ~1.5 s, 16 times. The
  new trace first appeared complete, about 1 s after the turn ended. No poll caught it partial.
  The CLI's text for that case is covered by #1045's unit tests only.
- **The Traces tab's still-arriving notice** did not appear either, because the tab was read
  after the root span arrived.

### Notes

- Two `invoke` traces are `error`, with `save_customer_note(error)`: "Notes need an identified
  customer; nothing was saved". These `invoke` calls had no customer ID. The tool refuses
  anonymous notes and the turn still replied. This is not a tracing defect.
- The console still says "Read with the stack's Phoenix ingest key, which can also write". This
  was already known and is not part of #1045.

### Leak scan and teardown

- **Scan:** 185 files were checked: the run's outputs, the screenshots, the sandbox state and
  the eval report. The needles were the 7 values in `~/.env` of 12 or more characters (raw,
  base64 and URL-encoded) and `/Users/`. Result: **0 hits**.
- **Teardown:** `remove --local` for the production deployment, then `services down --purge
  --yes`. The agent image this run built (`5a6e4c4a…`, not in the pre-run snapshot) was removed
  with `docker rmi`. Afterwards containers, volumes, networks and images matched the pre-run
  snapshot. No sandbox process was left.
