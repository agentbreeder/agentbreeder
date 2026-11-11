import type { Metadata } from 'next';
import Link from 'next/link';
import Image from 'next/image';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';

export const metadata: Metadata = {
  title: 'About',
  description:
    'AgentBreeder is an open-source preview that deploys existing agent containers behind a deploy-time identity gate. Built by Rajit Saha alongside a role as Director of Data Platform at Udemy + Coursera.',
  alternates: { canonical: '/about' },
  openGraph: {
    title: 'About AgentBreeder',
    description:
      'AgentBreeder is an open-source preview that deploys existing agent containers behind a deploy-time identity gate. Built by Rajit Saha alongside a role as Director of Data Platform at Udemy + Coursera.',
    url: '/about',
    type: 'website',
    images: [
      { url: '/og.png', width: 1280, height: 640, alt: 'About AgentBreeder' },
    ],
  },
};

const CONTACT_EMAIL = 'hello@agentbreeder.io';
const GITHUB_URL = 'https://github.com/agentbreeder/agentbreeder';
const LINKEDIN_URL = 'https://www.linkedin.com/in/rajsaha/';

export default function AboutPage() {
  return (
    <>
      <Nav />
      <main className="mx-auto max-w-[860px] px-4 sm:px-8 py-16 sm:py-24">
        <p
          className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
          style={{ color: 'var(--accent)' }}
        >
          About AgentBreeder
        </p>
        <h1
          className="mb-6 font-display text-[36px] sm:text-[48px] font-extrabold text-white"
          style={{ letterSpacing: '-1.5px', lineHeight: 1.1 }}
        >
          Know which agent you are running.
        </h1>
        <p
          className="mb-12 text-[18px] sm:text-[20px] leading-[1.6]"
          style={{ color: 'var(--text-muted)' }}
        >
          AgentBreeder is an open-source preview that deploys an agent container you already
          built, and only after your identity provider confirms the agent is real, sponsored and
          still within its review date. Today it deploys to local Docker; production targets are
          not shipped yet.
        </p>

        {/* Mission */}
        <section className="mb-14">
          <h2
            className="mb-4 text-[24px] sm:text-[28px] font-extrabold text-white"
            style={{ letterSpacing: '-0.5px' }}
          >
            Why this exists
          </h2>
          <p className="mb-4 text-[16px] leading-[1.75]" style={{ color: 'var(--text-muted)' }}>
            Agents are starting to act on real systems, but most teams can&apos;t answer a basic
            question before one goes live: which identity is this, who sponsors it, and is it
            still meant to be running? Identity providers already hold that answer. Deploy
            pipelines rarely ask.
          </p>
          <p className="text-[16px] leading-[1.75]" style={{ color: 'var(--text-muted)' }}>
            AgentBreeder makes the deploy ask. You keep your framework and your container; it
            checks the agent&apos;s identity, optional policy and second-person approval, records
            every attempt in a hash-chained log, and can block a principal everywhere at once.
            An earlier 2.x version tried to cover building, registries and every cloud at once,
            and much of it did not work as documented. The rebuild only claims what we have run
            end to end.
          </p>
        </section>

        {/* The inventor */}
        <section className="mb-14">
          <h2
            className="mb-6 text-[24px] sm:text-[28px] font-extrabold text-white"
            style={{ letterSpacing: '-0.5px' }}
          >
            The inventor
          </h2>
          <div
            className="rounded-[18px] border p-6 sm:p-8"
            style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
          >
            <div className="flex flex-col sm:flex-row gap-6 sm:gap-8 items-start">
              <div className="flex-shrink-0">
                <div className="relative overflow-hidden rounded-2xl" style={{ width: 96, height: 96 }}>
                  <Image
                    src="/rajit-saha.jpg"
                    alt="Rajit Saha"
                    fill
                    className="object-cover"
                    sizes="96px"
                  />
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="mb-1 text-[20px] font-bold text-white">Rajit Saha</h3>
                <p className="mb-4 text-[13px]" style={{ color: 'var(--accent)' }}>
                  Founder, AgentBreeder · Director of Data Platform, Udemy + Coursera
                </p>
                <p className="mb-3 text-[14px] leading-[1.75]" style={{ color: 'var(--text-muted)' }}>
                  Rajit has spent 23+ years building distributed systems and data
                  infrastructure across Oracle, IBM, Yahoo, Teradata, VMware, LendingClub,
                  Experian, and Udemy. At the merged Udemy + Coursera company he leads the
                  Data Platform team, where shipping AI agents into production surfaced the
                  exact gap AgentBreeder fills.
                </p>
                <p className="mb-4 text-[14px] leading-[1.75]" style={{ color: 'var(--text-muted)' }}>
                  AgentBreeder is currently built alongside that role. The open-source
                  project is released under Apache 2.0. A managed cloud offering is planned
                  but not available.
                </p>
                <Link
                  href={LINKEDIN_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] font-medium no-underline transition-all hover:border-[var(--accent)] hover:text-[var(--accent)]"
                  style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
                >
                  LinkedIn
                </Link>
              </div>
            </div>
          </div>
        </section>

        {/* Status & roadmap */}
        <section className="mb-14">
          <h2
            className="mb-4 text-[24px] sm:text-[28px] font-extrabold text-white"
            style={{ letterSpacing: '-0.5px' }}
          >
            Where we are today
          </h2>
          <ul className="space-y-3 text-[15px] leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
            <li>
              <strong className="text-white">Open-source preview</strong> — Apache 2.0, 3.0.0a1 on
              PyPI and npm. Local Docker deploys behind an identity gate; see{' '}
              <Link href="/docs" className="underline" style={{ color: 'var(--accent)' }}>what works today</Link>.
              The older 2.x on PyPI is unmaintained.
            </li>
            <li>
              <strong className="text-white">Managed cloud (planned)</strong> — a hosted
              version is planned. It is not available, and there is no date yet.
            </li>
            <li>
              <strong className="text-white">Status</strong> — bootstrapped, no external funding,
              built in public on <Link href={GITHUB_URL} className="underline" style={{ color: 'var(--accent)' }}>GitHub</Link>.
              Contributions and design feedback welcome.
            </li>
          </ul>
        </section>

        {/* Accessibility */}
        <section className="mb-14">
          <h2
            className="mb-4 text-[24px] sm:text-[28px] font-extrabold text-white"
            style={{ letterSpacing: '-0.5px' }}
          >
            Accessibility
          </h2>
          <p className="mb-3 text-[15px] leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
            The site is dark-only by design — the whole color system is tuned for dark
            surfaces and a separate light mode would mean re-tuning every component. If
            that&rsquo;s a blocker for you, let us know at{' '}
            <a href={`mailto:${CONTACT_EMAIL}`} className="underline" style={{ color: 'var(--accent)' }}>
              {CONTACT_EMAIL}
            </a>.
          </p>
          <p className="text-[15px] leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
            The site respects <code className="rounded px-1.5 py-0.5 text-[13px]" style={{ background: 'var(--bg-elevated)', color: 'var(--accent)' }}>prefers-reduced-motion</code> and
            targets WCAG 2.1 AA contrast for all body and meta text. Found an
            accessibility issue? Open one on{' '}
            <Link href={`${GITHUB_URL}/issues`} className="underline" style={{ color: 'var(--accent)' }}>
              GitHub
            </Link>.
          </p>
        </section>

        {/* Contact */}
        <section className="mb-4">
          <h2
            className="mb-4 text-[24px] sm:text-[28px] font-extrabold text-white"
            style={{ letterSpacing: '-0.5px' }}
          >
            Contact
          </h2>
          <p className="text-[15px] leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
            Email{' '}
            <a href={`mailto:${CONTACT_EMAIL}`} className="underline" style={{ color: 'var(--accent)' }}>
              {CONTACT_EMAIL}
            </a>{' '}
            for partnerships, pilots, press, or just to say hi. For bug reports and feature
            requests, please open an issue on{' '}
            <Link href={`${GITHUB_URL}/issues`} className="underline" style={{ color: 'var(--accent)' }}>
              GitHub
            </Link>.
          </p>
        </section>
      </main>
      <Footer />
    </>
  );
}
