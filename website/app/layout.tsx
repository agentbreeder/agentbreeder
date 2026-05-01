import type { Metadata } from 'next';
import { GeistSans } from 'geist/font/sans';
import { GeistMono } from 'geist/font/mono';
import { RootProvider } from 'fumadocs-ui/provider/next';
import './globals.css';

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
      className={`${GeistSans.variable} ${GeistMono.variable} dark`}
      suppressHydrationWarning
    >
      <body>
        <RootProvider theme={{ forcedTheme: 'dark', disableTransitionOnChange: true }}>{children}</RootProvider>
      </body>
    </html>
  );
}
