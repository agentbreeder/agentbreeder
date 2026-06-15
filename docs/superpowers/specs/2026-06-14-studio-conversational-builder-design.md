# Studio Conversational Agent Builder ("White-Glove") — Design

> Status: Approved direction (brainstorm complete) — pending spec review
> Date: 2026-06-14
> Branch: `feat/studio-conversational-builder`
> Author: Rajit + Claude

## 1. Problem & Vision

AgentBreeder Studio's first-run experience is a static onboarding checklist (Connect a
model → Create agent → Test → Deploy). The pieces of a conversational builder already
exist but are disconnected and under-ambitious. We want the **first interface a user
sees to be a conversation**: an agent that asks what they want to build, what the
business use case is, where it should run — navigates them with the right questions,
collects what it needs (models, MCP servers, secrets) inline and securely, then
**builds and deploys the agent from the chat itself**. White-glove service, Lovable-style.

This must work identically when Studio runs **locally** (`agentbreeder` on the user's
machine) and **in the cloud** (console.agentbreeder.io), because the cloud is a thin
metered proxy over the same OSS surface.

### One-sentence goal
A user lands in Studio, types "a support agent that reads our docs and Zendesk," and a
guided conversation produces a governed, deployed, running agent — without leaving the
thread.

## 2. What already exists (grounding)

The build→deploy loop is ~75% wired but fragmented:

| Capability | Where | State |
|---|---|---|
| Chat-to-build (interview → validated `agent.yaml`) | `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx` → `POST /api/v1/builders/chat` → `engine/agent_chat_builder.py` | Works, one-shot, non-streaming |
| Deterministic stack advisor | `engine/recommend.py` + `POST /builders/recommend` | Pure function, reusable |
| Deploy from raw YAML + SSE logs | `POST /api/v1/deploys`, `GET /api/v1/deployments/{job}/stream` | Works |
| Secrets (BYO key, server-side, masked) | `engine/secrets/*`, `POST /api/v1/secrets` | Works |
| Provider catalog + key entry | `dashboard/src/components/provider-catalog.tsx`, `/providers/catalog` | Works |
| MCP register/discover | `POST /api/v1/mcp-servers`, `/{id}/discover` | Works |
| Streaming provider support | `engine/providers/base.py::generate_stream()` | Exists, not wired into builder |
| Cloud proxy + metering + multi-tenant | `agentbreeder-cloud` `/cloud/builders/chat`, `QuotaCounter`, `TenantMiddleware` | Works |

### Gaps that prevent the "Lovable feeling"
1. Builder is one-shot interview → YAML; it cannot write `agent.py`, custom tools, or iterate (no code generation).
2. No streaming in chat (`generate()` not `generate_stream()`).
3. No refinement loop after validation fails.
4. Deploy is a separate page (`/deploy-wizard`), not continuous from the chat.
5. No conversational front door — users land on a checklist.
6. Secrets/MCP/provider setup happen on separate settings pages, not inline.

## 3. Decisions (locked)

| Decision | Choice |
|---|---|
| Builder brain | **Hybrid**: interview → governed `agent.yaml` (Phase 1), then optional eject to coding agent for `agent.py`/tools/tests (Phase 2) |
| Coding-agent engine | **Both Claude (Agent SDK) and Codex (OpenAI Agents), selectable per session** |
| Code-gen sandbox | **Local in-process + managed cloud sandbox service** (symmetric UX) |
| Cloud sandbox provider | **Managed e2b-style code-exec sandbox** (Cloud Run jobs as fallback) |
| Front door | **Conversation replaces the home checklist** as primary empty-state; checklist becomes a quiet progress rail |
| Eject scope (v1) | Coding agent writes `agent.py`/tools/tests **and may propose `agent.yaml` edits, always re-validated through governance** — never a silent bypass |
| Delivery | **Phased epic, 4 waves**, each independently shippable; PR opened only after the epic + local tests pass |
| Deliverable for this session | Design spec + implementation plan |

## 4. The Experience (end to end)

The front door is a single prompt — *"What do you want to build today?"* — with example
chips. Once a build starts, it becomes a two-pane **build console**.

1. **Interview (Phase 1 brain)** — agent asks 3–5 targeted questions (use case,
   framework/cloud/scale, tools/data), streamed token-by-token. Backed by
   `recommend.py` so it *proposes* a stack rather than interrogating.
2. **Inline setup cards** — when the plan needs a model key, MCP server, or secret, a
   secure inline card appears in the thread. Values go straight to the secrets backend,
   masked, never in browser state.
3. **Spec preview** — the validated `agent.yaml` renders live in the artifact panel,
   editable, with inline validation errors.
4. **Eject to code (Phase 2 brain, optional)** — "Need custom logic?" hands the project
   to the selected coding agent (Claude or Codex) in a sandbox, which writes
   `agent.py` + `tools/` + tests, shows diffs, and iterates on follow-up messages.
5. **Deploy from the thread** — Deploy runs the existing 8-step pipeline with live SSE
   logs streaming into the same conversation, ending with the endpoint URL and a
   "test it now" link to the playground.

