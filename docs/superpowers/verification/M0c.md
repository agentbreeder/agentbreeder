# M0c verification: brand tokens (#817)

Status: **branch run** (`feat/817-brand-tokens` off `main` f7b521f, 2026-11-11, macOS). The
post-merge run from a wheel built from `main` is still to do; repeat the "Wheel" section then.

## What changed

- `website/brand.css`: a light `:root` palette (same token names as `.dark`), and in both palettes
  `--text-muted`, `--control-border`, `--success`, `--warning`, `--info` and their `-subtle`
  backgrounds, mapped in `@theme` as `--color-control-border`, `--color-success`, … No existing
  token value changed.
- `src/agentbreeder/center/static/brand.css`: a copy of the `:root` and `.dark` blocks (no Tailwind
  syntax, no external URLs). Not served or used by the console yet (lane B, M0a).
- `src/agentbreeder/center/static/fonts/`: `Geist-Variable.woff2` (sha256 `e24cec10…1505`),
  `GeistMono-Variable.woff2` (sha256 `5f687a5d…8c87`) and `OFL.txt`, from the npm package
  `geist@1.7.0` (the version the website's lockfile resolves; tarball sha512 matches the registry's
  `dist.integrity`; licence "SIL OPEN FONT LICENSE", © 2023 Vercel with basement.studio).
  Shipped as package data (`center/static/fonts/*`).
- `tests/test_brand_tokens.py`: drift (snapshot vs source, both palettes), the snapshot is
  self-contained (exactly one `:root` and one `.dark` rule), WCAG contrast for every text pair
  (≥ 4.5, except dark `--muted-foreground`, pinned at 3.36) and for `--control-border` and `--ring`
  on every surface (≥ 3), font hashes and licence.
- This record, `docs/superpowers/verification/M0c.md`, is part of the change set.
- Cloud companion (`agentbreeder-cloud`, branch `feat/817-brand-tokens`): `dashboard/app/tokens.css`
  re-vendored; `check-token-drift.mjs` now also compares the `:root` palette (keyed `:root <name>`).

## Contrast (computed by the test from the token values)

