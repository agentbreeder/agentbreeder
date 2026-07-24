# CLAUDE.md — AgentBreeder

> **This file is a thin router.** Only always-on invariants + standing
> instructions live here; the reference bodies (project structure, agent.yaml
> spec, architecture, coding standards, MCP servers, API conventions, etc.)
> were moved to `docs/` to cut the per-turn token cost (~12.2k → ~2.5k).
> **Read the linked doc BEFORE you touch its area** — the pointers are not
> optional; a rule you skip is still binding.

## 🧠 What is AgentBreeder?

AgentBreeder is an **open-source platform** for building, deploying, and governing enterprise AI agents.

**Core tagline:** Define Once. Deploy Anywhere. Govern Automatically.

**The one-sentence pitch:** A developer writes one `agent.yaml` file, runs `agentbreeder deploy`, and their agent is live on AWS or GCP — with RBAC, cost tracking, audit trail, and org-wide discoverability automatic and zero extra work.

**What makes it unique:**
- Framework-agnostic (LangGraph, CrewAI, Claude SDK, OpenAI Agents, Google ADK, Custom)
- Multi-cloud first (AWS ECS Fargate/App Runner/EKS, GCP Cloud Run/GKE, Azure Container Apps, and Kubernetes as equal first-class targets)
- Governance is a **side effect** of deploying, not extra configuration
- Shared org-wide registry for agents, prompts, tools/MCP servers, models, knowledge bases
- **Three builder tiers** for both agent development AND agent orchestration:
  - **No Code** — Visual drag-and-drop UI, registry pickers, ReactFlow canvas (for PMs, analysts, citizen builders)
  - **Low Code** — YAML config (`agent.yaml`, `orchestration.yaml`) in any IDE or the dashboard editor (for ML engineers, DevOps)
  - **Full Code** — Python/TS SDK with full programmatic control, custom routing, state machines (for senior engineers, researchers)
- All three tiers compile to the same internal format and share the same deploy pipeline, governance, and observability
- **Tier mobility** — start No Code, eject to YAML, eject to Full Code. No vendor lock-in at any level.

---

## 🚫 Common Mistakes to Avoid

