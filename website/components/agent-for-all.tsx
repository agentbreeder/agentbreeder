'use client';

import { useEffect, useRef } from 'react';

function wait(ms: number) {
  return new Promise<void>(r => setTimeout(r, ms));
}

// ─── Shared helpers ───────────────────────────────────────────────────────────

function makeLine(text: string, color: string): HTMLDivElement {
  const div = document.createElement('div');
  div.style.cssText =
    'opacity:0;transform:translateY(3px);' +
    'transition:opacity 0.15s ease,transform 0.15s ease;' +
    'line-height:1.6;font-size:12px;font-family:"JetBrains Mono","Fira Code",monospace;white-space:pre;';
  const span = document.createElement('span');
  span.textContent = text;
  span.style.color = color;
  div.appendChild(span);
  return div;
}

function reveal(el: HTMLElement) {
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      el.style.opacity = '1';
      el.style.transform = 'translateY(0)';
    }),
  );
}

function append(parent: HTMLElement, el: HTMLElement) {
  parent.appendChild(el);
  reveal(el);
  parent.scrollTop = parent.scrollHeight;
}

// ─── Phase 0: No Code (Dashboard form UI) ────────────────────────────────────

function makeField(label: string, value: string, valueColor: string): HTMLDivElement {
  const row = document.createElement('div');
  row.style.cssText =
    'opacity:0;transform:translateY(3px);transition:opacity 0.2s ease,transform 0.2s ease;' +
    'display:flex;align-items:center;gap:8px;margin-bottom:8px;';

  const labelSpan = document.createElement('span');
  labelSpan.style.cssText = 'width:92px;flex-shrink:0;color:#71717a;font-size:11px;';
  labelSpan.textContent = label;

  const valueSpan = document.createElement('span');
  valueSpan.style.cssText =
    'flex:1;border:1px solid rgba(255,255,255,0.1);border-radius:5px;padding:3px 8px;' +
    'background:#161b22;font-family:"JetBrains Mono",monospace;font-size:11px;';
  valueSpan.style.color = valueColor;
  valueSpan.textContent = value;

  row.appendChild(labelSpan);
  row.appendChild(valueSpan);
  return row;
}

function makeCheckbox(label: string): HTMLDivElement {
  const row = document.createElement('div');
  row.style.cssText =
    'opacity:0;transform:translateY(3px);transition:opacity 0.2s ease,transform 0.2s ease;' +
    'display:flex;align-items:center;gap:6px;margin-bottom:5px;font-size:11px;margin-left:100px;';

  const check = document.createElement('span');
  check.style.cssText = 'color:#3fb950;font-size:12px;';
  check.textContent = '☑';

  const text = document.createElement('span');
  text.style.color = '#e4e4e7';
  text.textContent = label;

  row.appendChild(check);
  row.appendChild(text);
  return row;
}

async function runNoCode(
  screenEl: HTMLDivElement,
  signal: { cancelled: boolean },
): Promise<void> {
  screenEl.textContent = '';

  // Header
  const hdr = document.createElement('div');
  hdr.style.cssText =
    'opacity:0;transition:opacity 0.25s ease;font-size:11px;font-weight:600;color:#484f58;' +
    'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:14px;' +
    'padding-bottom:8px;border-bottom:1px solid #21262d;';
  hdr.textContent = 'Dashboard  →  New Agent';
  screenEl.appendChild(hdr);
  reveal(hdr);
  await wait(300); if (signal.cancelled) return;

  const f1 = makeField('Framework', 'LangGraph  ▾', '#c084fc');
  append(screenEl, f1);
  await wait(350); if (signal.cancelled) return;

  const f2 = makeField('Model', 'claude-sonnet-4  ▾', '#93c5fd');
  append(screenEl, f2);
  await wait(350); if (signal.cancelled) return;

  const toolsLbl = document.createElement('div');
  toolsLbl.style.cssText =
    'opacity:0;transition:opacity 0.2s ease;font-size:11px;color:#71717a;margin-bottom:5px;';
  toolsLbl.textContent = 'Tools';
  append(screenEl, toolsLbl);
  await wait(200); if (signal.cancelled) return;

  const cb1 = makeCheckbox('zendesk-mcp');
  append(screenEl, cb1);
  await wait(250); if (signal.cancelled) return;

  const cb2 = makeCheckbox('order-lookup-api');
  append(screenEl, cb2);
  await wait(350); if (signal.cancelled) return;

  const f3 = makeField('Deploy to', 'GCP Cloud Run  ▾', '#4285f4');
  append(screenEl, f3);
  await wait(500); if (signal.cancelled) return;

  // Deploy button
  const btn = document.createElement('div');
  btn.style.cssText =
    'opacity:0;transform:translateY(3px);transition:opacity 0.2s ease,transform 0.2s ease;' +
    'margin-top:12px;text-align:center;';

  const btnInner = document.createElement('span');
  btnInner.style.cssText =
    'display:inline-block;background:#22c55e;color:#000;font-size:12px;font-weight:700;' +
    'padding:6px 20px;border-radius:6px;';
  btnInner.textContent = '▶  Deploy Agent';
  btn.appendChild(btnInner);
  append(screenEl, btn);
  await wait(900); if (signal.cancelled) return;

  // Success banner
  const ok = document.createElement('div');
  ok.style.cssText =
    'opacity:0;transition:opacity 0.3s ease;margin-top:10px;font-size:11px;' +
    'font-family:"JetBrains Mono",monospace;color:#3fb950;' +
    'border:1px solid rgba(34,197,94,0.25);border-radius:5px;padding:6px 10px;' +
    'background:rgba(34,197,94,0.07);';
  ok.textContent = '✓  Agent deployed  ·  agent-abc.a.run.app';
  screenEl.appendChild(ok);
  requestAnimationFrame(() => requestAnimationFrame(() => { ok.style.opacity = '1'; }));
  await wait(3800); if (signal.cancelled) return;
}

