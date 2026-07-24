> Extracted from CLAUDE.md (thin-router refactor). Read on demand — see CLAUDE.md router for when.

### Documenting a cross-repo feature (bidirectional)

When you add or change a feature, record what must change on **each** side, in both directions:

| Direction | Trigger | What to update |
|---|---|---|
| **OSS → Cloud** | New engine/CLI/connector/schema capability lands here | Note in Cloud `CLAUDE.md` how Cloud should expose/gate it; file a Cloud issue; update Cloud ROADMAP sequencing |
| **Cloud → OSS** | Cloud needs a capability that belongs in the core (per OSS-first policy) | File an OSS issue here first; implement in OSS; Cloud consumes it as a dependency — never fork logic into Cloud |
| **Either → Website** | User-facing behaviour, naming, or pricing changes | Update `website/` content + keep terminology identical across OSS, Cloud, and site |

> The cloud project uses `agentbreeder` packages as its deploy infrastructure.
> A silent break in OSS will silently break Cloud. The website going stale misrepresents the product.

### Design system source of truth (#583)

The AgentBreeder visual system lives in **`dashboard/src/styles/brand.css`** — the dark
palette, the Tailwind v4 `@theme` mapping, brand keyframes (`ab-pulse`,
`ab-glow-breathe`), and brand utilities (`gradient-text`, `ab-radial-glow`,
`ab-card-glow`, `ab-status-dot`). `dashboard/src/index.css` `@import`s it after the
Tailwind/shadcn/font imports; `@custom-variant dark` and `@layer base` stay in
`index.css` (they are build directives, not portable tokens).

**This file is the single source of truth for the brand, consumed OSS → Cloud → Website:**
- **Cloud** (`agentbreeder-cloud/dashboard/app/tokens.css`) vendors a snapshot of the
  portable slice and drift-checks the full brand surface — the `.dark` palette **and**
  the `@theme` mapping — against `brand.css` via `npm run tokens:check`. It runs on every
  local build **where the OSS checkout is present as a sibling**; in Cloud CI (no OSS
  checkout) it skips with a notice (a TODO tracks pinning the OSS ref into CI). Changes
  flow **OSS → Cloud, never the reverse** — edit `brand.css` here, then re-vendor into Cloud.
- Keep the palette **oklch-exact** with `agentbreeder.io`. If you change a token, the
  Cloud drift-guard will fail until Cloud re-vendors — that's intentional.
- **shadcn caveat:** `dashboard/components.json → tailwind.css` still points at
  `src/index.css` (that's where `@layer base` lives, which shadcn expects). So a
  `shadcn add` that injects CSS variables will write a `.dark` / `@theme` block into
  `index.css`, **not** `brand.css`. When that happens, **hand-move the generated theme
  tokens into `brand.css`** so the palette stays in one place and the Cloud drift-guard
  keeps working — never leave a second `.dark` block in `index.css`.
- A published npm package can wrap this same file as a follow-up; the file is already
  the portable, build-agnostic layer, so the physical location is the only thing that
  changes when it graduates to a package.

---
