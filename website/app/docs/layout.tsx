import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { Banner } from 'fumadocs-ui/components/banner';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { source } from '@/lib/source';
import { Logo } from '@/components/logo';

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <>
      <Banner id="rebuild-docs-2026-11">
        <span>
          Preview, install from source. <code>pip install agentbreeder</code> still installs the
          unmaintained 2.x:{' '}
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
