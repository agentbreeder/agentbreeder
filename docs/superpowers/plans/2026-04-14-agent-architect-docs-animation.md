# Agent Architect Docs & Homepage Animation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a split-screen autoplay animation to the homepage and update how-to.md with full `/agent-build` advisory mode documentation.

**Architecture:** Three independent edits to the `feature/website` worktree — one HTML block inserted into `docs/index.md`, two section changes to `docs/how-to.md`. No new files, no new MkDocs plugins. Pure markdown + inline HTML.

**Tech Stack:** MkDocs Material, `md_in_html` extension (already enabled), HTML/CSS/JS (no external deps)

**Worktree:** `.worktrees/feature-website/` — all file paths below are relative to that root.

---

## File Map

| File | Change |
|------|--------|
| `docs/index.md` | Insert `## From Idea to Deployed Agent` section with animation HTML between line 19 (`---`) and line 21 (`## Three Builder Tiers`) |
| `docs/how-to.md` | Prepend `/agent-build` lead paragraph to `## Build Your First Agent` (line 84); insert new `## Use the Agent Architect (/agent-build)` section before `## Deploy to Different Targets` (line 190) |

---

## Task 1: Homepage animation

**Files:**
- Modify: `docs/index.md` (insert after line 19, before `## Three Builder Tiers`)

- [ ] **Step 1: Verify insertion point**

```bash
sed -n '17,23p' docs/index.md
```

Expected output:
```
| Builder diversity | Engineers only | No Code → Low Code → Full Code |

---

## Three Builder Tiers
```

The blank line + `---` + blank line is where the animation section goes.

- [ ] **Step 2: Insert the animation section**

Open `docs/index.md`. Find the `---` separator that appears after the "Why AgentBreeder?" table (between the table and `## Three Builder Tiers`). Replace that separator block with the following (the trailing `---` before Three Builder Tiers stays):

