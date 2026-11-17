# Cross-repo sync

AgentBreeder spans this repo (OSS, including `website/`) and `agentbreeder-cloud`. The rules in
`CLAUDE.md` apply. This page records the current state of each dependency after the focused rebuild.

## Current state (2026-11-11)

| Surface | State |
|---|---|
| PyPI `agentbreeder` | `3.0.0a2`, the focused rebuild as a pre-release (released 2026-11-11; `3.0.0a1` on 2026-11-10). A bare install still gets 2.7.3, the legacy 2.x release, because installers skip pre-releases unless pinned |
| npm `agentbreeder` | `3.0.0-alpha.2` (tags `latest`, `next`), a launcher that runs the same PyPI release through `uvx` |
| Website version badge | `3.0.0a2` (`website/components/footer.tsx`) |
| `website/content/docs/` | Documents the focused rebuild (since 2026-11-07). 2.x is unmaintained and summarised in `legacy-2x.mdx`; its docs live at the `v2.7.3` tag. Old `/docs/preview/*` and 2.x URLs redirect (`website/next.config.mjs`) |
| `agentbreeder-cloud` code | Imports no OSS module and doesn't read the manifest or call the CLI. A rebuild manifest change needs no Cloud code change today |
| Design system | `website/brand.css` (moved from `dashboard/src/styles/brand.css` when the dashboard was removed). Tokens v2 (#1113), step T (#1119): merges together with the website switch (#1115), not before, because the console's palette changes with it; Cloud re-vendors at C (#1117), which blocks the switch |

## Design system (`brand.css`, #583)

`website/brand.css` is the single source of truth for the brand: the light `:root` and dark
`.dark` palettes (the light one since #817), the Tailwind v4 `@theme` mapping, keyframes and
utilities. Changes flow **OSS → Cloud**, never the reverse. `.dark` follows `:root` with the same
specificity, so a page with `class="dark"` on `<html>` gets the dark palette. With tokens v2
(#1113, step T #1119) the file starts with a `Tokens v2 (#1113)` header line, `:root` is a
light-first monochrome palette (neutral greys, a near-black primary, a blue `--ring`, green only in
`--brand` and `--success`), and `@layer utilities` holds the `.glass`/`.glass-strong` utilities
and their three fallbacks (spec `docs/superpowers/specs/2026-11-16-design-system-v2.md`).

- **Cloud** vendors a snapshot at `dashboard/app/tokens.css` and drift-checks it (`:root`, `.dark`
  and `@theme`) against this file with `npm run tokens:check`
  (`dashboard/scripts/check-token-drift.mjs`) when both repos are checked out side by side.
  The `tokens.css` header mirrors the `Tokens v2` line and names the OSS commit it vendored.
  **CI pin (planned at C, #1117; not in place yet):** Cloud CI will fetch `website/brand.css` over HTTPS from the public repo
  at a full 40-character commit SHA (never a branch or tag), with no token, treat it as data
  (parsed, never executed) and assert its recorded sha256. Any OSS PR that edits `brand.css`
  (W, L or R included) needs a paired Cloud PR that moves the pin and re-vendors; moving the pin
  is always a reviewed PR.
- **Website** imports `brand.css` from `app/globals.css` and overrides the font families and radii
  in its own `@theme inline` block; until its v2 switch (#1115) it also pins Tailwind's default
  `--shadow-xs…lg` there, because fumadocs uses `shadow-sm/md/lg` and `brand.css` now maps them to
  `--elev-*`. It renders forced dark (`app/layout.tsx`). Its own later `:root` block in
  `globals.css` overrides `--border`, `--accent` and `--text-muted`, so the site will not follow
  `brand.css` changes to those three tokens until #1115 deletes that block.
- **Local console** (`agentbreeder up`) ships a copy of the `:root` and `.dark` blocks at
  `src/agentbreeder/center/static/brand.css`, with vendored Geist and Geist Mono woff2 files under
  `center/static/fonts/`, and a copy of the `.glass` rules in `center/static/style.css` (in a
  native `@layer utilities`). `tests/test_brand_tokens.py` fails on drift of either copy, checks
  WCAG contrast (AA for text and status tokens, 3:1 for `--control-border` and `--ring`, and both
  on `--glass`/`--glass-strong` over every backdrop) and pins the font hashes. Copying the blocks
  changes the console's palette at once; its v2 restyle is #1116.

With tokens v2 the dark `--muted-foreground` passes AA (`#a3a3a3`); the legacy 3.36:1 exception
is gone.

Edit `brand.css` here first, then re-vendor in Cloud and let the drift check confirm parity.

## When the rebuild changes

| Change | OSS | Cloud | Website |
|---|---|---|---|
| Manifest field or CLI flag | `docs/manifest.md`, `docs/manifest.schema.json`, tests | Search for the field; today nothing consumes it | Update `website/content/docs/manifest.mdx` |
| Identity gate or runtime enforcement | `docs/identity-gate.md` | Note in Cloud `CLAUDE.md` if Cloud should expose it | Update `website/content/docs/identity-gate.mdx` (and `what-ships.tsx` if a capability changes) |
| New rebuild release (PyPI/npm) | `CHANGELOG.md`; the pins in `README.md`, `npm/README.md` and the Northwind README; `scripts/sync_npm.py --check` | Grep for version pins; companion PR if needed; note it in Cloud `CLAUDE.md` | Footer badge in `website/components/footer.tsx` shows the exact released version; pins and verified scope in `website/content/docs/install.mdx`; the npx pin in `website/components/hero.tsx`; `what-ships.tsx` |
| `brand.css` tokens | Edit `website/brand.css`; re-copy the `:root` and `.dark` blocks to `center/static/brand.css` | Re-vendor `tokens.css` (header names the OSS commit); run `tokens:check`; move the CI pin in a paired PR | Check home and docs look unchanged (forced dark until #1115) |
| `brand.css` theme or utilities (`@theme`, `@layer utilities`, keyframes) | Edit `website/brand.css`; re-copy the `.glass` rules to `center/static/style.css` if they change | Re-vendor `tokens.css`; `tokens:check` compares `@theme` per token and the utilities and keyframes verbatim; move the CI pin in a paired PR. Keep `ab-pulse`/`ab-glow-breathe` unless the same Cloud PR changes `BRAND_RULES` | A new `@theme` entry in a Tailwind namespace (`--shadow-*`, `--text-*`, `--font-*`) changes utilities the site or fumadocs already use: pin the old value in `globals.css` or check the rendered pages |

The release rows may claim only what has been run from the published packages. As of `3.0.0a2`
that is macOS end to end, including `agentbreeder up` with Northwind on local Docker and `kind`
(#783), and, on Linux, install, `--version` and `doctor` only, on `3.0.0a1` (#754).

The legacy website and Cloud interfaces are not compatible with the focused development baseline.
Before a rebuild release, inspect `agentbreeder-cloud` consumers and provide explicit
compatibility, versioning and migration handling. Do not publish private strategy content.
