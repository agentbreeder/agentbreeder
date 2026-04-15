'use client';

import Link from 'next/link';

const YAML_PREVIEW = `# identity
name: customer-support-agent
version: 1.0.0
team: customer-success
framework: langgraph

# model config
model:
  primary: claude-sonnet-4
  fallback: gpt-4o
  temperature: 0.7

# deploy to any cloud
deploy:
  cloud: gcp
  runtime: cloud-run`;

function YamlLine({ line }: { line: string }) {
  // Colorize YAML tokens
  const comment = line.match(/^(\s*)(#.*)$/);
  if (comment) {
    return (
      <span>
        {comment[1]}
        <span style={{ color: '#3f3f46', fontStyle: 'italic' }}>{comment[2]}</span>
      </span>
    );
  }
  const kv = line.match(/^(\s*)([a-z_]+)(:)(\s*)(.*)$/);
  if (kv) {
    const [, indent, key, colon, space, value] = kv;
    const isKeyword = ['langgraph', 'crewai', 'claude_sdk', 'google_adk', 'gcp', 'local', 'cloud-run'].includes(value);
    const isNumber = /^\d+(\.\d+)?$/.test(value);
    return (
      <span>
        {indent}
        <span style={{ color: '#93c5fd' }}>{key}</span>
        {colon}{space}
        {value && (
          <span style={{ color: isKeyword ? '#c084fc' : isNumber ? '#fb923c' : '#86efac' }}>
            {value}
          </span>
        )}
      </span>
    );
  }
  return <span style={{ color: '#e4e4e7' }}>{line}</span>;
}

export function Hero() {
  return (
    <section
      className="relative mx-auto grid max-w-[1200px] grid-cols-2 items-center gap-16 px-20 pb-20 pt-24"
    >
      {/* Background glows */}
      <div
        className="pointer-events-none absolute -right-24 -top-24 h-[500px] w-[500px] rounded-full"
        style={{ background: 'radial-gradient(circle, rgba(34,197,94,0.06) 0%, transparent 70%)' }}
      />
      <div
        className="pointer-events-none absolute bottom-0 left-1/3 h-[300px] w-[300px] rounded-full"
        style={{ background: 'radial-gradient(circle, rgba(167,139,250,0.04) 0%, transparent 70%)' }}
      />

      {/* Left column */}
      <div className="relative z-10">
        <p
          className="mb-5 font-mono text-[11px] uppercase tracking-[1.5px]"
          style={{ color: 'var(--text-dim)' }}
        >
          // open source · apache 2.0
        </p>
        <h1
          className="mb-5 text-[54px] font-black leading-[1.08] text-white"
          style={{ letterSpacing: '-2.5px' }}
        >
          Build agents.<br />
          <span
            style={{
              background: 'linear-gradient(90deg, #22c55e 0%, #a78bfa 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
            }}
          >
            Deploy anywhere.
          </span>
          <br />
          Govern free.
        </h1>
        <p className="mb-8 max-w-[420px] text-base leading-[1.75]" style={{ color: 'var(--text-muted)' }}>
          One YAML file. Any framework. Any cloud. Governance, RBAC, cost tracking and
          audit trail — automatic on every deploy.
        </p>
        <div
          className="mb-7 inline-flex items-center gap-3 rounded-xl border px-4 py-3 font-mono"
          style={{ background: 'var(--bg-surface)', borderColor: 'var(--border-hover)' }}
        >
          <span style={{ color: 'var(--accent)' }}>$</span>
          <span className="text-sm text-white">pip install agentbreeder</span>
          <button
            className="ml-2 rounded-md border px-2.5 py-0.5 font-sans text-xs transition-colors"
            style={{
              background: 'var(--bg-elevated)',
              borderColor: 'var(--border)',
              color: 'var(--text-muted)',
            }}
            onClick={() => navigator.clipboard.writeText('pip install agentbreeder')}
          >
            copy
          </button>
        </div>
        <div className="flex items-center gap-3">
          <Link
            href="/docs"
            className="rounded-lg px-5 py-2.5 text-sm font-bold no-underline transition-opacity hover:opacity-90"
            style={{ background: 'var(--accent)', color: '#000' }}
          >
            Read the docs →
          </Link>
          <a
            href="https://github.com/rajitsaha/agentbreeder"
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg border px-5 py-2.5 text-sm no-underline transition-colors"
            style={{ borderColor: 'var(--border-hover)', color: 'var(--text-muted)' }}
          >
            ★ &nbsp;Star on GitHub
          </a>
        </div>
      </div>

      {/* Right column */}
      <div className="relative z-10">
        {/* YAML card */}
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
            <span
              className="flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold"
              style={{
                background: 'var(--accent-dim)',
                borderColor: 'var(--accent-border)',
                color: 'var(--accent)',
              }}
            >
              <span
                className="h-1.5 w-1.5 animate-pulse rounded-full"
                style={{ background: 'var(--accent)' }}
              />
              ready to deploy
            </span>
          </div>
          <pre className="px-5 py-5 font-mono text-[13px] leading-[2]">
            {YAML_PREVIEW.split('\n').map((line, i) => (
              <div key={i}>
                <YamlLine line={line} />
              </div>
            ))}
          </pre>
        </div>

        {/* Terminal deploy output */}
        <div
          className="mt-3 overflow-hidden rounded-xl border"
          style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
        >
          <div
            className="flex items-center gap-1.5 border-b px-3.5 py-2.5"
            style={{ background: 'rgba(255,255,255,0.02)', borderColor: 'var(--border)' }}
          >
            <div className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
            <div className="h-2.5 w-2.5 rounded-full bg-[#ffbd2e]" />
            <div className="h-2.5 w-2.5 rounded-full bg-[#28ca41]" />
            <span className="mx-auto font-mono text-[11px]" style={{ color: 'var(--text-dim)' }}>
              terminal
            </span>
          </div>
          <pre className="px-4 py-3.5 font-mono text-xs leading-[1.9]">
            <span style={{ color: 'var(--accent)' }}>$ </span>
            <span className="text-white">agentbreeder deploy</span>{'\n'}
            <span style={{ color: 'var(--text-dim)' }}>✓ Validating agent.yaml{'\n'}</span>
            <span style={{ color: 'var(--text-dim)' }}>✓ Building container image{'\n'}</span>
            <span style={{ color: 'var(--text-dim)' }}>✓ Deploying to GCP Cloud Run{'\n'}</span>
            <span style={{ color: 'var(--text-dim)' }}>✓ Registered in org registry{'\n'}</span>
            <span style={{ color: '#60a5fa' }}>→ https://support-v1-abc.run.app</span>
          </pre>
        </div>
      </div>
    </section>
  );
}
