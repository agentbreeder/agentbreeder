# #1019 verification: gateway budget, rpm, retries and fallback on a live LiteLLM

Status: **partial, verified from `main`.** First run: 89cf804, after #1016 merged, 2026-11-15.
Re-check: 3f13edb4 and 310fc6f6, after #1034 and #1039, 2026-11-19 (S3 and S4 below). Both on
macOS 13.7.8 x86_64 with Docker Desktop engine 27.5.1. The 2026-11-15 results below are from a live
gateway (LiteLLM 1.106.2 from `service/litellm@1.0.0`) with real provider calls; the
2026-11-19 update follows them:
- **Budget:** a deployment key with `max_budget` 0.0001 was refused (LiteLLM `422 budget_exceeded`)
  after its first turn. **`invoke` and the console do not show this as a budget error.** Both
  report `502 The model service is unavailable; please try again`.
- **rpm:** with `rpm: 2`, the third and fourth direct calls in a minute got `429 throttling_error`
  (`retry-after: 60`). An `invoke` started at the cap, right after those 429s, did not fail. It
  took 67.6 s and then answered. A 429 through `invoke` in a fresh window was not tried.
- **Retry:** seen. With two deployments behind one alias, one of them broken, 4 of 20 calls were
  answered after `x-litellm-attempted-retries: 1`. **Cooldown:** not seen consistently; see the
  table.
- **Fallback:** Northwind's `support-model` → `support-model-openrouter` worked with the real
  OpenRouter key under the production deployment's scoped key, both for direct calls and for a
  real `invoke` turn. That key gets **403** when it asks for `support-model-openrouter` directly.
  So a fallback reaches an alias the key is not granted (**#1033**).

On 2026-11-15 the opt-in Docker test was not added; it came in the 2026-11-19 PR. In that first
run the leak scan found 0 hits, and Docker was torn down to the pre-run snapshot, with one
exception: a pre-existing image was removed (see Leak scan and teardown).

**Update, 2026-11-19:** after #1034 and #1039, budget and rpm reach `invoke` as coded errors
(`MODEL_BUDGET_EXCEEDED` 402, `MODEL_RATE_LIMITED` 429), live with Gemini (S3). The opt-in Docker
test now exists and passed (S4). The console was not re-checked, and cooldown is still not seen
consistently.

This is on `main`, not in a published preview.

## Why `support-model-openrouter` is not in `environment.models`

This was found by reading the code, then checked live:
- `services up` prints `WARNING: gateway config serves aliases the environment doesn't grant:
  ['support-model-openrouter']`. It comes from `services/lock.py`, which warns about every alias
  the gateway config serves that `environment.models` does not list. Northwind's `stack.yaml`
  lists `support-model`, `support-embedding` and `support-judge`. The fallback alias exists only in
  `stack/litellm/config.yaml` (`model_list` and `litellm_settings.fallbacks`).
- The deployment key's `models` come from `build.py`: the agent's `model.alias`, plus each
  knowledge version's embedding alias (`support-model`, `support-embedding`). Fallback targets are
  not added. `deployment.py` mints the key with that list (`gateway_keys.LiteLLM.mint`).
- Live, LiteLLM 1.106.2 serves the fallback anyway (below). Northwind does not need the grant for
  the fallback to work. The open question is whether it should: today `environment.models` and
  the key's `models` do not limit what a fallback can reach. Filed as **#1033**; the fix (grant
  the alias and check that fallback targets are granted, or document it) belongs to a code lane.
  The example was not edited.

## Wheel from main (clean sandbox)

