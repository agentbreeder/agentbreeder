# Focused rebuild architecture

## Implemented now

- `src/agentbreeder/manifest.py`: versioned strict input contract, bounded YAML parsing, no side effects.
- `src/agentbreeder/doctor.py`: read-only local Docker diagnostic with a timeout.
- `src/agentbreeder/cli.py`: validate, doctor, deploy, status and remove commands.
- Identity gate (#701): `operator_config.py` (operator enforcement/environments), `identity/` (read-only IdP adapters), `deployer.py` (OIDC-verified deployers), `policy/` (Cedar/OPA/AuthZEN PDP), `gate.py` (deploy-time checks), `audit.py` (hash-chained attempt log). See `docs/identity-gate.md`.
- `examples/http-agent`: real health/invocation sample; not an AI agent or production service.

## Next implementation

CLI and a minimal UI will call one deployment API. A durable worker will perform real deployment and recovery, backed by persistent job and audit state. It will validate permissions and exact-revision approvals before infrastructure changes. Only the executor receives production deployment credentials.

Local Docker plus one certified production target is the intended first scope. A target enum in the manifest is not an implemented adapter. The initial UI will cover Agents, Agent detail, Approvals and Settings.

Existing Dockerfiles/images are the input. Agent behavior, models, RAG and orchestration remain the responsibility of the supplied application. Builds will use local/customer CI tooling; arbitrary server-side builds are outside the initial scope.

A synchronous local lifecycle service uses SQLite transactions and a process lock, with ownership-checked Docker operations. No background/distributed worker, production adapter, server-side executor or UI exists in this baseline. Optional runtime tool enforcement (`runtime_enforcement.mode: mcp_http`) runs a per-deployment sidecar on a Docker internal network plus a host decision service (`src/agentbreeder/egress/`). The identity gate runs inside the local service, so it protects against mistakes and policy drift, not against a local Docker or state administrator. Add them as tested vertical slices rather than restoring legacy placeholder paths.

Local implementation: `deployment.py` is the shared service, `store.py` provides durable state/events, and `runtime.py` provides the local Docker boundary. See `docs/local-deployment.md`.
