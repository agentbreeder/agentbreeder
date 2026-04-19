'use client';

import Link from 'next/link';
import { useState } from 'react';
import { Logo } from './logo';

const NAV_LINKS = [
  { href: '/docs', label: 'Docs' },
  { href: '/docs/quickstart', label: 'Examples' },
  { href: '#cloud', label: 'Cloud ⚡', highlight: true },
  { href: '/blog', label: 'Blog' },
];

const GITHUB_URL = 'https://github.com/agentbreeder/agentbreeder';

export function Nav({ docsSearch = false }: { docsSearch?: boolean }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <nav
        className="sticky top-0 z-50 flex h-14 items-center justify-between border-b px-4 sm:px-8 md:grid md:grid-cols-3"
        style={{
          background: 'rgba(9,9,11,0.85)',
          backdropFilter: 'blur(12px)',
          borderColor: 'var(--border)',
        }}
      >
        {/* Left: Logo */}
        <div className="flex items-center">
          <Logo />
        </div>

        {/* Center: Nav links or docs search — hidden on mobile */}
        <div className="hidden md:flex items-center justify-center">
          {!docsSearch && (
            <ul className="flex list-none gap-1 m-0 p-0">
              {NAV_LINKS.map(({ href, label, highlight }) => (
                <li key={href}>
                  <Link
                    href={href}
                    className="rounded-md px-3 py-1.5 text-sm no-underline transition-colors hover:text-white"
                    style={highlight
                      ? { color: '#c084fc', fontWeight: 600 }
                      : { color: 'var(--text-muted)' }
                    }
                  >
                    {label}
                  </Link>
                </li>
              ))}
            </ul>
          )}

          {docsSearch && (
            <div
              className="flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-1.5 text-sm"
              style={{
                background: 'var(--bg-surface)',
                borderColor: 'var(--border)',
                color: 'var(--text-muted)',
                width: '220px',
              }}
            >
              <span>🔍</span>
              <span>Search docs...</span>
              <kbd
                className="ml-auto rounded border px-1.5 py-0.5 font-mono text-[10px]"
                style={{ background: 'var(--bg-elevated)', borderColor: 'var(--border-hover)' }}
              >
                ⌘K
              </kbd>
            </div>
          )}
        </div>

        {/* Right: buttons — hidden on mobile */}
        <div className="hidden md:flex items-center justify-end gap-2.5">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs no-underline transition-colors hover:text-white"
            style={{ borderColor: 'var(--border-hover)', color: 'var(--text-muted)' }}
          >
            ★ &nbsp;GitHub
          </a>
          {!docsSearch && (
            <Link
              href="/docs"
              className="rounded-lg px-4 py-1.5 text-sm font-bold no-underline transition-opacity hover:opacity-90"
              style={{ background: 'var(--accent)', color: '#000' }}
            >
              Get Started →
            </Link>
          )}
        </div>

        {/* Hamburger — visible on mobile only */}
        <button
          className="md:hidden flex flex-col gap-[5px] p-2 rounded-md"
          onClick={() => setOpen(!open)}
          aria-label="Toggle menu"
        >
          <span
            className="block h-0.5 w-5 rounded transition-all duration-200"
            style={{
              background: 'var(--text-muted)',
              transform: open ? 'rotate(45deg) translate(3.5px, 3.5px)' : 'none',
            }}
          />
          <span
            className="block h-0.5 w-5 rounded transition-all duration-200"
            style={{
              background: 'var(--text-muted)',
              opacity: open ? 0 : 1,
            }}
          />
          <span
            className="block h-0.5 w-5 rounded transition-all duration-200"
            style={{
              background: 'var(--text-muted)',
              transform: open ? 'rotate(-45deg) translate(3.5px, -3.5px)' : 'none',
            }}
          />
        </button>
      </nav>

      {/* Mobile drawer */}
      {open && (
        <div
          className="md:hidden sticky top-14 z-40 border-b px-4 pb-4 pt-3"
          style={{
            background: 'rgba(9,9,11,0.97)',
            backdropFilter: 'blur(12px)',
            borderColor: 'var(--border)',
          }}
        >
          <ul className="flex flex-col gap-1 list-none m-0 p-0 mb-3">
            {NAV_LINKS.map(({ href, label, highlight }) => (
              <li key={href}>
                <Link
                  href={href}
                  onClick={() => setOpen(false)}
                  className="block rounded-md px-3 py-2 text-sm no-underline transition-colors hover:text-white"
                  style={highlight
                    ? { color: '#c084fc', fontWeight: 600 }
                    : { color: 'var(--text-muted)' }
                  }
                >
                  {label}
                </Link>
              </li>
            ))}
          </ul>
          <div className="flex flex-col gap-2">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-sm no-underline transition-colors hover:text-white"
              style={{ borderColor: 'var(--border-hover)', color: 'var(--text-muted)' }}
            >
              ★ &nbsp;GitHub
            </a>
            <Link
              href="/docs"
              onClick={() => setOpen(false)}
              className="rounded-lg px-4 py-2 text-sm font-bold no-underline text-center transition-opacity hover:opacity-90"
              style={{ background: 'var(--accent)', color: '#000' }}
            >
              Get Started →
            </Link>
          </div>
        </div>
      )}
    </>
  );
}
