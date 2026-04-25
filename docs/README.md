# docs/

Internal engineering documentation for AgentBreeder.

**User-facing docs** (quickstart, guides, CLI reference, API) live at [agentbreeder.io/docs](https://agentbreeder.io/docs) — source in `website/content/docs/`.

**Platform architecture** — see [`ARCHITECTURE.md`](../ARCHITECTURE.md) at the repo root.

---

## Contents

```
docs/
└── design/           # Feature-level design documents
    ├── README.md     # Index of all design docs
    ├── litellm-integration.md   # Model gateway & LiteLLM proxy (#131)
    ├── polyglot-agents.md       # TypeScript/Rust/Go runtimes + APS sidecar (#129)
    └── rbac-auth.md             # RBAC, ACL, approvals, credential lifecycle (#128)
```