// ─── Phase 1: Low Code (agent.yaml + CLI) ────────────────────────────────────

const YAML_LINES: [string, string][] = [
  ['# agent.yaml',              '#484f58'],
  ['name: support-agent',       '#e6edf3'],
  ['version: 1.0.0',            '#e6edf3'],
  ['framework: langgraph',      '#e6edf3'],
  ['',                          ''],
  ['model:',                    '#79c0ff'],
  ['  primary: claude-sonnet-4','#e6edf3'],
  ['  fallback: gpt-4o',        '#e6edf3'],
  ['',                          ''],
  ['tools:',                    '#79c0ff'],
  ['  - ref: tools/zendesk-mcp','#3fb950'],
  ['  - ref: tools/order-lookup','#3fb950'],
  ['',                          ''],
  ['deploy:',                   '#79c0ff'],
  ['  cloud: gcp',              '#e6edf3'],
  ['  runtime: cloud-run',      '#e6edf3'],
  ['  region: us-central1',     '#e6edf3'],
];

async function runLowCode(
  screenEl: HTMLDivElement,
  signal: { cancelled: boolean },
): Promise<void> {
  screenEl.textContent = '';

  const hdr = document.createElement('div');
  hdr.style.cssText =
    'opacity:0;transition:opacity 0.2s ease;font-size:11px;font-weight:600;color:#484f58;' +
    'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:12px;' +
    'padding-bottom:8px;border-bottom:1px solid #21262d;';
  hdr.textContent = 'agent.yaml';
  screenEl.appendChild(hdr);
  reveal(hdr);
  await wait(200); if (signal.cancelled) return;

  for (const [text, color] of YAML_LINES) {
    if (signal.cancelled) return;
    append(screenEl, makeLine(text, color || '#484f58'));
    await wait(text === '' ? 70 : 140);
  }

  await wait(300); if (signal.cancelled) return;
  append(screenEl, makeLine('', '#484f58'));
  append(screenEl, makeLine('$ agentbreeder validate agent.yaml', '#3fb950'));
  await wait(450); if (signal.cancelled) return;

  append(screenEl, makeLine('  ✓  Valid', '#3fb950'));
  await wait(350); if (signal.cancelled) return;

  append(screenEl, makeLine('$ agentbreeder deploy agent.yaml', '#ffa657'));
  await wait(600); if (signal.cancelled) return;

  append(screenEl, makeLine('  ✓  Live  ·  agent-abc.a.run.app', '#3fb950'));
  await wait(3500); if (signal.cancelled) return;
}

// ─── Phase 2: Full Code (Python SDK) ─────────────────────────────────────────

