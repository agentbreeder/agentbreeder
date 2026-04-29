// Package agentbreeder is the Go SDK for the AgentBreeder platform.
//
// It is a Tier-2 polyglot SDK: thin and contract-first. Every type and every
// HTTP shape in this package corresponds 1:1 to a definition in
// engine/schema/runtime-contract-v1.openapi.yaml. The SDK has two halves:
//
//   - Server: a chi-based [http.Handler] factory that satisfies the runtime
//     contract for any Go agent. The agent author supplies a single
//     [InvokeFunc] (and optionally a [StreamFunc]); this package wires up
//     /health, /invoke, /stream, /openapi.json, /.well-known/agent.json,
//     bearer-token auth from AGENT_AUTH_TOKEN, the X-Runtime-Contract-Version
//     response header, and SSE framing including the [DONE] terminator.
//
//   - Client: a typed registry client (see [Client]) for managing agents,
//     models, and secrets against an AgentBreeder API instance. This is the
//     Full Code tier surface — it is small on purpose and never bypasses
//     the central API.
//
// # Authentication
//
// The runtime contract specifies bearer-token auth via the AGENT_AUTH_TOKEN
// environment variable. When unset/empty (the local-dev path), the auth
// middleware no-ops; when set, it constant-time compares against
// "Authorization: Bearer …". /health, /openapi.json, and the agent card
// endpoints stay open even when auth is enabled.
//
// # Versioning
//
// This SDK targets contract version 1. Every response includes
// X-Runtime-Contract-Version: 1. When v2 ships, a major SDK release will
// follow.
//
// See the spec at engine/schema/runtime-contract-v1.md for the full prose
// definition.
package agentbreeder

// Version is the SDK version. Bumped manually on each release.
const Version = "0.1.0"

// ContractVersion is the runtime-contract version this SDK targets.
const ContractVersion = "1"
