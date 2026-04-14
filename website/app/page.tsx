import { Nav } from '@/components/nav';
import { Hero } from '@/components/hero';
import { Frameworks } from '@/components/frameworks';
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
        <Features />
        <HowItWorks />
      </main>
      <Footer />
    </>
  );
}
