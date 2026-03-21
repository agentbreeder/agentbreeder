---
name: Three-Tier Builder Model Decision
description: AgentBreeder supports No Code / Low Code / Full Code for both agent development and orchestration — decided 2026-03-12
type: project
---

AgentBreeder uses a three-tier builder model for both individual agent development AND multi-agent orchestration:
- **No Code** (visual UI, drag-and-drop) → generates YAML
- **Low Code** (YAML in any IDE or dashboard editor) → is YAML
- **Full Code** (Python/TS SDK) → generates YAML + bundles custom code

All three tiers compile to the same internal format and share the same deploy pipeline.

**Why:** Real teams have PMs (No Code), ML engineers (Low Code), and senior engineers (Full Code). Tier mobility (eject from UI → YAML → SDK) prevents lock-in and is the key differentiator vs. competitors.

**How to apply:**
- The deploy pipeline must NEVER contain tier-specific logic
- No Code must always generate valid, human-readable YAML
- Full Code SDK must NOT bypass the config parser — it generates agent.yaml + code
- Visual builder layout metadata goes in `.agentbreeder/layout.json`, never in agent.yaml
- New milestones: M28 (Python SDK, v0.4), M29 (Orchestration YAML, v1.0), M30 (Visual Orchestration + TS SDK, v1.1), M31 (Full Code Orchestration SDK, v1.3)