`main` at 89cf804, no tracked changes. The wheel was built with `python -m build --wheel`
(`agentbreeder-3.0.0a2-py3-none-any.whl`, sha256
`ffe84b9090146ac4df97a3b885156b5328086c3ae785b4218dc3d86821ea5902`) and installed with `[eval]` into a
fresh Python 3.12 venv. Every command ran under `env -i` with a fresh `HOME`, `TMPDIR` inside
the sandbox, `PIP_NO_CACHE_DIR=1` and `USER=demo`. The sandbox was in `/tmp`, not in a path that
names the OS user. A Docker context was created inside the sandbox `HOME`, since AgentBreeder
refuses `DOCKER_HOST`. Only the key lines each run needed were copied from `~/.env` into 0600 files
in the sandbox `HOME`: Gemini, Tavily and OpenRouter for Northwind, and Gemini for the starter.
The files were deleted after the runs.

Gateway calls were made with a small Python script. It reads the deployment's own key file
(`state/deployments/<id>/secrets/llm-gateway-key`), sends one short chat completion ("Reply with
the single word: ok", `max_tokens` 20) to the stack's LiteLLM, and saves the status, the
`x-litellm-*` headers, the model that served it and the error's type, code and message. It never
saves the key or the reply. Spend rows were read with `psql` in the stack's Postgres
(`LiteLLM_SpendLogs`).

### S1: Northwind (fallback, retry, cooldown)

| Step | Command (UTC start) | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `example northwind <NW>` | 0 | 0.6 s | |
| 2 | `services up <NW> --environment dev --env-file <keys> --yes` (22:14:01) | 0 | 253.8 s | 5 services; the `support-model-openrouter` warning above |
| 3 | `release <NW>/agent.yaml --stack <NW> --environment dev` (22:18:19) | 0 | 186.0 s | `61/61 evals passed`, first attempt; production `05b1c96f…` |
| 4 | direct calls with the production key, config unchanged (22:21:45) | | | table below |
| 5 | sandbox-only config edit: `support-model` → `gemini/gemini-0-no-such-model`; `services up … --update` (22:22:22) | 0 | 55.0 s | only `litellm` recreated |
| 6 | 6 direct calls to `support-model` (22:23:18–22:23:39) | | | all 200, served by the fallback |
| 7 | config edit: primary back to `gemini/gemini-3.8-flash` with `timeout: 0.01`; `--update` (22:24:12) | 0 | 58.1 s | |
| 8 | 8 calls with fallback, then 6 with `disable_fallbacks: true` (22:25:11–22:26:31) | | | table below |
| 9 | config edit: a second, healthy `support-model` deployment beside the 10 ms one; `--update` (22:26:49) | 0 | 53.1 s | |
| 10 | 20 calls with `disable_fallbacks: true`, with a 35 s pause after the 14th (22:27:48–22:28:54) | | | table below |
| 11 | config edit: primary `gemini/gemini-0-no-such-model` again; `--update` (22:29:18) | 0 | 65.3 s | |
| 12 | `invoke northwind-support "How do I reset my password?" --json` (22:30:24) | 0 | 10.8 s | 200, a correct reply citing Account and security; `model: support-model`, `cost_usd` 0.00144525 |
| 13 | `remove 05b1c96f… --local`, `services down <NW> --purge --yes` | 0, 0 | 1.9 s, 13.2 s | |

The `--update` steps changed only the sandbox copy's `stack/litellm/config.yaml`. `services up`
refuses a changed path ref with `HASH_MISMATCH` unless `--update` is given.

**Step 4, config unchanged (production key, granted `support-model` and `support-embedding`):**

| Call | Status | What came back |
|---|---|---|
| `support-model` | 200 | `x-litellm-model-group: support-model`, Gemini API base, `attempted-retries 0`, `attempted-fallbacks 0`, `x-litellm-key-max-budget: 25.0` (Northwind's `budget_usd`) |
| `support-model-openrouter`, asked for directly | **403** | `key_model_access_denied`: "The requested model 'support-model-openrouter' is not available for this API key" |
| `support-model` with `mock_testing_fallbacks: true` | 400 | "Mock testing request params are disabled on this proxy" (so a client cannot use LiteLLM's mock parameters) |

A chat call to `support-embedding` was also sent by mistake. It is an embedding alias, so the 404
it got says nothing about limits.

**Step 6, primary broken (unknown model id):** all 6 calls returned 200, with
`x-litellm-model-group: support-model-openrouter`, `attempted-fallbacks: 1`, `attempted-retries:
0` and served model `google/gemini-3.8-flash`, in 1.6 to 12.2 s. The spend log has 6 `success`
rows for `support-model-openrouter` / `openrouter/google/gemini-3.8-flash` under the production
key's alias (`agentbreeder-05b1c96f…`), at about $0.000065 each. In that window (22:23:18 to
22:23:39) the failed primary attempts have no rows of their own. LiteLLM does not retry a 404 (`attempted-retries` 0).

**Step 8, primary times out (10 ms):** with fallbacks on, all 8 calls were served by
`support-model-openrouter` (`attempted-fallbacks: 1`) in 4.0 to 14.2 s. Step 6's calls took 1.6
to 2.6 s, apart from one at 12.2 s. With `disable_fallbacks: true`, all 6 returned **408**
`litellm.Timeout` ("Timeout passed=0.01, time taken=0.011 seconds") in 2.4 to 3.1 s, though each
attempt failed in about 0.01 s. The extra time fits a retry after `retry_after: 1`, but the
headers on an error do not show the retry count, so this is not proof. None of these calls ever
got "No deployments available": LiteLLM did not cool down an alias that has only one deployment.

**Step 10, one broken (10 ms) and one healthy deployment behind `support-model`, fallbacks off**
(`num_retries: 1`, `allowed_fails: 3`, `cooldown_time: 30`):

| Calls | Outcome |
|---|---|
| 1, 9, 11, 15 | 200 from the healthy deployment with `x-litellm-attempted-retries: 1`: the first try failed, the retry succeeded. **This is the retry, seen.** |
| 2, 10 | 408 in about 0.1 s, with no retry headers |
| 3–8, 12–14, 16–20 | 200 from the healthy deployment with `attempted-retries: 0` |

Cooldown: after call 2's failure, calls 3 to 8 all went straight to the healthy deployment, which
is what a cooldown would do. But call 9 reached the broken deployment again 10 s after call 2,
sooner than the 30 s `cooldown_time`. Call 11, right after call 10's failure, reached the broken
deployment first too (`attempted-retries: 1`). LiteLLM 1.106.2 does not expose its cooldown list to
a client, and its log level here is `WARNING`. **Cooldown is recorded as not seen consistently.**
Why calls 2 and 10 got no retry was not looked into.

**Step 12, a real agent turn with the primary broken:** the turn answered. Its chat call is in
the spend log as `support-model-openrouter` / `openrouter/google/gemini-3.8-flash`, `success`,
0.001445, under the production key, the same as the runtime's `cost_usd`. Its embedding call went to
`support-embedding` (Gemini) as usual. The `invoke` output names `support-model` and does not say
that a fallback served the turn.

### S2: gemini starter (budget and rpm)

`new agent lim-gem --model gemini` (the starter: `chat` and `judge` aliases, ceilings $5 and 60
rpm).

| Step | Command (UTC start) | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `services up <G> --environment dev --env-file <key> --yes` (22:31:55) | 0 | 77.7 s | |
| 2 | `release <G>/agent.yaml --stack <G> --environment dev` (22:33:13) | 0 | 36.8 s | `2/2 evals passed`; production `6e4961c6…` |
| 3 | `agent.yaml`: `model.budget_usd: 0.0001`; `build` (22:33:57) | 0 | 1.3 s | `model_access.budget_usd: 0.0001` in the deploy manifest |
| 4 | `deploy deploy/lim-gem.deploy.yaml --local …` | 2 | | `[DEPLOYMENT_ERROR] Agent name already has a different spec; remove it first` (seen in the terminal; the saved output was overwritten by step 5's retry under the same name) |
| 5 | `remove 6e4961c6… --local`, then the same `deploy` (22:34:13) | 0, 0 | 1.8 s, 9.2 s | `449e60ed…` ready |
| 6 | `invoke lim-gem "When is support open?" --json`, 3 times (22:34:23–22:34:32) | 0, 1, 1 | 7.7, 1.5, 1.0 s | 200 with a correct reply ($0.00015375); then **502** `{"error": "The model service is unavailable; please try again"}` twice |
| 7 | a direct call with that key (22:34:41) | | | **422** `budget_exceeded`: "Budget has been exceeded! Key=agentbreeder-449e60ed… (sk-...XXXX) Current cost: 0.00015375…, Max budget: 0.0001" |
| 8 | console: `up --no-browser`, add the project, the agent's Try it, send "When is support open?" (22:35:26) | | 2.6 s | "No usable reply: The agent answered 502: The model service is unavailable; please try again [AGENT_ERROR]. The agent may have run this turn; sending it again is a second turn." |
| 9 | `agent.yaml`: `rpm: 2` instead of the budget; `build`, `remove 449e60ed…`, `deploy` (22:35:45) | 0, 0, 0 | 1.1, 2.0, 7.2 s | `134a3cb8…` ready |
| 10 | 4 direct calls with that key (22:36:02–22:36:05) | | | 200, 200, then **429** twice: `throttling_error`, "Limit type: requests. Current limit: 2, Remaining: 0", `retry-after: 60` |
| 11 | `invoke … --json`, twice, straight after step 10 (22:36:05) | 0, 0 | **67.6 s**, 3.2 s | both 200 with correct replies; the first waited out the rpm window |
| 12 | `remove 134a3cb8… --local`, `services down <G> --purge --yes`, `docker rmi` of the image this run built | 0, 0 | 1.9 s, 6.9 s | |

How each limit reaches the user:
- **Budget:** the budget is checked before each call, against the spend so far. The first turn
  therefore ran and went over (0.000154 against 0.0001). Every turn after it fails. The runtime
  turns LiteLLM's 422 into a generic 502, so neither `invoke` nor the console Try it names the
  budget, and both suggest trying again, which cannot help until the 30-day `budget_duration`
  resets. The cause is visible only in the agent container's log (LiteLLM's `BadRequestError` text,
  read live with `docker logs` and not saved) or with a direct gateway call.
- **rpm:** the agent did not fail; the turn was only slow (67.6 s, about the 60 s window). That its
  model client waited and retried is inferred from the timing. No 429 or rate-limit message reached
  `invoke`. A live `docker logs` grep of the agent container for 429, rate or retry found nothing
  (not saved).
- LiteLLM's budget message includes the last 4 characters of the virtual key (`sk-...XXXX`, masked
  here). It is in the saved direct-call result, and the agent container's log carried it too (read
  live, not saved). The 429 message names the key's SHA-256
  token hash. Whether `agentbreeder logs` redacts either was not checked: `logs lim-gem` was
  refused with `ID_INVALID` (it takes a deployment ID), and the container was removed before it
  was run again.

