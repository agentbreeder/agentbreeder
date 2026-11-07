import type { Metadata } from 'next';
import Link from 'next/link';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';

export const metadata: Metadata = {
  title: 'Terms of Use',
  description: 'Terms governing your use of the AgentBreeder website and documentation.',
  alternates: { canonical: '/terms' },
};

const CONTACT_EMAIL = 'hello@agentbreeder.io';
const LAST_UPDATED = 'May 23, 2026';
const LICENSE_URL = 'https://github.com/agentbreeder/agentbreeder/blob/main/LICENSE';

export default function TermsPage() {
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
          Terms of Use
        </h1>
        <p className="mb-10 text-[13px]" style={{ color: 'var(--text-dim)' }}>
          Last updated: {LAST_UPDATED}
        </p>

        <Section title="Scope">
          <p>
            These terms govern your use of the AgentBreeder website at{' '}
            <code>agentbreeder.io</code> and the documentation hosted there. They do
            <strong> not</strong> govern your use of the AgentBreeder software itself —
            see the <strong>Software license</strong> section below.
          </p>
          <p>
            The managed cloud offering at <code>console.agentbreeder.io</code> is not yet
            launched. Separate terms will apply when it goes live.
          </p>
        </Section>

        <Section title="Software license">
          <p>
            The <code>agentbreeder</code> CLI, <code>agentbreeder-sdk</code>, and all
            related open-source components are licensed under the{' '}
            <Link href={LICENSE_URL} target="_blank" rel="noopener noreferrer">
              Apache License 2.0
            </Link>. Your use, modification, and distribution of the software is governed
            by that license — not by these terms.
          </p>
          <p>
            Under Apache 2.0, the software is provided <strong>&ldquo;as is&rdquo;,
            without warranties of any kind</strong>.
          </p>
        </Section>

        <Section title="Acceptable use of the website">
          <p>You agree not to:</p>
          <ul>
            <li>Use the website to violate any applicable law or regulation.</li>
            <li>Attempt to interfere with, disrupt, or compromise the website&rsquo;s integrity or performance.</li>
            <li>Scrape or copy the site at a volume that imposes an unreasonable load on our infrastructure.</li>
            <li>Use AgentBreeder branding, logos, or trademarks in a way that suggests endorsement, partnership, or affiliation without our written permission.</li>
          </ul>
          <p>
            Normal crawling by search engines, normal documentation reading, code copying
            from documentation, and forking the open-source repository are all fine.
          </p>
        </Section>

        <Section title="Intellectual property">
          <p>
            The AgentBreeder name and logo are trademarks of Rajit Saha (registration
            pending). The source code is licensed under Apache 2.0 as described above.
            Website copy, design, and documentation are © Rajit Saha and may be quoted
            with attribution.
          </p>
        </Section>

        <Section title="Third-party links">
          <p>
            The website links to third-party services (GitHub, PyPI, Docker Hub,
            Homebrew, LinkedIn, and others). We do not control those services and are
            not responsible for their content or practices.
          </p>
        </Section>

        <Section title="Disclaimer">
          <p>
            The website and documentation are provided <strong>&ldquo;as is&rdquo;</strong>{' '}
            without warranties of any kind, express or implied, including but not limited
            to warranties of merchantability, fitness for a particular purpose, and
            non-infringement. We do not guarantee that the information on the site is
            accurate, complete, or current.
          </p>
        </Section>

        <Section title="Limitation of liability">
          <p>
            To the maximum extent permitted by law, AgentBreeder and Rajit Saha will not
            be liable for any indirect, incidental, special, consequential, or punitive
            damages arising out of or related to your use of the website, the
            documentation, or the open-source software.
          </p>
        </Section>

        <Section title="Governing law">
          <p>
            These terms are governed by the laws of the United States. Any disputes
            arising out of or related to these terms will be handled under U.S. law.
          </p>
        </Section>

        <Section title="Changes">
          <p>
            We may revise these terms from time to time as the project evolves. The
            &ldquo;Last updated&rdquo; date at the top reflects the most recent change.
            Continued use of the site after a change constitutes acceptance of the
            revised terms.
          </p>
        </Section>

        <Section title="Contact">
          <p>
            Questions about these terms? Email{' '}
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
