# LLM cost (`agentbreeder cost`)

On `main`, not in a published preview (#906).

```
agentbreeder cost [--stack DIR [--environment ENV] | --state-dir DIR]
                  [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                  [--by deployment|model] [--json]
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
failed calls, tokens in/out, USD) or by model (`--by model`: the gateway alias, the provider
model, and the same counts), with a total. A key that names no deployment in this state is shown
with `not in this state` (another state directory, or history that was deleted); an eval judge
key as `eval run <run>`.

`--json` prints both groupings:

```json
{
  "schema_version": 1,
  "since": "2026-10-15", "until": "2026-11-13",
  "gateway": "litellm", "currency": "USD", "source": "litellm spend logs",
  "truncated": false,
  "keys": [
    {"key_alias": "agentbreeder-<id>", "kind": "deployment", "id": "<id>", "name": "hello",
     "target": "local", "state": "ready", "environment": "dev",
     "calls": 7, "failed": 0, "prompt_tokens": 77, "completion_tokens": 140,
     "cost_usd": 0.003108, "unpriced": 0,
     "models": [{"model_group": "chat", "model": "openai/gpt-5.6", "calls": 5, "...": "..."}]}
  ],
  "models": [{"model_group": "chat", "model": "openai/gpt-5.6", "calls": 8, "...": "..."}],
  "total": {"calls": 10, "failed": 0, "prompt_tokens": 110, "completion_tokens": 200,
            "cost_usd": 0.00444, "unpriced": 0}
}
```

`kind` is `deployment`, `eval` (with `run` instead of the deployment fields) or `unknown` (with
`id` only). Keys and models are sorted by cost, highest first. Text fields come from the gateway
and are passed on as they are in JSON; the table shows them on one line with control characters
replaced and the counts and USD right-aligned (USD to 6 decimals in the table, 8 in JSON).

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
No real provider call has been costed yet.

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
null or missing. It is covered by the runtime's tests
with scripted gateway responses; no live gateway has been checked yet. See
[`agent-yaml.md`](agent-yaml.md#runtimeadk120).
