# `runtime/crewai@1.0.0`

**Status:** not in a published preview (#1090). **Not run end to end.**
Its runtime tests run real CrewAI 1.15.34 against a scripted gateway behind the kit's real model
proxy; nothing has been run against a real model, Docker, `kind` or `agentbreeder conformance`.
CrewAI is not a supported framework. What has and has not run is in
[the F6 record](../superpowers/verification/F6-crewai.md).

A runtime on AgentBreeder's **runtime kit** (#1083, multi-framework spec
`docs/superpowers/specs/2026-11-16-multi-framework-agents-design.md` §7 and §8.5). An agent picks
it with one line:

```yaml
runtime: runtime/crewai@1.0.0
```

Everything else in `agent.yaml` means what it means on `runtime/adk@1.3.0`. There are no
CrewAI-specific fields, and no Python to write: the runtime builds a CrewAI agent from the
`agent.config.json` that `agentbreeder build` generates. `examples/northwind-crewai` is Northwind
with only `name` and `runtime` changed.

## What it supports

| Part of `agent.yaml` | On this runtime |
|---|---|
| `model` (`alias`, `reasoning`, `max_model_calls`, `budget_usd`, `rpm`) | Yes, through the kit's model proxy (below) |
| `prompt` | Yes, as the agent's backstory; the customer's notes are added to it each turn |
| `description` | The agent's goal (its `name` is the role) |
| `tools` | Yes, up to 50, as on ADK (`fn(rt, **args)`, the declared JSON Schema; see the schema limits below) |
| `knowledge` | One entry: retrieval before the model call, plus `search_knowledge_base` |
| `memory` (`sessions`, `notes`) | Yes. History comes from the agent's `turns` table |
| `agent_events`, `traces` | Yes |
| `evals` | Yes (`release` reads the same reply fields) |
| `mcp`, `subagents`, `skills` | **No.** `build` refuses them (`MCP_RUNTIME_UNSUPPORTED`, `RUNTIME_NO_SUBAGENTS`, `RUNTIME_NO_SKILLS`) |

`search_knowledge_base` is reserved: a tool function may not use that name.

**Tool schemas.** CrewAI needs each tool's parameters as a pydantic model, so the runtime
generates one from the declared JSON Schema. It supports string, integer, number, boolean, arrays
of those, free-form objects, `enum`, `description`, `required` and `default`, which covers
Northwind's tools. It refuses at start-up (the agent's `/health` reports a failed start) a
schema with `$ref`, `anyOf`/`oneOf`/`allOf`/`not`, tuple `items`, a type list other than
`[T, "null"]`, a nested object with declared properties, or a property name a Python identifier
cannot carry. A tool name must already be lowercase letters, digits and underscores (CrewAI would
rename anything else). The model sees CrewAI's strict form of the schema (every property listed,
an optional one allowed to be `null`); a `null` for an optional argument is treated as "not
given", and the kit checks each call against the declared schema, as on every kit runtime.

## How it differs from `runtime/adk`

- **One CrewAI `Agent` per turn,** run with `Agent.kickoff_async` and the conversation as
  messages. No `Crew` or `Task` is built. The agent has memory, planning, reasoning, delegation,
  code execution, context-window summarising, "better compaction" (which writes a large tool
  result to `/tmp` and adds an undeclared tool to read it back), checkpoints, the tool-result
  cache and its own task retries off,
  and `max_iter` = `max_model_calls` + 2, so CrewAI makes no model call the agent did not ask for
  and the proxy's cap is reached first.
- **Models are reached through a loopback proxy.** The proxy runs inside the agent's process on
  `127.0.0.1` and holds the gateway key. CrewAI's `LLM` (CrewAI's native OpenAI client; CrewAI does
  not use LiteLLM) gets a random per-turn token through an interceptor instead. The proxy builds
  each upstream request from an allow-list of Chat Completions fields and forwards no inbound
  header; a request for another model alias, path or token is refused.
- **Retries, cost and model errors are 1.3.0's**, as on every kit runtime: at most 3 tries, a rate
  limit waited out only within 10 seconds per turn, the same codes, statuses and `Retry-After`.
  The OpenAI client's retries are off (`max_retries=0`). CrewAI also retries a throttled call
  itself, above the client (up to 3 attempts with 1–8 s backoff, and the proxy answers its
  refusals, `MODEL_STEP_LIMIT` included, with 429); the runtime switches that loop off for the turn
  through its own guard, and if it ran anyway the proxy would answer its extra attempts with the
  turn's stored status, without a gateway call.
