import type { Metadata } from 'next';
import Link from 'next/link';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';

export const metadata: Metadata = {
  title: 'Cloud — Managed AgentBreeder',
  description:
    'Managed AgentBreeder at console.agentbreeder.io — hosted registry, RBAC, observability, and billing. Your agents stay in your cloud.',
  alternates: { canonical: '/cloud' },
};

const WAITLIST_HREF =
  'mailto:hello@agentbreeder.io?subject=AgentBreeder%20Cloud%20Waitlist&body=Please%20add%20me%20to%20the%20console.agentbreeder.io%20waitlist.%20Team%20size%3A%20___%20%7C%20Cloud%3A%20AWS%20%2F%20GCP%20%2F%20Azure%20%7C%20Use%20case%3A%20___';
const CONSOLE_URL = 'https://console.agentbreeder.io';
const GITHUB_URL = 'https://github.com/agentbreeder/agentbreeder';

const HOSTED = [
  'Cross-team registry of agents, prompts, tools, RAG indexes',
  'OAuth + SSO + RBAC + audit log',
  'Cost tracking per team / agent / model / call',
  'Tracing + evals + replay across runs',
  'Org-wide policy enforcement (guardrails, budgets, approvals)',
  'Multi-cloud deploy orchestration',
];

const YOURS = [
  'The agent containers run in YOUR cloud account',
  'Your VPC, your network rules, your secrets manager',
  'Your model inference endpoints (or BYO LLM gateway)',
  'Your data — nothing leaves your perimeter without a configured egress',
  'Your existing IAM, your existing observability sinks',
];

const COMPARISON = [
  { feature: 'CLI + engine + connectors', oss: 'Yes (Apache 2.0)', cloud: 'Yes' },
  { feature: 'Per-team isolated registry', oss: 'Self-host', cloud: 'Hosted, multi-tenant' },
  { feature: 'RBAC + SSO + audit', oss: 'You wire it up', cloud: 'Out of the box' },
  { feature: 'Cost + tracing + evals', oss: 'You provision Postgres, OTLP sinks', cloud: 'Out of the box' },
  { feature: 'Upgrades + migrations', oss: 'You run alembic', cloud: 'Managed' },
  { feature: 'Agent containers run in', oss: 'Your cloud', cloud: 'Your cloud (same)' },
  { feature: 'Support', oss: 'GitHub issues, community', cloud: 'Email + SLA (paid tiers)' },
];

const FAQ = [
  {
    q: 'When does the Cloud launch?',
    a: 'Private alpha is rolling out in 2026. Waitlist members get invited first. The exact GA date depends on alpha feedback — we are not putting an arbitrary date on the calendar.',
  },
  {
    q: 'How does pricing work?',
    a: 'Free tier for individuals and tiny teams. Paid tiers based on number of deployed agents, traces ingested, and team seats. Exact prices announced at launch — waitlist members get founder pricing.',
  },
  {
    q: 'Can I use my own cloud accounts (AWS / GCP / Azure)?',
    a: 'Yes. That is the point — agents always run in your cloud, never on our infrastructure. Cloud only hosts the control plane (registry, RBAC, observability, billing).',
  },
  {
    q: 'Is the open-source project still maintained?',
    a: 'Yes. The OSS engine, CLI, and SDK are the foundation Cloud is built on. Every feature ships to OSS first. Self-hosting will always be a supported path.',
  },
  {
    q: 'What happens to my data if I leave?',
    a: 'Export your full registry as YAML at any time. Agents in your cloud are unaffected. The data we hold (registry metadata, traces, costs) is exportable.',
  },
];

