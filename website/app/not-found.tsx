import type { Metadata } from 'next';
import Link from 'next/link';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';

export const metadata: Metadata = {
  title: 'Page not found',
  description: "The page you're looking for doesn't exist.",
  robots: { index: false, follow: false },
};

const RECOVERY_LINKS = [
  { href: '/', label: 'Home', description: 'Back to the front page' },
  { href: '/docs', label: 'Docs', description: 'Read the documentation' },
  { href: '/docs/quickstart', label: 'Quickstart', description: 'Deploy your first agent in 5 minutes' },
  { href: '/blog', label: 'Blog', description: 'Posts on agents, deploy, and AgentBreeder' },
  { href: '/about', label: 'About', description: 'About AgentBreeder and the project' },
];

export default function NotFound() {
  return (
    <>
      <Nav />
      <main className="mx-auto max-w-[820px] px-4 sm:px-8 py-20 sm:py-28">
        <p
          className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
          style={{ color: 'var(--accent)' }}
        >
          404
        </p>
        <h1
          className="mb-6 text-[40px] sm:text-[56px] font-extrabold text-white"
          style={{ letterSpacing: '-1.5px', lineHeight: 1.05 }}
        >
          This page didn&rsquo;t deploy.
        </h1>
        <p
          className="mb-12 max-w-[560px] text-[17px] sm:text-[18px] leading-[1.6]"
          style={{ color: 'var(--text-muted)' }}
        >
          The URL you followed isn&rsquo;t in our registry. It may have moved, been renamed,
          or never existed. Here&rsquo;s where to go next:
        </p>

        <nav aria-label="Suggested pages">
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-3 list-none p-0 m-0">
            {RECOVERY_LINKS.map(({ href, label, description }) => (
              <li key={href}>
                <Link
                  href={href}
                  className="flex min-h-[44px] flex-col justify-center rounded-[12px] border px-5 py-3 no-underline transition-colors hover:border-[var(--accent)]"
                  style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                >
                  <span className="text-[15px] font-semibold text-white">{label} &rarr;</span>
                  <span className="text-[13px]" style={{ color: 'var(--text-muted)' }}>
                    {description}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <p className="mt-10 text-[13px]" style={{ color: 'var(--text-dim)' }}>
          Still stuck? Email{' '}
          <a href="mailto:hello@agentbreeder.io" className="underline" style={{ color: 'var(--accent)' }}>
            hello@agentbreeder.io
          </a>{' '}
          or open an issue on{' '}
          <Link
            href="https://github.com/agentbreeder/agentbreeder/issues"
            className="underline"
            style={{ color: 'var(--accent)' }}
          >
            GitHub
          </Link>
          .
        </p>
      </main>
      <Footer />
    </>
  );
}
