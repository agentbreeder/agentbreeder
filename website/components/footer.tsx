import Link from 'next/link';
import { Logo } from './logo';

const LINKS = {
  Docs: [
    { label: 'Getting Started', href: '/docs' },
    { label: 'agent.yaml', href: '/docs/agent-yaml' },
    { label: 'CLI Reference', href: '/docs/cli-reference' },
    { label: 'SDK', href: '/docs/orchestration-sdk' },
    { label: 'Migrations', href: '/docs/migrations/overview' },
  ],
  'Open Source': [
    { label: 'GitHub ↗', href: 'https://github.com/rajitsaha/agentbreeder' },
    { label: 'PyPI ↗', href: 'https://pypi.org/project/agentbreeder/' },
    { label: 'Docker Hub ↗', href: 'https://hub.docker.com/u/rajits' },
    { label: 'npm ↗', href: 'https://www.npmjs.com/package/@agentbreeder/sdk' },
    { label: 'Homebrew ↗', href: 'https://github.com/rajitsaha/homebrew-agentbreeder' },
  ],
  Community: [
    { label: 'Twitter ↗', href: 'https://twitter.com' },
    { label: 'LinkedIn ↗', href: 'https://www.linkedin.com/in/rajsaha/' },
    { label: 'Issues ↗', href: 'https://github.com/rajitsaha/agentbreeder/issues' },
  ],
};

export function Footer() {
  return (
    <footer className="border-t px-20 pb-10 pt-12" style={{ borderColor: 'var(--border)' }}>
      <div className="mx-auto max-w-[1200px]">
        <div className="mb-10 flex gap-16">
          <div className="flex-1">
            <Logo />
            <p
              className="mt-3 max-w-[280px] text-[13px] leading-[1.7]"
              style={{ color: 'var(--text-muted)' }}
            >
              Open-source platform for building, deploying, and governing enterprise AI agents.
            </p>
          </div>
          {Object.entries(LINKS).map(([group, items]) => (
            <div key={group}>
              <h4
                className="mb-3.5 text-[12px] font-semibold uppercase tracking-[0.8px] text-white"
              >
                {group}
              </h4>
              {items.map(({ label, href }) => (
                <Link
                  key={label}
                  href={href}
                  className="mb-2 block text-[13px] no-underline transition-colors"
                  style={{ color: 'var(--text-muted)' }}
                  {...(href.startsWith('http') ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
                >
                  {label}
                </Link>
              ))}
            </div>
          ))}
        </div>
        <div
          className="flex items-center justify-between border-t pt-6"
          style={{ borderColor: 'var(--border)' }}
        >
          <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
            © 2026 Rajit Saha & AgentBreeder Contributors — Apache License 2.0
          </p>
          <span
            className="rounded border px-2.5 py-0.5 font-mono text-[11px] font-semibold"
            style={{
              background: 'var(--accent-dim)',
              borderColor: 'var(--accent-border)',
              color: 'var(--accent)',
            }}
          >
            v1.4.0
          </span>
        </div>
      </div>
    </footer>
  );
}
