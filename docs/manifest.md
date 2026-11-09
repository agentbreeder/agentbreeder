# Deployment manifest (v1 and v2)

This contract is under development. Validation does not deploy resources or check that an image/secret exists. It is incompatible with legacy `agent.yaml` files.

| Field | Contract |
|---|---|
| `schema_version` | Required integer `1` or `2`. Under operator `enforce` mode, deploy requires `2` |
| `name` | Required lowercase DNS label, 1–63 characters |
| `image` | Required image reference ending in `@sha256:` and 64 lowercase hexadecimal characters, or local-only `sha256:<64 hex>` image ID |
| `target` | `local` (default) or `kubernetes`; only the local adapter is implemented |
| `port` | Integer 1–65535; default 8080 |
| `health_path` | Absolute HTTP path containing letters, digits, `/`, `_`, `-`; default `/health`; no query string |
| `secrets` | Optional map of uppercase environment names to `secret://name` references; no literal values. Locally, values come from secret files (below) |
| `identity` | v2 only, required: `provider` (operator connection name), `ref` (provider-specific immutable ID), `environment`, optional `expect_owner`. A reference only; never created |
| `requires.roles` | v2: list of `{authority, resource, role_id}` immutable IDs; must already be granted |
| `requires.groups` | v2: list of `{authority, group_id}` stable IDs |
| `requires.attributes` | v2: map of `namespace/name` to exact required value |
| `capabilities` | v2: `tools: [{mcp: https://…, actions: [...]}]` and `approval_required` (declared actions). Deploy-time ceiling; enforced per call only when the operator enables `runtime_enforcement.mode: mcp_http` (MCP Streamable HTTP via the sidecar; see `docs/identity-gate.md`) |
| `policy.restrictions` | v2: operator-approved restriction bundle IDs (lowercase IDs, never paths or OCI references). They can only restrict |
| `model_access` | Optional: `{gateway, aliases: [...], budget_usd?, rpm?}`. Model gateway access the deployment requests. The operator environment grants it: the gate denies a gateway mismatch (`GATEWAY_MISMATCH`), an alias outside `environments.<env>.models` (`MODEL_NOT_ALLOWED`), a budget or rpm above `ceilings` (`BUDGET_CEILING`) and an environment with no gateway (`MODEL_ACCESS_UNCONFIGURED`). None of these can be overridden in audit mode. The container gets `AGENTBREEDER_GATEWAY_URL` and `AGENTBREEDER_SERVICE_<NAME>` (plain, non-secret values). `agentbreeder build` writes this block. When the gateway has a `master_key`, deploy mints a LiteLLM virtual key for each container generation (alias `agentbreeder-<deployment id>`). The key is limited to the aliases, a budget that renews every 30 days (manifest value, else the environment ceiling) and the rpm (likewise). An environment that sets a `gateway` must set `ceilings`, so nothing can request an uncapped key. It is mounted at `/run/secrets/llm-gateway-key` with `LLM_GATEWAY_KEY_FILE`. A manifest may not declare `LLM_GATEWAY_KEY` itself (`SECRET_RESERVED`). Remove and disable revoke the key; a revoke that fails because the gateway is down is retried by `reconcile`, which also revokes orphaned keys. `status` shows spend against budget. The audit log records the alias, never the key |
| `mcp_access` | Optional, needs `model_access`: `[{ref: mcp/<name>@X.Y.Z, tools: [...]}]`, one entry per MCP server. **Contract only so far: the relay that serves these grants is not implemented yet, so this isn't usable end to end.** Deploy resolves each `ref` to the environment's `mcp_relays` entry with that ref (`MCP_SERVER_UNAVAILABLE`, `MCP_SERVER_AMBIGUOUS`) and checks that the agent's identity is granted every tool (`environments.<env>.agents.<agent>.mcp`, `MCP_TOOL_NOT_GRANTED`); `preflight` and `release --rollback` see the same denies. It is refused with `runtime_enforcement: mcp_http` (`MCP_RELAY_UNSUPPORTED`). For each container generation deploy deletes the deployment's earlier grants, mints a 256-bit relay token into the deployment's private secret file (`AGENTBREEDER_MCP_<NAME>_TOKEN_FILE`), writes a read-only grant file named by the token's sha256 (deployment, agent, environment, server, tools; never the token) into the relay's `grants_dir`, and sets `AGENTBREEDER_MCP_<NAME>_URL`. Failure, remove and disable delete the grants first; `reconcile` deletes grants with no active deployment or a stale token. Left out of the digest when unset |

Input must be one UTF-8 YAML mapping at most 64 KiB. Unknown fields, duplicate keys, aliases/anchors and invalid types are rejected. Secret references are names only. Local secret files: each `secret://name` reference (local names match `[a-z0-9][a-z0-9_-]{0,62}`) is read from `<state-dir>/secrets/<name>`, which `agentbreeder secret set NAME` writes from stdin (mode 0600, owned by you, symlinks refused). After the gate allows the deploy, AgentBreeder copies each value into the deployment's private directory and bind-mounts it read-only at `/run/secrets/<name>`, setting `<ENV>_FILE=/run/secrets/<name>` in the container (for example `DATABASE_URL_FILE`). Read the file in your app; the value is never put in environment variables, labels, logs or the audit log (which records names only). A missing or unsafe file is a deny (`SECRET_MISSING`, `SECRET_MODE`). The copies are deleted when the deployment fails or is removed. Secret files and `model_access` are not yet supported together with `runtime_enforcement: mcp_http` (`SECRETS_UNSUPPORTED`, `MODEL_ACCESS_UNSUPPORTED`): the egress boundary has no route to the gateway yet, so agents built from `agent.yaml` can't run behind it. A production secret backend is a future slice.

```yaml
schema_version: 1
name: support-agent
# Replace with an actual image digest before deploying locally.
image: registry.example.com/support@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
target: local
port: 8080
health_path: /health
secrets:
  MODEL_API_KEY: secret://model-key
```

Schema version 2 adds the identity reference and requirements checked by the deploy-time identity gate; see `identity-gate.md`. Nothing in the manifest can grant a permission, select the baseline policy, choose enforcement mode or create an environment.

```yaml
schema_version: 2
name: refund-agent
image: registry.example.com/refund@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
target: local
identity:
  provider: corp
  ref: 4f1c2b7e-0000-4000-8000-000000000001
  environment: prod
  expect_owner: alice@example.com
requires:
  roles:
    - {authority: tenant-id, resource: payments-api-id, role_id: refunds-role-id}
  groups:
    - {authority: tenant-id, group_id: finance-group-id}
  attributes:
    acme-governance/data_classification: confidential
policy:
  restrictions: [pci]
```

Exit codes: validation success 0; invalid input/usage 2. The CLI supports `validate agent.yaml --json` for normalized output. The new manifest intentionally omits framework, prompt, RAG and orchestration definitions: supply agent behavior in your container.

The local deploy command applies the identity gate (`identity-gate.md`), mounts local secret files and rejects Kubernetes targets. A local image ID is invalid with a Kubernetes target. See `local-deployment.md` for runtime limitations.
