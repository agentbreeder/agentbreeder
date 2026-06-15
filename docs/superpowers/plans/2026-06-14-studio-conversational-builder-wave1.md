# Studio Conversational Builder — Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Studio's first-run a conversation that streams the agent-builder interview token-by-token and lets the user deploy the built agent without leaving the thread — locally and (for free, via the proxy) in cloud.

**Architecture:** Add a streaming variant of the existing chat-to-build driver (`run_chat_turn_stream`) and an SSE endpoint (`POST /api/v1/builders/chat/stream`) that reuses the existing BYO-key security contract. On the frontend, add a reusable fetch-based SSE reader, upgrade `ChatBuildPanel` to stream tokens and to deploy-from-chat (calling the existing `/deploys` + `/deployments/{id}/stream`), and turn the home empty-state into a conversational front door. No server-side `BuilderSession` resource yet — Wave 1 keeps the conversation client-held (reusing the stateless chat contract); the resumable server session lands in Wave 3 when the coding agent needs a persistent workspace. This is a deliberate YAGNI deviation from spec §6, documented here.

**Tech Stack:** Python 3.11 / FastAPI / `sse-starlette` (`EventSourceResponse`) / pytest · React 18 / TypeScript / TanStack Query / Vitest.

---

## File Structure

**Backend**
- Modify `engine/agent_chat_builder.py` — add `run_chat_turn_stream()` async generator + `ChatStreamEvent` dataclass; reuse existing `_handle_spec_submission()`.
- Modify `api/routes/builders.py` — add `POST /chat/stream` SSE endpoint (mirrors `chat_build` security, returns `EventSourceResponse`).
- Test `tests/unit/test_agent_chat_builder_stream.py` — streaming driver against a `FakeStreamingProvider`.
- Test `tests/integration/test_builders_chat_stream.py` — endpoint auth + event shape with a mocked provider.

**Frontend**
- Modify `dashboard/src/lib/api.ts` — add `streamSSE()` helper + `api.builders.chatStream()` + reuse for deploy log tailing.
- Modify `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx` — consume streamed tokens; add `DeployFromChatCard`.
- Create `dashboard/src/components/home/BuildFrontDoor.tsx` — conversational empty-state (prompt + example chips).
- Modify `dashboard/src/pages/home.tsx` — render `BuildFrontDoor` as primary empty-state; demote `GetStartedChecklist` to a progress rail.
- Test `dashboard/src/components/agent-wizard/ChatBuildPanel.test.tsx` and `dashboard/src/components/home/BuildFrontDoor.test.tsx`.

**Docs**
- Modify `website/content/docs/quickstart.mdx` and `website/content/docs/how-to.mdx` — describe the conversational front door.

---

## Task 1: Streaming chat-build driver

**Files:**
- Modify: `engine/agent_chat_builder.py`
- Test: `tests/unit/test_agent_chat_builder_stream.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_agent_chat_builder_stream.py
"""Unit tests for the streaming agent-builder driver."""
from __future__ import annotations

import pytest

from engine.agent_chat_builder import ChatStreamEvent, run_chat_turn_stream
from engine.providers.models import StreamChunk, ToolCall


class FakeStreamingProvider:
    """Yields a scripted sequence of StreamChunks from generate_stream()."""

    def __init__(self, chunks: list[StreamChunk]) -> None:
        self._chunks = chunks
        self.closed = False

    async def generate_stream(self, **_kwargs):
        for chunk in self._chunks:
            yield chunk

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stream_emits_text_tokens_then_done():
    provider = FakeStreamingProvider(
        [
            StreamChunk(content="Hi! "),
            StreamChunk(content="What should it do?"),
            StreamChunk(finish_reason="stop"),
        ]
    )
    events = [e async for e in run_chat_turn_stream(provider, [{"role": "user", "content": "hello"}])]

    tokens = [e.text for e in events if e.type == "token"]
    done = [e for e in events if e.type == "done"]

    assert "".join(tokens) == "Hi! What should it do?"
    assert len(done) == 1
    assert done[0].result.agent_yaml is None
    assert done[0].result.valid is False


@pytest.mark.asyncio
async def test_stream_handles_spec_submission():
    spec = (
        '{"name":"my-agent","version":"1.0.0","team":"default",'
        '"owner":"owner@example.com","framework":"langgraph",'
        '"model":{"primary":"claude-sonnet-4-6"},"deploy":{"cloud":"local"}}'
    )
    provider = FakeStreamingProvider(
        [
            StreamChunk(tool_calls=[ToolCall(id="t1", function_name="submit_agent_spec", function_arguments=spec)]),
            StreamChunk(finish_reason="tool_calls"),
        ]
    )
    events = [e async for e in run_chat_turn_stream(provider, [{"role": "user", "content": "build it"}])]

    done = [e for e in events if e.type == "done"]
    assert len(done) == 1
    assert done[0].result.agent_yaml is not None
    assert done[0].result.valid is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_agent_chat_builder_stream.py -v`
