# Studio Phase 8 — BYO-Key Chat-to-Build Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a "Chat to build" tab to `/agents/new`: a conversational agent builder powered by the user's own Claude API key. The user describes their agent in prose; the backend drives a short Claude conversation that ends by emitting a **schema-valid `agent.yaml`** (via Anthropic tool-use), which is created through the existing `from-yaml` path. A premium layer beside the deterministic form wizard.

**Architecture:** Reuse the existing httpx-based `AnthropicProvider` — for BYO key, construct a fresh `AnthropicProvider(ProviderConfig(api_key=<user key>))` per request (never the shared registry provider). The user's key is stored once via the existing `POST /api/v1/secrets` as `AGENTBREEDER_CLAUDE_BUILDER_KEY` (workspace secrets backend, never DB, never logged) and read server-side per request. Structured output uses an Anthropic **tool** `submit_agent_spec` whose `input_schema` is the agent JSON schema; the backend serializes the tool input to YAML and **re-validates with `validate_config_yaml()`** before returning it (model output is untrusted). Request/response (no SSE) with client-side simulated streaming, mirroring the Playground. Endpoint mirrors `POST /api/v1/builders/recommend`.

**Tech Stack:** FastAPI + the repo's `AnthropicProvider` (httpx, tool-use), Pydantic, pytest (Anthropic call mocked). React + TS + React Query, Vitest, Playwright. Claude model `claude-sonnet-4-6`; apply prompt caching to the (large, static) system prompt + tool schema.

**Branch:** `feat/studio-ux-simplification` (commit per task; no PR until the whole epic passes locally).

---

## File Structure

- `engine/agent_chat_builder.py` — NEW: the conversation driver (build system prompt + `submit_agent_spec` tool, call provider, parse tool-use → validated yaml). Pure-ish (provider injected).
- `tests/unit/test_agent_chat_builder.py` — NEW: unit tests with a mocked provider.
- `api/routes/builders.py` — add `POST /builders/chat`.
- `tests/unit/test_builders_chat.py` — NEW: endpoint tests (mocked provider + secrets).
- `dashboard/src/lib/api.ts` — add `builders.chat()` + types; reuse `secrets.list`/`secrets.create`.
- `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx` — NEW: chat UI + key-entry guard + create-on-spec.
- `dashboard/src/pages/agent-wizard.tsx` — add `mode: "form" | "chat"` toggle.

---

### Task 1: Conversation driver (`engine/agent_chat_builder.py`)

**Files:** Create `engine/agent_chat_builder.py`; Test `tests/unit/test_agent_chat_builder.py`

