import type { Metadata, Viewport } from 'next';
import { GeistSans } from 'geist/font/sans';
import { GeistMono } from 'geist/font/mono';
import { RootProvider } from 'fumadocs-ui/provider/next';
import { ThemeColorSync } from '@/components/theme-color-sync';
import { THEME_COLOR, THEME_GUARD_SCRIPT } from '@/lib/theme';
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
          Must stay before RootProvider, whose next-themes script reads the stored theme next:
          this drops a stored value outside system/light/dark and stops one arriving from another
          tab (lib/theme.ts, design v2 §10.2).
        */}
        <script dangerouslySetInnerHTML={{ __html: THEME_GUARD_SCRIPT }} />
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
            // With a value map next-themes' React effect adds only mapped classes; an unknown
            // theme adds none instead of throwing in classList.add (design v2 §10.2).
            value: { light: 'light', dark: 'dark' },
            disableTransitionOnChange: true,
          }}
          // fumadocs mounts its closed search dialog on load by default, which downloads and runs
          // the search, HTML and Markdown parsers on every page before anyone searches. Load them
          // on the first open (the search button or ⌘K) instead (#1130).
          search={{ preload: false }}
        >
          <ThemeColorSync />
          {children}
        </RootProvider>
      </body>
    </html>
  );
}