Expected: FAIL with `ImportError: cannot import name 'ChatStreamEvent'` (and `run_chat_turn_stream`).

- [ ] **Step 3: Write minimal implementation**

Add to `engine/agent_chat_builder.py` (after the `ChatTurnResult` dataclass, before `run_chat_turn`):

```python
from collections.abc import AsyncIterator


@dataclass
class ChatStreamEvent:
    """One event from run_chat_turn_stream().

    type == "token": `text` carries an incremental assistant text fragment.
    type == "done":  `result` carries the final ChatTurnResult (spec or text reply).
    """

    type: str
    text: str = ""
    result: ChatTurnResult | None = None


async def run_chat_turn_stream(
    provider: Any,
    history: list[dict[str, Any]],
) -> AsyncIterator[ChatStreamEvent]:
    """Streaming variant of run_chat_turn().

    Yields ChatStreamEvent(type="token") for each text fragment as it arrives,
    then exactly one ChatStreamEvent(type="done") with the final ChatTurnResult.
    Security contract is identical to run_chat_turn(): the key lives inside the
    injected provider and is never read, logged, or returned here.
    """
    if len(history) > MAX_HISTORY_MESSAGES:
        history = history[-MAX_HISTORY_MESSAGES:]
    history = [m for m in history if m.get("role") in {"user", "assistant"}]

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *history,
    ]

    text_parts: list[str] = []
    tool_args: str | None = None

    async for chunk in provider.generate_stream(
        messages=messages,
        model=DEFAULT_MODEL,
        max_tokens=2048,
        tools=[SUBMIT_TOOL],
    ):
        if chunk.content:
            text_parts.append(chunk.content)
            yield ChatStreamEvent(type="token", text=chunk.content)
        if chunk.tool_calls:
            for tc in chunk.tool_calls:
                if tc.function_name == SUBMIT_TOOL_NAME and tc.function_arguments:
                    tool_args = tc.function_arguments

    if tool_args is not None:
        result = _handle_spec_submission(tool_args)
    else:
        result = ChatTurnResult(
            assistant_message="".join(text_parts),
            agent_yaml=None,
            valid=False,
            errors=[],
        )
    yield ChatStreamEvent(type="done", result=result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/unit/test_agent_chat_builder_stream.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Verify AnthropicProvider conforms to the contract**

The streaming driver assumes `AnthropicProvider.generate_stream()` yields `StreamChunk.content` text fragments and, for tool use, a `StreamChunk.tool_calls[*]` whose `function_arguments` is the fully-assembled JSON. Read `engine/providers/anthropic_provider.py::generate_stream` and confirm it assembles `input_json_delta` partials into a complete arguments string before yielding (Anthropic streams tool input as `partial_json`). If it does not, add the assembly there with its own unit test — do NOT work around it in the driver.

Run: `./venv/bin/pytest tests/unit/ -k "anthropic and stream" -v`
Expected: PASS (or add a test that pins the assembled-args behavior).

- [ ] **Step 6: Commit**

```bash
git add engine/agent_chat_builder.py tests/unit/test_agent_chat_builder_stream.py
git commit -m "feat(builder): streaming chat-build driver (run_chat_turn_stream)"
```

---

## Task 2: SSE endpoint `POST /builders/chat/stream`

**Files:**
- Modify: `api/routes/builders.py`
- Test: `tests/integration/test_builders_chat_stream.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_builders_chat_stream.py
"""Integration tests for POST /api/v1/builders/chat/stream."""
from __future__ import annotations