## Re-check after the #1033 fix (PR #1046)

Run 2026-11-15 from a wheel built from branch `fix/1033-fallback-grant` at `038f6cc8`
(`agentbreeder-3.0.0a2-py3-none-any.whl`, sha256
`af1f96ab894dd0bfbeb654f43555f33298ebb4598d945aab7b39bff2249bdbdd`), on the same Mac, Docker
engine 27.5.1, LiteLLM 1.106.2. The sandbox was set up as above: a fresh Python 3.12 venv,
`env -i`, a fresh `HOME` in `/tmp`, `USER=demo`, a sandbox Docker context, and only the
`GEMINI_API_KEY` and `OPENROUTER_API_KEY` lines in a 0600 env file, deleted after the run. The
project was `new agent fb --model gemini` (aliases `chat` and `judge`), edited in the sandbox only:
`chat` points at `gemini/gemini-0-no-such-model`, a new `chat-or` alias points at
`openrouter/google/gemini-3.8-flash`, and `litellm_settings.fallbacks: [{"chat": ["chat-or"]}]`.

| Step | Command (UTC start) | Exit | Time | Result |
|---|---|---|---|---|
| 1 | `services up <fb> --environment dev --env-file <keys> --yes`, `chat-or` not in `environment.models` (23:47:36) | 2 | 0.8 s | `[GATEWAY_FALLBACK_UNGRANTED] the gateway config falls back to aliases environment.models doesn't grant: ['chat-or']; …`. Nothing started |
| 2 | `stack.yaml`: `models: [chat, chat-or, judge]`; the same `services up` (23:47:43) | 0 | 158.6 s | the grant lists `chat-or: openrouter (OPENROUTER_API_KEY)`; the operator config has `fallbacks: {chat: [chat-or]}` |
| 3 | `release <fb>/agent.yaml --stack <fb> --environment dev` (23:50:27) | 0 | 50.0 s | `2/2 evals passed` with the primary broken; production `cd3ad4ac…` |
| 4 | `status cd3ad4ac… --json` | 0 | | `gateway_key.models: ["chat", "chat-or"]` |
| 5 | `audit list --json` | 0 | | both `gateway_key` events (candidate, production) carry `GATEWAY_KEY_MODELS: chat, chat-or` |
| 6 | direct calls with the production key: `chat` twice, `chat-or`, `judge` | | | `chat`: 200, `x-litellm-model-group: chat-or`, `attempted-fallbacks: 1`. `chat-or`: 200 (granted now). `judge`: **403** `key_model_access_denied` (not this key's alias) |
| 7 | `invoke fb "When is support open?" --json` (23:52:27) | 0 | 8.0 s | 200, a reply, `model: chat`, `cost_usd` 0.00012375. The spend log row for it (read live with `psql`, not saved) is `chat-or` / `openrouter/google/gemini-3.8-flash`, `success` |
| 8 | `remove cd3ad4ac… --local`, `services down <fb> --purge --yes` | 0, 0 | 7.2 s, 11.7 s | |

- The fallback is now inside the grant: an ungranted target stops `services up`, and a granted one
  is on the key explicitly and served (steps 3, 6, 7).
- `invoke --json` still names `chat` and does not say that a fallback served the turn. That half
  of #1033 is #1039 (a new runtime version).
- Leak scan of the 12 saved outputs (command output and the call results, which hold status and
  headers only, never the key or the reply) for both key values and `/Users/`: **0 hits**.
- Teardown: the only image the run built (the agent image, not in the pre-run snapshot) was
  removed by ID. Containers, images and volumes then matched the snapshot (compared live, not saved). Docker's default
  `bridge` network had a new ID, created at 23:48:33 UTC during step 2, which is what a Docker
  daemon restart does; no command in this run restarts Docker. It was reported to the
  coordinator.

## Re-check after #1034 and #1039, and the opt-in Docker test (2026-11-18/19)

After #1033 (fallback inside the grant), #1034 (runtime `adk@1.3.0`+ turns budget and rpm
refusals into coded errors) and #1039 (`fallbacks` and `served_by` on a turn) merged.

