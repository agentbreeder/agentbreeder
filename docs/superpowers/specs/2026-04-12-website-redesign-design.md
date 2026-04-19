# AgentBreeder Website Redesign — Design Spec

**Date:** 2026-04-12  
**Status:** Approved  
**Domain:** agentbreeder.io

---

## Overview

Replace the current MkDocs static site with a professional Next.js website using Fumadocs — the same stack powering mastra.ai. The site combines a marketing landing page at `/` with full documentation at `/docs`, deployed to Vercel with the custom domain `agentbreeder.io`.

---

## Goals

- First impression that converts visitors from Hacker News / Twitter into GitHub stars and installs
- Documentation as clean and navigable as mastra.ai
- Single deploy pipeline: push to `main` → live on Vercel in under 2 minutes
- All existing docs content preserved, zero content loss

---

## Architecture

### Repository structure

```
agentbreeder/
└── website/                        ← new Next.js 14 app (App Router)
    ├── app/
    │   ├── layout.tsx              ← root layout, Geist font, dark theme
    │   ├── page.tsx                ← landing page (agentbreeder.io)
    │   ├── globals.css
    │   └── docs/
    │       └── [[...slug]]/
    │           └── page.tsx        ← all doc pages (agentbreeder.io/docs/*)
    ├── components/
    │   ├── nav.tsx                 ← top navigation bar
    │   ├── hero.tsx                ← landing hero section
    │   ├── features.tsx            ← features grid
    │   ├── frameworks.tsx          ← framework compatibility strip
    │   ├── footer.tsx              ← site footer
    │   └── logo.tsx                ← SVG logo component (hexagon B)
    ├── content/
    │   └── docs/                   ← MDX content (migrated from /docs/*.md)
    │       ├── index.mdx
    │       ├── quickstart.mdx
    │       ├── how-to.mdx
    │       ├── agent-yaml.mdx
    │       ├── cli-reference.mdx
    │       ├── orchestration-sdk.mdx
    │       ├── registry-guide.mdx
    │       ├── local-development.mdx
    │       ├── api-stability.mdx
    │       └── migrations/
    │           ├── overview.mdx
    │           ├── from-langgraph.mdx
    │           ├── from-crewai.mdx
    │           ├── from-openai-agents.mdx
    │           ├── from-autogen.mdx
    │           └── from-custom.mdx
    ├── lib/
    │   └── source.ts               ← Fumadocs content source config
    ├── source.config.ts            ← Fumadocs MDX config
    ├── next.config.mjs
    ├── tailwind.config.ts
    ├── tsconfig.json
    └── package.json
```

### Tech stack

| Layer | Technology |
|---|---|
| Framework | Next.js 14 (App Router) |
| Docs engine | Fumadocs Core + Fumadocs UI |
| Styling | Tailwind CSS v3 |
| Font | Geist Sans + Geist Mono (Vercel) |
| Content | MDX via Fumadocs |
| Search | Fumadocs built-in (Orama, client-side) |
| Deploy | Vercel |
| CI trigger | GitHub Actions → `vercel --prod` |

---

## Design Tokens

```css
--bg:           #09090b   /* near-black background */
--bg-surface:   #111113   /* card / sidebar background */
--bg-elevated:  #1a1a1e   /* hover states */
--border:       rgba(255,255,255,0.07)
--border-hover: rgba(255,255,255,0.14)
--text:         #e4e4e7
--text-muted:   #71717a
--text-dim:     #3f3f46
--accent:       #22c55e   /* green — primary brand color */
--accent-dim:   rgba(34,197,94,0.10)
--accent-border:rgba(34,197,94,0.22)
```

---

## Landing Page (`/`)

### 1. Navigation bar (sticky, blur backdrop)

- Left: Logo SVG + "AgentBreeder" wordmark
- Center: Docs · Examples · Blog (links)
- Right: GitHub star count badge · `Get Started →` button (green, solid)
- Height: 56px, `backdrop-filter: blur(12px)`, background `rgba(9,9,11,0.85)`

### 2. Hero section

**Layout:** Two-column split, full viewport height minus nav.

**Left column:**
- Tag line: `// open source · apache 2.0` in JetBrains Mono, muted
- Headline (3 lines, 52px, weight 900, letter-spacing -2px):
  ```
  Build agents.
  Deploy anywhere.
  Govern free.
  ```
  "Deploy anywhere." rendered with green-to-purple gradient (`linear-gradient(90deg, #22c55e, #a78bfa)`)
- Subtext (16px muted): "One YAML file. Any framework. Any cloud. Governance, RBAC, cost tracking and audit trail — automatic on every deploy."
- Install command box:
  ```
  $ pip install agentbreeder
  ```
  Dark surface, mono font, copy-to-clipboard button
- Two CTAs: `Read the docs →` (green solid) · `★ Star on GitHub` (ghost)

**Right column:**
- `agent.yaml` code preview card
- Header: filename `agent.yaml` · badge `● ready to deploy` (green)
- Syntax-highlighted YAML (keys in blue, values in green, numbers in orange, comments in dim)
- Shows: name, version, framework, model (primary + temperature), deploy (cloud + runtime)
- Card has subtle green glow: `box-shadow: 0 0 60px rgba(34,197,94,0.06)`

### 3. Framework compatibility strip

