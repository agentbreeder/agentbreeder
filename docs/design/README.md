# Design Documents

Feature-level design docs for AgentBreeder. Each doc covers the problem statement, key decisions, architecture, and pending work for its feature area.

For the platform-level architecture overview, see [`ARCHITECTURE.md`](../ARCHITECTURE.md) at the repo root.

---

## Index

| Document | Feature | Status | Issue |
|---|---|---|---|
| [litellm-integration.md](litellm-integration.md) | Model Gateway & LiteLLM proxy integration | **In progress** — `LiteLLMProvider` + live gateway (Ph1), key injection into APS sidecar (Ph2), guardrails + OTEL (Ph3) | [#131](https://github.com/agentbreeder/agentbreeder/issues/131) |
| [polyglot-agents.md](polyglot-agents.md) | TypeScript/Node.js, Rust, Go agent runtimes + APS sidecar | **In progress** — Node runtime + APS sidecar (Ph1), Rust + Go + Go binary APS (Ph2) | [#129](https://github.com/agentbreeder/agentbreeder/issues/129) |
| [rbac-auth.md](rbac-auth.md) | RBAC, per-asset ACL, approval workflow, credential lifecycle | **Ph1–3 done** — deploy gate, `litellm_team_id` migration, ACL on non-agent routes, APS token pending; SSO (Ph4) planned | [#128](https://github.com/agentbreeder/agentbreeder/issues/128) |

---

## Adding a New Design Doc

Create a markdown file in this directory named after the feature: `docs/design/<feature>.md`.

Each doc should cover:
1. **Problem statement** — what gap this solves
2. **Key design decisions** — the choices made and alternatives rejected
3. **Architecture** — how it fits into the system (reference `ARCHITECTURE.md` sections)
4. **Database schema** — any new tables or migrations
5. **API endpoints** — any new or changed routes
6. **Pending work** — checklist of what's not yet implemented

Link to the relevant GitHub issue and update this README index.
