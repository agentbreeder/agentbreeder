import type { Metadata } from 'next';
import { GeistSans } from 'geist/font/sans';
import { GeistMono } from 'geist/font/mono';
import { Bricolage_Grotesque } from 'next/font/google';
import { RootProvider } from 'fumadocs-ui/provider/next';
import './globals.css';

// Distinctive display font for marketing headlines — #493.
// Applied selectively via the `font-display` Tailwind utility (see
// tailwind.config.ts), only on hero h1 and a few high-impact h2s on
// the marketing pages. Docs and blog continue using Geist Sans for
// readability and consistency with the technical content.
const bricolage = Bricolage_Grotesque({
  subsets: ['latin'],
  variable: '--font-display',
  weight: ['600', '700', '800'],
  display: 'swap',
});

export const metadata: Metadata = {
  title: {
    default: 'AgentBreeder — Define Once. Deploy Anywhere. Govern Automatically.',
    template: '%s | AgentBreeder',
  },
  description:
    'The only agent platform that doesn\'t pick a winner. Open-source substrate for building, deploying, and governing AI agents — any framework, any cloud, one agent.yaml. Apache 2.0.',
  metadataBase: new URL('https://www.agentbreeder.io'),
  openGraph: {
    siteName: 'AgentBreeder',
    type: 'website',
    url: 'https://www.agentbreeder.io',
    images: [{ url: '/og.png', width: 1280, height: 640, alt: 'AgentBreeder — the only agent platform that doesn\'t pick a winner' }],
  },
  twitter: {
    card: 'summary_large_image',
    images: [{ url: '/og.png', alt: 'AgentBreeder — the only agent platform that doesn\'t pick a winner' }],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable} ${bricolage.variable} dark`}
      suppressHydrationWarning
    >
      <body>
        {/*
          Dark mode is intentional, not an oversight. The whole design system
          (background, glows, syntax-highlighted YAML, accent palette) is built
          around dark surfaces. Switching to light would require re-tuning every
          token and component. Tracked in #489 if we revisit.

          The site respects `prefers-reduced-motion` (see app/globals.css)
          and uses WCAG-AA contrast for all body and meta text.
        */}
        <RootProvider theme={{ forcedTheme: 'dark', disableTransitionOnChange: true }}>{children}</RootProvider>
      </body>
    </html>
  );
}
