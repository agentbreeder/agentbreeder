# `runtime/openai-agents@1.0.0`

**Status:** on `main` once merged, not in a published preview. **Not run end to end.** Its own
tests run the real OpenAI Agents SDK against a scripted gateway behind the kit's real model
proxy (CI job `runtime-openai-agents`). It has not been built as an image, run on Docker, run
against a real gateway or model, run through `release` or on `kind`, or installed on arm64, and
the OpenAI Agents SDK is not a supported framework. What has and has not run is in
[the F4 record](../superpowers/verification/F4-openai-agents.md).

The second runtime on AgentBreeder's **runtime kit** (#1083, multi-framework spec
`docs/superpowers/specs/2026-11-16-multi-framework-agents-design.md` §7 and §8.1), after
[LangGraph](langgraph.md). An agent picks it with one line:

```yaml
runtime: runtime/openai-agents@1.0.0
```

Everything else in `agent.yaml` means what it means on `runtime/adk@1.3.0`. There are no
SDK-specific fields, and no Python to write: the runtime builds an OpenAI Agents SDK agent from the
`agent.config.json` that `agentbreeder build` generates. `examples/northwind-openai-agents` is
Northwind with only `name` and `runtime` changed.

## What it supports

The same as `runtime/langgraph@1.0.0` (the kit decides it, not the framework):

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

`search_knowledge_base` is reserved: a tool function may not use that name. A tool module may not
be named `agents` or `mcp` (the SDK installs both), or after any other package in the lock.

## How it differs from `runtime/adk`

The kit's differences are LangGraph's: the loopback model proxy, 1.3.0's retries, cost and
coded errors, the step cap that does not trust the provider (`MODEL_STEP_LIMIT`, 502), history
as user and assistant text, `/health` 503 until built, and no chat page. See
[runtimes/langgraph.md](langgraph.md#how-it-differs-from-runtimeadk). What is particular to this
runtime:

- **Chat Completions, not the Responses API.** The model is `OpenAIChatCompletionsModel` on an
  `AsyncOpenAI` client at the proxy, with the client's own retries off; the proxy serves only
  `/v1/chat/completions`.
- **Nothing goes to OpenAI** (by construction, and checked in a venv with a scripted model; not
  yet checked in a container or on the deploy network). The entry point removes every `OPENAI_*` environment variable before
  the SDK is imported, so no key, base URL or tracing switch from the environment applies. The SDK
  keeps tracing on (switching it off also stops the OpenInference spans), and OpenInference's
  instrumentor is installed with `exclusive_processor=True`, which replaces the SDK's own exporter
  to OpenAI's trace endpoint.
- **Only the agent's tools.** One `FunctionTool` per declared function, with its JSON Schema and
  `strict_json_schema=False` (strict mode would make every parameter required). None of the SDK's
  built-in tools (`ShellTool`, `LocalShellTool`, `ComputerTool`, `CodeInterpreterTool`,
  `ApplyPatchTool`, `FileSearchTool`, `HostedMCPTool` and the rest), no handoffs, no MCP servers,
  and `RunConfig.sandbox` is never set.
- **Tool arguments that are not a JSON object** reach the kit as no arguments, so its schema check
  answers the model and records an `ok: false` row.
- **A call to a tool the agent lacks** runs nothing and is answered to the model
  (`tool_not_found_behavior="return_error_to_model"`), as LangGraph's tool node does; the SDK's
  default would fail the turn.
- **No SDK session.** History is given as input items; `max_turns` is `max_model_calls + 2`, a
  backstop the proxy's cap reaches first.
- **Traces:** the kit writes the root `agent_turn` span, `retrieve_knowledge` and one TOOL span per
  call; OpenInference's OpenAI Agents instrumentor adds LLM, AGENT and CHAIN spans (its own TOOL
  spans are exported as CHAIN).

## Packages

`openai-agents` 0.24.0, `openai` 3.42.1, `openinference-instrumentation-openai-agents` 2.7.0,
OpenTelemetry 1.45.2, `psycopg` 3.3.6, `h11` 0.16.0 and `httpx` 0.28.1: 58 packages, hash-locked
for Linux amd64 and arm64 (both resolve to the same pins; only the lock resolution has been
checked, not an install on Linux or arm64). The SDK depends on `mcp` 2.3.0, which brings
`starlette`, `uvicorn`, `cryptography` and `pyjwt` into the image; the runtime never starts an MCP
client or server. The image is `python:3.12-slim` by digest, runs as uid 65532 and carries the
labels `org.agentbreeder.runtime` and `org.agentbreeder.protocol`.
