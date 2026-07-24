> Extracted from CLAUDE.md (thin-router refactor). Read on demand — see CLAUDE.md router for when.

## 🤝 For Contributors

When reviewing AI-generated code, always verify:
- [ ] Does this change touch the deploy pipeline? If yes, run full integration tests.
- [ ] Does this change the `agent.yaml` schema? If yes, update JSON Schema + docs.
- [ ] Does this change the registry schema? If yes, write a migration.
- [ ] Does this add a new deployer or runtime? If yes, add it to the supported stack matrix in README.
- [ ] Does this change a CLI command? If yes, update the corresponding page in `website/content/docs/` **in the same commit**. See the doc sync table below.

### 📄 Docs Sync — What to Update

Every time a feature or CLI command changes, update the matching doc page(s) before closing the PR.

| What changed | Doc pages to update |
|---|---|
| New CLI command | `website/content/docs/cli-reference.mdx` (add full entry with flags + examples) |
| Changed CLI command flags | `website/content/docs/cli-reference.mdx` (update the relevant section) |
| New quickstart / bootstrap flow | `website/content/docs/quickstart.mdx` |
| New install method | `website/content/docs/quickstart.mdx` → Install table; `website/content/docs/how-to.mdx` → Install section |
| New or changed RAG / ChromaDB behavior | `website/content/docs/rag.mdx` |
| New or changed GraphRAG / Neo4j behavior | `website/content/docs/graphrag.mdx` |
| New or changed MCP server support | `website/content/docs/mcp-servers.mdx` |
| New or changed A2A protocol behavior | `website/content/docs/a2a-protocol.mdx` |
| New `agent.yaml` field | `website/content/docs/agent-yaml.mdx` |
| New LLM provider | `website/content/docs/how-to.mdx` (local models section) |
| New framework support | `website/content/docs/how-to.mdx` + README supported stack matrix |
| New cloud deploy target | `website/content/docs/how-to.mdx` + README supported stack matrix |
| New evaluation feature | `website/content/docs/evaluations.mdx` |
| README Install section | Keep in sync with `website/content/docs/quickstart.mdx` → Install table |

**Rule:** The doc update goes in the **same commit** as the code change — never as a follow-up. If the doc page does not exist yet, create it under `website/content/docs/`.

**The website auto-deploys on push to `main`.** There is no separate deploy step — merging the PR publishes the docs immediately.

---

*Last updated: April 2026 — AgentBreeder v2.0*
