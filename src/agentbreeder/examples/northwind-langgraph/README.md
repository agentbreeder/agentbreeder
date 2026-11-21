# Northwind support agent on LangGraph

**Status: run end to end on macOS x86_64 (Docker Desktop and `kind`), not verified.** This is
Northwind (`../northwind`) on `runtime/langgraph@1.0.0`, the first runtime on AgentBreeder's
runtime kit (#1083 F3). On 2026-11-19 `release` passed Northwind's 61-case eval gate locally, and
`promote` passed it again on `kind`. `agentbreeder conformance` gave 26 pass, 0 fail and 13 not run
of 39 checks (the kit-profile checks are not built yet). arm64 has not been run. LangGraph is
not a supported framework yet: that is an owner decision. The record is
`docs/superpowers/verification/F3-langgraph.md`.

`agent.yaml` is Northwind's file with two lines changed, `name` and `runtime`. The prompt, model
alias, tools, knowledge, memory and eval set are Northwind's, from Northwind's registry, and
`build` gives the agent the same `agent.config.json` (as JSON, apart from the name) and the same
artifacts, byte for byte (`tests/test_northwind_langgraph.py`).

| What | Where |
|---|---|
| Agent definition | `agent.yaml`, `agent.lock` (its registry entries say `source: ../northwind/registry`) |
| Prompt, knowledge, tools, eval set | `../northwind/registry/` |
| Runtime | `runtime/langgraph@1.0.0`, shipped with AgentBreeder: LangGraph's `create_agent`, with the runtime kit's server, turn, tools, memory and loopback model proxy |
| Backing services | Northwind's `stack.yaml`, which also names this agent's identity |

## What the runtime does differently from ADK

- Models are reached only through the kit's **loopback model proxy**, inside the agent's process.
  It holds the gateway key; LangGraph gets a per-turn token. The proxy rebuilds every request from
  an allow-list of fields, so the agent cannot switch off gateway guardrails or redirect a call,
  and owns retries, cost, coded model errors and the model alias.
- **The step cap does not trust the provider.** The last allowed model call goes with function
  calling off, but Gemini ignores that (#1111). If the answer still calls a tool, the proxy relays
  none of it and the turn fails `MODEL_STEP_LIMIT` (502).
- **History** is the conversation's user and assistant text from the agent's `turns` table, given
  to LangGraph as messages. ADK also replayed earlier tool calls and knowledge blocks; the
  multi-turn golden cases passed without them (spec §7.5, D5). The agent role reads six columns
  of `turns` for this (Northwind's `commerce.sql`).
- `/health` answers 503 until the agent is built.
- No `mcp:` servers, sub-agents or skills in this version.

## Run it (from a checkout)

You need Docker and the keys Northwind needs (`GEMINI_API_KEY`, `TAVILY_API_KEY`).

```
pip install -e ".[eval]"                       # at the repo root
cd src/agentbreeder/examples/northwind
agentbreeder services up --environment dev --env-file ~/.env
cd ../northwind-langgraph
export AGENTBREEDER_REGISTRY_PATH=../northwind/registry   # Northwind's prompt, tools, knowledge
agentbreeder release agent.yaml --stack ../northwind --environment dev
```

`release` builds the image, deploys a candidate, runs Northwind's 61 evals against it and promotes
it only if all pass. The stack names both agents, so they are meant to run side by side (sharing
the `support` database's tables); running both at once has not been tried. A Northwind stack
started before the `turns` grant needs `services up --update`.

`agentbreeder example` does not list this directory: it needs Northwind beside it, so it is run
from a checkout.

## The acceptance run

`tests/test_northwind_langgraph_docker.py` runs the spec's acceptance steps against a real stack
when `AGENTBREEDER_DOCKER_TEST=1` and `AGENTBREEDER_NORTHWIND_LANGGRAPH=1` are set:

```
AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_LANGGRAPH=1 \
  python -m pytest -q tests/test_northwind_langgraph_docker.py
```

It uses the Northwind directory you ran `services up` in (this checkout's, or the one named by
`AGENTBREEDER_NORTHWIND_DIR`).

It needs the Northwind stack up with real keys, and `AGENTBREEDER_KIND_TEST=1` with
`agentbreeder cluster up` done for the `kind` test. It passed on 2026-11-19 (macOS x86_64); see
`docs/superpowers/verification/F3-langgraph.md`.