```markdown
---

## From Idea to Deployed Agent

Not sure which framework, model, or RAG setup is right for your use case?
Run `/agent-build` in [Claude Code](https://claude.ai/code) — it interviews you, recommends the full stack, and scaffolds a production-ready project in one conversation.

<div class="ab-demo" aria-label="Demo: /agent-build advisory flow" role="img">
<style>
.ab-demo{font-family:Inter,system-ui,sans-serif;margin:24px 0}
.ab-wrap{border:1px solid #30363d;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.4);background:#0d1117}
.ab-bar{background:#161b22;border-bottom:1px solid #30363d;padding:10px 16px;display:flex;align-items:center;gap:8px}
.ab-dot{width:12px;height:12px;border-radius:50%}
.ab-title{font-size:12px;color:#8b949e;margin:0 auto}
.ab-prog{height:2px;background:#21262d}
.ab-fill{height:100%;background:linear-gradient(90deg,#3fb950,#58a6ff);width:0%;transition:width .3s linear}
.ab-split{display:grid;grid-template-columns:1fr 1fr;min-height:300px}
.ab-left{background:#0d1117;border-right:1px solid #30363d;padding:20px;font-family:'JetBrains Mono','Fira Code',monospace;font-size:12px;line-height:1.7;overflow:hidden}
.ab-right{background:#0d1117;padding:20px;font-family:'JetBrains Mono','Fira Code',monospace;font-size:12px;line-height:1.7}
.ab-ph{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#484f58;margin-bottom:12px;border-bottom:1px solid #21262d;padding-bottom:8px}
.ab-ln{opacity:0;transform:translateY(4px);transition:opacity .25s ease,transform .25s ease}
.ab-ln.ab-show{opacity:1;transform:translateY(0)}
.ab-foot{background:#161b22;border-top:1px solid #30363d;padding:8px 16px;display:flex;align-items:center;gap:10px}
.ab-dots{display:flex;gap:5px}
.ab-sd{width:6px;height:6px;border-radius:50%;background:#30363d;transition:background .3s}
.ab-sd.ab-act{background:#58a6ff}
.ab-sd.ab-dn{background:#3fb950}
.ab-sn{font-size:11px;color:#e6edf3;margin-left:auto}
.ab-sl{font-size:11px;color:#8b949e}
@media(max-width:600px){.ab-split{grid-template-columns:1fr}.ab-right{display:none}}
</style>
<noscript>Demo: /agent-build advisory flow — interview → recommendations → scaffold → agentbreeder deploy</noscript>
<div class="ab-wrap">
  <div class="ab-bar">
    <div class="ab-dot" style="background:#ff5f56"></div>
    <div class="ab-dot" style="background:#ffbd2e"></div>
    <div class="ab-dot" style="background:#27c93f"></div>
    <div class="ab-title">agent-architect-demo</div>
  </div>
  <div class="ab-prog"><div class="ab-fill" id="ab-prog"></div></div>
  <div class="ab-split">
    <div class="ab-left">
      <div class="ab-ph">Advisory Interview</div>
      <div id="ab-left"></div>
    </div>
    <div class="ab-right">
      <div class="ab-ph">Generated Project</div>
      <div id="ab-right"></div>
    </div>
  </div>
  <div class="ab-foot">
    <div class="ab-dots">
      <div class="ab-sd" id="ab-s0"></div><div class="ab-sd" id="ab-s1"></div>
      <div class="ab-sd" id="ab-s2"></div><div class="ab-sd" id="ab-s3"></div>
      <div class="ab-sd" id="ab-s4"></div>
    </div>
    <div class="ab-sl">Step</div>
    <div class="ab-sn" id="ab-sn">Starting...</div>
  </div>
</div>
<script>
(function(){
var L=document.getElementById('ab-left'),
    R=document.getElementById('ab-right'),
    P=document.getElementById('ab-prog'),
    SN=document.getElementById('ab-sn');
var C={p:'#3fb950',c:'#58a6ff',q:'#e6edf3',a:'#f78166',d:'#8b949e',rh:'#d2a8ff',rc:'#79c0ff',dp:'#ffa657'};
function sp(t,col){var s=document.createElement('span');s.textContent=t;if(col)s.style.color=col;return s;}
function ln(){
  var div=document.createElement('div');
  div.className='ab-ln';
  for(var i=0;i<arguments.length;i++){
    var a=arguments[i];
    if(typeof a==='string') div.appendChild(document.createTextNode(a));
    else div.appendChild(a);
  }
  return div;
}
function add(par,el){par.appendChild(el);requestAnimationFrame(function(){requestAnimationFrame(function(){el.classList.add('ab-show');});});}
function w(ms){return new Promise(function(r){setTimeout(r,ms);});}
function prog(n){P.style.width=n+'%';}
function step(i,name){
  SN.textContent=name;
  for(var j=0;j<5;j++){
    var d=document.getElementById('ab-s'+j);
    d.className='ab-sd'+(j<i?' ab-dn':j===i?' ab-act':'');
  }
}
async function run(){
  L.textContent='';R.textContent='';prog(0);
  step(0,'Invoke /agent-build');
  add(L,ln(sp('$ ',C.p),sp('/agent-build',C.c)));prog(4);await w(700);
  add(L,ln());add(L,ln(sp('Know your stack, or should I recommend?',C.q)));await w(500);
  add(L,ln(sp('(a) I know my stack',C.d)));add(L,ln(sp('(b) Recommend for me',C.d)));await w(700);
  add(L,ln(sp('> b',C.a)));prog(10);await w(800);
  step(1,'Advisory Interview');
  add(L,ln());add(L,ln(sp('What problem does this agent solve?',C.q)));await w(500);
  add(L,ln(sp('> Reduce tier-1 support tickets',C.a)));prog(22);await w(700);
  add(L,ln());add(L,ln(sp('Describe the workflow step by step.',C.q)));await w(500);
  add(L,ln(sp('> Search KB \u2192 lookup order \u2192 escalate',C.a)));prog(34);await w(700);
  add(L,ln());add(L,ln(sp('State complexity? (loops/HITL/parallel)',C.q)));await w(500);
  add(L,ln(sp('> a, c  (loops + human-in-the-loop)',C.a)));prog(44);await w(600);
  add(L,ln());add(L,ln(sp('... 3 more questions ...',C.d)));prog(50);await w(900);
  step(2,'Recommendations');
  add(L,ln());add(L,ln(sp('\u2500\u2500 Recommendations \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500',C.rh)));await w(300);
  var recs=[['Framework','LangGraph \u2014 Full Code'],['Model','claude-sonnet-4-6'],['RAG','Vector (pgvector)'],['Memory','Short-term (Redis)'],['MCP','MCP servers'],['Deploy','ECS Fargate'],['Evals','deflection-rate, CSAT']];
  for(var i=0;i<recs.length;i++){
    add(L,ln(sp('  '+recs[i][0].padEnd(10),C.rc),sp(recs[i][1],C.q)));
    prog(50+i*2.5);await w(220);
  }
  prog(68);await w(500);
  add(L,ln());add(L,ln(sp('Override anything, or proceed? ',C.q),sp('> proceed',C.a)));await w(600);
  step(3,'Scaffolding Files');
  var files=[
    [0,'support-agent/',true],[1,'agent.yaml',false],[1,'agent.py',false],
    [1,'requirements.txt',false],[1,'.env.example',false],[1,'Dockerfile',false],
    [1,'tools/',true],[2,'zendesk.py',false],[1,'rag/',true],[2,'ingest.py',false],
    [1,'tests/',true],[2,'eval_deflect.py',false],[1,'ARCHITECT_NOTES.md',false],
    [1,'CLAUDE.md',false],[1,'.cursorrules',false],[1,'README.md',false]
  ];
  var delays=[250,200,180,160,160,160,200,140,200,140,200,140,180,160,160,160];
  for(var i=0;i<files.length;i++){
    var f=files[i],pre='  '.repeat(f[0]),parts=[];
    if(pre) parts.push(document.createTextNode(pre));
    if(!f[2]) parts.push(sp('+ ',C.p));
    parts.push(sp(f[1],f[2]?C.c:C.p));
    add(R,ln.apply(null,parts));
    prog(68+(i+1)/files.length*24);await w(delays[i]||160);
  }
  prog(93);await w(600);
  step(4,'Deploy');
  add(L,ln());add(L,ln(sp('\u2713 ',C.p),sp('16 files generated in support-agent/',C.q)));await w(400);
  add(L,ln());add(L,ln(sp('$ ',C.p),sp('agentbreeder deploy',C.dp)));await w(700);
  add(L,ln(sp('\u2713 ',C.p),sp('Deployed to ECS Fargate',C.q)));await w(400);
  add(L,ln(sp('\u2713 ',C.p),sp('https://support-agent.company.com',C.d)));
  prog(100);await w(3000);
  run();
}
run();
})();
</script>
</div>

---
```

