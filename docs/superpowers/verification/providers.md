# #1020 verification: OpenAI, Anthropic and OpenRouter end to end

Status: **partial, verified from `main`** (89cf804, after #1016 merged, 2026-11-15, macOS 13.7.8
x86_64, Docker Desktop engine 27.5.1, LiteLLM 1.106.2 from `service/litellm@1.0.0`). Per provider,
from the starter (`new agent --model <p>` → `services up` → `release` → `invoke`):

| Provider | Model | Result |
|---|---|---|
| **openrouter** | `openrouter/google/gemini-3.8-flash` | **PASS**: release 2/2 (the judge's `json_schema` verdicts parsed), `invoke` 2/2 |
| openrouter | `openrouter/anthropic/claude-haiku-5.5` | Release **FAIL 1/2** on the model's answer, not on the plumbing: chat and the judge worked, and the judge failed one case on its content |
| **openai** | `openai/gpt-5.6` (the default) | **Not verified, blocked by the key:** OpenAI answered 429 "You have no credits remaining" to every call |
| **anthropic** | `anthropic/claude-opus-5-5` (the default) | **Not verified, blocked by the key:** Anthropic refused every call, because the key is not scoped to a workspace |

`Provider.verified` was **not** changed by this PR. The results were sent to the coordinator; a
flip, if any, goes to a code lane. OpenAI embeddings (`--from-spec` with knowledge) were **not
run**, since the OpenAI key has no credits. The leak scan found 0 hits, and Docker was torn down
to the pre-run snapshot after each run.

This is on `main`, not in a published preview.

## Wheel from main (clean sandbox)

This used the same wheel and sandbox as `gateway-limits.md` (#1019): `main` at 89cf804, wheel
`agentbreeder-3.0.0a2-py3-none-any.whl`, sha256
`ffe84b9090146ac4df97a3b885156b5328086c3ae785b4218dc3d86821ea5902`, installed with `[eval]` into a
fresh Python 3.12 venv. Every command ran under `env -i` with a fresh `HOME`, `TMPDIR` inside the
sandbox, `PIP_NO_CACHE_DIR=1` and `USER=demo`. A Docker context was created inside the sandbox
`HOME`. Each run got a 0600 file holding only its provider's key line from `~/.env`. The files
were deleted after the runs.

One script ran each provider: `services up <P> --environment dev --env-file <key> --yes`, then
`release <P>/agent.yaml --stack <P> --environment dev`, then two `invoke --json` turns ("When is
support open?", "What can you help me with?"), then a read of `LiteLLM_SpendLogs` grouped by
alias, model and status. A teardown script removed any deployment, ran `services down <P> --purge
--yes`, and removed only the images missing from the pre-run snapshot. Each starter is the
template's two eval cases: `hours` (must match `9`, judged against "Support is open 9am to 5pm
Pacific, Monday to Friday.") and `unknown` ("What is Acme's stock price today?", judged against
"The agent says it does not know rather than inventing a number."), with `threshold: 1.0` and
`retries: 1`. The same model serves both the `chat` and `judge` aliases.

`new agent` printed "`<p>` is config-only: this starter has not been run end to end with it (the
eval judge needs strict json_schema output)" for each of the three providers.

### OpenRouter

The model is picked with `--model-id`. Both models were chosen from OpenRouter's public model list
on 2026-11-15 (`/api/v1/models`, read once and not saved), as cheap models that list
`structured_outputs`. OpenRouter's list prices per million tokens in and out were $0.10 / $0.50
for `anthropic/claude-haiku-5.5` and $0.825 / $4.125 for `google/gemini-3.8-flash`. LiteLLM's
cost estimates below use its own price map, which for Gemini 3.8 Flash comes to about
$0.75 / $3.75.
Gemini 3.8 Flash is the model behind Northwind's OpenRouter fallback.

| Step | Command (UTC start) | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `new agent prov-openrouter --model openrouter --model-id openrouter/anthropic/claude-haiku-5.5` | 0 | 0.8 s | |
| 2 | `services up` (22:42:57) | 0 | 85.7 s | |
| 3 | `release` (22:44:23) | **5** | 32.7 s | `Eval gate failed: 1/2 passed`. `hours` passed on its first attempt (judge correctness 1.0). `unknown` failed both attempts on `judge_correct` (0.5): the reply declined to give a stock price, but it also gave "9am to 5pm Pacific, Monday to Friday" as hours for Acme's investor relations team, which the judge called an invented detail |
| 4 | `invoke`, twice | 2, 2 | 0.7, 0.6 s | refused: no production deployment (the gate kept production unchanged) |
| 5 | spend log | | | `chat`: 3 success, 348 in / 216 out, $0.000143; `judge`: 3 success, 1445 in / 978 out, $0.000634 |
| 6 | teardown | 0 | 5.9 s | |
| 7 | `new agent prov-orgem --model openrouter --model-id openrouter/google/gemini-3.8-flash` | 0 | 0.6 s | |
| 8 | `services up` (22:45:28) | 0 | 48.7 s | |
| 9 | `release` (22:46:16) | **0** | 30.9 s | `Released sha256:6e3fde7d…: 2/2 evals passed`; production `55caf32f…` |
| 10 | `invoke prov-orgem "When is support open?" --json` (22:46:47) | 0 | 7.7 s | 200: "Acme's support hours are Monday through Friday, from 9:00 AM to 5:00 PM Pacific Time." 1 model call, 76 in / 28 out, `cost_usd` 0.000162 |
| 11 | `invoke prov-orgem "What can you help me with?" --json` (22:46:55) | 0 | 5.6 s | 200: a short description of what it helps with. 1 model call, 78 in / 47 out, `cost_usd` 0.00023475 |
| 12 | spend log | | | `chat`: 4 success (2 candidate eval turns, 2 production turns), 310 in / 112 out, $0.000653; `judge`: 2 success, 426 in / 399 out, $0.001816. No failures |
| 13 | teardown | 0 | 5.7 s | |

The judge's strict `json_schema` output worked through OpenRouter with both models: every judge
call succeeded, and each case got a parsed correctness score and explanation. The Haiku failure
is the eval gate working as designed on a weaker answer. It does not show a provider defect.

### OpenAI

| Step | Command (UTC start) | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `new agent prov-openai --model openai` | 0 | 0.7 s | `openai/gpt-5.6` on both aliases |
| 2 | `services up` (22:37:58) | 0 | 59.6 s | |
| 3 | `release` (22:38:58) | 5 | 56.7 s | `Eval gate failed: 0/2 passed`. Both cases: `agent_responded` false, `HTTPError: 429` |
| 4 | `invoke`, twice | 2, 2 | | refused: no production deployment |
| 5 | spend log | | | `chat`: 12 failure rows, 0 tokens out. No judge call was made |
| 6 | teardown | 0 | 8.2 s | |

LiteLLM's log gives the cause 12 times: `RateLimitError: OpenAIException - You have no credits
remaining. Add credits to continue using the API`. The OpenAI key in `~/.env` has no credit, so
nothing about the OpenAI path was verified, including whether `openai/gpt-5.6` is a valid model ID
and the judge's `json_schema`. To run it again, the owner needs to add credit, or supply a key that
has some.

### Anthropic

| Step | Command (UTC start) | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `new agent prov-anthropic --model anthropic` | 0 | 0.6 s | `anthropic/claude-opus-5-5` on both aliases |
| 2 | `services up` (22:40:42) | 0 | 71.5 s | |
| 3 | `release` (22:41:53) | 5 | 23.8 s | `Eval gate failed: 0/2 passed`. Both cases: `agent_responded` false, `HTTPError: 502` |
| 4 | `invoke`, twice | 2, 2 | | refused: no production deployment |
| 5 | spend log | | | `chat`: 4 failure rows. No judge call was made |
| 6 | teardown | 0 | 8.2 s | |

LiteLLM's log gives the cause: `AnthropicException - invalid_request_error: This API key is not
scoped to a workspace, so this request must include the anthropic-workspace-id header with the ID
of the workspace to use. Add the header, or use an API key that is scoped to a workspace.`
That is a 400-class error from Anthropic. The 502 in the eval output is what the agent runtime
returned once the model call failed. The LiteLLM container logs quoted in this record (Anthropic
here, OpenAI above) were read during the run and were not kept. AgentBreeder's gateway config review allows no custom headers, so the starter cannot send that
header. To run it again, the owner needs a workspace-scoped Anthropic key. Whether AgentBreeder
should support a workspace header is a separate decision, and no issue has been filed for it.

## Acceptance (from the issue)

| Check | Result |
|---|---|
| For each provider: `new agent --model <p>` → services up → release (judge passes) → invoke, from a wheel of main, real calls, leak scan, teardown | **openrouter PASS** (with `google/gemini-3.8-flash`; `anthropic/claude-haiku-5.5` failed the gate on answer quality). **openai not verified** (the key has no credits). **anthropic not verified** (the key is not workspace-scoped) |
| Flip `verified` only for the providers that passed | **Not done here**, by instruction: the results went to the coordinator for a code lane. Only openrouter qualifies |
| Embeddings per `agentspec.EMBEDDINGS` checked for openai | **Not run**: the OpenAI key has no credits |
| Record `docs/superpowers/verification/providers.md`; docs and console builder copy updated | This file. The docs and console copy follow the flip, in the code lane |

Flows that need a model key ran here: OpenRouter with the two models above. The OpenAI and
Anthropic keys were sent, and both providers refused them.

## Browser click-through

Not run for this record. The console was not used for the provider runs.

## Leak scan and teardown

- Every saved output of the session (284 files, shared with `gateway-limits.md`) was scanned for
  each `~/.env` value of 12 or more characters (7 values, plain, base64 and URL-encoded) and for
  `/Users/`: **0 hits**. The key files were deleted.
- After each provider run, the containers, volumes and networks matched the pre-run snapshot. The
  only images removed were the ones each run built (4 starter images), none of which was in the
  snapshot. The snapshot exception in `gateway-limits.md` (a pre-existing Northwind image removed
  in S1) happened before these runs.

## Notes

- Costs are LiteLLM's estimates, not a bill: $0.0032 in all for the two OpenRouter runs. The
  OpenAI and Anthropic calls failed and cost nothing.
- A starter's eval set has two cases, so a release here shows that the path works, not how good
  the model is. The Haiku result shows that the choice of model can fail the gate.
- `openai/gpt-5.6` is not an ID in OpenRouter's list (OpenRouter lists `openai/gpt-5.6-luna`,
  `-terra` and `-sol`). Whether OpenAI's own API accepts `gpt-5.6` was not tested, since the key
  had no credit.