### S3: live Gemini, budget and rpm through `invoke`

A wheel of `main` (3f13edb4, `agentbreeder-3.0.0a3-py3-none-any.whl`), installed and run as
above (fresh Python 3.12 venv, `env -i`, fresh `HOME` and `TMPDIR` in `/tmp`, `USER=demo`, a sandbox
Docker context, only `GEMINI_API_KEY` in a 0600 env file). The project was the #1111 step-cap
agent (`new agent cap --model gemini`, `runtime/adk@1.4.0`, `gemini/gemini-3.8-flash`, LiteLLM
1.106.2 from `service/litellm@1.0.0`; see `1111-adk-step-cap-gemini.md`), redeployed with a
budget and then with an rpm cap. Each limit was set in `agent.yaml`, then `build`, `remove` of the
running deployment and `deploy --local`.

| Step | Command (UTC, 2026-11-19) | Result |
|---|---|---|
| 1 | `model.budget_usd: 0.0001`; build, remove, deploy (00:24) | `a587a3f8…` ready |
| 2 | `invoke cap "When is support open?" --json`, 3 times, new session each (00:25–00:26) | 200 with a correct reply (`cost_usd` 0.0002205, over the budget); then **402 `MODEL_BUDGET_EXCEEDED`** twice, `retry_after_s: null`, "Sending it again won't help: raise model.budget_usd …" |
| 3 | `rpm: 2` instead of the budget; build, remove, deploy (00:26) | `42cd12bd…` ready |
| 4 | 4 direct calls with that key | 200, 200, then **429** `throttling_error` twice, `retry-after: 60` |
| 5 | `invoke … --json`, twice, straight after step 4 (00:26–00:27) | **429 `MODEL_RATE_LIMITED`** both times, `retry_after_s: 60`, "Wait and send the message again, or raise model.rpm …" |

