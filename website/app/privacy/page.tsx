import type { Metadata } from 'next';
import Link from 'next/link';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';

export const metadata: Metadata = {
  title: 'Privacy Policy',
  description: 'How AgentBreeder collects, uses, and protects information.',
  alternates: { canonical: '/privacy' },
};

const CONTACT_EMAIL = 'hello@agentbreeder.io';
const LAST_UPDATED = 'May 23, 2026';

export default function PrivacyPage() {
  return (
    <>
      <Nav />
      <main className="mx-auto max-w-[820px] px-4 sm:px-8 py-16 sm:py-24">
        <p
          className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]"
          style={{ color: 'var(--accent)' }}
        >
          Legal
        </p>
        <h1
          className="mb-4 text-[36px] sm:text-[44px] font-extrabold text-white"
          style={{ letterSpacing: '-1px' }}
        >
          Privacy Policy
        </h1>
        <p className="mb-10 text-[13px]" style={{ color: 'var(--text-dim)' }}>
          Last updated: {LAST_UPDATED}
        </p>

        <Section title="Who this covers">
          <p>
            This policy describes how the AgentBreeder open-source project (&ldquo;AgentBreeder,&rdquo;
            &ldquo;we,&rdquo; or &ldquo;us&rdquo;) handles information collected through the website
            at <code>agentbreeder.io</code> and the related documentation. The project is
            currently maintained by Rajit Saha.
          </p>
          <p>
            The managed cloud offering at <code>console.agentbreeder.io</code> is not yet
            launched. A separate, more detailed privacy policy will apply when it goes live.
          </p>
        </Section>

        <Section title="What we collect">
          <p>The website collects only what is necessary to serve and operate it:</p>
          <ul>
            <li>
              <strong>Standard server logs</strong> — IP address, user agent, referrer,
              requested path, and timestamp. These are produced by our hosting provider
              (Vercel) for every request and are used for security, abuse prevention, and
              basic operational diagnostics.
            </li>
            <li>
              <strong>Emails you send us</strong> — if you email{' '}
              <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>, we retain the
              message and your email address to reply and to keep a record of the
              conversation.
            </li>
          </ul>
          <p>The marketing site does <strong>not</strong> use third-party analytics, advertising trackers, or marketing cookies.</p>
        </Section>

        <Section title="What the open-source CLI and SDK collect">
          <p>
            The <code>agentbreeder</code> CLI and{' '}
            <code>agentbreeder-sdk</code> package do not send telemetry to AgentBreeder.
            All data the CLI handles (your <code>agent.yaml</code>, your secrets, your
            deploy artifacts) stays within your own environment and the cloud accounts
            you configure.
          </p>
        </Section>

        <Section title="How we use information">
          <ul>
            <li>To operate and secure the website.</li>
            <li>To respond to email you send us.</li>
            <li>To investigate abuse, fraud, or violations of our terms.</li>
          </ul>
          <p>We do not sell or rent personal information to anyone.</p>
        </Section>

        <Section title="Third parties">
          <p>The website relies on the following service providers:</p>
          <ul>
            <li>
              <strong>Vercel</strong> — hosting and CDN. See{' '}
              <Link href="https://vercel.com/legal/privacy-policy" target="_blank" rel="noopener noreferrer">
                Vercel&rsquo;s privacy policy
              </Link>.
            </li>
          </ul>
        </Section>

        <Section title="Your choices">
          <ul>
            <li>
              You can browse the site without providing any personal information beyond
              what is in standard server logs.
            </li>
            <li>
              You can email us at <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a> to
              ask what information we hold about you, to request deletion, or to raise
              any other privacy concern. We will respond in a reasonable timeframe.
            </li>
          </ul>
        </Section>

        <Section title="Children">
          <p>
            AgentBreeder is a developer tool and is not directed to children under 13.
            We do not knowingly collect personal information from children.
          </p>
        </Section>

        <Section title="Governing law">
          <p>
            This policy is governed by the laws of the United States. Any disputes will
            be handled under U.S. law.
          </p>
        </Section>

        <Section title="Changes to this policy">
          <p>
            We may update this policy as the project evolves — most notably when the
            managed cloud offering launches. The &ldquo;Last updated&rdquo; date at the
            top reflects the most recent change.
          </p>
        </Section>

        <Section title="Contact">
          <p>
            Questions about privacy? Email{' '}
            <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
          </p>
        </Section>
      </main>
      <Footer />
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-10 [&_p]:mb-3 [&_p]:text-[15px] [&_p]:leading-[1.75] [&_ul]:my-3 [&_ul]:ml-5 [&_ul]:list-disc [&_ul]:space-y-2 [&_li]:text-[15px] [&_li]:leading-[1.75] [&_a]:underline [&_code]:rounded [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[13px]"
      style={{ color: 'var(--text-muted)' }}
    >
      <h2 className="mb-3 text-[20px] sm:text-[22px] font-bold text-white" style={{ letterSpacing: '-0.3px' }}>
        {title}
      </h2>
      <div className="[&_strong]:text-white [&_a]:text-[color:var(--accent)] [&_code]:bg-[color:var(--bg-elevated)] [&_code]:text-[color:var(--accent)]">
        {children}
      </div>
    </section>
  );
}
