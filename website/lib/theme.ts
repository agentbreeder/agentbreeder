import { THEMES } from './theme-guard.mjs';

// Browser chrome colours for <meta name="theme-color"> (design v2 §7). They equal --background
// in website/brand.css (light :root, dark .dark); tests/test_website_tokens.py checks that.
// Kept out of the .tsx files, which may not hold colour literals (§8.5).
export const THEME_COLOR = { light: '#ffffff', dark: '#0a0a0a' } as const;

// The allow-list and the guard script live in theme-guard.mjs (plain JS, tested in Node).
export { THEMES, THEME_STORAGE_KEY, THEME_GUARD_SCRIPT } from './theme-guard.mjs';
export type ThemeChoice = (typeof THEMES)[number];

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return typeof value === 'string' && (THEMES as readonly string[]).includes(value);
}
