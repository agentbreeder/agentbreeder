# `runtime/langgraph@1.0.0`

**Status:** on `main`, not in a published preview. Run end to end on macOS x86_64 (2026-11-19):
Northwind passed its 61-case eval gate on local Docker and on `kind`, and
`agentbreeder conformance` gave 26 pass, 0 fail and 13 not run of 39. arm64 has not been run, the
kit-profile conformance checks are not built, and LangGraph is not a supported framework. What has and has not run is in
[the F3 record](../superpowers/verification/F3-langgraph.md).

The first runtime on AgentBreeder's **runtime kit** (#1083, multi-framework spec
`docs/superpowers/specs/2026-11-16-multi-framework-agents-design.md` §7 and §8.2). An agent picks
it with one line:

```yaml
runtime: runtime/langgraph@1.0.0
```

Everything else in `agent.yaml` means what it means on `runtime/adk@1.3.0`. There are no
LangGraph-specific fields, and no Python to write: the runtime builds a LangGraph agent from the
`agent.config.json` that `agentbreeder build` generates. `examples/northwind-langgraph` is
Northwind with only `name` and `runtime` changed.

## What it supports

| Part of `agent.yaml` | On this runtime |
|---|---|
| `model` (`alias`, `reasoning`, `max_model_calls`, `budget_usd`, `rpm`) | Yes, through the kit's model proxy (below) |
| `prompt` | Yes; the customer's notes are added to it each turn |
| `tools` | Yes, up to 50, as on ADK (`fn(rt, **args)`, the declared JSON Schema) |
| `knowledge` | One entry: retrieval before the model call, plus `search_knowledge_base` |
| `memory` (`sessions`, `notes`) | Yes. History comes from the agent's `turns` table |
| `agent_events`, `traces` | Yes |
| `evals` | Yes (`release` reads the same reply fields) |
| `mcp`, `subagents`, `skills` | **No.** `build` refuses them (`MCP_RUNTIME_UNSUPPORTED`, `RUNTIME_NO_SUBAGENTS`, `RUNTIME_NO_SKILLS`) |

`search_knowledge_base` is reserved: a tool function may not use that name.

## How it differs from `runtime/adk`

- **Models are reached through a loopback proxy.** The proxy runs inside the agent's process on
  `127.0.0.1` and holds the gateway key; LangGraph's client gets a random per-turn token instead.
  The proxy builds each upstream request from an allow-list of Chat Completions fields, so
  guardrail switches, `api_base`, `metadata`, `mock_response` and every other LiteLLM control
  field are dropped, and it forwards no inbound header. A request for another model alias, path
  or token is refused.
- **Retries, cost and model errors are 1.3.0's**, reimplemented over the gateway's HTTP answers
  and checked against 1.3.0's own functions (`tests/test_runtime_kit_parity.py`): at most 3 tries,
  a rate limit waited out only within 10 seconds per turn, the same codes, statuses and
  `Retry-After`. LangChain's own retries are off. The log line for a failed model call names the
  code and status only; the gateway's text (redacted) goes to the turn record.
- **The step cap does not trust the provider.** With `max_model_calls: N`, the Nth call goes with
  function calling off (`tool_choice: none`). Gemini ignores that (#1111), so if the Nth answer
  still calls a tool, the proxy relays none of it and the turn fails `MODEL_STEP_LIMIT` (HTTP
  502). A further call is refused without reaching the gateway.
- **History** is the conversation's user and assistant text, given to LangGraph as messages. ADK
  also replays earlier tool calls and knowledge blocks. 7 of Northwind's 61 golden cases have more
  than one turn; if they fail because of this, a later kit version adds the previous turn's tool
  results (spec D5).
- **`/health`** is 503 until the agent is built and after a failed start (protocol profile
  `kit`); a turn on a session that is still running one is 409 at once; on SIGTERM the turn in
  flight gets up to 8 seconds to finish.
- **`memory_recalled`** is written when the conversation has no earlier answered turn in
  `turns`; after a first turn that failed it is written again (ADK keyed it on the session
  having no events at all).
- **Log lines** name a failed turn's exception type only, and a failed model call's code and
  status only; ADK logged the redacted text.
- **A final answer that still asks for a tool**, or whose tool call LangChain could not parse,
  is not used as the reply; the turn answers with the fallback text.
- **No chat page** at `GET /`.
- **Traces:** the kit writes the root `agent_turn` span, `retrieve_knowledge` and one TOOL span
  per call; OpenInference's LangChain instrumentor adds LLM and CHAIN spans (its own TOOL spans are
  exported as CHAIN). LangSmith's environment variables are removed before LangChain is imported.

## Packages

`langgraph` 1.2.19, `langchain` 1.4.8, `langchain-core` 1.7.4, `langchain-openai` 1.7.3,
`openinference-instrumentation-langchain` 0.1.81, OpenTelemetry 1.45.2, `psycopg` 3.3.6, `h11`
0.16.0 and `httpx` 0.28.1: 61 packages, hash-locked for Linux amd64 and arm64 (both resolve to the
same pins; only the lock resolution has been checked, not an install on arm64). No
`langchain-community` and no `allow_dangerous_*` tools. The image is `python:3.12-slim` by
digest, runs as uid 65532 and carries the labels `org.agentbreeder.runtime` and
`org.agentbreeder.protocol`.
