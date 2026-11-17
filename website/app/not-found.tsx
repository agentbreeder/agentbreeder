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
  { href: '/docs/local-deployment', label: 'Local deployment', description: 'Deploy your own image through the gate' },
  { href: '/blog', label: 'Blog', description: 'Posts on agents, deploy, and AgentBreeder' },
  { href: '/about', label: 'About', description: 'About AgentBreeder and the project' },
];

export default function NotFound() {
  return (
    <>
      <Nav />
      <main className="ab-container max-w-[820px] py-20 sm:py-28">
        <p className="ab-label mb-3">404</p>
        <h1 className="mb-6 font-display text-display font-semibold text-foreground">
          This page didn&rsquo;t deploy.
        </h1>
        <p className="ab-lede mb-12 text-[17px] sm:text-lg">
          The URL you followed isn&rsquo;t in our registry. It may have moved, been renamed,
          or never existed. Here&rsquo;s where to go next:
        </p>

        <nav aria-label="Suggested pages">
          <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2">
            {RECOVERY_LINKS.map(({ href, label, description }) => (
              <li key={href}>
                <Link
                  href={href}
                  className="ab-card ab-card-link flex min-h-[44px] flex-col justify-center px-5 py-3 no-underline"
                >
                  <span className="text-[15px] font-semibold text-foreground">{label}</span>
                  <span className="text-[13px] text-muted-foreground">{description}</span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <p className="mt-10 text-[13px] text-muted-foreground">
          Still stuck? Email{' '}
          <a href="mailto:hello@agentbreeder.io" className="ab-link">
            hello@agentbreeder.io
          </a>{' '}
          or open an issue on{' '}
          <Link href="https://github.com/agentbreeder/agentbreeder/issues" className="ab-link">
            GitHub
          </Link>
          .
        </p>
      </main>
      <Footer />
    </>
  );
}
