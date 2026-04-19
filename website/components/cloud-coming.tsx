'use client';

import { useState } from 'react';

function WaitlistForm() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) return;
    const subject = encodeURIComponent('AgentBreeder Cloud Waitlist');
    const body = encodeURIComponent(
      `Hi,\n\nPlease add me to the AgentBreeder Cloud waitlist.\n\nEmail: ${email}\n\nThanks!`,
    );
    window.location.href = `mailto:hello@agent-breeder.com?subject=${subject}&body=${body}`;
    setSent(true);
  }

  return (
    <div>
      {sent ? (
        <div className="flex items-center gap-3">
          <span
            className="inline-flex items-center gap-2 rounded-xl px-6 py-3 text-sm font-semibold"
            style={{ background: 'rgba(167,139,250,0.12)', color: '#c084fc', border: '1px solid rgba(167,139,250,0.25)' }}
          >
            ✓ Your email client should have opened — send that email to lock your spot!
          </span>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex items-center gap-3">
          <input
            type="email"
            required
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded-xl border px-4 py-3 text-sm outline-none transition-all focus:border-[#a78bfa]"
            style={{
              background: 'var(--bg-surface)',
              borderColor: 'var(--border)',
              color: 'var(--text)',
              width: 260,
            }}
          />
          <button
            type="submit"
            className="inline-flex cursor-pointer items-center gap-2 rounded-xl border-0 px-7 py-3 text-sm font-bold transition-all hover:scale-[1.02] hover:opacity-90"
            style={{
              background: 'linear-gradient(135deg, #a78bfa 0%, #f472b6 100%)',
              color: '#fff',
              boxShadow: '0 0 40px rgba(167,139,250,0.30)',
            }}
          >
            ⚡ Join the waitlist
          </button>
        </form>
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
    desc: 'Every LLM call traced, every token counted, every tool execution logged. Full cost dashboards across all agents and teams.',
  },
  {
    icon: '🌐',
    title: 'Multi-cloud by default',
    desc: 'Deploy to AWS, GCP, or Azure with one flag. Failover between clouds automatically when regions degrade.',
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
    <section
      id="cloud"
      className="relative mx-auto max-w-[1200px] px-20 py-24"
    >
      {/* Background glow */}
      <div
        className="pointer-events-none absolute left-1/2 top-1/2 h-[600px] w-[600px] -translate-x-1/2 -translate-y-1/2 rounded-full"
        style={{ background: 'radial-gradient(circle, rgba(167,139,250,0.06) 0%, transparent 65%)' }}
      />

      <div className="relative z-10">
        {/* Badge */}
        <div className="mb-6 flex items-center gap-2">
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
          className="mb-4 text-[48px] font-black leading-[1.06] text-white"
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
          className="mb-4 max-w-[560px] text-lg leading-[1.7]"
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
        <div className="mb-14 grid grid-cols-3 gap-4">
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

        {/* Pricing preview */}
        <div
          className="mb-10 overflow-hidden rounded-2xl border"
          style={{ borderColor: 'rgba(167,139,250,0.20)', background: 'var(--bg-surface)' }}
        >
          <div
            className="border-b px-8 py-5"
            style={{ borderColor: 'rgba(167,139,250,0.15)', background: 'rgba(167,139,250,0.04)' }}
          >
            <p className="text-[11px] font-semibold uppercase tracking-[1.5px]" style={{ color: '#a78bfa' }}>
              Pricing preview
            </p>
          </div>
          <div className="grid grid-cols-3 divide-x" style={{ borderColor: 'var(--border)' }}>
            {[
              {
                name: 'Starter',
                price: 'Free',
                sub: 'forever',
                features: ['3 agents', '10k runs / mo', 'Community support', 'OSS CLI included'],
                color: 'var(--accent)',
                highlight: false,
              },
              {
                name: 'Pro',
                price: '$49',
                sub: 'per seat / mo',
                features: ['Unlimited agents', '500k runs / mo', 'Cost dashboards', 'RBAC + audit trail', 'Priority support'],
                color: '#c084fc',
                highlight: true,
              },
              {
                name: 'Enterprise',
                price: 'Custom',
                sub: 'contact us',
                features: ['SSO + SCIM', 'Private VPC deploy', 'SLA + dedicated CSM', 'On-prem option', 'Custom contracts'],
                color: '#fb923c',
                highlight: false,
              },
            ].map(({ name, price, sub, features, color, highlight }) => (
              <div
                key={name}
                className="relative px-8 py-7"
                style={highlight ? { background: 'rgba(167,139,250,0.04)' } : {}}
              >
                {highlight && (
                  <span
                    className="absolute right-5 top-5 rounded-full px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider"
                    style={{ background: 'rgba(167,139,250,0.15)', color: '#c084fc' }}
                  >
                    Most popular
                  </span>
                )}
                <p className="mb-1 text-sm font-bold text-white">{name}</p>
                <p className="mb-0.5 text-3xl font-black" style={{ color, letterSpacing: '-1px' }}>
                  {price}
                </p>
                <p className="mb-5 text-xs" style={{ color: 'var(--text-dim)' }}>{sub}</p>
                <ul className="space-y-2">
                  {features.map(f => (
                    <li key={f} className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-muted)' }}>
                      <span style={{ color }}>✓</span>
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

        {/* Waitlist CTA */}
        <WaitlistForm />
      </div>
    </section>
  );
}
