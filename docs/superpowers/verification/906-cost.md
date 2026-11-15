# #906 verification: LLM cost per deployment (CLI and console)

Status: **verified from `main`** (2b4e4cf, 2026-11-15, macOS 13.7.8 x86_64, Docker Desktop
engine 27.5.1). After a Northwind release (61 eval cases), one Try it turn and 5 `invoke` turns:
- `agentbreeder cost` matched LiteLLM's own spend log exactly, per deployment key and per model:
  260 calls, 233,191 tokens in, 24,279 out and USD 0.264939 in total. The comparison was a SQL
  query on `LiteLLM_SpendLogs` in the stack's Postgres.
- The console's **LLM cost** cards on Home and on the agent's Overview showed the same figures.
- The per-turn `cost_usd` the runtime reports added up to the production key's chat spend
  (within rounding).

The numbers are LiteLLM's estimate from its model price list, **not a bill**. Nothing here was
compared with a provider invoice. The optional per-key budget (`max_budget`) was not run.

One docs defect was filed: **#1011** (`docs/cost.md` says per-turn `cost_usd` is planned, but it
is emitted). The leak scan found 0 hits. Docker was torn down to the pre-run snapshot.

This is on `main`, not in a published preview.

## What changed (4 PRs)

| PR | Merged | Content |
|---|---|---|
| #924 | ba183eb | CLI: `agentbreeder cost`, LLM cost per deployment and per model from LiteLLM's spend logs |
| #933 | eabef68 | Console: Try it shows each turn's cost |
| #1003 | 504b126 | Console: LLM cost on Home and on an agent's Overview |
| #1008 | 2b4e4cf | (the `main` this ran on; no cost change) |

The full suite was not re-run for this record.

## Wheel from main (clean sandbox)

The same wheel, sandbox and stack as the #907 record (`907-tracing.md`): `main` at 2b4e4cf, wheel
`agentbreeder-3.0.0a2-py3-none-any.whl` with sha256
`c4c38f3bc2fa15288b8d579fbccab7cdac4370c723cca9aa5fb1a5621c724251`. Every command ran under
`env -i` with a fresh `HOME`, in a fresh Python 3.12 venv with `[eval]`. Only the `GEMINI_API_KEY`
and `TAVILY_API_KEY` lines of `~/.env` were copied, into a 0600 file that was deleted after the
run. The stack is Northwind's (`service/litellm@1.0.0`, which pins the LiteLLM image), released
61/61 on its first attempt (191.9 s).

## Calls made

All went through LiteLLM with per-deployment virtual keys (`agentbreeder-<deployment id>`) and
the eval judge's run key (`agentbreeder-eval-<run>`):

| Source | Key | Calls (from the spend log) |
|---|---|---|
| `release`: the candidate answering 61 eval cases, with retries | `agentbreeder-3788f514…` (candidate, removed) | 182 |
| `release`: the eval judge | `agentbreeder-eval-20261115-122017-fbd4f1` | 62 |
| 1 Try it turn (907-tracing.md) and 5 `invoke` turns (below) | `agentbreeder-c841f364…` (production) | 16 (9 chat, 7 embedding) |

The 5 `invoke --json` turns (20:25:14 to 20:25:37 UTC, exit 0 each, 3.5 to 7.7 s):

| Turn | Model calls | Tokens in / out | `cost_usd` (runtime) |
|---|---|---|---|
| "How do I reset my password?" | 1 | 1774 / 106 | 0.001728 |
| "What is the refund policy for monthly plans?" | 1 | 1751 / 39 | 0.0014595 |
| "Does the Northwind Pen work with the iPad app?" | 3 | 5997 / 97 | 0.0048615 |
| "How do I change my billing email?" | 1 | 1787 / 92 | 0.00168525 |
| "Can I export my notes to PDF?" | 1 | 1826 / 78 | 0.001662 |

The Try it turn added 2 model calls, 3,663 in / 211 out and $0.003538.

## LiteLLM's own spend log

At 20:26:02, before `cost` ran, a count over the table gave 260 rows, and the newest
`startTime` was 20:25:36, a call of the last `invoke` (which ran from 20:25:34 to about
20:25:37). So every call had been written within about 30 s. That check was seen only in the
terminal and was not saved; the saved tables below show the same 260 calls. They were read just
after it, also before `cost`. The query ran in the stack's Postgres
container (`psql -d litellm`), grouped by `metadata->>'user_api_key_alias'`:

| key_alias | calls | failed | prompt_tokens | completion_tokens | sum(spend) |
|---|---|---|---|---|---|
| `agentbreeder-3788f514…` | 182 | 0 | 201439 | 9934 | 0.18737035 |
| `agentbreeder-eval-20261115-122017-fbd4f1` | 62 | 0 | 14882 | 13722 | 0.06261900 |
| `agentbreeder-c841f364…` | 16 | 0 | 16870 | 623 | 0.01494915 |

Grouped by `model_group, model`:

