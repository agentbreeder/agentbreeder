import { Nav } from '@/components/nav';
import { CloudBanner } from '@/components/cloud-banner';
import { Hero } from '@/components/hero';
import { Frameworks } from '@/components/frameworks';
import { AgentDemo } from '@/components/agent-demo';
import { AgentForAll } from '@/components/agent-for-all';
import { DeployAnywhere } from '@/components/deploy-anywhere';
import { PlatformLock } from '@/components/platform-lock';
import { FiveLayerArch } from '@/components/five-layer-arch';
import { RegistryLifecycle } from '@/components/registry-lifecycle';
import { Features } from '@/components/features';
import { HowItWorks } from '@/components/how-it-works';
import { CloudComing } from '@/components/cloud-coming';
import { BuiltBy } from '@/components/built-by';
import { Footer } from '@/components/footer';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  alternates: { canonical: '/' },
};

const SITE_URL = 'https://www.agentbreeder.io';
const GITHUB_URL = 'https://github.com/agentbreeder/agentbreeder';
const SITE_DESCRIPTION =
  "The only agent platform that doesn't pick a winner. Open-source substrate for building, deploying, and governing AI agents — any framework, any cloud, one agent.yaml. Apache 2.0.";

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
      operatingSystem: 'Linux, macOS, Windows',
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
      <CloudBanner />
      <main>
        <Hero />
        <Frameworks />
        <AgentDemo />
        <AgentForAll />
        <DeployAnywhere />
        <PlatformLock />
        <FiveLayerArch />
        <RegistryLifecycle />
        <Features />
        <HowItWorks />
        <BuiltBy />
        <div className="border-t" style={{ borderColor: 'var(--border)' }} />
        <CloudComing />
      </main>
      <Footer />
    </>
  );
}
