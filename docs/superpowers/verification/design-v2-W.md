# Design v2 verification: website on the v2 tokens, step W (#1115)

Status: **partial** (branch `feat/1115-website-v2`, 2026-11-16, macOS 13, Chromium 148 through
Playwright 1.64.1, a production build served by `next start` on loopback). The theme, motion,
keyboard, reflow, axe and literal checks pass. Since the glass revision (below), the §6.3 delta
reaches 1.5% over the glass-exposed part of the sheet, but not over the whole sheet as §6.3 words
it; the owner accepts that definition or not in the PR. Not met: the §6.3 delta over the whole
sheet, the §12 Lighthouse performance score on the docs pages (also below target on `main`), and
the §14.5 state sheet and §14.10 VoiceOver pass, which were not run. Nothing was deployed; no
Docker was used.

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
| Tampered stored theme (§10.2): nine values (`"x y"`, `"<img src=x onerror=alert(1)>"`, `""`, a 100,000-character string, `"dark "`, `"foo"`, `"system light"`, `"constructor"`, `"__proto__"`) on `/` and `/docs/install`, OS light and dark, through `DOMContentLoaded`, hydration and one theme switch; plus seven values from another tab | **pass** after the stored-theme fix (below): 36/36 and 7/7, no page error, no class added, effective theme `system`. Before the fix a value with a space blanked the page after hydration (found in the glass revision) |
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
| Glass vs fallback (§6.3, ≥ 1.5% mean luminance delta inside the sheet) | **partial**: the whole sheet, as §6.3 words it, is not met (1280: light 0.68%, dark 0.65%). Over the glass-exposed area (the sheet minus the manifest `pre` and the session island) it is met after the glass revision: 1280 light 1.75%, dark 1.68%; 390 light 1.76%, dark 1.84% (before: 0.43% and 0.20% at 1280). Owner to accept the narrower definition. See below |
| Lighthouse (§12), mobile, simulated throttling | **partial**, see below |
| CSS added (§12, ≤ 3 KB gz) | **pass**: +1.9 KB gzipped against `main` (17.8 → 19.7 KB, after the glass revision). All JS chunks +4.2 KB gz (toggle, drawer, theme sync, MDX components and motion together) |
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

### Glass: the revision (owner decision 2026-11-16, "make glass more visible")

§6.3 asks for a ≥ 1.5% mean luminance delta between the glass and its opaque fallback inside
the hero sheet. With `--glass` at 84% (fixed by §5.4's contrast ratios), at most 16% of the
backdrop shows through, and most of the sheet is opaque panels (the manifest and the session
island). With only the page-wide washes at 14%, the delta was 0.43% (light) and 0.20% (dark)
over the glass-exposed area. The owner chose to make the glass more visible.

The change is W-only; no `brand.css` token moved, so the Cloud pin and the console are
unaffected. A `.hero-glow` element sits in the sheet's column, first in DOM order (so it paints under the
gate diagram and never dims the gate line), directly behind the sheet: a `--tint-green` radial
behind the checks band and a `--tint-violet` radial behind the header. Its alpha is far above
the spec's tint limits (§5.1: tints ≤ 10%; §6.3: washes ≤ 14%): light 60% and 55%, dark 85% and
80%, through relative colour syntax. The owner's decision amends §6.3 for this one backdrop
element (see the spec); the tokens are unchanged. A browser without relative colour syntax
(before Chrome 119, Safari 16.4, Firefox 128) keeps the token alpha, so the glow is nearly
invisible there and the deltas below hold only with it. The box is 48 px larger than the sheet
on each side and each ellipse fades out inside it (no clipped edges). It is hidden when `backdrop-filter` is
unsupported, under `prefers-reduced-transparency` and under forced colours, so every fallback
stays flat. The page-wide washes are unchanged at 14%.

Measured the same way as before (production build, Chromium, reduced motion, the sheet's
screenshot with glass against the same with `.glass` forced to `--glass-fallback`; mean absolute
grey difference; at 390 the sheet is scrolled into view):

