# #1020 verification: OpenAI, Anthropic and OpenRouter end to end

Status: **partial, verified from `main`** (re-run on f6709b2: openai now passes, anthropic still
blocked; a third Anthropic run on 3e4b4869 with a new key, at the end of this file, is still
blocked; first run on 89cf804, after #1016 merged, 2026-11-15, macOS 13.7.8
x86_64, Docker Desktop engine 27.5.1, LiteLLM 1.106.2 from `service/litellm@1.0.0`). Per provider,
from the starter (`new agent --model <p>` → `services up` → `release` → `invoke`). The table and
sections up to "Notes" are the **first run (89cf804)**. The openai rows are superseded by the
re-run at the end (openai PASS, chat and embeddings); anthropic is still blocked there.

| Provider | Model | Result |
|---|---|---|
| **openrouter** | `openrouter/google/gemini-3.8-flash` | **PASS**: release 2/2 (the judge's `json_schema` verdicts parsed), `invoke` 2/2 |
| openrouter | `openrouter/anthropic/claude-haiku-5.5` | Release **FAIL 1/2** on the model's answer, not on the plumbing: chat and the judge worked, and the judge failed one case on its content |
| **openai** | `openai/gpt-5.6` (the default) | **Not verified, blocked by the key:** OpenAI answered 429 "You have no credits remaining" to every call |
| **anthropic** | `anthropic/claude-opus-5-5` (the default) | **Not verified, blocked by the key:** Anthropic refused every call, because the key is not scoped to a workspace |

`Provider.verified` was **not** changed by this PR. The results were sent to the coordinator; a
flip, if any, goes to a code lane. In the first run, OpenAI embeddings (`--from-spec` with
knowledge) were **not run**, since the OpenAI key had no credits (they pass in the re-run). The leak scan found 0 hits, and Docker was torn down
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
| For each provider: `new agent --model <p>` → services up → release (judge passes) → invoke, from a wheel of main, real calls, leak scan, teardown | **Re-run (f6709b2): openai PASS; anthropic still not verified. Third Anthropic run (3e4b4869, new key): still not verified.** First run: **openrouter PASS** (with `google/gemini-3.8-flash`; `anthropic/claude-haiku-5.5` failed the gate on answer quality). **openai not verified** (the key has no credits). **anthropic not verified** (the key is not workspace-scoped) |
| Flip `verified` only for the providers that passed | **Not done here**, by instruction: the results went to the coordinator for a code lane. Only openrouter qualifies |
| Embeddings per `agentspec.EMBEDDINGS` checked for openai | **Re-run: PASS** (`text-embedding-3-small`, 1536). First run: not run, the key had no credits |
| Record `docs/superpowers/verification/providers.md`; docs and console builder copy updated | This file. The docs and console copy follow the flip, in the code lane |

Flows that need a model key ran here. First run: OpenRouter with the two models above; the OpenAI
and Anthropic keys were sent, and both providers refused them. Re-run: OpenAI (chat and
embeddings) passed; Anthropic refused the key again.

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

- Costs are LiteLLM's estimates, not a bill: $0.0032 in all for the two first-run OpenRouter runs.
  In the first run the OpenAI and Anthropic calls failed and cost nothing.
- A starter's eval set has two cases, so a release here shows that the path works, not how good
  the model is. The Haiku result shows that the choice of model can fail the gate.
- `openai/gpt-5.6` is not an ID in OpenRouter's list (OpenRouter lists `openai/gpt-5.6-luna`,
  `-terra` and `-sol`). Whether OpenAI's own API accepts `gpt-5.6` was not tested, since the key
  had no credit.

## Re-run 2026-11-15 (evening): OpenAI with credit, Anthropic retried

