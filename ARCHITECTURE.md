# Focused rebuild architecture

## Implemented now

- `src/agentbreeder/manifest.py`: versioned strict input contract, bounded YAML parsing, no side effects.
- `src/agentbreeder/doctor.py`: read-only local Docker diagnostic with a timeout.
- `src/agentbreeder/cli.py`: validate, doctor, deploy, status, remove, approve/reject, approve-call/reject-call, disable/enable, reconcile, audit list/verify and login.
- Identity gate (#701): `operator_config.py` (operator enforcement/environments), `identity/` (read-only IdP adapters), `deployer.py` (OIDC-verified deployers), `policy/` (Cedar/OPA/AuthZEN PDP), `gate.py` (deploy-time checks), `audit.py` (hash-chained attempt log). See `docs/identity-gate.md`.
- `examples/http-agent`: real health/invocation sample; not an AI agent or production service.
- Agent building (#725, preview):
  - `agentfile.py` reads `agent.yaml` (authoring schema, `kind: agent`).
  - `registry.py` and `lock.py` handle versioned, content-hashed registries and `agent.lock`.
  - `build.py` (`agentbreeder build`) assembles the image from the built-in ADK runtime
    (`builtin_registry/runtime/adk/`) and writes a v2 deploy manifest.
  - `secret_files.py` mounts local secret files.
  - `gateway_keys.py` mints a LiteLLM virtual key per deployment.
  - `evals.py` and `release.py` (`agentbreeder eval` / `release`) run the eval gate and promote
    the exact evaluated image.
  - Verified end to end with `examples/northwind`. See `docs/agent-yaml.md` and
    `docs/registry.md`.
- Local backing services (#734, local-only preview): `services/` reads `stack.yaml` and
  `service.yaml` (`schema.py`), checks references and taint rules (`refs.py`), locks versions
  (`lock.py`), plans a hardened Compose file (`plan.py`, `compose.py`), keeps secrets and state
  under `~/.agentbreeder/stacks/` (`state.py`) and writes a demo operator config (`operator.py`);
  `engine.py` runs `agentbreeder services up|status|logs|env|down`. Built-ins live in
  `builtin_registry/service/` (postgres-pgvector, litellm, phoenix, echo). Verified with
  `examples/northwind` (61/61). See `docs/services.md`.

## Next implementation

CLI and a minimal UI will call one deployment API. A durable worker will perform real deployment and recovery, backed by persistent job and audit state. It will validate permissions and exact-revision approvals before infrastructure changes. Only the executor receives production deployment credentials.

Local Docker plus one certified production target is the intended first scope. A target enum in the manifest is not an implemented adapter. The initial UI will cover Agents, Agent detail, Approvals and Settings.

Deploy takes an image: the v2 manifest stays image-in, and any existing image can be deployed. Separately, `agentbreeder build` can produce that image from an `agent.yaml` for Google ADK. It uses the shipped runtime plus registry entries (prompts, knowledge, tool packages, eval sets), so the agent author writes no runtime code. Custom logic lives in tool packages. Other frameworks are not supported yet. Builds run with the local Docker; server-side builds are out of scope.

A synchronous local lifecycle service uses SQLite transactions and a process lock, with ownership-checked Docker operations. No background/distributed worker, production adapter, server-side executor or UI exists in this baseline. Optional runtime tool enforcement (`runtime_enforcement.mode: mcp_http`) runs a per-deployment sidecar on a Docker internal network plus a host decision service (`src/agentbreeder/egress/`). The identity gate runs inside the local service, so it protects against mistakes and policy drift, not against a local Docker or state administrator. Add them as tested vertical slices rather than restoring legacy placeholder paths.

Local implementation: `deployment.py` is the shared service, `store.py` provides durable state/events, and `runtime.py` provides the local Docker boundary. See `docs/local-deployment.md`.
