'use client';

import { useEffect, useRef } from 'react';

const COLORS = {
  p: '#3fb950',   // prompt $
  c: '#58a6ff',   // command
  q: '#e6edf3',   // question
  a: '#f78166',   // answer
  d: '#8b949e',   // dim
  rh: '#d2a8ff',  // rec heading
  rc: '#79c0ff',  // rec key
  dp: '#ffa657',  // deploy
};

type Part = { text: string; color?: string };

function makeLine(...parts: Part[]): HTMLDivElement {
  const div = document.createElement('div');
  div.style.cssText = 'opacity:0;transform:translateY(4px);transition:opacity 0.25s ease,transform 0.25s ease;line-height:1.7';
  parts.forEach(({ text, color }) => {
    const span = document.createElement('span');
    span.textContent = text;
    if (color) span.style.color = color;
    div.appendChild(span);
  });
  return div;
}

function show(el: HTMLElement) {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    el.style.opacity = '1';
    el.style.transform = 'translateY(0)';
  }));
}

function wait(ms: number) {
  return new Promise<void>(r => setTimeout(r, ms));
}

async function runLoop(
  leftEl: HTMLDivElement,
  rightEl: HTMLDivElement,
  progEl: HTMLDivElement,
  stepNameEl: HTMLSpanElement,
  dotEls: HTMLDivElement[],
  signal: { cancelled: boolean },
) {
  const prog = (n: number) => { progEl.style.width = n + '%'; };
  const step = (i: number, name: string) => {
    stepNameEl.textContent = name;
    dotEls.forEach((d, j) => {
      d.style.background = j < i ? '#3fb950' : j === i ? '#58a6ff' : '#30363d';
    });
  };
  const add = (parent: HTMLDivElement, el: HTMLDivElement) => {
    parent.appendChild(el);
    show(el);
  };

  while (!signal.cancelled) {
    leftEl.textContent = '';
    rightEl.textContent = '';
    prog(0);

    // Step 0 — invoke
    step(0, 'Invoke /agent-build');
    add(leftEl, makeLine({ text: '$ ', color: COLORS.p }, { text: '/agent-build', color: COLORS.c }));
    prog(4); await wait(700); if (signal.cancelled) return;

    add(leftEl, makeLine({ text: '' }));
    add(leftEl, makeLine({ text: 'Know your stack, or should I recommend?', color: COLORS.q }));
    await wait(500); if (signal.cancelled) return;
    add(leftEl, makeLine({ text: '(a) I know my stack', color: COLORS.d }));
    add(leftEl, makeLine({ text: '(b) Recommend for me', color: COLORS.d }));
    await wait(700); if (signal.cancelled) return;
    add(leftEl, makeLine({ text: '> b', color: COLORS.a }));
    prog(10); await wait(800); if (signal.cancelled) return;

    // Step 1 — interview
    step(1, 'Advisory Interview');
    add(leftEl, makeLine({ text: '' }));
    add(leftEl, makeLine({ text: 'What problem does this agent solve?', color: COLORS.q }));
    await wait(500); if (signal.cancelled) return;
    add(leftEl, makeLine({ text: '> Reduce tier-1 support tickets', color: COLORS.a }));
    prog(22); await wait(700); if (signal.cancelled) return;

    add(leftEl, makeLine({ text: '' }));
    add(leftEl, makeLine({ text: 'Describe the workflow step by step.', color: COLORS.q }));
    await wait(500); if (signal.cancelled) return;
    add(leftEl, makeLine({ text: '> Search KB \u2192 lookup order \u2192 escalate', color: COLORS.a }));
    prog(34); await wait(700); if (signal.cancelled) return;

    add(leftEl, makeLine({ text: '' }));
    add(leftEl, makeLine({ text: 'State complexity? (loops/HITL/parallel)', color: COLORS.q }));
    await wait(500); if (signal.cancelled) return;
    add(leftEl, makeLine({ text: '> a, c  (loops + human-in-the-loop)', color: COLORS.a }));
    prog(44); await wait(600); if (signal.cancelled) return;

    add(leftEl, makeLine({ text: '' }));
    add(leftEl, makeLine({ text: '... 3 more questions ...', color: COLORS.d }));
    prog(50); await wait(900); if (signal.cancelled) return;

    // Step 2 — recommendations
    step(2, 'Recommendations');
    add(leftEl, makeLine({ text: '' }));
    add(leftEl, makeLine({ text: '\u2500\u2500 Recommendations \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500', color: COLORS.rh }));
    await wait(300); if (signal.cancelled) return;

    const recs: [string, string][] = [
      ['Framework', 'LangGraph \u2014 Full Code'],
      ['Model',     'claude-sonnet-4-6'],
      ['RAG',       'Vector (pgvector)'],
      ['Memory',    'Short-term (Redis)'],
      ['MCP',       'MCP servers'],
      ['Deploy',    'ECS Fargate'],
      ['Evals',     'deflection-rate, CSAT'],
    ];
    for (let i = 0; i < recs.length; i++) {
      if (signal.cancelled) return;
      add(leftEl, makeLine(
        { text: '  ' + recs[i][0].padEnd(10), color: COLORS.rc },
        { text: recs[i][1], color: COLORS.q },
      ));
      prog(50 + i * 2.5);
      await wait(220);
    }
    prog(68); await wait(500); if (signal.cancelled) return;

    add(leftEl, makeLine({ text: '' }));
    add(leftEl, makeLine(
      { text: 'Override anything, or proceed? ', color: COLORS.q },
      { text: '> proceed', color: COLORS.a },
    ));
    await wait(600); if (signal.cancelled) return;

    // Step 3 — scaffold
    step(3, 'Scaffolding Files');
    const files: { indent: number; name: string; isDir: boolean }[] = [
      { indent: 0, name: 'support-agent/', isDir: true },
      { indent: 1, name: 'agent.yaml',        isDir: false },
      { indent: 1, name: 'agent.py',          isDir: false },
      { indent: 1, name: 'requirements.txt',  isDir: false },
      { indent: 1, name: '.env.example',      isDir: false },
      { indent: 1, name: 'Dockerfile',        isDir: false },
      { indent: 1, name: 'tools/',            isDir: true  },
      { indent: 2, name: 'zendesk.py',        isDir: false },
      { indent: 1, name: 'rag/',              isDir: true  },
      { indent: 2, name: 'ingest.py',         isDir: false },
      { indent: 1, name: 'tests/',            isDir: true  },
      { indent: 2, name: 'eval_deflect.py',   isDir: false },
      { indent: 1, name: 'ARCHITECT_NOTES.md', isDir: false },
      { indent: 1, name: 'CLAUDE.md',         isDir: false },
      { indent: 1, name: '.cursorrules',      isDir: false },
      { indent: 1, name: 'README.md',         isDir: false },
    ];
    const delays = [250,200,180,160,160,160,200,140,200,140,200,140,180,160,160,160];
    for (let i = 0; i < files.length; i++) {
      if (signal.cancelled) return;
      const f = files[i];
      const prefix = '  '.repeat(f.indent);
      const parts: Part[] = [];
      if (prefix) parts.push({ text: prefix });
      if (!f.isDir) parts.push({ text: '+ ', color: COLORS.p });
      parts.push({ text: f.name, color: f.isDir ? COLORS.c : COLORS.p });
      add(rightEl, makeLine(...parts));
      prog(68 + ((i + 1) / files.length) * 24);
      await wait(delays[i] ?? 160);
    }
    prog(93); await wait(600); if (signal.cancelled) return;

    // Step 4 — deploy
    step(4, 'Deploy');
    add(leftEl, makeLine({ text: '' }));
    add(leftEl, makeLine(
      { text: '\u2713 ', color: COLORS.p },
      { text: '16 files generated in support-agent/', color: COLORS.q },
    ));
    await wait(400); if (signal.cancelled) return;
    add(leftEl, makeLine({ text: '' }));
    add(leftEl, makeLine(
      { text: '$ ', color: COLORS.p },
      { text: 'agentbreeder deploy', color: COLORS.dp },
    ));
    await wait(700); if (signal.cancelled) return;
    add(leftEl, makeLine(
      { text: '\u2713 ', color: COLORS.p },
      { text: 'Deployed to ECS Fargate', color: COLORS.q },
    ));
    await wait(400); if (signal.cancelled) return;
    add(leftEl, makeLine(
      { text: '\u2713 ', color: COLORS.p },
      { text: 'https://support-agent.company.com', color: COLORS.d },
    ));
    prog(100);
    await wait(3200); if (signal.cancelled) return;
  }
}