1. **Never skip RBAC validation** — every deploy MUST check permissions, even in tests (mock it, don't skip it)
2. **Never write to the registry directly** — always use registry service classes
3. **Never hard-code cloud provider names** — use the deployer abstraction
4. **Never put framework-specific logic in `engine/builder.py`** — it belongs in `engine/runtimes/`
5. **Never commit secrets or credentials** — use `.env` and Secrets Manager references
6. **Never use synchronous I/O in async FastAPI handlers** — always `await` or use `run_in_executor`
7. **Never break the `agentbreeder deploy` happy path** — it is the product; protect it like an API contract
8. **Never merge without tests** — CI blocks PRs with < 80% coverage on changed files

---

## 🚦 Standard Delivery Workflow (Definition of Done)

**Every enhancement or fix follows this pipeline end-to-end.** Each step gates the next — don't skip. This is the standard process across the AgentBreeder repos and must stay identical in both `CLAUDE.md` files.

1. **Track it** — create a GitHub **epic + sub-issues** for the change (one epic per enhancement/fix; sub-issues per milestone/slice). No substantial work without an issue.
2. **Spec it** — write a spec *before* code (problem, goal, scope, acceptance criteria, cross-repo split). Store under `docs/superpowers/specs/`. Use the `brainstorming` → `spec` skills.
3. **Design-review the spec** through the required lenses, folding findings back in: **`/architect`** (architecture), **`frontend-design`** (visual/UX), **`ui-ux-pro-max`** (UI/UX Pro Max), and **`/security`**. For a user-facing/marketing surface also run **`marketing-ideas` / `seo-audit` / `ai-seo`**.
4. **Plan it** — write the implementation details (tasks, sequencing, cross-repo split) under `docs/superpowers/plans/`.
5. **Codex-reviews the plan/implementation** — hand it to **Codex** (`codex review`), apply valid findings, **re-review in a loop until it converges** (see *AI Harnesses & Code Review* below).
6. **Gate it** — after implementation run the **`/launch` quality gate** (tests ≥ threshold, security 0 critical/high, build, Docker, cloud-security). Enforced by the pre-commit gate hook — a commit/push is blocked until all gates pass for the exact tree.
   - **Exception — internal docs/inert-tooling changes only** (`CLAUDE.md`/`AGENTS.md`/plan/spec markdown, `.gitignore`): skip the full multi-gate suite and instead get a `codex review --uncommitted` pass, then run `python3 ~/100xprism/hooks/gate-pass.py` to unblock the commit hook. **Never extend this exception to CI workflows (`.github/workflows/*.yml`), `Dockerfile`, `docker-compose.yml`, or anything else that controls build/test/deploy behavior** — those go through the full gate like application code, since a broken pipeline config is exactly what the gate exists to catch.
   - **`.gitignore` specifically:** a bad ignore rule can hide secrets or drop tracked source, so treat any `.gitignore` edit as security-sensitive even under this exception — the Codex review must explicitly verify (a) no previously-committed pattern that protected a real secret was weakened, and (b) no newly-untracked file exposes one. A generic "looks fine" Codex pass is not enough; ask Codex (or the security workflow) to check both directions before recording the gate pass.
7. **Branch + PR** — conventional-named feature branch; open a PR (stack PRs when milestones build on each other).
8. **Merge gate** — merge **only when CI is green AND Codex has approved.** Both are required.
9. **Auto-merge** — once (8) holds, enable **auto-merge** (squash) so it lands as soon as required checks pass.

For changes spanning OSS/Cloud/Website, run this pipeline per the **Cross-Repo Sync Policy** below and keep issues, terminology, and PRs aligned across repos.

## 🤖 AI Harnesses & Code Review (Claude + Codex)

This repo is worked on by **two AI coding harnesses**, and both guidance files must stay in sync:
- **Claude Code** reads `CLAUDE.md` (this file).
- **Codex** reads `AGENTS.md` — a **100xprism-generated skills/command catalog** (source: `.agents/skills/*/SKILL.md`), **not** a copy of these rules. Never hand-edit it; refresh it with **`100xprism update`**. Repo rules live in `CLAUDE.md` (the source of truth for **both** harnesses) — Codex reads `CLAUDE.md` directly, so rules added here reach it. Note: `/update-claude` edits *`CLAUDE.md`*; it does **not** regenerate `AGENTS.md`.

**Codex is available as a second reviewer.** The `codex` CLI is installed and logged in. Use it for an independent, non-interactive review of a branch/PR before merge:

```bash
codex review --base main        # review current branch vs main
codex review --uncommitted      # review staged/unstaged/untracked changes (use before the first commit of a docs/config-only fix — see gate exception above)
# NOTE: do NOT pass a custom prompt string together with --base (they conflict).
```

Recommended flow: **hand a PR to Codex, apply its valid findings, re-review, and loop until it converges** (no substantive findings left). Codex reviews are read-only. Prefer running Codex inside a subagent so its output is triaged and kept out of the main context.

---

## 🔄 Cross-Repo Sync (Standing Instruction)

AgentBreeder ships across **three repositories** that must stay in sync at all times:

| Repo | Path | Role |
|------|------|------|
| `agentbreeder` (this repo) | `/Users/rajit/personal-github/agentbreeder` | OSS CLI + engine + connectors — source of truth |
| `agentbreeder-cloud` | `/Users/rajit/personal-github/agentbreeder-cloud` | SaaS managed platform — deploys on top of agentbreeder packages |
| `website` | `website/` subdirectory | agentbreeder.io marketing + docs site |

**Rules — before closing any PR:**
- **Schema change** (`agent.yaml`, API shape, CLI flags): grep `agentbreeder-cloud` for affected fields and update them
- **New connector or feature**: check if `agentbreeder-cloud` needs to expose it
- **Version bump**: update `website/components/footer.tsx` version badge, update features/docs pages
- **Breaking change**: `agentbreeder-cloud` must get a companion PR before or alongside
- **Update the guides in BOTH repos**: any feature that changes cross-repo behaviour MUST update `CLAUDE.md` in **both** `agentbreeder` and `agentbreeder-cloud`, spelling out *what changes on each side* (see "Documenting a cross-repo feature" below). `AGENTS.md` is a 100xprism-generated skills catalog (`# Source of truth: modules/<slug>/SKILL.md`) — **do not hand-edit it**; refresh it with `100xprism update`. It does **not** carry these rules; put them in `CLAUDE.md`, which both harnesses use (Codex reads `CLAUDE.md` directly). `/update-claude` edits `CLAUDE.md`, not `AGENTS.md`.

**Cross-repo detail** (bidirectional feature-documentation table + the
`brand.css` design-system source-of-truth #583) lives in
`docs/cross-repo-sync.md` — read it before any schema/API/branding change.

## 🎯 When Adding a New Feature

1. **Check the ROADMAP.md** — is this feature planned? Which milestone?
2. **Check AGENT.md** — which AI skills/agents can help build it?
3. **Use `sequential-thinking` MCP** — plan before coding for anything > 100 lines
4. **Write the test first** — TDD is strongly preferred for engine and API code
5. **Update the JSON Schema** — if you changed `agent.yaml` fields
6. **Update the docs** — if you changed a public API or CLI command
7. **Add an example** — if you added a new framework or deployer, add it to `examples/`
8. **Scaffold with `/agent-build`** — when starting a new agent project, run `/agent-build` in Claude Code. The Advisory Path generates IDE config files (`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.antigravity.md`) tailored to the chosen framework, model, and deployment target. These files give Claude and Cursor context-aware guidance for the specific agent being built.
9. **Sync the guides both ways** — if the feature touches Cloud or the website, update `CLAUDE.md` in **both** repos per the Cross-Repo Sync table above (what changes OSS→Cloud, Cloud→OSS, and →Website). `AGENTS.md` is a generated catalog (refresh via `100xprism update`, never hand-edit) and does not hold these rules.

---

## Resume & live state (read FIRST when resuming)

- **`RESOLVE.md`** — the live loop cursor (current epic / stage / NEXT ACTION).
  Read it INSTEAD of scanning `ROADMAP.md` (3.4k lines / ~50k tok) to find
  what's live. Update its `## LIVE` block at every stage boundary, then
  `/clear` (free) rather than `/compact` (paid). `ROADMAP.md` is history only.

## Reference router — read the doc before working in its area

| When you are… | Read FIRST |
|---|---|
| navigating the repo layout / finding a module | `docs/project-structure.md` |
| reasoning about tech stack, architecture, naming, packaging | `docs/architecture.md` |
| running dev commands or setting env vars | `docs/commands.md` |
| authoring/validating an `agent.yaml` | `docs/agent-yaml-spec.md` |
| wiring or using an MCP server | `docs/mcp-servers.md` |
| writing Python/TS/React code (style, tests) | `docs/coding-standards.md` |
| designing/changing an API route | `docs/api-conventions.md` |
| a schema/API/branding change that crosses repos | `docs/cross-repo-sync.md` |
| onboarding as a contributor | `docs/contributing.md` |
