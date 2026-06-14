# Studio Conversational Builder — Wave 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development. Implement task-by-task, failing test first.
> Branch: `feat/studio-conversational-builder` (continues Wave 1).
> Spec: `docs/superpowers/specs/2026-06-14-studio-conversational-builder-design.md` §8, §13 (W2).

**Goal:** Let the conversational builder collect the dependencies an agent needs —
secrets, MCP servers, and model-provider keys — **inline in the thread**, never sending
the user to a settings page. The dependency is recorded **by reference** in `agent.yaml`
and the spec is re-validated through governance. Values never enter the spec or browser
state.

**Mechanism (locked):** Add a second Anthropic tool, `request_setup(kind, name, reason)`,
to the interview. When the model calls it (instead of `submit_agent_spec`), the streaming
driver emits a `setup_request` event and ends the turn. The frontend renders an inline
`SetupCard`. On submit it calls the existing endpoint (`/secrets`, `/mcp-servers` +
`/{id}/discover`, or a provider key → `/secrets`), then appends a confirmation user
message and auto-continues the conversation. The model then references the dependency
(`secrets:` / `mcp_servers:` / `tools:`) and calls `submit_agent_spec`, which is
re-validated by `validate_config_yaml()`. This preserves the stateless,
client-held-conversation contract from Wave 1 (no `BuilderSession` resource yet — that is
still Wave 3).

**Tech Stack:** Python 3.11 / FastAPI / `sse-starlette` / pytest · React 18 / TypeScript /
TanStack Query / Vitest.

---

## File Structure

**Backend**
- Modify `engine/agent_chat_builder.py` — add `SetupRequest` dataclass; add
  `setup_request` field to `ChatTurnResult`; add `REQUEST_SETUP_TOOL`; extend
  `ChatStreamEvent` (`"setup_request"` type + `setup` field); handle the new tool in both
  `run_chat_turn` and `run_chat_turn_stream`; update `_SYSTEM_PROMPT`.
- Modify `api/routes/builders.py` — emit a `setup_request` SSE event in
  `chat_build_stream`'s generator.
- Test `tests/unit/test_agent_chat_builder_setup.py` — driver emits setup_request from the
  tool; non-stream path sets the field; spec submission still wins when both appear.
- Test `tests/integration/test_builders_chat_stream.py` — add a case asserting the
  `event: setup_request` frame shape.

**Frontend**
- Modify `dashboard/src/lib/api.ts` — add `ChatBuildSetupRequest` type and a
  `setup_request` field on `ChatBuildResult`.
- Create `dashboard/src/components/agent-wizard/SetupCard.tsx` — inline card for
  secret | mcp | provider; password/endpoint fields cleared after submit.
- Create `dashboard/src/components/agent-wizard/SetupCard.test.tsx`.
- Modify `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx` — handle the
  `setup_request` event, render `SetupCard`, continue the thread on completion.
- Modify `dashboard/src/components/agent-wizard/ChatBuildPanel.test.tsx` — add a
  setup-card flow case.

**Docs**
- Modify `website/content/docs/how-to.mdx` — document inline credential/MCP capture in the
  chat builder.

---

## Task 1: Backend driver — `request_setup` tool + `SetupRequest`

**Files:** Modify `engine/agent_chat_builder.py`; Test
`tests/unit/test_agent_chat_builder_setup.py`.

- [ ] **Step 1 — failing test** (`tests/unit/test_agent_chat_builder_setup.py`):
  - `FakeStreamingProvider` scripts a `request_setup` tool call with
    `{"kind":"mcp","name":"zendesk","reason":"read tickets"}`; assert the stream yields
    exactly one `ChatStreamEvent(type="setup_request")` whose `setup.kind == "mcp"` /
    `setup.name == "zendesk"`, followed by a `done` whose `result.setup_request` mirrors it
    and `result.agent_yaml is None`.
  - A second test: when a turn contains **both** a `submit_agent_spec` and a
    `request_setup` call, the spec wins (no setup_request event; `done.result.valid`
    reflects the spec). Belt-and-suspenders for model misbehaviour.
  - Non-stream test: `run_chat_turn` with a fake non-stream provider whose
    `result.tool_calls` is a single `request_setup` returns a `ChatTurnResult` with
    `setup_request` set and `agent_yaml is None`.
