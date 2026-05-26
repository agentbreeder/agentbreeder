'use client';

import { useState } from 'react';

export function WaitlistForm() {
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'sent' | 'error'>('idle');
  const [error, setError] = useState('');
  const loading = status === 'loading';

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!email || loading) return;
    const company = (e.currentTarget.elements.namedItem('company') as HTMLInputElement | null)?.value ?? '';
    setStatus('loading');
    setError('');
    try {
      const res = await fetch('/api/waitlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, company }),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        throw new Error(data.error || 'Something went wrong. Please try again.');
      }
      setStatus('sent');
    } catch (err) {
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Something went wrong. Please try again.');
    }
  }

  return (
    <div id="waitlist">
      {status === 'sent' ? (
        <div role="status" aria-live="polite">
          <span
            className="inline-flex items-center gap-2 rounded-xl px-6 py-3 text-sm font-semibold"
            style={{ background: 'rgba(167,139,250,0.12)', color: '#c084fc', border: '1px solid rgba(167,139,250,0.25)' }}
          >
            ✓ You&rsquo;re on the list — we&rsquo;ll email you the moment access opens.
          </span>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
          {/* Honeypot — hidden from users; bots that fill it are silently dropped. */}
          <input type="text" name="company" tabIndex={-1} autoComplete="off" aria-hidden="true" className="hidden" />
          <input
            type="email"
            required
            disabled={loading}
            aria-label="Email address"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded-xl border px-4 py-3 text-sm outline-none transition-all focus:border-[#a78bfa] w-full sm:w-[260px]"
            style={{
              background: 'var(--bg-surface)',
              borderColor: 'var(--border)',
              color: 'var(--text)',
            }}
          />
          <button
            type="submit"
            disabled={loading}
            className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-xl border-0 px-7 py-3 text-sm font-bold transition-all hover:scale-[1.02] hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-70"
            style={{
              background: 'linear-gradient(135deg, #a78bfa 0%, #f472b6 100%)',
              color: '#fff',
              boxShadow: '0 0 40px rgba(167,139,250,0.30)',
            }}
          >
            {loading ? 'Joining…' : '⚡ Join the waitlist'}
          </button>
        </form>
      )}
      {status === 'error' && (
        <p role="alert" className="mt-3 text-xs" style={{ color: '#f87171' }}>
          {error}
        </p>
      )}
      <p className="mt-3 text-xs" style={{ color: 'var(--text-dim)' }}>
        Early access opens Q3 2026 · No credit card required
      </p>
    </div>
  );
}

const CLOUD_FEATURES = [
  {
    icon: '⚡',
    title: 'Zero-config deploys',
    desc: 'Push agent.yaml. We provision, scale, and secure the infrastructure. No AWS console, no Kubernetes YAML, no Terraform.',
  },
  {
    icon: '🔒',
    title: 'Governance out of the box',
    desc: 'RBAC, cost attribution, audit trail, and PII guardrails enforced at the platform level — not bolted on per team.',
  },
  {
    icon: '📊',
    title: 'Fleet-wide observability',
    desc: 'Every LLM call traced, every token counted, every tool execution logged. Full cost tracking across all agents and teams.',
  },
  {
    icon: '🌐',
    title: 'Multi-cloud by default',
    desc: 'Deploy to AWS, GCP, or Azure with one flag — same agent.yaml, same governance, same CLI.',
  },
  {
    icon: '🏪',
    title: 'Private marketplace',
    desc: 'Share agents, prompts, tools, and MCP servers across your org. One-click deploy from an internal catalog.',
  },
  {
    icon: '🤝',
    title: 'Agent-to-agent network',
    desc: 'A2A protocol built in. Your agents can discover and call each other across teams, clouds, and frameworks.',
  },
];

export function CloudComing() {
  return (
    <section id="cloud" className="relative w-full py-20 lg:py-28 overflow-hidden">
      {/* Background glow */}
      <div
        className="pointer-events-none absolute left-1/2 top-1/2 h-[600px] w-[600px] -translate-x-1/2 -translate-y-1/2 rounded-full"
        style={{ background: 'radial-gradient(circle, rgba(167,139,250,0.06) 0%, transparent 65%)' }}
      />

      <div className="relative z-10 max-w-[1400px] mx-auto px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24">
        {/* Badge */}
        <div className="mb-6">
          <span
            className="inline-flex items-center gap-2 rounded-full border px-4 py-1.5 text-xs font-bold uppercase tracking-widest"
            style={{
              background: 'rgba(167,139,250,0.10)',
              borderColor: 'rgba(167,139,250,0.30)',
              color: '#c084fc',
            }}
          >
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[#c084fc]" />
            Coming soon
          </span>
        </div>

        {/* Headline */}
        <h2
          className="mb-4 text-[36px] sm:text-[48px] font-black leading-[1.06] text-white"
          style={{ letterSpacing: '-2px' }}
        >
          AgentBreeder{' '}
          <span
            style={{
              background: 'linear-gradient(90deg, #a78bfa 0%, #f472b6 60%, #fb923c 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
            }}
          >
            Cloud
          </span>
        </h2>
        <p
          className="mb-4 max-w-[560px] text-base sm:text-lg leading-[1.7]"
          style={{ color: 'var(--text-muted)' }}
        >
          The open-source CLI you love — now with a fully managed control plane.
          Ship your first production agent in{' '}
          <span className="font-semibold text-white">under 5 minutes</span>,
          with no infrastructure to manage.
        </p>
        <p
          className="mb-12 max-w-[560px] text-base leading-[1.7]"
          style={{ color: 'var(--text-dim)' }}
        >
          Same <code className="rounded px-1 py-0.5 font-mono text-sm" style={{ background: 'var(--bg-surface)', color: 'var(--accent)' }}>agent.yaml</code> format.
          Same CLI. Just add{' '}
          <code className="rounded px-1 py-0.5 font-mono text-sm" style={{ background: 'var(--bg-surface)', color: '#c084fc' }}>cloud: claude-managed</code>{' '}
          to your deploy block.
        </p>

        {/* Feature grid */}
        <div className="mb-14 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {CLOUD_FEATURES.map(({ icon, title, desc }) => (
            <div
              key={title}
              className="rounded-xl border p-5 transition-colors"
              style={{
                background: 'var(--bg-surface)',
                borderColor: 'var(--border)',
              }}
            >
              <div className="mb-3 text-2xl">{icon}</div>
              <h3 className="mb-1.5 text-[15px] font-bold text-white">{title}</h3>
              <p className="text-sm leading-[1.65]" style={{ color: 'var(--text-muted)' }}>
                {desc}
              </p>
            </div>
          ))}
        </div>

        {/* Waitlist CTA */}
        <WaitlistForm />
      </div>
    </section>
  );
}
