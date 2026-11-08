# /loop brief: agent.yaml → build → governed deploy, with registries; then Studio on top

Self-paced loop. Each iteration advances ONE stage below, records it in `RESOLVE.md` (`## LIVE`:
epic, phase, stage, NEXT ACTION) and stops. Never skip a stage gate.

## Owner decisions (2026-11-07)
- Rebuild on the new engine (`src/agentbreeder/`). Port useful 2.x pieces from tag `v2.7.3`
  (e.g. `engine/runtimes/templates/google_adk_server.py`, `engine/prompt_resolver.py`,
  `engine/tool_bridge.py`) after reviewing them. Do not revive the 2.x API server or dashboard.
- Google ADK is the first and only framework until the Northwind acceptance test passes.
- The user writes `agent.yaml` and no Python. AgentBreeder ships the runtime. Custom code lives in
  the tool registry, as Python tool packages or MCP servers.
- Registries: prompts (versioned), tools, vector/KB indexes, MCP servers, evals. They start
  file/git-backed and content-hashed, with versions pinned in `agent.lock`. A registry server
  comes later behind the same `kind/name@version` reference format.
- `agentbreeder build agent.yaml` produces `image@sha256` plus the existing deploy manifest.
  `agentbreeder deploy` is unchanged: identity gate, policy, approval, audit. Local first; cloud
  later as one more deploy target.
- **Studio follows the same flow.** It is a UI over the same build/deploy/registries, with:
  - registry browsers: prompts with version diff, tools, indexes, MCP servers, eval sets;
  - deployments: status, history, gate results, audit;
  - a chat UI that can plug into ANY deployed agent for testing, picking the endpoint and session;
  - no separate template layer. This supersedes spec r2 §3.1(b), §5.1 and §5.5's template parts;
    the rest of r2's security/UX findings still apply. Revise
    `docs/superpowers/specs/2026-11-07-studio-ui-design.md` in Phase B.
- This reverses the rebuild's "manifest has no framework/prompt/RAG fields" rule. The authoring
  file (`agent.yaml`) is new and separate; the deploy manifest stays image-in. Update `CLAUDE.md`,
  `ARCHITECTURE.md` and `docs/` accordingly in the spec PR.

## Acceptance test (Phase A done)
`~/personal-github/agentbreeder-support-agent` is rebuilt from an `agent.yaml` of about 40 lines,
plus registry entries:
- prompt `northwind-support`;
- tools: kb-search, orders, cancel-order (human approval), notes, escalate, Tavily via MCP or a tool;
- kb `northwind-help` (pgvector, gemini-embedding-2 768-d via LiteLLM);
- evals `northwind-golden` (the 61 cases).

`agentbreeder build` + the eval gate + `agentbreeder deploy --local` must pass **61/61** with no
hand-written agent code. The supporting stack (Postgres/pgvector, LiteLLM, Phoenix) may stay a
Compose file in Phase A; say so honestly.

## Hard rules (CLAUDE.md, binding)
- Never break the `agentbreeder deploy` happy path; never bypass the gate, policy, approval or audit.
- No secrets in images or logs. Local runtime secret files are a prerequisite slice: the r2
  review showed build-arg secrets leak.
- Claim rule: docs, README and website claim only what ran end to end.
- Delivery workflow: epic + sub-issues → spec → design review (architecture, security,
  frontend/UX for Studio) → plan → Codex review loop → TDD slices → `/launch` gate per commit →
  feature branch → PR only when the whole phase passes locally. Merge only with green CI + Codex
  approval. Never `--no-verify`, `--admin` or `--force`.
- Pin dependencies to releases at least two weeks old. Never hard-code cloud provider names; use
  the deployer abstraction.
- Cross-repo: note any `agentbreeder-cloud` / `website/` impact in the spec; update them only for
  what shipped.

## Phase A stages (one per iteration)
1. Track: epic + sub-issues with `gh` (the GitHub MCP server is failing to connect). Comment on
   #718 that Studio now follows this epic.
2. Spec `docs/superpowers/specs/2026-11-07-agent-yaml-build-design.md`. Cover:
   - the `agent.yaml` schema;
   - reference format and lock file;
   - each registry's storage, versioning and integrity;
   - the ADK runtime contract;
   - how tools are packaged (Python package and MCP);
   - KB index build/ingest;
   - the eval gate in `build`/`deploy`;
   - runtime secret files;
   - the doc rule changes;
   - acceptance criteria;
   - the cross-repo split.
3. Design review by two independent subagents (architecture+security; DX/CLI ergonomics). Verify
   their code claims, fold them in. **Pause only if** the review surfaces a decision that changes
   the owner decisions above; otherwise continue.
4. Plan `docs/superpowers/plans/2026-11-07-agent-yaml-build.md`: thin, tested vertical slices,
   with runtime secret files first.
5. Codex review of the plan (`codex review` via a subagent), looped until it converges.
6+. One slice per iteration on branch `feat/agent-yaml-build`: TDD → implement → run for real →
   `/launch` gate → commit. Last slice: the Northwind acceptance test at 61/61 and docs.
   Then open the PR.

## Phase B (Studio), after Phase A's PR is open
Revise the Studio spec to this flow → review → plan → Codex → slices: registry browsers,
deployments, plug-any-agent chat, deploy jobs. Same rules.

## Stop conditions
Stop and report on:
- a decision only the owner can make;
- a gate failing after two fix attempts;
- a needed secret or credential;
- PR ready for merge (merging needs the owner + Codex);
- phase complete.