- [ ] **Step 2 — run, verify red.** `ImportError: SetupRequest`.
- [ ] **Step 3 — implement:**
  - Add dataclass:
    ```python
    @dataclass
    class SetupRequest:
        kind: Literal["secret", "mcp", "provider"]
        name: str
        reason: str = ""
    ```
  - Add `setup_request: SetupRequest | None = None` to `ChatTurnResult`.
  - Extend `ChatStreamEvent`: `type: Literal["token", "setup_request", "done"]` and add
    `setup: SetupRequest | None = None`.
  - Add the tool:
    ```python
    REQUEST_SETUP_TOOL_NAME = "request_setup"
    _REQUEST_SETUP_TOOL = ToolDefinition(
        type="function",
        function=ToolFunction(
            name=REQUEST_SETUP_TOOL_NAME,
            description=(
                "Request that the user provide a dependency the agent needs before the "
                "spec can be finished: an API key/secret, a model-provider key, or an MCP "
                "server. Call this BEFORE submit_agent_spec when the agent requires a "
                "credential or tool you cannot supply. After the user confirms it is "
                "connected, reference it by name in the spec (secrets/mcp_servers/tools) "
                "and then submit."
            ),
            parameters={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["secret", "mcp", "provider"]},
                    "name": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["kind", "name"],
            },
        ),
    )
    REQUEST_SETUP_TOOL: ToolDefinition = _REQUEST_SETUP_TOOL
    ```
  - Add `_parse_setup_request(arguments_json: str) -> SetupRequest | None` (lenient: bad
    JSON / missing fields / bad kind → `None`, logged, so a malformed tool call degrades to
    a plain reply rather than erroring).
  - Pass `tools=[SUBMIT_TOOL, REQUEST_SETUP_TOOL]` in both drivers.
  - In `run_chat_turn`: scan `result.tool_calls`; **submit wins**; else if a
    `request_setup` call parses, return `ChatTurnResult(assistant_message=result.content or
    "", setup_request=...)`.
  - In `run_chat_turn_stream`: collect `submit_args` and `setup_args` across chunks. After
    the loop: if `submit_args` → `_handle_spec_submission` → `done`. Elif `setup_args`
    parses → yield `ChatStreamEvent(type="setup_request", setup=req)` then a `done` whose
    result carries `setup_request=req` and `assistant_message="".join(text_parts)`. Else →
    plain text `done`.
- [ ] **Step 4 — run, verify green.**
- [ ] **Step 5 — commit:** `feat(builder): request_setup tool + SetupRequest (inline deps)`.

---

## Task 2: SSE endpoint emits `setup_request`

**Files:** Modify `api/routes/builders.py`; Test
`tests/integration/test_builders_chat_stream.py` (add a case).

- [ ] **Step 1 — failing test:** monkeypatch `run_chat_turn_stream` to yield a
  `setup_request` event then `done`; POST to `/api/v1/builders/chat/stream`; assert
  `event: setup_request` is in the body and its data JSON has `kind`/`name`.
- [ ] **Step 2 — red** (event not emitted).
- [ ] **Step 3 — implement:** in the generator, add
  ```python
  elif evt.type == "setup_request" and evt.setup is not None:
      yield {"event": "setup_request", "data": json.dumps(asdict(evt.setup))}
  ```
  (`asdict` already imported.) The `done` branch already serialises the full result, whose
  nested `setup_request` becomes a dict — fine.
- [ ] **Step 4 — green.** Run `tests/integration -k builders`. Confirm still auth-gated.
- [ ] **Step 5 — commit:** `feat(api): emit setup_request SSE event from chat/stream`.

---

## Task 3: Frontend types

**Files:** Modify `dashboard/src/lib/api.ts`.

- [ ] Add:
  ```typescript
  export interface ChatBuildSetupRequest {
    kind: "secret" | "mcp" | "provider";
    name: string;
    reason?: string;
  }
  ```
  and add `setup_request?: ChatBuildSetupRequest | null;` to the existing `ChatBuildResult`
  interface. No runtime change; typecheck must stay clean. (Commit folded into Task 5.)

---

## Task 4: `SetupCard` inline component

**Files:** Create `SetupCard.tsx` + `SetupCard.test.tsx`.

- [ ] **Step 1 — failing test** (`SetupCard.test.tsx`):
  - `secret` kind: renders the reason; a `type="password"` field; on submit calls
    `api.secrets.create({ name, value })`, clears the field, and fires `onComplete` with a
    confirmation string mentioning the name.
  - `mcp` kind: renders endpoint + transport inputs; on submit calls
    `api.mcpServers.create({ name, endpoint, transport })` then
    `api.mcpServers.discover(id)`, then `onComplete` with a string mentioning the tool
    count.
  - `provider` kind: password field; on submit calls `api.secrets.create` with a
    provider-key secret name (`${name}/api-key`, mirroring `provider-catalog.tsx`), then
    `onComplete`.
  - error path: rejected mutation surfaces an inline error and does NOT call `onComplete`.
