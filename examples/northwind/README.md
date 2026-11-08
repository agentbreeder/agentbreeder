# Northwind support agent, built from `agent.yaml`

A customer support agent for a fictional company, made from an `agent.yaml` plus registry entries
and **no agent Python**. It has:

- **knowledge search:** pgvector and BM25 over help articles;
- **memory:** Postgres sessions and per-customer notes;
- **order lookup**;
- **cancellation requests** that staff approve;
- **web search**, through Tavily;
- **escalation** to a human.

Verified on macOS with Docker Desktop: `build` → `deploy` → the agent answers.

| What | Where | Whose code |
|---|---|---|
| Agent definition | `agent.yaml`, `agent.lock` | yours (about 35 lines of YAML) |
| Prompt, knowledge, tools, eval set | `registry/<kind>/<name>/1.0.0/` | registry entries. The tools are small Python packages: that is where custom logic lives |
| Runtime (ADK, memory, RAG, server) | `runtime/adk@1.0.2`, shipped with AgentBreeder | AgentBreeder |
| Supporting stack (Postgres, LiteLLM, Phoenix, key proxy, ops dashboard) | `stack/` | example infrastructure, not AgentBreeder product |

## Run it

You need Docker, a source checkout of AgentBreeder, and `GEMINI_API_KEY` and `TAVILY_API_KEY` in
the environment or in `~/.env`. `OPENROUTER_API_KEY` is optional; it enables the gateway's
fallback route.

```
python3 -m venv .venv && .venv/bin/pip install -e .      # at the repo root; there is no PyPI release
export PATH=$PWD/.venv/bin:$PATH
examples/northwind/stack/up.sh        # starts the stack; writes .agentbreeder/ (operator config, identities, secret files)
export AGENTBREEDER_OPERATOR_CONFIG=$PWD/examples/northwind/.agentbreeder/operator.yaml
export AGENTBREEDER_STATE_DIR=$PWD/examples/northwind/.agentbreeder/state
cd examples/northwind
pip install -e ".[eval]"             # at the repo root; the eval gate resets fixtures in Postgres
agentbreeder release agent.yaml --environment dev   # build → candidate → 61 evals → promote
```

Or step by step: `agentbreeder build agent.yaml --environment dev`, then
`agentbreeder deploy deploy/northwind-support.deploy.yaml --local --environment dev`.

**Models and cost.** These were measured from LiteLLM's spend logs on 2026-11-07. The agent and
the eval judge both use `gemini-3.8-flash`: a full release (candidate, 61 cases, judge, retries)
costs about $0.24 and passed 61/61. The previous setup, with a `gemini-3.1-pro-preview` judge,
cost about $0.44 per release. `gemini-3.5-flash-lite` for the agent failed the gate (50/61): its
answers left out required details. Change models in `stack/litellm/config.yaml`.

The deploy prints the chat endpoint, for example `http://127.0.0.1:55448`. Open it in a browser,
or call it directly:

```
curl -s <endpoint>/invoke -H 'content-type: application/json' \
  -d '{"message":"Where is my order NW-10021?","customer_id":"cust-001"}'
```

The seed customers are listed in `stack/db/commerce.sql`. Staff approve or reject cancellation
requests in the ops dashboard at http://127.0.0.1:8790. Traces are in Phoenix at
http://127.0.0.1:6006: log in as `admin@localhost` with the initial password from
`stack/.secrets/phoenix.env`.

## Honest limits (preview)

- **Customer scoping is tool code.** It holds in the shipped tools, but it is not an engine
  boundary, and `customer_id` in `/invoke` is not authenticated.
- **Gateway image.** The stack's LiteLLM gateway image is pinned at v1.106.2, which still bundles
  filelock 3.x (local-user advisories). The agent runtime itself uses litellm 1.108.3 with
  filelock 4.x.
- **Ports.** The stack publishes 127.0.0.1 ports 5433, 4000, 6006, 8787 and 8790. Stop anything
  else that uses them first.
