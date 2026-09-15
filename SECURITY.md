# Security policy

Report vulnerabilities through [GitHub private vulnerability reporting](https://github.com/agentbreeder/agentbreeder/security/advisories/new). Do not publish credentials or exploit details in a public issue.

## Development baseline

This branch is not production-ready and is not a supported upgrade to previous releases. It provides local manifest validation and diagnostics. There is no deployment access enforcement or compliance certification in this baseline.

- Manifests are limited to 64 KiB and reject unknown/duplicate fields, anchors and aliases.
- Image references must contain an immutable SHA-256 digest; existence/signatures are not verified yet.
- Secret configuration contains references only. Validation errors must not echo supplied values.
- Doctor only inspects Docker availability; it does not install software or change host configuration.
- The HTTP sample is a local deployment demonstration without authentication. Do not expose it publicly or submit sensitive data.

The next deployment slice must enforce identity, permission checks, protected invocation, exact-revision approval and durable audit recording. Private model claims require verification of every relevant outbound data path. Unknown checks cannot be reported as passing.