- [ ] **Step 2 — red** (module missing).
- [ ] **Step 3 — implement** `SetupCard({ request, onComplete, onSkip })`:
  - `request: ChatBuildSetupRequest`; `onComplete: (confirmation: string) => void`;
    `onSkip: () => void`.
  - Branch by `request.kind`; password inputs `autoComplete="off"`, cleared after submit;
    never keep the value in state post-submit. Use `useMutation`. Show `Loader2` while
    pending, inline `AlertCircle` error on failure.
  - Confirmation strings (sent back into the thread as a user message):
    - secret → `I've added the secret \`${name}\`.`
    - provider → `I've added my ${name} API key.`
    - mcp → `I've connected the MCP server '${name}' (${toolCount} tools discovered).`
  - Reuse Tailwind patterns + `Button` from the existing panel; `data-testid="setup-card"`,
    `setup-card-submit`, kind-specific input testids.
- [ ] **Step 4 — green.**
- [ ] **Step 5 — commit folded into Task 5.**

---

## Task 5: Wire `setup_request` into ChatBuildPanel

**Files:** Modify `ChatBuildPanel.tsx` + `ChatBuildPanel.test.tsx`, `api.ts` (Task 3).

- [ ] **Step 1 — failing test** (add to `ChatBuildPanel.test.tsx`): mock
  `api.builders.chatStream` to emit a `setup_request` event (`kind:"secret"`,
  `name:"ZENDESK_API_KEY"`) then `done` with no spec. Assert the `setup-card` renders.
  Mock `api.secrets.create`; fill the password; submit; assert a second `chatStream` call
  occurs whose history includes the confirmation user message.
- [ ] **Step 2 — red.**
- [ ] **Step 3 — implement:**
  - Add `pendingSetup: ChatBuildSetupRequest | null` state. In `sendStreaming`'s event
    handler, add `else if (event === "setup_request") setPendingSetup(data as
    ChatBuildSetupRequest);`. Also read `result.setup_request` on `done` as a fallback.
  - Render `<SetupCard>` (when `pendingSetup`) in the thread above the input. Its
    `onComplete(confirmation)` pushes a user `ChatEntry` with the confirmation, clears
    `pendingSetup`, and calls `sendStreaming(buildHistory(confirmation))` to continue.
    `onSkip` clears `pendingSetup`.
  - Ensure `buildHistory` includes the just-pushed confirmation (mirror the existing
    `handleSend` ordering — append to `entries` then build from a local copy, or pass the
    confirmation explicitly as the current input like `handleSend` does).
- [ ] **Step 4 — green.** Run the full `ChatBuildPanel.test.tsx`.
- [ ] **Step 5 — full suite + typecheck:** `cd dashboard && npx vitest run && npm run
  typecheck && npm run lint`.
- [ ] **Step 6 — commit:** `feat(studio): inline setup cards (secret/mcp/provider) in chat`.

---

## Task 6: Docs + cross-repo sync

**Files:** Modify `website/content/docs/how-to.mdx`.

- [ ] Add an "Inline setup" subsection to the chat-to-build walkthrough: when the agent
  needs a credential, MCP server, or provider key, a secure card appears in the thread;
  values go to the secrets backend (masked) and the spec records only references.
- [ ] **Cloud sync check:** the cloud proxies `/builders/chat`. Confirm whether
  `agentbreeder-cloud` needs a sibling change for the new `setup_request` SSE event (likely
  pass-through if the proxy forwards arbitrary SSE frames). File a companion issue if not.
- [ ] **Commit:** `docs: inline secrets/MCP/provider capture in chat builder`.

---

## Final verification

- [ ] **Backend:** `./venv/bin/pytest tests/unit/test_agent_chat_builder_setup.py
  tests/unit/test_agent_chat_builder_stream.py tests/integration/test_builders_chat_stream.py -v`.
- [ ] **Frontend:** `cd dashboard && npx vitest run && npm run typecheck && npm run lint`.
- [ ] **Governance check:** confirm the final spec contains only refs (no secret values);
  `validate_config_yaml()` runs on every submit (unchanged from Wave 1).
- [ ] **Gate:** run `/gate`. Defer the PR until the epic per the standing preference, or
  open a Wave-2 increment.