oklch values are converted to sRGB; translucent colours are composited over the surface. The dark
surfaces are the real token values (`--background` `oklch(0.085 0 0)` ≈ #020202, `--card` ≈ #040404,
`--secondary` ≈ #0c0c0c), which are darker than the hex comments in `brand.css` suggest.

| Pair | Light | Dark |
|---|---|---|
| `--foreground` / `--background` | 16.97 | 16.07 |
| `--primary-foreground` / `--primary` | 5.02 | 9.12 |
| `--destructive` / `--background` | 6.20 | 7.17 |
| `--text-muted` / `--background`, `--card`, `--secondary` | 7.41, 7.73, 7.03 | 8.09, 7.98, 7.63 |
| `--success` / `--background`, `--secondary`, `--success-subtle` | 4.81, 4.56, 4.79 | 11.91, 11.22, 10.12 |
| `--warning` / `--background`, `--secondary`, `--warning-subtle` | 4.81, 4.57, 4.84 | 12.43, 11.71, 10.48 |
| `--info` / `--background`, `--secondary`, `--info-subtle` | 6.42, 6.10, 6.16 | 8.16, 7.69, 7.18 |
| `--muted-foreground` / `--background` | 7.41 | **3.36** (unchanged, see below) |
| `--control-border` / `--background`, `--card`, `--secondary` (3:1) | 4.63, 4.83, 4.40 | 4.29, 4.23, 4.04 |
| `--ring` / `--background`, `--secondary` (3:1) | 4.81, 4.56 | 9.12, 8.60 |

The dark `--muted-foreground` fails AA and is kept so the website and Cloud look unchanged; the test
pins it as the one known exception and new UI uses `--text-muted`.

## Website unchanged in dark

`next build` + `next start` before (tokens from `main`) and after, Playwright 1.58.2 (Chromium
1208), 1280×900, full page, `colorScheme: dark`, reduced motion, animations off; pixelmatch at
threshold 0 on `/`, `/docs`, `/docs/install`, `/docs/your-own-agent`.

| Page | Size | Diff pixels |
|---|---|---|
| `/` | 1280×4532 | 0 |
| `/docs` | 1280×2595 | 0 |
| `/docs/install` | 1280×5641 | 0 or 373 |
| `/docs/your-own-agent` | 1280×5484 | 0 or 324 |

Every non-zero diff is the same box (x 1013–1181, y 148–180): the "On this page" scroll-spy
highlight, which flips between runs of the **same** build (before vs before: 324 on
`your-own-agent`; after vs after: 373 on `install`). Computed `--background`, `--foreground`,
`--muted-foreground`, `--border`, `--text-muted` and `--bg` on `<html>` are identical before and
after on all four pages.

Console snapshot (manual check, not a test): a swatch page using only `center/static/brand.css`
and the vendored fonts, rendered light and dark with Playwright and inspected by eye; both fonts
loaded from the local files. Screenshots (not committed):
`/private/tmp/claude-501/-Users-rajit-personal-github-agentbreeder/168e3980-c9ff-4b98-9c13-476fd610fcde/scratchpad/shots/console-snapshot-light.png`
and `…/console-snapshot-dark.png` in the same folder.

## Gate (branch)

- `pytest` (worktree venv, editable install): 1819 passed, 32 skipped; coverage 97%.
- `AGENTBREEDER_PACKAGING_TEST=1 pytest tests/test_packaging.py`: 8 passed (wheel and sdist
  allowlists include `brand.css` and the fonts).
- `ruff check` / `ruff format --check` on src tests scripts: clean.
- `python -m build`: sdist and wheel built. `scripts/sync_npm.py --check` and
  `scripts/content_manifest.py --check`: pass.
- Website: `npx tsc --noEmit` clean, `npm run build` ok, `npm audit --omit=dev` 0 vulnerabilities.
- Cloud: `tokens:check` against the new OSS file: 140 tokens match; fails as expected against
  `main`'s `brand.css` (extra `:root` tokens), on a changed `:root` value, and with the old
  vendored file (missing tokens). `npm ci && npm run build` (prebuild runs the check): ok.

## Wheel (clean sandbox)

Fresh `HOME` and uv cache, Python 3.12 venv, wheel from the branch: installed in 3 s;
`agentbreeder --version` 3.0.0a2; installed `center/static/brand.css` and both fonts match the
checkout's sha256. No containers or services started, so no teardown; no secrets read.

## Review

Author: Claude Opus 5.5. Reviewer: Claude Fable 5.2 (different model): **APPROVED** with one
should-fix and seven nits. Each was checked against the code before applying; all eight were valid
and applied, none rejected.

| # | Finding | Outcome |
|---|---|---|
| 1 (should-fix) | Test comment gave dark `--muted-foreground` as #71717a, 4.1:1 | Applied: now oklch(0.493 0 0), 3.36:1, checked in light only, pointing to the exception test |
| 2 | Pin the exception's ratio; say so in the CHANGELOG | Applied: `pytest.approx(3.36, abs=0.01)`; CHANGELOG clause added |
| 3 | Cloud drift-script comment wrong about pre-#817 OSS (it fails as "extra tokens") | Applied: comment corrected; prints "OSS checkout predates agentbreeder#817 … update it"; throws if the vendored file has no `:root` |
| 4 | `_block` reads only the first rule | Applied: the snapshot must hold exactly one `:root` and one `.dark` rule (two `{`, two `}`) |
| 5 | `-subtle` comments said "over the background" but were composited over #09090b | Applied in all three copies: "over #09090b (the website's --bg)"; older stale hex comments left as they were |
| 6 | The website's later `:root` in `globals.css` overrides `--border`, `--accent`, `--text-muted` | Applied: one sentence in `docs/cross-repo-sync.md` |
| 7 | This record: list itself as part of the change; note the exception; mark the swatch check as manual | Applied |
| 8 | Light status/ring colours on `--sidebar-accent` are 3.95:1 | Applied: `--text-muted` is also checked on `--sidebar-accent` (6.09:1 light); a comment in all three copies says status text is not for `--sidebar-accent` |

The `3.4:1` ratio in the `.dark` comment and in `docs/cross-repo-sync.md` was also corrected to
3.36:1.

Re-run after the fixes: `tests/test_brand_tokens.py` 12 passed; packaging test 8 passed;
`ruff check` and `ruff format --check` clean. Cloud `tokens:check`: 140 tokens match. Its negative
cases fail as intended: pre-#817 OSS `brand.css` ("predates agentbreeder#817" plus extra tokens), a
changed `:root` value, and the pre-#817 vendored file (throws "No :root block found").

## Merge order

OSS first. Cloud's drift check reads the sibling `agentbreeder/website/brand.css`, so the Cloud PR
passes locally only once the OSS change is on the checked-out OSS branch (CI skips the check).
