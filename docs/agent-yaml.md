# `agent.yaml` (authoring schema v1, preview)

> Part of the `agent.yaml` build work (#725), preview. `validate` and `build` work today with the
> ADK runtime (`runtime/adk@1.0.1`). Verified end to end on macOS + Docker Desktop with the
> Northwind example (`examples/northwind/`): built from `agent.yaml`, deployed through the gate
> and answering, and released through the 61-case eval gate with `agentbreeder release` (61/61,
> 2026-11-07).

An agent file says what an agent is made of: a runtime, a model alias, a prompt, knowledge, tools,
memory and evals. Everything is a registry reference (`kind/name@X.Y.Z`, see `registry.md`). You
write no Python: the runtime and the tool packages provide the code.

An agent file is **not** the deploy manifest (`manifest.md`), which stays image-in. It is also
environment-neutral: the gateway, services, secret bindings and identities come from the operator
config for the environment you build for. Nothing in it can grant a permission or raise a limit.

```yaml
kind: agent                    # required; tells agent files apart from manifests and 2.x files
schema_version: 1
name: northwind-support        # DNS label, at most 53 characters
runtime: runtime/adk@1.0.1
model:
  alias: support-model         # a LiteLLM gateway alias
  reasoning: low               # none | low | medium | high
  max_model_calls: 4           # the last call has tools switched off
  budget_usd: 25               # optional; can only lower the operator's ceiling
  rpm: 120                     # optional; same rule
prompt: {ref: prompt/northwind-support@1.0.0}   # or {path: ./prompts/dev.md} while iterating
knowledge:
  - {ref: knowledge/northwind-help@1.0.0, top_k: 5, extra_searches: 1}
tools:
  - {ref: tool/customer-notes@1.0.0}
  - {ref: tool/orders@1.0.0}
memory: {sessions: postgres, notes: true}       # notes needs tool/customer-notes
agent_events: postgres         # the agent's own event log, not the deploy audit chain
traces: otlp
evals: {ref: eval/northwind-golden@1.0.0, threshold: 1.0, retries: 1}
```

## Validation rules

- **Strict YAML:** at most 64 KiB, unique keys, no anchors or aliases, and no unknown fields.
- **Refs:** each ref must have the kind its field expects, an exact version, and may appear only
  once.
- **Paths:** `prompt.path` must be a relative path inside the project.
- **Error messages** name the field and never echo the value you supplied.

## Wrong file kinds

`validate` and `deploy` tell the three file kinds apart:

- **An agent file passed to `deploy`** is refused: build it first.
- **A deploy manifest passed where an agent file is expected** gets a pointer to `deploy`.
- **A 2.x `agent.yaml`** (`spec_version` or `framework`, without `kind`) is reported as
  unsupported.

## Build

```
agentbreeder build agent.yaml --environment dev [--locked | --update [REF ...]] [--output deploy/] [--json]
```

`build` resolves every ref (`registry.md`), copies and hashes each one into a temporary build
context, writes `agent.config.json` (data only), and runs `docker build` with the runtime's fixed
Dockerfile. That Dockerfile has a base image pinned by digest, hash-locked requirements, and no
build arguments or secrets. `build` then writes:

- `deploy/<name>.deploy.yaml`: the v2 deploy manifest for that image. Its identity, secret
  bindings and `model_access` come from the operator environment.
- `deploy/<name>-candidate.deploy.yaml`, when the environment defines a candidate identity.
- `deploy/<name>.build.json`: the image, the agent.yaml and lock hashes, and every ref's hash.
- `agent.lock`, unless you ran with `--locked`.

`build` never deploys and never reads secrets. Deploy the manifest with `agentbreeder deploy`.

The operator environment has to grant what the build needs:

```yaml
gateways:
  litellm-local:
    type: litellm
    url: http://127.0.0.1:4000
    agent_url: http://host.docker.internal:4000
environments:
  dev:
    targets: [local]
    gateway: litellm-local
    models: [support-model, support-embedding]
    ceilings: {budget_usd: 50, rpm: 300}
    services: {traces_url: …, tools_proxy_url: …}            # → AGENTBREEDER_SERVICE_<NAME>
    bindings: {DATABASE_URL: secret://northwind-db}
    agents:
      northwind-support:
        identity: {provider: demo, ref: support-agent, environment: dev}
        candidate_identity: {provider: demo, ref: support-agent-candidate, environment: dev}
```

## The ADK runtime (`runtime/adk@1.0.1`)

The runtime ships with AgentBreeder and is built on Google ADK 2.12. It reaches models through
the LiteLLM gateway, using the `openai/<alias>` route with `reasoning_effort` set from
`model.reasoning`.

- **Sessions:** ADK sessions in Postgres.
- **Memory:** per-customer notes, injected into the prompt.
- **Knowledge:** hybrid pgvector and BM25 retrieval, with embeddings through the gateway.
- **Tools:** loaded from tool packages (`tool.yaml` plus `src/`). The customer comes from the
  server-side session, never from the model.
- **Step cap:** the last model call has tools switched off.
- **Observability:** OpenInference traces and an append-only event log.
- **Secrets:** read only from `*_FILE` mounts.

Tools run in the agent's own process with its privileges. Customer scoping is enforced by the
shipped tools' own code, not by an engine boundary.

**`customer_id` is not authenticated.** `POST /invoke` accepts `customer_id` from the request
body, and AgentBreeder does not ship authenticated invocation. Anyone who can reach the endpoint
can act as any customer. The endpoint is published on loopback only. Put an authenticating proxy
in front of it before exposing it to real users, and have that proxy set the customer. Requests
without a `customer_id` run as the shared `guest` identity. Guests get no saved notes, so notes
cannot leak between anonymous visitors, and note text is neutralised before it enters the
prompt.

## Eval and release

```
agentbreeder eval agent.yaml --endpoint URL [--environment ENV] [--only id,...] [--retries N] [--json]
agentbreeder release agent.yaml --environment ENV [--threshold 1.0] [--retries N] [--json]
agentbreeder release agent.yaml --rollback [--environment ENV]   # gate-checked before anything is removed
```

`eval` runs the agent's eval set (`eval/<name>@X.Y.Z`, verified against `agent.lock`) against a
running endpoint. A case passes when every rule check passes (sources, tools, regex) and the LLM
judge scores correctness at least 0.7. The judge calls the gateway alias from the eval manifest,
on its own short-lived key that is revoked after the run. The eval set's `fixtures.sql` runs
against the environment's `eval_database` before the run and before each retry round. That needs
`pip install "agentbreeder[eval]"`.

`release` is orchestration around two ordinary governed deploys. The exit codes are 0 released or
already in production, 1 runtime failure, 2 denied, 3 needs approval, 5 eval gate failed. The
steps:

1. Build with `--locked`.
2. Check the production manifest through the gate without deploying. A deny exits 2. A required
   approval exits 3, because release does not run approvals.
3. Deploy `<name>-candidate` under its own identity and gateway key, run the eval set against it,
   then remove the candidate.
4. Below the threshold, exit 5 with production unchanged. The report is in
   `deploy/<name>.eval-<run>.json`.
5. Otherwise write a release journal (the previous production and its manifest) to
   `<state-dir>/releases/<name>.json`,
   remove the old production, and deploy the **exact evaluated image**.
6. If the new production fails to start, restore the previous one.

Limits:
- **The eval gate can be bypassed.** It is release orchestration, not an engine control: anyone
  with deploy rights can run plain `deploy`.
- **Promotion has brief downtime.** The engine refuses two specs under one name, so the old
  production is removed before the new one starts.
- **Gateway changes are not gated.** Changing the gateway's model mapping does not change the
  image, so `release` reports "already in production". Re-run `eval` against production after
  gateway changes.
