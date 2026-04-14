import { Nav } from '@/components/nav';
import { Hero } from '@/components/hero';
import { Frameworks } from '@/components/frameworks';
import { Features } from '@/components/features';
import { AgentDemo } from '@/components/agent-demo';
import { HowItWorks } from '@/components/how-it-works';
import { Footer } from '@/components/footer';

export default function HomePage() {
  return (
    <>
      <Nav />
      <main>
        <Hero />
        <Frameworks />
        <Features />
        <AgentDemo />
        <HowItWorks />
      </main>
      <Footer />
    </>
  );
}
