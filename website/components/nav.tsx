'use client';

import Link from 'next/link';
import { useState } from 'react';
import { Logo } from './logo';

type NavLink = {
  href: string;
  label: string;
  highlight?: boolean;
  icon?: 'zap';
};

const NAV_LINKS: NavLink[] = [
  { href: '/docs', label: 'Docs' },
  { href: '/docs/quickstart', label: 'Examples' },
  { href: '#cloud', label: 'Cloud', highlight: true, icon: 'zap' },
  { href: '/blog', label: 'Blog' },
];

function NavIcon({ name }: { name: 'zap' }) {
  if (name === 'zap') {
    return (
      <svg
        aria-hidden="true"
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="currentColor"
        className="inline-block -mt-0.5 ml-1"
      >
        <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z" />
      </svg>
    );
  }
  return null;
}

function GitHubIcon() {
  return (
    <svg
      aria-hidden="true"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="currentColor"
    >
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.78-.25.78-.55v-2.03c-3.2.7-3.88-1.37-3.88-1.37-.53-1.34-1.29-1.69-1.29-1.69-1.05-.72.08-.71.08-.71 1.16.08 1.77 1.19 1.77 1.19 1.04 1.78 2.72 1.27 3.38.97.11-.75.41-1.27.74-1.56-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.47.11-3.06 0 0 .97-.31 3.18 1.19a11.05 11.05 0 0 1 5.78 0c2.21-1.5 3.18-1.19 3.18-1.19.63 1.59.23 2.77.11 3.06.74.81 1.19 1.84 1.19 3.1 0 4.42-2.69 5.39-5.25 5.68.42.36.8 1.07.8 2.16v3.2c0 .31.21.66.79.55 4.57-1.52 7.86-5.83 7.86-10.91C23.5 5.65 18.35.5 12 .5z" />
    </svg>
  );
}

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
              {NAV_LINKS.map(({ href, label, highlight, icon }) => (
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
                    {icon && <NavIcon name={icon} />}
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
            aria-label="AgentBreeder on GitHub"
            className="flex min-h-[44px] items-center gap-1.5 rounded-md px-2 text-sm no-underline transition-colors hover:text-white"
            style={{ color: 'var(--text-muted)' }}
          >
            <GitHubIcon />
            <span className="hidden lg:inline">GitHub</span>
          </a>
          {!docsSearch && (
            <Link
              href="/docs"
              className="flex min-h-[44px] items-center rounded-lg px-4 text-sm font-bold no-underline transition-opacity hover:opacity-90"
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
            {NAV_LINKS.map(({ href, label, highlight, icon }) => (
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
                  {icon && <NavIcon name={icon} />}
                </Link>
              </li>
            ))}
          </ul>
          <div className="flex flex-col gap-2">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex min-h-[44px] items-center justify-center gap-2 rounded-md text-sm no-underline transition-colors hover:text-white"
              style={{ color: 'var(--text-muted)' }}
            >
              <GitHubIcon />
              GitHub
            </a>
            <Link
              href="/docs"
              onClick={() => setOpen(false)}
              className="flex min-h-[44px] items-center justify-center rounded-lg px-4 text-sm font-bold no-underline transition-opacity hover:opacity-90"
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
