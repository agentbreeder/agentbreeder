# AgentBreeder website

The public homepage, blog and documentation at [agentbreeder.io](https://www.agentbreeder.io/), built with Next.js, Fumadocs and Tailwind CSS. Run commands from `website/` with Node.js 24 or later.

```bash
npm ci
npm run dev
```

Open [localhost:3000](http://localhost:3000). Shared light/dark design tokens live in `brand.css`; website layout and component rules live in `app/globals.css`.

## Verify a production build

```bash
npm test
npm run lint
npm run build
npm start
```

`npm run build` automatically runs `scripts/verify-build.mjs` after Next.js finishes. It checks the stylesheets actually linked by the generated homepage, requires the homepage's layout and control rules, and rejects legacy theme overrides. On Vercel it checks the adapter's emitted HTML and assets under `.next/output`; locally it checks the normal `.next` output. A stale asset fails the build instead of shipping invisible headings and unstyled controls.

Production Turbopack filesystem caching is disabled in `next.config.mjs`: a restored cache previously served the old stylesheet with the new homepage HTML. Development caching remains enabled. When recovering an already affected Vercel deployment, rebuild without the existing build cache.

Review light and dark themes at phone, tablet and desktop widths. The hero recording is visible on arrival; visitors can start or pause replay. Reduced motion renders the complete recording.