import pytest

from engine.agent_chat_builder import ChatStreamEvent, ChatTurnResult


@pytest.mark.asyncio
async def test_chat_stream_requires_key(async_client, auth_headers, monkeypatch):
    """With no BYO key stored, the endpoint returns 400 before constructing a provider."""

    class _Backend:
        async def get(self, _name):
            return None

    monkeypatch.setattr(
        "api.routes.builders.get_workspace_backend", lambda: (_Backend(), None)
    )

    resp = await async_client.post(
        "/api/v1/builders/chat/stream",
        headers=auth_headers,
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400
    assert "key" in resp.text.lower()


@pytest.mark.asyncio
async def test_chat_stream_emits_token_and_done_events(async_client, auth_headers, monkeypatch):
    class _Backend:
        async def get(self, _name):
            return "sk-ant-test"

    monkeypatch.setattr(
        "api.routes.builders.get_workspace_backend", lambda: (_Backend(), None)
    )

    class _Provider:
        def __init__(self, *_a, **_k):
            pass

        async def close(self):
            pass

    monkeypatch.setattr("api.routes.builders.AnthropicProvider", _Provider)

    async def _fake_stream(_provider, _history):
        yield ChatStreamEvent(type="token", text="Hello")
        yield ChatStreamEvent(
            type="done",
            result=ChatTurnResult(assistant_message="Hello", agent_yaml=None, valid=False, errors=[]),
        )

    monkeypatch.setattr("api.routes.builders.run_chat_turn_stream", _fake_stream)

    resp = await async_client.post(
        "/api/v1/builders/chat/stream",
        headers=auth_headers,
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    body = resp.text
    assert "event: token" in body
    assert "event: done" in body
    assert "Hello" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/integration/test_builders_chat_stream.py -v`
Expected: FAIL with 404 (route not registered) / missing `run_chat_turn_stream` import.

- [ ] **Step 3: Write minimal implementation**

At the top of `api/routes/builders.py`, extend the existing import:

```python
from engine.agent_chat_builder import (
    ChatTurnResult,
    run_chat_turn,
    run_chat_turn_stream,
)
```

Add the `sse-starlette` import near the other imports:

```python
from sse_starlette.sse import EventSourceResponse
```

Add the endpoint immediately after `chat_build`:

```python
@router.post("/chat/stream")
async def chat_build_stream(
    body: ChatBuildRequest,
    current_user: User = Depends(get_current_user),
) -> EventSourceResponse:
    """Streaming variant of POST /builders/chat.

    Identical BYO-key security contract: the key is read server-side from the
    workspace secrets backend (AGENTBREEDER_CLAUDE_BUILDER_KEY__{user.id}),
    never stored, never returned, never logged. Emits SSE events:
      - "token": {"text": "..."}   incremental assistant text
      - "done":  the final ChatTurnResult as JSON
      - "error": {"detail": "..."} on upstream/auth failure
    """
    secret_name = _builder_key_name(current_user)
    backend, _ws = get_workspace_backend()
    api_key: str | None = await backend.get(secret_name)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "No Claude key connected. Add your Claude API key in "
                f"Settings → Secrets as '{secret_name}'."
            ),
        )

    history = [{"role": m.role, "content": m.content} for m in body.messages]

    async def generator() -> AsyncGenerator[dict[str, str], None]:
        provider = AnthropicProvider(
            ProviderConfig(provider_type=ProviderType.anthropic, api_key=api_key)
        )
        try:
            async for evt in run_chat_turn_stream(provider, history):
                if evt.type == "token":
                    yield {"event": "token", "data": json.dumps({"text": evt.text})}
                elif evt.type == "done" and evt.result is not None:
                    yield {"event": "done", "data": json.dumps(asdict(evt.result))}
        except AuthenticationError:
            logger.warning("chat_build_stream: auth failed for '%s' (key not logged)", secret_name)
            yield {"event": "error", "data": json.dumps({"detail": "Claude API authentication failed."})}
        except ProviderError:
            logger.warning("chat_build_stream: upstream error for '%s'.", secret_name)
            yield {"event": "error", "data": json.dumps({"detail": "Upstream Claude API error."})}
        finally:
            await provider.close()

    return EventSourceResponse(generator())