- [ ] **Step 3: Verify insertion looks correct**

```bash
grep -n "From Idea to Deployed\|Three Builder Tiers\|Why AgentBreeder" docs/index.md
```

Expected output — animation section sits between the two:
```
9:## Why AgentBreeder?
21:## From Idea to Deployed Agent
XX:## Three Builder Tiers
```

- [ ] **Step 4: Build and check for errors**

```bash
cd /path/to/.worktrees/feature-website
mkdocs build --strict 2>&1 | tail -20
```

Expected: `INFO - Documentation built` with no warnings about `index.md`.

- [ ] **Step 5: Visual check in browser**

```bash
mkdocs serve --dev-addr 127.0.0.1:8001 &
open http://127.0.0.1:8001
```

Verify:
- Animation appears between "Why AgentBreeder?" table and "Three Builder Tiers" tabs
- Animation autoplays and loops
- Left panel shows conversation, right panel shows file tree building
- Step dots at the bottom advance through 5 states
- On mobile (< 600px): right panel hides, left panel fills width

Kill server when done: `pkill -f "mkdocs serve"`

- [ ] **Step 6: Commit**

```bash
git add docs/index.md
git commit -m "feat(docs): add split-screen /agent-build animation to homepage"
```

---

## Task 2: Update "Build Your First Agent" section opener

**Files:**
- Modify: `docs/how-to.md` lines 84–90

- [ ] **Step 1: Read the current section opener**

```bash
sed -n '84,95p' docs/how-to.md
```

Expected:
```markdown
## Build Your First Agent

### Step 1: Scaffold

```bash
agentbreeder init
```
```

- [ ] **Step 2: Replace the section opener**

Find the line `## Build Your First Agent` in `docs/how-to.md` (line 84). Replace the content between it and `### Step 1: Scaffold` with:

