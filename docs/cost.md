# LLM cost (`agentbreeder cost`)

On `main`, not in a published preview (#906).

```
agentbreeder cost [--stack DIR [--environment ENV] | --state-dir DIR]
                  [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                  [--by deployment|model|subagent] [--json]
```

`cost` reports what the model calls of your deployments cost, per deployment and per model,
from the spend logs of the environment's LiteLLM gateway. It only reads: the deployment state
is opened read-only (nothing is created when it is missing) and the gateway is asked with its
master key from the operator config (with `--stack DIR`, the one `services up` wrote; without
it, the operator config's `default_environment`). `--environment` needs `--stack`, as for
`status` and `list`. The master key is never printed and is sent only to the configured gateway
URL: no proxy, and a redirect is refused, not followed.

## Where the numbers come from

Every deployment with `model_access` calls the gateway with its own LiteLLM virtual key, whose
alias is `agentbreeder-<deployment id>`; eval judges use `agentbreeder-eval-<run>`. LiteLLM
writes one spend log row per model call, with the key alias, the model, the token counts and
the cost it computed. `cost` reads those rows from `/spend/logs/v2` for the window and adds them
up per key and per model.

- **Amounts are USD as LiteLLM computed them** from its model price list (LiteLLM's own; in the
  run below, with no network, it used the copy bundled in the pinned image). They are an
  estimate from list prices, **not a bill**: your provider's invoice is the record.
- **Spend logs are written in batches.** A call reaches the logs about a minute after it is made,
  so the latest calls can be missing.
- **Removed deployments are still counted.** Their keys are revoked, but their spend log rows
  stay. A key's own `spend` (shown by `status --json` as `gateway_key.spend_usd`) resets every
  30-day budget window; `cost` adds up the logs instead.
- **Only AgentBreeder keys are counted.** Calls made with the master key or a key minted by hand
  are not.
- Calls to a local model (Ollama, #1025 O1; LiteLLM's `custom_llm_provider` `ollama` or
  `ollama_chat`) have no price: they are counted in `local`, never as $0.00, and a row of only
  such calls shows `no price (local model)` (in the console too). LiteLLM records their spend as
  0.0, so a budget would not count them; that was not run.
- Calls with no usable price (missing, negative, not a number, or above 10^12 USD) are counted
  (`unpriced`) but are not in the USD totals. Calls LiteLLM marks `failure` are counted in
  `failed`; in the run below LiteLLM recorded failed calls at 0 USD.
- The window is UTC days, from `--since` 00:00:00 to `--until` 23:59:59, at most 366 days. By
  default it is the 30 days ending today (or ending at `--until`). At most 50 pages of 1,000 rows
  are read (each page at most 32 MiB, keeping only the fields above); beyond that the result is
  marked `truncated` and a warning asks for a shorter window.
- The deployment state is read before the gateway: a state that needs `agentbreeder migrate`, or
  that cannot be read, stops the report with its code.

## Output

The table groups by deployment key (`--by deployment`, the default: ID, name, state, calls,
failed calls, tokens in/out, cached tokens in, USD) or by model (`--by model`: the gateway alias, the provider
model, and the same counts), with a total. A key that names no deployment in this state is shown
with `not in this state` (another state directory, or history that was deleted); an eval judge
key as `eval run <run>`.

`--by subagent` splits each key by sub-agent; see [Per sub-agent](#per-sub-agent---by-subagent).

`--json` prints both groupings (and, for a key with tagged calls, the sub-agent rows):

```json
{
  "schema_version": 1,
  "since": "2026-10-15", "until": "2026-11-13",
  "gateway": "litellm", "currency": "USD", "source": "litellm spend logs",
  "truncated": false,
  "keys": [
    {"key_alias": "agentbreeder-<id>", "kind": "deployment", "id": "<id>", "name": "hello",
     "target": "local", "state": "ready", "environment": "dev",
     "calls": 7, "failed": 0, "prompt_tokens": 77, "cached_prompt_tokens": 0,
     "completion_tokens": 140,
     "cost_usd": 0.003108, "unpriced": 0,
     "models": [{"model_group": "chat", "model": "openai/gpt-5.6", "calls": 5, "...": "..."}]}
  ],
  "models": [{"model_group": "chat", "model": "openai/gpt-5.6", "calls": 8, "...": "..."}],
  "total": {"calls": 10, "failed": 0, "prompt_tokens": 110, "completion_tokens": 200,
            "cost_usd": 0.00444, "unpriced": 0}
}
```

`kind` is `deployment`, `eval` (with `run` instead of the deployment fields) or `unknown` (with
`id` only). `cached_prompt_tokens` (`CACHED IN` in the table; #1056, on `main`, not in a published
preview) is the part of the prompt tokens the provider reported reading from its cache, as LiteLLM
recorded it in each spend log row (`additional_usage_values.cache_read_input_tokens` or
`usage_object.prompt_tokens_details.cached_tokens`, whichever is larger); a row whose figure is
above its own prompt tokens counts 0. Nothing is estimated, and the USD is LiteLLM's figure, which
already prices cached tokens if LiteLLM knows the provider's cache price. Keys and models are sorted by cost, highest first. Text fields come from the gateway
and are passed on as they are in JSON; the table shows them on one line with control characters
replaced and the counts and USD right-aligned (USD to 6 decimals in the table, 8 in JSON).

### Per sub-agent (`--by subagent`)

On `main`, not in a published preview (#1058). **Tested on fixture spend rows only**: no runtime
sends the tag yet, so this has not been run against a real agent with sub-agents. The
`runtime/adk` version that runs sub-agents is a later slice, and so is the run that compares these
numbers with the reply's per-sub-agent cost.

A runtime that runs sub-agents is to tag each model call with `agentbreeder-subagent:<name>`, and
the parent's own calls with `agentbreeder-subagent:-` (sub-agents spec §10.2). LiteLLM keeps a
request's `metadata.tags` in the spend row's `request_tags`; a probe on LiteLLM 1.106.2 with raw
HTTP requests showed that
([record](superpowers/verification/1058-S0-litellm-tags.md)). `cost` reads that field and keeps
only tags of exactly that form (the name is a sub-agent name, `^[a-z][a-z0-9_]{0,31}$`); LiteLLM's
own tags (`User-Agent: ...`) and anything else are dropped as the rows are read. Only the first 64
tags of a row are read; in the probe the caller's tags came before the ones LiteLLM adds.

A key with at least one tagged call gets a `subagents` list in `--json`, after `models`:

```json
"subagents": [
  {"role": "parent",   "name": null,      "calls": 4, "cost_usd": 0.0021, "unpriced": 0, "...": "..."},
  {"role": "subagent", "name": "billing", "calls": 2, "cost_usd": 0.0009, "unpriced": 1, "...": "..."},
  {"role": "untagged", "name": null,      "calls": 1, "cost_usd": 0.0001, "unpriced": 0, "...": "..."}
]
```

Each row has the same counts as a key. The parent is first, then the sub-agents by cost (highest
first, then by calls, then by name; a row with no priced call counts as 0 here), then the calls
with no tag. The rows add up to the key. Rows are per key: the same sub-agent name under two keys
is two rows, one in each key's list. At most 50 sub-agent names are listed per key (an agent file
has at most 5); calls with any further name are added up in one row with `role` `other`
(`(other tags)` in the table), before `untagged`. A key with no
tagged call has no `subagents` field, so the answer for an agent without sub-agents is what it was
before, and `total` and `models` never depend on tags. `schema_version` stays 1.

`--by subagent` prints one row per key and agent, with `AGENT` `(parent)`, the sub-agent's name
or `(not tagged)`; a key with no tagged call is one `(not tagged)` row.

- **A tag is the caller's claim.** The gateway does not check it: any key can send any tag. So a
  tag only splits the spend of the key that sent it. It never moves spend from one key to
  another, and the per-key numbers do not depend on tags.
- **A call with two different `agentbreeder-subagent:` tags is not attributed**; it is counted
  under `untagged`.
- **An unknown cost is shown as unknown, never as zero.** A call with no usable price is counted
  in the row's `unpriced`. In the `--by subagent` table the USD cell is `unknown` when no call of
  the row has a price (also when its other calls went to a local model), and
  `0.002000 + 1 unknown` when some do. The other two tables are unchanged.
- Embedding calls (knowledge search) are not model calls of a sub-agent; whether a runtime tags
  them is the runtime's choice and is not decided here.
- The console does not show the per-sub-agent rows yet: `center/cost.py` passes on known fields
  only.

Errors are `[CODE] message` on stderr, exit 2; with `--json` also
`{"schema_version": 1, "error": {"code", "message"}}` on stdout:

| Code | Meaning |
|---|---|
| `COST_WINDOW` | `--since`/`--until` is not a `YYYY-MM-DD` date, is reversed, or spans more than 366 days |
| `COST_NO_GATEWAY` | no environment is selected, it has no gateway, or the gateway has no `master_key` |
| `GATEWAY_CREDENTIAL` | the gateway's master key is empty |
| `GATEWAY_UNREACHABLE` | the gateway did not answer (is the stack running? `services status`) |
| `GATEWAY_ERROR` | the gateway answered with an HTTP error (a redirect included), something other than JSON, more than 32 MiB, or no spend logs (an older LiteLLM without `/spend/logs/v2`) |
| `DEPLOYMENT_ERROR`, `STATE_NEEDS_MIGRATION`, `STATE_BUSY` | the local state cannot be opened read-only (permissions, an older version: run `migrate`, a write in progress) |
| `STATE_UNREADABLE` | the local state could not be read (an OS or SQLite error) |
| `COST_ERROR` | the operator config or a credential file could not be used, or the gateway's answer could not be read (the message says which) |

## What was run

Against the pinned `ghcr.io/berriai/litellm-database:v1.106.2` with Postgres, in a throwaway
sandbox on macOS (2026-11-13): two deployment keys minted by AgentBreeder's own gateway client,
ten model calls answered by LiteLLM's server-side mock response (no provider was called), one
deployment removed and its key revoked. `cost` matched LiteLLM's own key spend and the sum of its
spend log rows for each deployment, counted the removed one, and never printed the master key.

Against real provider calls on Northwind (`main` at 2b4e4cf, macOS, 2026-11-15): a release (61
eval cases), one Try it turn and 5 `invoke` turns made 260 calls to `gemini/gemini-3.8-flash` and
`gemini/gemini-embedding-2` through LiteLLM. `cost`, by deployment and `--by model`, equalled a SQL
sum of LiteLLM's own spend log table per key, per model and in total (USD 0.264939, removed
candidate and eval judge keys included). That is LiteLLM's estimate; nothing was compared with a
provider invoice. A key's budget (`max_budget`) being reached was not run. The record is
`docs/superpowers/verification/906-cost.md`.

## In the console

The console (`agentbreeder up`) runs `cost --stack DIR --environment ENV --since DAY --until DAY
--json` for a project's **LLM cost** card on Home and for an agent's Overview (its own deployment
keys, removed ones included). It shows the currency, the window and the time it was read, and
labels the amounts as LiteLLM spend, not a bill. See
[`command-center.md`](command-center.md#llm-cost-906-console-half-on-main-not-in-a-published-preview)
(on `main`, not in a published preview). In the 2026-11-15 Northwind run above, both cards showed
the same figures as `cost` and the spend log.

## Per-turn cost (`runtime/adk@1.2.0`)

Spend logs arrive about a minute late, so a turn's cost comes from the runtime: LiteLLM returns
each call's cost in its `x-litellm-response-cost` response header, and `runtime/adk@1.2.0`
(#828, on `main`, not in a published preview) adds those headers up over the turn's model calls
and returns the total in the turn body (`invoke --json`'s `response`), next to the counts:

```json
{"model_calls": 2, "prompt_tokens": 412, "output_tokens": 57, "cost_usd": 0.000444}
```

`cost_usd` is the sum of the headers LiteLLM sent (USD, its estimate), or `null` when any call in
the turn had no cost header. Each `model_called` event also has that call's `cost_usd`. Older
runtimes (`1.1.0` and earlier) don't send the field; consumers show the cost as not measured. The
console's Try it inspector shows `cost_usd` when it is there (#906) and "Not measured" when it is
null or missing.

In the 2026-11-15 Northwind run above, `invoke --json` returned `cost_usd` for every turn, and Try
it showed it ("$0.003538 (LiteLLM's estimate, reported by the runtime)"). The 6 turns' `cost_usd`
added up to USD 0.01493425; LiteLLM's spend log had 0.01493475 for that key's 9 chat calls (a
rounding difference). The turn's figure evidently leaves out its knowledge embedding calls (that
key's 7 cost 0.0000144 in all); this was not checked call by call. See
[`agent-yaml.md`](agent-yaml.md#runtimeadk120).

## A budget or rate limit reached (`runtime/adk@1.3.0`)

When a deployment key's `max_budget` (from `model.budget_usd` or the environment's ceiling) is
spent, LiteLLM refuses the next call with `budget_exceeded`. LiteLLM checks the budget before
each call, against the spend so far, so the turn that crosses the budget still completes.
`runtime/adk@1.3.0` (#1034, on `main`, not in a published preview) answers the refused turn with
`MODEL_BUDGET_EXCEEDED` (HTTP 402), whose message says to raise `model.budget_usd` or wait for the
budget period to reset. A rate limit (`rpm`) still hit after the runtime's bounded wait (10 s per
turn) is `MODEL_RATE_LIMITED` (429, with `retry_after_s`). `1.2.0` and earlier answer both with a
generic "unavailable, please try again" (502), and a rate-limited turn waits out the limit
without saying so. See [`agent-yaml.md`](agent-yaml.md#runtimeadk130).