```

Ensure these are imported at the top of the file (add any missing): `import json`, `from dataclasses import asdict`, `from collections.abc import AsyncGenerator`, and the existing `AuthenticationError`, `ProviderError` from `engine.providers.base`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/integration/test_builders_chat_stream.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Verify the route is wired and auth-gated**

Run: `./venv/bin/pytest tests/integration/ -k builders -v`
Expected: PASS. Confirm `/builders/chat/stream` appears in the OpenAPI and is behind `get_current_user` (no anonymous access), consistent with the 247/247 auth-gated-routes invariant.

- [ ] **Step 6: Commit**

```bash
git add api/routes/builders.py tests/integration/test_builders_chat_stream.py
git commit -m "feat(api): SSE streaming endpoint POST /builders/chat/stream"
```

---

## Task 3: Frontend SSE helper + `api.builders.chatStream`

**Files:**
- Modify: `dashboard/src/lib/api.ts`
- Test: `dashboard/src/lib/api.test.ts` (create if absent)

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard/src/lib/api.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { streamSSE } from "@/lib/api";

function sseResponse(body: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

describe("streamSSE", () => {
  beforeEach(() => {
    localStorage.setItem("ag-token", "tok");
  });

  it("parses event/data frames and invokes the handler per event", async () => {
    const raw =
      "event: token\ndata: {\"text\":\"Hi\"}\n\n" +
      "event: done\ndata: {\"agent_yaml\":null}\n\n";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(raw));

    const seen: Array<{ event: string; data: unknown }> = [];
    await streamSSE("/builders/chat/stream", { method: "POST", body: "{}" }, (e, d) =>
      seen.push({ event: e, data: d }),
    );

    expect(seen).toEqual([
      { event: "token", data: { text: "Hi" } },
      { event: "done", data: { agent_yaml: null } },
    ]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run src/lib/api.test.ts`
Expected: FAIL — `streamSSE` is not exported.

- [ ] **Step 3: Write minimal implementation**

Add to `dashboard/src/lib/api.ts` (after the `request` helper, and export it):