```markdown
## Build Your First Agent

The fastest way to scaffold a new agent project is the **AI Agent Architect**:

```bash
# Run this in Claude Code
/agent-build
```

It asks you 6 questions (or recommends the best stack for your use case) and generates a complete, production-ready project. See the [full walkthrough below](#use-the-agent-architect-agent-build).

Prefer to scaffold manually? The steps below walk through each file.

### Step 1: Scaffold
```

- [ ] **Step 3: Verify the change**

```bash
sed -n '84,100p' docs/how-to.md
```

Expected: lead paragraph with `/agent-build` code block, followed by `### Step 1: Scaffold`.

- [ ] **Step 4: Build check**

```bash
mkdocs build --strict 2>&1 | grep -E "ERROR|WARNING|built"
```

Expected: `INFO - Documentation built` with no errors.

- [ ] **Step 5: Commit**

```bash
git add docs/how-to.md
git commit -m "docs: lead Build Your First Agent with /agent-build"
```

---

## Task 3: New "Use the Agent Architect" section in how-to.md

**Files:**
- Modify: `docs/how-to.md` — insert new section before `## Deploy to Different Targets`

- [ ] **Step 1: Find exact insertion line**

```bash
grep -n "^## Deploy to Different Targets" docs/how-to.md
```

Note the line number — insert the new section immediately before it (after the `---` separator on the preceding line).

- [ ] **Step 2: Insert the new section**

Find the `---` separator immediately before `## Deploy to Different Targets` and replace it with the following (the `---` before the next section is included at the end):

```markdown
---

## Use the Agent Architect (/agent-build)

`/agent-build` is a Claude Code skill that acts as an AI Agent Architect. Run it inside Claude Code at the root of any directory where you want to scaffold a new agent project.

It supports two paths:

- **Fast Path** — you know your stack. Six quick questions, then scaffold.
- **Advisory Path** — you describe your use case. It recommends the best framework, model, RAG, memory, MCP/A2A, deployment, and eval setup — with reasoning — before scaffolding begins.

### Fast Path

```
$ /agent-build

Do you already know your stack, or would you like me to recommend?
(a) I know my stack — I'll ask 6 quick questions and scaffold your project
(b) Recommend for me — ...

> a

What should we call this agent?
> support-agent

What will this agent do?
> Handle tier-1 customer support tickets

Which framework?
1. LangGraph  2. CrewAI  3. Claude SDK  4. OpenAI Agents  5. Google ADK  6. Custom
> 1

Where will it run?
1. Local  2. AWS  3. GCP  4. Kubernetes (planned)
> 2

What tools should this agent have?
> zendesk lookup, knowledge base search

Team name and owner email? [engineering / you@company.com]
> (enter)

┌─────────────────────────────────────┐
│  Framework   LangGraph              │
│  Cloud       AWS (ECS Fargate)      │
│  Model       gpt-4o                 │
│  Tools       zendesk, kb-search     │
│  Team        engineering            │
└─────────────────────────────────────┘
Look good? I'll generate your project. > yes

✓ 10 files generated in support-agent/
```

### Advisory Path

```
$ /agent-build

> b

What problem does this agent solve, and for whom?
> Reduce tier-1 support tickets for our SaaS by deflecting common questions

What does the agent need to do, step by step?
> User sends ticket → search knowledge base → look up order status →
  respond if found, escalate to human if not

Does your agent need: (a) loops/retries (b) checkpoints (c) human-in-the-loop
(d) parallel branches (e) none
> a, c

Primary cloud provider? (a) AWS (b) GCP (c) Azure (d) Local
Language preference?    (a) Python (b) TypeScript (c) No preference
> a  a

What data does this agent work with?
(a) Unstructured docs  (b) Structured DB  (c) Knowledge graph
(d) Live APIs          (e) None
> a, d

Traffic pattern?
(a) Real-time interactive  (b) Async batch
(c) Event-driven           (d) Internal/low-volume
> a

── Recommendations ───────────────────────────────
  Framework   LangGraph — Full Code
  Model       claude-sonnet-4-6
  RAG         Vector (pgvector)
  Memory      Short-term (Redis)
  MCP         MCP servers
  Deploy      ECS Fargate
  Evals       deflection-rate, CSAT, escalation-rate

Override anything, or proceed? > proceed

