# AgentBreeder

> **3.x is a rebuild, not an upgrade from 2.x.** No 2.x command, config or data carries over,
> and 3.x neither reads nor changes 2.x state in
> `~/.agentbreeder` (`agentbreeder doctor` reports when it finds some). If you use 2.x, pin
> `agentbreeder<3`. Until 3.x has a final release, a bare `pip install agentbreeder` still
> installs 2.7.3, which is unmaintained and has known functional and security issues; see
> [Legacy 2.x](https://www.agentbreeder.io/docs/legacy-2x).

**Focused rebuild — development baseline, not a production release.**

AgentBreeder is being rebuilt around deploying existing agents into customer-controlled infrastructure. This branch provides manifest validation, Docker diagnostics, a local deployment lifecycle with SQLite history, and a real HTTP sample service.

Deploys pass a **deploy-time identity gate**: the agent must already exist in your identity provider (Entra, Keycloak, Okta (experimental), AWS or a static file), with an active sponsor, a valid review date and the required scoped grants. Optional controls are a Cedar/OPA/AuthZEN policy check, second-person approval, a kill switch and a hash-chained local audit log; see [docs/identity-gate.md](https://github.com/agentbreeder/agentbreeder/blob/main/docs/identity-gate.md). Operators can also put local deployments behind an MCP egress boundary that enforces policy on every MCP Streamable-HTTP tool call. Production deployment and the operational UI are **not implemented yet**. The local sample lifecycle and fresh-install demo have been verified against Docker Desktop on macOS. There are no simulated deployments or placeholder production endpoints.

## Try the complete local demo

Requires macOS or Linux, Python 3.11+, and a running Docker engine with a local context. From this checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/agentbreeder quickstart
```

`agentbreeder quickstart --dry-run` runs `doctor` and validates the sample without building or deploying anything.

The demo builds a real sample image, deploys it, invokes its HTTP endpoint, retries the same deployment, verifies saved history, and removes its container and temporary image tag. It prints progress and the private directory containing its history and recovery files. No model account or API key is needed. Initial image downloads need internet access.

A successful run ends with:

```text
3/5 Invocation succeeded: http://127.0.0.1:…
4/5 Retry reused …; history persisted.
5/5 Sample container removed. History retained.
```

This sample echoes JSON; it is not an AI agent. Use a fresh environment: an older editable installation may expose the previous CLI, and PyPI does not install this branch.

## If the demo stops

Run `.venv/bin/agentbreeder doctor` first. Start Docker Desktop or your local Docker Engine if it cannot connect. The demo never installs or restarts Docker automatically.

| Problem | Next step |
|---|---|
| Docker cannot connect | Start the engine, check `docker context show`, then rerun doctor. |
| Remote Docker context or `DOCKER_HOST` override | Choose a local Unix-socket context and unset the override. |
| Image build fails | Run `docker build src/agentbreeder/examples/http-agent` to see the build error; check registry access. |
| Deployment or cleanup fails | Keep the printed state directory. Follow the printed removal command, or use the recovery commands below. |

```sh
export AGENTBREEDER_OPERATOR_CONFIG=DEMO_DIRECTORY/operator.yaml
.venv/bin/agentbreeder status DEPLOYMENT_ID --state-dir STATE_DIRECTORY --json
.venv/bin/agentbreeder remove DEPLOYMENT_ID --state-dir STATE_DIRECTORY --local
```

If no ID was printed, retry `deploy DEMO_DIRECTORY/agent.yaml --state-dir DEMO_DIRECTORY/state --local --environment dev` (with the same `AGENTBREEDER_OPERATOR_CONFIG`) to reconcile the saved request, then remove its returned ID. Do not delete state while a workload remains. Demo history is stored in a temporary directory and may be cleared by the OS; copy it privately if needed.

## Bring your own agent

Follow [the local image guide](https://github.com/agentbreeder/agentbreeder/blob/main/docs/first-deployment.md). Supply a trusted stateless HTTP container, a real immutable image ID/digest, and a health path returning HTTP 200. Containers run as UID 65532 with a read-only filesystem and publish only to loopback. Images declaring volumes are unsupported. Secrets are local files mounted read-only (`agentbreeder secret set`; see [local deployment](https://github.com/agentbreeder/agentbreeder/blob/main/docs/local-deployment.md)).

Local OS/Docker permissions are the control boundary. Production permissions, approvals and authenticated invocation are later work. Status shows the last recorded observation, not a live health guarantee.

## Build an agent with its backing services (preview)

`agentbreeder build` turns an `agent.yaml` (registry references for the runtime, prompt,
knowledge, tools and evals; no agent code) into an image, and `agentbreeder release` deploys a
candidate, runs its eval set and promotes it only if the gate passes. `agentbreeder services up`
starts the services the agent needs from a `stack.yaml` (Postgres with pgvector, a LiteLLM
gateway, Phoenix, project services) on local Docker and writes a demo operator config for them.
`src/agentbreeder/examples/northwind` runs this way end to end on macOS with Docker Desktop (61/61 evals,
2026-11-08):

```sh
cd src/agentbreeder/examples/northwind
agentbreeder services up --environment dev --env-file ~/.env --yes
agentbreeder release agent.yaml --stack . --environment dev
agentbreeder services down --purge --yes   # after `agentbreeder remove ID --local --stack .`
```

Local-only preview: see [src/agentbreeder/examples/northwind](https://github.com/agentbreeder/agentbreeder/blob/main/src/agentbreeder/examples/northwind/README.md),
[agent.yaml](https://github.com/agentbreeder/agentbreeder/blob/main/docs/agent-yaml.md) and [services](https://github.com/agentbreeder/agentbreeder/blob/main/docs/services.md).

## Develop

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/python -m build
```

Read [the manifest contract](https://github.com/agentbreeder/agentbreeder/blob/main/docs/manifest.md), [architecture](https://github.com/agentbreeder/agentbreeder/blob/main/ARCHITECTURE.md), [contributing guide](https://github.com/agentbreeder/agentbreeder/blob/main/CONTRIBUTING.md), and [security policy](https://github.com/agentbreeder/agentbreeder/blob/main/SECURITY.md).

## Compatibility

This is a clean, incompatible development baseline. Do not use it to upgrade an existing AgentBreeder installation. No data migration or production deployment path exists yet. The legacy implementation remains in Git history at `7b086d9`; existing deployments and published packages are unaffected by changes to this branch.

Engineering tracking: [#648](https://github.com/agentbreeder/agentbreeder/issues/648). Apache 2.0; see LICENSE and TRADEMARK.md.