- **The step cap does not trust the provider.** With `max_model_calls: N`, the Nth call goes with
  function calling off (`tool_choice: none`). Gemini ignores that (#1111), so if the Nth answer
  still calls a tool, the proxy relays none of it and the turn fails `MODEL_STEP_LIMIT` (HTTP
  502). CrewAI writes its own system message around the prompt, so the runtime tells the proxy
  that message, and the proxy still recognises CrewAI's own reasoning steps as the agent's
  (spec §7.2.4).
- **No customer text outside `turns`, by design** (checked in the runtime tests, not yet on the
  built image). CrewAI 1.15.34, even with memory off, writes every task's
  input and output to `latest_kickoff_task_outputs.db` under `HOME`, and its global event listener
  prints panels to stdout (the container's log) with the backstory, the customer's notes, each
  tool's arguments and output, and the answer, whatever `verbose` says. The runtime:
  - builds no `Crew`, so the task-output writer is never made, and also makes that writer keep
    nothing for any `Crew` in the process;
  - silences CrewAI's console formatter, its printer and `rich` console output (the kit never uses
    `rich`; its own log lines are one line, redacted);
  - checks both at start-up, with the retry guard, the compaction settings and that tracing and
    telemetry are off, and does not start if CrewAI changed underneath;
  - deletes the task-output file after every turn, and the file and CrewAI's credentials
    directory at start-up, if either exists. (`crewai/credentials/secret.key` is a random key
    CrewAI's own platform login uses to encrypt its token; it holds no AgentBreeder secret, and
    on this runtime's path it is not created.)

  The runtime tests run turns with a fresh `HOME` and temp directory and find no file holding the
  turn's text, and nothing of the turn on stdout or stderr. The same check against the built image
  is written (`tests/test_northwind_crewai_docker.py`) and has not been run.
- **Telemetry off before import:** `CREWAI_DISABLE_TELEMETRY`, `CREWAI_DISABLE_TRACKING` and
  `CREWAI_TRACING_ENABLED=false` (trace upload is a separate switch), and every other `CREWAI_*` and
  `OPENAI_*` variable is removed. `OTEL_SDK_DISABLED` is never set: CrewAI honours it too, and it
  would stop the kit's own spans. In the runtime tests a turn opened no socket but loopback.
- **Prompt placeholders.** CrewAI substitutes `{input}`, `{tools}` and similar in its system prompt
  after the backstory is in it, so a customer's note holding `{input}` would put the customer's
  message into the system prompt. Those placeholders are written with a full-width brace in the
  role, goal and backstory.
- **History** is the conversation's user and assistant text, given to CrewAI as messages (CrewAI
  prefixes the current message with "Current Task:"). ADK also replays earlier tool calls and
  knowledge blocks; on LangGraph the multi-turn golden cases passed without them (spec D5).
- **A tool call the agent did not declare**, or one whose arguments are not JSON, is answered to
  the model by CrewAI ("Tool not found", a parse error); nothing runs and the turn goes on.
- **Extra model calls CrewAI makes itself,** all counted by the proxy's cap: an empty answer is
  asked again once (if the second is empty too, the turn answers with the fallback text);
  after 5 identical tool calls in a row CrewAI forces a final answer; and at `max_iter` it forces
  one, which the cap reaches first.
- **`/health`** is 503 until the agent is built and after a failed start (protocol profile
  `kit`); a turn on a session that is still running one is 409 at once; on SIGTERM the turn in
  flight gets up to 8 seconds to finish.
- **Log lines** name a failed turn's exception type only, and a failed model call's code and
  status only.
- **No chat page** at `GET /`.
- **Traces:** the kit writes the root `agent_turn` span, `retrieve_knowledge` and one TOOL span
  per call; OpenInference's CrewAI instrumentor adds AGENT and CHAIN spans (its own TOOL spans are
  exported as CHAIN) and **no LLM spans**. Each model call is in the turn's `model_called` audit
  rows (alias, tokens, cost, latency).

## Packages

`crewai` 1.15.34, `openinference-instrumentation-crewai` 1.1.22, OpenTelemetry 1.45.2, `psycopg`
3.3.6, `h11` 0.16.0 and `httpx` 0.28.1: 144 packages, hash-locked for Linux amd64 and arm64 (both
resolve to the same pins; only the lock resolution has been checked, not an install on arm64).
`crewai` alone pulls `chromadb`, `lancedb`, `onnxruntime`, `kubernetes`, `mcp` and `uv`; only
CrewAI's memory, RAG and CLI code import most of them, which this runtime does not use, and they
stay in the lock so `pip-audit` covers them. F0 measured a 365 MB image. No `crewai-tools`. The
image is `python:3.12-slim` by digest, runs as uid 65532 with `HOME=/tmp` (CrewAI does not start
without a writable `HOME`), and carries the labels `org.agentbreeder.runtime` and
`org.agentbreeder.protocol`. `pip-audit` reports four advisories in `chromadb` 1.5.9, all in
ChromaDB's HTTP server, with no fixed version yet; the runtime imports `chromadb` as a library
(through `crewai`) and never starts its server. The image carries ChromaDB's server code unused.
The four advisories are a documented known security exception (`CLAUDE.md`), limited to those four advisories
in this lock and to be re-checked when chromadb ships a fix (details in the F6 record). `lancedb` and
`onnxruntime` have no macOS 13 x86_64 wheel, so the
runtime tests on such a Mac run without them; the Linux image installs the lock unchanged.