Both limits now reach `invoke` as coded errors. On 2026-11-15 they were a generic 502 and a
67.6 s wait. The console's Try it was not re-checked.

### S4: the opt-in Docker test

`tests/test_gateway_limits_docker.py` (new in this PR) runs postgres, `service/litellm@1.3.0` and two
scripted providers. `provider-ok` answers every call and reports 200,000 prompt tokens, so each
call has a price. `provider-bad` answers 500. No provider key is needed. It mints keys the way
`deploy` does and checks each limit twice: with a direct gateway call, and with a real
`runtime/adk@1.4.0` turn (the agent image is built by `build` and run on the stack's agents
network). The one test-only change is the config review accepting `api_base`, as in
`tests/test_guardrails_docker.py`.

`AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_gateway_limits_docker.py --no-cov -s`, on this
branch (`main` 310fc6f6 plus the test), 2026-11-19: **1 passed in 220.26 s** on the first run.
After the review edits (snapshot-aware image teardown, a stronger body assertion), the final file
was run again: **1 passed in 196.82 s**, with the same results.

| Limit | Direct call | Runtime turn |
|---|---|---|
| budget (`max_budget` 0.001, first call costs more) | 422 "Budget has been exceeded!" | **402 `MODEL_BUDGET_EXCEEDED`**; the body has no LiteLLM budget text and no `sk-` key fragment |
| rpm 2 | 200, 200, 429 | **429 `MODEL_RATE_LIMITED`**, `retry_after_s` 60, after 17.0 s |
| fallback `main` (provider-bad) → `backup`, key granted both | 200 "from OK", and provider-bad's log shows it was tried | 200, reply "from OK", `fallbacks` ≥ 1, `served_by` includes `backup` |
| a key granted only `chat` asks for `backup` by name | refused (the test asserts 401 or 403; the code was not printed) | n/a |