The glass-exposed area is the sheet's visible box minus the bounding boxes of `.ab-sheet pre`
(the manifest) and `.ab-sheet .ab-island` (the session): the header row, the checks band and the
padding between them.

| Width | Light, glass-exposed / whole sheet | Dark, glass-exposed / whole sheet |
|---|---|---|
| 1280, before | 0.43% / 0.17% | 0.20% / 0.08% |
| 1280, after | **1.75%** / 0.68% | **1.68%** / 0.65% |
| 390, before | 1.05% / 0.54% | 0.71% / 0.36% |
| 390, after | **1.76%** / 0.90% | **1.84%** / 0.94% |

The whole-sheet figure stays under 1.5%: about 60% of the sheet is the opaque manifest and the
session island, which §6.1 and DS12 keep opaque. Reaching 1.5% over the whole sheet would need
the glass area to carry about 4%, a bit over twice the current glow.

- Contrast: text on the sheet is only `--foreground` and `--text-muted` (§5.4), and §5.4's
  `--glass` minimums are computed with `--foreground` behind the glass, the worst case in each
  theme; any tinted backdrop is further from the text colour than that, so the ratios hold (light 13.89 / 5.48,
  dark 10.17 / 4.72; control border 3.33 / 3.01). axe: 0 violations on 44 runs after the change.
- Fallback: `home-glass-fallback-*` and `home-forced-colors-light-1280` are flat.
- Cost: +0.1 KB CSS gzipped (19.7 KB total, +1.9 KB against `main`). Scroll at 4× CPU
  throttling, `/` and `/docs`, 390 and 1280, both themes: median frame 16.7 ms with or without
  the glow, no frame regression (one run had a p95 spike on light 1280 with the glow; two
  reruns were clean, machine load average ~8). Lighthouse `/` dark, five runs: 93, 71, 70, 94, 95 (the two low
  runs had 1,040 ms total blocking time with the machine at load average ~10; this change adds
  no JavaScript). No horizontal scroll at 320, 390, 768, 1024.