const SDK_LINES: [string, string][] = [
  ['from agenthub import Agent',          '#79c0ff'],
  ['',                                    ''],
  ['agent = (',                           '#e6edf3'],
  ['  Agent("support-agent")',            '#e6edf3'],
  ['    .with_version("1.0.0")',          '#d2a8ff'],
  ['    .with_framework("langgraph")',    '#d2a8ff'],
  ['    .with_model(',                    '#d2a8ff'],
  ['      primary="claude-sonnet-4",',   '#3fb950'],
  ['      fallback="gpt-4o",',           '#3fb950'],
  ['    )',                               '#d2a8ff'],
  ['    .with_tools([',                   '#d2a8ff'],
  ['      "tools/zendesk-mcp",',         '#3fb950'],
  ['      "tools/order-lookup",',        '#3fb950'],
  ['    ])',                              '#d2a8ff'],
  ['    .with_deploy(',                   '#d2a8ff'],
  ['      cloud="gcp",',                  '#3fb950'],
  ['      runtime="cloud-run",',          '#3fb950'],
  ['    )',                               '#d2a8ff'],
  [')',                                   '#e6edf3'],
  ['',                                    ''],
  ['result = agent.deploy()',             '#ffa657'],
  ['print(result.endpoint)',              '#ffa657'],
];

async function runFullCode(
  screenEl: HTMLDivElement,
  signal: { cancelled: boolean },
): Promise<void> {
  screenEl.textContent = '';

  const hdr = document.createElement('div');
  hdr.style.cssText =
    'opacity:0;transition:opacity 0.2s ease;font-size:11px;font-weight:600;color:#484f58;' +
    'letter-spacing:0.08em;text-transform:uppercase;margin-bottom:12px;' +
    'padding-bottom:8px;border-bottom:1px solid #21262d;';
  hdr.textContent = 'agent_deploy.py';
  screenEl.appendChild(hdr);
  reveal(hdr);
  await wait(200); if (signal.cancelled) return;

  for (const [text, color] of SDK_LINES) {
    if (signal.cancelled) return;
    append(screenEl, makeLine(text, color || '#e6edf3'));
    await wait(text === '' ? 60 : 110);
  }

  await wait(300); if (signal.cancelled) return;
  append(screenEl, makeLine('', '#484f58'));
  append(screenEl, makeLine('# Running deploy pipeline…', '#484f58'));
  await wait(700); if (signal.cancelled) return;

  append(screenEl, makeLine('https://agent-abc.a.run.app', '#3fb950'));
  await wait(3500); if (signal.cancelled) return;
}

// ─── Main loop ────────────────────────────────────────────────────────────────

const PHASES = [
  { label: 'No Code',   role: 'Business Users · PMs · Executives',  color: '#58a6ff', run: runNoCode   },
  { label: 'Low Code',  role: 'ML Engineers · DevOps · Architects',  color: '#3fb950', run: runLowCode  },
  { label: 'Full Code', role: 'Senior Engineers · Researchers',      color: '#a78bfa', run: runFullCode },
];

async function runLoop(
  screenEl: HTMLDivElement,
  tabEls: HTMLDivElement[],
  roleEl: HTMLSpanElement,
  winTitleEl: HTMLSpanElement,
  signal: { cancelled: boolean },
) {
  let phase = 0;
  while (!signal.cancelled) {
    const p = PHASES[phase];

    tabEls.forEach((t, i) => {
      t.style.color          = i === phase ? p.color : '#484f58';
      t.style.borderBottomColor = i === phase ? p.color : 'transparent';
      t.style.fontWeight     = i === phase ? '600' : '400';
    });
    roleEl.textContent = p.role;
    roleEl.style.color = p.color;
    winTitleEl.textContent = p.label + '  ·  ' + p.role;

    await p.run(screenEl, signal);
    if (signal.cancelled) return;

    phase = (phase + 1) % PHASES.length;
  }
}

// ─── Static data ─────────────────────────────────────────────────────────────

const FRAMEWORKS = ['LangGraph', 'OpenAI Agents', 'Claude SDK', 'CrewAI', 'Google ADK', 'Custom'];

const CLOUDS = [
  { label: 'Local',      color: '#22c55e' },
  { label: 'Cloud Run',  color: '#4285f4' },
];

// ─── Component ────────────────────────────────────────────────────────────────

