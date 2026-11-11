# AgentBreeder

> **3.x is a rebuild, not an upgrade from 2.x.** No 2.x command, config or data carries over,
> and 3.x neither reads nor changes 2.x state in
> `~/.agentbreeder` (`agentbreeder doctor` reports when it finds some). If you use 2.x, pin
> `agentbreeder<3`. Until 3.x has a final release, a bare `pip install agentbreeder` still
> installs 2.7.3, which is unmaintained and has known functional and security issues; see
> [Legacy 2.x](https://www.agentbreeder.io/docs/legacy-2x).

**Alpha preview of a focused rebuild, not a production release.**

AgentBreeder is being rebuilt around deploying existing agents into customer-controlled infrastructure. This preview provides manifest validation, Docker diagnostics, a local deployment lifecycle with SQLite history, and a real HTTP sample service.

Deploys pass a **deploy-time identity gate**: the agent must already exist in your identity provider (Entra, Keycloak, Okta (experimental), AWS or a static file), with an active sponsor, a valid review date and the required scoped grants. Optional controls are a Cedar/OPA/AuthZEN policy check, second-person approval, a kill switch and a hash-chained local audit log; see [docs/identity-gate.md](https://github.com/agentbreeder/agentbreeder/blob/main/docs/identity-gate.md). Operators can also put local deployments behind an MCP egress boundary that enforces policy on every MCP Streamable-HTTP tool call. Production deployment is **not implemented yet**; a local single-user command center and a Kubernetes target for local clusters are a preview ([docs/command-center.md](https://github.com/agentbreeder/agentbreeder/blob/main/docs/command-center.md)). The local sample lifecycle and fresh-install demo have been verified against Docker Desktop on macOS. There are no simulated deployments or placeholder production endpoints.

## Install the preview

`3.0.0a1` is an alpha preview, on [PyPI](https://pypi.org/project/agentbreeder/3.0.0a1/) and
[npm](https://www.npmjs.com/package/agentbreeder/v/3.0.0-alpha.1). Each command pins the exact
version. A bare `pip install agentbreeder` installs 2.x (see the notice above).

```sh
pip install "agentbreeder[eval]==3.0.0a1"                               # in a virtual environment
uvx --from "agentbreeder[eval]==3.0.0a1" agentbreeder quickstart --dry-run   # needs uv
npx agentbreeder@3.0.0-alpha.1 quickstart --dry-run                       # needs Node 18+ and uv
```

The npm package is a launcher that runs the same PyPI release through `uvx`; see
[npm/README.md](https://github.com/agentbreeder/agentbreeder/blob/main/npm/README.md).
The [`v3.0.0a1` GitHub release](https://github.com/agentbreeder/agentbreeder/releases/tag/v3.0.0a1)
carries a hashed requirements file for `uv pip install --require-hashes -r
requirements-3.0.0a1.txt` (or `pip install --require-hashes -r …`).

What has been verified, installing `3.0.0a1` from the public registries on 2026-11-10:

- **macOS with Docker Desktop, end to end.** In a clean sandbox (fresh `HOME`, no checkout), pip,
  uvx and npx each ran a real `agentbreeder quickstart`. From the pip install, Northwind ran
  `services up` and `release` (61/61 evals), answered a live `/invoke` and was fully torn down.
- **Linux, install and `doctor` only.** On Ubuntu 24.04 in a container (Node 18.19.1), each of the
  three paths installed and ran `--version` and `doctor`. A real `quickstart` and Northwind have
  not been run on Linux.

## Try the complete local demo

Requires Python 3.11+ and a running Docker engine with a local context. The demo has been run on
macOS; on Linux only the install and `doctor` have been checked.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install "agentbreeder[eval]==3.0.0a1"
.venv/bin/agentbreeder quickstart
```

To work from a checkout instead, see Develop below. `agentbreeder quickstart --dry-run` runs `doctor` and validates the sample without building or deploying anything.

The demo builds a real sample image, deploys it, invokes its HTTP endpoint, retries the same deployment, verifies saved history, and removes its container and temporary image tag. It prints progress and the private directory containing its history and recovery files. No model account or API key is needed. Initial image downloads need internet access.

A successful run ends with:

```text
3/5 Invocation succeeded: http://127.0.0.1:…
4/5 Retry reused …; history persisted.
5/5 Sample container removed. History retained.
```

This sample echoes JSON; it is not an AI agent. Use a fresh environment: an older installation may expose the 2.x CLI.

## If the demo stops

Run `.venv/bin/agentbreeder doctor` first. Start Docker Desktop or your local Docker Engine if it cannot connect. The demo never installs or restarts Docker automatically.

| Problem | Next step |
|---|---|
| Docker cannot connect | Start the engine, check `docker context show`, then rerun doctor. |
| Remote Docker context or `DOCKER_HOST` override | Choose a local Unix-socket context and unset the override. |
| Image build fails | Run `.venv/bin/agentbreeder example http-agent`, then `docker build http-agent`, to see the build error; check registry access. |
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
The Northwind example runs this way end to end on macOS with Docker Desktop (61/61 evals, from a
checkout on 2026-11-08 and from PyPI on 2026-11-10). It has not been run on Linux. With
`agentbreeder` on your `PATH`:

```sh
agentbreeder example northwind   # copies it to ./northwind
cd northwind
agentbreeder services up --environment dev --env-file ~/.env --yes
agentbreeder release agent.yaml --stack . --environment dev
agentbreeder services down --purge --yes   # after `agentbreeder remove ID --local --stack .`
```

Local-only preview: see [src/agentbreeder/examples/northwind](https://github.com/agentbreeder/agentbreeder/blob/main/src/agentbreeder/examples/northwind/README.md),
[agent.yaml](https://github.com/agentbreeder/agentbreeder/blob/main/docs/agent-yaml.md) and [services](https://github.com/agentbreeder/agentbreeder/blob/main/docs/services.md).

## Local command center (preview, from a checkout)

`agentbreeder up` starts a web UI on `127.0.0.1` for one person. It onboards a project, starts its services after a grant review, runs the eval-gated release, and deploys that same image by digest to a local `kind` cluster. Every action runs the CLI, so the identity gate, policy, audit and eval gate still apply. Northwind passed 61/61 on both local Docker and `kind` on macOS on 2026-11-10. It is not in `3.0.0a1`, and no production cluster has been tested. See [docs/command-center.md](https://github.com/agentbreeder/agentbreeder/blob/main/docs/command-center.md).

## Develop

From a checkout of this repository:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/python -m build
```

Read [the manifest contract](https://github.com/agentbreeder/agentbreeder/blob/main/docs/manifest.md), [architecture](https://github.com/agentbreeder/agentbreeder/blob/main/ARCHITECTURE.md), [contributing guide](https://github.com/agentbreeder/agentbreeder/blob/main/CONTRIBUTING.md), and [security policy](https://github.com/agentbreeder/agentbreeder/blob/main/SECURITY.md).

## Compatibility

This is a clean rebuild, incompatible with 2.x. Do not use it to upgrade an existing AgentBreeder installation. No data migration or production deployment path exists yet. The legacy implementation remains in Git history at `7b086d9`; existing 2.x deployments and published 2.x packages are unaffected.

Engineering tracking: [#648](https://github.com/agentbreeder/agentbreeder/issues/648). Apache 2.0; see LICENSE and TRADEMARK.md.
