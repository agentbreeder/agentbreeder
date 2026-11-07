// website/next.config.mjs
import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  reactStrictMode: true,
  // The site documents the focused rebuild. Old preview URLs moved to the docs root; 2.x pages
  // were removed (they live at the v2.7.3 tag) and point at the Legacy 2.x page. Temporary
  // redirects, so the 2.x URLs stay free for future pages.
  async redirects() {
    return [
      // Unpublished 2026-11-07: competitor comparisons that made unverified product claims.
      { source: '/blog/agentbreeder-vs-competitors', destination: '/blog', permanent: false },
      { source: '/blog/the-big-four-agent-platforms-2026', destination: '/blog', permanent: false },
      { source: '/docs/preview', destination: '/docs', permanent: true },
      { source: '/docs/preview/:slug', destination: '/docs/:slug', permanent: true },
      { source: '/docs/migrations', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/a2a-protocol', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/agent-yaml', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/api-stability', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/authentication', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/cli-reference', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/comparisons', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/connectors', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/deployment', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/evaluations', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/examples', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/faq', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/full-code', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/gateways', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/go-sdk', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/graphrag', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/how-to', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/local-development', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/low-code', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/mcp-authoring', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/mcp-servers', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/memory', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/no-code', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/orchestration-sdk', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/orchestration-yaml', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/playground', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/polyglot-agents', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/prompts', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/providers', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/quickstart', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/rag', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/registry-guide', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/runtime-contract', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/sdks-rag', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/secrets', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/self-hosting', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/sidecar', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/tools', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/troubleshooting', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/upgrading', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/migrations/from-autogen', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/migrations/from-crewai', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/migrations/from-custom', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/migrations/from-langgraph', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/migrations/from-openai-agents', destination: '/docs/legacy-2x', permanent: false },
      { source: '/docs/migrations/overview', destination: '/docs/legacy-2x', permanent: false },
    ];
  },
};

export default withMDX(config);
