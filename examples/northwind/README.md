# Northwind support agent, built from `agent.yaml`

A customer support agent for a fictional company, made from an `agent.yaml` plus registry entries
and **no agent Python**. It has:

- **knowledge search:** pgvector and BM25 over help articles;
- **memory:** Postgres sessions and per-customer notes;
- **order lookup**;
- **cancellation requests** that staff approve;
- **web search**, through Tavily;
- **escalation** to a human.

Verified on macOS with Docker Desktop: `agentbreeder services up` started the stack from
`stack.yaml`, and `agentbreeder release` passed the 61-case eval gate (61/61, 2026-11-08) and
deployed the agent.

| What | Where | Whose code |
|---|---|---|
| Agent definition | `agent.yaml`, `agent.lock` | yours (about 35 lines of YAML) |
| Prompt, knowledge, tools, eval set | `registry/<kind>/<name>/<version>/` | registry entries. The tools are small Python packages: that is where custom logic lives |
| Runtime (ADK, memory, RAG, server) | `runtime/adk@1.0.5`, shipped with AgentBreeder | AgentBreeder |
| Backing services | `stack.yaml`, `stack.lock` | built-in `postgres-pgvector`, `litellm` and `phoenix`, plus this example's `northwind-proxy` and `northwind-ops` (`registry/service/`, built from source) |
| Seed data and gateway models | `stack/db/commerce.sql`, `stack/litellm/config.yaml` | example data |

## Run it

You need Docker, a source checkout of AgentBreeder, and `GEMINI_API_KEY` and `TAVILY_API_KEY` in
the environment or in an env file (`--env-file ~/.env`). `OPENROUTER_API_KEY` is optional; it
enables the gateway's fallback route.

```
python3 -m venv .venv && .venv/bin/pip install -e ".[eval]"   # at the repo root; there is no PyPI release. [eval] lets the eval gate reset fixtures in Postgres
export PATH=$PWD/.venv/bin:$PATH
cd examples/northwind
agentbreeder services up --environment dev --env-file ~/.env   # shows what it will run and grant; --yes confirms
agentbreeder release agent.yaml --stack . --environment dev    # build → candidate → 61 evals → promote
```

`services up` generates every service secret, keeps all state under
`~/.agentbreeder/stacks/northwind-<id>/`, and writes the operator config and demo identities for
`dev` there. Nothing is written into this directory except `stack.lock`. The agent joins the
stack's agents network: it reaches Postgres, the gateway and the key proxy, but not Phoenix or the
ops dashboard. `agentbreeder services status` lists each service's `127.0.0.1` port;
`agentbreeder services down` stops the stack and keeps its data.

Or step by step: `agentbreeder build agent.yaml --stack . --environment dev`, then
`agentbreeder deploy deploy/northwind-support.deploy.yaml --local --stack . --environment dev`.

**Models and cost.** These were measured from LiteLLM's spend logs on 2026-11-07. The agent and
the eval judge both use `gemini-3.8-flash`: a full release (candidate, 61 cases, judge, retries)
costs about $0.24. The previous setup, with a `gemini-3.1-pro-preview` judge, cost about $0.44 per
release. `gemini-3.5-flash-lite` for the agent failed the gate (50/61): its answers left out
required details. Change models in `stack/litellm/config.yaml`.

The release prints the chat endpoint, for example `http://127.0.0.1:55448`. Open it in a browser,
or call it directly:

```
curl -s <endpoint>/invoke -H 'content-type: application/json' \
  -d '{"message":"Where is my order NW-10021?","customer_id":"cust-001"}'
```

The seed customers are listed in `stack/db/commerce.sql`.

**Ops dashboard** (staff approve or reject cancellation requests): open
`http://127.0.0.1:<ops port>/#token=<token>`, with the token from
`~/.agentbreeder/stacks/northwind-<id>/service-secrets/ops/ops-token`. The page keeps the token
from the URL fragment, which is never sent to the server.

**Phoenix** (traces): `http://127.0.0.1:<phoenix port>`. Sign in as `admin@localhost` with the
password in `service-secrets/phoenix/admin-password`.

## Honest limits (preview)

- **Customer scoping is tool code.** It holds in the shipped tools, but it is not an engine
  boundary, and `customer_id` in `/invoke` is not authenticated.
- **Gateway image.** The stack's LiteLLM gateway image is pinned at v1.106.2, which still bundles
  filelock 3.x (local-user advisories). The agent runtime itself uses litellm 1.108.3 with
  filelock 4.x.
- **Phoenix links.** The ops dashboard's "open in Phoenix" link uses Phoenix's address inside the
  stack, which a browser can't open; use the Phoenix port from `services status`.
- **Local only.** The stack is a local demo (`127.0.0.1` ports, generated demo identities). It is
  not a production deployment.
