# AgentBreeder

**Focused rebuild — development baseline, not a production release.**

AgentBreeder is being rebuilt around deploying existing agents into customer-controlled infrastructure. This branch provides manifest validation, Docker diagnostics, a local deployment lifecycle with SQLite history, and a real HTTP sample service.

Deploys pass a **deploy-time identity gate**: the agent must already exist in your identity provider (Entra, Keycloak, Okta (experimental), AWS or a static file), with an active sponsor, a valid review date and the required scoped grants. Optional controls are a Cedar/OPA/AuthZEN policy check, second-person approval, a kill switch and a hash-chained local audit log; see [docs/identity-gate.md](docs/identity-gate.md). Operators can also put local deployments behind an MCP egress boundary that enforces policy on every MCP Streamable-HTTP tool call. Production deployment and the operational UI are **not implemented yet**. The local sample lifecycle and fresh-install demo have been verified against Docker Desktop on macOS. There are no simulated deployments or placeholder production endpoints.

## Try the complete local demo

Requires macOS or Linux, Python 3.11+, and a running Docker engine with a local context. From this checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/http-agent/quickstart.py
```

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
| Image build fails | Run `docker build examples/http-agent` to see the build error; check registry access. |
| Deployment or cleanup fails | Keep the printed state directory. Follow the printed removal command, or use the recovery commands below. |

```sh
export AGENTBREEDER_OPERATOR_CONFIG=DEMO_DIRECTORY/operator.yaml
.venv/bin/agentbreeder status DEPLOYMENT_ID --state-dir STATE_DIRECTORY --json
.venv/bin/agentbreeder remove DEPLOYMENT_ID --state-dir STATE_DIRECTORY --local
```

If no ID was printed, retry `deploy DEMO_DIRECTORY/agent.yaml --state-dir DEMO_DIRECTORY/state --local --environment dev` (with the same `AGENTBREEDER_OPERATOR_CONFIG`) to reconcile the saved request, then remove its returned ID. Do not delete state while a workload remains. Demo history is stored in a temporary directory and may be cleared by the OS; copy it privately if needed.

## Bring your own agent

Follow [the local image guide](docs/first-deployment.md). Supply a trusted stateless HTTP container, a real immutable image ID/digest, and a health path returning HTTP 200. Containers run as UID 65532 with a read-only filesystem and publish only to loopback. Images declaring volumes and secret injection are currently unsupported.

Local OS/Docker permissions are the control boundary. Production permissions, approvals and authenticated invocation are later work. Status shows the last recorded observation, not a live health guarantee.

## Develop

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/ruff check src tests examples/http-agent
.venv/bin/ruff format --check src tests examples/http-agent
.venv/bin/python -m build
```

Read [the manifest contract](docs/manifest.md), [architecture](ARCHITECTURE.md), [contributing guide](CONTRIBUTING.md), and [security policy](SECURITY.md).

## Compatibility

This is a clean, incompatible development baseline. Do not use it to upgrade an existing AgentBreeder installation. No data migration or production deployment path exists yet. The legacy implementation remains in Git history at `7b086d9`; existing deployments and published packages are unaffected by changes to this branch.

Engineering tracking: [#648](https://github.com/agentbreeder/agentbreeder/issues/648). Apache 2.0; see LICENSE and TRADEMARK.md.
