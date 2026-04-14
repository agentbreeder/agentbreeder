# Design: Agent Architect Docs & Homepage Animation

**Date:** 2026-04-14
**Feature:** M35 follow-on — documentation and homepage animation for `/agent-build` advisory mode
**Branch:** `feature/website`
**Files changed:**
- `docs/index.md` — new animation section
- `docs/how-to.md` — updated "Build Your First Agent" + new `/agent-build` section

---

## Overview

M35 shipped the `/agent-build` advisory architect skill. This design covers the two user-facing documentation artifacts that make it discoverable and understandable:

1. **Homepage animation** — a self-contained split-screen demo embedded in `docs/index.md` showing the advisory flow from interview → recommendations → scaffold → deploy, autoplay loop.
2. **How-to documentation** — update existing "Build Your First Agent" to lead with `/agent-build`, plus a new dedicated section with full Fast Path and Advisory Path walkthroughs.

---

## Part 1: Homepage Animation

### Placement

Inserted as a new `## From Idea to Deployed Agent` section in `docs/index.md`, between the "Why AgentBreeder?" comparison table and the "Three Builder Tiers" tabbed section.

**Rationale:** The reader understands the value prop from the table first, then sees a live demo of the highest-DX feature. Gives the animation context without burying it below the fold.

### Section copy

```markdown
## From Idea to Deployed Agent

Not sure which framework, model, or RAG setup is right for your use case?
Run `/agent-build` in Claude Code — it interviews you, recommends the full
stack, and scaffolds a production-ready project in one conversation.
```

Followed immediately by the animation block.

### Animation design

**Style:** Split screen — left panel shows the advisory conversation, right panel shows the project file tree building in real-time.

**Playback:** Autoplay on page load, loops continuously. No user interaction required.

**Cycle length:** ~14 seconds per loop.

**5 steps in sequence:**

| Step | Left panel | Right panel | Duration |
|------|-----------|------------|---------|
| 0 — Invoke | `$ /agent-build` → fork question → user picks `b` | (empty) | ~2s |
| 1 — Interview | 3 Q&A pairs visible; "3 more questions..." | (empty) | ~4s |
| 2 — Recommendations | 7-dimension summary: Framework → Evals | (empty) | ~3s |
| 3 — Scaffold | "override or proceed?" → proceed | 16 files appear one-by-one | ~4s |
| 4 — Deploy | `agentbreeder deploy` → live URL | (complete tree) | ~3s then loop |

**Files shown in right panel (Advisory Path, LangGraph + pgvector + Redis example):**
```
support-agent/
  agent.yaml
  agent.py
  requirements.txt
  .env.example
  Dockerfile
  tools/
    zendesk.py
  rag/
    ingest.py
  tests/
    eval_deflect.py
  ARCHITECT_NOTES.md
  CLAUDE.md
  .cursorrules
  README.md
```

### Implementation

**Technology:** Pure HTML + CSS + JS, no external dependencies. Embedded as a raw HTML block directly in `docs/index.md`. MkDocs Material renders raw HTML blocks transparently.

**Dark/light mode:** The animation uses a fixed dark terminal aesthetic (`#0d1117` background) which matches the dark mode. A `prefers-color-scheme: light` media query adds a subtle border and softens contrast for light mode visitors.

**Accessibility:** The animation block includes `aria-label="Demo: /agent-build advisory flow"` and `role="img"`. A static text fallback is provided in a `<noscript>` block.

**No MkDocs plugin required:** The HTML block uses standard MkDocs Material inline HTML support. No extra `markdown_extensions` needed beyond what the site already uses.

---

## Part 2: how-to.md Updates

### 2a. Update "Build Your First Agent"

**Current state:** The section starts with `### Step 1: Scaffold` which runs `agentbreeder init` manually.

**Change:** Prepend a short lead paragraph that introduces `/agent-build` as the recommended primary path. The existing manual steps become a secondary "or do it manually" flow.

**New opening:**