Contract:
```python
@dataclass
class ChatTurnResult:
    assistant_message: str           # text to show the user (may be "" if it went straight to spec)
    agent_yaml: str | None           # set when Claude submitted a spec
    valid: bool                      # post-validation result when agent_yaml is set
    errors: list[str]                # validation errors if invalid

SUBMIT_TOOL = {
    "name": "submit_agent_spec",
    "description": "Submit the finished agent specification once you have enough info.",
    "input_schema": <contents of engine/schema/agent.schema.json>,
}

async def run_chat_turn(provider, history: list[dict], *, recommend_hint: dict | None = None) -> ChatTurnResult: ...
```
- `provider` is an `AnthropicProvider` (injected — tests pass a fake). System prompt: a concise instruction to interview the user for the minimum required fields (name, goal→description, framework, model, deploy cloud), keep it to a few turns, and call `submit_agent_spec` as soon as it has enough — using the recommend hint as a sensible default. Mark the system prompt for **prompt caching** (it's large + static).
- Call `provider.complete(messages=history, system=..., tools=[SUBMIT_TOOL], model="claude-sonnet-4-6")`.
- If the response contains a `tool_use` for `submit_agent_spec`: convert the JSON input → YAML (reuse the dashboard's contract is frontend-only; on the backend serialize via `yaml.safe_dump` — confirm pyyaml/ruamel is a dep; `engine` already parses YAML so a dumper exists), then `validate_config_yaml(yaml_str)` → set `agent_yaml`, `valid`, `errors`. Else return the assistant text with `agent_yaml=None`.
- **Never** put the API key in any log line or the result.

- [ ] **Step 1: failing tests** — with a fake provider returning (a) a text turn → `assistant_message` set, `agent_yaml is None`; (b) a `submit_agent_spec` tool_use with a valid spec → `agent_yaml` set + `valid is True`; (c) a tool_use with an invalid spec (missing required field) → `valid is False`, `errors` non-empty. Assert the system prompt is built and the tool schema equals the agent schema.
- [ ] **Step 2** — run → FAIL.
- [ ] **Step 3** — implement. Reuse `registry.agents.validate_config_yaml` (or `engine.config_parser`) for validation; reuse `engine/schema/agent.schema.json` via the existing loader. Confirm `AnthropicProvider.complete()` signature + how tool_use blocks surface (from research: `_parse_response` → `ToolCall` objects) and how to pass `system` + `tools` + enable cache_control.
- [ ] **Step 4** — run → PASS. `ruff check`/`format`, `mypy engine/agent_chat_builder.py --ignore-missing-imports` clean.
- [ ] **Step 5** — commit: `git commit -m "feat(engine): agent_chat_builder — Claude tool-use → validated agent.yaml"`

---

### Task 2: `POST /api/v1/builders/chat` endpoint (BYO key, secure)

**Files:** Modify `api/routes/builders.py`; Test `tests/unit/test_builders_chat.py`

- [ ] **Step 1** — Read the `/builders/recommend` handler for the auth dep + `ApiResponse[T]` pattern, and `get_workspace_backend()` for reading a secret.
- [ ] **Step 2: failing tests** (mock the provider construction + the secrets backend):
  - missing key (secret not set) → HTTP 400 (NOT 500) with a clear "connect your Claude key" message; the provider is never constructed;
  - present key + a mocked provider returning a text turn → 200 with `assistant_message`, `agent_yaml` null;
  - present key + mocked tool-use valid spec → 200 with `agent_yaml` + `valid: true`;
  - **the API key never appears** in the response body or in any log (assert via caplog that the key string is absent).
- [ ] **Step 3** — run → FAIL.
- [ ] **Step 4** — implement `POST /builders/chat` (`get_current_user` auth):
  - Request model `ChatBuildRequest { messages: list[ChatMessage] }` (bounded — cap message count e.g. ≤40 and total chars to a sane limit; reject oversize with 413/400).
  - Read `AGENTBREEDER_CLAUDE_BUILDER_KEY` from `get_workspace_backend().get(...)`; if `None` → `HTTPException(400, "No Claude key connected — add one to chat-to-build.")`.
  - Construct a fresh `AnthropicProvider(ProviderConfig(api_key=key, ...))`, call `run_chat_turn(...)`, return `ApiResponse[ChatTurnResult]`.
  - Wrap the provider call: on Anthropic auth/network error, return a clean 502/400 with a non-leaking message (never echo the key; never dump the raw upstream error if it could contain the key). Log a warning WITHOUT the key.
- [ ] **Step 5** — run → PASS; `pytest tests/unit -k "builders or chat" -v`; ruff + mypy clean.
- [ ] **Step 6** — commit: `git commit -m "feat(api): POST /builders/chat — BYO-key conversational agent builder"`

---

### Task 3: Frontend API client + types

**Files:** Modify `dashboard/src/lib/api.ts`

- [ ] **Step 1** — Add TS `ChatMessage`, `ChatBuildResult` interfaces + `builders.chat(messages)` returning `request<ChatBuildResult>("/builders/chat", {method:"POST", body: JSON.stringify({messages})})`. Confirm `secrets.list()` + `secrets.create()` exist (from earlier work) for the key guard. No `any`.
- [ ] **Step 2** — `cd dashboard && npx tsc --noEmit` clean.
- [ ] **Step 3** — commit: `git commit -m "feat(studio): builders.chat API client method"`

---

### Task 4: `ChatBuildPanel` component (chat UI + key guard + create)

**Files:** Create `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx`; Test alongside

- [ ] **Step 1: failing tests** — (a) when `secrets.list()` does NOT contain `AGENTBREEDER_CLAUDE_BUILDER_KEY`, the panel renders a key-entry form (not the chat); submitting calls `secrets.create({name:"AGENTBREEDER_CLAUDE_BUILDER_KEY", value})` then reveals the chat. (b) with the key present, typing + send calls `builders.chat` and renders the assistant reply. (c) when a `builders.chat` response includes `agent_yaml` + `valid:true`, a "Create agent" button appears that calls `agents.fromYaml(agent_yaml)` and navigates to `/agents/:id`; if `valid:false`, show the errors and do NOT offer create. Mirror the `QueryClientProvider`+`MemoryRouter` test wrapper.
- [ ] **Step 2** — run → FAIL.
- [ ] **Step 3** — implement: mirror `ModelChatPanel` (message list + input + `useMutation` on `builders.chat`, optional client-side simulated streaming). Key-entry guard via `useQuery(secrets.list)` checking for the key name (never display key values — `secrets.list` returns masked metadata only). On a valid `agent_yaml`, show a confirm card → `agents.fromYaml` → navigate. The key input is `type="password"`, never stored in component state beyond submit, never logged.
- [ ] **Step 4** — run → PASS; tsc clean; `npm run build` ok.
- [ ] **Step 5** — commit: `git commit -m "feat(studio): ChatBuildPanel — BYO-key conversational builder UI"`

---

### Task 5: Wire the `Form | Chat` toggle into the wizard

**Files:** Modify `dashboard/src/pages/agent-wizard.tsx`

- [ ] **Step 1** — Add a `mode: "form" | "chat"` state (default `"form"`) + a small segmented toggle above the `StepIndicator`. In `"form"` mode render the existing steps unchanged; in `"chat"` mode render `<ChatBuildPanel />` in place of the steps. Don't touch the step reducer.
- [ ] **Step 2** — `npx tsc --noEmit` clean; `npm run build` ok.
- [ ] **Step 3** — commit: `git commit -m "feat(studio): Form | Chat toggle on the agent wizard"`

---

### Task 6: Docs + verify + SECURITY pass

**Files:** `website/content/docs/` (a short "Chat to build (BYO Claude key)" note where the wizard/agent-creation is documented)

- [ ] **Step 1** — Doc note: the chat builder needs your own Claude API key (stored in your workspace secrets backend, never the DB), and it always produces a schema-validated `agent.yaml` you confirm before creation.
- [ ] **Step 2** — Full suites: `venv/bin/python -m pytest tests/unit -k "chat or builders or agent_chat" -v` (green); `cd dashboard && npx vitest run` (green); `npx tsc --noEmit` + `npm run build`; `ruff`/`mypy` on the new Python.
- [ ] **Step 3 (controller)** — Run a dedicated **security review** of the diff (the `security` skill / a security-focused reviewer): assert the key is never logged/returned, is read only from the secrets backend, the endpoint is auth-gated + size-bounded, model output is schema-validated before any create, and upstream errors don't leak the key. Plus browser verification.
- [ ] **Step 4** — commit: `git commit -m "docs: chat-to-build (BYO Claude key) note"`

---

## Self-Review

**Spec coverage (§ chat-to-build, phase 8):** Form-side `Chat to build` tab gated on a connected Claude key (Task 4-5) ✓; backend calls Claude with the BYO key from the secrets backend (Task 2) ✓; structured output via tool-use + **schema validation before save** (Task 1) ✓; converges on the same `from-yaml` create path (Task 4) ✓. Security notes (key never in DB/logs/response; untrusted output validated; auth-gated; bounded) are explicit (Task 2, Task 6 Step 3).

**Placeholder scan:** the `ChatTurnResult`/tool/endpoint contracts are concrete; "confirm the YAML dumper dep / `complete()` tool signature / `secrets.list` shape" are real verification steps grounded in the research, not placeholders. Test cases enumerate the key-absent, text-turn, valid-spec, invalid-spec, and no-key-leak paths.

**Type/name consistency:** `AGENTBREEDER_CLAUDE_BUILDER_KEY` is the exact secret name across backend (Task 2) and frontend guard (Task 4). `run_chat_turn`/`ChatTurnResult`/`submit_agent_spec` are consistent across Tasks 1-2. `builders.chat` client (Task 3) matches the endpoint (Task 2). Model `claude-sonnet-4-6` per the repo's AnthropicProvider default.