```typescript
/**
 * Stream a Server-Sent-Events endpoint using fetch (so the Authorization
 * header works — EventSource cannot set headers). Calls `onEvent(event, data)`
 * for each parsed `event:`/`data:` frame. Resolves when the stream closes.
 */
export async function streamSSE(
  path: string,
  init: RequestInit,
  onEvent: (event: string, data: unknown) => void,
): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { headers: getAuthHeaders(), ...init });
  if (res.status === 401) {
    localStorage.removeItem("ag-token");
    if (window.location.pathname !== "/login") window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `API error ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;
      const dataStr = dataLines.join("\n");
      let data: unknown = dataStr;
      try {
        data = JSON.parse(dataStr);
      } catch {
        /* keep raw string */
      }
      onEvent(event, data);
    }
  }
}
```

Add to the `builders` object in `api` (alongside `chat`):

```typescript
    chatStream: (
      messages: ChatBuildMessage[],
      onEvent: (event: string, data: unknown) => void,
    ) =>
      streamSSE(
        "/builders/chat/stream",
        { method: "POST", body: JSON.stringify({ messages }) },
        onEvent,
      ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run src/lib/api.test.ts`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/lib/api.ts dashboard/src/lib/api.test.ts
git commit -m "feat(studio): fetch-based SSE helper + api.builders.chatStream"
```

---

## Task 4: Stream tokens in ChatBuildPanel

**Files:**
- Modify: `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx`
- Test: `dashboard/src/components/agent-wizard/ChatBuildPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard/src/components/agent-wizard/ChatBuildPanel.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ChatBuildPanel } from "./ChatBuildPanel";
import { api } from "@/lib/api";

vi.mock("@/hooks/use-auth", () => ({ useAuth: () => ({ user: { id: "u1" } }) }));

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ChatBuildPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ChatBuildPanel streaming", () => {
  beforeEach(() => {
    vi.spyOn(api.secrets, "list").mockResolvedValue({
      data: [{ name: "AGENTBREEDER_CLAUDE_BUILDER_KEY__u1" }],
      meta: { page: 1, per_page: 50, total: 1 },
      errors: [],
    } as never);
  });

  it("renders streamed tokens incrementally then the done reply", async () => {
    vi.spyOn(api.builders, "chatStream").mockImplementation(async (_m, onEvent) => {
      onEvent("token", { text: "Hel" });
      onEvent("token", { text: "lo" });
      onEvent("done", { assistant_message: "Hello", agent_yaml: null, valid: false, errors: [] });
    });

    renderPanel();
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "hi" } });
    fireEvent.click(screen.getByTestId("send-btn"));

    await waitFor(() => expect(screen.getByText("Hello")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run src/components/agent-wizard/ChatBuildPanel.test.tsx`
Expected: FAIL — panel still calls `api.builders.chat` (mock for `chatStream` unused; no "Hello" appears as a streamed bubble).

- [ ] **Step 3: Write minimal implementation**

In `ChatBuildPanel.tsx`, replace the `chatMutation` (useMutation calling `api.builders.chat`) and `handleSend` body with a streaming send. Add a `streaming` text buffer state and render it as a live assistant bubble:

```typescript
  const [streaming, setStreaming] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const sendStreaming = useCallback(
    async (msgs: ChatBuildMessage[]) => {
      setSending(true);
      setStreaming("");
      setSendError(null);
      let acc = "";
      try {
        await api.builders.chatStream(msgs, (event, data) => {
          if (event === "token") {
            acc += (data as { text: string }).text;
            setStreaming(acc);
          } else if (event === "done") {
            const result = data as ChatBuildResult;
            setStreaming(null);
            if (result.agent_yaml) {
              setPendingSpec(result);
              if (result.assistant_message) {
                setEntries((prev) => [
                  ...prev,
                  { id: generateId(), role: "assistant", content: result.assistant_message },
                ]);
              }
            } else {
              setEntries((prev) => [
                ...prev,
                { id: generateId(), role: "assistant", content: result.assistant_message || acc },
              ]);
            }
          } else if (event === "error") {
            setSendError((data as { detail?: string }).detail ?? "Something went wrong.");
          }
        });
      } catch (err) {
        setSendError((err as Error).message || "Something went wrong.");
      } finally {
        setSending(false);
        setStreaming(null);
      }
    },
    [],
  );
```

Update `handleSend` to call `void sendStreaming(buildHistory(trimmed))` instead of `chatMutation.mutate(...)`, and replace every `chatMutation.isPending` with `sending` and `chatMutation.isError`/`chatMutation.error` with `sendError`. Render the live streaming bubble below the message list:

```tsx
        {streaming !== null && (
          <ChatBubble entry={{ id: "streaming", role: "assistant", content: streaming || "…" }} />
        )}
```

Remove the now-unused `useMutation` import for chat (keep it if still used by `SpecReadyCard`/`KeyEntryGuard`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run src/components/agent-wizard/ChatBuildPanel.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/agent-wizard/ChatBuildPanel.tsx dashboard/src/components/agent-wizard/ChatBuildPanel.test.tsx
git commit -m "feat(studio): stream interview tokens in ChatBuildPanel"
```

---

## Task 5: Deploy-from-chat

**Files:**
- Modify: `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx`
- Test: `dashboard/src/components/agent-wizard/ChatBuildPanel.test.tsx` (add a case)

The existing `SpecReadyCard` only offers "Create agent". Add a **Deploy** action: create the agent from YAML, trigger a `local` deploy, and tail logs via the existing deployment SSE stream (`/api/v1/deployments/{id}/stream` — note the `deployments` prefix, distinct from the `deploys` create route).

- [ ] **Step 1: Write the failing test**

```typescript
// add to ChatBuildPanel.test.tsx
import { within } from "@testing-library/react";

it("deploys the built agent and tails logs to the thread", async () => {
  vi.spyOn(api.builders, "chatStream").mockImplementation(async (_m, onEvent) => {
    onEvent("done", {
      assistant_message: "",
      agent_yaml: "name: my-agent\nversion: 1.0.0\n",
      valid: true,
      errors: [],
    });
  });
  vi.spyOn(api.agents, "fromYaml").mockResolvedValue({
    data: { id: "a1", name: "my-agent" }, meta: { page: 1, per_page: 1, total: 1 }, errors: [],
  } as never);
  vi.spyOn(api.deploys, "create").mockResolvedValue({
    data: { id: "job1", status: "parsing" }, meta: { page: 1, per_page: 1, total: 1 }, errors: [],
  } as never);
  const streamSpy = vi
    .spyOn(api.deploys, "streamLogs")
    .mockImplementation(async (_id, onEvent) => {
      onEvent("log", { level: "info", message: "Building image…" });
      onEvent("complete", { endpoint_url: "http://localhost:8080" });
    });

  renderPanel();
  fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "build it" } });
  fireEvent.click(screen.getByTestId("send-btn"));

  const card = await screen.findByTestId("spec-ready-card");
  fireEvent.click(within(card).getByTestId("deploy-agent-btn"));

  await waitFor(() => expect(streamSpy).toHaveBeenCalledWith("job1", expect.any(Function)));
  await waitFor(() => expect(screen.getByText(/Building image/)).toBeInTheDocument());
  await waitFor(() => expect(screen.getByText(/localhost:8080/)).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run src/components/agent-wizard/ChatBuildPanel.test.tsx`
Expected: FAIL — `api.deploys.streamLogs` undefined and no `deploy-agent-btn`.

- [ ] **Step 3: Write minimal implementation**

Add `streamLogs` to the `deploys` object in `dashboard/src/lib/api.ts`:

```typescript
    streamLogs: (id: string, onEvent: (event: string, data: unknown) => void) =>
      streamSSE(`/deployments/${id}/stream`, { method: "GET" }, onEvent),
```

In `ChatBuildPanel.tsx`, extend `SpecReadyCard` with a Deploy button and inline log tail:

```tsx
  const [logs, setLogs] = useState<string[]>([]);
  const [endpoint, setEndpoint] = useState<string | null>(null);
  const [deploying, setDeploying] = useState(false);

  async function handleDeploy() {
    setDeploying(true);
    setLogs([]);
    setEndpoint(null);
    try {
      await api.agents.fromYaml(agentYaml);
      const job = await api.deploys.create({ config_yaml: agentYaml, target: "local" });
      await api.deploys.streamLogs(job.data.id, (event, data) => {
        if (event === "log") {
          setLogs((prev) => [...prev, (data as { message: string }).message]);
        } else if (event === "complete") {
          const url = (data as { endpoint_url?: string }).endpoint_url ?? null;
          setEndpoint(url);
        } else if (event === "error") {
          setLogs((prev) => [...prev, `Error: ${(data as { detail?: string }).detail ?? "deploy failed"}`]);
        }
      });
    } finally {
      setDeploying(false);
    }
  }
```

Render (inside the valid branch of `SpecReadyCard`, below the existing "Create agent" button):

```tsx
      <Button
        data-testid="deploy-agent-btn"
        variant="secondary"
        onClick={handleDeploy}
        disabled={deploying}
        className="w-full"
      >
        {deploying ? (
          <><Loader2 className="mr-2 size-4 animate-spin" />Deploying…</>
        ) : (
          "Deploy now"
        )}
      </Button>
      {logs.length > 0 && (
        <pre className="rounded-lg bg-background border border-border px-3 py-2 text-[11px] max-h-40 overflow-y-auto">
          {logs.join("\n")}
        </pre>
      )}
      {endpoint && (
        <a href={endpoint} target="_blank" rel="noopener noreferrer" className="text-xs underline text-green-600">
          Agent live at {endpoint} — test it in the Playground
        </a>
      )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run src/components/agent-wizard/ChatBuildPanel.test.tsx`
Expected: PASS (all cases).

- [ ] **Step 5: Verify the deployment stream path against the backend**

Confirm the `DeployJob.id` returned by `POST /api/v1/deploys` is the same id accepted by `GET /api/v1/deployments/{id}/stream` (both should resolve through `app.state.deploy_job_service`). If the two routers use different id spaces, adjust `streamLogs` to the correct prefix. Run a local smoke deploy: `agentbreeder deploy --target local` and confirm logs stream.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/lib/api.ts dashboard/src/components/agent-wizard/ChatBuildPanel.tsx dashboard/src/components/agent-wizard/ChatBuildPanel.test.tsx
git commit -m "feat(studio): deploy-from-chat with live log tail"
```

---

## Task 6: Conversational front door on home

**Files:**
- Create: `dashboard/src/components/home/BuildFrontDoor.tsx`
- Modify: `dashboard/src/pages/home.tsx`
- Test: `dashboard/src/components/home/BuildFrontDoor.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard/src/components/home/BuildFrontDoor.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BuildFrontDoor } from "./BuildFrontDoor";

describe("BuildFrontDoor", () => {
  it("shows the prompt and example chips, and calls onStart with the chosen text", () => {
    const onStart = vi.fn();
    render(<BuildFrontDoor onStart={onStart} />);
    expect(screen.getByText(/what do you want to build/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/support agent/i));
    expect(onStart).toHaveBeenCalledWith(expect.stringMatching(/support agent/i));
  });

  it("submits the freeform prompt", () => {
    const onStart = vi.fn();
    render(<BuildFrontDoor onStart={onStart} />);
    fireEvent.change(screen.getByTestId("frontdoor-input"), { target: { value: "an invoice bot" } });
    fireEvent.submit(screen.getByTestId("frontdoor-form"));
    expect(onStart).toHaveBeenCalledWith("an invoice bot");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npx vitest run src/components/home/BuildFrontDoor.test.tsx`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

```tsx
// dashboard/src/components/home/BuildFrontDoor.tsx
import { useState } from "react";
import { Sparkles, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const EXAMPLES = [
  "A support agent that reads our docs and Zendesk",
  "A daily news digest agent emailed to the team",
  "An invoice-processing agent that extracts line items",
];

export function BuildFrontDoor({ onStart }: { onStart: (prompt: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center gap-6 py-12 text-center">
      <div className="flex size-12 items-center justify-center rounded-full bg-primary/10">
        <Sparkles className="size-6 text-primary" />
      </div>
      <h1 className="text-2xl font-semibold">What do you want to build today?</h1>
      <p className="text-sm text-muted-foreground">
        Describe your agent in plain language. I&apos;ll ask a few questions, generate a
        ready-to-deploy <span className="font-mono">agent.yaml</span>, and ship it.
      </p>
      <form
        data-testid="frontdoor-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onStart(value.trim());
        }}
        className="flex w-full items-center gap-2"
      >
        <input
          data-testid="frontdoor-input"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. a customer-support agent for our SaaS"
          className={cn(
            "flex-1 rounded-lg border border-border bg-background px-4 py-3 text-sm",
            "focus:outline-none focus:ring-2 focus:ring-primary/50",
          )}
        />
        <Button type="submit" disabled={!value.trim()} size="icon" className="shrink-0">
          <ArrowRight className="size-4" />
        </Button>
      </form>
      <div className="flex flex-wrap justify-center gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => onStart(ex)}
            className="rounded-full border border-border bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted"
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npx vitest run src/components/home/BuildFrontDoor.test.tsx`
Expected: PASS (2 passed).

- [ ] **Step 5: Wire into home.tsx**

In `dashboard/src/pages/home.tsx`: when the workspace has **no agents yet**, render `<BuildFrontDoor onStart={(p) => navigate("/agents/new?prompt=" + encodeURIComponent(p) + "&mode=chat")} />` as the primary hero (above stats). Move `<GetStartedChecklist />` into a smaller secondary "Setup progress" rail (e.g. a right column or a collapsed card) rather than the main focus. Keep the checklist component itself unchanged. Then in `agent-wizard.tsx`, read the `prompt` query param and, when present with `mode=chat`, pre-seed the chat panel's first user message and auto-send.

- [ ] **Step 6: Run the full frontend suite + typecheck**

Run: `cd dashboard && npx vitest run && npm run typecheck`
Expected: PASS, zero TS errors.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/components/home/BuildFrontDoor.tsx dashboard/src/components/home/BuildFrontDoor.test.tsx dashboard/src/pages/home.tsx dashboard/src/pages/agent-wizard.tsx
git commit -m "feat(studio): conversational front door on home empty-state"
```

---

## Task 7: Docs + cross-repo sync

**Files:**
- Modify: `website/content/docs/quickstart.mdx`
- Modify: `website/content/docs/how-to.mdx`

- [ ] **Step 1: Update quickstart**

Add a "Build from a conversation" section to `quickstart.mdx`: land in Studio → type what you want → answer 3–5 streamed questions → review the generated `agent.yaml` → click **Deploy now** and watch logs in the thread. Note it works locally and in cloud (cloud meters turns).

- [ ] **Step 2: Update how-to**

In `how-to.mdx`, add the conversational front door to the Studio walkthrough and cross-link to the BYO-key Settings → Secrets step.

- [ ] **Step 3: Cloud sync check**

The cloud proxies `/builders/chat`. Confirm whether `agentbreeder-cloud/api/routes/builders.py` needs a sibling `/cloud/builders/chat/stream` proxy for SSE. If yes, file a companion issue/PR in `agentbreeder-cloud` (SSE passthrough + per-turn metering) per the cross-repo sync rule. If the cloud proxy forwards arbitrary paths, verify streaming passes through unbuffered.

- [ ] **Step 4: Commit**

```bash
git add website/content/docs/quickstart.mdx website/content/docs/how-to.mdx
git commit -m "docs: conversational front door + deploy-from-chat (quickstart, how-to)"
```

---

## Final verification

- [ ] **Backend:** `./venv/bin/pytest tests/unit/test_agent_chat_builder_stream.py tests/integration/test_builders_chat_stream.py -v` — all pass.
- [ ] **Frontend:** `cd dashboard && npx vitest run && npm run typecheck && npm run lint` — all pass.
- [ ] **Manual smoke:** run API + dashboard locally, connect a Claude key, type a prompt at the front door, confirm streamed interview → valid spec → "Deploy now" → live logs → endpoint link.
- [ ] **Gate:** run `/gate` and let all gates pass before opening the Wave-1 PR (defer the PR until the full epic per the standing "defer PR until done" preference — or open a Wave-1 PR if shipping incrementally).

---

# Waves 2–4 (outline — each gets its own plan before implementation)

**Wave 2 — Inline secrets / MCP / provider capture.** Add `setup_request` events to the builder driver (kind = secret|mcp|provider). Render inline cards in the thread (reuse `provider-catalog.tsx` + `ChatBuildPanel` key-entry pattern). On submit, call `/secrets`, `/providers`, or `/mcp-servers` (+ `/{id}/discover`); record the dependency by reference and re-validate the spec. Tests: driver emits setup_request when a required dep is missing; card submits to the right endpoint; spec re-validates.

**Wave 3 — Eject-to-code (Claude + Codex) + Sandbox + LocalSandbox.** Introduce the server-side resumable `BuilderSession` (spec §6), the `Sandbox` protocol with `LocalSandbox`, and `CodingAgentEngine` with `ClaudeAgentEngine` (Claude Agent SDK) and `CodexEngine` (OpenAI Agents). Add `POST /builder/sessions/{id}/eject` (engine selector) streaming `file_change` events; artifact-panel Code tab with diffs. Eject may propose `agent.yaml` edits, always re-validated through governance. Tests: `FakeSandbox`, engine event parsing with mocked providers, golden transcripts per engine.

**Wave 4 — CloudSandbox + metering + analytics.** Implement `CloudSandbox` (managed e2b-style microVM: egress allowlist, CPU/mem/time caps, auto-teardown, secrets-as-env). Add `/cloud/builder/sessions/*` proxy with turn + sandbox-minute metering against `QuotaCounter`. Ship the analytics funnel (spec §12) and a builder-funnel view. Tests: sandbox safety (egress blocked, caps enforced, teardown, secrets-not-on-disk), metering, end-to-end cloud build→deploy.
