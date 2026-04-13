import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import type { ReactNode } from 'react';
import { source } from '@/lib/source';
import { Logo } from '@/components/logo';

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <DocsLayout
      tree={source.pageTree}
      nav={{
        title: <Logo size={22} />,
        transparentMode: 'none',
      }}
      sidebar={{
        collapsible: false,
      }}
    >
      {children}
    </DocsLayout>
  );
}