- The nav, the docs top bar and menus are `--glass-strong` (92%) chrome; at rest over a flat
  page they cannot differ from the fallback by design (§6.3: "other pages use flat
  `--background`"). Cards are opaque by §6.1. Neither was changed.

The owner still signs off in the PR that the sheet reads as glass (§6.3).

## Stored-theme fix (coordinator task, 2026-11-16)

next-themes 0.4.6 passes the stored `theme` to `classList.add` in two places: its blocking script
(inside a try) and its React effect (outside one). So a value with a space (`"x y"`, a markup-like
string) threw after hydration and the page showed "This page couldn't load", and any other unknown
value (`"foo"`) became a class on `<html>`. `main` is not affected: it still forces dark.

The fix has two parts:

- `value: { light: 'light', dark: 'dark' }` on the provider (`app/layout.tsx`). The React effect
  then adds only mapped classes, so an unknown theme such as `"x y"` adds none instead of throwing.
  It does not cover a prototype key: `"constructor"` looks up `Object`, and `classList.add` throws
  on its string form.
- `THEME_GUARD_SCRIPT` (`lib/theme-guard.mjs`, re-exported by `lib/theme.ts`), rendered first in
  `<body>`, before next-themes' script. It removes a stored value outside `system`/`light`/`dark`
  inside a try, so next-themes reads no value and falls back to `system`. It also registers a
  capture `storage` listener before next-themes registers its own at hydration. For a value from
  another tab outside the three themes, it calls `stopImmediatePropagation` and removes the value,
  so next-themes never adopts it, and the other tabs see a null value and reset to `system`. The
  script is built from `THEMES` with `JSON.stringify` and only compares the value.

Playwright (Chromium, production build), the check script kept outside the repo like the others.
Nine values are seeded once (`"x y"`, `"<img src=x onerror=alert(1)>"`, `""`, 100,000 × `a`,
`"dark "`, `"foo"`, `"system light"`, `"constructor"`, `"__proto__"`) on `/` and `/docs/install`,
OS light and dark. The script listens for page errors and dialogs from navigation through
`DOMContentLoaded`, hydration (until the toggle shows a checked option) and one click to the other
theme. At each step it asserts that the page renders, `<html>` has no class beyond the untampered
baseline plus `light`/`dark`, and no `img[src=x]` exists. After hydration it asserts the effective
theme matches the OS, storage holds no value, and the toggle shows System. After the switch it
asserts the new theme is applied and stored. Cross-tab: a second tab writes `"x y"`, `"foo"`,
`"dark "`, `"constructor"`, `"toString"`, `"__proto__"` or `"hasOwnProperty"` after the first tab
has hydrated. Page errors that the same page throws untampered are reported and not counted: React
#418 on the docs pages, also on `main` (Known issues).

| Run | Result |
|---|---|
| Fix as committed | **pass**: 36/36 cases and 7/7 cross-tab values, 0 failures |
| First revision (value map, stored-value guard, a reset to `system` in `ThemeColorSync`), cross-tab | **failed** on the four prototype keys: `classList.add` threw (`function Object() { [native code] }`) and the page broke before the reset ran. Found by the Sonnet review (below); the `storage` listener replaced the reset |
| Guard emptied in the HTML (value map only), control | the run stops at a stored `"constructor"`: the page never hydrates. Before that, `"foo"` and the long string are classes on `<html>` from next-themes' blocking script, and every value stays stored. The guard is needed for both |
| axe, 11 pages × light/dark × 390/1280 | **pass**: 0 violations on all 44 runs |
| `npm run build`, `npm run lint`, `tsc --noEmit` | **pass** |
| `tests/test_website_theme_guard.py` (new: runs the guard in Node against a fake `localStorage` and `storage` events; skipped without Node), `tests/test_website_tokens.py`, `tests/test_brand_tokens.py` | **pass** on 3.11, 3.12, 3.13 (92 each) |

A future CSP (DS7) needs a nonce or a hash for the guard as well as for next-themes' script (spec
§11). Blocked storage (`localStorage` throws) is unchanged: the guard's try catches it.

### Review

| Round | Reviewer | Verdict |
|---|---|---|
| r1 (uncommitted, on `fc755e05`) | Claude Sonnet, read-only; frontend engineering, security | CHANGES REQUESTED: 1 major, 2 minor, 1 nit. Major: a prototype key from another tab still crashed through the value map (reproduced, fixed with the guard's `storage` listener, now in the Playwright run and the Node test). Minor: the repo test was source substrings only (now runs the guard in Node); the docs overclaimed the cross-tab case (reworded). Nit: CSP line wrap. |
| r2 (head `e4c6f97d`) | same reviewer | APPROVED; `e4c6f97d` has the reviewed code unchanged, plus the r2 docs nits (measured size, this row). Verified listener order (capture listener first, on `window`), the cross-tab convergence (no loop), null and `clear()` events, and that the Node test fails if the guard regresses. Nits: real dispatch order is covered by the Playwright run only, not the Node test; the guard size (now measured, 334 bytes); a tab without the provider keeps its state while storage is empty. |

## Screenshots

`docs/superpowers/verification/design-v2/website/` (lossless WebP, viewport captures):
`<page>-<light|dark>-<320|390|1280>.webp` for `home`, `docs`, `install`, `manifest`, `blog`, `post`
(why-i-built-agentbreeder), `cloud`, `about` and `404`, all with reduced motion; plus
`home-drawer-dark-390`, `home-forced-colors-light-1280`, `home-reduced-motion-light-1280`,
`home-js-off-1280` and the glass/fallback captures `home-glass[-fallback]-<light|dark>-<390|1280>`.
The home captures and the glass captures were refreshed in the glass revision.

## Known issues left for follow-ups

- React hydration error #418 on the docs pages (`main` has it too).
- `/docs/install` Lighthouse performance below 90 (`main` too).
