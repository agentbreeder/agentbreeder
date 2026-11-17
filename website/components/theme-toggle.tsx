'use client';

import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useTheme } from 'next-themes';
import { MonitorIcon, MoonIcon, SunIcon } from './icons';
import { THEMES, type ThemeChoice } from '@/lib/theme';

const OPTIONS: { value: ThemeChoice; label: string; Icon: typeof SunIcon }[] = [
  { value: 'system', label: 'System', Icon: MonitorIcon },
  { value: 'light', label: 'Light', Icon: SunIcon },
  { value: 'dark', label: 'Dark', Icon: MoonIcon },
];

// The theme toggle (design v2 §10.1): a three-option radiogroup. Arrow keys move and select;
// one option is in the tab order. The choice stays in the browser (next-themes' `theme` key):
// no request, no analytics event.
export function ThemeToggle({ size = 'md' }: { size?: 'md' | 'lg' }) {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  // The stored choice is only known in the browser; until then no option is checked, so the
  // server and first client render agree.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => setMounted(true), []);

  const current: ThemeChoice | null =
    mounted && THEMES.includes(theme as ThemeChoice) ? (theme as ThemeChoice) : mounted ? 'system' : null;
  const focusIndex = Math.max(0, OPTIONS.findIndex((o) => o.value === current));

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    let next: number;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (focusIndex + 1) % OPTIONS.length;
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (focusIndex - 1 + OPTIONS.length) % OPTIONS.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = OPTIONS.length - 1;
    else return;
    e.preventDefault();
    setTheme(OPTIONS[next].value);
    refs.current[next]?.focus();
  }

  // 28 px options in the desktop nav (≥ 24 px, WCAG 2.5.8); 44 px in the mobile drawer (touch).
  const box = size === 'lg' ? 'h-11 w-11' : 'h-7 w-7';

  return (
    <div
      role="radiogroup"
      aria-label="Theme"
      onKeyDown={onKeyDown}
      className="inline-flex items-center gap-0.5 rounded-full border border-control-border bg-background p-0.5"
    >
      {OPTIONS.map(({ value, label, Icon }, i) => {
        const checked = current === value;
        return (
          <button
            key={value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={label}
            title={label}
            tabIndex={i === focusIndex ? 0 : -1}
            onClick={() => setTheme(value)}
            className={`inline-flex ${box} items-center justify-center rounded-full border transition-colors ${
              checked
                ? 'border-control-border bg-card text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            <Icon size={16} />
          </button>
        );
      })}
    </div>
  );
}

// The same radiogroup in the fumadocs theme-switch slot (docs sidebar footer and mobile menu),
// labelled like the site's mobile drawer so the row does not read as an empty field.
export function DocsThemeSwitch({ className }: { className?: string }) {
  return (
    <div className={`flex flex-1 items-center justify-between gap-2 ps-2 ${className ?? ''}`}>
      <span className="ab-label" aria-hidden="true">
        theme
      </span>
      <ThemeToggle />
    </div>
  );
}
