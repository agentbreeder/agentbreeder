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
    { label: 'GitHub ↗', href: 'https://github.com/agentbreeder/agentbreeder' },
    { label: 'PyPI ↗', href: 'https://pypi.org/project/agentbreeder/' },
    { label: 'Docker Hub ↗', href: 'https://hub.docker.com/u/rajits' },
    { label: 'npm ↗', href: 'https://www.npmjs.com/package/@agentbreeder/sdk' },
    { label: 'Homebrew ↗', href: 'https://github.com/agentbreeder/homebrew-agentbreeder' },
  ],
  Blog: [
    { label: 'Why I Built AgentBreeder', href: '/blog/why-i-built-agentbreeder' },
    { label: 'All Posts', href: '/blog' },
  ],
  Community: [
    { label: 'Twitter ↗', href: 'https://twitter.com' },
    { label: 'LinkedIn ↗', href: 'https://www.linkedin.com/in/rajsaha/' },
    { label: 'Issues ↗', href: 'https://github.com/agentbreeder/agentbreeder/issues' },
  ],
};

export function Footer() {
  return (
    <footer className="border-t px-4 sm:px-8 md:px-12 lg:px-16 xl:px-24 pb-10 pt-12" style={{ borderColor: 'var(--border)' }}>
      <div className="max-w-[1400px] mx-auto">
        <div className="mb-10 flex flex-col gap-10 sm:flex-row sm:flex-wrap sm:gap-12">
          <div className="sm:flex-1 sm:min-w-[180px]">
            <Logo />
            <p
              className="mt-3 max-w-[280px] text-[13px] leading-[1.7]"
              style={{ color: 'var(--text-muted)' }}
            >
              Open-source platform for building, deploying, and governing enterprise AI agents.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-8 sm:contents">
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
                    target={href.startsWith('http') ? '_blank' : undefined}
                    rel={href.startsWith('http') ? 'noopener noreferrer' : undefined}
                  >
                    {label}
                  </Link>
                ))}
              </div>
            ))}
          </div>
        </div>
        <div
          className="flex flex-col gap-3 border-t pt-6 sm:flex-row sm:items-center sm:justify-between"
          style={{ borderColor: 'var(--border)' }}
        >
          <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
            © 2026 Rajit Saha. AgentBreeder™ is a trademark pending registration. Apache License 2.0
          </p>
          <span
            className="rounded border px-2.5 py-0.5 font-mono text-[11px] font-semibold self-start sm:self-auto"
            style={{
              background: 'var(--accent-dim)',
              borderColor: 'var(--accent-border)',
              color: 'var(--accent)',
            }}
          >
            v1.9.0
          </span>
        </div>
      </div>
    </footer>
  );
}