The owner added credit to the OpenAI key and asked for one Anthropic retry with the current key.
This used a new wheel from `main` at f6709b2 (after #1046), `agentbreeder-3.0.0a2-py3-none-any.whl`,
sha256 `df05d9863d0e2fbfda5aabaff6bbd8ce5c6814e453cd3febf371fa3f6dc34460`, installed with `[eval]`
into a fresh Python 3.12 venv. The sandbox was set up as above (`env -i`, fresh `HOME` and
`TMPDIR`, `USER=demo`, a Docker context in the sandbox `HOME`, one 0600 key file per provider,
deleted after the run).

| Provider | Model | Result |
|---|---|---|
| **openai** | `openai/gpt-5.6` (the default) | **PASS**: release 2/2 (the judge's `json_schema` verdicts parsed), `invoke` 2/2 |
| **openai embeddings** | `openai/text-embedding-3-small`, 1536 dimensions, via `--from-spec` with one knowledge version | **PASS**: indexed 4 of 4 chunks, release 2/2, `invoke` 2/2 with the answers from the articles |
| anthropic | `anthropic/claude-opus-5-5` (the default) | **Not verified, same block as before:** Anthropic still refuses the key because it is not scoped to a workspace. Retried once, as instructed; no header or config change was made |

### OpenAI (starter)

| Step | Command (UTC start) | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `new agent prov-openai --model openai` (01:55:20) | 0 | 0.6 s | `openai/gpt-5.6` on both aliases; it printed the config-only notice |
| 2 | `services up` (01:55:29) | 0 | 52.1 s | |
| 3 | `release` (01:56:21) | **0** | 46.6 s | `Released sha256:93017539…: 2/2 evals passed`. `hours`: correctness 1.0 on attempt 1; `unknown`: correctness 1.0 on attempt 1 ("correctly states that it does not know Acme's current stock price") |
| 4 | `invoke prov-openai "When is support open?" --stack prov-openai --environment dev --json` (01:57:23) | 0 | 6.9 s | 200: "Acme support is open Monday through Friday, 9:00 AM to 5:00 PM Pacific Time." 1 model call, 80 in / 26 out, `cost_usd` 0.00084 |
| 5 | `invoke prov-openai "What can you help me with?" …` (01:57:30) | 0 | 5.3 s | 200: a short description of what it helps with. 1 model call, 82 in / 46 out, `cost_usd` 0.001248 |
| 6 | spend log (read under 30 s after step 5) | | | `chat`: 4 success (2 candidate eval turns, 2 production turns), 326 in / 132 out, $0.003944; `judge`: 2 success, 374 in / 84 out, $0.003176. No failures |
| 7 | teardown (`remove ID --local`, `services down --purge --yes`, `docker rmi` of the one new image) | 0 | | containers, networks, volumes and images matched the pre-run snapshot |

The first `invoke` attempt in this run left out `--stack` and got `NO_STATE` (exit 2). That was
the script's mistake, not a product fault. Steps 4 and 5 are the corrected commands. The run
script printed the exit codes and times to the terminal and did not save them to files. Teardown
was checked each time by comparing the Docker state with the pre-run snapshot, also printed and
not saved.

So `openai/gpt-5.6` is a valid model ID on OpenAI's own API, and the judge's strict
`json_schema` output works there.

### OpenAI embeddings (`--from-spec` with knowledge)

A sandbox-only knowledge version, `knowledge/acme-help@1.0.0` (two short Markdown articles:
warranty 27 months from delivery; unused returns within 41 days, return shipping paid only for
faulty items), was put in a registry named by `AGENTBREEDER_REGISTRY_PATH`. Its manifest names
`embedding: {alias: acme-embedding, dimensions: 1536}`. The spec (0600) asked for `model: openai`,
a short prompt, two eval cases (`warranty` must match `27`, `returns` must match `41`, each with a
judge reference), `threshold: 1.0`, `retries: 1`, and that knowledge version with `top_k: 5`.

| Step | Command (UTC start) | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `new agent prov-openai-kb --from-spec <spec> --dry-run --json` | 0 | | combination `model, prompt, evals, knowledge` (`verified: false`), aliases `chat, judge, acme-embedding`, key `OPENAI_API_KEY`, `openai/text-embedding-3-small` 1536, 2 documents, 4 chunks |
| 2 | the same without `--dry-run` (02:12:29) | 0 | | wrote the project; config-only notice for openai |
| 3 | `services up` (02:12:30) | 0 | 46.1 s | |
| 4 | `knowledge status knowledge/acme-help@1.0.0 --project <P> --environment dev` (02:13:16) | 0 | 7.3 s | `not_indexed`, 0 of 4 chunks ("the runtime indexes at the agent's first search") |
| 5 | `release` (02:13:23) | **0** | 28.1 s | `Released sha256:4e13d69c…: 2/2 evals passed`. `warranty` and `returns`: correctness 1.0 on attempt 1 |
| 6 | `knowledge status` again (02:13:51) | 0 | 0.7 s | `fresh`, chunks 4 indexed of 4, documents 2 of 2, `acme-embedding` 1536 |
| 7 | `invoke … "How long is the warranty on an Acme kettle?"` (02:13:52) | 0 | 8.5 s | 200: "The warranty is **27 months from the delivery date**." 1 model call, 228 in / 16 out, with `sources` |
| 8 | `invoke … "Who pays return shipping?"` (02:14:01) | 0 | 2.6 s | 200: "Acme pays return shipping only for faulty items." 1 model call, 222 in / 14 out |
| 9 | spend log (read under 30 s after step 8) | | | `acme-embedding` (`openai/text-embedding-3-small`): 6 success, 110 tokens, $0.000002; `chat`: 4 success, 907 in / 76 out, $0.005148; `judge`: 2 success, 358 in / 75 out, $0.002932. No failures |
| 10 | teardown | 0 | | snapshot matched |

### Anthropic retry

| Step | Command (UTC start) | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `new agent prov-anthropic --model anthropic` (02:14:49) | 0 | | `anthropic/claude-opus-5-5` on both aliases |
| 2 | `services up` (02:14:50) | 0 | 49.2 s | |
| 3 | `release` (02:15:39) | 5 | 25.1 s | `Eval gate failed: 0/2 passed`. Both cases: `agent_responded` false, `HTTPError: 502` |
| 4 | spend log | | | `chat`: 2 failure rows. No judge call was made |
| 5 | teardown | 0 | | snapshot matched |

The script's `invoke` calls for Anthropic also left out `--stack` (`NO_STATE`). They were moot,
since the gate kept production empty.

LiteLLM's log gives the same cause as before: "This API key is not scoped to a workspace, so this
request must include the anthropic-workspace-id header with the ID of the workspace to use." The
log was read during the run and not kept. As instructed, no header was added and `config_review`
was not changed. Anthropic stays unverified until the owner supplies a workspace-scoped key, or
decides whether AgentBreeder should support the header.

### Re-run: acceptance, leak scan and what changes

- openai (chat and embeddings) now qualifies for a `Provider.verified` flip. That flip goes to a
  code lane, and the results were sent to the coordinator. This PR changes no code.
- anthropic still does not qualify.
- Leak scan: the re-run's saved outputs and project directories (85 files) were scanned for each
  `~/.env` value of 12 or more characters (8 values, plain, base64 and URL-encoded) and for
  `/Users/`: **0 hits**. The key files were deleted after the runs.
- After each of the three runs, the containers, networks, volumes and images matched the pre-run
  snapshot. Only the image each run built was removed.
- Costs are LiteLLM's estimates, not a bill: about $0.015 in all for the two OpenAI runs. The
  Anthropic calls failed and cost nothing.

## Third Anthropic run 2026-11-16: a new key, still refused

The owner put a new `ANTHROPIC_API_KEY` in `~/.env` and asked for one more Anthropic run; #1020
stays open for Anthropic only. This used a wheel from `main` at 3e4b4869 (3.0.0a3, the starter on
`runtime/adk@1.4.0`), `agentbreeder-3.0.0a3-py3-none-any.whl`, sha256
`cbebf4da89aa690422a920d7b815eda6a8cc53e5f128326cfa1cb6abee04a965`, installed with `[eval]` into a
fresh Python 3.12 venv, in the same sandbox as above (`env -i`, fresh `HOME` and `TMPDIR`,
`USER=demo`, a Docker context in the sandbox `HOME`). A 0600 file held only the `ANTHROPIC_API_KEY`
line from `~/.env`. The starter needs no other key.

| Provider | Model | Result |
|---|---|---|
| anthropic | `anthropic/claude-opus-5-5` (the default) | **Not verified, same refusal:** Anthropic says the key is not scoped to a workspace. Retried once with no header or config change |

| Step | Command (UTC start) | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `new agent prov-anthropic --model anthropic` (23:23:13) | 0 | 0.7 s | `anthropic/claude-opus-5-5` on both aliases; it printed the config-only notice |
| 2 | `services up prov-anthropic --environment dev --env-file <key> --yes` (23:23:27) | 0 | 133 s | |
| 3 | `release prov-anthropic/agent.yaml --stack prov-anthropic --environment dev` (23:25:40) | 5 | 124 s (with the build) | `Eval gate failed: 0/2 passed`. Both cases, 2 attempts each: `agent_responded` false, empty reply, explanation `HTTPError: 502` |
| 4 | `invoke`, twice, with `--stack` (23:27:44) | 2, 2 | | `NOT_DEPLOYED`: the release said "production unchanged", so there was nothing to invoke |
| 5 | `release` again, unchanged (23:28:03) | 5 | 10 s | the same `0/2`, `HTTPError: 502` |
| 6 | spend log (after step 5) | | | `chat`: 4 failure rows, no `judge` rows (see below for the count) |
| 7 | teardown (`services down prov-anthropic --purge --yes`, then `docker rmi` by ID of the images the run created) | 0 | | containers, networks, volumes and images matched the pre-run snapshot |

The error class is unchanged: LiteLLM logged `litellm.BadRequestError: AnthropicException`,
`invalid_request_error`, "This API key is not scoped to a workspace, so this request must include
the anthropic-workspace-id header with the ID of the workspace to use. Add the header, or use an
API key that is scoped to a workspace." on 16 lines, 2 per failed call, so 8 calls: each release
ran 2 cases with 2 attempts (the eval reports). The spend log query after the retry returned only
4 `chat` failure rows; why it held 4 rather than 8 was not checked. The log was read during the
run and not kept.

The key's format was checked without reading its value: it is one unquoted line of 106 characters
with the prefix `sk-ant-usr-`, not `sk-ant-api03-`, and Anthropic's error says it is not scoped to
a workspace. AgentBreeder's gateway config review allows no custom headers (as above), so the
starter cannot send `anthropic-workspace-id`. To run it again, the owner needs a key that Anthropic
accepts without that header (a workspace-scoped key), or a decision on supporting the header. No
header or config was changed here, and `Provider.verified` is unchanged.

- Leak scan: the run's saved outputs and project directory (34 files, outside the venv and the
  checkout) were scanned for each `~/.env` value of 12 or more characters (8 values, plain, base64
  and URL-encoded), for the Anthropic and Gemini key patterns, and for `/Users/`: **0 hits**. One
  earlier hit was the sandbox Docker context's socket path, which holds a home path and no key;
  the context was deleted before the final scan. The key file was deleted.
- Teardown: the release build left untagged intermediate images as well as the tagged ones. Each
  was created during the run and was not in the pre-run snapshot, and each was removed by ID; no
  prune was run: 4 new images (the agent, its Python base, Postgres and LiteLLM) and about 9
  untagged intermediate layers created between 23:25 and 23:27 UTC. The snapshot matched
  afterwards (3 images, no containers or volumes).
- No Anthropic call succeeded.
