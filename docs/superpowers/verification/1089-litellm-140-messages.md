# #1089 verification: `service/litellm@1.4.0` and the Anthropic Messages route on real Docker

Status: **the opt-in Docker test passed, run from the PR branch (#1187), not from `main`.**
Run on 2026-11-20 (first run started 19:59Z) on macOS 13.7.8 x86_64, Docker Desktop engine
27.5.1 (linux/amd64), Python 3.12.8.

Command, in a fresh `[dev,eval]` venv with this worktree installed editable:

```
AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_gateway_messages_filter_docker.py --no-cov -s
```

Images, by the digests in the built-ins and the test:
- gateway: `ghcr.io/berriai/litellm-database@sha256:35f2cec3a14bb297e08bfab56c1fc377432bc4071aaf71978fdff043a5d91e79`
  (LiteLLM 1.106.2, the image of 1.3.0 and 1.4.0)
- postgres: `pgvector/pgvector@sha256:0dd3149a06e4579a0d70d8fef9945636cd514005e7237deb27f9b67886f8dc4f`
- stub providers: `python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54`

The upstreams are the test's two scripted providers. They log each request's path and which
imported key they saw, never the key. **No real provider was called and no model ran**, and
no real Claude Code CLI or F7 runtime was used.

## The first run failed, because of the test

The first run: 1.3.0 passed; 1.4.0 failed. The streamed request for the OpenAI alias `a` got
**429** (`No deployments available ... cooldown_list`).

**Cause.** LiteLLM 1.106.2 sends a Messages request for an `openai/...` model to the provider's
**Responses API** (`/v1/responses`), not to `/v1/chat/completions`. The stub from #1105
answers every path other than Messages and embeddings with a chat-completion body. LiteLLM could
not parse that as Responses (`'GenericEvent' object has no attribute 'type'`). The streamed call
retried, failed again and put the deployment in cooldown, so it ended in the 429.

**Not caught by the test before the fix.** A broken stream still answers **HTTP 200**, with an
`event: error` in the SSE body. A by-hand probe on its own 1.4.0 stack showed this. The
non-streamed translated call also failed against the old stub; the loop stopped at the stream
first.

**Fix, test only.**
- The test now builds its own `PROVIDER`: #1105's stub with a `/responses` branch, plain and
  streamed. `tests/test_gateway_request_filter_docker.py` is unchanged.
- The streamed body must contain `from A` and no `event: error`.
- The docstring said "translated to chat"; it now names the Responses API. Nothing in
  `docs/` or the product made that claim.

No product file changed, so `_content.json` and the built-in hashes were not regenerated.

## Second run: 2 passed (154 s)

| Check | 1.4.0 | 1.3.0 (control) |
|---|---|---|
| Chat on both keys (`/v1/chat/completions`, alias `a`) | 200, `from A` | 200, `from A` |
| Chat-only key on `/v1/messages` | **403** | **403** |
| Messages, Anthropic alias `m` with a client tool | 200, `from B`; provider B saw `/v1/v1/messages` with key B | 400 `GATEWAY_ROUTE_REFUSED` |
| Messages, OpenAI alias `a` (translated) | 200, `from A`; provider A saw `/v1/responses` with key A | 400 `GATEWAY_ROUTE_REFUSED` |
| Messages, alias `a`, `stream: true` | 200 SSE with `from A`, no error event | 400 `GATEWAY_ROUTE_REFUSED` |
| Messages with header `anthropic-version: 2023-06-01` | 200 | 400 `GATEWAY_ROUTE_REFUSED` |
| Provider calls for the refused plain requests | n/a | none |

On 1.4.0, each of these 18 requests got **400 `GATEWAY_PARAM_REFUSED`** naming the field, and
**neither provider logged a request**:
- Fields: `mcp_servers`, `container`, `service_tier`, `inference_geo`, `speed`, `betas`,
  `anthropic_beta`, `compaction`, `custom_llm_provider`, `extra_body.api_key`.
- Tools: `web_search_20250305`, `code_execution_20250825`, `bash_20250124`, `mcp`.
- Headers: the `anthropic-beta` header, and `provider_specific_header` in the body.
- Content: a Files API source and a `url` source.

**Logs.** The gateway's log had 18 `agentbreeder gateway: refused` lines. The first was
`refused GATEWAY_PARAM_REFUSED field=mcp_servers in=body call=anthropic_messages key_alias=agentbreeder-ccc…`.
The test searched the gateway log for each of these and found none:
- the two provider keys
- the master key
- both minted keys
- the client-supplied secret

**`/v1/messages/count_tokens` (recorded, not asserted; an owner decision is pending).**
- The request used a key allowed `/v1/messages` and a Files API source.
- It got **200** `{"input_tokens":0,"litellm_count_source":"provider"}`.
- So the route is reachable with that key and the filter does not refuse it.
- LiteLLM's reply says a provider produced the count. Provider B's request log was not
  captured for this call.

## Findings, recorded only

- **The Messages-to-OpenAI path is the Responses API.** On LiteLLM 1.106.2 the gateway calls an
  OpenAI alias at `/v1/responses`. The filter checks the incoming `anthropic_messages` body
  before that call; this run did not exercise any Responses-specific parameter.
- **Doubled path `/v1/v1/messages`.** The test's `api_base` ends in `/v1`, and LiteLLM's
  Anthropic provider appends `/v1/messages`. This comes from the test config, not the
  built-in, and the stub accepted it.
- **A step container left behind (existing engine behaviour, not this PR).**
  - The first run left one health-probe container, `…-step-…`, in `Created` state. Docker
    had created it but not started it.
  - `services down` did not remove it, because it removes only the stack's services.
  - `compose.py` `oneshot` runs with `--rm` and removes a step only when it times out or
    writes too much.
  - It was removed by ID at teardown. The second run left none.
- The owner-pending items are unchanged: `count_tokens`; deploy not refusing
  `anthropic-messages` on gateways older than 1.4.0; and the chat `provider_specific_header`
  rule.

## Teardown and leak scan

- **Snapshot** (19:59:00Z): 5 images, 1 volume, 3 networks, 0 containers.
- **Removed after the runs, by exact ID, all created after the snapshot:**
  - the three pulled images above
  - the stray step container
- No prune was run.
- **After teardown:** image IDs, volumes, networks and containers equal the snapshot.
- No keys were read from the environment file; the test uses fake keys and stub providers.