export function AgentForAll() {
  const screenRef   = useRef<HTMLDivElement>(null);
  const tab0        = useRef<HTMLDivElement>(null);
  const tab1        = useRef<HTMLDivElement>(null);
  const tab2        = useRef<HTMLDivElement>(null);
  const roleRef     = useRef<HTMLSpanElement>(null);
  const winTitleRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const signal = { cancelled: false };
    runLoop(
      screenRef.current!,
      [tab0.current!, tab1.current!, tab2.current!],
      roleRef.current!,
      winTitleRef.current!,
      signal,
    );
    return () => { signal.cancelled = true; };
  }, []);

  const tabBase: React.CSSProperties = {
    fontSize: 13,
    fontWeight: 400,
    color: '#484f58',
    padding: '7px 0',
    borderBottom: '2px solid transparent',
    transition: 'color 0.3s ease, border-color 0.3s ease',
    userSelect: 'none',
  };

  return (
    <section
      className="border-t"
      style={{ background: 'var(--bg-base)', borderColor: 'var(--border)' }}
    >
      <div className="max-w-[1400px] mx-auto px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24 py-20 lg:py-28">
        {/* Header */}
        <p
          className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
          style={{ color: 'var(--accent)' }}
        >
          Agent for All
        </p>
        <h2
          className="mb-3 text-[36px] font-extrabold text-white"
          style={{ letterSpacing: '-1px' }}
        >
          No matter your role, you ship faster.
        </h2>
        <p className="mb-2 max-w-[560px] text-base leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
          Business users drag and drop. Engineers write YAML. Researchers use the full SDK.
          All three compile to the same pipeline, with the same governance, to every cloud.
        </p>
        <p className="mb-10 text-sm font-mono">
          <span
            ref={roleRef}
            style={{ transition: 'color 0.3s ease', color: '#484f58' }}
          >
            Starting…
          </span>
        </p>

        {/* Main terminal window */}
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
            <span
              ref={winTitleRef}
              style={{ fontSize: 12, color: '#8b949e', margin: '0 auto' }}
            >
              agentbreeder
            </span>
          </div>

          {/* Tier tabs */}
          <div style={{
            background: '#161b22',
            borderBottom: '1px solid #30363d',
            padding: '0 20px',
            display: 'flex',
            gap: 28,
          }}>
            {PHASES.map((p, i) => (
              <div
                key={p.label}
                ref={[tab0, tab1, tab2][i]}
                style={tabBase}
              >
                {p.label}
              </div>
            ))}
          </div>

          {/* Split: builder (left) + matrix (right) */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', minHeight: 340 }}>
            {/* Left: animated builder experience */}
            <div style={{ borderRight: '1px solid #30363d', padding: 20 }}>
              <div ref={screenRef} style={{ overflowY: 'auto', maxHeight: 300 }} />
            </div>

            {/* Right: capability matrix — always visible */}
            <div style={{ padding: 20 }}>
              <div style={{
                fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase',
                color: '#484f58', marginBottom: 10,
                borderBottom: '1px solid #21262d', paddingBottom: 8,
              }}>
                All 6 Frameworks — Every Tier
              </div>
              <div style={{
                display: 'grid', gridTemplateColumns: '1fr 1fr',
                gap: '5px 8px', marginBottom: 18,
              }}>
                {FRAMEWORKS.map(fw => (
                  <div key={fw} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11 }}>
                    <span style={{ color: '#3fb950' }}>✓</span>
                    <span style={{ color: '#e4e4e7' }}>{fw}</span>
                  </div>
                ))}
              </div>

              <div style={{
                fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase',
                color: '#484f58', marginBottom: 10,
                borderBottom: '1px solid #21262d', paddingBottom: 8,
              }}>
                All 6 Deployment Targets — Every Tier
              </div>
              <div style={{
                display: 'grid', gridTemplateColumns: '1fr 1fr',
                gap: '5px 8px', marginBottom: 18,
              }}>
                {CLOUDS.map(({ label }) => (
                  <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11 }}>
                    <span style={{ color: '#3fb950' }}>✓</span>
                    <span style={{ color: '#e4e4e7' }}>{label}</span>
                  </div>
                ))}
              </div>

              <div style={{
                fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase',
                color: '#484f58', marginBottom: 8,
                borderBottom: '1px solid #21262d', paddingBottom: 8,
              }}>
                Tier Mobility — No Lock-in
              </div>
              <div style={{
                fontSize: 11, color: '#8b949e', lineHeight: 1.9,
                fontFamily: '"JetBrains Mono",monospace',
              }}>
                <span style={{ color: '#58a6ff' }}>No Code</span>
                <span style={{ color: '#484f58' }}> → view YAML → </span>
                <span style={{ color: '#3fb950' }}>Low Code</span>
                <br />
                <span style={{ color: '#3fb950' }}>Low Code</span>
                <span style={{ color: '#484f58' }}> → eject → </span>
                <span style={{ color: '#a78bfa' }}>Full Code</span>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div style={{
            background: '#161b22',
            borderTop: '1px solid #30363d',
            padding: '8px 16px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}>
            <span style={{ fontSize: 11, color: '#484f58' }}>
              All tiers → same 8-step pipeline → auto governance
            </span>
            <span style={{
              fontSize: 11, color: '#484f58',
              fontFamily: '"JetBrains Mono",monospace',
            }}>
              agentbreeder eject --to sdk
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
