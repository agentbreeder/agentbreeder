'use client';

import { useEffect, useRef } from 'react';

function wait(ms: number) {
  return new Promise<void>(r => setTimeout(r, ms));
}

const TARGETS = [
  { label: 'Local',      detail: 'Docker Compose',   color: '#22c55e', endpoint: 'localhost:8080' },
  { label: 'Cloud Run',  detail: 'GCP · Serverless', color: '#4285f4', endpoint: 'agent-abc.a.run.app' },
] as const;

const PIPELINE = [
  { text: '✓  YAML parsed & validated',    color: '#3fb950' },
  { text: '✓  RBAC check passed',          color: '#3fb950' },
  { text: '✓  Dependencies resolved',      color: '#3fb950' },
  { text: '✓  Container built',            color: '#3fb950' },
  { text: '⟳  Deploying to all targets…', color: '#ffa657' },
] as const;

const CARD_TRANSITION = 'border-color 0.35s ease, box-shadow 0.35s ease, background 0.35s ease';

type CardState = {
  card: HTMLDivElement;
  badge: HTMLSpanElement;
  endpointEl: HTMLDivElement;
  pulseEl: HTMLDivElement;
};

function makeLine(text: string, color: string): HTMLDivElement {
  const div = document.createElement('div');
  div.style.cssText =
    'opacity:0;transform:translateY(4px);' +
    'transition:opacity 0.18s ease,transform 0.18s ease;' +
    'line-height:1.65;font-family:"JetBrains Mono","Fira Code",monospace;font-size:12px;';
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

async function runLoop(
  termEl: HTMLDivElement,
  progEl: HTMLDivElement,
  stepEl: HTMLSpanElement,
  cards: CardState[],
  statsEl: HTMLDivElement,
  signal: { cancelled: boolean },
) {
  const prog = (n: number) => { progEl.style.width = `${n}%`; };

  function resetAll() {
    termEl.textContent = '';
    prog(0);
    // Disable transitions for instant card reset
    for (const c of cards) {
      c.card.style.transition = 'none';
      c.card.style.borderColor = 'rgba(255,255,255,0.07)';
      c.card.style.boxShadow = 'none';
      c.card.style.background = '#111113';
      c.badge.textContent = 'pending';
      c.badge.style.color = '#484f58';
      c.badge.style.background = '#1a1a1e';
      c.endpointEl.style.opacity = '0';
      c.pulseEl.style.opacity = '0';
      c.pulseEl.style.animation = 'none';
    }
    statsEl.style.transition = 'none';
    statsEl.style.opacity = '0';
    // Re-enable transitions on next frame
    requestAnimationFrame(() => {
      for (const c of cards) c.card.style.transition = CARD_TRANSITION;
      statsEl.style.transition = 'opacity 0.6s ease';
    });
  }

  function add(line: HTMLDivElement) {
    termEl.appendChild(line);
    reveal(line);
    termEl.scrollTop = termEl.scrollHeight;
  }

  while (!signal.cancelled) {
    resetAll();
    await wait(300);

    // Command line
    stepEl.textContent = 'Initializing…';
    add(makeLine('$ agentbreeder deploy agent.yaml --target all', '#3fb950'));
    prog(5);
    await wait(900); if (signal.cancelled) return;

    // Pipeline steps
    for (let i = 0; i < PIPELINE.length; i++) {
      if (signal.cancelled) return;
      add(makeLine('  ' + PIPELINE[i].text, PIPELINE[i].color));
      stepEl.textContent = PIPELINE[i].text.replace(/^[✓⟳]\s+/, '');
      prog(10 + (i + 1) * 13);
      await wait(500);
    }
    await wait(250); if (signal.cancelled) return;

    // Flash cards to "deploying" state with stagger
    for (let i = 0; i < cards.length; i++) {
      if (signal.cancelled) return;
      const c = cards[i];
      const t = TARGETS[i];
      c.card.style.borderColor = t.color + '55';
      c.card.style.boxShadow   = `0 0 18px ${t.color}1e`;
      c.card.style.background  = t.color + '0b';
      c.badge.textContent      = 'deploying';
      c.badge.style.color      = t.color;
      c.badge.style.background = t.color + '1a';
      c.pulseEl.style.opacity  = '1';
      c.pulseEl.style.animation = 'ab-pulse 1.2s ease-in-out infinite';
      await wait(140);
    }
    await wait(1200); if (signal.cancelled) return;

    // Success flash — cards go live one by one
    add(makeLine('', '#30363d'));
    for (let i = 0; i < cards.length; i++) {
      if (signal.cancelled) return;
      const c = cards[i];
      const t = TARGETS[i];
      c.card.style.borderColor = t.color;
      c.card.style.boxShadow   = `0 0 28px ${t.color}44`;
      c.card.style.background  = t.color + '12';
      c.badge.textContent      = '✓ live';
      c.badge.style.color      = t.color;
      c.badge.style.background = t.color + '25';
      c.endpointEl.style.opacity = '1';
      c.pulseEl.style.opacity  = '0';
      c.pulseEl.style.animation = 'none';
      add(makeLine(`  ✓  ${t.label} → ${t.endpoint}`, t.color));
      prog(75 + (i + 1) * 4);
      await wait(120);
    }

    prog(100);
    stepEl.textContent = 'All targets healthy ✓';
    await wait(400); if (signal.cancelled) return;

    // Governance stats fade in
    statsEl.style.opacity = '1';
    await wait(4500); if (signal.cancelled) return;
  }
}

export function DeployAnywhere() {
  const termRef  = useRef<HTMLDivElement>(null);
  const progRef  = useRef<HTMLDivElement>(null);
  const stepRef  = useRef<HTMLSpanElement>(null);
  const statsRef = useRef<HTMLDivElement>(null);

  // One set of refs per target card — must match TARGETS.length exactly
  const card0  = useRef<HTMLDivElement>(null);  const badge0  = useRef<HTMLSpanElement>(null);  const ep0  = useRef<HTMLDivElement>(null);  const pulse0  = useRef<HTMLDivElement>(null);
  const card1  = useRef<HTMLDivElement>(null);  const badge1  = useRef<HTMLSpanElement>(null);  const ep1  = useRef<HTMLDivElement>(null);  const pulse1  = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const cards: CardState[] = [
      { card: card0.current!, badge: badge0.current!, endpointEl: ep0.current!, pulseEl: pulse0.current! },
      { card: card1.current!, badge: badge1.current!, endpointEl: ep1.current!, pulseEl: pulse1.current! },
    ];
    const signal = { cancelled: false };
    runLoop(termRef.current!, progRef.current!, stepRef.current!, cards, statsRef.current!, signal);
    return () => { signal.cancelled = true; };
  }, []);

  const allRefs = [
    { card: card0, badge: badge0, ep: ep0, pulse: pulse0 },
    { card: card1, badge: badge1, ep: ep1, pulse: pulse1 },
  ];

  return (
    <section style={{ background: 'var(--bg-base)' }}>
      <div className="max-w-[1400px] mx-auto px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24 py-20 lg:py-28">
        <p
          className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
          style={{ color: 'var(--accent)' }}
        >
          Deploy Anywhere
        </p>
        <h2
          className="mb-3 text-[36px] font-extrabold text-white"
          style={{ letterSpacing: '-1px' }}
        >
          One command. Every cloud.
        </h2>
        <p className="mb-10 max-w-[560px] text-base leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
          <code
            className="rounded px-1 py-0.5 text-[13px]"
            style={{ background: '#161b22', color: '#58a6ff' }}
          >
            agentbreeder deploy
          </code>{' '}
          runs the same 8-step atomic pipeline regardless of target — Local Docker Compose or GCP Cloud Run.
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
            <span style={{ fontSize: 12, color: '#8b949e', margin: '0 auto' }}>agentbreeder-deploy</span>
          </div>

          {/* Progress bar */}
          <div style={{ height: 2, background: '#21262d' }}>
            <div
              ref={progRef}
              style={{
                height: '100%',
                background: 'linear-gradient(90deg, #22c55e, #58a6ff, #a78bfa)',
                width: '0%',
                transition: 'width 0.3s linear',
              }}
            />
          </div>

          {/* Split panels */}
          <div className="grid grid-cols-1 md:grid-cols-2" style={{ minHeight: 260 }}>
            {/* Left: pipeline terminal */}
            <div style={{ borderRight: '1px solid #30363d', padding: 20 }}>
              <div style={{
                fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase',
                color: '#484f58', marginBottom: 12, borderBottom: '1px solid #21262d', paddingBottom: 8,
              }}>
                Deploy Pipeline
              </div>
              <div ref={termRef} style={{ overflowY: 'auto', maxHeight: 280 }} />
            </div>

            {/* Right: target cards */}
            <div style={{ padding: 20 }}>
              <div style={{
                fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase',
                color: '#484f58', marginBottom: 12, borderBottom: '1px solid #21262d', paddingBottom: 8,
              }}>
                Deployment Targets
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
                {TARGETS.map((t, i) => {
                  const r = allRefs[i];
                  return (
                    <div
                      key={t.label}
                      ref={r.card}
                      style={{
                        position: 'relative',
                        border: '1px solid rgba(255,255,255,0.07)',
                        borderRadius: 8,
                        padding: '10px 10px 8px',
                        background: '#111113',
                        transition: CARD_TRANSITION,
                        overflow: 'hidden',
                      }}
                    >
                      {/* Pulsing ring overlay */}
                      <div
                        ref={r.pulse}
                        style={{
                          position: 'absolute',
                          inset: 0,
                          borderRadius: 7,
                          border: `1px solid ${t.color}`,
                          opacity: 0,
                          pointerEvents: 'none',
                        }}
                      />
                      {/* Status badge */}
                      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 5 }}>
                        <span
                          ref={r.badge}
                          style={{
                            fontSize: 9,
                            padding: '2px 5px',
                            borderRadius: 4,
                            background: '#1a1a1e',
                            color: '#484f58',
                            transition: 'color 0.3s ease, background 0.3s ease',
                          }}
                        >
                          pending
                        </span>
                      </div>
                      {/* Label */}
                      <div style={{
                        fontSize: 11,
                        fontWeight: 600,
                        color: '#e4e4e7',
                        lineHeight: 1.3,
                        marginBottom: 2,
                      }}>
                        {t.label}
                      </div>
                      {/* Detail */}
                      <div style={{ fontSize: 9, color: '#484f58', marginBottom: 6 }}>
                        {t.detail}
                      </div>
                      {/* Endpoint */}
                      <div
                        ref={r.ep}
                        style={{
                          fontSize: 9,
                          fontFamily: '"JetBrains Mono","Fira Code",monospace',
                          color: t.color,
                          opacity: 0,
                          transition: 'opacity 0.35s ease',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {t.endpoint}
                      </div>
                    </div>
                  );
                })}
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
          }}>
            <span style={{ fontSize: 11, color: '#484f58' }}>Status</span>
            <span ref={stepRef} style={{ fontSize: 11, color: '#e6edf3', marginLeft: 'auto' }}>
              Starting…
            </span>
          </div>
        </div>

        {/* Governance stats */}
        <div
          ref={statsRef}
          style={{
            marginTop: 20,
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: 10,
            opacity: 0,
            transition: 'opacity 0.6s ease',
          }}
        >
          {[
            { val: '✓', label: 'Registered in org registry' },
            { val: '✓', label: 'RBAC enforced'              },
            { val: '✓', label: 'Cost attributed to team'    },
            { val: '✓', label: 'Audit log written'          },
          ].map(({ val, label }) => (
            <div
              key={label}
              style={{
                border: '1px solid var(--accent-border)',
                borderRadius: 8,
                padding: '12px 16px',
                background: 'var(--accent-dim)',
              }}
            >
              <div style={{ fontSize: 18, color: 'var(--accent)', marginBottom: 4 }}>{val}</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{label}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
