# Northwind support agent on the OpenAI Agents SDK

**Status: built and unit-tested with a scripted model; not run end to end, not verified.** This is
Northwind (`../northwind`) on `runtime/openai-agents@1.0.0`, the second runtime on AgentBreeder's
runtime kit (#1083 F4, after LangGraph). Its own tests run the real SDK against a scripted
gateway behind the kit's real model proxy. Nothing has run it on Docker, against the real
gateway and model, through `release`, or on `kind`, and arm64 has not been run. The OpenAI Agents
SDK is not a supported framework. The record of what ran is
`docs/superpowers/verification/F4-openai-agents.md`.

`agent.yaml` is Northwind's file with two lines changed, `name` and `runtime`. The prompt, model
alias, tools, knowledge, memory and eval set are Northwind's, from Northwind's registry, and
`build` gives the agent the same `agent.config.json` (as JSON, apart from the name) and the same
artifacts, byte for byte (`tests/test_northwind_openai_agents.py`).

| What | Where |
|---|---|
| Agent definition | `agent.yaml`, `agent.lock` (its registry entries say `source: ../northwind/registry`) |
| Prompt, knowledge, tools, eval set | `../northwind/registry/` |
| Runtime | `runtime/openai-agents@1.0.0`, shipped with AgentBreeder: the SDK's `Agent` and `Runner` over Chat Completions, with the runtime kit's server, turn, tools, memory and loopback model proxy |
| Backing services | Northwind's `stack.yaml`, which also names this agent's identity |

## What the runtime does differently from ADK

- Models are reached only through the kit's **loopback model proxy**, inside the agent's process.
  It holds the gateway key; the SDK's OpenAI client gets a per-turn token, with its own retries
  off. The proxy rebuilds every request from an allow-list of fields, and owns retries, cost,
  coded model errors and the model alias. The SDK uses Chat Completions, not the Responses API.
- **The step cap does not trust the provider.** The last allowed model call goes with function
  calling off, but Gemini ignores that (#1111). If the answer still calls a tool, the proxy relays
  none of it and the turn fails `MODEL_STEP_LIMIT` (502).
- **Nothing goes to OpenAI.** The entry point removes every `OPENAI_*` environment variable, and
  the OpenInference instrumentor replaces the SDK's own trace exporter, so traces go only to the
  stack's collector.
- **Only the agent's tools.** The agent gets one tool per declared function, and none of the SDK's
  built-in tools (shell, computer, code interpreter, hosted MCP), handoffs or MCP servers.
- **History** is the conversation's user and assistant text from the agent's `turns` table, given
  to the SDK as input items, not an SDK session. The agent role reads six columns of `turns` for
  this (Northwind's `commerce.sql`).
- `/health` answers 503 until the agent is built.
- No `mcp:` servers, sub-agents or skills in this version.

## Run it (from a checkout)

You need Docker and the keys Northwind needs (`GEMINI_API_KEY`, `TAVILY_API_KEY`).

```
pip install -e ".[eval]"                       # at the repo root
cd src/agentbreeder/examples/northwind
agentbreeder services up --environment dev --env-file ~/.env
cd ../northwind-openai-agents
export AGENTBREEDER_REGISTRY_PATH=../northwind/registry   # Northwind's prompt, tools, knowledge
agentbreeder release agent.yaml --stack ../northwind --environment dev
```

`release` builds the image, deploys a candidate, runs Northwind's 61 evals against it and promotes
it only if all pass. This has not been run for this port yet. The stack names every Northwind
port, so they are meant to run side by side (sharing the `support` database's tables); that has
not been tried.

`agentbreeder example` does not list this directory: it needs Northwind beside it, so it is run
from a checkout.

## The acceptance run

`tests/test_northwind_openai_agents_docker.py` runs the spec's acceptance steps against a real
stack when `AGENTBREEDER_DOCKER_TEST=1` and `AGENTBREEDER_NORTHWIND_OPENAI_AGENTS=1` are set:

```
AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_OPENAI_AGENTS=1 \
  python -m pytest -q tests/test_northwind_openai_agents_docker.py
```

It uses the Northwind directory you ran `services up` in (this checkout's, or the one named by
`AGENTBREEDER_NORTHWIND_DIR`). It needs the Northwind stack up with real keys, and
`AGENTBREEDER_KIND_TEST=1` with `agentbreeder cluster up` done for the `kind` test. **It has not
been run.**
