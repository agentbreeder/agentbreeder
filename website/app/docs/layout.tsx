import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { Banner } from 'fumadocs-ui/components/banner';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { source } from '@/lib/source';
import { Logo } from '@/components/logo';
import { DocsThemeSwitch } from '@/components/theme-toggle';

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <>
      <aside aria-label="Preview notice">
        <Banner id="preview-3-0-0a3">
          <span>
            Alpha preview: pin <code>3.0.0a3</code> when you{' '}
            <Link href="/docs/install" className="underline underline-offset-4">
              install
            </Link>
            . A bare <code>pip install agentbreeder</code> still installs the unmaintained 2.x:{' '}
            <Link href="/docs/legacy-2x" className="underline underline-offset-4">
              known issues
            </Link>
            .
          </span>
        </Banner>
      </aside>
      <DocsLayout
        tree={source.pageTree}
        nav={{
          title: <Logo size={22} />,
          transparentMode: 'none',
        }}
        sidebar={{
          collapsible: false,
        }}
        // fumadocs' own switch exposes no checked state to assistive tech, so the docs mount the
        // site's System/Light/Dark radiogroup in its slot (design v2 §10.1).
        slots={{ themeSwitch: DocsThemeSwitch }}
      >
        {children}
      </DocsLayout>
    </>
  );
}