export default function CloudPage() {
  return (
    <>
      <Nav />
      <main className="mx-auto max-w-[1100px] px-4 sm:px-8 py-16 sm:py-24">
        {/* Hero */}
        <section className="mb-24 max-w-[820px]">
          <p
            className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
            style={{ color: 'var(--accent)' }}
          >
            Coming soon · console.agentbreeder.io
          </p>
          <h1
            className="mb-6 font-display text-[40px] sm:text-[56px] font-extrabold text-white"
            style={{ letterSpacing: '-1.5px', lineHeight: 1.05 }}
          >
            Managed AgentBreeder. <br className="hidden sm:inline" />
            Zero DevOps.
          </h1>
          <p
            className="mb-10 text-[18px] sm:text-[20px] leading-[1.6]"
            style={{ color: 'var(--text-muted)' }}
          >
            All the registry, RBAC, observability, and billing of AgentBreeder &mdash;
            hosted for you. Your agents still run in your cloud. Your data never
            leaves your perimeter.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <a
              href={WAITLIST_HREF}
              className="flex min-h-[44px] items-center rounded-lg px-5 text-[15px] font-bold no-underline transition-opacity hover:opacity-90"
              style={{ background: 'var(--accent)', color: '#000' }}
            >
              Join the waitlist &rarr;
            </a>
            <Link
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex min-h-[44px] items-center rounded-lg border px-5 text-[14px] no-underline transition-colors hover:text-white"
              style={{ borderColor: 'var(--border-hover)', color: 'var(--text-muted)' }}
            >
              Or self-host the OSS &rarr;
            </Link>
          </div>
        </section>

        {/* What's hosted vs what's yours */}
        <section className="mb-24">
          <p
            className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
            style={{ color: 'var(--accent)' }}
          >
            Where things live
          </p>
          <h2
            className="mb-10 text-[28px] sm:text-[36px] font-extrabold text-white"
            style={{ letterSpacing: '-1px' }}
          >
            We host the control plane. You keep the agents.
          </h2>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <div
              className="rounded-[18px] border p-6 sm:p-8"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              <h3 className="mb-4 text-[16px] font-bold text-white">
                Hosted at console.agentbreeder.io
              </h3>
              <ul className="space-y-2.5 list-none m-0 p-0">
                {HOSTED.map((line) => (
                  <li
                    key={line}
                    className="flex gap-2.5 text-[14px] leading-[1.55]"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    <span style={{ color: 'var(--accent)' }} aria-hidden>&bull;</span>
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div
              className="rounded-[18px] border p-6 sm:p-8"
              style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
            >
              <h3 className="mb-4 text-[16px] font-bold text-white">
                Stays in your cloud
              </h3>
              <ul className="space-y-2.5 list-none m-0 p-0">
                {YOURS.map((line) => (
                  <li
                    key={line}
                    className="flex gap-2.5 text-[14px] leading-[1.55]"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    <span style={{ color: 'var(--purple)' }} aria-hidden>&bull;</span>
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        {/* Comparison */}
        <section className="mb-24">
          <p
            className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
            style={{ color: 'var(--accent)' }}
          >
            OSS vs Cloud
          </p>
          <h2
            className="mb-8 text-[28px] sm:text-[36px] font-extrabold text-white"
            style={{ letterSpacing: '-1px' }}
          >
            Same engine. Different operating model.
          </h2>

          <div
            className="overflow-x-auto rounded-[14px] border"
            style={{ borderColor: 'var(--border)' }}
          >
            <table className="w-full text-left text-[14px]" style={{ borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-surface)' }}>
                  <th className="px-5 py-3.5 text-[11px] font-semibold uppercase tracking-[1.5px]" style={{ color: 'var(--text-dim)' }}>Feature</th>
                  <th className="px-5 py-3.5 text-[11px] font-semibold uppercase tracking-[1.5px]" style={{ color: 'var(--text-dim)' }}>OSS (self-host)</th>
                  <th className="px-5 py-3.5 text-[11px] font-semibold uppercase tracking-[1.5px]" style={{ color: 'var(--text-dim)' }}>Cloud</th>
                </tr>
              </thead>
              <tbody>
                {COMPARISON.map((row, i) => (
                  <tr key={row.feature} style={{ borderTop: i === 0 ? 'none' : '1px solid var(--border)' }}>
                    <td className="px-5 py-3.5 font-medium text-white">{row.feature}</td>
                    <td className="px-5 py-3.5" style={{ color: 'var(--text-muted)' }}>{row.oss}</td>
                    <td className="px-5 py-3.5" style={{ color: 'var(--text-muted)' }}>{row.cloud}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* FAQ */}
        <section className="mb-24">
          <p
            className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
            style={{ color: 'var(--accent)' }}
          >
            FAQ
          </p>
          <h2
            className="mb-8 text-[28px] sm:text-[36px] font-extrabold text-white"
            style={{ letterSpacing: '-1px' }}
          >
            The honest answers
          </h2>

          <div className="space-y-4">
            {FAQ.map(({ q, a }) => (
              <div
                key={q}
                className="rounded-[12px] border p-5 sm:p-6"
                style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
              >
                <h3 className="mb-2 text-[15px] font-bold text-white">{q}</h3>
                <p className="text-[14px] leading-[1.7]" style={{ color: 'var(--text-muted)' }}>{a}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Final CTA */}
        <section
          className="rounded-[18px] border p-8 sm:p-12 text-center"
          style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
        >
          <h2 className="mb-3 text-[24px] sm:text-[32px] font-extrabold text-white" style={{ letterSpacing: '-0.8px' }}>
            Ready when the alpha opens?
          </h2>
          <p className="mb-6 text-[15px] sm:text-[16px]" style={{ color: 'var(--text-muted)' }}>
            Join the waitlist and we&rsquo;ll reach out as soon as <Link href={CONSOLE_URL} className="underline" style={{ color: 'var(--accent)' }}>console.agentbreeder.io</Link> opens up access.
          </p>
          <a
            href={WAITLIST_HREF}
            className="inline-flex min-h-[44px] items-center rounded-lg px-6 text-[15px] font-bold no-underline transition-opacity hover:opacity-90"
            style={{ background: 'var(--accent)', color: '#000' }}
          >
            Join the waitlist &rarr;
          </a>
        </section>
      </main>
      <Footer />
    </>
  );
}