```markdown
## Build Your First Agent

The fastest way is the AI Agent Architect — run `/agent-build` in Claude Code.
It asks 6 questions (or advises you on the best stack) and generates a
complete project. [Jump to the full walkthrough →](#use-the-agent-architect-agent-build)

Prefer to set things up manually? The steps below walk through each file.
```

### 2b. New section: `## Use the Agent Architect (/agent-build)`

**Placement:** After the existing `## Build Your First Agent` section, before `## Deploy to Different Targets`.

**Content structure:**

#### What it is (short intro paragraph)
`/agent-build` is a Claude Code skill that acts as an AI agent architect. It supports two paths: Fast Path (you know your stack) and Advisory Path (it recommends the best setup for your use case).

#### Fast Path walkthrough
6 questions shown as a `bash` code block simulating the conversation:
- Agent name
- Purpose
- Framework (numbered list)
- Cloud target
- Tools
- Team & owner

Then: confirmation table → scaffold.

#### Advisory Path walkthrough
Full example conversation as a `bash` code block:
- All 6 interview questions (business goal, use case, state complexity, team/cloud, data access, scale)
- Sample Recommendations Summary card (support bot example: LangGraph Full Code, claude-sonnet-4-6, pgvector, Redis short-term, MCP servers, ECS Fargate, deflection-rate + CSAT evals)
- Override step
- Scaffold begins

#### What gets generated (two-column table)

| File / Directory | Purpose |
|-----------------|---------|
| `agent.yaml` | AgentBreeder config — framework, model, deploy, tools, guardrails |
| `agent.py` | Framework entrypoint (LangGraph graph / CrewAI crew / etc.) |
| `tools/` | Tool stub files, one per tool named in the interview |
| `requirements.txt` | Framework + provider dependencies |
| `.env.example` | Required API keys and env vars |
| `Dockerfile` | Multi-stage container image |
| `deploy/` | `docker-compose.yml` or cloud deploy config |
| `criteria.md` | Eval criteria |
| `README.md` | Project overview + quick-start |
| `memory/` | Redis / PostgreSQL setup *(Advisory Path, if recommended)* |
| `rag/` | Vector or Graph RAG index + ingestion *(Advisory Path, if recommended)* |
| `mcp/servers.yaml` | MCP server references *(Advisory Path, if recommended)* |
| `tests/evals/` | Eval harness + use-case criteria *(Advisory Path)* |
| `ARCHITECT_NOTES.md` | Reasoning behind every recommendation *(Advisory Path)* |
| `CLAUDE.md` | Agent-specific Claude Code context *(Advisory Path)* |
| `AGENTS.md` | AI skill roster for iterating on this agent *(Advisory Path)* |
| `.cursorrules` | Framework-specific Cursor IDE rules *(Advisory Path)* |
| `.antigravity.md` | Hard constraints for this agent *(Advisory Path)* |

#### Next steps after scaffolding

```bash
cd support-agent/
agentbreeder validate          # check agent.yaml schema
agentbreeder deploy            # deploy locally or to cloud
agentbreeder chat              # test the agent interactively
```

---

## Out of Scope

- Changes to `quickstart.md`, `cli-reference.md`, or any other doc pages
- Any changes to the animation content based on runtime data (static/hardcoded only)
- Separate `/agent-build` docs page (may be added in a future milestone)

---

## Acceptance Criteria

- [ ] Animation loads and autoplays on `docs/index.md` homepage
- [ ] Animation loops continuously without flickering or layout shift
- [ ] Animation section sits between "Why AgentBreeder?" and "Three Builder Tiers"
- [ ] `how-to.md` "Build Your First Agent" opens with `/agent-build` lead paragraph
- [ ] New `## Use the Agent Architect (/agent-build)` section exists with both path walkthroughs
- [ ] What-gets-generated table covers all 19 scaffold outputs
- [ ] No new MkDocs plugins or dependencies required
- [ ] Site builds cleanly with `mkdocs build`
