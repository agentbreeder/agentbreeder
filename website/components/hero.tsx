'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';

// Pinned: keep in step with the released version (docs/cross-repo-sync.md).
const INSTALL_CMD = 'npx agentbreeder@3.0.0-alpha.1 quickstart';

// A real schema-2 manifest: it validates with `agentbreeder validate` on main. The image digest
// is shortened for display only.
const MANIFEST_PREVIEW = `schema_version: 2
name: support-agent
image: sha256:93bf7e06…5a5f49
target: local
identity:
  provider: corp
  ref: support-agent
  environment: dev`;

type Line = { kind: 'cmd' | 'out' | 'ok' | 'warn' | 'deny' | 'note'; text: string };

// Recorded from a real session against main (static IdP, no policy engine configured). Only the
// 32-character IDs are shortened; every message is the CLI's own wording.
const SESSION: Line[] = [
  { kind: 'cmd', text: 'agentbreeder validate agent.yaml' },
  { kind: 'out', text: 'Valid manifest: support-agent (schema 2). Nothing deployed.' },
  { kind: 'cmd', text: 'agentbreeder deploy agent.yaml --local --environment dev' },
  { kind: 'ok', text: 'e47c6215…  ready' },
  { kind: 'out', text: 'http://127.0.0.1:52576' },
  {
    kind: 'warn',
    text: 'Gate attempt 7806d5df… (enforce); unavailable checks: policy, approval, deployer_verification',
  },
  { kind: 'cmd', text: 'agentbreeder status e47c6215…' },
  { kind: 'ok', text: 'Identity: compliant' },
  { kind: 'note', text: '# later: the agent’s review date passes in the identity provider' },
  { kind: 'cmd', text: 'agentbreeder reconcile --local      # exits 4 on drift' },
  { kind: 'deny', text: 'e47c6215…  support-agent  noncompliant:REVIEW_EXPIRED' },
  { kind: 'cmd', text: 'agentbreeder deploy agent-v2.yaml --local --environment dev' },
  { kind: 'deny', text: '[REVIEW_EXPIRED] Principal is past its review date' },
];

const LINE_COLOR: Record<Line['kind'], string> = {
  cmd: '#ffffff',
  out: 'var(--text-muted)',
  ok: 'var(--accent)',
  warn: '#fbbf24',
  deny: '#f87171',
  note: 'var(--text-dim)',
};

// What each check really is today. "opt-in" = the operator has to configure it.
const CHECKS = [
  { label: 'Identity', color: '#22c55e', state: 'on every deploy', lines: ['enabled · sponsor · review date', 'roles · groups · attributes'] },
  { label: 'Policy', color: '#60a5fa', state: 'opt-in', lines: ['Cedar · OPA · AuthZEN', 'verified deployer required'] },
  { label: 'Approval', color: '#a78bfa', state: 'opt-in', lines: ['second person', 'bound to the exact revision'] },
  { label: 'MCP egress', color: '#fb923c', state: 'opt-in', lines: ['per tool call, HTTP only', 'STDIO not mediated'] },
];

function ManifestLine({ line }: { line: string }) {
  const kv = line.match(/^(\s*)([a-z_]+)(:)(\s*)(.*)$/);
  if (!kv) return <span style={{ color: '#e4e4e7' }}>{line}</span>;
  const [, indent, key, colon, space, value] = kv;
  const isNumber = /^\d+$/.test(value);
  return (
    <span>
      {indent}
      <span style={{ color: '#93c5fd' }}>{key}</span>
      {colon}
      {space}
      {value && <span style={{ color: isNumber ? '#fb923c' : '#86efac' }}>{value}</span>}
    </span>
  );
}

function useSessionReplay(total: number) {
  // null = not animating: server render, no-JS and reduced-motion users see the whole session.
  const [step, setStep] = useState<number | null>(null);
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const timer = window.setInterval(() => {
      // Reveal one line per tick, hold the finished session for a few beats, then replay it.
      setStep((s) => (s === null || s >= total + 6 ? 0 : s + 1));
    }, 650);
    return () => window.clearInterval(timer);
  }, [total]);
  return step === null ? total : Math.min(step, total);
}

