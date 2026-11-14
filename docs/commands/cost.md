# `agentbreeder cost`

Show LLM cost per deployment or per model from the gateway's spend logs (read-only).

`cost [--stack DIR [--environment ENV] | --state-dir DIR] [--since DAY] [--until DAY] [--by deployment|model] [--json]` reports what your deployments' model calls cost, per deployment key and per model, from the environment's LiteLLM spend logs, reading only: USD as LiteLLM computed it from its price list (an estimate, not a bill), removed deployments still counted, only `agentbreeder-` keys, UTC days from `--since` 00:00:00 to `--until` 23:59:59 (default the last 30, at most 366). Spend logs reach LiteLLM's database about a minute after a call. Errors are `[CODE] message` (`COST_WINDOW`, `COST_NO_GATEWAY`, `GATEWAY_UNREACHABLE`, `GATEWAY_ERROR`, state codes), with `--json` also the error object. Run against a real LiteLLM with mocked model responses only. The per-turn `cost_usd` for `invoke` and the console's Try it is planned, not emitted. See [`cost.md`](../cost.md) (on `main`, not in a published preview).

Flags: `agentbreeder cost --help`.
