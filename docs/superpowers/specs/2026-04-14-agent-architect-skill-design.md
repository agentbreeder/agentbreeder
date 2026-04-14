# Agent Architect Skill Design

**Date:** 2026-04-14
**Status:** Approved
**Evolves:** `docs/superpowers/specs/2026-04-10-agent-build-skill-design.md`

---

## Summary

Evolve the existing `/agent-build` Claude Code skill into an **AI Agent Architect** that combines an advisory interview with full project scaffolding. The skill opens with a fork: users who know their stack get the existing fast path; users who want recommendations go through a structured 6-question interview that produces framework, model, RAG, memory, MCP/A2A, deployment, and eval recommendations — each with explicit reasoning. Scaffolding then generates a complete, tier-interoperable agent project plus IDE config files (CLAUDE.md, AGENTS.md, .cursorrules, .antigravity.md) for both the new agent project and the AgentBreeder repo.

---

## Approach

Single skill (`/agent-build`) with an opt-in advisory mode. The advisory output feeds directly into scaffolding — no handoff gap. Users who already know their stack are not forced through the advisory flow. The recommendation logic is a deterministic decision engine driven by the 6-question interview answers.

---

## Conversation Flow

### Entry fork

```
"Do you already know your stack, or do you want me to recommend the best setup for your use case?"
  (a) I know my stack → existing fast path (name → framework → cloud → tools → team → scaffold)
  (b) Recommend for me → advisory interview (6 questions below)
```

### Advisory Interview (option b)

Questions are asked one at a time.

**Q1 — Business goal**
Free text. "What problem does this agent solve, and for whom?"
Example answers: "reduce tier-1 support tickets for our SaaS product", "automate weekly financial reporting for the CFO"
Purpose: surfaces domain, end-user, and implicit tool/data needs before asking about them explicitly. Feeds directly into eval dimension selection.

**Q2 — Technical use case**
Free text. "What does the agent need to do, step by step?"
Purpose: maps the workflow into framework and mode signals.

**Q3 — State complexity**
Multiple choice (select all that apply):
- (a) Loops or retries
- (b) Checkpoints / resume from failure
- (c) Human-in-the-loop approvals
- (d) Parallel branches
- (e) None of the above

**Q4 — Team and org context**
Two sub-questions (combined in one message):
- Primary cloud: (a) AWS (b) GCP (c) Azure (d) local/none
- Language preference: (a) Python (b) TypeScript (c) No preference

**Q5 — Data access**
Multiple choice (select all that apply):
- (a) Unstructured docs / PDFs / text files
- (b) Structured database (SQL/NoSQL)
- (c) Knowledge graph / relationship data
- (d) Live APIs or web
- (e) None

**Q6 — Scale profile**
Single choice:
- (a) Real-time interactive (users waiting for responses)
- (b) Async batch (scheduled or queue-driven)
- (c) Event-driven (triggered by external events)
- (d) Internal tooling / low-volume

### Recommendations Summary

After Q6, the skill produces a structured summary with reasoning before scaffolding:

```
Framework:   LangGraph (Full Code)
             → You need checkpoints + human-in-the-loop (Q3b, Q3c). LangGraph is
               the only framework with native StateGraph checkpointing and
               interrupt() for HITL. CrewAI would require custom workarounds.

Model:       claude-sonnet-4-6
             → Real-time interactive (Q6a) + tool use. Opus is overkill for
               latency-sensitive tool-calling; Haiku lacks reasoning depth for
               multi-step planning.

RAG:         Vector DB (pgvector)
             → Unstructured docs (Q5a) + semantic search. pgvector is default;
               Pinecone/Weaviate recommended if corpus > 1M documents.

Memory:      Short-term (Redis) + Long-term (PostgreSQL)
             → Real-time interactive suggests per-session context (Redis).
               Business goal implies cross-session user state (PostgreSQL).

MCP/A2A:     MCP for external tools
             → Live APIs selected (Q5d). No agent delegation signals → A2A not needed.

Deploy:      AWS ECS Fargate
             → AWS (Q4) + persistent real-time service (Q6a) + Python.

Evals:       Deflection rate, escalation accuracy, CSAT proxy, PII non-leakage, tone
             → Derived from business goal: "reduce support tickets".

Override any recommendation before scaffolding begins? (y/n per item)
```

---

## Recommendation Logic

### Framework + Mode

