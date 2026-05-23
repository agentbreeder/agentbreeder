// website/tailwind.config.ts
// Note: fumadocs-ui v16 uses CSS-based theming (see app/globals.css).
// Tailwind v4 — the JS config still exposes utilities; the source-of-truth
// for color values is the CSS custom properties in app/globals.css.
//
// All semantic colors below map to CSS vars so changing a value in
// globals.css propagates to every utility class automatically. Use these
// utilities in new code instead of `style={{ color: 'var(--text-muted)' }}`.
import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './content/**/*.{md,mdx}',
    './node_modules/fumadocs-ui/dist/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        // Surfaces
        bg: 'var(--bg)',
        surface: 'var(--bg-surface)',
        elevated: 'var(--bg-elevated)',

        // Borders
        'border-default': 'var(--border)',
        'border-hover': 'var(--border-hover)',

        // Text — text, text-muted, text-dim already meet WCAG 4.5:1 (see #482)
        text: 'var(--text)',
        muted: 'var(--text-muted)',
        dim: 'var(--text-dim)',

        // Brand
        accent: 'var(--accent)',
        'accent-dim': 'var(--accent-dim)',
        'accent-border': 'var(--accent-border)',
        purple: 'var(--purple)',
      },

      // Marketing type scale. Apply with `text-display`, `text-h1`, etc.
      // Body sizes inherit Tailwind defaults (text-sm, text-base, text-lg).
      fontSize: {
        'display-xl': ['3.5rem', { lineHeight: '1.05', letterSpacing: '-0.035em' }],
        'display':    ['3rem',   { lineHeight: '1.1',  letterSpacing: '-0.03em'  }],
        'h1':         ['2.25rem',{ lineHeight: '1.15', letterSpacing: '-0.025em' }],
        'h2':         ['1.75rem',{ lineHeight: '1.2',  letterSpacing: '-0.02em'  }],
        'h3':         ['1.375rem',{ lineHeight: '1.3', letterSpacing: '-0.015em' }],
        'eyebrow':    ['0.6875rem', { lineHeight: '1', letterSpacing: '0.125em' }],
      },

      letterSpacing: {
        display: '-0.035em',
        headline: '-0.02em',
      },

      fontFamily: {
        sans: ['var(--font-geist-sans)', 'Inter', 'sans-serif'],
        mono: ['var(--font-geist-mono)', 'JetBrains Mono', 'monospace'],
        // Display font for marketing hero headlines only. See #493.
        display: ['var(--font-display)', 'var(--font-geist-sans)', 'Inter', 'sans-serif'],
      },
    },
  },
};

export default config;
