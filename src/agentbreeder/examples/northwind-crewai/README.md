# Northwind support agent on CrewAI

**Status: tested with a scripted model only. Not run end to end, not verified.** This is
Northwind (`../northwind`) on `runtime/crewai@1.0.0`, a runtime on AgentBreeder's runtime kit
(#1090, #1083 F6). Its runtime tests run real CrewAI 1.15.34 against a scripted gateway behind
the kit's real model proxy. Nothing has been run against a real model, Docker or `kind` yet:
not `release` with Northwind's 61 evals, not `agentbreeder conformance`. CrewAI is not a
supported framework. The record is `docs/superpowers/verification/F6-crewai.md`.

`agent.yaml` is Northwind's file with two lines changed, `name` and `runtime`. The prompt, model
alias, tools, knowledge, memory and eval set are Northwind's, from Northwind's registry, and
`build` gives the agent the same `agent.config.json` (as JSON, apart from the name) and the same
artifacts, byte for byte (`tests/test_northwind_crewai.py`).

| What | Where |
|---|---|
| Agent definition | `agent.yaml`, `agent.lock` (its registry entries say `source: ../northwind/registry`) |
| Prompt, knowledge, tools, eval set | `../northwind/registry/` |
| Runtime | `runtime/crewai@1.0.0`, shipped with AgentBreeder: one CrewAI `Agent` per turn, with the runtime kit's server, turn, tools, memory and loopback model proxy |
| Backing services | Northwind's `stack.yaml`, which also names this agent's identity |

## What the runtime does differently from ADK

- Models are reached only through the kit's **loopback model proxy**, inside the agent's process.
  It holds the gateway key; CrewAI gets a per-turn token. The proxy rebuilds every request from
  an allow-list of fields, and owns retries, cost, coded model errors, the model alias and the
  step cap, which does not trust the provider to honour `tool_choice: none` (#1111).
- **One CrewAI `Agent` per turn**, run with `Agent.kickoff_async`: role from the agent's name,
  goal from its description, backstory the prompt and the customer's notes. No `Crew` is built.
  Memory, planning, reasoning, delegation, code execution, context summarising, the tool cache
  and CrewAI's own rate-limit retries are off.
- **No customer text outside `turns`, by design** (checked in the runtime tests, not yet on the
  image). CrewAI writes every task's input and output to a SQLite
  file under `HOME`, and prints panels with the backstory, tool arguments and answers to stdout,
  whatever `verbose` says. The runtime switches both off, checks it at start-up, and deletes the
  task-output file after every turn and at start-up if it ever appears.
- **History** is the conversation's user and assistant text from the agent's `turns` table, given
  to CrewAI as messages, as on LangGraph. The agent role reads six columns of `turns` for this
  (Northwind's `commerce.sql`).
- `/health` answers 503 until the agent is built.
- No `mcp:` servers, sub-agents or skills in this version.

## Run it (from a checkout)

You need Docker and the keys Northwind needs (`GEMINI_API_KEY`, `TAVILY_API_KEY`).

```
pip install -e ".[eval]"                       # at the repo root
cd src/agentbreeder/examples/northwind
agentbreeder services up --environment dev --env-file ~/.env
cd ../northwind-crewai
export AGENTBREEDER_REGISTRY_PATH=../northwind/registry   # Northwind's prompt, tools, knowledge
agentbreeder release agent.yaml --stack ../northwind --environment dev
```

`release` builds the image, deploys a candidate, runs Northwind's 61 evals against it and promotes
it only if all pass. This has not been run for this port yet. The image is large (F0 measured
365 MB for CrewAI's packages): CrewAI pulls `chromadb`, `lancedb` and `onnxruntime`, which only
its memory and RAG storage import.

`agentbreeder example` does not list this directory: it needs Northwind beside it, so it is run
from a checkout.

## The acceptance run (written, not run)

`tests/test_northwind_crewai_docker.py` runs the spec's acceptance steps against a real stack
when `AGENTBREEDER_DOCKER_TEST=1` and `AGENTBREEDER_NORTHWIND_CREWAI=1` are set:

```
AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_CREWAI=1 \
  python -m pytest -q tests/test_northwind_crewai_docker.py
```

It uses the Northwind directory you ran `services up` in (this checkout's, or the one named by
`AGENTBREEDER_NORTHWIND_DIR`). It needs the Northwind stack up with real keys, and
`AGENTBREEDER_KIND_TEST=1` with `agentbreeder cluster up` done for the `kind` test.
