# Security policy

Report vulnerabilities through [GitHub private vulnerability reporting](https://github.com/agentbreeder/agentbreeder/security/advisories/new). Do not publish credentials or exploit details in a public issue.

## Development baseline

This branch is not production-ready and is not a supported upgrade to previous releases. It provides local manifest validation, diagnostics, a local Docker deployment lifecycle and a deploy-time identity gate (see `docs/identity-gate.md`). It has no compliance certification.

- Manifests are limited to 64 KiB and reject unknown/duplicate fields, anchors and aliases.
- Image references must contain an immutable SHA-256 digest; existence/signatures are not verified yet.
- Secret configuration contains references only. Validation errors must not echo supplied values.
- Doctor only inspects Docker availability; it does not install software or change host configuration.
- The HTTP sample is a local deployment demonstration without authentication. Do not expose it publicly or submit sensitive data.
- The identity gate, approvals, kill switch and hash-chained audit log run inside the local service. They guard against mistakes and policy drift, not against a local Docker or state administrator. The audit hash chain detects corruption; the database owner can recompute it, so it is not tamper-evidence.
- Runtime enforcement covers only MCP Streamable HTTP tool calls through the opt-in egress sidecar. STDIO and in-process tools are never mediated.

Production deployment, protected invocation and server-side durable audit are later work. Private model claims require verification of every relevant outbound data path. Unknown checks cannot be reported as passing.
