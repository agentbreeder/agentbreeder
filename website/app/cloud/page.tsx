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
      <main className="ab-container max-w-[820px] py-16 sm:py-24">
        <p className="ab-label mb-3">planned · not available</p>
        <h1 className="mb-6 font-display text-display font-semibold text-foreground">AgentBreeder Cloud</h1>
        <p className="ab-lede mb-6 text-[17px]">
          A managed version of AgentBreeder is planned. It does not exist yet: there is no alpha, no
          pricing and no launch date. We will only describe its features once they work.
        </p>
        <p className="ab-lede mb-10 text-[17px]">
          What you can use today is the open-source preview, which deploys existing agent containers
          to local Docker behind a deploy-time identity gate.{' '}
          <Link href="/docs" className="ab-link">
            See what works today
          </Link>
          .
        </p>

        <section id="waitlist" aria-labelledby="waitlist-title" className="ab-card p-6 sm:p-10">
          <h2 id="waitlist-title" className="ab-h2 mb-3">
            Hear when it exists
          </h2>
          <p className="mb-6 text-[15px] leading-[1.7] text-muted-foreground">
            Leave your email and we will write when there is something real to try.
          </p>
          <WaitlistForm />
        </section>
      </main>
      <Footer />
    </>
  );
}