Full-width strip with `Works with every major framework` label, then pill badges:
`LangGraph` · `CrewAI` · `Claude SDK` · `Google ADK` · `OpenAI Agents` · `Custom`

### 4. Features grid (2×3)

Six cards on dark surface, subtle border, hover lifts slightly:

| Icon | Title | Description |
|---|---|---|
| 🔌 | Framework Agnostic | LangGraph, CrewAI, Claude SDK, Google ADK, OpenAI Agents. One pipeline for all. |
| ☁️ | Multi-Cloud | GCP Cloud Run and local Docker Compose today. AWS ECS planned. |
| 🔒 | Auto Governance | RBAC, cost attribution, audit trail, registry registration on every deploy. |
| 🗂️ | Shared Registry | Agents, prompts, tools, MCP servers, models — one org-wide catalog. |
| 🎯 | Three Builder Tiers | No Code → Low Code → Full Code. Start visual, eject to YAML, eject to SDK. |
| 🔗 | Multi-Agent Orchestration | 6 orchestration strategies via YAML or Python/TS SDK. |

### 5. Footer

Logo + tagline · navigation columns (Docs, GitHub, PyPI, Docker Hub, Homebrew) · Apache 2.0 · copyright line

---

## Docs (`/docs`)

### Layout

- Fumadocs `DocsLayout` with sidebar
- Top nav shared with landing page (same component)
- Sidebar: fixed left, 260px, sections with uppercase labels matching current MkDocs nav structure
- Content area: max-width 720px, centered
- TOC: fixed right, 180px, current section highlighted in green
- Page footer: Prev / Next navigation links

### Sidebar structure

```
Getting Started
  Overview
  Quickstart
  How-To Guide
  Local Development

Core Concepts
  agent.yaml Reference
  Registry Guide
  API Stability

Frameworks
  LangGraph
  CrewAI
  Claude SDK
  Google ADK
  OpenAI Agents

Reference
  CLI Reference
  orchestration.yaml
  Orchestration SDK

Migrations
  From LangGraph
  From CrewAI
  From OpenAI Agents
  From AutoGen
  From Custom
```

### Features

- `⌘K` search modal (Fumadocs Orama, client-side, no external service)
- Syntax-highlighted code blocks with copy button (Shiki)
- Tabbed code blocks for multi-language examples
- Callout components: Note, Warning, Tip (mapped from MkDocs admonitions)
- Mermaid diagram support
- Breadcrumb navigation

### Content migration

All existing `/docs/*.md` files migrated to `/website/content/docs/*.mdx`:
- Frontmatter converted from MkDocs format to Fumadocs format (`title`, `description`)
- MkDocs admonitions (`!!! note`) converted to Fumadocs `<Callout>` components
- MkDocs tabbed content (`=== "tab"`) converted to Fumadocs `<Tabs>` components
- All internal links updated to new path structure
- No content changes — copy only, no rewrites

---

## Logo

**Concept B — Hexagon Node Network**

SVG icon: dark navy background (`#0a0f1e`), hexagon outline in green (`#22c55e`, 60% opacity), central node (filled green circle), four satellite nodes (lighter green, 70% opacity), connector lines (green, 40% opacity).

Wordmark: icon + "agentbreeder" in Geist Sans weight 800, lowercase, white with "breeder" in green.

Favicon: same SVG, simplified — hexagon + central node only (readable at 16px).

Files to generate:
- `website/public/logo.svg` — full wordmark
- `website/public/icon.svg` — icon only (for favicon)
- `website/public/favicon.ico` — 16/32/48px ICO
- `website/public/apple-touch-icon.png` — 180×180px

---

## CI/CD

### New workflow: `.github/workflows/deploy-website.yml`

```yaml
on:
  push:
    branches: [main]
    paths: [website/**]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: npm ci
        working-directory: website
      - run: npx vercel --prod --token ${{ secrets.VERCEL_TOKEN }}
        working-directory: website
        env:
          VERCEL_ORG_ID: ${{ secrets.VERCEL_ORG_ID }}
          VERCEL_PROJECT_ID: ${{ secrets.VERCEL_PROJECT_ID }}
```

### Required GitHub secrets

- `VERCEL_TOKEN` — Vercel personal access token
- `VERCEL_ORG_ID` — from `vercel link`
- `VERCEL_PROJECT_ID` — from `vercel link`

### Domain cutover

1. Deploy to Vercel (gets `agentbreeder-website.vercel.app`)
2. Add `agentbreeder.io` custom domain in Vercel dashboard
3. Update DNS: CNAME `agentbreeder.io` → `cname.vercel-dns.com`
4. Once live, disable old `deploy-docs.yml` GitHub Pages workflow

---

## Out of scope (future)

- Blog section
- Interactive playground (try agent.yaml in-browser)
- Algolia DocSearch (upgrade from Orama when traffic grows)
- Dark/light mode toggle (dark-only for launch)
- i18n

---

## Success criteria

- [ ] `agentbreeder.io` loads the landing page
- [ ] `agentbreeder.io/docs` loads the docs with full sidebar
- [ ] All existing doc pages accessible at new URLs
- [ ] `⌘K` search works across all docs
- [ ] Deploys automatically on push to `main`
- [ ] Lighthouse performance score ≥ 90
- [ ] Logo renders correctly at all sizes including favicon
