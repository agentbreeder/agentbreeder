import { Nav } from '@/components/nav';
import { Hero } from '@/components/hero';
import { Frameworks } from '@/components/frameworks';
import { AgentDemo } from '@/components/agent-demo';
import { AgentForAll } from '@/components/agent-for-all';
import { DeployAnywhere } from '@/components/deploy-anywhere';
import { Features } from '@/components/features';
import { HowItWorks } from '@/components/how-it-works';
import { Footer } from '@/components/footer';

export default function HomePage() {
  return (
    <>
      <Nav />
      <main>
        <Hero />
        <Frameworks />
        <AgentDemo />
        <AgentForAll />
        <DeployAnywhere />
        <Features />
        <HowItWorks />
      </main>
      <Footer />
    </>
  );
}
