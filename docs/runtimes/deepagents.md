# `runtime/deepagents@1.0.0`

**Status:** on `main` (#1083 F5), not in a published preview. Run end to end on macOS x86_64
(2026-11-20): Northwind passed its 61-case eval gate on local Docker and on `kind`, and
`agentbreeder conformance` gave 26 pass, 0 fail and 13 not run of 39. On real Gemini, the `task`
sub-agent's calls counted against the turn's cap, the file tools saw only the turn's state, and
the image's unused vendor SDKs could not reach out. arm64 has not been run, the kit-profile
conformance checks are not built, and Deep Agents is not a supported framework. What has and has
not run is in [the F5 record](../superpowers/verification/F5-deepagents.md).

Deep Agents on AgentBreeder's **runtime kit** (#1083, multi-framework spec
`docs/superpowers/specs/2026-11-16-multi-framework-agents-design.md` §7 and §8.3), the second kit
runtime after [`runtime/langgraph@1.0.0`](langgraph.md). An agent picks it with one line:

```yaml
runtime: runtime/deepagents@1.0.0
```

Everything else in `agent.yaml` means what it means on `runtime/adk@1.3.0`. There are no
Deep-Agents-specific fields, and no Python to write: the runtime builds the agent with
`create_deep_agent` from the `agent.config.json` that `agentbreeder build` generates.
`examples/northwind-deepagents` is Northwind with only `name` and `runtime` changed.

## What it supports

| Part of `agent.yaml` | On this runtime |
|---|---|
| `model` (`alias`, `reasoning`, `max_model_calls`, `budget_usd`, `rpm`) | Yes, through the kit's model proxy |
| `prompt` | Yes; the customer's notes are added to it each turn |
| `tools` | Yes, up to 50, as on ADK (`fn(rt, **args)`, the declared JSON Schema) |
| `knowledge` | One entry: retrieval before the model call, plus `search_knowledge_base` |
| `memory` (`sessions`, `notes`) | Yes. History comes from the agent's `turns` table |
| `agent_events`, `traces` | Yes |
| `evals` | Yes (`release` reads the same reply fields) |
| `mcp`, `subagents`, `skills` | **No.** `build` refuses them (`MCP_RUNTIME_UNSUPPORTED`, `RUNTIME_NO_SUBAGENTS`, `RUNTIME_NO_SKILLS`) |

**Reserved tool names:** `search_knowledge_base` and Deep Agents' built-ins, `ls`, `read_file`,
`write_file`, `edit_file`, `delete`, `glob`, `grep` and `task`. A tool function may not use one;
`build` refuses it.

## What it adds to `runtime/langgraph`

Everything [`runtime/langgraph@1.0.0`](langgraph.md#how-it-differs-from-runtimeadk) does
differently from ADK holds here, because both run on the same kit (byte for byte): the loopback
model proxy and its allow-list, 1.3.0's retries and codes, the step cap that does not trust the
provider (`MODEL_STEP_LIMIT`), history from `turns`, `/health` readiness, the SIGTERM drain and
the traces. On top of that:

- **Built-in file tools on in-memory state.** Deep Agents offers the model `ls`, `read_file`,
  `write_file`, `edit_file`, `delete`, `glob` and `grep`. The runtime passes Deep Agents' state
  backend explicitly, so these act on the turn's graph state only: they never touch the
  container's disk, a host path such as `/etc/passwd` is "not found", and a file written in one
  turn is gone in the next. There is no `execute` tool (the state backend has none) and no
  checkpointer, store, skills, memory files or interrupts.
- **The `task` sub-agent** (owner decision D10: kept). The model may delegate part of a turn to
  Deep Agents' general-purpose sub-agent. It uses the same model client, so every sub-agent call
  goes through the proxy with the turn's token and **counts against the turn's
  `max_model_calls`**. Its calls to the agent's tools go through the kit's wrapper like any other:
  one `tool:<name>` audit row, one TOOL span, the reply's `tools`, the customer the server set.
  The sub-agent is not given the agent's prompt or the customer's notes.
- **Main-loop and hidden calls.** Each `model_called` audit row says `main_loop: true` for the
  agent's own calls and `false` for the sub-agent's. The proxy tells them apart by the request
  (spec §7.2.4): the agent's system prompt and exactly its tool set, which the runtime registers
  for the turn from a middleware placed after Deep Agents' own prompt-building middleware. When the step cap is reached in a hidden
  call, the turn fails `MODEL_STEP_LIMIT` rather than answer with the sub-agent's text.
- **Built-in tools are not audited.** Calls to the file tools and to `task` itself get no
  `tool:` audit row and are not in the reply's `tools` (only the agent's own tools are; the
  sub-agent's model calls are in `model_calls` and the `model_called` rows).
- **Prompt size.** Deep Agents' tool descriptions add about 2.6k prompt tokens to every model
  call (F0's probe, against 176 for LangGraph). It adds no system-prompt text of its own in this
  set-up: the system message is the agent's prompt plus the customer's notes.
- **Recursion limit.** The main graph's is 2 × `max_model_calls` + 4, in place of Deep Agents'
  default of 9,999. The `task` sub-agent keeps Deep Agents' own 9,999 (it is invoked with its own
  config); every sub-agent step needs a model call, so the proxy's cap is its bound.

## Packages

`deepagents` 0.7.32 on runtime/langgraph's set (`langgraph` 1.2.19, `langchain` 1.4.8,
`langchain-core` 1.7.4, `langchain-openai` 1.7.3, `openinference-instrumentation-langchain`
0.1.81, OpenTelemetry 1.45.2, `psycopg` 3.3.6, `h11` 0.16.0, `httpx` 0.28.1): 76 packages,
hash-locked for Linux amd64 and arm64 (both resolve to the same pins; only the lock resolution
has been checked, not an install on arm64). `deepagents` also brings `langchain-anthropic`,
`langchain-google-genai`, `anthropic` and `google-genai`, which the runtime never uses: it hands
Deep Agents a `ChatOpenAI` pointed at the kit's proxy (test-backed). Kit deployments are meant
to have default-deny egress (F1b), but that has not been run on a real engine for this runtime. No `langchain-community` and no `allow_dangerous_*` tools. The image is
`python:3.12-slim` by digest, runs as uid 65532 and carries the labels `org.agentbreeder.runtime`
and `org.agentbreeder.protocol`.

Because the engine refuses a tool module whose name shadows a package any built-in runtime
installs, the names these packages bring (`anthropic`, `deepagents`, `wcmatch`, `filetype` and
others in `runtime/modules.json`) are now refused as tool module names on every runtime.
