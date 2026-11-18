# #1058 S0 verification: LiteLLM request tags in spend logs

Status: **verified from a checkout of `main`** (732d6f17, after #1134 merged, 2026-11-17 local time (2026-11-18 05:54 UTC), macOS
13.7.8 x86_64, Docker Desktop engine 27.5.1). This is the probe of
`docs/superpowers/plans/2026-11-16-sub-agents-and-skills.md` §3. It ran against a live LiteLLM
proxy on two built-in gateway versions, with real provider calls.

**Verdict:**

| Gateway | LiteLLM | Verdict |
|---|---|---|
| `service/litellm@1.0.0` (what Northwind pins) | 1.106.2 | **tags** |
| `service/litellm@1.3.0` (request filter, #1105) | 1.106.2 | **tags** |

On both versions a request's `metadata.tags` reaches the `/spend/logs/v2` row, in the row field
`request_tags`. A second key that sends the first key's tag is billed to the second key. A request
with no tags logs normally. `service/litellm@1.3.0` neither refuses nor strips `metadata.tags`.

RT can send tags and AC can group by tag. Five things below change how RT and EV must do it; see
[What RT and EV need to know](#what-rt-and-ev-need-to-know).

This is on `main`, not in a published preview. No code changed. The probe covered raw HTTP
requests only; see [Not run](#not-run).

## How it ran

- **Code:** an editable install of the checkout at 732d6f17 (`agentbreeder --version` printed
  `3.0.0a3`), Python 3.12.8. No wheel was built: the probe tests LiteLLM's behaviour, not
  packaging.
- **Sandbox:** every command ran under `env -i` with a fresh `HOME`, `TMPDIR` inside the sandbox
  and `USER=demo`. A Docker context was created inside the sandbox `HOME`, since AgentBreeder
  refuses `DOCKER_HOST`.
- **Stack:** a two-service `stack.yaml`: `service/postgres-pgvector@1.2.0` with one database, and
  `service/litellm@<version>` wired to it. The gateway config had one alias, `probe-model`. It was
  started with `agentbreeder services up --environment dev --env-file <file> --yes` and removed with
  `agentbreeder services down --purge --yes`. One stack ran at a time: 1.0.0 first, then a fresh
  stack on 1.3.0 with an empty database.
- **Model: live, not a stub.** `probe-model` was `openai/gpt-4o-mini` on a real OpenAI key. Each
  request was "Reply with the single word: ok" with `max_tokens` 5. About 30 calls were made in
  all, for less than $0.0001.
- **Keys:** two virtual keys per stack, minted by `gateway_keys.LiteLLM.mint` with the arguments
  `deployment.py` passes for a deployment key: alias `agentbreeder-s0-probe-a` and
  `agentbreeder-s0-probe-b`, `models: [probe-model]`, a budget, an rpm limit, AgentBreeder's four
  metadata fields, and `routes=gateway_keys.key_routes()`. The `/key/generate` body fields were
  `allowed_routes`, `budget_duration`, `key_alias`, `max_budget`, `metadata`, `models` and
  `rpm_limit`. `allowed_routes` was `/v1/chat/completions`, `/chat/completions`, `/v1/embeddings`
  and `/embeddings`. Since #1105 every key gets these routes on every gateway version, so the keys
  on 1.0.0 were limited the same way as on 1.3.0. Both keys were revoked at the end of each run.
- **Requests:** a small Python script sent `POST /v1/chat/completions` with `urllib`. It saved
  statuses, field names, header names, tags, key aliases and booleans. It never saved a key, a
  key hash or a reply.
- **Reading spend:** `GET /spend/logs/v2` with the master key, the route and query parameters
  `gateway_keys.spend_logs` uses (`start_date`, `end_date`, `page`, `page_size`). The script
  polled until every call had a row. A row was matched to its request by the row field
  `litellm_call_id`, which equals the response header `x-litellm-call-id`. A row's key was
  identified by comparing the row's `api_key` with the SHA-256 of each minted key, in memory.
- **LiteLLM version:** `importlib.metadata.version("litellm")` inside each gateway container
  printed `1.106.2`. That output was read in the terminal and not saved to a file. On 1.3.0 the
  response header `x-litellm-version`, which the script saved, said the same; on 1.0.0 the script
  did not yet save that header. Both gateway
  versions use the same image digest (`litellm-database@sha256:35f2cec3a14b…`); 1.3.0 adds a
  launcher and the request filter.

The 1.0.0 run was done twice in the same stack, because the first version of the script matched
rows on the wrong field. The table shows the second run; the first run's results are not used here.

## The four plan steps

The same results were seen on both gateway versions unless a row says otherwise.

| Step (plan §3) | Request | Result |
|---|---|---|
| 1. A chat completion with `metadata.tags` | key A, body field `metadata: {"tags": ["agentbreeder-subagent:billing"]}` | 200 |
| 2. `/spend/logs/v2` keeps the tag | the row for step 1 | **PASS.** `request_tags` is `["agentbreeder-subagent:billing", "User-Agent: Python-urllib", "User-Agent: Python-urllib/3.12"]`. The row's `metadata` has no `tags` field. |
| 3. A second key sends the first key's tag and is still billed | key B, the same `metadata.tags` | **PASS.** 200. The row carries the tag, its `api_key` is key B's hash, `metadata.user_api_key_alias` is `agentbreeder-s0-probe-b`, and `spend` is above zero. |
| 4. A request with no tags logs normally | key A, no `metadata` | **PASS.** 200. The row exists with `spend` above zero and key A's alias. `request_tags` holds only the two `User-Agent` entries. |

`spend` in each of these rows equalled the response header `x-litellm-response-cost`.

Per-key spend, read with the master key through `gateway_keys.LiteLLM.describe` (`spend_usd`),
was above zero for both keys on both versions. On 1.0.0 each key's `spend_usd` equalled the sum of
its rows. On 1.3.0 key A's `spend_usd` was lower than the sum of its rows when it was read (5.4e-06
against 1.89e-05), and key B's was half its rows' sum. The read came right after the rows appeared
and was not repeated, so whether the key total catches up was not checked.

## Other requests

| Request | Result (both versions unless noted) |
|---|---|
| Key A, a top-level body field `tags: [...]` | 200. The tag is in `request_tags`. |
| Key A, the request header `x-litellm-tags` | 200. The tag is in `request_tags`. |
| Key A, `metadata.tags` with `stream: true` | 200. The tag is in `request_tags` and `spend` is above zero. The streamed response had `x-litellm-call-id` but **no `x-litellm-response-cost` header**. |
| Key A, two tags in `metadata.tags` | 200. Both are in `request_tags`, in order, before the `User-Agent` entries. |
| Key B, `metadata.tags` plus `metadata.user_api_key_alias` set to key A's alias and `metadata.user_api_key` set to key A's hash | 200. The row's `api_key`, `metadata.user_api_key` and `metadata.user_api_key_alias` are all key B's. The request's values were not kept. |
| Key A, `metadata.spend_logs_metadata: {"agentbreeder_subagent": "billing"}` | 200. The object is in the row's `metadata.spend_logs_metadata`. `request_tags` is unchanged. |
| 1.3.0 only, control: key A, `metadata.tags` plus top-level `custom_llm_provider` | **400** `GATEWAY_PARAM_REFUSED … (custom_llm_provider)`. The gateway log (`docker logs`, read in the terminal and not saved) had one `agentbreeder gateway: refused` line for it. This shows the request filter was loaded. A `failure` row was written with the tag and `spend` 0. |
| 1.3.0 only: key A, `metadata.tags` plus `metadata.api_base` | **401** from LiteLLM's own blocklist (`api_base is not allowed in request body`). No spend row. |

## Can a deployment key read its own spend?

No, on both versions. Key A got **403** on each of `GET /spend/logs/v2`, `/spend/logs`,
`/key/info`, `/spend/tags`, `/global/spend/tags` and `/v1/models`. This is `allowed_routes`
working as #1105 intends. Spend is read host-side with the master key, as `gateway_keys.spend_logs`
does today.

## Field names observed

No key values are recorded here.

- **Request body:** `model`, `messages`, `max_tokens`, `metadata` (with `tags`, a list of
  strings), and in the other requests `tags`, `stream`, `custom_llm_provider`.
- **Response body:** `id`, `object`, `created`, `model`, `choices`, `usage`, `service_tier`,
  `system_fingerprint`. `usage` has `prompt_tokens`, `completion_tokens`, `total_tokens`,
  `prompt_tokens_details` and `completion_tokens_details`.
- **Response headers used:** `x-litellm-call-id`, `x-litellm-response-cost`, `x-litellm-version`,
  `x-litellm-key-spend`, `x-litellm-model-group`. Seventeen more `x-litellm-*` headers were
  present.
- **`/spend/logs/v2` envelope:** `data`, `page`, `page_size`, `total`, `total_pages`,
  `total_is_capped`.
- **Spend row:** `request_id`, `litellm_call_id`, `call_type`, `api_key`, `spend`, `total_tokens`,
  `prompt_tokens`, `completion_tokens`, `startTime`, `endTime`, `completionStartTime`,
  `request_duration_ms`, `model`, `model_id`, `model_group`, `custom_llm_provider`, `api_base`,
  `user`, `end_user`, `team_id`, `organization_id`, `agent_id`, `session_id`, `status`,
  `cache_hit`, `cache_key`, `request_tags`, `requester_ip_address`, `mcp_namespaced_tool_name`,
  `metadata`.
- **Spend row `metadata`, the fields that matter here:** `user_api_key`, `user_api_key_alias`,
  `litellm_call_id`, `spend_logs_metadata`, `usage_object`, `additional_usage_values`, `status`.
  The row's `metadata` had 49 fields in all. `tags` is not one of them.
- **Tag summary (master key):** `GET /spend/tags` returned a list of `individual_request_tag`,
  `log_count` and `total_spend`, with no key field. `GET /tag/list` returned 200 and
  `GET /global/spend/tags` without parameters returned 400; neither was used.

## What RT and EV need to know

1. **Read `request_tags`, not `metadata.tags`.** The request field is `metadata.tags`. The spend
   row field is the top-level list `request_tags`.
2. **`gateway_keys._slim` drops `request_tags` today.** `gateway_keys.spend_logs` returned rows
   with `request_id`, `spend`, `model`, `model_group`, `status`, `prompt_tokens`,
   `completion_tokens`, `custom_llm_provider` and `metadata` only. Grouping by tag needs
   `request_tags` added there, as a bounded list of short strings. `_slim` also drops
   `litellm_call_id` (see item 5).
3. **LiteLLM adds its own tags.** Every row, tagged or not, had `User-Agent: <client>` and
   `User-Agent: <client>/<version>` in `request_tags`. Match on the `agentbreeder-subagent:`
   prefix. Do not expect an empty list for an untagged call, and do not expect exactly one tag.
4. **A tag is the caller's claim.** The gateway does not check it: any key can send any tag, by
   three routes (body `metadata.tags`, body `tags`, header `x-litellm-tags`). Billing follows the
   key, so group by key first (`metadata.user_api_key_alias`, which was not overwritten in the
   one spoof attempt per version), then by tag inside that key. `/spend/tags` is not usable for
   this: its rows have no key field.
5. **`request_id` is not the gateway's call id.** On a successful non-streamed call the row's
   `request_id` was the provider's response id (the response body's `id`). On the refused call it
   was the `x-litellm-call-id`. On the streamed call it was neither the call id nor an id the
   script saved. `litellm_call_id` equalled `x-litellm-call-id` on every row these runs matched.
   If anything is matched per request, match on `litellm_call_id`.

One more observation for the cost check in the sub-agents spec §10.2: the streamed response had
no `x-litellm-response-cost` header. If the runtime's `CostClient` streams, those calls would
count as cost-unknown. Whether it streams was not checked here.

## Not run

- **The runtime's own client.** The probe sent raw HTTP. It did not send through ADK's `LiteLlm`
  with `extra_body`, so it does not show that the runtime's request reaches the proxy with
  `metadata` at the top level of the body. RT's tests need to show that.
- **The parent's tag value `agentbreeder-subagent:-`.** The tags sent ended in `billing`, `plain`,
  `header` and `second`.
- **Embeddings.** Only chat completions were sent, and one streamed call per version.
- **Tag limits.** Tag length, tag count and unusual characters were not tested.
- **`service/litellm@1.1.0` and `1.2.0`**, a guardrails floor, and agent guardrails.
- **Any provider other than OpenAI**, and any fallback or retry.
- **Linux.**
- **The opt-in Docker test.** None was added; this PR is a record only.

## Leak scan and teardown

- Only the `OPENAI_API_KEY` line was copied from `~/.env`, into a 0600 file in the sandbox
  `HOME`. The file was deleted after the second stack came down.
- This record was scanned for provider-key and virtual-key patterns, key hashes, home paths, the
  OS user name and email addresses other than `example.com`: 0 hits. The saved probe outputs
  (not committed) were scanned for key patterns and key hashes: 0 hits.
- `docker images`, `docker volume ls`, `docker network ls` and `docker ps -a` were saved before
  the first stack. Volume and network creation were tested first with a throwaway volume and
  network, then removed.
- Both stacks were removed with `services down --purge --yes`. The three images the probe pulled
  (`pgvector/pgvector`, `ghcr.io/berriai/litellm-database` and a `python` base image) were not in
  the snapshot and were removed by ID. Nothing was pruned.
- Afterwards image IDs, volumes, networks and containers were identical to the snapshot:
  `alpine:3.20` and the two Presidio images remain, with no volumes and no containers.
