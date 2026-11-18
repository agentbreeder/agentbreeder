'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { CheckIcon, CircleIcon, CopyIcon, GitHubIcon, PauseIcon, PlayIcon } from './icons';

// Pinned: keep in step with the released version (docs/cross-repo-sync.md).
const INSTALL_CMD = 'npx agentbreeder@3.0.0-alpha.3 quickstart';

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

// Classes, not colours: inside the dark island these resolve to the dark palette (§8.4).
const LINE_CLASS: Record<Line['kind'], string> = {
  cmd: 'text-foreground',
  out: 'text-muted-foreground',
  ok: 'text-success',
  warn: 'text-warning',
  deny: 'text-destructive',
  note: 'text-syntax-comment',
};

// What each check really is today. "opt-in" = the operator has to configure it.
const CHECKS = [
  { label: 'identity', always: true, state: 'on every deploy', lines: ['enabled · sponsor · review date', 'roles · groups · attributes'] },
  { label: 'policy', always: false, state: 'opt-in', lines: ['Cedar · OPA · AuthZEN', 'verified deployer required'] },
  { label: 'approval', always: false, state: 'opt-in', lines: ['second person', 'bound to the exact revision'] },
  { label: 'mcp egress', always: false, state: 'opt-in', lines: ['per tool call, HTTP only', 'STDIO not mediated'] },
];

function ManifestLine({ line }: { line: string }) {
  const kv = line.match(/^(\s*)([a-z_]+)(:)(\s*)(.*)$/);
  if (!kv) return <span>{line}</span>;
  const [, indent, key, colon, space, value] = kv;
  const isNumber = /^\d+$/.test(value);
  return (
    <span>
      {indent}
      <span className="text-syntax-key">{key}</span>
      {colon}
      {space}
      {value && <span className={isNumber ? 'text-syntax-number' : 'text-syntax-string'}>{value}</span>}
    </span>
  );
}

// Loops pause off-screen and in a hidden tab (design v2 §9.5).
function useOnScreen<T extends Element>() {
  const ref = useRef<T | null>(null);
  // False until the observer reports: the server and no-JS render the hero paused, so the CSS loop
  // never runs without the script that can pause it (WCAG 2.2.2).
  const [active, setActive] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let inView = false;
    const update = () => setActive(inView && document.visibilityState === 'visible');
    const io = new IntersectionObserver(([entry]) => {
      inView = entry.isIntersecting;
      update();
    });
    io.observe(el);
    document.addEventListener('visibilitychange', update);
    return () => {
      io.disconnect();
      document.removeEventListener('visibilitychange', update);
    };
  }, []);
  return [ref, active] as const;
}

function useSessionReplay(total: number, active: boolean, stopped: boolean) {
  // null = not animating: server render, no-JS and reduced-motion users see the whole session,
  // and so does anyone who pressed pause (WCAG 2.2.2).
  const [step, setStep] = useState<number | null>(null);
  useEffect(() => {
    if (!active || stopped) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const timer = window.setInterval(() => {
      // Reveal one line per tick, hold the finished session for a few beats, then replay it.
      setStep((s) => (s === null || s >= total + 6 ? 0 : s + 1));
    }, 650);
    return () => window.clearInterval(timer);
  }, [total, active, stopped]);
  return step === null || stopped ? total : Math.min(step, total);
}

