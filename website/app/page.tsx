import { Nav } from '@/components/nav';
import { Hero } from '@/components/hero';
import { HowItWorks } from '@/components/how-it-works';
import { WhatShips } from '@/components/what-ships';
import { BuiltBy } from '@/components/built-by';
import { Footer } from '@/components/footer';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  alternates: { canonical: '/' },
};

const SITE_URL = 'https://www.agentbreeder.io';
const GITHUB_URL = 'https://github.com/agentbreeder/agentbreeder';
const SITE_DESCRIPTION =
  'Open-source preview: deploy an agent container to local Docker only after its identity checks out in your identity provider, with policy, approval, a kill switch and a hash-chained audit log. Apache 2.0.';

const homeJsonLd = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'Organization',
      name: 'AgentBreeder',
      url: SITE_URL,
      logo: `${SITE_URL}/og.png`,
      sameAs: [GITHUB_URL],
    },
    {
      '@type': 'SoftwareApplication',
      name: 'AgentBreeder',
      applicationCategory: 'DeveloperApplication',
      operatingSystem: 'macOS, Linux',
      url: SITE_URL,
      description: SITE_DESCRIPTION,
      offers: {
        '@type': 'Offer',
        price: '0',
        priceCurrency: 'USD',
      },
    },
  ],
};

export default function HomePage() {
  return (
    <>
      {/*
        JSON-LD structured data. Rendered as a script text child (React 19 /
        Next 15) rather than dangerouslySetInnerHTML. The payload is static,
        server-controlled data with no user input and no `<`/`>`/`&`
        characters, so it serializes safely and validates as schema.org.
      */}
      <script type="application/ld+json">{JSON.stringify(homeJsonLd)}</script>
      <Nav />
      <main>
        <Hero />
        <HowItWorks />
        <WhatShips />
        <BuiltBy />
      </main>
      <Footer />
    </>
  );
}