✓ 19 files generated in support-agent/
```

### What gets generated

| File / Directory | Purpose | Path |
|-----------------|---------|------|
| `agent.yaml` | AgentBreeder config — framework, model, deploy, tools, guardrails | Both paths |
| `agent.py` | Framework entrypoint (LangGraph graph / CrewAI crew / etc.) | Both paths |
| `tools/` | Tool stub files, one per tool named in the interview | Both paths |
| `requirements.txt` | Framework + provider dependencies | Both paths |
| `.env.example` | Required API keys and env vars | Both paths |
| `Dockerfile` | Multi-stage container image | Both paths |
| `deploy/` | `docker-compose.yml` or cloud deploy config | Both paths |
| `criteria.md` | Eval criteria | Both paths |
| `README.md` | Project overview + quick-start | Both paths |
| `memory/` | Redis / PostgreSQL setup | Advisory (if recommended) |
| `rag/` | Vector or Graph RAG index + ingestion scripts | Advisory (if recommended) |
| `mcp/servers.yaml` | MCP server references | Advisory (if recommended) |
| `tests/evals/` | Eval harness + use-case criteria | Advisory |
| `ARCHITECT_NOTES.md` | Reasoning behind every recommendation | Advisory |
| `CLAUDE.md` | Agent-specific Claude Code context | Advisory |
| `AGENTS.md` | AI skill roster for iterating on this agent | Advisory |
| `.cursorrules` | Framework-specific Cursor IDE rules | Advisory |
| `.antigravity.md` | Hard constraints for this agent | Advisory |

### Next steps after scaffolding

```bash
cd support-agent/

# Validate the generated agent.yaml
agentbreeder validate

# Deploy locally first
agentbreeder deploy --target local

# Chat with your agent
agentbreeder chat

# When ready, deploy to cloud
agentbreeder deploy
```

---
```

- [ ] **Step 3: Verify the section was inserted correctly**

```bash
grep -n "^## Use the Agent Architect\|^## Deploy to Different Targets\|^## Build Your First Agent" docs/how-to.md
```

Expected — three sections in order:
```
84:## Build Your First Agent
XXX:## Use the Agent Architect (/agent-build)
YYY:## Deploy to Different Targets
```

- [ ] **Step 4: Build check**

```bash
mkdocs build --strict 2>&1 | grep -E "ERROR|WARNING|built"
```

Expected: `INFO - Documentation built` with no errors or warnings.

- [ ] **Step 5: Visual check**

```bash
mkdocs serve --dev-addr 127.0.0.1:8001 &
open http://127.0.0.1:8001/how-to/
```

Verify:
- "Build Your First Agent" opens with `/agent-build` lead and link to the new section
- "Use the Agent Architect (/agent-build)" section appears in the left nav under Getting Started → How-To Guide
- Fast Path and Advisory Path code blocks render correctly
- "What gets generated" table renders with all 18 rows
- "Next steps" code block renders

Kill server: `pkill -f "mkdocs serve"`

- [ ] **Step 6: Commit**

```bash
git add docs/how-to.md
git commit -m "docs: add /agent-build advisory architect how-to section"
```

---

## Task 4: Final build verification + PR

**Files:** No file changes — verification only.

- [ ] **Step 1: Clean build with strict mode**

```bash
mkdocs build --strict --clean 2>&1
```

Expected last line: `INFO - Documentation built in X.X seconds`
Any `WARNING` or `ERROR` lines must be resolved before opening the PR.

- [ ] **Step 2: Check internal links are valid**

```bash
grep -n "agent-build\|#use-the-agent-architect" docs/index.md docs/how-to.md
```

Verify the anchor `#use-the-agent-architect-agent-build` used in the how-to.md lead paragraph resolves to the new section heading. MkDocs Material auto-generates anchors from headings — `## Use the Agent Architect (/agent-build)` becomes `#use-the-agent-architect-agent-build`.

- [ ] **Step 3: Open PR from feature/website**

```bash
git push -u origin feature/website
gh pr create \
  --title "docs: homepage animation + /agent-build how-to (M35 follow-on)" \
  --body "Adds split-screen autoplay animation to homepage and full /agent-build advisory mode documentation to how-to guide. Closes the M35 documentation gap." \
  --base main
```
