import type { Metadata } from 'next';
import Link from 'next/link';
import { Nav } from '@/components/nav';
import { Footer } from '@/components/footer';
import { WaitlistForm } from '@/components/waitlist-form';

const DESCRIPTION =
  'A managed AgentBreeder is planned. Nothing is available yet; join the waitlist to hear when it is.';

export const metadata: Metadata = {
  title: 'Cloud (planned)',
  description: DESCRIPTION,
  alternates: { canonical: '/cloud' },
  openGraph: {
    title: 'AgentBreeder Cloud (planned)',
    description: DESCRIPTION,
    url: '/cloud',
    type: 'website',
    images: [{ url: '/og.png', width: 1280, height: 640, alt: 'AgentBreeder Cloud (planned)' }],
  },
};

export default function CloudPage() {
  return (
    <>
      <Nav />
      <main className="mx-auto max-w-[820px] px-4 sm:px-8 py-16 sm:py-24">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-[2px]" style={{ color: 'var(--accent)' }}>
          Planned · not available
        </p>
        <h1
          className="mb-6 font-display text-[40px] sm:text-[56px] font-extrabold text-white"
          style={{ letterSpacing: '-1.5px', lineHeight: 1.05 }}
        >
          AgentBreeder Cloud
        </h1>
        <p className="mb-6 text-[17px] leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
          A managed version of AgentBreeder is planned. It does not exist yet: there is no alpha, no
          pricing and no launch date. We will only describe its features once they work.
        </p>
        <p className="mb-10 text-[17px] leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
          What you can use today is the open-source preview, which deploys existing agent containers
          to local Docker behind a deploy-time identity gate.{' '}
          <Link href="/docs" className="underline" style={{ color: 'var(--accent)' }}>
            See what works today
          </Link>
          .
        </p>

        <section
          id="waitlist"
          className="rounded-[18px] border p-8 sm:p-10"
          style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
        >
          <h2 className="mb-3 text-[22px] sm:text-[28px] font-extrabold text-white" style={{ letterSpacing: '-0.6px' }}>
            Hear when it exists
          </h2>
          <p className="mb-6 text-[15px] leading-[1.7]" style={{ color: 'var(--text-muted)' }}>
            Leave your email and we will write when there is something real to try.
          </p>
          <WaitlistForm />
        </section>
      </main>
      <Footer />
    </>
  );
}