export function Hero() {
  const [copied, setCopied] = useState(false);
  const shown = useSessionReplay(SESSION.length);

  function copyInstallCmd() {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(INSTALL_CMD).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    }
  }

  return (
    <section className="relative w-full min-h-[calc(100vh-3.5rem)] flex items-center">
      <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
        <div
          className="absolute -right-40 -top-40 h-[700px] w-[700px] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(34,197,94,0.07) 0%, transparent 65%)' }}
        />
        <div
          className="absolute bottom-0 left-1/4 h-[400px] w-[400px] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(167,139,250,0.05) 0%, transparent 65%)' }}
        />
        {/* The gate: four checks feed one deploy decision. */}
        <svg
          className="absolute inset-0 h-full w-full opacity-[0.12]"
          viewBox="0 0 400 300"
          xmlns="http://www.w3.org/2000/svg"
          aria-hidden="true"
        >
          <defs>
            <style>{`
              .ab-edge { stroke-dasharray: 160; animation: ab-edge-travel 3s linear infinite; }
              .ab-e2 { animation-delay: 0.75s; }
              .ab-e3 { animation-delay: 1.5s; }
              .ab-e4 { animation-delay: 2.25s; }
              .ab-node { animation: ab-node-pulse 2s ease-in-out infinite; }
              .ab-n2 { animation-delay: 0.5s; }
              .ab-n3 { animation-delay: 1s; }
              .ab-n4 { animation-delay: 1.5s; }
              @keyframes ab-edge-travel { 0% { stroke-dashoffset: 160; } 100% { stroke-dashoffset: -160; } }
              @keyframes ab-node-pulse { 0%, 100% { opacity: 0.4; } 50% { opacity: 1; } }
            `}</style>
          </defs>
          <line x1="90" y1="65" x2="200" y2="150" stroke="#22c55e" strokeWidth="1.5" className="ab-edge" />
          <line x1="310" y1="65" x2="200" y2="150" stroke="#60a5fa" strokeWidth="1.5" className="ab-edge ab-e2" />
          <line x1="310" y1="235" x2="200" y2="150" stroke="#a78bfa" strokeWidth="1.5" className="ab-edge ab-e3" />
          <line x1="90" y1="235" x2="200" y2="150" stroke="#fb923c" strokeWidth="1.5" className="ab-edge ab-e4" />
          <circle cx="90" cy="65" r="7" fill="#22c55e" className="ab-node" />
          <circle cx="310" cy="65" r="7" fill="#60a5fa" className="ab-node ab-n2" />
          <circle cx="310" cy="235" r="7" fill="#a78bfa" className="ab-node ab-n3" />
          <circle cx="90" cy="235" r="7" fill="#fb923c" className="ab-node ab-n4" />
          <circle cx="200" cy="150" r="9" fill="white" className="ab-node" />
          <text x="90" y="51" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">identity</text>
          <text x="310" y="51" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">policy</text>
          <text x="310" y="252" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">approval</text>
          <text x="90" y="252" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">audit</text>
          <text x="200" y="137" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">deploy gate</text>
        </svg>
      </div>

      <div className="relative z-10 w-full max-w-[1400px] mx-auto px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24 py-16 lg:py-0">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 lg:gap-16 items-center">
          <div className="hero-stagger">
            <p
              className="mb-5 inline-flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-[11px]"
              style={{ borderColor: 'var(--accent-border)', background: 'var(--accent-dim)', color: 'var(--accent)' }}
            >
              Open-source preview · local Docker only
            </p>
            <h1
              className="mb-5 font-display font-black leading-[1.06] text-white text-[38px] sm:text-[48px] lg:text-[56px] xl:text-[64px]"
              style={{ letterSpacing: '-2.5px' }}
            >
              Know the agent.
              <br />
              <span
                style={{
                  background: 'linear-gradient(90deg, #22c55e 0%, #a78bfa 100%)',
                  WebkitBackgroundClip: 'text',
                  WebkitTextFillColor: 'transparent',
                }}
              >
                Gate the deploy.
              </span>
              <br />
              Audit every attempt.
            </h1>
            <p className="mb-8 text-base sm:text-lg leading-[1.75] max-w-[500px]" style={{ color: 'var(--text-muted)' }}>
              AgentBreeder deploys an agent container you already built to local Docker, and only
              after your identity provider confirms the agent: enabled, an active sponsor, a current
              review date and the grants you require. Production targets are not shipped yet.
            </p>
            <div
              className="mb-7 inline-flex items-center gap-3 rounded-xl border px-4 py-3 font-mono w-full sm:w-auto max-w-full"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border-hover)' }}
            >
              <span style={{ color: 'var(--accent)' }}>$</span>
              <span className="text-xs sm:text-sm text-white flex-1 truncate">{INSTALL_CMD}</span>
              <button
                className="ml-2 rounded-md border px-2.5 py-0.5 font-sans text-xs transition-colors flex-shrink-0"
                style={{
                  background: 'var(--bg-elevated)',
                  borderColor: 'var(--border)',
                  color: copied ? 'var(--accent)' : 'var(--text-muted)',
                }}
                onClick={copyInstallCmd}
              >
                {copied ? 'copied!' : 'copy'}
              </button>
            </div>
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
              <Link
                href="/docs/install"
                className="rounded-lg px-5 py-2.5 text-sm font-bold no-underline text-center transition-opacity hover:opacity-90"
                style={{ background: 'var(--accent)', color: '#000' }}
              >
                Run the local demo →
              </Link>
              <a
                href="https://github.com/agentbreeder/agentbreeder"
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-lg border px-5 py-2.5 text-sm no-underline text-center transition-colors"
                style={{ borderColor: 'var(--border-hover)', color: 'var(--text-muted)' }}
              >
                ★ &nbsp;Star on GitHub
              </a>
            </div>
          </div>

          <div className="w-full min-w-0 hero-reveal-late">
            <div
              className="overflow-hidden rounded-[14px] border"
              style={{
                background: 'var(--bg-surface)',
                borderColor: 'var(--border-hover)',
                boxShadow: '0 0 80px rgba(34,197,94,0.07), 0 20px 60px rgba(0,0,0,0.4)',
              }}
            >
              <div
                className="flex items-center justify-between border-b px-4 py-3"
                style={{ borderColor: 'var(--border)', background: 'rgba(255,255,255,0.02)' }}
              >
                <span className="font-mono text-xs" style={{ color: 'var(--text-muted)' }}>
                  agent.yaml
                </span>
                <span className="font-mono text-[11px]" style={{ color: 'var(--text-dim)' }}>
                  your image + an IdP identity
                </span>
              </div>
              <pre className="px-5 py-4 font-mono text-[12px] sm:text-[13px] leading-[1.9] overflow-x-auto">
                {MANIFEST_PREVIEW.split('\n').map((line, i) => (
                  <div key={i}>
                    <ManifestLine line={line} />
                  </div>
                ))}
              </pre>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-2">
              {CHECKS.map(({ label, color, state, lines }) => (
                <div
                  key={label}
                  className="rounded-xl border px-3.5 py-3"
                  style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                >
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
                    <span className="text-[12px] font-bold text-white">{label}</span>
                    <span className="ml-auto font-mono text-[9px]" style={{ color: 'var(--text-dim)' }}>
                      {state}
                    </span>
                  </div>
                  {lines.map((l) => (
                    <p key={l} className="font-mono text-[10px] leading-[1.6]" style={{ color: 'var(--text-dim)' }}>
                      {l}
                    </p>
                  ))}
                </div>
              ))}
            </div>

            <div
              className="mt-3 overflow-hidden rounded-xl border"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              <div
                className="flex items-center gap-1.5 border-b px-3.5 py-2"
                style={{ background: 'rgba(255,255,255,0.02)', borderColor: 'var(--border)' }}
              >
                <div className="h-2 w-2 rounded-full bg-[#ff5f57]" />
                <div className="h-2 w-2 rounded-full bg-[#ffbd2e]" />
                <div className="h-2 w-2 rounded-full bg-[#28ca41]" />
                <span className="mx-auto font-mono text-[10px]" style={{ color: 'var(--text-dim)' }}>
                  recorded session · IDs shortened
                </span>
              </div>
              <pre
                className="px-4 py-3 font-mono text-[10.5px] leading-[1.75] overflow-x-auto"
                aria-label="Recorded AgentBreeder CLI session"
              >
                {SESSION.map((line, i) => (
                  <div key={i} style={{ visibility: i < shown ? 'visible' : 'hidden' }}>
                    {line.kind === 'cmd' && <span style={{ color: 'var(--accent)' }}>$ </span>}
                    <span style={{ color: LINE_COLOR[line.kind] }}>{line.text}</span>
                  </div>
                ))}
              </pre>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
