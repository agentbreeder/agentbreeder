'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Logo } from './logo';
import { GitHubIcon, MenuIcon, XIcon } from './icons';
import { ThemeToggle } from './theme-toggle';

const NAV_LINKS = [
  { href: '/docs', label: 'Docs' },
  { href: '/cloud', label: 'Cloud (planned)' },
  { href: '/blog', label: 'Blog' },
];

const GITHUB_URL = 'https://github.com/agentbreeder/agentbreeder';

function isCurrent(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

// Sticky glass nav (design v2 §8.3). The mobile drawer is a sibling of <nav>, never a
// descendant: a backdrop-filter element is the containing block for fixed children (§6.1).
export function Nav() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname() ?? '/';
  const buttonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  const close = useCallback((returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) buttonRef.current?.focus();
  }, []);

  // The drawer exists only under md; close it if the viewport widens while it is open.
  useEffect(() => {
    if (!open) return;
    const mq = window.matchMedia('(min-width: 768px)');
    const onChange = () => mq.matches && setOpen(false);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [open]);

  // While open: Esc closes and returns focus to the button; Tab stays inside the drawer.
  useEffect(() => {
    if (!open) return;
    const drawer = drawerRef.current;
    const focusables = () =>
      Array.from(drawer?.querySelectorAll<HTMLElement>('a[href]:not([tabindex="-1"]), button:not([disabled]):not([tabindex="-1"]), [tabindex="0"]') ?? []);
    focusables()[0]?.focus();
    // Modal while open: the page behind does not scroll and is inert to pointer, keyboard and
    // assistive tech (the drawer has aria-modal).
    const behind = Array.from(document.querySelectorAll<HTMLElement>('main, footer'));
    behind.forEach((el) => el.setAttribute('inert', ''));
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        close(true);
        return;
      }
      if (e.key !== 'Tab') return;
      const items = [buttonRef.current, ...focusables()].filter(Boolean) as HTMLElement[];
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      behind.forEach((el) => el.removeAttribute('inert'));
      document.body.style.overflow = overflow;
    };
  }, [open, close]);

  return (
    <>
      <nav
        aria-label="Main"
        className="ab-nav glass-strong sticky top-0 z-50 h-[calc(3.5rem+env(safe-area-inset-top))] pt-[env(safe-area-inset-top)] pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]"
      >
        <div className="ab-container flex h-full items-center justify-between md:grid md:grid-cols-[1fr_auto_1fr]">
          <div className="flex items-center">
            <Logo />
          </div>

          <ul className="m-0 hidden list-none items-center gap-1 p-0 md:flex">
            {NAV_LINKS.map(({ href, label }) => {
              const current = isCurrent(pathname, href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={current ? 'page' : undefined}
                    className={`inline-flex min-h-[44px] items-center rounded-md px-3 text-sm no-underline transition-colors hover:text-foreground ${
                      current ? 'font-semibold text-foreground' : 'text-muted-foreground'
                    }`}
                  >
                    <span className="ab-reserve-bold" data-label={label}>
                      {label}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>

          <div className="hidden items-center justify-end gap-2 md:flex">
            <ThemeToggle />
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="AgentBreeder on GitHub"
              className="flex min-h-[44px] min-w-[44px] items-center justify-center gap-1.5 rounded-md px-2 text-sm text-muted-foreground no-underline transition-colors hover:text-foreground"
            >
              <GitHubIcon />
              <span className="hidden lg:inline">GitHub</span>
            </a>
            <Link href="/docs/install" className="ab-btn ab-btn-primary">
              Get started
            </Link>
          </div>

          <button
            ref={buttonRef}
            type="button"
            className="inline-flex h-11 w-11 items-center justify-center rounded-md text-foreground md:hidden"
            onClick={() => setOpen((o) => !o)}
            aria-label={open ? 'Close menu' : 'Open menu'}
            aria-expanded={open}
            aria-controls="site-drawer"
          >
            {open ? <XIcon size={20} /> : <MenuIcon size={20} />}
          </button>
        </div>
      </nav>

      {open && (
        // The scrim dims the page behind the drawer; a click on it closes the drawer.
        <div
          aria-hidden="true"
          onClick={() => close(true)}
          className="fixed inset-x-0 bottom-0 top-[calc(3.5rem+env(safe-area-inset-top))] z-30 bg-scrim md:hidden"
        />
      )}
      {open && (
        <div
          id="site-drawer"
          ref={drawerRef}
          role="dialog"
          aria-modal="true"
          aria-label="Menu"
          className="ab-drawer glass-strong fixed inset-x-0 top-[calc(3.5rem+env(safe-area-inset-top))] z-40 max-h-[calc(100dvh-3.5rem-env(safe-area-inset-top))] overflow-y-auto pb-[calc(16px+env(safe-area-inset-bottom))] md:hidden"
        >
          <div className="ab-container pt-3">
            <ul className="m-0 mb-3 flex list-none flex-col gap-1 p-0">
              {NAV_LINKS.map(({ href, label }) => {
                const current = isCurrent(pathname, href);
                return (
                  <li key={href}>
                    <Link
                      href={href}
                      onClick={() => close(false)}
                      aria-current={current ? 'page' : undefined}
                      className={`flex min-h-[44px] items-center rounded-md px-3 text-base no-underline ${
                        current
                          ? 'bg-secondary font-semibold text-foreground shadow-[inset_2px_0_0_var(--primary)]'
                          : 'text-muted-foreground'
                      }`}
                    >
                      {label}
                    </Link>
                  </li>
                );
              })}
            </ul>
            <div className="mb-4 flex items-center justify-between border-t border-border pt-4">
              <span className="ab-label">theme</span>
              <ThemeToggle size="lg" />
            </div>
            <div className="flex flex-col gap-2">
              <a
                href={GITHUB_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="ab-btn ab-btn-secondary"
              >
                <GitHubIcon />
                GitHub
              </a>
              <Link href="/docs/install" onClick={() => close(false)} className="ab-btn ab-btn-primary">
                Get started
              </Link>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