## 5. Architecture (architect lens)

The unifying abstraction is a server-side, tenant-scoped, resumable **`BuilderSession`**
holding: conversation history, evolving `agent.yaml`, scaffold workspace handle, deploy
job handle, selected engine, and satisfied-dependency references (secrets/MCP by ref).

```
Studio chat thread (local + cloud, same React surface)
        │  SSE (tokens, setup-card requests, spec updates, file diffs, deploy logs)
        ▼
BuilderSession  ──┬─ Interviewer  → engine/agent_chat_builder + recommend.py   (Phase 1)
                  ├─ CodingAgent   → ClaudeAgentEngine | CodexEngine           (Phase 2)
                  │                     └─ Sandbox (interface)
                  │                          ├─ LocalSandbox   (in-process)
                  │                          └─ CloudSandbox   (managed microVM)
                  ├─ SecretsBridge → engine/secrets/* (inline capture)
                  ├─ MCPBridge     → /mcp-servers discover/connect
                  └─ Deployer      → existing /deploys pipeline + SSE
```

### Principles (honoring CLAUDE.md)
- **Governance is non-negotiable.** The coding agent writes files into a scaffold
  workspace; results still flow through Parse → RBAC → Resolve → Build → Provision →
  Deploy → Health → Register. No "quick deploy." Registry writes go through `registry/`.
- **`Sandbox` is a pluggable interface** — the coding agent is written once against it.
- **Engine selection is a strategy** behind one `CodingAgentEngine` protocol.
- **Cloud stays a thin metered proxy** — it forwards `BuilderSession` traffic to OSS and
  counts turns + sandbox-minutes against quota. No logic fork.
- **Framework-agnostic** — codegen targets whatever framework `recommend.py`/the spec
  chose; no framework names hard-coded outside `engine/runtimes/`.

## 6. Backend contracts