Docker was snapshotted before the first run and after each run (`docker ps -a`, `network ls`,
`volume ls`, `images`, the default `bridge` ID). After both runs, the lists matched the snapshot,
apart from one image another session built between the runs (`agentbreeder-cloud:gate-check`).
That image was left alone. The test's teardown removes only
agent images that were not present before it ran.

S3's saved outputs (the build, deploy and remove output, the `invoke --json` results and the
direct-call results) were scanned for the two key values in the sandbox env file (Gemini and
OpenRouter; OpenRouter was imported by the gateway but no alias used it), plain, base64 and
URL-encoded, and for `/Users/`. There were **0 hits**. The S3 stack's teardown is in the #1111
record (`1111-adk-step-cap-gemini.md`).

## Acceptance (from the issue)

| Check | Result |
|---|---|
| A key with a tiny budget is refused once spent (coded error surfaced by invoke/console) | **Partial.** Refused once spent: PASS (LiteLLM 422 `budget_exceeded` after the first turn). Coded error in invoke and the console: **FAIL**: both show a generic 502 "model service is unavailable" (`AGENT_ERROR` in the console). **Now PASS for invoke** (S3: 402 `MODEL_BUDGET_EXCEEDED`, live Gemini; S4: the opt-in test). The console was not re-checked |
| An rpm cap returns 429 | **PASS** for direct calls (429, `retry-after: 60`). Through `invoke`, the cap delays the turn (67.6 s) rather than returning 429. **Now PASS through invoke** (S3 and S4: 429 `MODEL_RATE_LIMITED` with `retry_after_s` 60) |
| A retry/cooldown actually happens | **Partial.** Retry: PASS (`x-litellm-attempted-retries: 1`, then success). Cooldown: not seen consistently |
| A forced primary failure falls back to the second alias under the deployment's scoped key | **PASS** (real OpenRouter key; 6 direct calls and one `invoke` turn), with the finding that the key is not granted that alias (#1033). The opt-in test (S4) covers it with scripted providers, and the turn reports `fallbacks` and `served_by` (#1039) |
| Decide and fix the Northwind `environment.models` grant (or document why not) | **Cause found, not fixed** in this run: the fallback worked without the grant. **Fixed by #1033 (PR #1046):** Northwind grants `support-model-openrouter`, `services up` refuses an ungranted fallback target, and keys are minted with granted targets. Re-checked live with a starter; see "Re-check after the #1033 fix" |
| Opt-in Docker test | **PASS.** `tests/test_gateway_limits_docker.py` (S4) covers budget, rpm and fallback, both direct and through a real `runtime/adk@1.4.0` turn. On 2026-11-19 it passed on the first run (220 s) and on the final file (197 s) |
| Verification record `docs/superpowers/verification/gateway-limits.md` | This file |

