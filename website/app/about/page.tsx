import type { Metadata } from 'next';
import Link from 'next/link';
import Image from 'next/image';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';

export const metadata: Metadata = {
  title: 'About',
  description:
    'AgentBreeder is an open-source platform for building, deploying, and governing enterprise AI agents. Built by Rajit Saha alongside his role as Director of Data Platform at Udemy + Coursera.',
  alternates: { canonical: '/about' },
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
          className="mb-6 text-[36px] sm:text-[48px] font-extrabold text-white"
          style={{ letterSpacing: '-1.5px', lineHeight: 1.1 }}
        >
          One agent.yaml. Any framework. Any cloud.
        </h1>
        <p
          className="mb-12 text-[18px] sm:text-[20px] leading-[1.6]"
          style={{ color: 'var(--text-muted)' }}
        >
          AgentBreeder is an open-source platform for building, deploying, and governing
          enterprise AI agents. It exists so a developer can write one config file, run one
          command, and ship an agent to AWS, GCP, Azure, or Kubernetes — with RBAC, cost
          tracking, audit trail, and observability automatic.
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
            Every team building production AI agents reinvents the same plumbing: a deploy
            pipeline, a registry, RBAC, cost attribution, an audit log, observability.
            And every framework (LangGraph, CrewAI, OpenAI Agents, Claude SDK, Google ADK)
            asks you to learn a new container shape, a new entrypoint, a new way to wire
            secrets.
          </p>
          <p className="text-[16px] leading-[1.75]" style={{ color: 'var(--text-muted)' }}>
            AgentBreeder collapses that down. Pick your framework. Pick your cloud. Write
            <code className="mx-1 rounded px-1.5 py-0.5 text-[14px]" style={{ background: 'var(--bg-elevated)', color: 'var(--accent)' }}>agent.yaml</code>.
            Run <code className="rounded px-1.5 py-0.5 text-[14px]" style={{ background: 'var(--bg-elevated)', color: 'var(--accent)' }}>agentbreeder deploy</code>.
            Governance happens as a side effect, not as extra configuration.
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
                  project is released under Apache 2.0, with a managed cloud offering
                  (<Link href="https://console.agentbreeder.io" className="underline" style={{ color: 'var(--accent)' }}>console.agentbreeder.io</Link>) coming next.
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
              <strong className="text-white">Open-source CLI + engine</strong> — Apache 2.0,
              installable via pip, Homebrew, or Docker. Multi-cloud, multi-framework, governance built in.
            </li>
            <li>
              <strong className="text-white">Managed cloud (coming soon)</strong> — a hosted
              control plane at <Link href="https://console.agentbreeder.io" className="underline" style={{ color: 'var(--accent)' }}>console.agentbreeder.io</Link> for
              teams that want the registry, RBAC, and observability without running the API server themselves.
            </li>
            <li>
              <strong className="text-white">Status</strong> — bootstrapped, no external funding,
              built in public on <Link href={GITHUB_URL} className="underline" style={{ color: 'var(--accent)' }}>GitHub</Link>.
              Contributions and design feedback welcome.
            </li>
          </ul>
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
