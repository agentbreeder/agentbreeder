import { Nav } from '@/components/nav';
import { CloudBanner } from '@/components/cloud-banner';
import { Hero } from '@/components/hero';
import { Frameworks } from '@/components/frameworks';
import { AgentDemo } from '@/components/agent-demo';
import { AgentForAll } from '@/components/agent-for-all';
import { DeployAnywhere } from '@/components/deploy-anywhere';
import { RegistryLifecycle } from '@/components/registry-lifecycle';
import { Features } from '@/components/features';
import { HowItWorks } from '@/components/how-it-works';
import { CloudComing } from '@/components/cloud-coming';
import { Footer } from '@/components/footer';

export default function HomePage() {
  return (
    <>
      <Nav />
      <CloudBanner />
      <main>
        <Hero />
        <Frameworks />
        <AgentDemo />
        <AgentForAll />
        <DeployAnywhere />
        <RegistryLifecycle />
        <Features />
        <HowItWorks />
        <div className="border-t" style={{ borderColor: 'var(--border)' }} />
        <CloudComing />
      </main>
      <Footer />
    </>
  );
}