export function AgentDemo() {
  const leftRef  = useRef<HTMLDivElement>(null);
  const rightRef = useRef<HTMLDivElement>(null);
  const progRef  = useRef<HTMLDivElement>(null);
  const nameRef  = useRef<HTMLSpanElement>(null);
  const dot0 = useRef<HTMLDivElement>(null);
  const dot1 = useRef<HTMLDivElement>(null);
  const dot2 = useRef<HTMLDivElement>(null);
  const dot3 = useRef<HTMLDivElement>(null);
  const dot4 = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const signal = { cancelled: false };
    const dots = [dot0, dot1, dot2, dot3, dot4].map(r => r.current!);
    runLoop(leftRef.current!, rightRef.current!, progRef.current!, nameRef.current!, dots, signal);
    return () => { signal.cancelled = true; };
  }, []);

  const dotStyle: React.CSSProperties = {
    width: 6, height: 6, borderRadius: '50%', background: '#30363d', transition: 'background 0.3s',
  };

  return (
    <section style={{ background: 'var(--bg-base, #010409)' }}>
      <div className="mx-auto max-w-[1200px] px-20 py-20">
        <p
          className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
          style={{ color: 'var(--accent)' }}
        >
          Agent Architect
        </p>
        <h2
          className="mb-3 text-[36px] font-extrabold text-white"
          style={{ letterSpacing: '-1px' }}
        >
          From idea to deployed agent
        </h2>
        <p className="mb-10 max-w-[500px] text-base leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
          Not sure which framework, model, or RAG setup is right?
          Run <code className="rounded px-1 py-0.5 text-[13px]" style={{ background: '#161b22', color: '#58a6ff' }}>/agent-build</code> in Claude Code — it interviews you, recommends the full stack, and scaffolds a production-ready project.
        </p>

        {/* Terminal window */}
        <div style={{
          border: '1px solid #30363d',
          borderRadius: 12,
          overflow: 'hidden',
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
          background: '#0d1117',
        }}>
          {/* Title bar */}
          <div style={{
            background: '#161b22',
            borderBottom: '1px solid #30363d',
            padding: '10px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}>
            <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#ff5f56' }} />
            <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#ffbd2e' }} />
            <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#27c93f' }} />
            <span style={{ fontSize: 12, color: '#8b949e', margin: '0 auto' }}>agent-architect-demo</span>
          </div>

          {/* Progress bar */}
          <div style={{ height: 2, background: '#21262d' }}>
            <div ref={progRef} style={{
              height: '100%',
              background: 'linear-gradient(90deg, #3fb950, #58a6ff)',
              width: '0%',
              transition: 'width 0.3s linear',
            }} />
          </div>

          {/* Split panels */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', minHeight: 300 }}>
            <div style={{ borderRight: '1px solid #30363d', padding: 20 }}>
              <div style={{
                fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase',
                color: '#484f58', marginBottom: 12, borderBottom: '1px solid #21262d', paddingBottom: 8,
              }}>
                Advisory Interview
              </div>
              <div
                ref={leftRef}
                style={{
                  fontFamily: "'JetBrains Mono','Fira Code',monospace",
                  fontSize: 12,
                  overflowY: 'auto',
                  maxHeight: 280,
                }}
              />
            </div>
            <div style={{ padding: 20 }}>
              <div style={{
                fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase',
                color: '#484f58', marginBottom: 12, borderBottom: '1px solid #21262d', paddingBottom: 8,
              }}>
                Generated Project
              </div>
              <div
                ref={rightRef}
                style={{
                  fontFamily: "'JetBrains Mono','Fira Code',monospace",
                  fontSize: 12,
                  overflowY: 'auto',
                  maxHeight: 280,
                }}
              />
            </div>
          </div>

          {/* Footer */}
          <div style={{
            background: '#161b22',
            borderTop: '1px solid #30363d',
            padding: '8px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}>
            <div style={{ display: 'flex', gap: 5 }}>
              {[dot0, dot1, dot2, dot3, dot4].map((r, i) => (
                <div key={i} ref={r} style={dotStyle} />
              ))}
            </div>
            <span style={{ fontSize: 11, color: '#8b949e' }}>Step</span>
            <span ref={nameRef} style={{ fontSize: 11, color: '#e6edf3', marginLeft: 'auto' }}>
              Starting...
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
