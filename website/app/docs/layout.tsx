import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { Banner } from 'fumadocs-ui/components/banner';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { source } from '@/lib/source';
import { Logo } from '@/components/logo';

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <>
      <Banner id="rebuild-preview-2026-11">
        <span>
          These docs cover AgentBreeder 2.x, the version on PyPI. A focused rebuild is in preview:{' '}
          <Link href="/docs/preview" className="underline underline-offset-4">
            read about it
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