Flows that need a model key ran here: Northwind with `gemini/gemini-3.8-flash` and
`openrouter/google/gemini-3.8-flash` through LiteLLM, and the starter with
`gemini/gemini-3.8-flash`. On 2026-11-19, S3 used `gemini/gemini-3.8-flash`; S4 needs no key.

## Browser click-through

Playwright with Chrome against the real stack: one screenshot of the starter's Try it after the
budget was spent (S2 step 8). It was kept in the sandbox and not committed. No Claude in Chrome
GIF was made.

## Leak scan and teardown

- Every saved output (284 files: command output, the gateway-call results, the spend-log reads,
  the console log and screenshot) was scanned for each `~/.env` value of 12 or more characters (7
  values, plain, base64 and URL-encoded) and for `/Users/`: **0 hits**. The deployment keys were
  never written to an output: the call script reads them and prints only status and headers.
  The key files were deleted, and no AgentBreeder process was left running.
- Docker was compared with the snapshot taken before S1. Containers, volumes and networks match
  it. Images: each image a run built was removed by ID (the S2 starter image). One exception:
  S1's teardown removed `5aeee5ee947e`, the Northwind agent image. That build gives the same image
  ID every time (it matches the #907 record), and the image was **already in the pre-run
  snapshot**, so this teardown removed an image the run did not create. The coordinator told the
  owner. Later teardowns removed only images missing from the snapshot.
- A container outside AgentBreeder (`supabase_db_norulestrends`) was in the pre-run snapshot and
  had gone by the end of S1. No command in this run touches it. The coordinator found no destroy
  event for it, its volumes are intact, and it was reported to the owner.

## Notes

- Costs here are LiteLLM's estimates, not a bill. The whole session (both records) spent well under
  $1 on the provider keys.
- The S1 config edits were made only in the sandbox copy of Northwind. The example in the repo is
  unchanged.
- Code-lane candidates, sent to the coordinator: #1033 (a fallback is not limited by the key's
  `models`); the runtime maps `budget_exceeded` (and other gateway 4xx) to a generic 502;
  `invoke` does not show that a fallback served a turn. The last two have no issue yet.
