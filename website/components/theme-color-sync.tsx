'use client';

import { useEffect } from 'react';
import { useTheme } from 'next-themes';
import { THEME_COLOR } from '@/lib/theme';

// The theme-color metas follow the OS (their media queries); the toggle can disagree with the
// OS, so this sets both to the resolved theme's colour (design v2 §7). No request, no storage.
export function ThemeColorSync() {
  const { resolvedTheme } = useTheme();
  useEffect(() => {
    if (resolvedTheme !== 'light' && resolvedTheme !== 'dark') return;
    const color = THEME_COLOR[resolvedTheme];
    document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.setAttribute('content', color));
  }, [resolvedTheme]);
  return null;
}
