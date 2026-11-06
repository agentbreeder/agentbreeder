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
| `secrets` | Optional map of uppercase environment names to `secret://name` references; no literal values |
| `identity` | v2 only, required: `provider` (operator connection name), `ref` (provider-specific immutable ID), `environment`, optional `expect_owner`. A reference only; never created |
| `requires.roles` | v2: list of `{authority, resource, role_id}` immutable IDs; must already be granted |
| `requires.groups` | v2: list of `{authority, group_id}` stable IDs |
| `requires.attributes` | v2: map of `namespace/name` to exact required value |
| `capabilities` | v2: `tools: [{mcp: https://…, actions: [...]}]` and `approval_required` (declared actions). Deploy-time ceiling; enforced per call only when the operator enables `runtime_enforcement.mode: mcp_http` (MCP Streamable HTTP via the sidecar; see `docs/identity-gate.md`) |
| `policy.restrictions` | v2: operator-approved restriction bundle IDs (lowercase IDs, never paths or OCI references). They can only restrict |

Input must be one UTF-8 YAML mapping at most 64 KiB. Unknown fields, duplicate keys, aliases/anchors and invalid types are rejected. Secret references are names only; the production secret backend will be configured by the administrator in a future slice.

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

The local deploy command applies the identity gate (`identity-gate.md`), rejects nonempty secrets and Kubernetes targets. A local image ID is invalid with a Kubernetes target. See `local-deployment.md` for runtime limitations.
