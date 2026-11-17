// Browser chrome colours for <meta name="theme-color"> (design v2 §7). They equal --background
// in website/brand.css (light :root, dark .dark); tests/test_website_tokens.py checks that.
// Kept out of the .tsx files, which may not hold colour literals (§8.5).
export const THEME_COLOR = { light: '#ffffff', dark: '#0a0a0a' } as const;

export const THEMES = ['system', 'light', 'dark'] as const;
export type ThemeChoice = (typeof THEMES)[number];
