# Cross-repo sync

AgentBreeder spans this repo (OSS, including `website/`) and `agentbreeder-cloud`. The rules in
`CLAUDE.md` apply. This page records the current state of each dependency after the focused rebuild.

## Current state (2026-11-10)

| Surface | State |
|---|---|
| PyPI `agentbreeder` | `3.0.0a1`, the focused rebuild as a pre-release (released 2026-11-10). A bare install still gets 2.7.3, the legacy 2.x release, because installers skip pre-releases unless pinned |
| npm `agentbreeder` | `3.0.0-alpha.1` (tags `latest`, `next`), a launcher that runs the same PyPI release through `uvx` |
| Website version badge | `3.0.0a1` (`website/components/footer.tsx`) |
| `website/content/docs/` | Documents the focused rebuild (since 2026-11-07). 2.x is unmaintained and summarised in `legacy-2x.mdx`; its docs live at the `v2.7.3` tag. Old `/docs/preview/*` and 2.x URLs redirect (`website/next.config.mjs`) |
| `agentbreeder-cloud` code | Imports no OSS module and doesn't read the manifest or call the CLI. A rebuild manifest change needs no Cloud code change today |
| Design system | `website/brand.css` (moved from `dashboard/src/styles/brand.css` when the dashboard was removed) |

## Design system (`brand.css`, #583)

`website/brand.css` is the single source of truth for the brand: the `.dark` palette, the Tailwind
v4 `@theme` mapping, keyframes and utilities. Changes flow **OSS → Cloud**, never the reverse.

- **Cloud** vendors a snapshot at `dashboard/app/tokens.css` and drift-checks it against this file
  with `npm run tokens:check` (`dashboard/scripts/check-token-drift.mjs`) when both repos are
  checked out side by side.
- **Website** does not import `brand.css` yet. Its `@theme inline` block hard-codes font family
  names and redefines radii and colour utilities, which would override the site's `next/font`
  families. Adopting it needs a visual review.

Edit `brand.css` here first, then re-vendor in Cloud and let the drift check confirm parity.

## When the rebuild changes

| Change | OSS | Cloud | Website |
|---|---|---|---|
| Manifest field or CLI flag | `docs/manifest.md`, `docs/manifest.schema.json`, tests | Search for the field; today nothing consumes it | Update `website/content/docs/manifest.mdx` |
| Identity gate or runtime enforcement | `docs/identity-gate.md` | Note in Cloud `CLAUDE.md` if Cloud should expose it | Update `website/content/docs/identity-gate.mdx` (and `what-ships.tsx` if a capability changes) |
| New rebuild release (PyPI/npm) | `CHANGELOG.md`; the pins in `README.md`, `npm/README.md` and the Northwind README; `scripts/sync_npm.py --check` | Grep for version pins; companion PR if needed; note it in Cloud `CLAUDE.md` | Footer badge in `website/components/footer.tsx` shows the exact released version; pins and verified scope in `website/content/docs/install.mdx`; the npx pin in `website/components/hero.tsx`; `what-ships.tsx` |
| `brand.css` | Edit `website/brand.css` | Re-vendor `tokens.css`; run `tokens:check` | Adopt once visually reviewed |

The release rows may claim only what has been run from the published packages. As of `3.0.0a1`
that is macOS end to end and, on Linux, install, `--version` and `doctor` only (#754).

The legacy website and Cloud interfaces are not compatible with the focused development baseline.
Before a rebuild release, inspect `agentbreeder-cloud` consumers and provide explicit
compatibility, versioning and migration handling. Do not publish private strategy content.
