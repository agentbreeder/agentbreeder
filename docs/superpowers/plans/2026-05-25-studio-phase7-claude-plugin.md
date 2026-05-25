# Studio Phase 7 — Package `/agent-build` as a Claude Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the repo's `/agent-build` command installable as a distributable Claude Code plugin (the repo doubles as its own marketplace), so external developers can `claude plugin install` it and scaffold AgentBreeder agents from their own Claude Code.

**Architecture:** Per the authoritative Claude Code plugin spec — a self-contained plugin lives at `plugins/agent-build/` with `.claude-plugin/plugin.json` (manifest; only `name` required, commands auto-discovered) + `commands/agent-build.md`. A repo-root `.claude-plugin/marketplace.json` lists it with `source: {type:github, repo:"agentbreeder/agentbreeder", path:"./plugins/agent-build"}`. The command is **copied** from the canonical `.claude/commands/agent-build.md` (kept for in-repo dev), and a **drift-guard test** asserts the two stay byte-identical so they can't fork (per the "fix upstream, don't fork" rule).

**Tech Stack:** JSON manifests + the existing markdown command; a pytest drift guard; docs.

**Branch:** `feat/studio-ux-simplification` (commit per task; no PR until the whole epic passes locally).

---

## File Structure

- `plugins/agent-build/.claude-plugin/plugin.json` — NEW: plugin manifest.
- `plugins/agent-build/commands/agent-build.md` — NEW: copy of the canonical command.
- `.claude-plugin/marketplace.json` — NEW (repo root): marketplace catalog.
- `tests/unit/test_plugin_command_sync.py` — NEW: byte-identical drift guard.
- `scripts/sync-plugin-command.sh` — NEW: one-liner to re-copy after editing the canonical.
- `README.md` + `website/content/docs/how-to.mdx` — add "Use with Claude Code (plugin)" install snippet.

---

### Task 1: Plugin manifest + command copy + marketplace

**Files:** Create `plugins/agent-build/.claude-plugin/plugin.json`, `plugins/agent-build/commands/agent-build.md`, `.claude-plugin/marketplace.json`

- [ ] **Step 1** — Create `plugins/agent-build/.claude-plugin/plugin.json`:

```json
{
  "name": "agent-build",
  "version": "1.0.0",
  "description": "Scaffold an AgentBreeder agent: an interactive architect that recommends framework, model, memory, RAG, and deploy target, then generates agent.yaml + code.",
  "author": { "name": "AgentBreeder" },
  "repository": "https://github.com/agentbreeder/agentbreeder",
  "license": "Apache-2.0"
}
```

(Confirm the repo's actual license from `LICENSE` at the root and match it — adjust the `license` field.)

- [ ] **Step 2** — Copy the canonical command verbatim:

Run: `mkdir -p plugins/agent-build/commands && cp .claude/commands/agent-build.md plugins/agent-build/commands/agent-build.md`

Do NOT edit the copy — it ships verbatim (its repo-specific Step 3 "advisory path" is harmless for external users, and editing would fork it from the canonical).

- [ ] **Step 3** — Create `.claude-plugin/marketplace.json` at the repo root:

```json
{
  "name": "agentbreeder",
  "plugins": [
    {
      "name": "agent-build",
      "source": { "type": "github", "repo": "agentbreeder/agentbreeder", "path": "./plugins/agent-build" },
      "description": "Scaffold an AgentBreeder agent with the /agent-build architect."
    }
  ]
}
```

- [ ] **Step 4** — Validate both JSON files parse: `python -c "import json; json.load(open('plugins/agent-build/.claude-plugin/plugin.json')); json.load(open('.claude-plugin/marketplace.json')); print('ok')"`.
- [ ] **Step 5** — Commit: `git commit -m "feat(plugin): package /agent-build as an installable Claude plugin"`

---

### Task 2: Drift-guard test + sync script

**Files:** Create `tests/unit/test_plugin_command_sync.py`, `scripts/sync-plugin-command.sh`

- [ ] **Step 1: Write the test** — `tests/unit/test_plugin_command_sync.py`:

```python
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

def test_plugin_agent_build_matches_canonical():
    canonical = (REPO / ".claude/commands/agent-build.md").read_text()
    plugin = (REPO / "plugins/agent-build/commands/agent-build.md").read_text()
    assert plugin == canonical, (
        "plugins/agent-build/commands/agent-build.md has drifted from "
        ".claude/commands/agent-build.md — re-run scripts/sync-plugin-command.sh"
    )

def test_plugin_manifest_is_valid():
    import json
    manifest = json.loads((REPO / "plugins/agent-build/.claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "agent-build"
    market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
    assert any(p["name"] == "agent-build" for p in market["plugins"])
```

(Confirm `parents[2]` resolves to the repo root from `tests/unit/`; adjust the index if needed.)

- [ ] **Step 2** — Run: `venv/bin/python -m pytest tests/unit/test_plugin_command_sync.py -v` → PASS (the Task-1 copy is identical).
- [ ] **Step 3** — Create `scripts/sync-plugin-command.sh` (idempotent re-copy + a note it's run after editing the canonical):

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
cp .claude/commands/agent-build.md plugins/agent-build/commands/agent-build.md
echo "Synced plugins/agent-build/commands/agent-build.md from .claude/commands/agent-build.md"
```

Make it executable: `chmod +x scripts/sync-plugin-command.sh`.

- [ ] **Step 4** — Commit: `git commit -m "test(plugin): guard plugin command against drift from canonical + sync script"`

---

### Task 3: Docs — install snippet

**Files:** Modify `README.md`, `website/content/docs/how-to.mdx`

- [ ] **Step 1** — In `README.md`, add a short "Use with Claude Code" section:

```markdown
## Use with Claude Code

Install the `/agent-build` architect as a Claude Code plugin:

\`\`\`bash
claude plugin marketplace add agentbreeder/agentbreeder
claude plugin install agent-build@agentbreeder
\`\`\`

Then run `/agent-build` in Claude Code to scaffold an agent (it recommends framework, model, memory, RAG, and deploy target, then generates `agent.yaml` + code).
```

- [ ] **Step 2** — In `website/content/docs/how-to.mdx`, expand the existing one-line `/agent-build` mention (~line 85) into the same install + usage snippet.
- [ ] **Step 3** — Commit: `git commit -m "docs: install /agent-build as a Claude Code plugin"`

---

### Task 4: Verify

- [ ] **Step 1** — `venv/bin/python -m pytest tests/unit/test_plugin_command_sync.py -v` (green); JSON validates (Task 1 Step 4). If `claude` CLI is available in the environment, optionally `claude plugin validate ./plugins/agent-build` (or `claude --help | grep plugin`) — but do not block on it if the CLI/subcommand isn't present; report whether it was runnable.

---

## Self-Review

**Spec coverage (Phase 7):** `/agent-build` packaged as an installable plugin via `plugins/agent-build/` + repo-root marketplace ✓; reuses the canonical command verbatim (no fork) with a drift-guard test ✓; install docs added ✓. Authoritative manifest/marketplace shapes from the Claude Code plugin spec.

**Placeholder scan:** the JSON is concrete; the license/`parents[2]`/existing-doc-line are flagged as "confirm against the repo" verification steps. No vague items.

**Type/name consistency:** plugin name `agent-build` is used identically across `plugin.json`, `marketplace.json`, the test, and the install docs. The canonical path `.claude/commands/agent-build.md` and the plugin path `plugins/agent-build/commands/agent-build.md` are referenced consistently in the guard test + sync script.
