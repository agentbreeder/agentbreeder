import type { Metadata, Viewport } from 'next';
import { GeistSans } from 'geist/font/sans';
import { GeistMono } from 'geist/font/mono';
import { RootProvider } from 'fumadocs-ui/provider/next';
import { ThemeColorSync } from '@/components/theme-color-sync';
import { THEME_COLOR } from '@/lib/theme';
import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'AgentBreeder — Know the agent. Gate the deploy. Audit every attempt.',
    template: '%s | AgentBreeder',
  },
  description:
    'Open-source preview: deploy an agent container to local Docker only after its identity checks out in your identity provider, with policy, approval, a kill switch and a hash-chained audit log. Apache 2.0.',
  metadataBase: new URL('https://www.agentbreeder.io'),
  openGraph: {
    siteName: 'AgentBreeder',
    type: 'website',
    url: 'https://www.agentbreeder.io',
    images: [{ url: '/og.png', width: 1280, height: 640, alt: 'AgentBreeder: deploy an agent only after its identity checks out' }],
  },
  twitter: {
    card: 'summary_large_image',
    images: [{ url: '/og.png', alt: 'AgentBreeder: deploy an agent only after its identity checks out' }],
  },
};

// theme-color is the no-JS fallback and follows the OS; ThemeColorSync makes it follow the toggle.
export const viewport: Viewport = {
  viewportFit: 'cover',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: THEME_COLOR.light },
    { media: '(prefers-color-scheme: dark)', color: THEME_COLOR.dark },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`} suppressHydrationWarning>
      <body>
        {/*
          Light by default, dark by system preference or the toggle (design v2 §10). next-themes
          emits a small blocking script at the top of <body> that sets class="dark" on <html>
          before first paint, so there is no flash. With JavaScript off the page renders light.
          Motion respects prefers-reduced-motion (website/brand.css, app/globals.css).
        */}
        <RootProvider
          theme={{
            defaultTheme: 'system',
            enableSystem: true,
            themes: ['light', 'dark'],
            disableTransitionOnChange: true,
          }}
        >
          <ThemeColorSync />
          {children}
        </RootProvider>
      </body>
    </html>
  );
}
