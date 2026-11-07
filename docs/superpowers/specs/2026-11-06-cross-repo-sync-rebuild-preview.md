# Cross-repo sync after the focused rebuild (#701 follow-up)

Date: 2026-11-06 · Issue: #712 · Epic: #701 · Tracking: #648

## Problem

#709 removed the legacy implementation and #708 merged the focused rebuild (manifest v2, identity
gate, opt-in MCP egress sidecar). Three things are now out of sync:

1. **Design system source deleted.** `dashboard/src/styles/brand.css` was the OSS source of truth
   for the brand (#583). Cloud's `dashboard/scripts/check-token-drift.mjs` reads it from a sibling
   checkout. The file is gone, so the check silently skips (exit 0) and the parity guard is off.
2. **Website docs describe only 2.x.** All 42 pages under `website/content/docs/` document the
   legacy product. That is still what PyPI installs (`agentbreeder` 2.7.3), so they are accurate
   for `pip install`. The rebuild has no website presence, though, and can only be installed from
   source (`0.0.0.dev0`).
3. **Guides are stale.** Cloud `CLAUDE.md` describes OSS as "engine, SDK, CLI, registry" and
   points at the deleted `brand.css`. This repo's `docs/cross-repo-sync.md` and `CLAUDE.md` also
   point at the deleted path.

Cloud code does not import any OSS module, read `agent.yaml` or call the CLI, so no Cloud code or
API change is needed.

## Decisions (user, 2026-11-06)

- **Website:** keep the 2.x docs. Add a clearly labelled "Focused rebuild (preview)" docs section
  plus a docs banner. No homepage, pricing or marketing copy changes.
- **brand.css:** the source of truth moves to `website/brand.css`.

## Scope

### OSS (this repo)

- `website/brand.css`: the deleted file restored byte-for-byte in its token blocks, with an
  updated header comment naming the new location and consumers.
  - **Not imported by the website in this change.** Its `@theme inline` block hard-codes
    `--font-sans`/`--font-display` family names (bypassing `next/font`) and redefines radii and
    shadcn colour utilities. Importing it needs a visual review, so that is tracked as a follow-up.
- `website/content/docs/preview/`: four pages (overview and install from source, manifest, local
  deployment, identity gate and runtime enforcement), derived from `README.md`, `docs/manifest.md`,
  `docs/local-deployment.md`, `docs/first-deployment.md` and `docs/identity-gate.md`. Each page links
  to the canonical file on GitHub.
- `website/content/docs/meta.json`: a "Focused rebuild (preview)" section.
- `website/app/docs/layout.tsx`: a dismissible banner saying these docs cover 2.x and linking to
  the preview section.
- `docs/cross-repo-sync.md` and `CLAUDE.md`: new `brand.css` location; current cross-repo state.

### Cloud (`agentbreeder-cloud`, companion change)

- `dashboard/scripts/check-token-drift.mjs`: add `../../../agentbreeder/website/brand.css` as the
  first sibling candidate, keep the old path as a fallback for older checkouts, and update comments.
- `CLAUDE.md`: describe OSS as it is now (manifest, local lifecycle, identity gate, opt-in MCP
  egress sidecar; legacy 2.x in history and on PyPI) and the new `brand.css` path.
- Done in a separate worktree off Cloud `main`, so the in-progress
  `codex/enterprise-assistants-foundation` branch and its uncommitted files are untouched.

## Out of scope

- Rewriting or removing the 2.x docs, homepage, Cloud page or marketing copy (after the rebuild
  ships to PyPI).
- Importing `brand.css` into the website (needs a visual review).
- Version badge: the footer stays `v2.6.0`, matching the 2.x docs. (Bumping it to 2.7.3 is a
  separate 2.x fix.)

## Claims rules for the preview pages

- Never claim tamper-evidence. The audit chain detects corruption; a database owner can recompute it.
- Runtime enforcement covers only MCP Streamable HTTP through the opt-in sidecar. STDIO and
  in-process tools are never mediated.
- Do not claim production deployment or an operational UI. Local Docker only; macOS Docker Desktop
  verified; Linux socket path not verified on a Linux host; Entra/Okta/AWS fixture-tested only.
- The 2.x "sidecar" page describes a different component (the Go sidecar). The preview page names
  its component "MCP egress sidecar" to avoid confusion.

## Acceptance criteria

- `npm run build` and `npm run lint` pass in `website/`. The preview pages render, and existing
  pages still return 200.
- The banner shows on docs pages and links to `/docs/preview`.
- With both repos checked out side by side, Cloud's `npm run tokens:check` finds
  `website/brand.css` and passes (no skip).
- No public text claims tamper-evidence, production deployment or mediation of STDIO/in-process tools.
- No marketing or strategy content in tracked paths.
