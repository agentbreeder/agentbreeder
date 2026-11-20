# Northwind support agent on Deep Agents

**Status: run end to end on macOS x86_64 (2026-11-20), not verified.** This is Northwind
(`../northwind`) on `runtime/deepagents@1.0.0`, Deep Agents on AgentBreeder's runtime kit (#1083
F5). With real Gemini it passed the 61-case eval gate on local Docker (twice) and on `kind`, and
conformance gave 26 pass, 0 fail and 13 not run. arm64 has not been run. Deep Agents is not a
supported framework.

`agent.yaml` is Northwind's file with two lines changed, `name` and `runtime`. The prompt, model
alias, tools, knowledge, memory and eval set are Northwind's, from Northwind's registry, and
`build` gives the agent the same `agent.config.json` (as JSON, apart from the name) and the same
artifacts, byte for byte (`tests/test_northwind_deepagents.py`).

| What | Where |
|---|---|
| Agent definition | `agent.yaml`, `agent.lock` (its registry entries say `source: ../northwind/registry`) |
| Prompt, knowledge, tools, eval set | `../northwind/registry/` |
| Runtime | `runtime/deepagents@1.0.0`, shipped with AgentBreeder: Deep Agents' `create_deep_agent`, with the runtime kit's server, turn, tools, memory and loopback model proxy |
| Backing services | Northwind's `stack.yaml`, which also names this agent's identity |

## What the runtime does differently from ADK and LangGraph

- Everything `runtime/langgraph@1.0.0` does differently from ADK holds here: models only through
  the kit's **loopback model proxy** (it holds the gateway key, rebuilds every request from an
  allow-list of fields, and owns retries, cost, coded model errors and the model alias); the
  **step cap does not trust the provider** (`MODEL_STEP_LIMIT`); **history** is the
  conversation's user and assistant text from the agent's `turns` table (the agent role reads six
  of its columns, Northwind's `commerce.sql`); `/health` is 503 until the agent is built.
- **Deep Agents' built-in tools.** The model is also offered `ls`, `read_file`, `write_file`,
  `edit_file`, `delete`, `glob`, `grep` and `task`. The file tools act on the turn's in-memory
  graph state only (the state backend): they never read or write a disk, there is no `execute`
  tool, and a file is gone when the turn ends. These names are reserved, so no agent tool may use
  one. The built-in tools are not the agent's tools: they get no audit row and are not in the
  reply's `tools`.
- **The `task` sub-agent** (owner decision D10: kept). The model may hand part of a turn to Deep
  Agents' general-purpose sub-agent. Its model calls go through the same proxy and **count against
  the same `max_model_calls`**, and its calls to Northwind's tools are audited as the agent's,
  with the customer the server set. The sub-agent does not see the agent's prompt or the
  customer's notes. If the step cap is reached inside the sub-agent, the turn fails
  `MODEL_STEP_LIMIT`: the sub-agent's text is not an answer to the customer.
- **Cost.** Deep Agents' tool descriptions add about 2.6k prompt tokens to every model call (F0's
  probe; 176 for LangGraph). Expect a higher `cost_usd` per turn than on ADK or LangGraph.
- No `mcp:` servers, sub-agents (`subagents:` in `agent.yaml`) or skills in this version.

## Run it (from a checkout)

You need Docker and the keys Northwind needs (`GEMINI_API_KEY`, `TAVILY_API_KEY`).

```
pip install -e ".[eval]"                       # at the repo root
cd src/agentbreeder/examples/northwind
agentbreeder services up --environment dev --env-file ~/.env
cd ../northwind-deepagents
export AGENTBREEDER_REGISTRY_PATH=../northwind/registry   # Northwind's prompt, tools, knowledge
agentbreeder release agent.yaml --stack ../northwind --environment dev
```

`release` builds the image, deploys a candidate, runs Northwind's 61 evals against it and promotes
it only if all pass. This has not been run for this port. The stack names every port, so they are
meant to run side by side (sharing the `support` database's tables); that has not been tried.

`agentbreeder example` does not list this directory: it needs Northwind beside it, so it is run
from a checkout.

## The acceptance run

`tests/test_northwind_deepagents_docker.py` runs the spec's acceptance steps against a real stack
when `AGENTBREEDER_DOCKER_TEST=1` and `AGENTBREEDER_NORTHWIND_DEEPAGENTS=1` are set:

```
AGENTBREEDER_DOCKER_TEST=1 AGENTBREEDER_NORTHWIND_DEEPAGENTS=1 \
  python -m pytest -q tests/test_northwind_deepagents_docker.py
```

It uses the Northwind directory you ran `services up` in (this checkout's, or the one named by
`AGENTBREEDER_NORTHWIND_DIR`). It needs the Northwind stack up with real keys, and
`AGENTBREEDER_KIND_TEST=1` with `agentbreeder cluster up` done for the `kind` test. It passed on
2026-11-20 (macOS x86_64). On Gemini with Northwind's prompt, the model declines the file-tool
requests and does not choose `task`, so those two tests show only that nothing leaks and that
the cap holds; the tools themselves were checked with a probe deployment. The record is
`docs/superpowers/verification/F5-deepagents.md`.
