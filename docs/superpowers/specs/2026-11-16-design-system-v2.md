# Design system v2: light-first monochrome with glass surfaces (#1113)

- Status: **r3, reviews converged** (r2 APPROVED by all five lenses; r2 minors folded in). The r1 reviews through five lenses (`frontend-design`, `ui-ux-pro-max`, frontend
  engineering, `/architect`, `/security`; Claude Sonnet, read-only) are folded in (§17). No code in
  this PR. The owner decisions are in §16.
- Part of epic #1112. Siblings: #1114 (3.0.0a3 content), #1115 (home and animations on the v2
  tokens), #1116 (local console rollout), #1117 (Cloud companion PR in `agentbreeder-cloud`).
- Owner decisions already taken (2026-11-16): all three surfaces; light by default with a dark
  toggle that respects the system preference; content goes live with 3.0.0a3.
- Source of truth stays `website/brand.css` (#583, #817). OSS owns the tokens; Cloud only vendors
  them. Changes flow OSS → Cloud, never the reverse (`docs/cross-repo-sync.md`).
- Every contrast ratio here was computed with the functions in `tests/test_brand_tokens.py`
  (`contrast`, alpha composited over the surface below), not estimated. Nothing else has been run.

## 1. Problem

The three surfaces share tokens but not a look:

- The website is forced dark (`RootProvider theme={{ forcedTheme: 'dark' }}` in
  `website/app/layout.tsx`, `themeSwitch: { enabled: false }` in `app/docs/layout.tsx`). Its
  components bypass the shared tokens: 162 raw `#hex`/`rgba()` literals in 9 files under
  `website/app` and `website/components` (hero 32, platform diagram 61, architecture layers 24),
  and an older token set in `app/globals.css` (`--bg`, `--bg-surface`, `--text`, `--text-dim`,
  `--accent`, …) that **overrides** three `brand.css` tokens of the same name (`--border`,
  `--accent`, `--text-muted`). A `brand.css` change to those three never reaches the site.
- The local console (`center/static/`) has light and dark palettes and a System/Light/Dark
  preference, but applies the theme from `app.js`, a deferred module, so a dark-preference user sees
  a light frame first.
- The Cloud console is dark-only (`<html className="dark">`), though it vendors both palettes.
- The palette is zinc with a saturated green primary and a green→violet gradient. The owner asked
  for a light-first monochrome look: white and black, very small colour tints and glass surfaces.

## 2. Goal and non-goals

**Goal.** One token set, in `website/brand.css`, that gives all three surfaces:

1. a light theme by default and a dark theme, chosen by the system preference or a toggle, with no
   flash of the wrong theme;
2. a monochrome base (neutral greys, black/white primary) with colour only for state (status,
   focus), for the one signature device (§3) and for very small tints;
3. glass surfaces (translucent fill, backdrop blur, hairline border, soft shadow) on a small,
   named set of components, over a backdrop that gives the blur something to show, with an opaque
   fallback;
4. WCAG 2.2 AA everywhere: 4.5:1 for text, 3:1 for control boundaries and focus rings, focus never
   obscured, targets ≥ 24 px, checked by tests;
5. motion that respects `prefers-reduced-motion`, causes no layout shift and stays inside a
   performance budget.

**Non-goals.** Page copy (#1114); the home page's layout and animations (#1115, which builds on §9
here); a website CSP (DS7); new product surfaces; Cloud feature work beyond theming.

## 3. Identity: the gate line

Everything is quiet except one device. **The gate line** is a 1.5 px `--brand` (green) stroke that
marks "passed the gate", and it is the only saturated colour in the chrome:

- **Home hero:** the check→gate diagram is drawn in hairlines (`--border-strong`, a token in both
  themes). Only the path that passes the gate is the gate line, and it is the one animated
  element (#1115 designs the animation within §9).
- **Logo mark:** the mark's single stroke is `--brand`; the wordmark is `--foreground`.
- **Console and Cloud:** "gate passed" and a healthy deployment show a 1.5 px `--brand` ring around
  a check icon, with the word ("Passed", "Healthy") beside it. This is the only place green appears
  outside `--success` text.

Deliberately quiet: no gradients, no coloured buttons, no coloured card glows, no colour on
navigation. Tints (§4.3) are backdrops, never decoration on components.

The second idea is typographic: **Geist Mono carries data.** Section labels, IDs, digests,
versions, counts and the hero's check labels are Geist Mono at the body size, lower case. There is
no uppercase tracked eyebrow anywhere (§7).

## 4. Audit (main at ae775548)

### 4.1 Website (`website/`, Next 16.4.5, React 19.2.4, Tailwind v4, fumadocs-ui 16.7.14, next-themes 0.4.6 via fumadocs)

| Area | Today | v2 issue |
|---|---|---|
| Theme | Forced dark; `class="dark"` hard-coded on `<html>`; docs theme switch off | Must follow system/toggle |
| Tokens | `globals.css` `:root` defines `--bg #09090b`, `--bg-surface #111113`, `--bg-elevated #1a1a1e`, `--border`/`--border-hover` (white 7%/14%), `--text #e4e4e7`, `--text-muted #a1a1aa`, `--text-dim #8b8b95`, `--accent #22c55e`, `--accent-dim`, `--accent-border`, `--purple` | Dark-only values on `:root`; three names shadow `brand.css` |
| Fumadocs | `.dark` and `@media (prefers-color-scheme: dark) :root` set `--color-fd-background/muted/card/border` to dark literals | The media-query block would paint fumadocs dark inside the light theme for a dark-OS visitor |
| `tailwind.config.ts` | Defines colours and a type scale | **Not loaded** (Tailwind v4 without `@config`); dead code, deleted at W |
| Hero (`components/hero.tsx`) | Gradient headline, two radial glows, an SVG of four checks feeding a gate (infinite `ab-edge-travel`/`ab-node-pulse`, inline `<style>` in the SVG), a 650 ms session replay (`visibility` toggling, no layout shift; reduced motion shows the whole session), syntax colours as literals, macOS traffic-light dots, 9–10.5 px labels | Literals throughout; the `<h1>` is in `.hero-stagger`, which starts at `opacity: 0`, so the LCP element is hidden for up to 600 ms |
| Nav (`components/nav.tsx`) | Sticky, `rgba(9,9,11,0.85)` + `blur(12px)`; green "Get Started"; the mobile drawer is a sibling `<div>` of `<nav>` | Already glass, dark literal |
| Other components | Diagram, architecture layers, what-ships, how-it-works, built-by, footer, waitlist form, blog pages: literals and legacy vars | All move to v2 tokens |
| Blog prose | `.prose-blog` in `globals.css`, `#fff` headings | Literal |
| Fonts | Geist Sans/Mono (`geist` package) and Bricolage Grotesque (`next/font/google`, downloaded at build, self-hosted at runtime), used via `font-display` in `hero.tsx`, `app/about`, `app/cloud`, `not-found.tsx`, `globals.css` | DS2 |
| Motion guard | `prefers-reduced-motion` block in both `brand.css` and `globals.css` | Keep one |
| Utilities | grep finds no `bg-primary`, `text-muted-foreground`, `border-border` or similar token utilities in `website/app` or `website/components` | Precondition for step T (§13) |

### 4.2 Local console (`src/agentbreeder/center/static/`)

- `brand.css` is a copy of the `:root` and `.dark` blocks; `tests/test_brand_tokens.py` checks drift,
  WCAG and font hashes. `style.css` uses only tokens (no literals except `dialog::backdrop`).
- Theme: `app.js` `applyTheme` toggles `html.dark` from the stored `console-theme`
  (`system`/`light`/`dark`; `prefs` wraps `localStorage` in try/catch) after the module loads.
- CSP: `default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; …;
  require-trusted-types-for 'script'` (`center/server.py`). No inline script or style is possible.
  `static_files()` serves every file under `static/`, so a new file needs no server change.
- Green is the primary action, "healthy" and "gate passed"; violet (`--chart-2`) marks candidates.
  `a { color: var(--primary) }` (style.css:28): with a monochrome primary, links would lose their
  colour cue.

### 4.3 Cloud console (`agentbreeder-cloud/dashboard`, origin/main 69a76c9)

- Vendors `:root`, `.dark` and `@theme` in `app/tokens.css` plus `@layer utilities` and the
  `ab-pulse`/`ab-glow-breathe` keyframes. `npm run tokens:check`
  (`scripts/check-token-drift.mjs`) compares tokens per block and those three rules verbatim
  (normalized), throws if any rule is missing, and skips when no sibling OSS checkout exists (so
  Cloud CI never runs it; `TODO(M1)` in the script).
- The script keys `:root` tokens with a prefix but `.dark` and `@theme` tokens by bare name, so a
  palette token and an `@theme` token with the same name would mask each other.
- Dark-only: `<html lang="en" className="dark">`, `@custom-variant dark (&:is(.dark *))`.
- Fonts from `@fontsource-variable/geist` and `@fontsource-variable/bricolage-grotesque`.

## 5. Tokens v2

### 5.1 Rules

- Every existing token name in `:root` and `.dark` stays. Values change; names are only added.
  Both blocks declare the same names (existing test).
- No palette token shares a name with an `@theme` token (Tailwind `--shadow-*`, `--blur-*`,
  `--text-*`, `--font-*`, `--radius-*` namespaces). A self-reference such as
  `--shadow-sm: var(--shadow-sm)` is a cycle and would also be masked by Cloud's check. New test.
- Greys are Tailwind **neutral** (no hue), not zinc (DS8).
- `--primary` becomes monochrome: near-black in light, near-white in dark (DS1). Green moves to
  `--brand` (the gate line, §3) and stays in `--success`.
- Colour appears only in status text and fills, the focus ring, charts, the gate line and tints at
  ≤ 10% alpha.
- **Colour never carries meaning alone** (WCAG 1.4.1): status badges have an icon and a word;
  selected and current items have weight and a bar (§8.2); chart series have markers or labels;
  links in text are underlined.
- The legacy dark `--muted-foreground` exception ends: it becomes `#a3a3a3` (7.85:1).
- Values contain no `{`/`}` (`_block` in the test reads to the first `}`).
- `brand.css` gains a header line `Tokens v2 (#1113)`; Cloud's `tokens.css` header mirrors it with
  the OSS commit it vendored.
- `color-scheme: light` in `:root` and `color-scheme: dark` in `.dark` (plain properties; both drift
  parsers read only `--name: value` declarations from these blocks, so they ignore them; T confirms
  by running Cloud's `tokens:check` against the T commit), so scrollbars and native controls follow the theme.

### 5.2 Existing tokens, new values

| Token | Light (`:root`) | Dark (`.dark`) |
|---|---|---|
| `--background` | `#ffffff` | `#0a0a0a` |
| `--card`, `--popover` | `#ffffff` | `#141414` |
| `--foreground`, `--card-foreground`, `--popover-foreground`, `--secondary-foreground`, `--accent-foreground`, `--sidebar-foreground`, `--sidebar-accent-foreground` | `#0a0a0a` | `#ededed` |
| `--primary`, `--sidebar-primary` | `#0a0a0a` | `#ededed` |
| `--primary-foreground`, `--sidebar-primary-foreground` | `#ffffff` | `#0a0a0a` |
| `--secondary`, `--muted`, `--accent` | `#f5f5f5` | `#1f1f1f` |
| `--muted-foreground`, `--text-muted` | `#525252` | `#a3a3a3` |
| `--destructive` | `#b91c1c` (unchanged) | `#f87171` |
| `--border`, `--sidebar-border` | `#e5e5e5` (decorative) | `rgb(255 255 255 / 8%)` (decorative) |
| `--input` | `#e5e5e5` (decorative only, §8.1) | `rgb(255 255 255 / 12%)` (decorative only) |
| `--control-border` | `#737373` | `#808080` |
| `--ring`, `--sidebar-ring` | `#2563eb` (blue-600) | `#60a5fa` (blue-400) |
| `--sidebar` | `#fafafa` | `#0f0f0f` |
| `--sidebar-accent` | `#ebebeb` | `#262626` |
| `--success`, `--warning`, `--info` | unchanged | unchanged |
| `--success-subtle`, `--warning-subtle`, `--info-subtle` | unchanged | recomputed as 10% over `#0a0a0a`: `#0c1d12`, `#22190a`, `#0f1622` |
| `--chart-1…5` | unchanged | unchanged |

The focus ring is blue, not black, so it never reads as a border on a monochrome control (DS10).

### 5.3 New tokens (both blocks)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--brand` | `#15803d` | `#22c55e` | The gate line (§3). Strokes and text only, never a fill |
| `--border-strong` | `#d4d4d4` | `rgb(255 255 255 / 16%)` | Hover/selected edges, card edges on glass (decorative) |
| `--destructive-subtle` | `#fef2f2` | `#211010` | Error badge and toast fill |
| `--tint-green` | `rgb(34 197 94 / 8%)` | `rgb(34 197 94 / 10%)` | Backdrop washes only: gate, success |
| `--tint-blue` | `rgb(59 130 246 / 8%)` | `rgb(59 130 246 / 10%)` | ″ docs, info |
| `--tint-violet` | `rgb(139 92 246 / 8%)` | `rgb(139 92 246 / 10%)` | ″ registry, candidates |
| `--tint-amber` | `rgb(245 158 11 / 8%)` | `rgb(245 158 11 / 10%)` | ″ warnings |
| `--glass` | `rgb(255 255 255 / 84%)` | `rgb(20 20 20 / 84%)` | In-flow glass panels |
| `--glass-strong` | `rgb(255 255 255 / 92%)` | `rgb(10 10 10 / 92%)` | Sticky/fixed glass: nav, menus, dialogs |
| `--glass-fallback` | `#ffffff` | `#141414` | Opaque fill when glass is off (§6.3) |
| `--glass-border` | `rgb(0 0 0 / 8%)` | `rgb(255 255 255 / 10%)` | Decorative hairline |
| `--glass-highlight` | `rgb(255 255 255 / 70%)` | `rgb(255 255 255 / 10%)` | 1 px inset top highlight; carries the lift in dark |
| `--glass-blur` | `16px` | `16px` | `backdrop-filter: blur()` radius |
| `--elev-1` | `0 1px 2px rgb(0 0 0 / 5%)` | `0 1px 2px rgb(0 0 0 / 40%)` | Cards, controls |
| `--elev-2` | `0 4px 16px rgb(0 0 0 / 6%), 0 1px 2px rgb(0 0 0 / 4%)` | `0 4px 16px rgb(0 0 0 / 40%), 0 1px 2px rgb(0 0 0 / 30%)` | Glass panels, sticky chrome |
| `--elev-3` | `0 8px 24px rgb(0 0 0 / 8%), 0 2px 4px rgb(0 0 0 / 4%)` | `0 8px 24px rgb(0 0 0 / 50%), 0 2px 4px rgb(0 0 0 / 30%)` | Menus, popovers, tooltips, toasts |
| `--elev-4` | `0 16px 48px rgb(0 0 0 / 10%), 0 2px 6px rgb(0 0 0 / 5%)` | `0 16px 48px rgb(0 0 0 / 55%), 0 2px 6px rgb(0 0 0 / 35%)` | Dialogs |
| `--scrim` | `rgb(0 0 0 / 40%)` | `rgb(0 0 0 / 60%)` | `dialog::backdrop`, drawer overlay |
| `--code-background` | `#fafafa` | `#111111` | Docs code blocks |
| `--syntax-key` | `#1d4ed8` | `#93c5fd` | YAML keys |
| `--syntax-string` | `#15803d` | `#86efac` | Strings |
| `--syntax-number` | `#b45309` | `#fdba74` | Numbers |
| `--syntax-comment` | `#525252` | `#a3a3a3` | Comments |

`@theme inline` maps each colour token to `--color-*` (`--color-brand`, `--color-glass`, …),
`--elev-1…4` to Tailwind's `--shadow-xs/sm/md/lg`, and `--glass-blur` to `--blur-glass`. The
brand aliases `--color-ab-green*`/`--color-ab-purple` stay until step R.

In dark mode black shadows barely show on `#0a0a0a`, so elevation in dark is carried by the
surface step (`--background` → `--card` → `--secondary`) and by `--glass-highlight` and
`--border-strong`, not by shadow alone.

### 5.4 Contrast (computed)

| Check | Light | Dark |
|---|---|---|
| `--text-muted` on background / card / secondary / sidebar / sidebar-accent | 7.81 / 7.81 / 7.17 / 7.49 / 6.55 | 7.85 / 7.30 / 6.53 / 7.60 / 6.00 |
| `--control-border` on background / card / secondary / sidebar-accent | 4.74 / 4.74 / 4.35 / 3.98 | 5.01 / 4.66 / 4.17 / 3.83 |
| `--ring` on background / card / secondary / sidebar-accent | 5.17 / 5.17 / 4.74 / 4.34 | 7.79 / 7.25 / 6.48 / 5.95 |
| `--primary-foreground` on `--primary` | 19.80 | 16.91 |
| `--brand` on background / card / secondary | 5.02 / 5.02 / 4.60 | 8.69 / 8.08 / 7.23 |
| `--destructive` on background / card / secondary / sidebar-accent / its subtle | 6.47 / 6.47 / 5.93 / 5.43 / 5.91 | 7.16 / 6.66 / 5.96 / 5.47 / 6.62 |
| `--success` (same as `--warning` ±0.01 in light) on bg / card / secondary / sidebar-accent / subtle | 5.02 / 5.02 / 4.60 / **4.21** / 4.79 | 11.36 / 10.57 / 9.46 / 8.68 / 10.04 |
| `--info` on bg / card / secondary / sidebar-accent / subtle | 6.70 / 6.70 / 6.15 / 5.62 / 6.16 | 7.79 / 7.25 / 6.48 / 5.95 / 7.13 |
| `--glass` panel, **minimum** over background, card, secondary, tinted background and `--foreground`: foreground / text-muted / control-border / ring | 13.89 / 5.48 / 3.33 / 3.63 | 10.17 / 4.72 / 3.01 / 4.68 |
| `--glass-strong`, same minimum: foreground / text-muted / control-border / ring | 16.61 / 6.55 / 3.98 / 4.34 | 14.56 / 6.76 / 4.32 / 6.70 |
| `--syntax-*` on `--code-background` (key, string, number, comment) | 6.42 / 4.81 / 4.81 / 7.49 | 10.47 / 13.45 / 11.20 / 7.49 |
| `--text-muted` / `--foreground` on any tint over background (min) | 7.09 / 17.96 | 6.87 / 14.81 |

Two rules follow from the table:

- **On glass, text is only `--foreground` or `--text-muted`.** Status colours over glass fall to
  3.52:1 in light when dark content is behind, so status on glass always sits in an opaque
  `-subtle` badge (≥ 4.79:1).
- **`--brand` text sits only on opaque surfaces**; on glass the gate line is a stroke only.
- **Status text never sits directly on `--sidebar-accent`** (light 4.21:1); a status shown on a
  hovered or selected row uses its `-subtle` badge. This keeps today's exclusion and makes it a
  rule.

### 5.5 Test changes (`tests/test_brand_tokens.py`, step T)

- `NEW_TOKENS` gains the §5.3 names; each colour token must have its `--color-*` mapping; separate
  assertions for `--shadow-*: var(--elev-*)` and `--blur-glass: var(--glass-blur)`.
- `test_no_theme_palette_name_collision`: no `:root`/`.dark` token name appears in `@theme`.
- `test_glass_text_passes_aa`: `--foreground` and `--text-muted` ≥ 4.5 and `--control-border`,
  `--ring` ≥ 3 on `--glass` and `--glass-strong`, each composited over background, card,
  secondary, each of the four tints over background, and `--foreground` used as a backdrop colour
  (dark content behind the glass). For the tints the helper composites
  twice (tint over background with `_over`, then glass over that result) before calling `contrast`.
- `test_status_tokens_pass_aa`: §5.4 status rows, including `--destructive` on `--destructive-subtle`.
- `test_syntax_tokens_pass_aa` on `--code-background`; `--brand` on `SURFACES` ≥ 4.5.
- Dark `--muted-foreground` joins the normal pairs, and the legacy-exception test is deleted, in
  the same commit as the value change.
- `test_console_snapshot_is_self_contained` keeps the two-block rule, and a new assertion checks
  that no `@supports`/`@media` appears in the snapshot.
- `test_console_glass_matches_source`: the `.glass`/`.glass-strong` declarations and the three
  fallback at-rules in the console's `style.css` equal those in `brand.css` `@layer utilities`
  after normalization (comments and whitespace stripped).
- The `-subtle` comments say "over `#0a0a0a`".

## 6. Glass

### 6.1 Where glass is allowed

Glass is for chrome and a few showcase panels, never for content density.

| Surface | Glass | Token | Elevation |
|---|---|---|---|
| Website nav, docs top bar | yes | `--glass-strong` | 2 |
| Website mobile drawer | yes (sibling of `<nav>`, never a descendant: a `backdrop-filter` element is the containing block for `position: fixed` children) | `--glass-strong` | 3 |
| Menus, popovers, tooltips, toasts (all surfaces) | yes | `--glass-strong` | 3 |
| Dialogs (all surfaces) | yes | `--glass-strong` | 4 |
| Home hero: **one** glass sheet holding the manifest, checks and session sections, separated by hairlines | yes | `--glass` | 2 |
| Console sticky header | yes | `--glass-strong` | 2 |
| Cards, tables, rows, lists, logs, docs content, code blocks, sidebars, forms | **no**: opaque `--card`/`--code-background` | — | 0–1 |

Nested glass does not blur through its parent (the parent is a backdrop root), so the hero uses a
single sheet, not three panels.

### 6.2 Recipe and where it lives

The recipe and its fallbacks live inside the existing `@layer utilities` block of `brand.css`,
because that is the block Cloud's check compares verbatim (a top-level `@supports` would never be
checked). `@layer utilities` (not Tailwind's `@utility`) means no `hover:glass` variants, which no
component needs. The console copies the same rules into `style.css`, also wrapped in a native `@layer utilities { … }`
(no build step needed); `test_console_glass_matches_source`
compares the two layer bodies.

```css
@layer utilities {
  .glass, .glass-strong {
    border: 1px solid var(--glass-border);
    box-shadow: inset 0 1px 0 var(--glass-highlight), var(--elev-2);
    -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(160%);
    backdrop-filter: blur(var(--glass-blur)) saturate(160%);
  }
  .glass { background: var(--glass); }
  .glass-strong { background: var(--glass-strong); }
  @supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
    .glass, .glass-strong { background: var(--glass-fallback); }
  }
  @media (prefers-reduced-transparency: reduce) {
    .glass, .glass-strong { background: var(--glass-fallback); -webkit-backdrop-filter: none; backdrop-filter: none; }
  }
  @media (forced-colors: active) {
    .glass, .glass-strong { background: Canvas; border-color: CanvasText; -webkit-backdrop-filter: none; backdrop-filter: none; }
  }
}
```

Elevation 3/4 components override the shadow with `--elev-3`/`--elev-4`. Where a glass edge is a
control boundary it uses `--control-border`. `prefers-reduced-transparency` is progressive
enhancement (not every browser supports it); the contrast rules in §5.4 hold without it.

### 6.3 The backdrop: what the glass blurs

Glass over a flat page is a white card with a border. The home hero (and only it) gets a backdrop,
specified here and drawn in #1115:

- Two radial washes: `--tint-green` centred behind the top-right corner of the glass sheet,
  ellipse 60% × 50% of the hero; `--tint-violet` behind its bottom-left, 40% × 40%.
- A 48 px grid of 1 px `--border` lines, masked by a radial gradient so it fades out 40% from the
  sheet's centre.
- The gate-line diagram (§3) sits partly under the sheet, so the blur shows the line passing
  through.
- Acceptance: the glass and the fallback rendering of the hero must differ by a mean luminance
  delta of ≥ 1.5% inside the sheet (not a pixel count, which the grid alone could satisfy), and the
  owner signs off in the W PR that the sheet reads as glass. If it does not, the washes get
  stronger (≤ 14% alpha); `--glass` does not get lighter, because §5.4's text ratios depend on it.

Other pages use flat `--background`; their only glass is chrome (nav, menus, dialogs) over scrolling
content.

### 6.4 Blur performance and side effects

- At most **three** blurred elements in any viewport (nav + hero sheet + one menu/dialog).
- `--glass-blur` ≤ 16 px. Never animate `backdrop-filter` or the blur radius.
- No `opacity`, `filter`, `mask` or `clip-path` animation on an **ancestor** of a glass element (it
  makes the ancestor a backdrop root, so the blur samples nothing and then pops in). Glass enters
  with `transform` only, or fades itself.
- No glass on elements taller than 50% of the viewport, except the mobile drawer and the home hero
  sheet (which is taller on phones); W measures the hero sheet's scroll cost on Safari (iPhone and
  macOS) and drops to the fallback under 640 px if it janks. None inside scroll containers.

## 7. Typography, spacing, radii, icons, imagery

- **Families:** Geist Sans for text and display; Geist Mono for code and data (§3). Bricolage
  Grotesque is dropped (DS2): the character it gave moves to the gate line and the mono data voice.
  Display weight 600; tracking `-0.04em` at ≥ 40 px and `-0.02em` at 24–39 px, confirmed visually at
  W.
- **Scale** (`@theme`, replacing the dead `tailwind.config.ts` scale and the current display/h1/h2):

| Token | Size / line height / tracking | Use |
|---|---|---|
| `--text-display` | `clamp(2.5rem, 5vw + 1rem, 4rem)` / 1.05 / -0.04em | Home hero only |
| `--text-h1` | 2.25rem / 1.15 / -0.03em | Page titles |
| `--text-h2` | 1.75rem / 1.2 / -0.02em | Sections |
| `--text-h3` | 1.25rem / 1.3 / -0.01em | Subsections |
| `--text-label` | 0.8125rem / 1.4 / 0, Geist Mono, lower case, `--text-muted` | Section labels, IDs, data |
| body (marketing, docs) | 1rem / 1.65, prose capped at 70ch | |
| body (consoles) | 0.875rem / 1.5 (unchanged) | |
| code | 0.8125rem / 1.7, Geist Mono | |

- Minimum text size 12 px anywhere (the hero's 9–10.5 px labels go to 12–13 px; #1115).
- **Spacing:** 4 px base. Section rhythm 96 px desktop / 64 px mobile; container `max-width:
  1200px`, 24 px gutters (16 px under 640 px).
- **Radii:** controls and inputs 6 px; badges and pills 9999 px; cards 12 px; glass panels and
  dialogs 16 px; code blocks 12 px. Hierarchy shows in the radii.
- **Icons:** one family, Lucide (ISC licence, paths inlined as SVG, no runtime package; the licence
  is added with the first use), 1.5 px stroke, 16 px in text and 20 px in controls, `currentColor`.
  No filled or coloured icons except status icons and the gate-line check.
- **Illustrations:** none besides the hero diagram and the two architecture diagrams, which are
  monochrome line art with one `--brand` stroke (§3).
- **OG and social images:** stay dark (they render outside the site's theme, in feeds that are
  mostly dark); regenerated at W in the v2 look: `#0a0a0a`, white Geist display title, the logo mark
  with its green stroke.
- **Favicon:** `icon.svg` carries a `@media (prefers-color-scheme: dark)` rule inside the SVG so
  the mark stays visible on light and dark tab bars; `favicon.ico` stays as is.
- **`theme-color`:** two `<meta name="theme-color">` entries with `media` for light (`#ffffff`)
  and dark (`#0a0a0a`), through Next's `viewport` export, as the no-JS fallback. Because they follow
  the OS, not the toggle, the toggle's change handler (and the console's `theme.js`/`applyTheme`)
  also updates their `content` to the resolved theme's colour.
- **Screenshots in docs and blog:** one theme (light) with a 1 px `--border` frame and radius 12 px;
  in dark mode content images get `filter: brightness(0.88)` to cut glare (on the image itself, never
  on an ancestor of glass). Paired light/dark images are allowed for the install and command-center
  pages.

## 8. Components

### 8.1 Rules for every component

- **Focus:** `outline: 2px solid var(--ring); outline-offset: 2px` on `:focus-visible`. Always
  `outline`, never `box-shadow`, so forced-colors mode keeps it.
- **Control boundaries:** every input, select, textarea, checkbox, radio, switch track, segmented
  control and tab boundary uses `--control-border`. `--border` and `--input` are decorative only. A
  test fails on a control rule that uses `--border`/`--input` for its border (console `style.css` at
  L; website and Cloud through the state sheet, §14).
- **Targets:** ≥ 24 × 24 px everywhere (WCAG 2.5.8); ≥ 44 × 44 px hit area for touch-primary
  controls (nav, drawer, toggle, copy button, table row actions on small screens).
- **Hover** effects only under `@media (hover: hover)`.
- **Focus not obscured** (WCAG 2.4.11): `html { scroll-padding-top: 72px }` on the website (the
  56 px nav plus 16 px) and the same for the console's sticky header height; anchors get the same
  `scroll-margin-top`.
- **Disabled:** `opacity: 0.55`, `cursor: not-allowed`, no colour; exempt from contrast (WCAG), but
  the label stays readable and a reason is shown nearby when the action is blocked.
- **Theme change:** no transitions while the theme switches (`disableTransitionOnChange` on the
  website and Cloud; the console adds a `no-transition` class for one frame in `applyTheme`).
- **Mobile:** `viewport-fit=cover`; the sticky nav and drawer pad with `env(safe-area-inset-*)`.

### 8.2 Interaction states

| Element | Hover | Active/pressed | Selected/current | Disabled | Invalid | Loading |
|---|---|---|---|---|---|---|
| Primary button | `--primary` at 88% (`color-mix`) | `translateY(1px)` | — | §8.1 | — | Spinner icon (a static icon under reduced motion) + label kept, `aria-busy` |
| Secondary button | `--secondary` fill, `--control-border` | `--accent` fill | — | §8.1 | — | ″ |
| Ghost button | `--secondary` fill | `--accent` fill | — | §8.1 | — | ″ |
| Danger button | `--destructive-subtle` fill, `--destructive` text and border | ″ | — | foreground text, `--control-border` (a faded red outline would still read as live) | — | ″ |
| Nav link (site) | `--foreground` text | — | `--foreground`, weight 600, `aria-current="page"` | — | — | — |
| Sidebar item (docs, consoles) | `--sidebar-accent` fill | — | `--sidebar-accent` fill **and** 2 px inset bar `--sidebar-primary` **and** weight 600, `aria-current` | — | — | — |
| Tabs / segmented | `--foreground` text | — | `--card` fill, `--control-border` edge, weight 600, `aria-selected` | §8.1 | — | — |
| Table row | `--secondary` fill (hover devices only) | — | 2 px inset bar `--primary`, `aria-selected` | — | — | Skeleton rows |
| Menu item | `--secondary` fill | — | check icon, `aria-checked` | §8.1 | — | — |
| Input | `--foreground` border | — | focus: `--ring` outline | `--secondary` fill | `--destructive` 2 px border, icon + message, `aria-invalid` | — |
| Switch | `--foreground` track edge | — | on: `--primary` fill with `--primary-foreground` knob and a check glyph; off: `--control-border` edge, hollow | §8.1 | — | — |
| Theme toggle | see §10.1 | | | | | |
| Card (link) | `--border-strong` edge, `--elev-2` | — | — | — | — | — |

**No shift on selection:** where selected state adds weight 600 (nav links, sidebar items, tabs),
the label reserves the bold width (a hidden bold copy via `::after { content: attr(data-label) }`
with `visibility: hidden; height: 0`), so the text does not jump.

**Skeletons:** `--secondary` blocks; a 1.2 s opacity pulse that does not run under reduced motion.
**Empty states:** a 20 px Lucide icon in `--text-muted`, one sentence saying what is empty and why,
and the one action that fills it (a button or a copyable command); no illustrations. **Errors:**
`--destructive-subtle` panel with icon, the coded message and the fix. **Toasts:** glass-strong,
elevation 3, status icon + word, `role="status"` (or `alert` for errors), dismissible, ≥ 5 s.

### 8.3 Inventory

| Component | Surface | v2 treatment |
|---|---|---|
| Top nav | Website | `.glass-strong`, sticky, 56 px; links `--text-muted`; primary CTA `--primary`; theme toggle (§10.1) before the GitHub link |
| Mobile drawer | Website | `.glass-strong` sibling of the nav; focus trapped while open; `Esc` closes and returns focus to the button; `aria-expanded`; toggle inside |
| Hero | Website | Monochrome headline (DS5); the backdrop (§6.3) and one glass sheet; install command in an inverse terminal (§8.4) with a copy button that announces "Copied" in a live region |
| Cards | All | Opaque `--card`, `--border`, `--elev-1`, radius 12 px; link cards per §8.2; `ab-card-glow` retired at R |
| Badges | All | Neutral: `--secondary` + `--text-muted`. Status: `-subtle` fill + status text + icon + word |
| Code blocks | Docs, blog | `--code-background`, `--border`, radius 12 px; Shiki through fumadocs with a CSS-variables theme mapped to `--syntax-*` (no stock `github-*` theme), so code follows the theme and the §5.4 ratios |
| Terminal panels (hero session, install command) | Website | Inverse island (§8.4); no traffic-light dots; a 1 px top bar with the file or session label in `--text-label` |
| Tables | Docs, blog, consoles | Header `--text-muted` `--text-label` style (mono, lower case); `--border` rules; numbers `tabular-nums` |
| Docs chrome | Website | Fumadocs `--color-fd-*` mapped to brand tokens (§10.2); opaque sidebar; fumadocs' theme switch styled to match §10.1 |
| Blog prose | Website | Headings `--foreground`; links `--foreground`, underlined, offset 3 px |
| Diagrams | Website | Strokes `--control-border`, fills `--card`, one gate-line path; forced colors: `stroke: CanvasText` |
| Console header | Local console | `.glass-strong`, sticky |
| Console links | Local console | `--foreground`, underlined (not `--primary`) |
| Console status | Local console | Healthy/passed: the gate-line check (§3); other states per §8.2 badges |
| Console panels, sidebar, tables, logs | Local console | Opaque, tokens only (already) |
| Console dialogs/menus | Local console | `.glass-strong`, elevation 3/4, `::backdrop` `--scrim` |
| Cloud shell, chat, workspace cards | Cloud | Same rules; theming only in #1117 |

### 8.4 Inverse island

The hero's terminal and install command stay dark in both themes (DS12): the panel carries
`class="dark"`, which re-scopes every token inside it to the dark palette, so no new tokens are
needed. Docs code blocks follow the page theme. The island is opaque (`--code-background`), not
glass.

### 8.5 Literal colours

After W, no `#hex`, `rgb()`, `rgba()` or `oklch()` literal remains in `website/app/**/*.tsx` or
`website/components/**/*.tsx` except in `app/icon.tsx`, `components/logo.tsx` and the OG image
source. A new `tests/test_website_tokens.py` enforces it. Components use classes, not inline
`style` props, for colour (a future CSP would otherwise need `style-src-attr`).

## 9. Motion

Rules for every surface; #1115 designs the home animations within them.

1. **Reduced motion.** Under `prefers-reduced-motion: reduce` every animation shows its final state
   at once (the global clamp stays in `brand.css`; the copy in `globals.css` goes). Infinite loops
   do not run.
2. **Compositor only.** Animate `transform` and `opacity` only (and see §6.4 for ancestors of
   glass). Colour, `box-shadow` and border transitions ≤ 150 ms.
3. **No layout shift.** Reveals reserve their space; CLS ≤ 0.02 on the home page.
4. **LCP is never hidden.** The hero `<h1>` renders at full opacity on first paint; it may move from
   `translateY(4px)` but not fade from 0. `.hero-stagger` applies only after the headline.
5. **Loops pause** when off-screen (`IntersectionObserver`) and when the tab is hidden.
6. **Durations.** Micro 120–180 ms, reveals 400–600 ms, `cubic-bezier(0.16, 1, 0.3, 1)`; staggers
   ≤ 80 ms per step, ≤ 5 steps.
7. **Budget.** No animation library. Home-page JS added for motion ≤ 4 KB gzipped. Keyframes live
   in stylesheets, not inline `<style>` (the hero SVG's moves to `globals.css`).

## 10. Theme selection with no flash

### 10.1 The toggle (one design, every surface)

A three-option segmented control: **System** (monitor icon), **Light** (sun), **Dark** (moon), as a
`role="radiogroup"` with an accessible name "Theme", each option a `role="radio"` with
`aria-checked` and an explicit `aria-label` ("System", "Light", "Dark"); the tooltip is visual
only; arrow keys move between options. Each
option is 28 px visible inside a 44 px hit area on touch. The checked option uses the
tabs/segmented "selected" state (§8.2). First visit is System (DS3); with no system preference the
page is light. The choice persists per site in `localStorage` (website: next-themes' `theme` key,
shared by marketing pages and docs, which are one app; console: `console-theme`; Cloud:
next-themes' key). Placement: website nav (desktop) and drawer (mobile); fumadocs' own switch in the
docs top bar (`mode: 'light-dark-system'`), styled through the `--color-fd-*` mapping to look the
same, and included in the state sheet's axe and keyboard checks (if it fails them, W hides it and
mounts the shared radiogroup instead); console header (replacing today's menu entry); Cloud shell header. The preference never leaves
the browser: no analytics event or request on change.

### 10.2 Website (W)

- `app/layout.tsx`: drop the hard-coded `dark` class and `forcedTheme`; pass `RootProvider
  theme={{ defaultTheme: 'system', enableSystem: true, themes: ['light', 'dark'],
  disableTransitionOnChange: true }}` (`attribute: 'class'` and the rest are fumadocs' defaults).
  next-themes emits a small blocking script at the top of `<body>` (`RootProvider` sits in the
  body); it runs before first paint. `suppressHydrationWarning` stays on `<html>`. With JavaScript
  off the page renders light (`:root`). The script exists today (with `forcedTheme`); v2 unforces
  it.
- W checks what the installed next-themes 0.4.6 script does with a stored value outside `themes`
  (`localStorage.theme = "x y"`). If it adds those classes, W passes a `value` map and asserts the
  result in a test; either way a tampered value can at most add class names to `<html>`, never markup.
- `app/docs/layout.tsx`: `themeSwitch: { enabled: true, mode: 'light-dark-system' }`.
- `globals.css`: delete the legacy `:root` block and both fumadocs overrides; map **every**
  `--color-fd-*` token declared by the installed `fumadocs-ui/style.css` (background, foreground,
  card(-foreground), popover(-foreground), primary(-foreground), secondary(-foreground),
  muted(-foreground), accent(-foreground), border, ring and any others it declares) once, on `:root`,
  to the brand token of the same role. fumadocs declares them on `:root`/`.dark` earlier in the
  cascade, so the later `:root` mapping wins in both themes and `var()` follows the class on
  `<html>`. A test lists the fumadocs names from the installed package and fails on any unmapped
  one.
- During W the legacy names may exist as aliases (`--bg: var(--background)`) so components move one
  at a time; the aliases are deleted before W merges.
- Bricolage: the `next/font/google` import and every `font-display`/`--font-bricolage` use go; a
  check fails if `next/font/google` appears in `website/`.

### 10.3 Local console (L, #1116)

- New `center/static/theme.js`, a classic script listed in `<head>` **before** the stylesheet links
  (a script after a pending stylesheet waits for it). It:
  - reads `localStorage["console-theme"]` inside try/catch (storage can throw when blocked);
  - accepts only `light`, `dark`, `system`; anything else, or a throw, is `system`;
  - for `system` uses `matchMedia('(prefers-color-scheme: dark)')`;
  - sets `html.dark` and `document.documentElement.style.colorScheme`.
  It is same-origin (`script-src 'self'`), touches no HTML sink (Trusted Types unaffected), and the
  stored value is only compared, never used as a class name. The CSP is unchanged.
- `app.js` keeps `applyTheme` and the system listener. The two files cannot share a module (one is
  classic, one is a module, and no inline code is allowed), so a test asserts both accept exactly the
  same three values.
- `tests/test_center_static.py`: `theme.js` precedes `brand.css` in `index.html`, has no
  `type="module"`, contains `try`/`catch` and `matchMedia`, and contains no `innerHTML`, `eval` or
  `document.write`.

### 10.4 Cloud (C, #1117)

- Remove the hard-coded `className="dark"`; add `next-themes` with §10.2's options and the §10.1
  toggle. Check Cloud's middleware for a CSP at C; if it uses nonces, pass the nonce to
  `ThemeProvider`.
- Re-vendor `tokens.css`; `npm run tokens:check` green.
- The CI pin (§13 C) fetches `website/brand.css` over HTTPS from the public repo at a full 40-character
  commit SHA (never a branch or tag), with no token, treats it as data (parsed, never executed) and
  asserts its recorded sha256. Moving the pin is a reviewed PR.

## 11. Security and privacy

- No new third-party script, stylesheet, font or CDN on any surface. Dropping Bricolage removes the
  website's only build-time Google Fonts download. Geist stays self-hosted on all three surfaces.
  Lucide paths are inlined as static markup (no package, no CDN, no `dangerouslySetInnerHTML`).
- Scripts: next-themes' blocking inline script already ships today (with `forcedTheme`); v2 only
  unforces it. The console adds one same-origin file, `theme.js`. The stored preference is compared
  by `theme.js` against its three literals, so it cannot inject anything. For next-themes, W verifies
  the behaviour (§10.2); at worst a same-origin value adds class names to `<html>`, never markup. Neither protects against same-origin XSS, which is out of
  scope.
- The website has no CSP today, so v2 regresses nothing. A future CSP (DS7) needs a nonce for the
  next-themes script and `style-src-attr` (or no inline `style` props, §8.5); the hero's inline
  `<style>` already goes (§9.7).
- Console CSP (`center/server.py`) is unchanged; `backdrop-filter`, `@supports` and setting
  `style.colorScheme` through the CSSOM need nothing.
- Privacy: no tracker, no theme telemetry; the preference stays in the browser (§10.1).

## 12. Performance budget

| Metric (Lighthouse mobile, simulated throttling) | Target |
|---|---|
| Performance | ≥ 90 on `/`, `/docs`, `/docs/install`, both themes |
| LCP | ≤ 2.5 s |
| CLS | ≤ 0.02 on `/`, ≤ 0.05 elsewhere |
| TBT | ≤ 200 ms |
| Accessibility | 100 |
| Best practices | ≥ 95 |
| SEO | 100 |
| CSS added by v2 (gzipped), measured against a baseline taken at the start of W/L | ≤ 3 KB website, ≤ 2 KB console (the snapshot carries every token, about 0.8 KB) |
| Fonts | Bricolage removed; no new font files |

## 13. Rollout

Each step is its own PR. Each leaves `tests/test_brand_tokens.py` and the website build green; from
C on, Cloud `tokens:check` too.

| Step | Issue | Repo | Contents | Checks at merge |
|---|---|---|---|---|
| **T** tokens | own #1112 sub-issue (to file) | OSS | `brand.css` §5 values and tokens, header line, `.glass` utilities and fallbacks in `@layer utilities`, `@theme` additions; re-copy `:root`/`.dark` to `center/static/brand.css` and the glass rules to `style.css`; §5.5 tests; `docs/cross-repo-sync.md` (state table, the vendored-from commit, a "theme/utilities" row in the change table) and the OSS `CLAUDE.md` per-side entry ("Website: no visible change until W; Cloud: re-vendor at C"). Precondition: the §4.1 grep still finds no token utilities in the website | `test_brand_tokens.py`; website and console screenshots re-run: the website (forced dark, legacy vars) must look unchanged, the console's palette changes here because its copy must match. Cloud's local `tokens:check` reports drift until C, intended: changed `:root`/`.dark` values, new tokens, and `@theme` entries reported missing (`--color-brand`, `--color-glass*`, the other new `--color-*`, `--shadow-*`, `--blur-glass`, changed `--font-display`, `--text-display`) |
| **W** website | #1115 (with #1114 content) | OSS `website/` | §10.2 theme, §8 components, §8.5 literals + `test_website_tokens.py`, §9 motion fixes, §6.3 backdrop, Bricolage removal, OG/favicon/theme-color | Build, lint, §14 |
| **L** local console | #1116 | OSS | `theme.js`, glass header/menus/dialogs, links underlined, gate-line status, the toggle, §8.1 control-border test, a check that no rule uses `--primary` to mean "healthy"; `docs/command-center.md` | `test_brand_tokens.py`, `test_center_static.py`, §14 console set |
| **C** Cloud | #1117 | agentbreeder-cloud | Re-vendor `tokens.css` (with the OSS commit in its header), next-themes + toggle, Bricolage fontsource removed; an audit of `primary`, `ring`, `font-display` and `text-display` usage (all change: green → monochrome, Bricolage → Geist, a `clamp` display size); the `TODO(M1)` pin: Cloud CI fetches `website/brand.css` at the pinned OSS commit so the check runs in CI; Cloud `CLAUDE.md` note | `tokens:check` green, locally and in CI |
| **R** cleanup | #1112 | OSS, then Cloud in one paired change | Remove `ab-card-glow`, `gradient-text`, `ab-radial-glow`, `--color-ab-*` once unused. Keep `@keyframes ab-pulse` and `ab-glow-breathe` (Cloud's `BRAND_RULES` throws if they vanish), or remove them only with the `BRAND_RULES` change in the same Cloud PR | Both checks green |

Release (DS9 as amended by the owner, 2026-11-16, a split): the 3.0.0a3 packages ship now, without
v2. The website switch (W: the new look plus the #1114 content) goes live after the 3.0.0a3
public-registry run (#836), and C blocks only that switch, not the packages. T changes the
console's palette (its copy must match `brand.css`), so T merges only with the switch, not before
it: until then the T PR is marked "DO NOT MERGE until the website switch (W #1115)", and neither
the live website nor a shipped console changes. C's CI pin needs a public commit that holds T's
`brand.css` before T is on `main`: C vendors from, and pins, the T PR's head commit (a public commit of
this repo, fetchable by SHA); when T merges, a paired Cloud PR moves the pin to the `main` commit
(the file and its sha256 are unchanged, which the pin's own check confirms). **Layers (for L and
W):** a layered rule loses to any unlayered rule, whatever its specificity, so `.glass` in
`@layer utilities` does not override an element's own unlayered `background`, `border` or
`box-shadow`. L removes those declarations from the console rules it puts glass on (`header.top`,
`.menu-body`, `dialog.confirm`), and W does the same for any unlayered site CSS. T pins Tailwind's default `--shadow-*` in the
website's `globals.css`, because fumadocs uses `shadow-sm/md/lg`; W deletes the pin. W and L are
not expected to edit `brand.css`; if either does, or R does, the Cloud CI pin moves in a paired
Cloud PR (a rule T adds to `docs/cross-repo-sync.md`). T and L ship with the next console release
after 3.0.0a3.

## 14. Acceptance criteria

1. `tests/test_brand_tokens.py` passes with §5.5; every §5.4 ratio is asserted.
2. `tests/test_website_tokens.py` passes: no colour literal outside the allow-list; no
   `next/font/google`.
3. **No flash:** Playwright loads `/` and `/docs/install` with (a) `colorScheme: 'dark'` and no
   stored preference and (b) stored `theme=dark` with `colorScheme: 'light'`, and asserts at
   `DOMContentLoaded` (before hydration) that `<html>` has class `dark` and `body` has the dark
   background. The console likewise with (a) `colorScheme: 'dark'` and no `console-theme`, (b)
   `console-theme=dark`, and (c) `localStorage` throwing.
4. **Screenshots** (Playwright, committed under `docs/superpowers/verification/design-v2/`): website
   `/`, `/docs`, `/docs/install`, `/docs/manifest`, `/blog`, one blog post, `/cloud`, `/about`, the
   404 page; × light, dark; × 320, 390 and 1280 px. `/` also with reduced motion, forced colors and
   the glass fallback, and the §6.3 glass-vs-fallback diff. Console: Home, Deployments, an open
   menu, a confirm dialog, × light, dark × 320, 390, 1280. Cloud: login, the workspace and an open
   dialog, × light, dark.
5. **State sheet:** a route `/design-states` that exists only when the build-time flag
   `DESIGN_STATES=1` is set (never in the production build; robots `noindex` as a second guard)
   renders every §8.2 state in light and dark from fixed fixtures only (no real data, no query input,
   no analytics); it is screenshotted and run through axe. The console renders
   the same states in a test fixture.
6. **axe-core** on every screenshot page and the state sheet: 0 violations of any impact, and 0
   `incomplete` colour-contrast results on glass (axe cannot compute contrast through
   `backdrop-filter`, so the computed tests in §5.5 are the contrast proof and any incomplete result
   is investigated, not ignored).
7. **Lighthouse** meets §12 on `/`, `/docs` and `/docs/install`, both themes
   (`emulatedMediaFeatures`).
8. **Keyboard:** every interactive element reachable; the focus ring visible on glass and opaque
   surfaces in both themes; a Playwright script tabs through a long docs page and asserts no focused
   element is under the sticky nav; drawer and dialogs trap focus, close on `Esc` and return focus.
9. **Reflow and spacing:** 200% zoom at 1280 px and WCAG 1.4.12 text spacing on `/` and
   `/docs/install` with no loss of content.
10. **Screen reader:** a short VoiceOver pass on the toggle, the drawer and a dialog, recorded in the
    verification note.
11. JavaScript off: the site renders light, all hero content visible.
12. `docs/cross-repo-sync.md` and both `CLAUDE.md` files record T and C per side.

## 15. Risks

- **Monochrome primary loses the "go" signal** (DS1). Mitigation: the gate line marks passed and
  healthy states; primary actions differ by fill, not hue.
- **Glass reads as nothing** on flat pages. Mitigation: §6.3 backdrop on the hero, chrome-only
  elsewhere, and the glass-vs-fallback diff.
- **Safari blur cost.** Mitigated by §6.4.
- **Fumadocs token names change** between minors. Mitigated by mapping every declared name with a
  test (§10.2).
- **Cloud drift window** between T and C. Closed by making C block the website switch (DS9 as
  amended) and running the check in Cloud CI (§13).

## 16. Owner decisions

| # | Decision | What you lose | Recommendation |
|---|---|---|---|
| DS1 | Primary actions monochrome (black in light, white in dark) instead of green, on all three surfaces | The green "go" colour on buttons; green stays for passed/healthy via the gate line | Yes |
| DS2 | Drop Bricolage Grotesque; Geist for display | The only expressive face; character moves to the gate line and mono data labels | Yes |
| DS3 | Toggle offers System / Light / Dark, first visit follows the system (light when none) | — | Yes |
| DS5 | Retire the green→violet gradient headline | The hero's colour energy | Retire |
| DS6 | Cloud ships the light theme and toggle in #1117 | Dark-only simplicity | Ship in #1117 |
| DS7 | A website CSP is a separate issue, not part of W | — | Separate issue |
| DS8 | Neutral greys instead of zinc | Zinc's faint blue cast | Neutral |
| DS9 | **Amended 2026-11-16 (owner): a split.** The 3.0.0a3 packages ship now without v2; the website switch (new look plus #1114 content) goes live together after the a3 verification; C (#1117) blocks only that switch; T merges with the switch | Shipping the look earlier; v2 in the 3.0.0a3 console | Split (owner) |
| DS10 | Focus ring blue on all surfaces | — | Blue |
| DS11 | The signature device is the gate line (§3) | — | Gate line |
| DS12 | The hero terminal and install command stay dark in both themes (inverse island) | A fully light hero | Dark island |
| DS13 | OG images stay dark | — | Dark |

(DS4 was merged into DS3 in r2.)

## 17. Review log

| Round | Lens (model, mode) | Verdict and what was folded in |
|---|---|---|
| r1 | `frontend-design` (Claude Sonnet, read-only) | CHANGES REQUESTED, 5 major. Folded in: a signature device, the gate line, and the mono data voice (§3, DS11); the hero backdrop spec and a glass-vs-fallback diff (§6.3); the interaction-state matrix with non-colour selected states (§8.2); an elevation scale `--elev-1…4`, with dark lift carried by surface steps, `--glass-highlight` 10% and `--border-strong` (§5.3, §6.1); the toggle designed once for every surface (§10.1). Minors: no uppercase eyebrow, a 70ch cap and tracking checked at W (§7); radii mapped by role (§7); empty, error, loading, toast and icon rules (§7, §8.2); OG, favicon, `theme-color` and screenshot treatment (§7, DS13); the inverse terminal island (§8.4, DS12); tints mapped to meanings (§5.3); `--border-strong`, `--brand` text/stroke only (§5.3); "what you lose" per decision, DS3/DS4 merged (§16). |
| r1 | `ui-ux-pro-max` (Claude Sonnet, read-only) | CHANGES REQUESTED, 5 major. Folded in: focus not obscured, `scroll-padding-top` and a tab-through test (§8.1, §14.8); every control boundary on `--control-border`, `--input` decorative, with a test (§8.1); non-colour selected/current/on states and the colour-alone rule (§5.1, §8.2); status tokens computed on every surface, `--destructive-subtle`, the sidebar-accent rule (§5.4); `--glass` raised to 84% and `--glass-strong` to 92% so text passes over `--foreground` behind it, and text on glass limited to foreground/text-muted (§5.3, §5.4). Minors: focus as `outline`, forced-colors rules for glass and SVG (§6.2, §8.1, §8.3); the toggle as a radiogroup, `color-scheme`, `theme-color`, try/catch (§5.1, §7, §10); 24/44 px targets, safe areas, 320 px, 200% zoom, text spacing, hover-only elevation (§8.1, §14); axe `incomplete` handling, keyboard scripts, a VoiceOver pass (§14); Shiki via a CSS-variables theme on `--syntax-*` (§8.3); the state sheet (§14.5); no transitions on theme change on every surface (§8.1). |
| r1 | Frontend engineering (Claude Sonnet, read-only) | CHANGES REQUESTED, 2 major. Folded in: shadows renamed `--elev-*` and mapped to `--shadow-*` (no self-reference) with a collision test (§5.1, §5.3, §5.5); `backdrop-filter` side effects: drawer as a sibling, one hero sheet, no opacity animation on glass ancestors (§6.1, §6.4); `theme.js` with try/catch, `matchMedia`, `colorScheme`, before the stylesheets, and an agreement test (§10.3). Minors: the script is at the top of `<body>`; redundant options dropped (§10.2); every `--color-fd-*` mapped with a test (§10.2); `@layer utilities` kept on purpose (§6.2); the snapshot no-at-rule test and console budget 2 KB (§5.5, §12); the website utility precondition and screenshots at T (§4.1, §13); CSS baseline measured first (§12); reduced transparency as progressive enhancement (§6.2). |
| r1 | `/architect` (Claude Sonnet, read-only) | CHANGES REQUESTED, 5 major. Folded in: the `@theme`/palette name collision (as above); `.glass` and its fallbacks inside `@layer utilities`, the block Cloud compares, with a console-copy test (§6.2, §5.5); R keeps the keyframes Cloud's `BRAND_RULES` needs or changes the script in the same PR (§13); C is a 3.0.0a3 blocker and adds the CI pin (`TODO(M1)`) (§13, DS9); a Cloud usage audit of `primary`, `ring`, `font-display`, `text-display` at C (§13). Minors: console links underlined (§4.2, §8.3); the OSS `CLAUDE.md` entry and a cross-repo-sync row at T (§13); a `Tokens v2` header and ownership (§5.1); T screenshot re-run on the website (§13). |
| r1 | `/security` (Claude Sonnet, read-only) | APPROVED, 9 minor. Folded in: `theme.js` try/catch, `matchMedia`, the shared allow-list test (§10.3); next-themes' allow-list verified at W with an `"x y"` test (§10.2); the inline script is existing, now unforced (§11); the Bricolage grep (§10.2, §14.2); inline `style` props and a future CSP (§8.5, §11); no theme telemetry (§10.1, §11); Cloud CSP nonce check at C (§10.4). |
| r2 | `/security` | APPROVED. Minors folded in: the Cloud CI fetch pins a full SHA over HTTPS with a recorded sha256 (§10.4); `/design-states` exists only behind a build flag, with fixtures only (§14.5); Lucide as static markup (§11). |
| r2 | `frontend-design` | APPROVED. Minors folded in: glass diff by luminance plus owner sign-off, washes (not glass) strengthened if needed (§6.3); dark-mode image dimming and paired images (§7); diagram hairlines use `--border-strong`, not a literal (§3); static spinner under reduced motion (§8.2). |
| r2 | Frontend engineering | APPROVED. Minors folded in: console glass copy inside native `@layer utilities` (§6.2); next-themes stored-value behaviour verified at W, wording softened (§10.2, §11); two-level compositing for tints (§5.5); hero sheet exception and Safari measurement (§6.4); `--destructive` on its subtle tested (§5.5); T gets its own sub-issue (§13). |
| r2 | `/architect` | APPROVED. Minors folded in: pin maintenance rule and W/L not editing `brand.css` (§13); intended Cloud drift listed in the T row (§13); T's own sub-issue (§13). |
| r2 | `ui-ux-pro-max` | APPROVED (re-ran the §5.4 glass minima, within 0.04). Minors folded in: `theme-color` follows the toggle (§7); reserved bold width (§8.2); explicit `aria-label`s, tooltip visual only, fumadocs switch in the state checks (§10.1); `--brand` text only on opaque surfaces (§5.4); `color-scheme` and the parsers (§5.1); console at 320 px (§14.4); stray reference removed (§8.2). |
| r3 | Narrow check, all lenses + docs-exception gate (Claude Sonnet, read-only) | APPROVED. Every r2 row verified in its section, no contradictions; no secrets, private or marketing content; claims honest; changelog lint OK. Minor folded in: §5.5 wording on `--foreground` as a backdrop; this row. |
| T (#1119) | Implementation review (Claude Sonnet, read-only; `frontend-design`, `ui-ux-pro-max`, frontend engineering) | r1 CHANGES REQUESTED, 3 major. Folded in: T merges with the website switch, not before, and C blocks only the switch (DS9 amended; §13, §15, `CLAUDE.md`, `docs/cross-repo-sync.md`); C pins the T PR head commit until T merges (§13); a layered `.glass` loses to unlayered rules, so L and W strip the elements' own background, border and shadow (§6.2, §13). Minors: `--sidebar` contrast pairs, a single console layer. r2 APPROVED. |
| L (#1116) | Implementation review (Claude Sonnet, read-only; `frontend-design` + `ui-ux-pro-max`, and frontend engineering + `/security`) | r1 CHANGES REQUESTED. Design: 3 major. Folded in: 320 px reflow (absolute `.visually-hidden` cell text widened the page; table scrollers are positioned, with a test); table action cells; the forced-colours selected state on the toggle. Engineering: 1 major. Folded in: the builder's sticky preview slid under the sticky header (`--sticky-top`). Minors: the header is sticky only from 1100 px, where it fits one row (WCAG 2.4.11); first-paint test with `app.js` blocked; grouped-selector glass check; `theme.js` sets `theme-color`; Home/End; 44 px touch test; nested menu glass documented (header is a backdrop root). r2 APPROVED (both). |
