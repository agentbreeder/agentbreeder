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