// The gate (design v2 §3, §6.3): four hairline checks converge on one gate; only the path that
// passes it is the gate line, in --brand. It sits partly under the glass sheet, so the blur shows
// the line passing through. Decorative: the checks are spelled out in the sheet.
function GateDiagram() {
  return (
    <div className="ab-diagram pointer-events-none absolute -inset-x-6 inset-y-10 sm:-inset-x-12" aria-hidden="true">
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
        {[12, 37, 63, 88].map((y) => (
          <line
            key={y}
            x1="0"
            y1={y}
            x2="28"
            y2="50"
            className="stroke-border-strong forced-colors:stroke-[CanvasText]"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
      {/* The gate: a hairline ring. */}
      <span className="absolute left-[28%] top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border border-control-border bg-background" />
      {/* The gate line: 1.5 px --brand, from the gate to the edge, and the dot that travels it. */}
      <span className="absolute left-[calc(28%+6px)] right-0 top-1/2 h-[1.5px] -translate-y-1/2 bg-brand forced-colors:bg-[CanvasText]">
        <span className="gate-run absolute inset-0">
          <span className="gate-dot absolute -top-[2.25px] left-0 h-1.5 w-1.5 rounded-full bg-brand" />
        </span>
      </span>
    </div>
  );
}

export function Hero() {
  const [copied, setCopied] = useState(false);
  const [sectionRef, onScreen] = useOnScreen<HTMLElement>();
  // The replay and the gate dot loop, so a pause control stops both (WCAG 2.2.2).
  // Show the complete recording on arrival; replay starts only when requested.
  const [stopped, setStopped] = useState(true);
  const active = onScreen && !stopped;
  const shown = useSessionReplay(SESSION.length, onScreen, stopped);

  function copyInstallCmd() {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(INSTALL_CMD).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      });
    }
  }

  return (
    <section
      ref={sectionRef}
      data-paused={active ? 'false' : 'true'}
      className="relative isolate w-full overflow-hidden lg:min-h-[calc(100svh-3.5rem)] lg:flex lg:items-center"
    >
      <div className="hero-backdrop" aria-hidden="true" />

      <div className="ab-container relative py-14 sm:py-16 lg:py-20">
        <div className="grid grid-cols-1 items-center gap-12 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:gap-12">
          <div>
            <p className="ab-label mb-5">open-source preview · local docker only</p>
            <h1 className="hero-title mb-6 font-display text-display font-semibold text-foreground">
              Know the agent.
              <br />
              <span className="gate-underline">Gate the deploy.</span>
              <br />
              Audit every attempt.
            </h1>
            <div className="hero-stagger">
              <p className="ab-lede mb-8 text-base sm:text-lg">
                AgentBreeder deploys an agent container you already built to local Docker, and only
                after your identity provider confirms the agent: enabled, an active sponsor, a current
                review date and the grants you require. Production targets are not shipped yet.
              </p>
              <div className="dark ab-island mb-6 flex w-full max-w-full items-center gap-3 py-1.5 pl-4 pr-1.5 font-mono sm:w-fit">
                <span className="text-muted-foreground" aria-hidden="true">
                  $
                </span>
                <code className="min-w-0 flex-1 break-words text-[13px] text-foreground sm:text-sm">{INSTALL_CMD}</code>
                <button
                  type="button"
                  onClick={copyInstallCmd}
                  aria-label="Copy the install command"
                  className="inline-flex min-h-[44px] min-w-[44px] flex-shrink-0 items-center justify-center gap-1.5 rounded-md px-2.5 font-sans text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                >
                  {copied ? <CheckIcon size={16} /> : <CopyIcon size={16} />}
                  <span aria-hidden="true" className="hidden sm:inline">
                    {copied ? 'copied' : 'copy'}
                  </span>
                </button>
                <span role="status" aria-live="polite" className="sr-only">
                  {copied ? 'Copied' : ''}
                </span>
              </div>
              <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
                <Link href="/docs/install" className="ab-btn ab-btn-primary">
                  Run the local demo
                </Link>
                <a
                  href="https://github.com/agentbreeder/agentbreeder"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ab-btn ab-btn-secondary"
                >
                  <GitHubIcon />
                  Star on GitHub
                </a>
              </div>
            </div>
          </div>

          <div className="relative min-w-0">
            {/* What the sheet's glass shows through (§6.3, owner decision 2026-11-16): two tint
                glows directly behind it, so the blur has colour to carry. First in DOM order, so
                it paints under the gate diagram and never dims the gate line. */}
            <div className="hero-glow" aria-hidden="true" />
            <GateDiagram />

            {/* One glass sheet (design v2 §6.1): nested glass would not blur through its parent. */}
            <div className="hero-sheet-enter relative">
              <div className="ab-sheet glass p-3 sm:p-4">
                <div className="flex items-center justify-between gap-3 px-1 pb-2.5">
                  <span className="font-mono text-[13px] text-foreground">agent.yaml</span>
                  <span className="ab-label text-xs">your image + an IdP identity</span>
                </div>
                <pre
                  tabIndex={0}
                  role="region"
                  aria-label="Example agent.yaml"
                  className="overflow-x-auto rounded-xl border border-border bg-code-background px-4 py-3 font-mono text-[13px] leading-[1.8] text-foreground">
                  {MANIFEST_PREVIEW.split('\n').map((line, i) => (
                    <div key={i}>
                      <ManifestLine line={line} />
                    </div>
                  ))}
                </pre>

                <ul className="my-3 grid list-none grid-cols-1 gap-x-4 border-y border-glass-border px-1 py-3 sm:grid-cols-2">
                  {CHECKS.map(({ label, always, state, lines }) => (
                    <li key={label} className="py-1.5">
                      <div className="mb-1 flex items-center gap-2">
                        <span className="text-foreground">
                          {always ? <CheckIcon size={14} /> : <CircleIcon size={14} />}
                        </span>
                        <span className="font-mono text-[13px] font-medium text-foreground">{label}</span>
                        <span className="ml-auto font-mono text-xs text-muted-foreground">{state}</span>
                      </div>
                      {lines.map((l) => (
                        <p key={l} className="pl-[22px] font-mono text-xs leading-[1.6] text-muted-foreground">
                          {l}
                        </p>
                      ))}
                    </li>
                  ))}
                </ul>

                <div className="dark ab-island overflow-hidden">
                  <div className="ab-island-bar">
                    <span className="ab-label text-xs">recorded session · ids shortened</span>
                    <button
                      type="button"
                      onClick={() => setStopped((v) => !v)}
                      className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-2 font-sans text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground motion-reduce:hidden"
                    >
                      {stopped ? <PlayIcon size={14} /> : <PauseIcon size={14} />}
                      {stopped ? 'Play animation' : 'Pause animation'}
                    </button>
                  </div>
                  <pre
                    tabIndex={0}
                    role="region"
                    aria-label="Recorded AgentBreeder CLI session"
                    className="overflow-x-auto px-4 py-3 font-mono text-xs leading-[1.75]"
                  >
                    {/* Screen readers get the whole transcript once, not the replay. */}
                    <span className="sr-only">
                      {SESSION.map((line) => (line.kind === 'cmd' ? `$ ${line.text}` : line.text)).join('\n')}
                    </span>
                    <span aria-hidden="true">
                      {SESSION.map((line, i) => (
                        <span key={i} className={`block ${i < shown ? 'visible' : 'invisible'}`}>
                          {line.kind === 'cmd' && <span className="text-muted-foreground">$ </span>}
                          <span className={LINE_CLASS[line.kind]}>{line.text}</span>
                        </span>
                      ))}
                    </span>
                  </pre>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