| model_group | model | calls | prompt | completion | spend |
|---|---|---|---|---|---|
| support-model | gemini/gemini-3.8-flash | 113 | 216489 | 10557 | 0.20195550 |
| support-judge | gemini/gemini-3.8-flash | 62 | 14882 | 13722 | 0.06261900 |
| support-embedding | gemini/gemini-embedding-2 | 85 | 1820 | 0 | 0.00036400 |

The production key's split was `support-model` with 9 calls, 16798 / 623 and 0.01493475, plus
`support-embedding` with 7 calls, 72 / 0 and 0.00001440.

## `agentbreeder cost`

| Command (20:26 UTC) | Exit | Time | Result |
|---|---|---|---|
| `cost --stack <NW> --environment dev` | 0 | 2.1 s | three rows, equal to the first SQL table: candidate `removed` 182 / 0 / 201439/9934 / 0.187370; `eval run 20261115-122017-fbd4f1` 62 / 0 / 14882/13722 / 0.062619; production `ready` 16 / 0 / 16870/623 / 0.014949; Total 260 / 0 / 233191/24279 / **0.264939**; window 2026-10-17 to 2026-11-15 (UTC); the "estimate, not a bill" note |
| the same with `--json` | 0 | 2.3 s | `total` `{calls 260, failed 0, prompt_tokens 233191, completion_tokens 24279, cost_usd 0.2649385, unpriced 0}`, `source: "litellm spend logs"`, `truncated: false`; keys at 8 decimals equal the SQL sums |
| `cost --stack <NW> --environment dev --by model` | 0 | 2.4 s | three rows, equal to the second SQL table |

## Console (Playwright, real stack)

| Time (UTC) | Card | Seen |
|---|---|---|
| 20:28:33 | Home, **LLM cost**, last 30 days | "USD 0.264939, 260 calls, 0 failed; 2026-10-17 to 2026-11-15 (UTC days); as of 2026-11-15 20:28:33 UTC". Per model: support-model 113 / 216,489 / 10,557 / USD 0.201956; support-judge 62 / 14,882 / 13,722 / USD 0.062619; support-embedding 85 / 1,820 / 0 / USD 0.000364. The card also said "LiteLLM spend: its estimate from its model price list, not a bill" |
| 20:28:37 | the same card, last 7 days | the same totals, window 2026-11-09 to 2026-11-15 |
| 20:28:43 | agent `northwind-support`, Overview, **LLM cost** | "USD 0.202319, 198 calls, 0 failed". Per deployment: northwind-support-candidate (local, removed) 182 / 201,439 / 9,934 / USD 0.187370; northwind-support (local, ready) 16 / 16,870 / 623 / USD 0.014949. The eval judge key is not the agent's, so it is not in the agent's card |

Screenshots: `c01-home-cost` to `c04-agent-full`, in the session scratchpad. They are not
attached, because the Home page shows the sandbox path (checked on that console: Home lists each
project's absolute path).

## Acceptance (from the issue)

| Check | Result |
|---|---|
| After N invocations of a starter agent, cost per deployment matches LiteLLM's own spend log | **PASS**, run on Northwind rather than a starter agent (the handoff allowed either), N = 6 turns (5 `invoke`, 1 Try it). The totals (260 calls) also include the release's candidate and judge calls; each key is compared on its own. `cost` and both console cards equal the SQL sums of `LiteLLM_SpendLogs` per key, per model and in total |
| Claims only what was run; no billing claims | **PASS**: the CLI and both cards say the amounts are LiteLLM's estimate, not a bill. This record makes no billing claim |

Other goals of the issue:
- **Tokens and cost per turn in `invoke` and Try it:** shown. `invoke --json` carries
  `response.cost_usd`, `prompt_tokens` and `output_tokens`. Try it's Turn details show "Cost
  $0.003538 (LiteLLM's estimate, reported by the runtime)". The 6 turns' `cost_usd` adds up to
  0.01493425, against 0.01493475 in the spend log for the key's 9 chat calls. The per-turn figure
  evidently does not include the turn's embedding call. The gap is rounding size, while the
  key's 7 embedding calls cost 0.0000144. This was not checked call by call. `docs/cost.md` still calls the per-turn cost
  planned (#1011).
- **Optional budget per deployment key (`max_budget`) with a coded error:** not run.

Flows that need a model key ran here: Northwind with `gemini/gemini-3.8-flash` and
`gemini/gemini-embedding-2` through LiteLLM.

## Leak scan and teardown

The same sandbox and the same scan as 907-tracing.md, which covered this record's outputs
(`invoke`, `cost`, the SQL tables and the console logs): 0 hits over the saved outputs for the `~/.env` values and
home paths; the key file was deleted; deployments removed, stacks purged, and Docker back to the
pre-run snapshot (details there). The SQL output was saved; it holds key aliases (deployment
IDs), not keys.

## Notes

- `cost` reads `/spend/logs/v2` with the master key. The SQL query read the table directly, as
  the database owner of the stack's Postgres. The two are independent reads of the same rows.
- The token totals here are per model call, from LiteLLM. The trace totals in 907-tracing.md
  count each call twice (#1009); they are not used for cost.
