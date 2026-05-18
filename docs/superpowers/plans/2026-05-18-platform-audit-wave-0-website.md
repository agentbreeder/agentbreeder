# Platform Audit — Wave 0 (Website Corrections) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the website docs into 100% factual alignment with the current AgentBreeder implementation as of 2026-05-18. Quickstart and how-to are the highest-priority surfaces; the stale-deploy-target sweep covers 9 additional pages.

**Architecture:** Pure documentation changes — no runtime/code/schema impact. Each task touches one or two `.mdx` files under `website/content/docs/`. Verification is via `grep` checks for required strings and `npm run build` from `website/` to ensure no MDX/link breakage. The website auto-deploys on push to `main` (per `CLAUDE.md`); there is no separate deploy step.

**Tech Stack:** Fumadocs MDX (`fumadocs-mdx@^14`), Next.js 16. Project is at `website/`. **`website/AGENTS.md` warns "This is NOT the Next.js you know"** — do not rely on Next.js training-data conventions; read `node_modules/next/dist/docs/` if you touch routing/build code (we don't here).

**Audit-spec corrections discovered during planning:** Three Wave 0 tasks from the audit spec were re-scoped after verifying against the codebase and memory observations:

| Spec item | Status | Reason |
|-----------|--------|--------|
| W-03 (`registry agent invoke` non-existent) | **Dropped — invalid finding** | Command exists at `cli/commands/registry_cmd.py:350` (`@agent_app.command("invoke")`). The how-to.mdx example is correct. |
| W-04 (stale `rajits/` namespace) | **Promoted to human-review (HR-7)** | `rajits/` is the *current* namespace in `.github/workflows/release.yml`. Migration to `agentbreeder/` is a 15-file cross-cutting change (per memory observation #13674) that requires Docker Hub coordination — out of scope for a docs-only Wave 0. |
| W-05 (deployment-targets row) | **Expanded** | Per memory observation #27759, stale claims span 10+ pages, not just `how-to.mdx`. Sweep is split into one task per affected page. |
| W-06 (verify `--reset` description) | **Merged into Task 1** | `quickstart.mdx` already documents `--reset` correctly; only `cli-reference.mdx` is missing it. |

Net Wave 0: **13 tasks** — 2 quickstart/how-to fixes + 11 deploy-targets-sweep pages.

---

## File Structure

Files modified in this wave (no files created):

| Task | File | Responsibility |
|------|------|----------------|
| 1 | `website/content/docs/cli-reference.mdx` | Quickstart flags table |
| 2 | `website/content/docs/how-to.mdx` | Migration table links + deploy-targets table |
| 3 | `website/content/docs/cli-reference.mdx` | Stale deploy-target mentions |
| 4 | `website/content/docs/agent-yaml.mdx` | Stale deploy-target mentions |
| 5 | `website/content/docs/no-code.mdx` | Stale deploy-target mentions |
| 6 | `website/content/docs/low-code.mdx` | Stale deploy-target mentions |
| 7 | `website/content/docs/examples.mdx` | Stale deploy-target mentions |
| 8 | `website/content/docs/deployment.mdx` | Stale deploy-target mentions |
| 9 | `website/content/docs/index.mdx` | Stale deploy-target mentions |
| 10 | `website/content/docs/secrets.mdx` | Stale auto-mirror claim |
| 11 | `website/content/docs/sidecar.mdx` | Stale deploy-target mentions |
| 12 | `website/content/docs/migrations.mdx` | Stale deploy-target mentions |
| 13 | (consolidation) | Run `npm run build` end-to-end + final commit |

**Standard verification pattern** used throughout this plan:

```bash
# Verification = a grep check + the website build.
# Run from repo root unless noted.
cd website
npm run build 2>&1 | tail -20    # must end with "✓ Compiled successfully"
cd ..
```

If you don't have `node_modules/` yet:

```bash
cd website && npm install && cd ..
```

**Standard truth-statement** to use whenever an `.mdx` page lists deploy targets:

> Currently shipped: **Local** (Docker Compose) and **GCP Cloud Run** are fully provisioned end-to-end.
> Other targets (AWS ECS Fargate, AWS App Runner, Azure Container Apps, Kubernetes/EKS/GKE/AKS, Claude Managed Agents) have working deployer implementations but require **existing infrastructure** (VPC, subnets, IAM roles, resource group, etc.) — greenfield provisioning is not yet supported. See [deployment.mdx](deployment) for prerequisites per target.

Use this exact wording (or a one-sentence pointer to `deployment.mdx`) every time, for consistency across pages.

---

## Task 1: Add missing quickstart flags to `cli-reference.mdx`

**Why:** `quickstart.mdx` documents `--reset`, `--no-ollama`, and `--ollama-model NAME` (lines 67–71). `cli/commands/quickstart.py:1488–1500` confirms these flags exist. But `cli-reference.mdx` (lines 42–50) only lists `--cloud`, `--no-browser`, `--skip-seed`, `--dev`. A user landing on the reference page would believe the other three don't exist.

**Files:**
- Modify: `website/content/docs/cli-reference.mdx:42-50` (synopsis + flags table)

- [ ] **Step 1: Capture failing check**

```bash
grep -E '\| `--reset`|\| `--no-ollama`|\| `--ollama-model' website/content/docs/cli-reference.mdx
```

Expected: **no output** (the flags are missing).

- [ ] **Step 2: Modify the synopsis line**

In `website/content/docs/cli-reference.mdx`, replace:

```
agentbreeder quickstart [--cloud TARGET] [--no-browser] [--skip-seed] [--dev]
```

with:

```
agentbreeder quickstart [--cloud TARGET] [--no-browser] [--skip-seed] [--dev] [--reset] [--no-ollama] [--ollama-model NAME]
```

- [ ] **Step 3: Append three rows to the flags table**

In the same file, after the existing `| --dev | Off | Build API and Dashboard from local source instead of pulling images |` row, append:

```
| `--reset` | Off | Tear down all docker volumes (postgres, redis, chromadb, neo4j) and start fresh |
| `--no-ollama` | Off | Skip the Ollama install/start/pull bootstrap step |
| `--ollama-model` | `llama3.2:3b` | Pull a different default model for Ollama (e.g. `llama3.2`, `phi4-mini`) |
```

(Verify the default `--ollama-model` value by inspecting `cli/commands/quickstart.py:1500`. If the default differs, use the actual default from code.)

- [ ] **Step 4: Verify the grep now passes**

```bash
grep -E '\| `--reset`|\| `--no-ollama`|\| `--ollama-model' website/content/docs/cli-reference.mdx
```

Expected: three matching lines.

- [ ] **Step 5: Build the site**

```bash
cd website && npm run build 2>&1 | tail -20 && cd ..
```

Expected: ends with `✓ Compiled successfully` (or equivalent — no MDX parse errors).

- [ ] **Step 6: Commit**

```bash
git add website/content/docs/cli-reference.mdx
git commit -m "docs(cli-reference): document quickstart --reset, --no-ollama, --ollama-model flags"
```

---

## Task 2: Fix migration table links and deploy-target column in `how-to.mdx`

**Why:** Two issues on the same page so we land them together.

1. The migration table (lines 409–413) links to `migrations/FROM_LANGGRAPH.md` etc., but the actual files are `migrations/from-langgraph.mdx` etc. (verified by `ls website/content/docs/migrations/`). Dead links today.
2. The deploy-targets section (line 92) lists AWS/Azure/K8s/Claude Managed without prerequisite context — users may try `agentbreeder deploy --target ecs-fargate` against a fresh AWS account and hit silent failure.

**Files:**
- Modify: `website/content/docs/how-to.mdx:409-413` (migration link table)
- Modify: `website/content/docs/how-to.mdx:92-104` (deploy-targets section — line range may have shifted; locate via grep)

- [ ] **Step 1: Capture failing checks**

```bash
# Migration links — should currently be wrong (`.md` + uppercase)
grep -nE 'FROM_LANGGRAPH\.md|FROM_OPENAI_AGENTS\.md|FROM_CREWAI\.md|FROM_AUTOGEN\.md|FROM_CUSTOM\.md' website/content/docs/how-to.mdx
```

Expected: 5 lines.

```bash
# Deploy-target caveat — should currently be missing
grep -A2 'Deploy to different targets' website/content/docs/how-to.mdx | grep -E 'existing infra|greenfield|Currently shipped'
```

Expected: **no output**.

- [ ] **Step 2: Rewrite migration links**

In `website/content/docs/how-to.mdx`, replace:

```
| LangGraph | `langgraph` | [FROM_LANGGRAPH.md](migrations/FROM_LANGGRAPH.md) |
| OpenAI Agents | `openai_agents` | [FROM_OPENAI_AGENTS.md](migrations/FROM_OPENAI_AGENTS.md) |
| CrewAI | `crewai` | [FROM_CREWAI.md](migrations/FROM_CREWAI.md) |
| AutoGen | `custom` | [FROM_AUTOGEN.md](migrations/FROM_AUTOGEN.md) |
| Custom code | `custom` | [FROM_CUSTOM.md](migrations/FROM_CUSTOM.md) |
```

with:

```
| LangGraph | `langgraph` | [from-langgraph](migrations/from-langgraph) |
| OpenAI Agents | `openai_agents` | [from-openai-agents](migrations/from-openai-agents) |
| CrewAI | `crewai` | [from-crewai](migrations/from-crewai) |
| AutoGen | `custom` | [from-autogen](migrations/from-autogen) |
| Custom code | `custom` | [from-custom](migrations/from-custom) |
```

(Fumadocs link-resolution drops the `.mdx` extension. If a built page complains about a missing route, add `.mdx` back — but the live convention used elsewhere in this repo is extension-less.)

- [ ] **Step 3: Add prereq caveat to deploy-targets section**

Locate the "Deploy to different targets" heading (around line 92) and insert this paragraph immediately under it (before any sub-headings or command examples):

```markdown
> **What's fully shipped today:** Local (Docker Compose) and GCP Cloud Run support greenfield provisioning end-to-end. AWS ECS Fargate, AWS App Runner, Azure Container Apps, Kubernetes (EKS/GKE/AKS), and Claude Managed Agents have working deployer implementations but require **existing infrastructure** — pre-provisioned VPC/subnets/IAM roles for AWS, resource group + Container Apps Environment for Azure, an existing cluster for Kubernetes. Prereq checklists are in [deployment.mdx](deployment).
```

- [ ] **Step 4: Verify links + caveat**

```bash
grep -nE 'from-langgraph|from-openai-agents|from-crewai|from-autogen|from-custom' website/content/docs/how-to.mdx
grep -A3 'Deploy to different targets' website/content/docs/how-to.mdx | grep 'fully shipped today'
```

Expected: 5 matching link lines + 1 caveat line.

- [ ] **Step 5: Build the site**

```bash
cd website && npm run build 2>&1 | tail -20 && cd ..
```

Expected: `✓ Compiled successfully` (any link errors will surface here).

- [ ] **Step 6: Commit**

```bash
git add website/content/docs/how-to.mdx
git commit -m "docs(how-to): fix migration table links + add deploy-target prereq caveat"
```

---

## Tasks 3–12: Stale-deploy-target sweep (one task per page)

These tasks share an identical pattern. For each task: locate the page's explicit "supported target / available target / works on X" claim, replace it with the **standard truth-statement** (see top of plan), commit. Do **not** delete educational mentions of AWS/Azure/K8s — only correct *availability* claims.

### Task 3: `cli-reference.mdx` — sweep

**Files:** `website/content/docs/cli-reference.mdx`

- [ ] **Step 1: Locate stale claims**

```bash
grep -nE 'AWS App Runner|App Runner|ecs-fargate|aws-ecs|Azure Container Apps|container-apps|--target ecs|--target app-runner|--target container-apps|--target k8s|--target kubernetes|--target claude-managed|Kubernetes|claude-managed' website/content/docs/cli-reference.mdx
```

Read each matching line and the 3 lines around it. Identify only those that *claim availability* (e.g., "deploy to AWS ECS Fargate with X") — ignore mentions in unrelated sections (e.g., environment variable docs, FAQ).

- [ ] **Step 2: For each availability claim, replace with the standard truth-statement**

For lines that list deploy targets in `--target` examples or "supported clouds" tables: replace the line(s) with the standard truth-statement (verbatim from the top of this plan). For lines that just mention AWS/Azure/K8s in passing (e.g., "logs are tagged with cloud=aws"), leave them alone.

If the page has a "Deploy targets" table or list, replace that whole block with:

```markdown
| Target | Status | Greenfield provisioning |
|--------|--------|--------------------------|
| `local` (Docker Compose) | ✅ Shipped | ✅ |
| `cloud-run` (GCP) | ✅ Shipped | ✅ |
| `ecs-fargate` (AWS) | 🟡 Deployer exists | ❌ — needs existing VPC + IAM |
| `app-runner` (AWS) | 🟡 Deployer exists | ❌ — needs existing VPC + IAM |
| `container-apps` (Azure) | 🟡 Deployer exists | ❌ — needs existing resource group + env |
| `kubernetes` (EKS/GKE/AKS/self-hosted) | 🟡 Deployer exists | ❌ — needs existing cluster + kubeconfig |
| `claude-managed` (Anthropic) | 🟡 Deployer exists | n/a — runtime is Anthropic-managed |
```

- [ ] **Step 3: Verify**

```bash
grep -c '🟡 Deployer exists' website/content/docs/cli-reference.mdx
```

Expected: ≥ 5 (rows added).

- [ ] **Step 4: Build + commit**

```bash
cd website && npm run build 2>&1 | tail -10 && cd ..
git add website/content/docs/cli-reference.mdx
git commit -m "docs(cli-reference): mark non-GCP/local deploy targets as requiring existing infra"
```

### Task 4: `agent-yaml.mdx` — sweep

**Files:** `website/content/docs/agent-yaml.mdx`

Same pattern as Task 3. The likely hot spot is the `deploy.cloud:` / `deploy.runtime:` enum tables — list every value but annotate availability honestly.

- [ ] **Step 1: Locate stale claims**

```bash
grep -nE 'aws|gcp|azure|kubernetes|claude-managed' website/content/docs/agent-yaml.mdx | head -40
```

- [ ] **Step 2: Update the `deploy.cloud` / `deploy.runtime` field documentation**

Wherever values like `aws | gcp | azure | kubernetes | claude-managed` are listed, add a short "Status" column / inline annotation per target using the same vocabulary as Task 3 (✅ Shipped vs 🟡 Deployer exists, requires existing infra).

- [ ] **Step 3: Build + commit**

```bash
cd website && npm run build 2>&1 | tail -10 && cd ..
git add website/content/docs/agent-yaml.mdx
git commit -m "docs(agent-yaml): annotate deploy.cloud values with shipped/requires-existing-infra status"
```

### Task 5: `no-code.mdx` — sweep

**Files:** `website/content/docs/no-code.mdx`

- [ ] **Step 1: Locate stale claims**

```bash
grep -nE 'AWS|GCP|Azure|Kubernetes|deploy to|deploy target' website/content/docs/no-code.mdx | head -30
```

- [ ] **Step 2: Apply the standard truth-statement** anywhere the No-Code UI is described as "deploying to AWS/Azure/K8s." The wizard probably *displays* all options in the picker — leave that screenshot/copy alone, but add a one-line note: *"Targets marked '🟡' require pre-provisioned cloud infrastructure — see [deployment.mdx](deployment) for prereqs."*

- [ ] **Step 3: Build + commit**

```bash
cd website && npm run build 2>&1 | tail -10 && cd ..
git add website/content/docs/no-code.mdx
git commit -m "docs(no-code): note non-GCP deploy targets need pre-provisioned infra"
```

### Task 6: `low-code.mdx` — sweep

**Files:** `website/content/docs/low-code.mdx`

Same as Task 5 but for YAML tier. Apply the standard truth-statement to any "supported deploy targets" list.

- [ ] **Step 1: Locate stale claims**

```bash
grep -nE 'AWS|GCP|Azure|Kubernetes|deploy to|deploy target' website/content/docs/low-code.mdx | head -30
```

- [ ] **Step 2: Apply the standard truth-statement** to availability lists. Leave educational YAML examples that show `cloud: aws` etc. alone — they're illustrating syntax, not claiming availability.

- [ ] **Step 3: Build + commit**

```bash
cd website && npm run build 2>&1 | tail -10 && cd ..
git add website/content/docs/low-code.mdx
git commit -m "docs(low-code): note non-GCP deploy targets need pre-provisioned infra"
```

### Task 7: `examples.mdx` — sweep

**Files:** `website/content/docs/examples.mdx`

- [ ] **Step 1: Locate stale claims**

```bash
grep -nE 'AWS|GCP|Azure|Kubernetes|claude-managed|deploy to' website/content/docs/examples.mdx | head -30
```

- [ ] **Step 2: For each example that claims "this works on AWS ECS / Azure / K8s,"** add a one-line caveat: *"Requires existing AWS/Azure/K8s infrastructure (see [deployment.mdx](deployment))."* Leave the example unchanged — just annotate.

- [ ] **Step 3: Build + commit**

```bash
cd website && npm run build 2>&1 | tail -10 && cd ..
git add website/content/docs/examples.mdx
git commit -m "docs(examples): caveat non-GCP examples on infra prereqs"
```

### Task 8: `deployment.mdx` — promote to canonical prereqs page

**Files:** `website/content/docs/deployment.mdx`

This page becomes the canonical place for "what's shipped + prereqs per target," so other pages can link here. Heavier than the other sweep tasks.

- [ ] **Step 1: Locate current claims**

```bash
grep -nE 'AWS|GCP|Azure|Kubernetes|claude-managed' website/content/docs/deployment.mdx | head -40
```

- [ ] **Step 2: Insert a "Status of deploy targets (as of 2026-05-18)" section near the top** of the page (after the intro / TOC, before the per-cloud chapters). Use the same status table from Task 3:

```markdown
## Status of deploy targets (as of 2026-05-18)

| Target | Status | Greenfield provisioning | Required prereqs |
|--------|--------|--------------------------|------------------|
| `local` (Docker Compose) | ✅ Shipped | ✅ | Docker Desktop / Engine |
| `cloud-run` (GCP) | ✅ Shipped | ✅ | GCP project + billing |
| `ecs-fargate` (AWS) | 🟡 Deployer exists | ❌ | VPC, subnets (private + public), security groups, execution IAM role |
| `app-runner` (AWS) | 🟡 Deployer exists | ❌ | IAM role with App Runner permissions |
| `container-apps` (Azure) | 🟡 Deployer exists | ❌ | Resource group, Container Apps Environment, subscription |
| `kubernetes` (EKS/GKE/AKS/self-hosted) | 🟡 Deployer exists | ❌ | Existing cluster + kubeconfig + namespace |
| `claude-managed` (Anthropic) | 🟡 Deployer exists | n/a | Anthropic API key with managed-agents access |

Greenfield provisioning (auto-creating VPCs/clusters/resource-groups from zero) is on the roadmap — see issue trackers tagged `greenfield-provisioning`. Until then, point AgentBreeder at existing infra for these targets.
```

- [ ] **Step 3: Update any per-cloud sections** further down the page to reference back to this status table instead of repeating "AWS App Runner is supported."

- [ ] **Step 4: Build + commit**

```bash
cd website && npm run build 2>&1 | tail -10 && cd ..
git add website/content/docs/deployment.mdx
git commit -m "docs(deployment): canonical status-of-deploy-targets table with prereqs"
```

### Task 9: `index.mdx` — sweep

**Files:** `website/content/docs/index.mdx`

The docs homepage. Likely contains a "Deploy anywhere" tagline or feature grid that lists clouds.

- [ ] **Step 1: Locate stale claims**

```bash
grep -nE 'AWS|GCP|Azure|Kubernetes|claude-managed|deploy' website/content/docs/index.mdx | head -30
```

- [ ] **Step 2: If a feature grid or tagline lists AWS/Azure/K8s as "available,"** replace with a link to the status table in `deployment.mdx`: *"Currently full support for Local + GCP Cloud Run; other targets require existing infra — see [deployment status](deployment#status-of-deploy-targets-as-of-2026-05-18)."*

- [ ] **Step 3: Build + commit**

```bash
cd website && npm run build 2>&1 | tail -10 && cd ..
git add website/content/docs/index.mdx
git commit -m "docs(index): align deploy-target claims with shipped reality"
```

### Task 10: `secrets.mdx` — fix specific auto-mirror claim

**Files:** `website/content/docs/secrets.mdx`

Per memory observation #27759: *"secrets.mdx claims 'auto-mirror ships for AWS ECS Fargate and...' — this is incorrect, only GCP Cloud Run auto-mirror is shipped."*

- [ ] **Step 1: Locate the auto-mirror claim**

```bash
grep -nB1 -A2 'auto-mirror' website/content/docs/secrets.mdx
```

- [ ] **Step 2: Replace** the misleading sentence with: *"Auto-mirror (creating cloud-provider secret-store entries automatically during deploy) is shipped for **GCP Cloud Run** today. AWS/Azure auto-mirror is on the roadmap; for now, pre-create secrets in AWS Secrets Manager / Azure Key Vault and reference them by name in `deploy.secrets:`."*

- [ ] **Step 3: Build + commit**

```bash
cd website && npm run build 2>&1 | tail -10 && cd ..
git add website/content/docs/secrets.mdx
git commit -m "docs(secrets): correct auto-mirror availability — GCP only today"
```

### Task 11: `sidecar.mdx` — sweep

**Files:** `website/content/docs/sidecar.mdx`

Sidecar is cloud-agnostic, so the "deploys to X" claims here are mostly about where the sidecar can run, not the deploy pipeline. Light touch.

- [ ] **Step 1: Locate claims**

```bash
grep -nE 'AWS|GCP|Azure|Kubernetes|deploy' website/content/docs/sidecar.mdx | head -20
```

- [ ] **Step 2: Where the sidecar is claimed to "auto-inject on AWS / Azure / K8s,"** add the same caveat as Task 7 — sidecar injection works on every deployer, but greenfield provisioning of the host platform is GCP-only.

- [ ] **Step 3: Build + commit**

```bash
cd website && npm run build 2>&1 | tail -10 && cd ..
git add website/content/docs/sidecar.mdx
git commit -m "docs(sidecar): caveat host-platform availability vs sidecar injection"
```

### Task 12: `migrations.mdx` — sweep

**Files:** `website/content/docs/migrations.mdx`

Migration index page. If it mentions "deploy on whatever cloud you came from," apply the same caveat.

- [ ] **Step 1: Locate claims**

```bash
grep -nE 'AWS|GCP|Azure|Kubernetes|deploy' website/content/docs/migrations.mdx | head -20
```

- [ ] **Step 2: Add a single-line callout at the top of the page** noting that the destination platform's deploy-target support reflects [deployment.mdx status](deployment#status-of-deploy-targets-as-of-2026-05-18).

- [ ] **Step 3: Build + commit**

```bash
cd website && npm run build 2>&1 | tail -10 && cd ..
git add website/content/docs/migrations.mdx
git commit -m "docs(migrations): link to deploy-target status table for destination availability"
```

---

## Task 13: Final consolidation — full-site build + Wave 0 closing commit

**Why:** After 12 incremental commits, do one clean end-to-end build to catch any cross-page link drift (Fumadocs validates internal links at build time). Then write the Wave 0 closing note.

**Files:**
- Create (if missing): `CHANGELOG.md` entry for the Wave 0 sweep (or append to existing)

- [ ] **Step 1: Full build**

```bash
cd website && npm run build 2>&1 | tee /tmp/wave0-build.log | tail -40 && cd ..
```

Expected: `✓ Compiled successfully`. If any "broken link" warnings appear in the build log, investigate and fix the source page (a previous task may have introduced a typo).

- [ ] **Step 2: Lint pass**

```bash
cd website && npm run lint 2>&1 | tail -20 && cd ..
```

Expected: no errors. (Warnings about unrelated TSX files are fine; we only care about MDX-adjacent code.)

- [ ] **Step 3: Verify no `rajits/` reference snuck out**

```bash
grep -rE '\brajits/agentbreeder-' website/content/docs/ || echo "OK — no references"
```

Note: `rajits/` is the *current* Docker namespace in `release.yml`, so existing doc mentions are factually correct today. Step is to confirm we didn't *introduce* any new ones. The full `rajits/` → `agentbreeder/` migration is human-review item HR-7 (see audit spec §5).

- [ ] **Step 4: Append CHANGELOG entry** (if `CHANGELOG.md` exists at repo root)

Add this paragraph under an "Unreleased / Docs" section:

```markdown
- **docs:** Wave 0 of the platform audit lands — quickstart/how-to/cli-reference are 100% aligned with the v1.7.x implementation. Stale "supported deploy targets" claims across 9 pages now distinguish ✅ Shipped (Local, GCP Cloud Run) from 🟡 Deployer-exists-but-requires-existing-infra (AWS ECS, App Runner, Azure, K8s, Claude Managed). Canonical status table lives in [deployment.mdx](deployment#status-of-deploy-targets-as-of-2026-05-18). See audit spec at `docs/superpowers/specs/2026-05-18-platform-audit-design.md`.
```

- [ ] **Step 5: Closing commit**

```bash
git add CHANGELOG.md  # if modified
git commit --allow-empty -m "docs(wave-0): close Wave 0 of platform audit — website is 100% aligned with v1.7.x"
```

(`--allow-empty` because the CHANGELOG may already be the only diff, or there may be nothing to commit if the project doesn't use CHANGELOG.md. The empty commit gives the loop a clean Wave-0 boundary marker.)

---

## Self-review notes (applied inline)

- **Spec coverage:** Wave 0 in spec §4 has 6 entries (W-01 … W-06). After corrections (W-03 dropped, W-04 deferred, W-05 expanded, W-06 merged), the 4 remaining concerns map to 13 tasks here. Confirmed:
  - W-01 → Task 1
  - W-02 (migration links) → Task 2 (first half)
  - W-05 → Tasks 2 (second half), 3–12 (sweep across 11 pages)
  - W-06 → Verified inline in Task 1; quickstart.mdx already correct
- **Placeholder scan:** No "TBD" / "TODO" / "implement later" in this plan. Every step has the exact text, command, or markdown block needed.
- **Type consistency:** N/A (no code). The "standard truth-statement" wording is repeated verbatim across pages (✅ Shipped / 🟡 Deployer exists) for consistency.
- **Risk:** Pure docs — additive within the risk envelope, no runtime impact, no cross-repo sync required. The website auto-deploys on push to `main`, so Wave 0's blast radius is the public docs site only.

---

## Execution

**Recommended:** subagent-driven — fast iteration, fresh context per task, easy to review the diff per page. Each task is ~5–10 minutes wall-clock.

**Alternative:** inline via `executing-plans` — fine if you'd rather batch and review at checkpoints.

After Wave 0 closes (Task 13's commit lands on `main`), generate the Wave 1 plan (`docs/superpowers/plans/2026-05-18-platform-audit-wave-1-p0-fixes.md`) covering the 4 P0 correctness/security fixes (W1-01 … W1-04 in the audit spec).
