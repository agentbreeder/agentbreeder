# #1111 verification: does `runtime/adk`'s step cap stop tool calls on Gemini? (live gateway)

Status: **verified from `main`; the cap does not hold as designed** (3f13edb4, 2026-11-19 UTC,
macOS 13.7.8 x86_64, Docker Desktop engine 27.5.1, LiteLLM 1.106.2 from `service/litellm@1.0.0`,
`runtime/adk@1.4.0` with google-adk 2.12.0, model `gemini/gemini-3.8-flash`). The agent had
`max_model_calls: 3` and a tool that always asks for one more call, and it never answered:
- The runtime turned function calling off for the 3rd and 4th model calls. Gemini returned a tool
  call both times, and the tool ran both times.
- ADK's own backstop (`max_llm_calls = cap + 1`) ended each turn. So the turn was bounded:
  **4 model calls sent and 4 tool runs, against a cap of 3**.
- The caller got the generic fallback reply with HTTP 200. It was neither an answer nor a coded
  error.
- All 5 turns behaved the same.

Not run: Linux or arm64; Anthropic, OpenAI or other Gemini models through the runtime; google-adk
versions other than 2.12.0; any fix. The outbound request was not captured, because the gateway's
message logging is off. That ADK's `tool_config` mode `NONE` reaches the gateway as `tool_choice:
none` is inferred from the code, not observed. The tool ran past the cap either way.

This is a record only. It changes no runtime code. "What the runtime needs" below lists proposals
for the next `runtime/adk` version.

`runtime/adk@1.5.0` (#1149) merged after this run and was not run here. Read from its code
(`runtime/adk/1.5.0/runtime/agent.py`), it keeps the same mechanism: `mode="NONE"` on the last
allowed call, and an ADK backstop with the same comment. No `after_model_callback` removes function
calls. The backstop is the whole-turn cap + 1: without sub-agents that is `max_model_calls + 1`, as
in 1.4.0. With sub-agents the turn cap defaults to `min(40, max_model_calls + 6)`, so a model that
ignores `NONE` could run more tools past the parent's own cap before ADK stops it. The sub-agent
runner (`subagents.py`) uses the same kind of backstop. So this finding is expected to apply to
1.5.0 as well.

This is on `main`, not in a published preview.

## Setup

- **Install:** a wheel of `main` (3f13edb4, `agentbreeder-3.0.0a3-py3-none-any.whl`) in a fresh
  Python 3.12 venv.
- **Sandbox:** every command ran under `env -i`, with a fresh `HOME` and `TMPDIR` inside the
  sandbox (`$SB`, under `/tmp`), `PIP_NO_CACHE_DIR=1` and `USER=demo`. A Docker context was
  created inside the sandbox `HOME`.
- **Keys:** `GEMINI_API_KEY` and `OPENROUTER_API_KEY` were copied from `~/.env` into one 0600 file
  in the sandbox, and only `services up --env-file` was given it. The gateway imports both, but
  every alias here (`chat`, `judge`) is Gemini.

The project `cap` was made with `new agent cap --model gemini` and then changed by hand:
- `runtime: runtime/adk@1.4.0` and `model: {alias: chat, reasoning: low, max_model_calls: 3}`,
  with no `rpm` and no budget. The `rpm: 2` now in the sandbox's `agent.yaml` was added after these
  turns, for the #1019 re-check (`gateway-limits.md`, S3).
- A local tool, `tool/next-clue@1.0.0`, is a clue chain that never ends. `next_clue("K1")` returns
  "Code K1 is not the final answer. The next code is K2. Call next_clue with K2 to continue."
- The prompt says to keep calling `next_clue` until a result says it is the final answer. No
  result ever does.
- Two evals (support hours) let `release` deploy it.

| Check (in the order run, UTC) | Result |
|---|---|
| `services up $SB/cap --environment dev --env-file <key> --yes` (2026-11-18 23:57) | exit 0; postgres and litellm (`chat`, `judge` → Gemini) |
| `release`, tries 1 to 6 (2026-11-19 00:02–00:21) | Try 1: `LOCK_STALE` after a prompt edit, so `build` was run. Tries 2 to 5: `Health check failed` (candidate or production) while other work loaded the 4-core host; nothing changed between tries. Try 6: **2/2 evals passed**, production `c2efe3b7…` |
| `invoke cap <message> --session sN --json` ×5 (00:21:44–00:22:33) | See "Turns" |
| direct probes `probe.py` and `probe2.py` (00:22:46–00:23:51) | See "Direct gateway probes" |
| direct probes `probe3.py` and `probe4.py` (07:53–07:59, on the same stack and gateway, still up) | See "Direct gateway probes" |

The message the five turns sent is not in the saved outputs; the JSON results do not echo it. The
tool loop comes from the prompt: in every turn the model called `next_clue` first and followed the
codes.

## The runtime's cap (read from the code)

`src/agentbreeder/builtin_registry/runtime/adk/1.4.0/runtime/agent.py` sets up three things:
- **The cap.** `before_model_callback` counts the call. On a step `>= max_model_calls` it sets
  `tool_config.function_calling_config.mode = NONE`, with the comment "last allowed call: no
  tools, so the model must reply".
- **The backstop.** `runner.run_async(..., RunConfig(max_llm_calls=max_steps + 1))`. ADK raises
  `LlmCallsLimitExceededError` when a call would go past that limit. The runtime catches it
  ("backstop only; the plugin already forces a reply on the last allowed call") and returns
  "Sorry, I couldn't put an answer together. Could you rephrase that?".
- **The order.** In google-adk 2.12.0, `before_model_callback` runs before ADK's limit check
  (`flows/llm_flows/core/_model_call.py`, `increment_llm_call_count`; read inside the agent
  image). So `model_calls` counts the call that the backstop stops, though that call is never sent.

## Turns

| Field | Value (the same in each of the 5 turns) |
|---|---|
| HTTP status | 200 |
| `tools` | `next_clue` × 4 |
| `model_calls` | 5 |
| `prompt_tokens`, `output_tokens` | 1190, 72 |
| `reply` | "Sorry, I couldn't put an answer together. Could you rephrase that?" |
| `cost_usd` | 0.0011625 |

The gateway's spend log (`LiteLLM_SpendLogs` in the stack's Postgres, saved as
`t3-spendlog-turns.txt`) shows what was actually sent. Each turn has **4 `chat` rows**, 20 in all,
every one a `success`. Per turn, the prompt tokens were 209, 268, 327 and 386 (1190 in total), each
with 18 output tokens (72 in total), and their spend adds up to the turn's `cost_usd`. So in each
turn:
- calls 1 and 2 were ordinary tool calls;
- calls 3 and 4 were the runtime's "no tools" calls. Gemini returned a tool call each time, and
  ADK ran the tool each time;
- the runtime counted a "call 5", which ADK's backstop stopped before it was sent.

## Direct gateway probes

Each probe makes direct calls to `/v1/chat/completions` on the same gateway, with a deployment key
and the same tool and prompt shape. It prints only the status, the finish reason and the
tool-call count, never the text or the key.

| Request | Result |
|---|---|
| no history, `tool_choice: auto` | tool call 5/5 |
| no history, `tool_choice: none` | **text 5/5** (`stop`) |
| history of 2 tool calls, `tool_choice: none` (like the runtime's last step) | **tool call 5/5** |
| history of 2 tool calls, `tool_choice: auto` | tool call 5/5 |
| history of 2 tool calls, **no `tools` in the request at all** | **tool call 3/3** |
| history of 2 tool calls, `tool_choice: none` (again) | tool call 3/3 |
| history of 2 tool calls, then a final user message ("You have used all your tool calls for this turn. Do not call any tool. Answer now in plain text with what you have."), no tools | text 2/2. The first of the 3 calls got `429` from the key's own `rpm: 2`, which is not a model result |
| the same final user message, with tools kept and `tool_choice: none` | **text 3/3** |

How the probes ran:
- The first four rows are `probe.py` and `probe2.py`, 5 calls each, back to back, with the
  deployment key of that time, which had no rpm cap.
- The last four rows are `probe3.py` and `probe4.py`, 3 calls each. They ran about 7.5 hours later
  on the same stack with the newest key, which has `rpm: 2`, so their calls were spaced 31 s
  apart.

Once the history holds tool calls, Gemini through LiteLLM 1.106.2 does not honour `tool_choice:
none`, and removing the tools from the request does not help either. Two request changes were
tried with a history, and only the final instruction in the conversation worked (5 of 5 calls).
This matches the F0 probes (`F0-framework-probes.md`, 12 of 12 with `none`) and goes further: F0
did not test ADK or a request with no tools.

## Acceptance (from the issue)

| Check | Result |
|---|---|
| On a real stack with a Gemini alias through the gateway, drive a `runtime/adk` agent to its step or tool-call limit, and record whether the model stops calling tools | **Done: it does not stop.** With `max_model_calls: 3`, 4 calls reach the model and 4 tools run. The turn ends on ADK's backstop with the generic reply, in 5 of 5 turns |
| If it does not stop, enforce the cap in the runtime (refuse tool calls past the limit, with a coded error); do not rely on `tool_choice` | **Not done here** (this lane is record only). The proposals below are for the next `runtime/adk` version (1.5.0 has the same mechanism, with a looser backstop when sub-agents are set) |
| Apply the same rule to the #1084 framework runtimes (spec delta) | Not done. It is a spec change, for the multi-framework lane |
| Record the run | This file |

Flows that needed a model key: Gemini (`gemini/gemini-3.8-flash`) through the gateway. The five
capped turns made 20 calls, and the direct probes made 32 (the key's rpm refused one). The spend
log covers the whole stack: the release evals, these turns and probes, and the #1019 budget and
rpm turns. It has 70 successful calls (62 `chat`, 8 `judge`) costing $0.0233 in total, plus 7
refused `chat` calls (budget and rpm), saved as `t3-spendlog-totals.txt`.

## Browser click-through

Not run: nothing in #1111 has a browser surface.

## What the runtime needs (proposals for the next runtime/adk version, not tested)

1. **Do not rely on `tool_choice` or `mode=NONE`.** On the last allowed step, drop any
   function-call parts from the model's response in `after_model_callback`, before ADK runs them.
   Then no tool runs past the cap, whatever the model returns. This can leave an empty response,
   so it needs item 3.
2. **Ask for the answer in the conversation.** On the last allowed step, add a short final
   user-role instruction to the request. Here it was the only change that made Gemini answer (5
   of 5 direct calls; one model and one wording, not run through ADK). Keep `mode=NONE` as well:
   OpenAI honours it (F0).
3. **If there is still no text, return a coded result**, for example `STEP_LIMIT_REACHED`, the way
   #1034 surfaces budget and rpm. Do not return the generic sorry with HTTP 200.
4. **Count only calls that are sent.** `model_calls` counts the call that ADK's backstop refuses
   (5 reported, 4 sent). Either subtract the refused call when `LlmCallsLimitExceededError` is
   caught, or count calls that returned and calls that errored (through the error callback).
   Counting only in `after_model_callback` would miss calls that failed with a 429 or 5xx.
5. **Correct the comment** at the backstop ("the plugin already forces a reply on the last
   allowed call"). This run shows that it does not on Gemini.
6. **Add a regression test that needs no Gemini key**: a scripted provider that returns a tool
   call even under `tool_choice: none`. `tests/test_gateway_limits_docker.py` (#1019) already
   scripts providers this way.

## Leak scan and teardown

- **Leak scan.** Every saved output was scanned for the two key values in the sandbox env file,
  plain, base64 and URL-encoded, and for `/Users/`: 64 files. They were the 24 `t3-*` files (the
  release, invoke and probe outputs and the two spend-log reads), the #1048 and #1019 outputs from
  the same sandbox, and the teardown output. **0 key hits.** The only `/Users/` hits were in two
  local pytest logs of the #1019 Docker test (pytest's `rootdir` line). Those logs are not committed.
- **Teardown (2026-11-19 08:10 UTC).** `remove 42cd12bd… --local` and `services down $SB/cap
  --purge --yes` both exited 0. No container, network or volume of the stack was left
  (`volumes_removed: [agentbreeder-cap-fb38d335-postgres-data]`, `networks_left: []`).
- **Images.** The ones this lane added were removed by ID with `docker rmi`, without `-f`: the agent
  image and the three base images it pulled (python, pgvector, the LiteLLM image).
- **Snapshot.** Docker then matched the lane's snapshot from 2026-11-18 23:25 UTC: the same
  containers (the lane's keepalive), volumes (none), networks, images, and the default `bridge` ID
  `0ce15d58…`. One exception: an image another session built during the lane
  (`agentbreeder-cloud:gate-check`), which was left alone.
- **Sandbox.** The sandbox and its key file were deleted, and the keepalive container was removed
  by name (2026-11-19 14:19 UTC). Docker then had no containers or volumes, the same networks and
  images, and the same `bridge` ID.

## Notes

- `release` needed 6 tries. Tries 2 to 5 failed health checks while the shared host was loaded,
  and the same image then passed. This was not investigated further.
- The keepalive container from #1077 ran throughout, so Docker Desktop's Resource Saver did not
  stop the VM.
