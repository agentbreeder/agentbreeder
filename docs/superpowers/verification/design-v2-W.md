# Design v2 verification: website on the v2 tokens, step W (#1115)

Status: **partial** (branch `feat/1115-website-v2`, 2026-11-16, macOS 13, Chromium 148 through
Playwright 1.64.1, a production build served by `next start` on loopback). The theme, motion,
keyboard, reflow, axe and literal checks pass. Three acceptance items do not: the §6.3 glass
luminance delta (owner decision below), the §12 Lighthouse performance score on the docs pages
(also below target on `main`), and the §14.5 state sheet and §14.10 VoiceOver pass, which were
not run. Nothing was deployed; no Docker was used.

This is on a branch, not on `main` and not in a published preview. It merges together with T
(#1119), L (#1116) and C (#1117) at the design v2 switch (DS9 as amended).

## What changed

Spec: `docs/superpowers/specs/2026-11-16-design-system-v2.md`, row W of §13.

- §10.2 theme: `RootProvider` with `defaultTheme: 'system'`, `enableSystem`, `themes: ['light',
  'dark']`, `disableTransitionOnChange`; the hard-coded `dark` class and `forcedTheme` are gone.
  The §10.1 System/Light/Dark radiogroup (`components/theme-toggle.tsx`) sits in the nav, the
  mobile drawer and the docs sidebar (fumadocs' own switch exposes no checked state, so per §10.1
  the shared radiogroup is mounted in its slot). `theme-color` follows the toggle.
- §10.2 fumadocs: every `--color-fd-*` the installed `fumadocs-ui` declares is mapped to a brand
  token; the legacy `:root` palette and both fumadocs overrides are deleted, and so are T's shadow
  pins.
- §8 components: nav (glass, `aria-current` with reserved bold width), drawer (glass sibling of the
  nav, focus trap, Esc, scrim, scroll lock, `inert` page), hero, cards, badges, buttons, inputs,
  code blocks (a CSS-variable Shiki theme on `--syntax-*`), tables, docs chrome, blog prose, the
  two blog diagrams (monochrome, one `--brand` stroke), footer, 404, about, cloud, privacy, terms.
- §8.5 literals: none left in `app/**/*.tsx` and `components/**/*.tsx`;
  `tests/test_website_tokens.py` enforces it, with the fumadocs mapping, Google Fonts,
  third-party hosts, motion rules and theme options.
- §9 motion: keyframes moved out of the hero's inline `<style>`; the `<h1>` is never hidden (it
  rises 4 px); animations exist only under `prefers-reduced-motion: no-preference`; the replay and
  the gate dot pause off-screen, in a hidden tab, and with a Pause control (WCAG 2.2.2).