New `BuilderSession` resource (OSS API):

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/builder/sessions` | Create session; returns id + opening assistant message |
| GET | `/api/v1/builder/sessions/{id}` | Get state (history, current yaml, files, deploy status) |
| POST | `/api/v1/builder/sessions/{id}/messages` | Send user message; SSE stream of the response |
| POST | `/api/v1/builder/sessions/{id}/eject` | Begin Phase 2 with `engine: claude|codex` |
| GET | `/api/v1/builder/sessions/{id}/stream` | SSE for all session events |
| POST | `/api/v1/builder/sessions/{id}/deploy` | Trigger deploy from current spec |

- Builder switches to `provider.generate_stream()`.
- **SSE event taxonomy**: `token`, `assistant_message`, `spec_update`, `setup_request`
  (kind = secret|mcp|provider), `validation` (valid + errors), `file_change` (path +
  diff), `deploy_log`, `complete`, `error`.
- Underneath, reuses `/secrets`, `/mcp-servers`, `/deploys` — no duplicate logic.
- **Cloud**: mirrored as `/cloud/builder/sessions/*` proxy with quota metering on turns
  and sandbox-minutes.
- Bounds: max messages/chars per session (extend current 40 msg / 100k char limits),
  per-user rate limit on sessions and deploy triggers.

## 7. Sandbox + Coding-Agent Engine interfaces

```python
class Sandbox(Protocol):
    async def write(self, path: str, content: str) -> None
    async def read(self, path: str) -> str
    async def list(self, dir: str) -> list[str]
    async def exec(self, cmd: list[str], timeout: float) -> ExecResult
    async def snapshot(self) -> bytes        # export project for deploy
    async def close(self) -> None
```

- **LocalSandbox** — temp dir + subprocess on the user's machine.
- **CloudSandbox** — managed e2b-style microVM per session: tenant-scoped, ephemeral,
  network egress allowlist, CPU/mem/wall-clock caps, auto-teardown, **secrets injected
  as env vars, never written to disk**.

```python
class CodingAgentEngine(Protocol):
    async def run(self, session, instruction: str, sandbox: Sandbox) -> AsyncIterator[AgentEvent]
```

- **ClaudeAgentEngine** — Claude Agent SDK headless; tools = filesystem + bash scoped to
  the sandbox; BYO Anthropic key from secrets backend.
- **CodexEngine** — OpenAI Agents loop; same tool surface; BYO OpenAI key.
- Both bounded by max turns/tokens/wall-clock; emit `file_change` + tool-call events.

## 8. Secrets & MCP in-conversation

- Interviewer and coding agent emit `setup_request` events; the UI renders an inline
  card (not a modal, not a page).
- On submit, the frontend calls `/secrets` (masked, server-side) or `/mcp-servers`
  (register + `/{id}/discover` to pull tools). The session records the dependency as
  satisfied **by reference**, never the value; `agent.yaml` gets the `secrets:` /
  `mcp_servers:` / `tools:` refs and is re-validated.
- Reuses `provider-catalog.tsx` and the `ChatBuildPanel` key-entry pattern (password
  field, cleared after submit, never in component state).

## 9. UI/UX & Frontend (ui-ux-pro-max + frontend-design lens)

- **Two-pane build console**: conversation thread (left) + **artifact panel** (right)
  tabbing between *Spec* (`agent.yaml`), *Code* (file tree + diffs, Phase 2), *Deploy*
  (streaming logs → endpoint). Lovable/Bolt pattern; reuses `MessageBubble`,
  `ToolCallCard`, `StreamingBubble`.
- **Inline cards as first-class messages**: secret capture, MCP connect, plan approval,
  deploy confirm — all inside the thread.
- **Progressive disclosure**: beginners see only the conversation; the artifact panel and
  "eject to code" reveal as the build matures. Power users can jump to the existing
  YAML/visual `agent-builder.tsx` (tier mobility preserved).
- **Front door**: full-width centered prompt + example chips as home empty-state,
  collapsing into the two-pane console once a build begins.
- **States & a11y**: every streaming/loading/error state handled (TS standards); a11y on
  inline cards, focus management on auto-scroll, WCAG 2.2 AA on the new surface.

## 10. Local vs Cloud parity

Same React surface, same `BuilderSession` API. Differences are isolated:

| Concern | Local | Cloud |
|---|---|---|
| Auth | single-user / local token | multi-tenant JWT + `TenantMiddleware` |
| Sandbox | `LocalSandbox` (in-process) | `CloudSandbox` (managed) |
| Metering | none | quota on turns + sandbox-minutes |
| Engine selection | available | available |
| Secrets backend | env/keychain | tenant workspace backend |

## 11. Model E2E testing (model-e2e-test lens)

- **Unit**: `recommend.py` decisions, session state machine, sandbox interface against a
  `FakeSandbox`, engine event parsing with a mocked provider.
- **Integration**: API endpoints with deterministic mocked tool-call fixtures — interview
  turn, eject turn, deploy trigger, setup-card flow.
- **E2E (gated, BYO key)**: scripted golden-transcript conversations against real models
  asserting build → valid `agent.yaml` → local deploy → `/health` 200 → invoke returns.
  One transcript set **per engine** (Claude, Codex) to catch behavioral divergence.
- **Sandbox safety**: egress blocked outside allowlist, CPU/mem/time caps enforced,
  teardown verified, secrets-not-on-disk asserted.
- **Eval metrics**: spec-validity rate, deploy-success rate, turns-to-spec,
  hallucinated-field rate.

## 12. Analytics & tracking (analytics-tracking lens)

- **Funnel taxonomy**: `builder_session_started` → `user_message_sent` →
  `stack_recommended` → `setup_card_shown/completed` (secret|mcp|provider) →
  `spec_validated` (valid|invalid) → `eject_to_code_started` → `coding_agent_turn` →
  `deploy_started/succeeded/failed` → `first_invoke`.
- **North-star**: time-to-first-deployed-agent. Activation = first successful deploy +
  first invoke.
- **Properties**: engine, framework, cloud, sandbox (local|cloud), turns, duration,
  tenant plan.
- **Wiring**: existing audit/cost infra + a frontend analytics hook; cloud meters turns +
  sandbox-minutes (ties to `QuotaCounter`).
- **Privacy**: never log secret values or PII-bearing prompts; hash user ids.

## 13. Phasing (epic, 4 waves)

| Wave | Scope | Independently shippable value |
|---|---|---|
| **W1** | Conversational front door + streaming interview + deploy-from-chat (`BuilderSession` MVP reusing `agent_chat_builder`, SSE). Replaces home empty-state. | Talk → spec → deploy in one thread |
| **W2** | Inline secrets / MCP / provider capture cards | No more leaving the thread to configure |
| **W3** | Eject-to-code: `CodingAgentEngine` (Claude + Codex) + `Sandbox` interface + `LocalSandbox` + artifact Code tab | Real code generation locally |
| **W4** | `CloudSandbox` (e2b-style) + cloud metering parity + analytics dashboard | Full Lovable parity in cloud |

Cross-cutting per wave: grow the model-e2e suite, add analytics events, and sync docs
(`website/content/docs/cli-reference|how-to|agent-yaml`), `agentbreeder-cloud`, and the
website per the CLAUDE.md cross-repo rules — in the same commit as the code.

## 14. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Server-side code execution security (biggest) | Strong sandbox isolation; deferred to W3/W4 behind an interface; egress allowlist, caps, auto-teardown |
| Cost/abuse (codegen + sandbox minutes) | Quota metering on turns + sandbox-minutes; per-user rate limits |
| BYO-key friction | Offer org-level keys in cloud; reuse existing secure key entry |
| Engine parity (Claude vs Codex divergence) | Golden transcripts per engine; shared `CodingAgentEngine` contract |
| Streaming latency through cloud proxy | SSE passthrough; measure; keepalive pings (existing pattern) |
| Two competing build flows | Phased evolution of existing surfaces, not a greenfield fork |

## 15. Out of scope (YAGNI for now)

- Visual/no-code canvas changes (existing `VisualBuilder` stays; tier mobility preserved).
- Orchestration (multi-agent) conversational builder — future epic.
- Marketplace template instantiation via chat — future.
- Non-Anthropic/OpenAI coding-agent engines.