| Signals | Recommendation |
|---|---|
| Checkpoints + HITL + conditional routing (Q3b, Q3c) | LangGraph — Full Code |
| Multiple specialized agents + crew coordination (Q2) | CrewAI — Full Code (complex) or Low Code (simple pipelines) |
| Native Claude tool use + adaptive thinking (Q2 + no state complexity) | Claude SDK — Full Code |
| GCP cloud (Q4) or Google Workspace / Vertex AI in Q2 | Google ADK — Low Code or Full Code |
| TypeScript preference (Q4) or OpenAI ecosystem in Q2 | OpenAI Agents SDK — Full Code |
| Simple sequential flow, no state complexity (Q3e) | Low Code YAML (any framework) |
| PM / analyst / citizen builder persona implied by Q2 | No Code (visual builder) |

**Full Code trigger rule:** any two of — loops/retries, checkpoints, HITL, parallel branches. One or zero → Low Code is sufficient.

### Model

| Signals | Recommendation |
|---|---|
| Multi-step planning, complex reasoning, research domain | claude-opus-4 |
| Balanced tool use + interactive speed (default) | claude-sonnet-4-6 |
| High-throughput, cost-sensitive, structured tasks | claude-haiku-4-5 |
| GCP cloud (Q4) | gemini-2.5-pro (reasoning) or gemini-2.0-flash (speed) |
| TypeScript + OpenAI ecosystem | gpt-4o (default) or o3 (complex reasoning) |

Claude SDK agents always get `prompt_caching: true` and `thinking: adaptive` in `agent.yaml`.

### RAG Type

| Signals | Recommendation |
|---|---|
| Unstructured docs (Q5a) | Vector DB RAG (pgvector default; Pinecone/Weaviate at scale) |
| Relationship queries / knowledge graph (Q5c) | Graph RAG (Neo4j + LangGraph) |
| Both unstructured + relationship (Q5a + Q5c) | Hybrid RAG |
| Structured DB only (Q5b) | SQL tool (not RAG — direct DB access via tool) |
| No complex retrieval | None |

### Memory

| Signals | Recommendation |
|---|---|
| Within-session context only | Short-term (Redis) |
| Cross-session user state / preferences implied by business goal | Long-term (PostgreSQL) |
| Both signals present | Short-term + Long-term |
| Stateless API agent or batch (Q6b) | None |

### MCP vs A2A

| Signals | Recommendation |
|---|---|
| Live APIs / external tools (Q5d or implied by Q1/Q2) | MCP servers |
| Delegates subtasks to other specialized agents (Q2) | A2A protocol |
| Both | MCP + A2A |
| Self-contained, no external calls | Neither |

### Deployment

| Signals | Recommendation |
|---|---|
| AWS + persistent service + Python (Q6a) | ECS Fargate |
| AWS + event-driven / infrequent (Q6c) | Lambda (planned) |
| GCP + any | Cloud Run |
| Local / no cloud (Q4d) | Docker Compose |
| Kubernetes / enterprise | EKS/GKE (flagged as planned, not yet available) |

### Eval Dimensions

| Business goal domain | Eval dimensions |
|---|---|
| Customer support | Deflection rate, escalation accuracy, CSAT proxy, PII non-leakage, tone |
| Financial / reporting | Numerical accuracy, schema correctness, completeness, hallucination rate |
| Code / dev tooling | Correctness, security (no injection), format compliance, test pass rate |
| Research / knowledge | Citation accuracy, hallucination rate, completeness, source relevance |
| Data pipeline | Schema validation, row completeness, latency, error rate |
| Sales / CRM | Lead scoring accuracy, email tone, compliance (opt-out handling) |

---

## Scaffolded Agent Project

