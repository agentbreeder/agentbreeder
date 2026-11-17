import Link from 'next/link';
import { Logo } from './logo';

const LINKS = {
  Docs: [
    { label: 'Overview', href: '/docs' },
    { label: 'Install', href: '/docs/install' },
    { label: 'Local deployment', href: '/docs/local-deployment' },
    { label: 'Manifest', href: '/docs/manifest' },
    { label: 'Identity gate', href: '/docs/identity-gate' },
    { label: 'Legacy 2.x', href: '/docs/legacy-2x' },
  ],
  'Open Source': [
    { label: 'GitHub ↗', href: 'https://github.com/agentbreeder/agentbreeder' },
    { label: 'Security ↗', href: 'https://github.com/agentbreeder/agentbreeder/security' },
  ],
  Blog: [
    { label: 'Why I Built AgentBreeder', href: '/blog/why-i-built-agentbreeder' },
    { label: 'All Posts', href: '/blog' },
  ],
  Community: [
    { label: 'LinkedIn ↗', href: 'https://www.linkedin.com/in/rajsaha/' },
    { label: 'Issues ↗', href: 'https://github.com/agentbreeder/agentbreeder/issues' },
    { label: 'Discussions ↗', href: 'https://github.com/agentbreeder/agentbreeder/discussions' },
  ],
  Company: [
    { label: 'About', href: '/about' },
    { label: 'Contact', href: 'mailto:hello@agentbreeder.io' },
    { label: 'Privacy', href: '/privacy' },
    { label: 'Terms', href: '/terms' },
  ],
};

export function Footer() {
  return (
    <footer className="border-t border-border pb-[calc(40px+env(safe-area-inset-bottom))] pt-12">
      <div className="ab-container">
        <div className="mb-10 flex flex-col gap-10 sm:flex-row sm:flex-wrap sm:gap-12">
          <div className="sm:min-w-[180px] sm:flex-1">
            <Logo />
            <p className="mt-3 max-w-[280px] text-[13px] leading-[1.7] text-muted-foreground">
              Open-source preview: deploy existing agent containers behind a deploy-time identity gate.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-8 sm:contents">
            {Object.entries(LINKS).map(([group, items]) => (
              <div key={group}>
                <h2 className="ab-label mb-2">{group.toLowerCase()}</h2>
                <ul className="m-0 list-none p-0">
                  {items.map(({ label, href }) => (
                    <li key={label}>
                      <Link
                        href={href}
                        className="-mx-2 flex min-h-[44px] items-center rounded-md px-2 text-[13px] text-muted-foreground no-underline transition-colors hover:text-foreground"
                        target={href.startsWith('http') ? '_blank' : undefined}
                        rel={href.startsWith('http') ? 'noopener noreferrer' : undefined}
                      >
                        {label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-3 border-t border-border pt-6 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-muted-foreground">
            © 2026 Rajit Saha. AgentBreeder™ is a trademark pending registration. Apache License 2.0
          </p>
          <span className="ab-badge self-start sm:self-auto">preview · 3.0.0a3</span>
        </div>
      </div>
    </footer>
  );
}
