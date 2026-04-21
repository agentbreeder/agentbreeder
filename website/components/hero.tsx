'use client';

import Link from 'next/link';

const YAML_PREVIEW = `# identity
name: customer-support-agent
version: 1.0.0
team: customer-success
framework: langgraph

# model config
model:
  primary: ollama/gemma4
  fallback: gpt-4o
  temperature: 0.7

# run locally
deploy:
  cloud: local
  runtime: docker`;

function YamlLine({ line }: { line: string }) {
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
    const isKeyword = ['langgraph', 'crewai', 'claude_sdk', 'google_adk', 'gcp', 'local', 'cloud-run', 'docker', 'ollama'].includes(value);
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
    <section className="relative w-full min-h-[calc(100vh-3.5rem)] flex items-center">
      {/* Full-bleed background glows */}
      <div
        className="pointer-events-none absolute inset-0 overflow-hidden"
        aria-hidden
      >
        <div
          className="absolute -right-40 -top-40 h-[700px] w-[700px] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(34,197,94,0.07) 0%, transparent 65%)' }}
        />
        <div
          className="absolute bottom-0 left-1/4 h-[400px] w-[400px] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(167,139,250,0.05) 0%, transparent 65%)' }}
        />
        <div
          className="absolute -left-20 top-1/3 h-[300px] w-[300px] rounded-full"
          style={{ background: 'radial-gradient(circle, rgba(96,165,250,0.04) 0%, transparent 65%)' }}
        />
        {/* Animated knowledge graph */}
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
              @keyframes ab-edge-travel {
                0% { stroke-dashoffset: 160; }
                100% { stroke-dashoffset: -160; }
              }
              @keyframes ab-node-pulse {
                0%, 100% { opacity: 0.4; }
                50% { opacity: 1; }
              }
            `}</style>
          </defs>
          {/* Edges: center → outer nodes */}
          <line x1="200" y1="150" x2="310" y2="65" stroke="#22c55e" strokeWidth="1.5" className="ab-edge" />
          <line x1="200" y1="150" x2="310" y2="235" stroke="#a855f7" strokeWidth="1.5" className="ab-edge ab-e2" />
          <line x1="200" y1="150" x2="90" y2="235" stroke="#3b82f6" strokeWidth="1.5" className="ab-edge ab-e3" />
          <line x1="200" y1="150" x2="90" y2="65" stroke="#f59e0b" strokeWidth="1.5" className="ab-edge ab-e4" />
          {/* Outer nodes */}
          <circle cx="310" cy="65" r="7" fill="#22c55e" className="ab-node" />
          <circle cx="310" cy="235" r="7" fill="#a855f7" className="ab-node ab-n2" />
          <circle cx="90" cy="235" r="7" fill="#3b82f6" className="ab-node ab-n3" />
          <circle cx="90" cy="65" r="7" fill="#f59e0b" className="ab-node ab-n4" />
          {/* Center node */}
          <circle cx="200" cy="150" r="9" fill="white" className="ab-node" />
          {/* Labels */}
          <text x="310" y="51" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">deploy</text>
          <text x="328" y="250" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">RBAC</text>
          <text x="72" y="250" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">cost</text>
          <text x="90" y="51" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">registry</text>
          <text x="200" y="137" textAnchor="middle" fill="#9ca3af" fontSize="9" fontFamily="monospace">agent.yaml</text>
        </svg>
      </div>

      <div className="relative z-10 w-full max-w-[1400px] mx-auto px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24 py-16 lg:py-0">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 lg:gap-16 items-center">

          {/* Left column */}
          <div>
            <h1
              className="mb-5 font-black leading-[1.06] text-white text-[38px] sm:text-[48px] lg:text-[56px] xl:text-[64px]"
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
            <p className="mb-8 text-base sm:text-lg leading-[1.75] max-w-[480px]" style={{ color: 'var(--text-muted)' }}>
              One YAML file. Any framework. Any cloud. Governance, RBAC, cost tracking and
              audit trail — automatic on every deploy.
            </p>
            <div
              className="mb-7 inline-flex items-center gap-3 rounded-xl border px-4 py-3 font-mono w-full sm:w-auto"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border-hover)' }}
            >
              <span style={{ color: 'var(--accent)' }}>$</span>
              <span className="text-sm text-white flex-1">pip install agentbreeder</span>
              <button
                className="ml-2 rounded-md border px-2.5 py-0.5 font-sans text-xs transition-colors flex-shrink-0"
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
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
              <Link
                href="/docs"
                className="rounded-lg px-5 py-2.5 text-sm font-bold no-underline text-center transition-opacity hover:opacity-90"
                style={{ background: 'var(--accent)', color: '#000' }}
              >
                Read the docs →
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

          {/* Right column */}
          <div className="w-full min-w-0">
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
              <pre className="px-5 py-5 font-mono text-[12px] sm:text-[13px] leading-[2] overflow-x-auto">
                {YAML_PREVIEW.split('\n').map((line, i) => (
                  <div key={i}>
                    <YamlLine line={line} />
                  </div>
                ))}
              </pre>
            </div>

            {/* Capability grid: MCP · A2A · Prompts · RAG */}
            <div className="mt-3 grid grid-cols-2 gap-2">
              {[
                {
                  label: 'Prompts',
                  color: '#f472b6',
                  icon: '✦',
                  lines: ['versioned · cached', 'variables · v3'],
                },
                {
                  label: 'RAG',
                  color: '#fb923c',
                  icon: '◈',
                  lines: ['vector · graph', 'hybrid search'],
                },
                {
                  label: 'MCP',
                  color: '#34d399',
                  icon: '⬡',
                  lines: ['4 servers · auto-discover', 'tool registry'],
                },
                {
                  label: 'A2A',
                  color: '#e879f9',
                  icon: '⇄',
                  lines: ['agent-to-agent', 'JSON-RPC · auth'],
                },
              ].map(({ label, color, icon, lines }) => (
                <div
                  key={label}
                  className="rounded-xl border px-3.5 py-3"
                  style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                >
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="text-base" style={{ color }}>{icon}</span>
                    <span className="text-[12px] font-bold text-white">{label}</span>
                    <span
                      className="ml-auto h-1.5 w-1.5 rounded-full animate-pulse"
                      style={{ background: color }}
                    />
                  </div>
                  {lines.map(l => (
                    <p key={l} className="font-mono text-[10px] leading-[1.6]" style={{ color: 'var(--text-dim)' }}>{l}</p>
                  ))}
                </div>
              ))}
            </div>

            {/* Terminal deploy output */}
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
                  terminal
                </span>
              </div>
              <pre className="px-4 py-3 font-mono text-[11px] leading-[1.8] overflow-x-auto">
                <span style={{ color: 'var(--accent)' }}>$ </span>
                <span className="text-white">agentbreeder deploy</span>{'\n'}
                <span style={{ color: 'var(--text-dim)' }}>✓ Validating · Building · Deploying{'\n'}</span>
                <span style={{ color: 'var(--text-dim)' }}>✓ Prompts cached · RAG indexed · MCP wired{'\n'}</span>
                <span style={{ color: '#60a5fa' }}>→ http://localhost:8080/support-v1</span>
              </pre>
            </div>
          </div>

        </div>
      </div>
    </section>
  );
}