```
<agent-name>/
├── agent.yaml                  # Canonical config (Low or Full Code tier)
├── agent.py                    # Working agent code (framework-specific)
├── requirements.txt            # Framework-pinned deps
├── Dockerfile                  # Local testing + cloud deploy
├── docker-compose.yml          # docker compose up with correct ports/env/health checks
├── .env.example                # API key template (model + tools)
│
├── .agentbreeder/
│   └── layout.json             # Visual builder canvas metadata (No Code tier)
│
├── memory/                     # Only generated if memory recommended
│   └── config.py               # Redis (short-term) and/or PostgreSQL (long-term) setup
│
├── rag/                        # Only generated if RAG recommended
│   ├── index.py                # Vector DB or Graph RAG setup
│   └── ingest.py               # Document ingestion starter
│
├── tools/                      # One stub per tool described in Q1/Q2
│   └── <tool-name>.py          # Typed + docstrings, matching described purpose
│
├── mcp/                        # Only generated if MCP recommended
│   └── servers.yaml            # MCP server references
│
├── tests/
│   ├── test_agent.py           # Smoke test: loads agent, validates response
│   └── evals/
│       ├── eval_runner.py      # Framework-specific harness (LangSmith / Inspect AI / PromptFoo)
│       └── criteria.md         # Use-case eval dimensions + starter test cases
│
├── ARCHITECT_NOTES.md          # Why each recommendation was made (onboarding reference)
│
├── CLAUDE.md                   # Agent-specific AI context for Claude Code
├── AGENTS.md                   # AI skill roster for iterating on this agent
├── .cursorrules                # Cursor IDE rules: framework idioms + banned patterns
├── .antigravity.md             # Hard constraints: what NOT to do
│
└── README.md                   # Quick start: install → configure → run → deploy
```

---

## IDE Config File Contents

### CLAUDE.md (inside agent project)

Three sections: what the agent does, stack summary, rules for AI-assisted development.
- Stack section lists framework + key patterns, model + caching config, RAG setup, memory setup, deploy target
- Rules section: where new tools go, test requirements, validate-before-commit, RBAC/eval gates

### AGENTS.md (inside agent project)

Skill roster mirroring AgentBreeder's own AGENT.md pattern, scoped to this agent:
- `add-tool` — add a new tool; files: tools/, agent.yaml, tests/
- `update-prompt` — revise system prompt; files: agent.yaml prompts section
- `add-eval` — add eval case; files: tests/evals/
- `update-rag` — change RAG index/ingestion; files: rag/
- `deploy` — validate then deploy; commands: agentbreeder validate && agentbreeder deploy

### .cursorrules (inside agent project)

Framework-specific Cursor rules. Content varies per framework:
- LangGraph: TypedDict state, async node signatures, interrupt() for HITL, checkpoint backends
- CrewAI: Task/Agent/Crew patterns, YAML config location, callback hooks
- Claude SDK: tool binding, cache_control, adaptive thinking config
- Google ADK: session backend config, ADK tool decorators, Vertex AI integration
- OpenAI Agents: handoff patterns, tool schema conventions, streaming

### .antigravity.md (inside agent project)

Hard constraints in four categories:
- Model: no silent model swaps without re-running evals, no disabling thinking in production
- Memory: PII TTL enforcement, unbounded memory prevention
- Tools: no tools without tests, no direct API calls bypassing tools/
- Deploy: validate gate required, no hardcoded credentials, no skipping health checks, no eval regression

### AgentBreeder repo updates

- `AGENT.md`: new `build:agent-scaffold` skill entry in the Build category documenting the advisory flow
- `CLAUDE.md`: note appended that /agent-build generates per-agent IDE config files following the repo's patterns

---

## ARCHITECT_NOTES.md Structure

```markdown
# Architect Notes — <agent-name>
Generated by /agent-build on <date>. Edit freely — this file is for humans.

## Business Goal
<Q1 answer verbatim>

## Why <Framework> (<Mode>)?
<Reasoning from recommendation engine>

## Why <Model>?
<Reasoning from recommendation engine>

## Why <RAG type>? / Why no RAG?
<Reasoning>

## Why <Memory config>? / Why no memory?
<Reasoning>

## Why <MCP/A2A/neither>?
<Reasoning>

## Why <Deploy target>?
<Reasoning>

## Eval Dimensions
<Dimensions list with one-line rationale per dimension>

## Overrides Made
<Any recommendations the user changed, with their stated reason>
```

---

## Skill Location and Invocation

- Skill file: `.claude/commands/agent-build.md` (updated in-place)
- Invocation: `/agent-build` in Claude Code
- No new commands introduced — the fork question handles mode selection

---

## What Does Not Change

- The fast path (option a) is byte-for-byte identical to today's flow
- `agent.yaml` schema is unchanged
- `layout.json` schema is unchanged
- The deploy pipeline does not know which path was taken — governance is always enforced

---

## Out of Scope

- Actual agent runtime code generation quality improvements (separate effort)
- Visual builder UI changes
- A2A protocol implementation (separate milestone)
- Kubernetes deployer (planned, flagged in recommendations but not implemented)