- §6.3 backdrop: two tint washes (strengthened to 14% alpha, the spec's maximum) and a masked
  48 px grid behind one glass sheet; the gate diagram runs under it.
- DS2 Bricolage removed (no `next/font/google`); DS12 the install command and the recorded session
  are dark islands; DS13 a new dark OG image (`public/og.svg` → `public/og.png`); a favicon
  (`app/icon.svg`) with a `prefers-color-scheme` rule; DS11 the gate line under "Gate the deploy."
- Content: the home keeps `INSTALL_CMD` at `npx agentbreeder@3.0.0-alpha.3 quickstart` and every
  claim from #1124/#1125. Copy changed only where it became false or overclaimed: the Cloud
  waitlist no longer says "Early access opens Q3 2026" (#492); the about page's "dark-only"
  paragraph; the blog post CTA ("Define once, deploy anywhere, govern automatically"). Docs page
  content (`website/content/**`) is unchanged.

## Checks

Scripts (kept outside the repo): a Playwright capture script for the screenshot matrix, a check
script for the items below, axe through `axe-playwright-python` 0.1.8, and Lighthouse 12.8.2.

| Check (spec) | Result |
|---|---|
| No flash, `/` and `/docs/install` (§14.3): OS dark and no stored value; stored `dark` with OS light; stored `light` with OS dark — `<html>` class and body background at `DOMContentLoaded` | **pass** (6/6) |
| Tampered stored theme (§10.2): `"x y"`, `"foo"`, `"<img src=x onerror=alert(1)>"` | **pass**: `"x y"` throws inside next-themes' try (no class), `"foo"` adds the class `foo`, markup is never created. A `value` map would not change this (the script falls back to the raw value), so none was added |
| `localStorage` throwing, OS dark | **fails before hydration**: next-themes' blocking script stops when storage throws, so the first paint is light; after hydration the page is dark. Not a §14.3 website case; fixing it needs another inline script, which §11 rules out |
| JavaScript off (§14.11) | **pass**: light, the whole session visible, `<h1>` opaque |
| LCP never hidden (§9.4) | **pass**: `<h1>` opacity 1 at `DOMContentLoaded` |
| CLS on `/` (§12, ≤ 0.02), motion on, replay running | **pass**: 0.0000 |
| Loops pause off-screen and with the Pause control (§9.5, WCAG 2.2.2); with JavaScript off the gate dot does not loop (the hero renders paused until its script runs) | **pass** |
| Toggle: radiogroup, arrows/Home/End, persistence, `theme-color`, shared with the docs (§10.1) | **pass** |
| Keyboard (§14.8): 138–139 Tab stops on `/` (1280), `/docs/install` (390, 1280), a blog post (1280); none covered by sticky chrome, outline visible | **pass** |
| Drawer: focus trapped, Esc closes and returns focus | **pass** |
| Reflow (§14.9): 200% zoom at 1280 and WCAG 1.4.12 text spacing on `/` and `/docs/install` | **pass** |
| axe (§14.6): 11 pages × light/dark × 390/1280 | **pass**: 0 violations of any impact on all 44 runs |
| Glass vs fallback (§6.3, ≥ 1.5% mean luminance delta inside the sheet) | **fails**: light 0.17% (0.43% over the glass-exposed area), dark 0.08% (0.20%). See below |
| Lighthouse (§12), mobile, simulated throttling | **partial**, see below |
| CSS added (§12, ≤ 3 KB gz) | **pass**: +1.8 KB gzipped against `main` (17.8 → 19.6 KB). All JS chunks +4.2 KB gz (toggle, drawer, theme sync, MDX components and motion together) |
| Fonts (§12) | **pass**: Bricolage removed, no new font files |
| `tests/test_website_tokens.py`, `tests/test_brand_tokens.py` | **pass** on 3.11, 3.12, 3.13 |
| `npm run build`, `npm run lint` | **pass** |
| State sheet `/design-states` (§14.5) | **not run**: not built in W |
| VoiceOver pass (§14.10) | **not run**: needs a person at the Mac |
| Safari blur cost on the hero sheet (§6.4) | **not run** |

### axe against `main`

The same axe run against `main` (8712696d, forced dark) found serious `color-contrast` (docs,
blog post), `link-in-text-block` and `scrollable-region-focusable` (home), and moderate
`landmark-one-main`, `region`, `landmark-unique` and `heading-order`. W fixes all of them: a
`<main>` around the docs page, the preview banner in an `<aside>`, named code regions, focusable
scrolling tables, empty table corners rendered as `td`, and fumadocs' invisible collapsed-sidebar
buttons removed from the tab order.

### Lighthouse

Single runs, both themes: accessibility 100, SEO 100, best practices 100 on `/` and 96 on the docs
pages. The docs pages lose 4 points to a React hydration error (#418) that `main` has too.
Performance swung between runs of the same page on this machine (other sessions were running),
so three dark runs each against `main` and W:

| Page | `main` | W |
|---|---|---|
| `/` | 82, 94, 93 | 95, 80, 95 |
| `/docs/install` | 83, 83, 87 | 88, 78, 70 |

`/` meets the ≥ 90 target on its median; `/docs/install` does not on `main` or on W (the W runs
with 78 and 70 had total blocking time spikes of 680 and 890 ms). LCP 2.6–3.5 s, CLS 0 everywhere.
The §12 Lighthouse target for the docs pages is not met.

### Glass: an owner decision

§6.3 asks for a ≥ 1.5% mean luminance delta between the glass and its opaque fallback inside
the hero sheet. With `--glass` at 84% (fixed by §5.4's contrast ratios), at most 16% of the
backdrop shows through, and most of the sheet is opaque panels (the manifest and the session
island). With the washes at the spec's maximum of 14%, the delta is 0.43% (light) and 0.20%
(dark) over the glass-exposed area. The spec's remaining levers are excluded (a lighter `--glass`
would break §5.4). Options: accept the sheet as it is (the owner signs off that it reads as glass,
as §6.3 asks either way), lower the threshold, or allow a denser element behind the sheet. The
screenshots `home-glass-*` and `home-glass-fallback-*` show both renderings.

## Screenshots

`docs/superpowers/verification/design-v2/website/` (lossless WebP, viewport captures):
`<page>-<light|dark>-<320|390|1280>.webp` for `home`, `docs`, `install`, `manifest`, `blog`, `post`
(why-i-built-agentbreeder), `cloud`, `about` and `404`, all with reduced motion; plus
`home-drawer-dark-390`, `home-forced-colors-light-1280`, `home-reduced-motion-light-1280`,
`home-js-off-1280` and the four glass/fallback captures.

## Known issues left for follow-ups

- React hydration error #418 on the docs pages (`main` has it too).
- `/docs/install` Lighthouse performance below 90 (`main` too).
