# Anthropic Prompt Caching + Cloud Builder Surfacing — Design

**Date:** 2026-05-25
**Status:** Approved
**Lands in:** OSS PR #519 (`feat/studio-ux-simplification`) for Part A; a companion branch/PR in `agentbreeder-cloud` for Part B.

## Motivation

PR #519 added two builder endpoints to the OSS API:

- `POST /api/v1/builders/recommend` — pure, stateless stack heuristics.
- `POST /api/v1/builders/chat` — BYO-key conversational agent builder. Each turn re-sends a static system prompt **and** the `submit_agent_spec` tool, whose `input_schema` is the full `agent.schema.json` (~23.5 KB ≈ 5.9 K tokens).

Two gaps:

1. The chat builder re-sends ~6 K tokens of static tool schema on every turn with no caching. `engine/agent_chat_builder.py` carries an explicit `TODO` to enable Anthropic prompt caching "once AnthropicProvider supports cache_control". `AnthropicProvider` does not support it today.
2. `agentbreeder-cloud` does not surface either builder endpoint. Per the cross-repo sync rule, new OSS capabilities should be exposed in Cloud.

## Part A — AnthropicProvider auto prompt caching (OSS)

### Behavior

Anthropic caches a request prefix (order: `tools` → `system` → `messages`) when a `cache_control: {"type": "ephemeral"}` breakpoint is attached, subject to a minimum cacheable prefix (~1024 tokens for Sonnet/Opus). Prompt caching is GA on `anthropic-version: 2023-06-01` — **no beta header required**.

`AnthropicProvider._build_payload` automatically attaches caching to the large static parts. Both `generate` and `generate_stream` call `_build_payload`, so streaming and non-streaming are covered by the single change.

Threshold: a char-based heuristic, `_CACHE_MIN_CHARS = 4096` (≈ 1024 tokens at ~4 chars/token). Evaluated **per part**:

- **Tools** — if the serialized tools array ≥ threshold, set `cache_control` on the **last** tool entry. This caches the entire `tools` prefix.
- **System** — if the system text ≥ threshold, convert `system` from a plain string into a one-block list:
  `[{"type": "text", "text": <system>, "cache_control": {"type": "ephemeral"}}]`. A breakpoint here caches the `tools` + `system` prefix.
- Below threshold → left unchanged (plain-string system, no markers).

This is **auto / zero-caller-change**: small prompts behave exactly as before, so existing callers and tests are unaffected. For the chat builder, the ~6 K-token tool is cached every turn; the small system prompt stays a plain string.

### Usage accounting

Anthropic responses report cached input separately: `usage.cache_creation_input_tokens` (first call, cache write) and `usage.cache_read_input_tokens` (later calls, cache hit), in addition to `usage.input_tokens` (uncached). `_parse_response` folds all three into `UsageInfo.prompt_tokens` (and `total_tokens`) so the deploy-pipeline cost-attribution side effect is not undercounted when caching is active.

No new `UsageInfo` fields — keep the model minimal (YAGNI). Streaming (`_collect_stream`) does not currently populate usage and is unchanged.

### Chat builder cleanup

Delete the stale `TODO` comment block at `engine/agent_chat_builder.py` (the one referencing "enable Anthropic prompt caching once AnthropicProvider supports cache_control"). Caching is now transparent — no logic change in the builder.

### Tests (`tests/unit/`)

- Large tools array → `cache_control` on the last tool only.
- Large system string → `system` becomes a single-block list with `cache_control`.
- Small system + small tools → `system` stays a plain string, no `cache_control` anywhere.
- `_parse_response` sums `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` into `prompt_tokens`.

## Part B — Cloud builder proxy routes (`agentbreeder-cloud`)

A new `api/routes/builders.py`, registered in `api/main.py` under prefix `/cloud/builders`, following existing cloud route patterns (`TenantMiddleware`, `get_current_tenant`, httpx client to `AGENTBREEDER_API_URL`, forwarding the caller's auth):

- `POST /cloud/builders/recommend` — tenant-scoped; forwards the body to OSS `POST {AGENTBREEDER_API_URL}/api/v1/builders/recommend` and returns the response.
- `POST /cloud/builders/chat` — tenant-scoped **and metered** as a billable LLM action (record a usage event via the existing quota/metering service) before/after forwarding to OSS `POST {AGENTBREEDER_API_URL}/api/v1/builders/chat`.

Exact metering and auth-forwarding mechanics will be matched to the existing cloud routes (`sessions.py`, `quotas.py`) at implementation time. Cloud-side tests cover: tenant scoping, successful forward, and the chat metering event.

## Out of scope

- No changes to other providers (OpenAI/Google/Ollama).
- No new agent.yaml schema fields; `claude_sdk.prompt_caching` is not wired here (auto-caching supersedes the need for a manual toggle in the chat-builder path).
- No streaming usage accounting changes.
