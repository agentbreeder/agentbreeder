# AgentBreeder

**Define Once. Deploy Anywhere. Govern Automatically.**

AgentBreeder is an open-source platform for building, deploying, and governing enterprise AI agents. Write one `agent.yaml`, run `agentbreeder deploy`, and your agent is live on AWS or GCP — with RBAC, cost tracking, audit trail, and org-wide discoverability automatic.

---

## Why AgentBreeder?

| Challenge | Without AgentBreeder | With AgentBreeder |
|-----------|---------------------|------------------|
| Framework fragmentation | Each team uses a different framework | One deploy pipeline, any framework |
| Cloud sprawl | Agents manually deployed to ad-hoc infra | One command, any cloud |
| Governance gaps | No audit trail, no RBAC, no cost tracking | Governance is a side effect of deploying |
| Discoverability | Agents exist in silos | Shared org-wide registry |
| Builder diversity | Engineers only | No Code → Low Code → Full Code |

---

## From Idea to Deployed Agent

Not sure which framework, model, or RAG setup is right for your use case?
Run `/agent-build` in [Claude Code](https://claude.ai/code) — it interviews you, recommends the full stack, and scaffolds a production-ready project in one conversation.

<div class="ab-demo" aria-label="Demo: /agent-build advisory flow" role="region">
<style>
.ab-demo{font-family:Inter,system-ui,sans-serif;margin:24px 0}
.ab-wrap{border:1px solid #30363d;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.4);background:#0d1117}
.ab-bar{background:#161b22;border-bottom:1px solid #30363d;padding:10px 16px;display:flex;align-items:center;gap:8px}
.ab-dot{width:12px;height:12px;border-radius:50%}
.ab-title{font-size:12px;color:#8b949e;margin:0 auto}
.ab-prog{height:2px;background:#21262d}
.ab-fill{height:100%;background:linear-gradient(90deg,#3fb950,#58a6ff);width:0%;transition:width .3s linear}
.ab-split{display:grid;grid-template-columns:1fr 1fr;min-height:300px}
.ab-left{background:#0d1117;border-right:1px solid #30363d;padding:20px;font-family:'JetBrains Mono','Fira Code',monospace;font-size:12px;line-height:1.7;overflow-y:auto;max-height:300px}
.ab-right{background:#0d1117;padding:20px;font-family:'JetBrains Mono','Fira Code',monospace;font-size:12px;line-height:1.7;overflow-y:auto;max-height:300px}
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
<noscript>Demo: /agent-build advisory flow — invoke → interview → recommendations → scaffold → agentbreeder deploy</noscript>
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
    <div class="ab-sn" id="ab-sn" aria-live="polite">Starting...</div>
  </div>
</div>
<script>
(function(){
var cancelled=false;
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
  if(cancelled)return;
  L.textContent='';R.textContent='';prog(0);
  step(0,'Invoke /agent-build');
  add(L,ln(sp('$ ',C.p),sp('/agent-build',C.c)));prog(4);await w(700);
  if(cancelled)return;
  add(L,ln());add(L,ln(sp('Know your stack, or should I recommend?',C.q)));await w(500);
  if(cancelled)return;
  add(L,ln(sp('(a) I know my stack',C.d)));add(L,ln(sp('(b) Recommend for me',C.d)));await w(700);
  if(cancelled)return;
  add(L,ln(sp('> b',C.a)));prog(10);await w(800);
  if(cancelled)return;
  step(1,'Advisory Interview');
  add(L,ln());add(L,ln(sp('What problem does this agent solve?',C.q)));await w(500);
  if(cancelled)return;
  add(L,ln(sp('> Reduce tier-1 support tickets',C.a)));prog(22);await w(700);
  if(cancelled)return;
  add(L,ln());add(L,ln(sp('Describe the workflow step by step.',C.q)));await w(500);
  if(cancelled)return;
  add(L,ln(sp('> Search KB \u2192 lookup order \u2192 escalate',C.a)));prog(34);await w(700);
  if(cancelled)return;
  add(L,ln());add(L,ln(sp('State complexity? (loops/HITL/parallel)',C.q)));await w(500);
  if(cancelled)return;
  add(L,ln(sp('> a, c  (loops + human-in-the-loop)',C.a)));prog(44);await w(600);
  if(cancelled)return;
  add(L,ln());add(L,ln(sp('... 3 more questions ...',C.d)));prog(50);await w(900);
  if(cancelled)return;
  step(2,'Recommendations');
  add(L,ln());add(L,ln(sp('\u2500\u2500 Recommendations \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500',C.rh)));await w(300);
  if(cancelled)return;
  var recs=[['Framework','LangGraph \u2014 Full Code'],['Model','claude-sonnet-4-6'],['RAG','Vector (pgvector)'],['Memory','Short-term (Redis)'],['MCP','MCP servers'],['Deploy','ECS Fargate'],['Evals','deflection-rate, CSAT']];
  for(var i=0;i<recs.length;i++){
    if(cancelled)return;
    add(L,ln(sp('  '+recs[i][0].padEnd(10),C.rc),sp(recs[i][1],C.q)));
    prog(50+i*2.5);await w(220);
  }
  if(cancelled)return;
  prog(68);await w(500);
  if(cancelled)return;
  add(L,ln());add(L,ln(sp('Override anything, or proceed? ',C.q),sp('> proceed',C.a)));await w(600);
  if(cancelled)return;
  step(3,'Scaffolding Files');
  var files=[
    [0,'support-agent/',true],[1,'agent.yaml',false],[1,'agent.py',false],
    [1,'requirements.txt',false],[1,'.env.example',false],[1,'Dockerfile',false],
    [1,'tools/',true],[2,'zendesk.py',false],[1,'rag/',true],[2,'ingest.py',false],
    [1,'tests/',true],[2,'eval_deflect.py',false],[1,'ARCHITECT_NOTES.md',false],
    [1,'CLAUDE.md',false],[1,'.cursorrules',false],[1,'README.md',false]
  ];
  var delays=[250,200,180,160,160,160,200,140,200,140,200,140,180,160,160];
  for(var i=0;i<files.length;i++){
    if(cancelled)return;
    var f=files[i],pre='  '.repeat(f[0]),parts=[];
    if(pre) parts.push(document.createTextNode(pre));
    if(!f[2]) parts.push(sp('+ ',C.p));
    parts.push(sp(f[1],f[2]?C.c:C.p));
    add(R,ln.apply(null,parts));
    prog(68+(i+1)/files.length*24);await w(delays[i]||160);
  }
  if(cancelled)return;
  prog(93);await w(600);
  if(cancelled)return;
  step(4,'Deploy');
  add(L,ln());add(L,ln(sp('\u2713 ',C.p),sp('16 files generated in support-agent/',C.q)));await w(400);
  if(cancelled)return;
  add(L,ln());add(L,ln(sp('$ ',C.p),sp('agentbreeder deploy',C.dp)));await w(700);
  if(cancelled)return;
  add(L,ln(sp('\u2713 ',C.p),sp('Deployed to ECS Fargate',C.q)));await w(400);
  if(cancelled)return;
  add(L,ln(sp('\u2713 ',C.p),sp('https://support-agent.company.com',C.d)));
  prog(100);await w(3000);
  if(!cancelled) run();
}
var _obs=new MutationObserver(function(){
  if(!document.getElementById('ab-prog')){cancelled=true;_obs.disconnect();}
});
_obs.observe(document.body,{childList:true,subtree:true});
run();
})();
</script>
</div>

---

## Three Builder Tiers

AgentBreeder supports three ways to build agents and orchestrations. All three compile to the same internal format and share the same deploy pipeline.

=== "No Code (Visual UI)"

    Drag-and-drop agent builder. Pick model, tools, prompt, guardrails from the registry. Define multi-agent routing on a ReactFlow canvas.

    **Who:** PMs, analysts, citizen builders.

=== "Low Code (YAML)"

    Write `agent.yaml` or `orchestration.yaml` in any IDE. Schema-aware, human-readable, version-controlled.

    ```yaml
    name: customer-support-agent
    version: 1.0.0
    team: customer-success
    framework: langgraph
    model:
      primary: claude-sonnet-4
    deploy:
      cloud: local          # or: gcp
    ```

    **Who:** ML engineers, DevOps, developers comfortable with config files.

=== "Full Code (Python/TS SDK)"

    Full programmatic control with custom routing, state machines, and dynamic agent spawning.

    ```python
    from agenthub import Pipeline

    research = (
        Pipeline("research-pipeline", team="eng")
        .step("researcher", ref="agents/researcher")
        .step("summarizer", ref="agents/summarizer")
        .step("reviewer",   ref="agents/reviewer")
    )
    research.deploy()
    ```

    **Who:** Senior engineers, researchers, teams that have outgrown YAML.

---

## Key Features

- **Framework-agnostic** — LangGraph, OpenAI Agents, CrewAI, Claude SDK, Google ADK, and Custom frameworks supported
- **Multi-cloud** — GCP Cloud Run and local Docker Compose implemented; AWS ECS and Kubernetes planned
- **Governance as a side effect** — RBAC, cost attribution, audit trail, and registry registration happen automatically on every `agentbreeder deploy`
- **Shared org registry** — agents, prompts, tools/MCP servers, models, knowledge bases all in one place
- **Tier mobility** — start No Code, eject to YAML, eject to SDK — no lock-in at any level
- **Multi-agent orchestration** — 6 strategies (router, sequential, parallel, supervisor, hierarchical, fan-out/fan-in) via YAML or SDK

---

## Quick Start

**Python (CLI + full platform):**

```bash
pip install agentbreeder
agentbreeder init
agentbreeder validate
agentbreeder deploy --target local
agentbreeder chat my-agent
```

**TypeScript / JavaScript (SDK only):**

```bash
npm install @agentbreeder/sdk
```

```typescript
import { Agent } from "@agentbreeder/sdk";

const agent = new Agent("customer-support", { version: "1.0.0", team: "eng" })
  .withModel({ primary: "claude-sonnet-4", fallback: "gpt-4o" })
  .withTool({ ref: "tools/zendesk-mcp" })
  .withDeploy({ cloud: "aws", region: "us-east-1" });

agent.toYaml("agent.yaml");
```

See the [Quickstart guide](quickstart.md) for the full setup.

---

## Supported Stack

| Layer | Implemented | Planned |
|-------|-------------|---------|
| Frameworks | LangGraph, OpenAI Agents, CrewAI, Claude SDK, Google ADK, Custom | — |
| Cloud targets | GCP Cloud Run, Local Docker Compose | AWS ECS Fargate, Kubernetes |
| LLM providers | Anthropic, OpenAI, Google, Ollama, LiteLLM, OpenRouter | — |
| Secrets backends | env, AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault | — |
| SDKs | Python (`agentbreeder-sdk`), TypeScript (`@agentbreeder/sdk`) | — |
| Auth | JWT + OAuth2, RBAC | SSO / SAML |
| Observability | OpenTelemetry, distributed tracing, cost monitoring | — |

---

## Documentation

| Section | Description |
|---------|-------------|
| [Quickstart](quickstart.md) | Get running in under 10 minutes |
| [How-To Guide](how-to.md) | 20+ practical recipes for common workflows |
| [Registry Guide](registry-guide.md) | Create, edit, and register prompts, tools, RAG, memory, and agents |
| [CLI Reference](cli-reference.md) | All `agentbreeder` commands |
| [agent.yaml](agent-yaml.md) | Full agent configuration reference |
| [orchestration.yaml](orchestration-yaml.md) | Multi-agent pipeline configuration |
| [Orchestration SDK](orchestration-sdk.md) | Python/TypeScript SDK for complex workflows |
| [TypeScript SDK](https://www.npmjs.com/package/@agentbreeder/sdk) | `npm install @agentbreeder/sdk` — JS/TS agent definitions |
| [Migration Guides](migrations/OVERVIEW.md) | Migrate from LangGraph, CrewAI, OpenAI Agents, AutoGen |
| [API Stability](api-stability.md) | API versioning and deprecation policy |
