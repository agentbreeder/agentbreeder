import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { Banner } from 'fumadocs-ui/components/banner';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { source } from '@/lib/source';
import { Logo } from '@/components/logo';

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <>
      <Banner id="preview-3-0-0a2">
        <span>
          Alpha preview: pin <code>3.0.0a2</code> when you{' '}
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
      <DocsLayout
        tree={source.pageTree}
        nav={{
          title: <Logo size={22} />,
          transparentMode: 'none',
        }}
        sidebar={{
          collapsible: false,
        }}
        themeSwitch={{ enabled: false }}
      >
        {children}
      </DocsLayout>
    </>
  );
}
